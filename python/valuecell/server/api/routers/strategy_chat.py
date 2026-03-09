"""
Strategy Chat router — lets users interact with a running strategy's LLM.

Endpoints
---------
POST /strategies/chat
    Stream the strategy's LLM response via Server-Sent Events.
    Emits:  { type: "chunk",  text: "..." }   — explanation text as it arrives
            { type: "done",   explanation, strategy_id, [prompt_proposal, original_prompt] }
            { type: "error",  message: "..." }  — on failure

PATCH /strategies/update-prompt
    Apply a prompt improvement to the strategy's stored configuration.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

from agno.agent import Agent as AgnoAgent
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from valuecell.server.api.auth_utils import (
    check_strategy_ownership,
    get_current_user_optional,
)
from valuecell.server.api.schemas.base import SuccessResponse
from valuecell.server.db.repositories import get_strategy_repository
from valuecell.utils.model import create_model_with_provider

_CHAT_SYSTEM_PROMPT = """\
You are an analyst embedded in an autonomous crypto trading strategy.
Your job is to answer the user's questions about this strategy clearly and concisely.

You have access to:
- The strategy configuration and trading prompt
- The current portfolio holdings
- Recent decision-cycle rationales produced by the model
- Recent executed trades and their outcomes

When asked to *explain* a decision, reference the actual rationale text and market context.
When evaluating whether a decision was optimal, be honest. If the strategy missed an
opportunity (e.g., failed to trail a stop loss, held a losing position too long, didn't
capitalise on a strong trend), say so clearly and identify the root cause in the prompt.

If you determine the strategy made a suboptimal decision that could be fixed by improving
the strategy prompt, set is_mistake to true and include a complete revised prompt_proposal.
The proposal must be the FULL revised prompt text, not just the changed lines.

IMPORTANT — always respond with valid JSON in exactly this format:
{
  "explanation": "<detailed answer grounded in the data>",
  "is_mistake": <true|false>,
  "prompt_proposal": "<complete revised prompt — only include this key when is_mistake is true>"
}

Do not include any text outside the JSON object. Do not invent numbers or events.
"""


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class StrategyChatRequest(BaseModel):
    strategy_id: str
    message: str
    history: list[ChatMessage] = []
    recent_cycles: int = 5
    recent_trades: int = 10


class UpdatePromptRequest(BaseModel):
    strategy_id: str
    prompt_text: str


# ---------------------------------------------------------------------------
# Incremental JSON explanation extractor
# ---------------------------------------------------------------------------


class _ExplanationExtractor:
    """Incrementally extracts the value of the "explanation" key from a
    streaming JSON string produced by the LLM.

    The LLM outputs something like:
        {"explanation": "The strategy did X because Y...", "is_mistake": false}

    As JSON chunks arrive token by token we:
      1. Wait until we see `"explanation":` in the accumulated buffer.
      2. Find the opening double-quote of the string value.
      3. Stream each character, unescaping JSON escape sequences.
      4. Stop when we hit the unescaped closing double-quote.

    If the LLM deviates from JSON format and we never find the key, the
    caller falls back to sending the whole raw text in the final "done" event.
    """

    _SEEKING_KEY = 0
    _SEEKING_OPEN_QUOTE = 1
    _IN_VALUE = 2
    _DONE = 3
    _MARKER = '"explanation":'

    def __init__(self) -> None:
        self._state = self._SEEKING_KEY
        self._buf = ""
        self._escaped = False

    def feed(self, chunk: str) -> tuple[str, bool]:
        """Process *chunk* and return (text_to_stream, extraction_complete)."""
        if self._state == self._DONE:
            return "", True

        self._buf += chunk

        if self._state == self._SEEKING_KEY:
            pos = self._buf.find(self._MARKER)
            if pos != -1:
                self._buf = self._buf[pos + len(self._MARKER) :]
                self._state = self._SEEKING_OPEN_QUOTE
            else:
                # Keep tail in case the marker spans chunks
                keep = len(self._MARKER) - 1
                self._buf = self._buf[-keep:] if len(self._buf) > keep else self._buf
                return "", False

        if self._state == self._SEEKING_OPEN_QUOTE:
            pos = self._buf.find('"')
            if pos != -1:
                self._buf = self._buf[pos + 1 :]
                self._state = self._IN_VALUE
            else:
                return "", False

        if self._state == self._IN_VALUE:
            result: list[str] = []
            i = 0
            while i < len(self._buf):
                c = self._buf[i]
                if self._escaped:
                    _ESCAPES = {
                        "n": "\n",
                        "t": "\t",
                        "r": "\r",
                        '"': '"',
                        "\\": "\\",
                        "/": "/",
                    }
                    result.append(_ESCAPES.get(c, c))
                    self._escaped = False
                    i += 1
                elif c == "\\":
                    self._escaped = True
                    i += 1
                elif c == '"':
                    # Closing quote — extraction done
                    self._state = self._DONE
                    self._buf = self._buf[i + 1 :]
                    return "".join(result), True
                else:
                    result.append(c)
                    i += 1

            self._buf = ""
            return "".join(result), False

        return "", False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_llm_response(raw: str) -> dict:
    """Extract JSON from LLM output, falling back to a plain explanation."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"explanation": raw, "is_mistake": False}


def _build_context(strategy, req: StrategyChatRequest, repo) -> str:
    cfg: dict = strategy.config or {}
    meta: dict = strategy.strategy_metadata or {}
    tc: dict = cfg.get("trading_config") or {}
    symbols = tc.get("symbols") or []
    prompt_text = tc.get("prompt_text") or tc.get("custom_prompt") or ""
    model_id = (cfg.get("llm_model_config") or {}).get("model_id", "")
    provider = (cfg.get("llm_model_config") or {}).get("provider", "")

    sections: list[str] = []

    sections.append(
        "## Strategy Info\n"
        f"- Name: {strategy.name}\n"
        f"- Status: {strategy.status}\n"
        f"- Model: {model_id} ({provider})\n"
        f"- Symbols: {', '.join(symbols) if symbols else 'N/A'}\n"
        f"- Trading mode: {meta.get('trading_mode', 'unknown')}\n"
        f"- Exchange: {meta.get('exchange_id', 'N/A')}"
    )

    if prompt_text:
        sections.append(f"## Strategy Prompt\n{prompt_text}")

    snapshot = repo.get_latest_portfolio_snapshot(req.strategy_id)
    if snapshot:
        sections.append(
            "## Portfolio Summary\n"
            f"- Total value: {snapshot.total_value}\n"
            f"- Cash: {snapshot.cash}\n"
            f"- Unrealized PnL: {snapshot.total_unrealized_pnl}\n"
            f"- Realized PnL: {snapshot.total_realized_pnl}"
        )

    holdings = repo.get_latest_holdings(req.strategy_id)
    active_holdings = [h for h in holdings if h.quantity]
    if active_holdings:
        lines = [
            f"  - {h.symbol} {h.type}: qty={h.quantity}, "
            f"entry_price={h.entry_price}, unrealized_pnl={h.unrealized_pnl}"
            for h in active_holdings
        ]
        sections.append("## Current Holdings\n" + "\n".join(lines))
    else:
        sections.append("## Current Holdings\nNo open positions.")

    cycles = repo.get_cycles(req.strategy_id, limit=req.recent_cycles)
    if cycles:
        lines = []
        for c in reversed(cycles):
            ts = (
                c.compose_time.strftime("%Y-%m-%d %H:%M")
                if c.compose_time
                else "unknown"
            )
            rationale = (c.rationale or "").strip()
            if len(rationale) > 600:
                rationale = rationale[:597] + "..."
            lines.append(f"  Cycle #{c.cycle_index} ({ts}):\n    {rationale}")
        sections.append(
            f"## Recent Decision Cycles (oldest → newest, last {len(cycles)})\n"
            + "\n".join(lines)
        )

    details = repo.get_details(req.strategy_id, limit=req.recent_trades)
    if details:
        lines = []
        for d in reversed(details):
            ts = d.entry_time.strftime("%Y-%m-%d %H:%M") if d.entry_time else "?"
            exit_ts = d.exit_time.strftime("%Y-%m-%d %H:%M") if d.exit_time else "open"
            lines.append(
                f"  - {ts}→{exit_ts} | {d.symbol} {d.type} {d.side} "
                f"qty={d.quantity} entry={d.entry_price} exit={d.exit_price} "
                f"realized_pnl={d.realized_pnl}"
            )
        sections.append(
            f"## Recent Trades (oldest → newest, last {len(details)})\n"
            + "\n".join(lines)
        )

    context = "\n\n".join(sections)

    # Conversation history
    history_text = ""
    if req.history:
        history_lines = []
        for msg in req.history:
            label = "User" if msg.role == "user" else "Assistant"
            history_lines.append(f"{label}: {msg.content}")
        history_text = "\n\n## Conversation History\n" + "\n\n".join(history_lines)

    return f"{context}{history_text}\n\n## User Question\n{req.message}"


def _sse(payload: dict) -> str:
    """Format a dict as a single SSE data line."""
    return f"data: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def create_strategy_chat_router() -> APIRouter:
    router = APIRouter(prefix="/strategies", tags=["strategies"])

    @router.post("/chat")
    async def strategy_chat(req: StrategyChatRequest):
        """
        Stream the strategy LLM response via Server-Sent Events.

        Event types emitted:
          { "type": "chunk",  "text": "..." }
          { "type": "done",   "strategy_id": ..., "explanation": ...,
                              ["prompt_proposal": ..., "original_prompt": ...] }
          { "type": "error",  "message": "..." }
        """
        repo = get_strategy_repository()
        strategy = repo.get_strategy_by_strategy_id(req.strategy_id)
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")

        cfg: dict = strategy.config or {}
        llm_cfg: dict = cfg.get("llm_model_config") or {}
        provider: Optional[str] = llm_cfg.get("provider")
        model_id: Optional[str] = llm_cfg.get("model_id")
        api_key: Optional[str] = llm_cfg.get("api_key")

        if not provider or not model_id:
            raise HTTPException(
                status_code=400,
                detail="Strategy LLM config is not available in the database",
            )

        tc: dict = cfg.get("trading_config") or {}
        current_prompt_text: str = (
            tc.get("prompt_text") or tc.get("custom_prompt") or ""
        )

        user_message = _build_context(strategy, req, repo)

        try:
            model = create_model_with_provider(
                provider=provider,
                model_id=model_id,
                api_key=api_key or None,
            )
            agent = AgnoAgent(
                model=model,
                instructions=[_CHAT_SYSTEM_PROMPT],
                markdown=False,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to create model: {exc}"
            ) from exc

        async def event_generator():
            full_text = ""
            extractor = _ExplanationExtractor()

            try:
                async with asyncio.timeout(120.0):
                    async for event in agent.arun(user_message, stream=True):
                        chunk: str = ""
                        if hasattr(event, "content") and isinstance(event.content, str):
                            chunk = event.content
                        if not chunk:
                            continue

                        full_text += chunk
                        text_to_send, _ = extractor.feed(chunk)
                        if text_to_send:
                            yield _sse({"type": "chunk", "text": text_to_send})

            except asyncio.TimeoutError:
                yield _sse({"type": "error", "message": "LLM timed out after 120 s"})
                return
            except Exception as exc:
                logger.exception(
                    "Streaming chat failed for strategy {}", req.strategy_id
                )
                yield _sse({"type": "error", "message": str(exc)})
                return

            # Final parsed response
            parsed = _parse_llm_response(full_text)
            explanation = parsed.get("explanation") or full_text
            is_mistake = bool(parsed.get("is_mistake", False))
            prompt_proposal = parsed.get("prompt_proposal") if is_mistake else None

            done: dict = {
                "type": "done",
                "strategy_id": req.strategy_id,
                "explanation": explanation,
            }
            if prompt_proposal:
                done["prompt_proposal"] = prompt_proposal
                done["original_prompt"] = current_prompt_text

            yield _sse(done)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.patch("/update-prompt")
    async def update_strategy_prompt(
        req: UpdatePromptRequest,
        current_user: Optional[str] = Depends(get_current_user_optional),
    ):
        """
        Apply a prompt improvement to the strategy's stored configuration.
        The old prompt is saved to strategy_metadata.prompt_history before overwriting.
        The new prompt is used on the next decision cycle.
        """
        repo = get_strategy_repository()
        strategy = repo.get_strategy_by_strategy_id(req.strategy_id)
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")

        # Ownership check
        check_strategy_ownership(strategy, current_user)

        from datetime import datetime, timezone

        cfg = dict(strategy.config or {})
        tc = dict(cfg.get("trading_config") or {})

        # Archive the current prompt into metadata.prompt_history before overwriting
        current_prompt = tc.get("prompt_text") or tc.get("custom_prompt") or ""
        if current_prompt:
            meta = dict(strategy.strategy_metadata or {})
            history: list = list(meta.get("prompt_history") or [])
            history.append(
                {
                    "prompt_text": current_prompt,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            meta["prompt_history"] = history
        else:
            meta = dict(strategy.strategy_metadata or {})

        tc["prompt_text"] = req.prompt_text
        cfg["trading_config"] = tc
        repo.upsert_strategy(req.strategy_id, config=cfg, metadata=meta)

        return SuccessResponse.create(
            data={"strategy_id": req.strategy_id},
            msg="Strategy prompt updated",
        )

    @router.get("/prompt-history")
    async def get_prompt_history(id: str):
        """
        Return all saved versions of a strategy's prompt plus the current one.

        Each entry has:
          - version: 1-based index (oldest = 1)
          - prompt_text: the prompt content
          - saved_at: ISO timestamp of when the version was archived
          - is_current: true only for the active prompt
        """
        repo = get_strategy_repository()
        strategy = repo.get_strategy_by_strategy_id(id)
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")

        cfg: dict = strategy.config or {}
        tc: dict = cfg.get("trading_config") or {}
        current_prompt = tc.get("prompt_text") or tc.get("custom_prompt") or ""

        meta: dict = strategy.strategy_metadata or {}
        history: list = meta.get("prompt_history") or []

        from datetime import datetime, timezone

        versions = []
        for i, entry in enumerate(history):
            versions.append(
                {
                    "version": i + 1,
                    "prompt_text": entry.get("prompt_text", ""),
                    "saved_at": entry.get("saved_at", ""),
                    "is_current": False,
                }
            )

        # Add current as the latest version
        versions.append(
            {
                "version": len(history) + 1,
                "prompt_text": current_prompt,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "is_current": True,
            }
        )

        return SuccessResponse.create(data=versions, msg="Prompt history retrieved")

    return router
