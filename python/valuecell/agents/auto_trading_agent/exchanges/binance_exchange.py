"""Binance exchange adapter for live trading

This adapter connects to Binance API for real trading on live accounts.
Requires: API key and secret from Binance account settings.

WARNING: Real money trading - handle with care!
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp

from .base_exchange import (
    ExchangeBase,
    ExchangeType,
    Order,
    OrderStatus,
)
from ..models import (
    Position,
    TradeType,
)
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class BinanceAPIError(Exception):
    """Binance API specific error"""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Binance API Error {code}: {message}")


class BinanceExchange(ExchangeBase):
    """
    Binance exchange adapter for live trading.

    Features:
    - Connect to Binance API
    - Execute real trades
    - Monitor real-time positions
    - Handle Binance-specific errors
    - Support spot trading

    WARNING: Real implementation for live trading.
    Use testnet=True for testing before going live!
    """

    # API endpoints
    LIVE_API_URL = "https://api.binance.com"
    TESTNET_API_URL = "https://testnet.binance.vision"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        """
        Initialize Binance exchange adapter.

        Args:
            api_key: Binance API key
            api_secret: Binance API secret
            testnet: Use testnet for testing (default: False)

        Note:
            - testnet=True connects to https://testnet.binance.vision (for testing)
            - testnet=False connects to https://api.binance.com (real trading!)
        """
        super().__init__(ExchangeType.BINANCE)
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.base_url = self.TESTNET_API_URL # if testnet else self.LIVE_API_URL
        self.session: Optional[aiohttp.ClientSession] = None

        # Cache for exchange info
        self._exchange_info: Optional[Dict[str, Any]] = None
        self._symbol_info: Dict[str, Dict[str, Any]] = {}

        logger.info(
            f"BinanceExchange initialized in {'TESTNET' if testnet else 'LIVE'} mode."
        )

    # ============ Connection Management ============

    async def connect(self) -> bool:
        """
        Connect to Binance API.

        Returns:
            True if connection successful
        """
        try:
            logger.info("Connecting to Binance API...")
            
            # Create aiohttp session
            if self.session is None:
                self.session = aiohttp.ClientSession()

            # Test connection by pinging the API
            async with self.session.get(f"{self.base_url}/api/v3/ping") as response:
                if response.status != 200:
                    logger.error(f"Failed to ping Binance API: {response.status}")
                    return False

            # Validate API credentials
            account_info = await self._signed_request("GET", "/api/v3/account")
            if not account_info:
                logger.error("Failed to validate API credentials")
                return False

            # Load exchange information
            await self._load_exchange_info()

            self.is_connected = True
            logger.info("Successfully connected to Binance API")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Binance API: {e}", exc_info=True)
            self.is_connected = False
            return False

    async def disconnect(self) -> bool:
        """
        Disconnect from Binance API gracefully.

        Returns:
            True if disconnection successful
        """
        logger.info("Disconnecting from Binance API...")
        
        if self.session:
            await self.session.close()
            self.session = None

        self.is_connected = False
        logger.info("Disconnected from Binance API")
        return True

    async def validate_connection(self) -> bool:
        """
        Validate that connection is still active.

        Returns:
            True if connection is valid
        """
        if not self.is_connected or self.session is None:
            return False

        try:
            async with self.session.get(f"{self.base_url}/api/v3/ping") as response:
                return response.status == 200
        except Exception as e:
            logger.error(f"Connection validation failed: {e}", exc_info=True)
            return False

    # ============ Account Information ============

    async def get_balance(self) -> Dict[str, float]:
        """
        Get account balances from Binance.

        Returns:
            Dictionary mapping asset -> balance
            Example: {"USDT": 100000.0, "BTC": 1.5}
        """
        try:
            account_info = await self._signed_request("GET", "/api/v3/account")
            if not account_info:
                return {}

            balances = {}
            for balance in account_info.get("balances", []):
                free = float(balance.get("free", 0))
                if free > 0:  # Only include non-zero balances
                    balances[balance["asset"]] = free

            return balances

        except Exception as e:
            logger.error(f"Failed to fetch balances: {e}", exc_info=True)
            return {}

    async def get_asset_balance(self, asset: str) -> float:
        """
        Get balance for a specific asset.

        Args:
            asset: Asset symbol (e.g., "USDT", "BTC")

        Returns:
            Available balance
        """
        balances = await self.get_balance()
        return balances.get(asset.upper(), 0.0)

    async def get_available_cash(self) -> float:
        return await self.get_asset_balance("USDT")

    # ============ Market Data ============

    async def get_current_price(self, *symbols: str) -> Dict[str, float]:
        """
        Get current market price(s) from Binance.

        Args:
            *symbols: One or more trading symbols (e.g., "BTCUSDT", "ETHUSDT").
                          This is equivalent to a variadic `...string` in Go.

        Returns:
            Mapping from normalized symbol -> current price.
            Example: {"BTCUSDT": 65000.0, "ETHUSDT": 3500.0}
        """
        try:
            if self.session is None:
                logger.error("Session is not initialized")
                return {}

            if not symbols:
                logger.error(f"No symbols provided to get_current_price, got {symbols}", exc_info=True)
                return {}

            # Normalize and deduplicate symbols
            normalized_symbols = [self.normalize_symbol(sym) for sym in symbols]
            symbols_json_string = '["' + '","'.join(normalized_symbols) + '"]'

            url = f"{self.base_url}/api/v3/ticker/price"

            # Directly pass symbols as a request parameter
            params = {"symbols": symbols_json_string}

            async with self.session.get(url, params=params) as response:
                if response.status != 200:
                    logger.error(
                        f"Failed to fetch prices for symbols {normalized_symbols}: {response.status}",
                        exc_info=True,
                    )
                    return {}

                data = await response.json()
                prices: Dict[str, float] = {}

                # Expecting a list of ticker objects back; build mapping
                for item in data:
                    prices[item.get("symbol")] = float(item.get("price", 0))

                # Log missing symbols (if any)
                missing = normalized_symbols - prices.keys()
                if missing:
                    logger.warning(f"No price data returned for symbols: {missing}")

                return prices

        except Exception as e:
            logger.error(f"Failed to fetch prices for symbols {symbols}: {e}", exc_info=True)
            return {}

    async def get_24h_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get 24-hour ticker data from Binance.

        Args:
            symbol: Trading symbol

        Returns:
            Ticker data dictionary
        """
        try:
            symbol = self.normalize_symbol(symbol)
            url = f"{self.base_url}/api/v3/ticker/24hr"
            params = {"symbol": symbol}

            if self.session is None:
                return {}

            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        "symbol": data.get("symbol"),
                        "price": float(data.get("lastPrice", 0)),
                        "open": float(data.get("openPrice", 0)),
                        "high": float(data.get("highPrice", 0)),
                        "low": float(data.get("lowPrice", 0)),
                        "volume": float(data.get("volume", 0)),
                        "price_change": float(data.get("priceChange", 0)),
                        "price_change_percent": float(data.get("priceChangePercent", 0)),
                        "quote_volume": float(data.get("quoteVolume", 0)),
                    }
                else:
                    logger.error(f"Failed to fetch 24h ticker for {symbol}: {response.status}")
                    return {}

        except Exception as e:
            logger.error(f"Failed to fetch 24h ticker for {symbol}: {e}", exc_info=True)
            return {}

    # ============ Order Management ============

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        order_type: str = "limit",
        **kwargs,
    ) -> Order:
        """
        Place an order on Binance.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            side: "buy" or "sell"
            quantity: Order quantity
            price: Limit price (None for market orders)
            order_type: "limit" or "market"
            **kwargs: Binance-specific parameters

        Returns:
            Order object with Binance order_id
        """
        try:
            symbol = self.normalize_symbol(symbol)

            params = {
                "symbol": symbol,
                "side": side.upper(),
                "type": order_type.upper(),
                "quantity": quantity,
            }

            # Add price for limit orders
            if order_type.lower() == "limit":
                if price is None:
                    raise ValueError("Price is required for limit orders")
                params["price"] = price
                params["timeInForce"] = kwargs.get("timeInForce", "GTC")

            # Add any additional parameters
            params.update(kwargs)

            # Place order
            response = await self._signed_request("POST", "/api/v3/order", params)

            if not response:
                raise BinanceAPIError(-1, "Failed to place order")

            # Create Order object
            order = Order(
                order_id=str(response["orderId"]),
                symbol=symbol,
                side=side.lower(),
                quantity=float(response["origQty"]),
                price=float(response.get("price", 0)) if response.get("price") else 0.0,
                order_type=order_type.lower(),
            )

            # Update order status
            order.status = self._map_binance_status(response["status"])
            order.filled_quantity = float(response["executedQty"])

            if order.filled_quantity > 0:
                order.filled_price = float(response.get("cummulativeQuoteQty", 0)) / order.filled_quantity

            # Store order
            self.orders[order.order_id] = order

            logger.info(f"Order placed: {order.order_id} - {side} {quantity} {symbol} @ {price or 'market'}")
            return order

        except Exception as e:
            logger.error(f"Failed to place order: {e}", exc_info=True)
            # Return placeholder order with error
            order = Order(
                order_id="error",
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price or 0.0,
                order_type=order_type,
            )
            order.status = OrderStatus.REJECTED
            return order

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """
        Cancel an order on Binance.

        Args:
            symbol: Trading symbol
            order_id: Binance order ID

        Returns:
            True if cancellation successful
        """
        try:
            symbol = self.normalize_symbol(symbol)
            params = {
                "symbol": symbol,
                "orderId": order_id,
            }

            response = await self._signed_request("DELETE", "/api/v3/order", params)

            if response and response.get("status") == "CANCELED":
                # Update local order status
                if order_id in self.orders:
                    self.orders[order_id].status = OrderStatus.CANCELLED

                logger.info(f"Order cancelled: {order_id}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}", exc_info=True)
            return False

    async def get_order_status(self, symbol: str, order_id: str) -> OrderStatus:
        """
        Get order status from Binance.

        Args:
            symbol: Trading symbol
            order_id: Binance order ID

        Returns:
            Order status
        """
        try:
            symbol = self.normalize_symbol(symbol)
            params = {
                "symbol": symbol,
                "orderId": order_id,
            }

            response = await self._signed_request("GET", "/api/v3/order", params)

            if response:
                return self._map_binance_status(response["status"])

            return OrderStatus.PENDING

        except Exception as e:
            logger.error(f"Failed to fetch order status for {order_id}: {e}", exc_info=True)
            return OrderStatus.PENDING

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """
        Get open orders from Binance.

        Args:
            symbol: Optional symbol filter

        Returns:
            List of open Order objects
        """
        try:
            params = {}
            if symbol:
                params["symbol"] = self.normalize_symbol(symbol)

            response = await self._signed_request("GET", "/api/v3/openOrders", params)

            if not response:
                return []

            orders = []
            for order_data in response:
                order = self._parse_order_response(order_data)
                orders.append(order)
                # Update local cache
                self.orders[order.order_id] = order

            return orders

        except Exception as e:
            logger.error(f"Failed to fetch open orders: {e}", exc_info=True)
            return []

    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Order]:
        """
        Get order history from Binance.

        Args:
            symbol: Optional symbol filter (required for Binance)
            limit: Maximum orders to return

        Returns:
            List of Order objects
        """
        try:
            if not symbol:
                logger.warning("Symbol is required for Binance order history")
                return []

            params = {
                "symbol": self.normalize_symbol(symbol),
                "limit": min(limit, 1000),  # Binance max is 1000
            }

            response = await self._signed_request("GET", "/api/v3/allOrders", params)

            if not response:
                return []

            orders = []
            for order_data in response:
                order = self._parse_order_response(order_data)
                orders.append(order)

            return orders

        except Exception as e:
            logger.error(f"Failed to fetch order history: {e}", exc_info=True)
            return []

    # ============ Position Management ============

    async def get_open_positions(
        self, symbol: Optional[str] = None
    ) -> Dict[str, Position]:
        """
        Get open positions from Binance account.

        Note: For spot trading, positions are just non-zero balances.

        Args:
            symbol: Optional symbol filter

        Returns:
            Dictionary of positions with details
        """
        try:
            balances = await self.get_balance()
            # logger.info(f"Number of open positions: {len(balances)}")
            positions = {}

            assets = []
            trading_symbols = []
            for asset, balance in balances.items():
                # TODO: remove me! testnet doesn't support all coins, work around it
                if asset not in ['BNB', 'BTC', 'ETH', 'SOL', 'XRP', 'DOGE']:
                    # if asset != "USDT":
                    #     trading_symbol = f"{asset}USDT"
                    #     logger.warning(f"Closing asset {balance} {trading_symbol} because it is not supported on Binance")
                    #     await self._signed_request("DELETE", "/api/v3/openOrders", {"symbol": trading_symbol})
                    #     await self.execute_sell(symbol=trading_symbol, quantity=balance)
                    continue
                if balance != 0 and asset != "USDT":  # Exclude USDT
                    trading_symbol = f"{asset}USDT"
                    if symbol and trading_symbol != symbol:
                        continue
                    assets.append(asset)
                    trading_symbols.append(trading_symbol)
            if not trading_symbols:
                logger.warning(f"No positions matching symbol filter. Returning empty positions. balance: {balances}")
                return positions

            prices = await self.get_current_price(*trading_symbols)
            for asset in assets:
                balance = balances[asset]
                trading_symbol = f"{asset}USDT"
                current_price = prices[trading_symbol] if trading_symbol in prices else 0.0

                positions[asset] = Position(
                    symbol = asset,
                    quantity = balance,
                    entry_price = current_price,
                    entry_time = datetime.now(timezone.utc),
                    notional = balance * current_price,
                    trade_type = TradeType.LONG,
                )

            return positions

        except Exception as e:
            logger.error(f"Failed to fetch open positions: {e}", exc_info=True)
            return {}

    async def get_position_details(self, symbol: str) -> Optional[Position]:
        """
        Get details for a specific position.

        Args:
            symbol: Trading symbol

        Returns:
            Position details or None
        """
        positions = await self.get_open_positions(symbol)

        # Extract asset from symbol (e.g., BTC from BTCUSDT)
        asset = symbol.replace("USDT", "")

        return positions.get(asset)

    # ============ Trade Execution ============

    async def execute_buy(
        self,
        symbol: str,
        quantity: float,
        price: Optional[float] = None,
        **kwargs,
    ) -> Optional[Order]:
        """
        Execute a buy order on Binance.

        Args:
            symbol: Trading symbol
            quantity: Amount to buy
            price: Price (None for market order)
            **kwargs: Additional parameters

        Returns:
            Filled Order or None if failed
        """
        order_type = "market" if price is None else "limit"

        order = await self.place_order(
            symbol=symbol,
            side="buy",
            quantity=quantity,
            price=price,
            order_type=order_type,
            **kwargs
        )

        if order.status != OrderStatus.REJECTED:
            # For market orders, wait a bit and check status
            if order_type == "market":
                await self._wait_for_fill(symbol, order.order_id)

            return order

        return None

    async def execute_sell(
        self,
        symbol: str,
        quantity: float,
        price: Optional[float] = None,
        **kwargs,
    ) -> Optional[Order]:
        """
        Execute a sell order on Binance.

        Args:
            symbol: Trading symbol
            quantity: Amount to sell
            price: Price (None for market order)
            **kwargs: Additional parameters

        Returns:
            Filled Order or None if failed
        """
        order_type = "market" if price is None else "limit"

        order = await self.place_order(
            symbol=symbol,
            side="sell",
            quantity=quantity,
            price=price,
            order_type=order_type,
            **kwargs
        )

        if order.status != OrderStatus.REJECTED:
            # For market orders, wait a bit and check status
            if order_type == "market":
                await self._wait_for_fill(symbol, order.order_id)

            return order

        return None

    # ============ Utilities ============

    def normalize_symbol(self, symbol: str) -> str:
        """
        Normalize symbol to Binance format.

        Args:
            symbol: Original symbol (e.g., "BTC-USD")

        Returns:
            Binance format (e.g., "BTCUSDT")
        """
        return (symbol
                .replace("-USD", "USDT")
                .replace("-USDT", "USDT")
                .replace("_", "")
                .replace("-", "")
                .upper())

    async def get_fee_tier(self) -> Dict[str, float]:
        """
        Get current trading fee tier from Binance.

        Returns:
            Fee dictionary with maker/taker fees
        """
        try:
            response = await self._signed_request("GET", "/api/v3/account")

            if response:
                maker_commission = response.get("makerCommission", 10)
                taker_commission = response.get("takerCommission", 10)

                # Binance commissions are in basis points (1 = 0.01%)
                return {
                    "maker": maker_commission / 10000,
                    "taker": taker_commission / 10000,
                }

        except Exception as e:
            logger.error(f"Failed to fetch fee tier: {e}", exc_info=True)

        # Default Binance fees
        return {"maker": 0.001, "taker": 0.001}

    async def get_trading_limits(self, symbol: str) -> Dict[str, float]:
        """
        Get trading limits for a symbol on Binance.

        Args:
            symbol: Trading symbol

        Returns:
            Dictionary with trading limits
        """
        try:
            symbol = self.normalize_symbol(symbol)

            # Load exchange info if not cached
            if not self._exchange_info:
                await self._load_exchange_info()

            symbol_info = self._symbol_info.get(symbol, {})

            if not symbol_info:
                logger.warning(f"No symbol info found for {symbol}")
                return {
                    "min_quantity": 0.0001,
                    "max_quantity": 1000000,
                    "quantity_precision": 8,
                    "min_notional": 10.0,
                }

            # Parse filters
            limits = {
                "min_quantity": 0.0,
                "max_quantity": 0.0,
                "step_size": 0.0,
                "min_notional": 0.0,
                "quantity_precision": 8,
                "price_precision": 8,
            }

            for filter_data in symbol_info.get("filters", []):
                filter_type = filter_data.get("filterType")

                if filter_type == "LOT_SIZE":
                    limits["min_quantity"] = float(filter_data.get("minQty", 0))
                    limits["max_quantity"] = float(filter_data.get("maxQty", 0))
                    limits["step_size"] = float(filter_data.get("stepSize", 0))

                elif filter_type == "MIN_NOTIONAL":
                    limits["min_notional"] = float(filter_data.get("minNotional", 0))

                elif filter_type == "NOTIONAL":
                    limits["min_notional"] = float(filter_data.get("minNotional", 0))

            # Get precision from symbol info
            limits["quantity_precision"] = symbol_info.get("baseAssetPrecision", 8)
            limits["price_precision"] = symbol_info.get("quotePrecision", 8)

            return limits

        except Exception as e:
            logger.error(f"Failed to fetch trading limits for {symbol}: {e}", exc_info=True)
            return {
                "min_quantity": 0.0001,
                "max_quantity": 1000000,
                "quantity_precision": 8,
                "min_notional": 10.0,
            }

    # ============ Private Helper Methods ============

    async def _signed_request(
        self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Make a signed request to Binance API.

        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint
            params: Request parameters

        Returns:
            Response data or None if error
        """
        if self.session is None:
            logger.error("Session not initialized")
            return None

        try:
            if params is None:
                params = {}
            params["recvWindow"] = 60000

            # Set headers
            headers = {
                "X-MBX-APIKEY": self.api_key
            }

            # build URL
            url = f"{self.base_url}{endpoint}"

            # Create signature
            api_secret = self.api_secret.encode("utf-8")
            params["timestamp"] = int(time.time() * 1000)
            query_string = urlencode(params).encode("utf-8")
            signature = hmac.new(
                api_secret,
                query_string,
                hashlib.sha256
            ).hexdigest()
            params["signature"] = signature

            if method == "GET":
                async with self.session.get(url, params=params, headers=headers) as response:
                    return await self._handle_response(response)

            elif method == "POST":
                async with self.session.post(url, params=params, headers=headers) as response:
                    return await self._handle_response(response)

            elif method == "DELETE":
                async with self.session.delete(url, params=params, headers=headers) as response:
                    return await self._handle_response(response)

        except Exception as e:
            logger.error(f"Signed request failed: {e}", exc_info=True)
            return None

    async def _handle_response(self, response: aiohttp.ClientResponse) -> Optional[Dict[str, Any]]:
        """Handle API response and errors"""
        try:
            data = await response.json()

            if response.status == 200:
                return data

            # Handle error
            error_code = data.get("code", -1)
            error_msg = data.get("msg", "Unknown error")
            logger.error(f"Binance API error {error_code}: {error_msg}")
            raise BinanceAPIError(error_code, error_msg)

        except aiohttp.ContentTypeError:
            logger.error(f"Invalid response content type: {response.status}")
            return None

    async def _load_exchange_info(self) -> None:
        """Load exchange information and symbol details"""
        try:
            if self.session is None:
                return

            url = f"{self.base_url}/api/v3/exchangeInfo"
            async with self.session.get(url) as response:
                if response.status == 200:
                    self._exchange_info = await response.json()

                    # Cache symbol information
                    for symbol_data in self._exchange_info.get("symbols", []):
                        symbol = symbol_data.get("symbol")
                        if symbol:
                            self._symbol_info[symbol] = symbol_data

                    logger.info(f"Loaded info for {len(self._symbol_info)} symbols")

        except Exception as e:
            logger.error(f"Failed to load exchange info: {e}", exc_info=True)

    def _map_binance_status(self, binance_status: str) -> OrderStatus:
        """Map Binance order status to OrderStatus enum"""
        status_map = {
            "NEW": OrderStatus.PENDING,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "PENDING_CANCEL": OrderStatus.PENDING,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.EXPIRED,
        }
        return status_map.get(binance_status, OrderStatus.PENDING)

    def _parse_order_response(self, order_data: Dict[str, Any]) -> Order:
        """Parse Binance order response into Order object"""
        order = Order(
            order_id=str(order_data["orderId"]),
            symbol=order_data["symbol"],
            side=order_data["side"].lower(),
            quantity=float(order_data["origQty"]),
            price=float(order_data.get("price", 0)),
            order_type=order_data["type"].lower(),
        )

        order.status = self._map_binance_status(order_data["status"])
        order.filled_quantity = float(order_data["executedQty"])

        if order.filled_quantity > 0:
            cumm_quote_qty = float(order_data.get("cummulativeQuoteQty", 0))
            order.filled_price = cumm_quote_qty / order.filled_quantity

        return order

    async def _wait_for_fill(self, symbol: str, order_id: str, timeout: int = 5) -> None:
        """Wait for order to be filled (for market orders)"""
        import asyncio

        start_time = time.time()
        while time.time() - start_time < timeout:
            status = await self.get_order_status(symbol, order_id)

            if status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]:
                break

            await asyncio.sleep(0.5)

    # ============ Error Handling ============

    async def handle_api_error(self, error: Dict[str, Any]) -> bool:
        """
        Handle API errors from Binance.

        Args:
            error: Error response from Binance

        Returns:
            True if error was handled, False if critical
        """
        error_code = error.get("code", -1)
        error_msg = error.get("msg", "Unknown error")

        # Map error codes to severity
        critical_errors = [
            -2015,  # Invalid API key
            -1022,  # Invalid signature
        ]

        if error_code in critical_errors:
            logger.critical(f"Critical Binance error {error_code}: {error_msg}")
            self.is_connected = False
            return False

        logger.error(f"Binance error {error_code}: {error_msg}")
        return True

    # ============ WebSocket Subscriptions (Placeholder) ============

    async def subscribe_to_ticker(self, symbol: str, callback) -> bool:
        """
        Subscribe to real-time ticker updates via WebSocket.

        TODO: Future implementation
        - Connect to Binance WebSocket
        - Subscribe to ticker stream
        - Call callback on each update
        - Handle reconnection

        Args:
            symbol: Trading symbol
            callback: Callback function for updates

        Returns:
            True if subscription successful
        """
        logger.info(f"[TODO] WebSocket ticker subscription for {symbol}...")
        return False

    async def subscribe_to_trades(self, symbol: str, callback) -> bool:
        """
        Subscribe to real-time trade updates via WebSocket.

        TODO: Future implementation
        - Connect to Binance WebSocket
        - Subscribe to trades stream
        - Call callback on each trade

        Args:
            symbol: Trading symbol
            callback: Callback function for updates

        Returns:
            True if subscription successful
        """
        logger.info(f"[TODO] WebSocket trade subscription for {symbol}...")
        return False
