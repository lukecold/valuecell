"""Formatting utilities for notifications and messages"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .models import (
    Position,
    TechnicalIndicators,
    TradeAction,
    TradeHistoryRecord,
    TradeType
)

logger = logging.getLogger(__name__)


class MessageFormatter:
    """Formats various messages and notifications"""

    @staticmethod
    def format_trade_notification(
        trade_details: Optional[TradeHistoryRecord], agent_name: str = "AutoTrading"
    ) -> str:
        """
        Format trade details into a notification message

        Args:
            trade_details: Trade execution details
            agent_name: Name of the agent

        Returns:
            Formatted notification message
        """
        if trade_details is None:
            logger.warning("No trade details provided")
            return "Trade executed"
        if trade_details.quantity == 0:
            logger.warning("Trade not executed")
            return "Trade not executed"

        try:
            symbol = trade_details.symbol
            action = trade_details.action
            trade_type = trade_details.trade_type
            timestamp = trade_details.timestamp

            if action == "opened":
                message = (
                    f"{agent_name} opened a {trade_type} position on {symbol}!\n"
                    f"{timestamp.strftime('%m/%d, %I:%M %p')}\n"
                    f"Price: ${trade_details.price:,.2f}\n"
                    f"Quantity: {trade_details.quantity:.4f}\n"
                    f"Notional: ${trade_details.notional:,.2f}"
                )
            else:  # closed
                # hours = int(trade_details["holding_time"].total_seconds() // 3600)
                # minutes = int(
                #     (trade_details["holding_time"].total_seconds() % 3600) // 60
                # )
                pnl = trade_details.pnl if trade_details.pnl is not None else 0
                pnl_sign = "+" if pnl >= 0 else ""

                entry_price = trade_details.price - pnl / trade_details.quantity
                entry_notional = trade_details.notional - pnl
                message = (
                    f"{agent_name} completed a {trade_type} trade on {symbol}!\n"
                    f"{timestamp.strftime('%m/%d, %I:%M %p')}\n"
                    f"Price: ${entry_price:,.2f} → ${trade_details.price:,.2f}\n"
                    f"Quantity: {trade_details.quantity:.4f}\n"
                    f"Notional: ${entry_notional:,.2f} → ${trade_details.notional:,.2f}\n"
                    # f"Holding time: {hours}H {minutes}M\n"
                    f"Net P&L: {pnl_sign}${pnl:,.2f}"
                )

            return message

        except Exception as e:
            logger.error(f"Failed to format trade notification: {e}", exc_info=True)
            return "Trade executed"

    @staticmethod
    def format_portfolio_notification(
        portfolio_value: float,
        positions_count: int,
        current_capital: float,
        agent_model: str,
        session_id: str,
        portfolio_history: list,
    ) -> tuple[str, Optional[str]]:
        """
        Format portfolio value notification for chart rendering

        Args:
            portfolio_value: Current portfolio value
            positions_count: Number of open positions
            current_capital: Available capital
            agent_model: Agent model name
            session_id: Current session ID
            portfolio_history: Historical portfolio data

        Returns:
            Tuple of (display message, chart data JSON)
        """
        try:
            timestamp = datetime.now(timezone.utc)

            # Append to history
            portfolio_history.append(
                {"timestamp": timestamp.isoformat(), "value": portfolio_value}
            )

            # Create chart data payload
            chart_data = {
                "id": f"AutoTradingAgent-{agent_model}",
                "filters": [
                    {"dimension": "Time", "gte": timestamp.isoformat()},
                    {"dimension": "Model", "=": agent_model},
                ],
                "data": {"Portfolio": portfolio_value},
            }

            display_message = (
                f"💰 Portfolio Update\n"
                f"Time: {timestamp.strftime('%m/%d, %I:%M %p UTC')}\n"
                f"Total Value: ${portfolio_value:,.2f}\n"
                f"Open Positions: {positions_count}\n"
                f"Available Capital: ${current_capital:,.2f}"
            )

            return display_message, json.dumps(chart_data)

        except Exception as e:
            logger.error(f"Failed to format portfolio notification: {e}")
            return "Portfolio update failed", None

    @staticmethod
    def format_market_analysis_notification(
        symbol: str,
        indicators: TechnicalIndicators,
        action: TradeAction,
        trade_type: TradeType,
        positions: Dict[str, Position],
        ai_reasoning: Optional[str] = None,
    ) -> str:
        """
        Format market analysis notification including HOLD decisions

        Args:
            symbol: Trading symbol
            indicators: Technical indicators
            action: Recommended action
            trade_type: Trade type
            positions: Current positions dictionary
            ai_reasoning: AI reasoning if available

        Returns:
            Formatted analysis message
        """
        try:
            timestamp = datetime.now(timezone.utc)

            # Format action with emoji
            action_emoji = {
                TradeAction.BUY: "🟢",
                TradeAction.SELL: "🔴",
                TradeAction.HOLD: "⏸️",
            }

            message = (
                f"📊 **Market Analysis - {symbol}**\n"
                f"Time: {timestamp.strftime('%m/%d, %I:%M %p UTC')}\n\n"
                f"**Current Price:** ${indicators.close_price:,.2f}\n"
                f"**Decision:** {action_emoji.get(action, '')} {action.value.upper()}"
            )

            if action != TradeAction.HOLD:
                message += f" ({trade_type.value.upper()})"

            message += "\n\n**Technical Indicators:**\n"

            # Add MACD
            if indicators.macd is not None and indicators.macd_signal is not None:
                macd_signal = (
                    "🟢 Bullish"
                    if indicators.macd > indicators.macd_signal
                    else "🔴 Bearish"
                )
                message += f"- MACD: {indicators.macd:.4f} / Signal: {indicators.macd_signal:.4f} ({macd_signal})\n"

            # Add RSI
            if indicators.rsi is not None:
                rsi_signal = (
                    "🟢 Oversold"
                    if indicators.rsi < 30
                    else ("🔴 Overbought" if indicators.rsi > 70 else "⚪ Neutral")
                )
                message += f"- RSI: {indicators.rsi:.2f} ({rsi_signal})\n"

            # Add EMAs
            if indicators.ema_12 is not None and indicators.ema_26 is not None:
                ema_signal = (
                    "🟢 Bullish"
                    if indicators.ema_12 > indicators.ema_26
                    else "🔴 Bearish"
                )
                message += f"- EMA 12/26: ${indicators.ema_12:,.2f} / ${indicators.ema_26:,.2f} ({ema_signal})\n"

            # Add Bollinger Bands
            if indicators.bb_upper is not None and indicators.bb_lower is not None:
                if indicators.close_price > indicators.bb_upper:
                    bb_signal = "🔴 Above Upper Band"
                elif indicators.close_price < indicators.bb_lower:
                    bb_signal = "🟢 Below Lower Band"
                else:
                    bb_signal = "⚪ Within Bands"
                message += f"- Bollinger Bands: ${indicators.bb_lower:,.2f} - ${indicators.bb_upper:,.2f} ({bb_signal})\n"

            # Add AI reasoning if available
            if ai_reasoning:
                message += f"\n**AI Analysis:**\n{ai_reasoning}\n"

            # Add current position info if exists
            asset = symbol.replace("USDT", "").replace("-USD", "")
            if asset in positions:
                pos = positions[asset]
                current_pnl = 0
                if pos.trade_type == TradeType.LONG:
                    current_pnl = (indicators.close_price - pos.entry_price) * abs(
                        pos.quantity
                    )
                else:
                    current_pnl = (pos.entry_price - indicators.close_price) * abs(
                        pos.quantity
                    )

                pnl_emoji = "🟢" if current_pnl >= 0 else "🔴"
                message += (
                    f"\n**Current Position:**\n"
                    f"- Type: {pos.trade_type.value.upper()}\n"
                    f"- Entry: ${pos.entry_price:,.2f}\n"
                    f"- Quantity: {abs(pos.quantity):.4f}\n"
                    f"- Unrealized P&L: {pnl_emoji} ${current_pnl:,.2f}\n"
                )
            else:
                message += f"\n**Current Position:** No open position for {asset}\n\n"

            return message

        except Exception as e:
            logger.error(f"Failed to format market analysis notification: {e}")
            return f"Market analysis for {symbol}"
