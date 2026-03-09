"""
Strategy Chat router — lets users interact with a running strategy's LLM.

Endpoints
---------
POST /strategies/chat
    Ask the strategy's model to explain past decisions or suggest improvements.
    Returns structured response: explanation + optional prompt_proposal.

PATCH /strategies/update-prompt
    Apply a prompt improvement to the strategy's stored configuration.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

from agno.agent import Agent as AgnoAgent
from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

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
            exit_ts = (
                d.exit_time.strftime("%Y-%m-%d %H:%M") if d.exit_time else "open"
            )
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


def create_strategy_chat_router() -> APIRouter:
    router = APIRouter(prefix="/strategies", tags=["strategies"])

    @router.post("/chat")
    async def strategy_chat(req: StrategyChatRequest):
        """
        Send a message to the strategy's LLM.

        Returns:
          - explanation: answer grounded in strategy data
          - prompt_proposal: full revised prompt (only when a correctable mistake is found)
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

        # Extract the current prompt text so we can include it in the diff on the FE
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
            response = await asyncio.wait_for(
                agent.arun(user_message), timeout=120.0
            )
            raw = getattr(response, "content", None) or response
            if not isinstance(raw, str):
                raw = str(raw)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504, detail="LLM call timed out after 120 seconds"
            )
        except Exception as exc:
            logger.exception(
                "Chat LLM call failed for strategy {}", req.strategy_id
            )
            raise HTTPException(
                status_code=500, detail=f"LLM call failed: {exc}"
            ) from exc

        parsed = _parse_llm_response(raw)
        explanation = parsed.get("explanation") or raw
        is_mistake = bool(parsed.get("is_mistake", False))
        prompt_proposal = parsed.get("prompt_proposal") if is_mistake else None

        result: dict = {
            "strategy_id": req.strategy_id,
            "explanation": explanation,
        }
        if prompt_proposal:
            result["prompt_proposal"] = prompt_proposal
            # Include the original so the frontend can render a diff
            result["original_prompt"] = current_prompt_text

        return SuccessResponse.create(data=result, msg="Chat response generated")

    @router.patch("/update-prompt")
    async def update_strategy_prompt(req: UpdatePromptRequest):
        """
        Apply a prompt improvement to the strategy's stored configuration.
        The new prompt is used on the next decision cycle.
        """
        repo = get_strategy_repository()
        strategy = repo.get_strategy_by_strategy_id(req.strategy_id)
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")

        cfg = dict(strategy.config or {})
        tc = dict(cfg.get("trading_config") or {})
        tc["prompt_text"] = req.prompt_text
        cfg["trading_config"] = tc
        repo.upsert_strategy(req.strategy_id, config=cfg)

        return SuccessResponse.create(
            data={"strategy_id": req.strategy_id},
            msg="Strategy prompt updated",
        )

    return router
