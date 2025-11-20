"""Main auto trading agent implementation with multi-instance support"""

import asyncio
import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Deque, Dict, List, Optional

from agno.agent import Agent
from agno.models.openrouter import OpenRouter

from valuecell.core.agent.responses import streaming
from valuecell.core.types import (
    BaseAgent,
    ComponentType,
    FilteredCardPushNotificationComponentData,
    FilteredLineChartComponentData,
    StreamResponse,
)

from .constants import (
    DEFAULT_AGENT_MODEL,
    DEFAULT_CHECK_INTERVAL,
)
from .formatters import MessageFormatter
from .models import (
    AutoTradingConfig,
    TradingRequest,
)
from .portfolio_decision_manager import (
    AssetAnalysis,
    PortfolioDecisionManager,
)
from .exchanges import create_exchange_adapter
from .technical_analysis import AISignalGenerator, TechnicalAnalyzer
from .trading_executor import TradingExecutor

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Maximum cached notifications per session
MAX_NOTIFICATION_CACHE_SIZE = 5000


class AutoTradingAgent(BaseAgent):
    """
    Automated crypto trading agent with technical analysis and position management.
    Supports multiple trading instances per session with independent configurations.
    """

    def __init__(self):
        super().__init__()

        # Configuration
        self.parser_model_id = os.getenv("TRADING_PARSER_MODEL_ID", DEFAULT_AGENT_MODEL)

        # Multi-instance state management
        # Structure: {session_id: {instance_id: TradingInstanceData}}
        self.trading_instances: Dict[str, Dict[str, Dict[str, Any]]] = {}

        # Notification cache for batch sending
        # Structure: {session_id: deque[FilteredCardPushNotificationComponentData]}
        # Using deque with maxlen for automatic FIFO eviction
        self.notification_cache: Dict[
            str, Deque[FilteredCardPushNotificationComponentData]
        ] = {}

        try:
            # Parser agent for natural language query parsing
            self.parser_agent = Agent(
                model=OpenRouter(id=self.parser_model_id),
                output_schema=TradingRequest,
                markdown=True,
            )
            logger.info("Auto Trading Agent initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Auto Trading Agent: {e}", exc_info=True)
            raise

    async def close(self):
        for instance_id, instance in self.trading_instances[session_id].items():
            executor: TradingExecutor = instance["executor"]
            await executor.close()

    async def _process_trading_instance(
        self,
        session_id: str,
        instance_id: str,
        semaphore: asyncio.Semaphore,
        unified_timestamp: Optional[datetime] = None,
    ) -> None:
        """
        Process a single trading instance with semaphore control for concurrency limiting.

        Args:
            session_id: Session identifier
            instance_id: Trading instance identifier
            semaphore: Asyncio semaphore to limit concurrent processing
            unified_timestamp: Optional unified timestamp for snapshot alignment across instances
        """
        async with semaphore:
            try:
                # Check if instance still exists and is active
                if instance_id not in self.trading_instances.get(session_id, {}):
                    return

                instance = self.trading_instances[session_id][instance_id]
                if not instance["active"]:
                    return

                # Get instance components
                executor = instance["executor"]
                config = instance["config"]
                ai_signal_generator = instance["ai_signal_generator"]

                # Update check info
                instance["check_count"] += 1
                instance["last_check"] = datetime.now()
                check_count = instance["check_count"]

                logger.info(
                    f"Trading check #{check_count} for instance {instance_id} (model: {config.agent_model})"
                )

                logger.info(
                    f"\n{'=' * 50}\n"
                    f"🔄 **Check #{check_count}** - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Instance: `{instance_id}`\n"
                    f"Model: `{config.agent_model}`\n"
                    f"{'=' * 50}\n\n"
                )

                # Phase 1: Collect analysis for all symbols
                logger.info("📊 **Phase 1: Analyzing all assets...**\n\n")

                # Initialize portfolio manager with LLM client for AI-powered decisions
                llm_client = None
                if ai_signal_generator and ai_signal_generator.llm_client:
                    llm_client = ai_signal_generator.llm_client

                portfolio_manager = PortfolioDecisionManager(config, llm_client)
                positions = await executor.get_positions()

                for symbol in config.crypto_symbols:
                    # Calculate indicators
                    indicators = TechnicalAnalyzer.calculate_indicators(symbol)

                    if indicators is None:
                        logger.warning(f"Skipping {symbol} - insufficient data")
                        continue

                    # Generate technical signal
                    technical_action, technical_trade_type = (
                        TechnicalAnalyzer.generate_signal(indicators)
                    )

                    # Generate AI signal if enabled
                    ai_action, ai_trade_type, ai_reasoning, ai_confidence = (
                        None,
                        None,
                        None,
                        None,
                    )

                    if ai_signal_generator:
                        ai_signal = await ai_signal_generator.get_signal(indicators)
                        if ai_signal:
                            (
                                ai_action,
                                ai_trade_type,
                                ai_reasoning,
                                ai_confidence,
                            ) = ai_signal
                            logger.info(
                                f"AI signal for {symbol}: {ai_action.value} {ai_trade_type.value} "
                                f"(confidence: {ai_confidence}%)"
                            )

                    # Create asset analysis
                    asset_analysis = AssetAnalysis(
                        symbol=symbol,
                        indicators=indicators,
                        technical_action=technical_action,
                        technical_trade_type=technical_trade_type,
                        ai_action=ai_action,
                        ai_trade_type=ai_trade_type,
                        ai_reasoning=ai_reasoning,
                        ai_confidence=ai_confidence,
                    )

                    # Add to portfolio manager
                    portfolio_manager.add_asset_analysis(asset_analysis)

                    # Display individual asset analysis
                    logger.info(
                        MessageFormatter.format_market_analysis_notification(
                            symbol,
                            indicators,
                            asset_analysis.recommended_action,
                            asset_analysis.recommended_trade_type,
                            positions,
                            ai_reasoning,
                        )
                    )

                # Phase 2: Make portfolio-level decision
                logger.info(
                    "\n" + "=" * 50 + "\n"
                    "🎯 **Phase 2: Portfolio Decision Making...**\n" + "=" * 50 + "\n\n"
                )

                # Get portfolio summary
                portfolio_summary = await executor.get_portfolio_summary()
                logger.info(f"{portfolio_summary}\n")

                # Make coordinated decision (async call for AI analysis)
                portfolio_decision = await portfolio_manager.make_portfolio_decision(
                    current_positions=positions,
                    available_cash=await executor.get_current_capital(),
                    total_portfolio_value=await executor.get_portfolio_value(),
                )

                # Display decision reasoning - cache it
                portfolio_decision_msg = FilteredCardPushNotificationComponentData(
                    title=f"{config.agent_model} Analysis",
                    data=f"💰 **Portfolio Decision Reasoning**\n{portfolio_decision.reasoning}\n",
                    filters=[config.agent_model],
                    table_title="Market Analysis",
                    create_time=datetime.now(timezone.utc).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                )
                # Cache the decision notification
                self._cache_notification(session_id, portfolio_decision_msg)

                # Phase 3: Execute approved trades
                if portfolio_decision.trades_to_execute:
                    logger.info(
                        "\n" + "=" * 50 + "\n"
                        f"⚡ **Phase 3: Executing {len(portfolio_decision.trades_to_execute)} trade(s)...**\n"
                        + "=" * 50
                        + "\n\n"
                    )

                    logger.info(f"full asset analysis {portfolio_manager.asset_analyses}")
                    for (
                        symbol,
                        action,
                        trade_type,
                        quantity,
                    ) in portfolio_decision.trades_to_execute:
                        # Get indicators for this symbol

                        asset = symbol.replace("USDT", "").replace("-USD", "")
                        asset_analysis_symbol = f'{asset}-USD'
                        asset_analysis = portfolio_manager.asset_analyses.get(asset_analysis_symbol).to_dict()
                        logger.info(f"asset analysis for {asset_analysis_symbol}: {asset_analysis}")
                        # if not asset_analysis:
                        #     continue

                        # Execute trade
                        normalized_symbol = f'{asset}USDT'
                        logger.info(f'Execute trade: {action.value} {quantity} of {normalized_symbol}')
                        trade_details = await executor.execute_trade(
                            normalized_symbol, action, quantity
                        )

                        if trade_details:
                            # Cache trade notification
                            trade_message_text = (
                                MessageFormatter.format_trade_notification(
                                    trade_details, config.agent_model
                                )
                            )
                            trade_message = FilteredCardPushNotificationComponentData(
                                title=f"{config.agent_model} Trade",
                                data=f"💰 **Trade Executed:**\n{trade_message_text}\n",
                                filters=[config.agent_model],
                                table_title="Trade Detail",
                                create_time=datetime.now(timezone.utc).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                ),
                            )
                            # Cache the trade notification
                            self._cache_notification(session_id, trade_message)
                        else:
                            trade_message = FilteredCardPushNotificationComponentData(
                                title=f"{config.agent_model} Trade",
                                data=f"💰 **Trade Failed:** Could not execute {action.value} "
                                f"{trade_type.value} on {symbol}\n",
                                filters=[config.agent_model],
                                table_title="Trade Detail",
                                create_time=datetime.now(timezone.utc).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                ),
                            )
                            # Cache the failed trade notification
                            self._cache_notification(session_id, trade_message)

                timestamp = unified_timestamp if unified_timestamp else datetime.now()
                # Send portfolio update
                portfolio_value = await executor.get_portfolio_value()
                portfolio_summary = await executor.get_portfolio_summary()
                total_pnl = portfolio_summary["portfolio"]["total_pnl"]

                portfolio_msg = (
                    f"💰 **Portfolio Update**\n"
                    f"Model: {config.agent_model}\n"
                    f"Time: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Total Value: ${portfolio_value:,.2f}\n"
                    f"P&L: ${total_pnl:,.2f}\n"
                    f"Open Positions: {len(executor.positions)}\n"
                    f"Available Capital: ${await executor.current_capital:,.2f}\n"
                )

                if executor.positions:
                    portfolio_msg += "\n**Open Positions:**\n"
                    for symbol, pos in executor.positions.items():
                        try:
                            trading_symbol = f"{symbol}USDT"
                            current_prices = await executor.exchange.get_current_price(trading_symbol)
                            current_price = current_prices[trading_symbol]
                            if pos.trade_type.value == "long":
                                current_pnl = (current_price - pos.entry_price) * abs(
                                    pos.quantity
                                )
                            else:
                                current_pnl = (pos.entry_price - current_price) * abs(
                                    pos.quantity
                                )
                            pnl_emoji = "🟢" if current_pnl >= 0 else "🔴"
                            portfolio_msg += f"- {symbol}: {pos.trade_type.value.upper()} @ ${pos.entry_price:,.2f} {pnl_emoji} P&L: ${current_pnl:,.2f}\n"
                        except Exception as e:
                            logger.warning(f"Failed to calculate P&L for {symbol}: {e}", exc_info=True)
                            portfolio_msg += f"- {symbol}: {pos.trade_type.value.upper()} @ ${pos.entry_price:,.2f}\n"

                logger.info(portfolio_msg + "\n")

                # Cache portfolio status notification
                component_data = await self._get_instance_status_component_data(
                    session_id, instance_id
                )
                if component_data:
                    self._cache_notification(session_id, component_data)

            except Exception as e:
                logger.error(f"Error processing trading instance {instance_id}: {e}", exc_info=True)
                # Don't raise - let other instances continue

    def _generate_instance_id(self, task_id: str, model_id: str) -> str:
        """
        Generate unique instance ID for a specific model

        Args:
            task_id: Task ID from the request
            model_id: Model identifier (e.g., 'deepseek/deepseek-v3.1-terminus')

        Returns:
            Unique instance ID combining timestamp, task, and model
        """
        import hashlib

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S_%f"
        )  # Include microseconds for uniqueness
        # Create a short hash from model_id for readability
        model_hash = hashlib.md5(model_id.encode()).hexdigest()[:6]
        # Extract model name (last part after /)
        model_name = model_id.split("/")[-1].replace("-", "_").replace(".", "_")[:15]

        return f"trade_{timestamp}_{model_name}_{model_hash}"

    def _init_notification_cache(self, session_id: str) -> None:
        """Initialize notification cache for a session if not exists"""
        if session_id not in self.notification_cache:
            self.notification_cache[session_id] = deque(
                maxlen=MAX_NOTIFICATION_CACHE_SIZE
            )
            logger.info(f"Initialized notification cache for session {session_id}")

    def _cache_notification(
        self, session_id: str, notification: FilteredCardPushNotificationComponentData
    ) -> None:
        """
        Cache a notification for later batch sending.
        Automatically evicts oldest notifications when cache exceeds MAX_NOTIFICATION_CACHE_SIZE.

        Args:
            session_id: Session ID
            notification: Notification to cache
        """
        self._init_notification_cache(session_id)
        self.notification_cache[session_id].append(notification)
        logger.debug(
            f"Cached notification for session {session_id}. "
            f"Cache size: {len(self.notification_cache[session_id])}"
        )

    def _get_cached_notifications(
        self, session_id: str
    ) -> List[FilteredCardPushNotificationComponentData]:
        """
        Get all cached notifications for a session.

        Args:
            session_id: Session ID

        Returns:
            List of cached notifications (oldest to newest)
        """
        if session_id not in self.notification_cache:
            return []
        return list(self.notification_cache[session_id])

    def _clear_notification_cache(self, session_id: str) -> None:
        """
        Clear notification cache for a session.

        Args:
            session_id: Session ID
        """
        if session_id in self.notification_cache:
            self.notification_cache[session_id].clear()
            logger.info(f"Cleared notification cache for session {session_id}")

    async def _parse_trading_request(self, query: str) -> TradingRequest:
        """
        Parse natural language query to extract trading parameters

        Args:
            query: User's natural language query

        Returns:
            TradingRequest object with parsed parameters
        """
        try:
            parse_prompt = f"""
            Parse the following user query and extract auto trading configuration parameters:
            
            User query: "{query}"
            
            Please identify:
            1. crypto_symbols: List of cryptocurrency symbols to trade (e.g., BTC-USD, ETH-USD, SOL-USD)
               - If user mentions "Bitcoin", extract as "BTC-USD"
               - If user mentions "Ethereum", extract as "ETH-USD"
               - If user mentions "Solana", extract as "SOL-USD"
               - Always use format: SYMBOL-USD
            2. initial_capital: Initial trading capital in USD (default: 100000 if not specified)
            3. use_ai_signals: Whether to use AI-enhanced signals (default: true)
            4. agent_model: Model ID for trading decisions (default: DEFAULT_AGENT_MODEL)
            
            Examples:
            - "Trade Bitcoin and Ethereum with $50000" -> {{"crypto_symbols": ["BTC-USD", "ETH-USD"], "initial_capital": 50000, "use_ai_signals": true}}
            - "Start auto trading BTC-USD" -> {{"crypto_symbols": ["BTC-USD"], "initial_capital": 100000, "use_ai_signals": true}}
            - "Trade BTC with AI signals" -> {{"crypto_symbols": ["BTC-USD"], "initial_capital": 100000, "use_ai_signals": true}}
            - "Trade BTC with AI signals using DeepSeek model" -> {{"crypto_symbols": ["BTC-USD"], "initial_capital": 100000, "use_ai_signals": true, "agent_models": ["deepseek/deepseek-v3.1-terminus"]}}
            - "Trade Bitcoin, SOL, Eth and DOGE with 100000 capital, using x-ai/grok-4, deepseek/deepseek-v3.1-terminus model" -> {{"crypto_symbols": ["BTC-USD", "SOL-USD", "ETH-USD", "DOGE-USD"], "initial_capital": 100000, "use_ai_signals": true, "agent_models": ["x-ai/grok-4", "deepseek/deepseek-v3.1-terminus"]}}
            """

            response = await self.parser_agent.arun(parse_prompt)
            trading_request = response.content

            logger.info(f"Parsed trading request: {trading_request}")
            return trading_request

        except Exception as e:
            logger.error(f"Failed to parse trading request: {e}", exc_info=True)
            raise ValueError(
                f"Could not parse trading configuration from query: {query}"
            )

    async def _create_trading_instance(
        self,
        session_id: str,
        config: AutoTradingConfig,
    ) -> Dict[str, Any]:
        """
        Create a new trading instance with exchange adapter.
        
        Args:
            session_id: Session ID
            config: Trading configuration
            
        Returns:
            Trading instance dictionary
        """
        # Create exchange adapter based on config
        exchange_platform = config.exchange_platform if hasattr(config, 'exchange_platform') else "binance"
        logger.info(f"Using exchange platform: {exchange_platform}")
        
        if exchange_platform.lower() == "binance":
            # Get API credentials from environment or config
            import os
            testnet = os.getenv("IS_BINANCE_TESTNET") or getattr(config, 'binance_testnet', True)  # Default to testnet for safety
            testnetPrefix = "TESTNET_" if testnet else ""
            api_key = os.getenv(testnetPrefix + "BINANCE_API_KEY") or getattr(config, 'binance_api_key', '')
            api_secret = os.getenv(testnetPrefix + "BINANCE_API_SECRET") or getattr(config, 'binance_api_secret', '')
            
            if not api_key or not api_secret:
                logger.warning("Binance credentials not found, falling back to paper trading")
                exchange = create_exchange_adapter("paper", initial_balance=config.initial_capital)
            else:
                exchange = create_exchange_adapter(
                    "binance",
                    api_key=api_key,
                    api_secret=api_secret,
                    testnet=testnet
                )
        else:
            # Default to paper trading
            exchange = create_exchange_adapter("paper", initial_balance=config.initial_capital)
        
        # Connect to exchange
        connected = await exchange.connect()
        if not connected:
            logger.error(f"Failed to connect to {exchange_platform} exchange")
            raise Exception(f"Exchange connection failed: {exchange_platform}")
        
        # Create executor with exchange
        executor = TradingExecutor(config, exchange)
        
        # Initialize AI signal generator if enabled
        ai_signal_generator = self._initialize_ai_signal_generator(config)
        
        return {
            "executor": executor,
            "exchange": exchange,
            "config": config,
            "ai_signal_generator": ai_signal_generator,
        }

    def _initialize_ai_signal_generator(
        self, config: AutoTradingConfig
    ) -> Optional[AISignalGenerator]:
        """Initialize AI signal generator if configured"""
        if not config.use_ai_signals:
            logger.info("don't use AI signals")
            return None

        try:
            api_key = config.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                logger.warning("OpenRouter API key not provided, AI signals disabled")
                return None

            llm_client = OpenRouter(
                id=config.agent_model,
                api_key=api_key,
            )
            return AISignalGenerator(llm_client)

        except Exception as e:
            logger.error(f"Failed to initialize AI signal generator: {e}", exc_info=True)
            return None

    async def _get_instance_status_component_data(
        self, session_id: str, instance_id: str
    ) -> Optional[FilteredCardPushNotificationComponentData]:
        """
        Generate portfolio status report in rich text format

        Returns:
            FilteredCardPushNotificationComponentData object or None if instance not found
        """
        if session_id not in self.trading_instances:
            return None

        if instance_id not in self.trading_instances[session_id]:
            return None

        instance = self.trading_instances[session_id][instance_id]
        executor: TradingExecutor = instance["executor"]
        config: AutoTradingConfig = instance["config"]

        # Get comprehensive portfolio summary
        portfolio_summary = await executor.get_portfolio_summary()
        await executor.get_positions()

        # Calculate overall statistics
        total_pnl = portfolio_summary["portfolio"]["total_pnl"]
        portfolio_value = portfolio_summary["portfolio"]["total_value"]
        available_cash = portfolio_summary["cash"]["available"]
        initial_capital = portfolio_value - total_pnl
        pnl_pct = total_pnl / initial_capital * 100

        # Build rich text output
        output = []

        # Header
        output.append(f"📊 **Trading Portfolio Status** - {instance_id}")
        output.append("\n**Instance Configuration**")
        output.append(f"- Model: `{config.agent_model}`")
        output.append(f"- Symbols: {', '.join(config.crypto_symbols)}")
        output.append(
            f"- Status: {'🟢 Active' if instance['active'] else '🔴 Stopped'}"
        )

        # Portfolio Summary Section
        output.append("\n💰 **Portfolio Summary**")
        output.append("\n**Overall Performance**")
        output.append(f"- Initial Capital: `${initial_capital:,.2f}`")
        output.append(f"- Current Value: `${portfolio_value:,.2f}`")

        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        pnl_sign = "+" if total_pnl >= 0 else ""
        output.append(
            f"- Total P&L: {pnl_emoji} **{pnl_sign}${total_pnl:,.2f}** ({pnl_sign}{pnl_pct:.2f}%)"
        )

        output.append("\n**Cash Position**")
        output.append(f"- Available Cash: `${available_cash:,.2f}`")

        # Current Positions Section
        output.append(f"\n📈 **Current Positions ({len(executor.positions)})**")

        if executor.positions:
            output.append(
                "\n| Symbol | Type | Quantity | Avg Price | Current Price | Position Value | Unrealized P&L |"
            )
            output.append(
                "|--------|------|----------|-----------|---------------|----------------|----------------|"
            )

            for symbol, pos in executor.positions.items():
                try:
                    trading_symbol = f"{symbol}USDT"
                    current_prices = await executor.exchange.get_current_price(trading_symbol)
                    current_price = current_prices[trading_symbol]

                    # Calculate unrealized P&L
                    if pos.trade_type.value == "long":
                        unrealized_pnl = (current_price - pos.entry_price) * abs(
                            pos.quantity
                        )
                        position_value = abs(pos.quantity) * current_price
                    else:
                        unrealized_pnl = (pos.entry_price - current_price) * abs(
                            pos.quantity
                        )
                        position_value = pos.notional + unrealized_pnl

                    # Format row
                    pnl_emoji = "🟢" if unrealized_pnl >= 0 else "🔴"
                    pnl_sign = "+" if unrealized_pnl >= 0 else ""

                    output.append(
                        f"| **{symbol}** | {pos.trade_type.value.upper()} | "
                        f"{abs(pos.quantity):.4f} | ${pos.entry_price:,.2f} | "
                        f"${current_price:,.2f} | ${position_value:,.2f} | "
                        f"{pnl_emoji} {pnl_sign}${unrealized_pnl:,.2f} |"
                    )

                except Exception as e:
                    logger.warning(f"Failed to get price for {symbol}: {e}", exc_info=True)
                    # Fallback display with entry price only
                    output.append(
                        f"| **{symbol}** | {pos.trade_type.value.upper()} | "
                        f"{abs(pos.quantity):.4f} | ${pos.entry_price:,.2f} | "
                        f"N/A | ${pos.notional:,.2f} | N/A |"
                    )
        else:
            output.append("\n*No open positions*")

        component_data = FilteredCardPushNotificationComponentData(
            title=f"{config.agent_model} Portfolio Status",
            data="\n".join(output),
            filters=[config.agent_model],
            table_title="Portfolio Detail",
            create_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
        return component_data

    async def _get_session_portfolio_chart_data(self, session_id: str) -> str:
        """
        Generate FilteredLineChartComponentData for all instances in a session
        Uses forward-fill strategy to handle missing timestamps

        Data format:
        [
            ['Time', 'model1', 'model2', 'model3'],
            ['2025-10-21 10:00:00', 100000, 50000, 30000],
            ['2025-10-21 10:01:00', 100234, 50123, 30045],
            ...
        ]

        Returns:
            JSON string of FilteredLineChartComponentData
        """
        if session_id not in self.trading_instances:
            return ""

        # Collect portfolio value history from all instances
        # Store as {model_id: {'initial_capital': float, 'history': [(timestamp, value)]}}
        model_data = {}

        for instance_id, instance in self.trading_instances[session_id].items():
            executor: TradingExecutor = instance["executor"]
            config: AutoTradingConfig = instance["config"]
            model_id = config.agent_model

            portfolio_summary = await executor.get_portfolio_summary()
            total_pnl = portfolio_summary["portfolio"]["total_pnl"]
            portfolio_value = portfolio_summary["portfolio"]["total_value"]
            initial_capital = portfolio_value - total_pnl
            if model_id not in model_data:
                model_data[model_id] = {
                    "initial_capital": initial_capital,
                    "history": [],
                }

            portfolio_history = executor.get_portfolio_history()

            for snapshot in portfolio_history:
                model_data[model_id]["history"].append(
                    (snapshot.timestamp, snapshot.total_value)
                )

        if not model_data:
            return ""

        # Sort each model's history by timestamp
        for model_id in model_data:
            model_data[model_id]["history"].sort(key=lambda x: x[0])

        # Collect all unique timestamps across all models
        all_timestamps = set()
        for model_id, data in model_data.items():
            for timestamp, _ in data["history"]:
                all_timestamps.add(timestamp)

        if not all_timestamps:
            return ""

        sorted_timestamps = sorted(all_timestamps)
        model_ids = list(model_data.keys())

        # Build data array with forward-fill strategy
        # First row: ['Time', 'model1', 'model2', ...]
        data_array = [["Time"] + model_ids]

        # Track last known value for each model (for forward-fill)
        last_known_values = {
            model_id: data["initial_capital"] for model_id, data in model_data.items()
        }

        # Data rows: ['timestamp', value1, value2, ...]
        for timestamp in sorted_timestamps:
            timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            row = [timestamp_str]

            for model_id in model_ids:
                # Find value at this timestamp for this model
                value_at_timestamp = None
                for ts, val in model_data[model_id]["history"]:
                    if ts == timestamp:
                        value_at_timestamp = val
                        break

                # Update logic: use new value if found, otherwise forward-fill
                if value_at_timestamp is not None:
                    last_known_values[model_id] = value_at_timestamp
                    row.append(value_at_timestamp)
                else:
                    # Use last known value (forward-fill)
                    row.append(last_known_values[model_id])

            data_array.append(row)

        component_data = FilteredLineChartComponentData(
            title=f"Portfolio Value History - Session {session_id[:8]}",
            data=json.dumps(data_array),
            create_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )

        return component_data.model_dump_json()

    async def _handle_stop_command(
        self, session_id: str, query: str
    ) -> AsyncGenerator[StreamResponse, None]:
        """Handle stop command for trading instances"""
        query_lower = query.lower().strip()

        # Check if specific instance_id is provided
        instance_id = None
        if "instance_id:" in query_lower or "instance:" in query_lower:
            # Extract instance_id
            parts = query.split(":")
            if len(parts) >= 2:
                instance_id = parts[1].strip()

        if session_id not in self.trading_instances:
            yield streaming.message_chunk(
                "⚠️ No active trading instances found in this session.\n"
            )
            return

        if instance_id:
            # Stop specific instance
            if instance_id in self.trading_instances[session_id]:
                self.trading_instances[session_id][instance_id]["active"] = False
                executor = self.trading_instances[session_id][instance_id]["executor"]
                portfolio_value = await executor.get_portfolio_value()
                positions = await executor.get_positions()

                yield streaming.message_chunk(
                    f"🛑 **Trading Instance Stopped**\n\n"
                    f"Instance ID: `{instance_id}`\n"
                    f"Final Portfolio Value: ${portfolio_value:,.2f}\n"
                    f"Open Positions: {len(positions)}\n\n"
                )
            else:
                yield streaming.message_chunk(
                    f"⚠️ Instance ID '{instance_id}' not found.\n"
                )
        else:
            # Stop all instances in this session
            count = 0
            for inst_id in self.trading_instances[session_id]:
                self.trading_instances[session_id][inst_id]["active"] = False
                count += 1

            yield streaming.message_chunk(
                f"🛑 **All Trading Instances Stopped**\n\n"
                f"Stopped {count} instance(s) in session: {session_id[:8]}\n\n"
            )

    async def _handle_status_command(
        self, session_id: str
    ) -> AsyncGenerator[StreamResponse, None]:
        """Handle status query command"""
        if (
            session_id not in self.trading_instances
            or not self.trading_instances[session_id]
        ):
            yield streaming.message_chunk(
                "⚠️ No trading instances found in this session.\n"
            )
            return

        status_message = f"📊 **Session Status** - {session_id[:8]}\n\n"
        status_message += (
            f"**Total Instances:** {len(self.trading_instances[session_id])}\n\n"
        )

        for instance_id, instance in self.trading_instances[session_id].items():
            executor: TradingExecutor = instance["executor"]
            config: AutoTradingConfig = instance["config"]

            status = "🟢 Active" if instance["active"] else "🔴 Stopped"
            portfolio_value = await executor.get_portfolio_value()
            total_pnl = portfolio_value - config.initial_capital
            positions = await executor.get_positions()

            status_message += (
                f"**Instance:** `{instance_id}`  {status}\n"
                f"- Model: {config.agent_model}\n"
                f"- Symbols: {', '.join(config.crypto_symbols)}\n"
                f"- Portfolio Value: ${portfolio_value:,.2f}\n"
                f"- P&L: ${total_pnl:,.2f}\n"
                f"- Open Positions: {len(positions)}\n"
                f"- Total Trades: {len(executor.get_trade_history())}\n"
                f"- Checks: {instance['check_count']}\n\n"
            )

        logger.info(f"Status message: {status_message}")

    async def stream(
        self,
        query: str,
        session_id: str,
        task_id: str,
        dependencies: Optional[Dict] = None,
    ) -> AsyncGenerator[StreamResponse, None]:
        """
        Process trading requests and manage multiple trading instances per session.

        Args:
            query: User's natural language query
            session_id: Session ID
            task_id: Task ID
            dependencies: Optional dependencies

        Yields:
            StreamResponse: Trading setup, execution updates, and data visualizations
        """
        # Track created instances for cleanup
        created_instances = []

        try:
            logger.info(
                f"Processing auto trading request - session: {session_id}, task: {task_id}"
            )

            query_lower = query.lower().strip()

            # Handle stop commands
            if any(
                cmd in query_lower.split()
                for cmd in ["stop", "pause", "halt", "停止", "暂停"]
            ):
                async for response in self._handle_stop_command(session_id, query):
                    yield response
                return

            # Handle status query commands
            if any(
                cmd in query_lower.split()
                for cmd in ["status", "summary", "状态", "摘要"]
            ):
                async for response in self._handle_status_command(session_id):
                    yield response
                return

            # Parse natural language query to extract trading configuration
            yield streaming.message_chunk("🔍 **Parsing trading request...**\n\n")

            try:
                trading_request = await self._parse_trading_request(query)
                logger.info(f"Parsed request: {trading_request}")
            except Exception as e:
                logger.error(f"Failed to parse trading request: {e}", exc_info=True)
                yield streaming.failed(
                    "**Parse Error**: Could not parse trading configuration from your query. "
                    "Please specify cryptocurrency symbols (e.g., 'Trade Bitcoin and Ethereum')."
                )
                return

            # Initialize session structure if needed
            if session_id not in self.trading_instances:
                self.trading_instances[session_id] = {}

            # Initialize notification cache for this session
            self._init_notification_cache(session_id)

            # Get list of models to create instances for
            agent_models = trading_request.agent_models or [DEFAULT_AGENT_MODEL]

            # Create one trading instance per model
            yield streaming.message_chunk(
                f"🚀 **Creating {len(agent_models)} trading instance(s)...**\n\n"
            )

            for model_id in agent_models:
                # Generate unique instance ID for this model
                instance_id = self._generate_instance_id(task_id, model_id)

                # Create configuration for this specific model
                config = AutoTradingConfig(
                    initial_capital=trading_request.initial_capital or 100000,
                    crypto_symbols=trading_request.crypto_symbols,
                    use_ai_signals=trading_request.use_ai_signals or False,
                    agent_model=model_id,
                    exchange_platform=trading_request.exchange_platform,
                )

                # Create trading instance with exchange adapter
                try:
                    instance = await self._create_trading_instance(
                        session_id=session_id,
                        config=config,
                    )
                    
                    # Update instance with ID and tracking info
                    instance.update({
                        "instance_id": instance_id,
                        "active": True,
                        "created_at": datetime.now(),
                        "check_count": 0,
                        "last_check": None,
                    })
                    
                    # Store instance
                    self.trading_instances[session_id][instance_id] = instance
                    created_instances.append(instance_id)

                except Exception as e:
                    logger.error(f"Failed to create trading instance for {model_id}: {e}", exc_info=True)
                    yield streaming.message_chunk(
                        f"⚠️ **Failed to create instance for {model_id}**: {str(e)}\n\n"
                    )
                    continue

                # Display configuration for this instance
                executor = instance["executor"]
                ai_status = "✅ Enabled" if config.use_ai_signals else "❌ Disabled"
                config_message = (
                    f"✅ **Trading Instance Created**\n\n"
                    f"**Instance ID:** `{instance_id}`\n"
                    f"**Model:** `{model_id}`\n\n"
                    f"**Configuration:**\n"
                    f"- Exchange Platform: {config.exchange_platform}\n"
                    f"- Trading Symbols: {', '.join(config.crypto_symbols)}\n"
                    f"- Initial Capital: ${await executor.get_portfolio_value():,.2f}\n"
                    f"- Check Interval: {config.check_interval}s (1 minute)\n"
                    f"- Risk Per Trade: {config.risk_per_trade * 100:.1f}%\n"
                    f"- Max Positions: {config.max_positions}\n"
                    f"- AI Signals: {ai_status}\n\n"
                )

                yield streaming.message_chunk(config_message)

            # Summary message
            yield streaming.message_chunk(
                f"**Session ID:** `{session_id[:8]}`\n"
                f"**Total Active Instances in Session:** {len(self.trading_instances[session_id])}\n\n"
                f"🚀 **Starting continuous trading for all instances...**\n"
                f"All instances will run continuously until stopped.\n\n"
            )

            # Initialize all instances with portfolio snapshots
            # Use unified timestamp for initial snapshots to align chart data
            unified_initial_timestamp = datetime.now()
            for instance_id in created_instances:
                instance = self.trading_instances[session_id][instance_id]
                executor = instance["executor"]
                config = instance["config"]

                # Send initial portfolio snapshot - cache it
                portfolio_value = await executor.get_portfolio_value()

                initial_portfolio_msg = FilteredCardPushNotificationComponentData(
                    title=f"{config.agent_model} Portfolio",
                    data=f"💰 **Initial Portfolio**\nTotal Value: ${portfolio_value:,.2f}\nAvailable Capital: ${await executor.get_current_capital():,.2f}\n",
                    filters=[config.agent_model],
                    table_title="Portfolio Detail",
                    create_time=datetime.now(timezone.utc).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                )
                # Cache the initial notification
                self._cache_notification(session_id, initial_portfolio_msg)

            # Set check interval
            check_interval = DEFAULT_CHECK_INTERVAL

            # Create semaphore to limit concurrent instance processing (max 10)
            semaphore = asyncio.Semaphore(10)

            # Main trading loop - monitor all instances in parallel
            yield streaming.message_chunk(
                "📈 **Starting monitoring loop for all instances...**\n\n"
            )

            # Check if any instance is still active
            while any(
                self.trading_instances[session_id][inst_id]["active"]
                for inst_id in created_instances
                if inst_id in self.trading_instances[session_id]
            ):
                try:
                    # Create unified timestamp for this iteration to align snapshots
                    unified_timestamp = datetime.now()

                    # Process all active instances concurrently using task pool
                    tasks = []
                    for instance_id in created_instances:
                        # Skip if instance was removed or is inactive
                        if instance_id not in self.trading_instances[session_id]:
                            continue

                        instance = self.trading_instances[session_id][instance_id]
                        if not instance["active"]:
                            continue

                        # Create task for this instance with semaphore control and unified timestamp
                        task = asyncio.create_task(
                            self._process_trading_instance(
                                session_id, instance_id, semaphore, unified_timestamp
                            )
                        )
                        tasks.append(task)

                    # Wait for all instance tasks to complete (process concurrently)
                    if tasks:
                        # Gather all tasks and handle any exceptions
                        results = await asyncio.gather(*tasks, return_exceptions=True)

                        # Log any exceptions that occurred
                        for i, result in enumerate(results):
                            if isinstance(result, Exception):
                                logger.error(
                                    f"Task {i} failed with exception: {result}"
                                )

                    # After processing all instances, send batched notifications
                    cached_notifications = self._get_cached_notifications(session_id)
                    if cached_notifications:
                        logger.info(
                            f"Sending {len(cached_notifications)} cached notifications for session {session_id}"
                        )
                        # Convert all cached notifications to a list of dicts for batch sending
                        batch_data = [
                            notif.model_dump() for notif in cached_notifications
                        ]
                        # Send as a single batch component - frontend will receive all historical data
                        yield streaming.component_generator(
                            json.dumps(batch_data),
                            ComponentType.FILTERED_CARD_PUSH_NOTIFICATION,
                            component_id=f"trading_status_{session_id}",
                        )

                    # Send chart data (not cached, sent separately)
                    chart_data = await self._get_session_portfolio_chart_data(session_id)
                    if chart_data:
                        yield streaming.component_generator(
                            content=chart_data,
                            component_type=ComponentType.FILTERED_LINE_CHART,
                            component_id=f"portfolio_chart_{session_id}",
                        )

                    # Wait for next check interval - only sleep once after processing all instances
                    logger.info(f"Waiting {check_interval}s until next check...")
                    await asyncio.sleep(check_interval)

                except Exception as e:
                    logger.error(f"Error during trading cycle: {e}", exc_info=True)
                    yield streaming.message_chunk(
                        f"⚠️ **Error during trading cycle**: {str(e)}\n"
                        f"Continuing with next check...\n\n"
                    )
                    await asyncio.sleep(check_interval)

        except Exception as e:
            logger.error(f"Critical error in stream method: {e}", exc_info=True)
            yield streaming.failed(f"Critical error: {str(e)}")
        finally:
            # Mark all created instances as inactive but keep data for history
            if session_id in self.trading_instances:
                for instance_id in created_instances:
                    if instance_id in self.trading_instances[session_id]:
                        self.trading_instances[session_id][instance_id]["active"] = (
                            False
                        )
                        logger.info(f"Stopped instance: {instance_id}")
