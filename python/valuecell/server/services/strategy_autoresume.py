"""Server-side strategy auto-resume logic.

This module scans persisted strategies with status 'running' on process
startup and dispatches them through the existing AgentOrchestrator using
their stored configuration. The core orchestrator remains unaware of
auto-resume concerns per design (separation of coordination vs runtime ops).

Resume Semantics:
 - Strategies whose status == 'running' (previous session crashed) are resumed.
 - Strategies whose status == 'stopped' with metadata.stop_reason == 'cancelled'
     (gracefully cancelled but intended to auto-resume) are also resumed.
 - Each strategy's original config dict is parsed into a UserRequest.
 - The stored strategy_id is injected into TradingConfig.strategy_id so the
   underlying runtime reuses portfolio state (idempotent initial snapshot).
 - Streaming responses are consumed and discarded (fire-and-forget). External
   observers can implement their own hooks if needed.

Failures during individual strategy resume are logged and skipped without
impacting other candidates.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from loguru import logger

from valuecell.agents.common.trading.models import (
    StopReason,
    StrategyStatus,
    StrategyStatusContent,
    TradingMode,
    UserRequest,
)
from valuecell.config.loader import get_config_loader
from valuecell.core.coordinate.orchestrator import AgentOrchestrator
from valuecell.core.types import CommonResponseEvent, UserInput, UserInputMetadata
from valuecell.server.db.models.strategy import Strategy
from valuecell.server.db.repositories.strategy_repository import get_strategy_repository
from valuecell.server.services import strategy_persistence
from valuecell.utils.uuid import generate_conversation_id

_AUTORESUME_STARTED = False


async def auto_resume_strategies(
    orchestrator: AgentOrchestrator,
    max_strategies: Optional[int] = None,
) -> None:
    """Dispatch background resume tasks for persisted running strategies.

    Args:
        orchestrator: Existing AgentOrchestrator instance.
        max_strategies: Optional limit to number of strategies resumed.
    """
    global _AUTORESUME_STARTED
    if _AUTORESUME_STARTED:
        return
    _AUTORESUME_STARTED = True

    try:
        repo = get_strategy_repository()
        rows = repo.list_strategies_by_status(
            [StrategyStatus.RUNNING.value, StrategyStatus.STOPPED.value],
            limit=max_strategies,
        )
        candidates = [s for s in rows if _should_resume(s)]
        if not candidates:
            logger.info("Auto-resume: no eligible strategies found")
            return
        logger.info("Auto-resume: found {} eligible strategies", len(candidates))
        # Fire-and-forget: create background tasks and do NOT await them.
        # The caller (FastAPI startup event) must not be blocked waiting for
        # strategies to fully restart — each strategy runs independently.
        for s in candidates:
            asyncio.create_task(_resume_one(orchestrator, s))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Auto-resume scan failed")


async def _resume_one(orchestrator: AgentOrchestrator, strategy_row: Strategy) -> None:
    strategy_id = strategy_row.strategy_id
    try:
        config_dict = strategy_row.config or {}
        metadata = strategy_row.strategy_metadata or {}
        agent_name = metadata.get("agent_name")

        # Parse request; tolerate partial configs
        request = UserRequest.model_validate(config_dict)
        if request.trading_config.strategy_id is None and strategy_id:
            request.trading_config.strategy_id = strategy_id

        # Re-apply LLM API key to os.environ so the in-process LlmComposer can
        # find it — mirrors the override the HTTP router performs at creation time.
        # The key was stored in llm_model_config (not excluded by _safe_config_dump).
        try:
            provider = request.llm_model_config.provider
            api_key = request.llm_model_config.api_key
            if provider and api_key:
                loader = get_config_loader()
                provider_cfg_raw = loader.load_provider_config(provider) or {}
                api_key_env = provider_cfg_raw.get("connection", {}).get("api_key_env")
                if api_key_env:
                    os.environ[api_key_env] = api_key
                    loader.clear_cache()
                    logger.info(
                        "Auto-resume: restored {} env var for strategy_id={}",
                        api_key_env,
                        strategy_id,
                    )
        except Exception:
            logger.warning(
                "Auto-resume: could not restore LLM API key for strategy_id={}",
                strategy_id,
            )

        # Re-inject exchange credentials for live trading strategies.
        # Credentials are excluded from stored config (_safe_config_dump) for security.
        # Restore them from environment variables using the convention:
        #   {EXCHANGE_ID_UPPER}_API_KEY, {EXCHANGE_ID_UPPER}_SECRET_KEY, etc.
        # Set these in /opt/valuecell/.env on the VM (same pattern as AI provider keys).
        try:
            if request.exchange_config.trading_mode == TradingMode.LIVE:
                exchange_id = (
                    (request.exchange_config.exchange_id or "")
                    .upper()
                    .replace("-", "_")
                )
                _cred_fields = [
                    ("api_key", f"{exchange_id}_API_KEY"),
                    ("secret_key", f"{exchange_id}_SECRET_KEY"),
                    ("secret_key", f"{exchange_id}_API_SECRET"),  # common alias
                    ("passphrase", f"{exchange_id}_PASSPHRASE"),
                    ("wallet_address", f"{exchange_id}_WALLET_ADDRESS"),
                    ("private_key", f"{exchange_id}_PRIVATE_KEY"),
                ]
                injected = {}
                for field, env_var in _cred_fields:
                    if field not in injected and not getattr(
                        request.exchange_config, field, None
                    ):
                        env_val = os.environ.get(env_var)
                        if env_val:
                            injected[field] = env_val
                if injected:
                    request = request.model_copy(
                        update={
                            "exchange_config": request.exchange_config.model_copy(
                                update=injected
                            )
                        }
                    )
                    logger.info(
                        "Auto-resume: injected exchange credentials {} for strategy_id={}",
                        list(injected.keys()),
                        strategy_id,
                    )

                # Safety check: abort if live strategy still has no credentials.
                missing = not (
                    request.exchange_config.api_key
                    or request.exchange_config.private_key
                )
                if missing:
                    logger.warning(
                        "Auto-resume: skipping live strategy {} — exchange credentials "
                        "not available. Set {}_API_KEY / {}_SECRET_KEY in the VM .env "
                        "file to enable auto-resume for live strategies.",
                        strategy_id,
                        exchange_id,
                        exchange_id,
                    )
                    return
        except Exception:
            logger.warning(
                "Auto-resume: could not restore exchange credentials for strategy_id={}",
                strategy_id,
            )
            return

        user_input = UserInput(
            query=request.model_dump_json(),
            target_agent_name=agent_name,
            meta=UserInputMetadata(
                user_id=strategy_row.user_id,
                conversation_id=generate_conversation_id(),
            ),
        )

        async for chunk in orchestrator.process_user_input(user_input):
            logger.debug("Auto-resume chunk for strategy_id={}: {}", strategy_id, chunk)
            if chunk.event == CommonResponseEvent.COMPONENT_GENERATOR:
                logger.info(
                    "Auto-resume dispatched strategy_id={} agent={}",
                    strategy_id,
                    agent_name,
                )
                status_content = StrategyStatusContent.model_validate_json(
                    chunk.data.payload.content
                )
                strategy_persistence.set_strategy_status(
                    strategy_id, status_content.status.value
                )
                return

    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "Auto-resume failed for strategy_id={}", strategy_id or "<unknown>"
        )


def _should_resume(strategy_row: Strategy) -> bool:
    """Return True if strategy should be auto-resumed based on status/metadata.

    Resume semantics:
    - RUNNING: container was killed mid-run (crash / host restart) — always resume.
    - STOPPED + no stop_reason: unclean stop (container kill, VM reset, OOM) —
      resume because no user intent was recorded.
    - STOPPED + stop_reason == 'cancelled': gracefully cancelled but intended to
      auto-resume — resume.
    - STOPPED + any other stop_reason (user_stopped, normal_exit, error, …):
      do NOT resume; the stop was intentional or requires manual review.
    """
    status_raw = strategy_row.status or ""
    metadata = strategy_row.strategy_metadata or {}
    try:
        status_enum = StrategyStatus(status_raw)
    except Exception:
        # Unknown/invalid status - skip
        return False

    if status_enum == StrategyStatus.RUNNING:
        return True

    if status_enum == StrategyStatus.STOPPED:
        stop_reason = metadata.get("stop_reason") or ""
        # No recorded stop_reason → unclean / abrupt stop; safe to resume.
        if not stop_reason:
            return True
        # Graceful cancellation that is meant to auto-resume.
        if stop_reason == StopReason.CANCELLED.value:
            return True

    return False
