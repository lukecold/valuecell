"""Trading execution and position management (refactored)"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .exchanges import ExchangeBase
from .models import (
    AutoTradingConfig,
    PortfolioValueSnapshot,
    Position,
    TradeAction,
    TradeHistoryRecord,
    TradeType,
)
from .trade_recorder import TradeRecorder

logger = logging.getLogger(__name__)


class TradingExecutor:
    """
    Orchestrates trade execution using specialized modules.

    This is the main facade that coordinates:
    - Position management (via PositionManager)
    - Trade recording (via TradeRecorder)
    - Cash management (via PositionManager)
    """

    def __init__(self, config: AutoTradingConfig, exchange: ExchangeBase):
        """
        Initialize trading executor.

        Args:
            config: Auto trading configuration
        """
        self.config = config
        self.exchange = exchange

        # Position tracking (managed by exchange but cached here)
        self.positions: Dict[str, Position] = {}

        # Trade history
        self.trade_history: List[Dict] = []

        # Portfolio snapshots for historical tracking
        self.portfolio_snapshots: List[Dict] = []
        self.position_snapshots: List[Dict] = []

        # Trade recorder
        self.total_pnl = 0.0
        self._trade_recorder = TradeRecorder()

    async def close(self):
        if self.exchange is not None:
            await self.exchange.disconnect()

    async def execute_trade(
        self,
        symbol: str,
        action: TradeAction,
        quantity: float,
    ) -> Optional[TradeHistoryRecord]:
        """
        Execute a trade (open or close position).

        Args:
            symbol: Trading symbol
            action: Trade action (buy/sell)
            trade_type: Trade type (long/short)
            indicators: Current technical indicators

        Returns:
            Trade execution details or None if execution failed
        """
        if action == TradeAction.HOLD:
            return None

        # initialize positions
        if not self.positions :
            self.positions = await self.exchange.get_open_positions()

        asset = symbol.replace("USDT", "").replace("-USD", "")
        if len(self.positions) == self.config.max_positions and asset not in self.positions:
            logger.info(f"Max positions reached ({self.config.max_positions}). Exiting trade.")
            return None

        try:
            timestamp = datetime.now(timezone.utc)

            current_prices = await self.exchange.get_current_price(symbol)
            current_price = current_prices.get(symbol)
            if not current_price:
                logger.error(f"Could not get price for {symbol}. Exiting trade.")
                return None

            # Calculate position size
            if action == TradeAction.BUY:
                available_cash = await self.exchange.get_available_cash()
                max_quantity = available_cash / current_price
                if quantity > max_quantity:
                    logger.warning(
                        f"Buy execution of {quantity:.2f} {symbol}@{current_price:.2f} exceeds available cash {max_quantity:.2f}."
                        f"Reducing to {max_quantity:.2f}"
                    )
                    quantity = max_quantity
                quantity_sign = 1
            else:
                quantity_sign = -1
            notional = quantity * current_price

            order = await self.exchange.place_order(
                symbol=symbol,
                side=action,
                order_type="market",
                quantity=quantity
            )
            signed_quantity = order.filled_quantity * quantity_sign

            current_price = order.filled_price
            # cancel the unfilled order
            try:
                await self.exchange.cancel_order(symbol, order.order_id)
            except Exception as e:
                logger.warning(f"Failed to cancel order {order.order_id}. Moving on: {e}", exc_info=True)

            # Update position tracking
            pnl = 0
            if asset in self.positions:
                # Average down
                existing_pos = self.positions[asset]
                total_quantity = existing_pos.quantity + signed_quantity
                if (existing_pos.quantity > 0 and signed_quantity > 0) or (existing_pos.quantity < 0 and signed_quantity < 0):
                    avg_price = (
                                        (existing_pos.entry_price * existing_pos.quantity) +
                                        (current_price * signed_quantity)
                                ) / total_quantity
                    existing_pos.entry_price = avg_price
                else:
                    pnl = (quantity_sign * (existing_pos.entry_price - current_price) # price change
                           * min(quantity, abs(existing_pos.quantity)))               # closed quantity
                    self.total_pnl += pnl
                existing_pos.quantity = total_quantity
                existing_pos.trade_type = TradeType.LONG if total_quantity > 0 else TradeType.SHORT
            else:
                # New position
                self.positions[asset] = Position(
                    symbol=asset,
                    entry_price=current_price,
                    quantity=signed_quantity,
                    trade_type=TradeType.LONG if signed_quantity > 0 else TradeType.SHORT,
                    entry_time=datetime.now(),
                    notional=signed_quantity * current_price
                )

            # Record trade
            trade_record = TradeHistoryRecord(
                timestamp=timestamp,
                symbol=symbol,
                action=action,
                trade_type=TradeType.LONG if action == TradeAction.BUY else TradeType.SHORT,
                price=current_price,
                quantity=quantity,
                notional=notional,
                pnl=pnl,
                portfolio_value_after=await self.get_portfolio_value(),
                cash_after=await self.exchange.get_available_cash(),
            )
            self._trade_recorder.record_trade(trade_record)

            return trade_record

        except Exception as e:
            logger.error(f"Failed to execute trade for {symbol}: {e}", exc_info=True)
            return None

    # ============ Portfolio Queries ============

    async def get_portfolio_value(self) -> float:
        """
        Get total portfolio value (cash + positions)

        Returns:
            Total portfolio value
        """
        # Get cash balance
        cash = await self.exchange.get_available_cash()

        # Get position values
        position_value = 0.0
        await self.get_positions()
        positions = await self.exchange.get_open_positions()
        for asset, position in positions.items():
            current_price = position.entry_price
            if position.trade_type == TradeType.LONG:
                position_value += abs(position.quantity) * current_price
            else:  # SHORT
                position_value -= abs(position.quantity) * current_price

        return cash + position_value

    async def get_positions(self) -> Dict[str, Position]:
        """Get all positions"""
        if not self.positions:
            self.positions = await self.exchange.get_open_positions()
        return self.positions

    async def get_portfolio_summary(self) -> Dict:
        """Get complete portfolio summary"""
        total_value = await self.get_portfolio_value()
        return {
            "cash": {
                "available": await self.exchange.get_available_cash(),
            },
            "portfolio": {
                "total_pnl": self.total_pnl,
                "total_value": total_value
            },
        }

    async def get_current_capital(self) -> float:
        """Get available cash"""
        return await self.exchange.get_available_cash()

    @property
    async def current_capital(self) -> float:
        """Property for backward compatibility"""
        return await self.exchange.get_available_cash()

    # ============ History Management ============

    def get_trade_history(self) -> List[TradeHistoryRecord]:
        """Get all trade history"""
        return self.exchange.get_all_trades()

    def get_portfolio_history(self) -> List[PortfolioValueSnapshot]:
        """Get all portfolio"""
        return []

    # ============ Statistics ============

    def get_trade_statistics(self) -> Dict:
        """Get trading statistics"""
        return self._trade_recorder.get_trade_statistics()

    def get_symbol_statistics(self, symbol: str) -> Dict:
        """Get statistics for a symbol"""
        return self._trade_recorder.get_symbol_statistics(symbol)

    def get_daily_statistics(self) -> Dict[str, Dict]:
        """Get daily P&L breakdown"""
        return self._trade_recorder.get_daily_statistics()
