"""Exchange adapters for different trading platforms

This module provides adapters for various cryptocurrency exchanges,
allowing the AutoTradingAgent to trade on both paper (simulated) and live (real) exchanges.

Adapters:
- ExchangeBase: Abstract base class defining the exchange interface
- PaperTrading: Simulated trading (default)
- BinanceExchange: Live trading on Binance (requires API keys)
"""
from typing import Optional

from .base_exchange import ExchangeBase, ExchangeType, OrderStatus
from .binance_exchange import BinanceExchange
from .paper_trading import PaperTrading


def create_exchange_adapter(
        exchange_type: str,
        initial_balance: Optional[float] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = True,
) -> ExchangeBase:
    """
    Factory function to create exchange adapters

    Args:
        exchange_type: Type of exchange ("paper", "binance")
        initial_balance: Initial balance for paper trading
        api_key: API key for real exchanges
        api_secret: API secret for real exchanges
        testnet: Whether to use testnet (for Binance)

    Returns:
        ExchangeBase: Exchange adapter instance
    """
    exchange_type = exchange_type.lower()

    if exchange_type == "paper":
        return PaperTrading(initial_balance=initial_balance or 100000.0)
    elif exchange_type == "binance":
        if not api_key or not api_secret:
            raise ValueError("Binance exchange requires api_key and api_secret")
        return BinanceExchange(api_key=api_key, api_secret=api_secret, testnet=testnet)
    else:
        raise ValueError(f"Unsupported exchange type: {exchange_type}")


__all__ = [
    "ExchangeBase",
    "ExchangeType",
    "OrderStatus",
    "PaperTrading",
    "BinanceExchange",
    "create_exchange_adapter",
]
