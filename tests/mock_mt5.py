"""MockMT5Server — simulation d'exécution MT5 pour tests avancés

Simule:
  - Positions ouvertes
  - Order matching (fills, slippage, rejections)
  - History de deals
  - Account info (balance, equity)
  - Symbol info (bid/ask, spread)
"""
import random
import time
from dataclasses import dataclass
from unittest.mock import MagicMock


@dataclass
class MockPosition:
    ticket: int
    symbol: str
    type: int  # 0=BUY, 1=SELL
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float = 0.0
    magic: int = 999001
    comment: str = ""
    time: int = 0


@dataclass
class MockDeal:
    position_id: int
    symbol: str
    type: int
    volume: float
    price: float
    profit: float
    magic: int = 999001
    time: int = 0


@dataclass
class MockOrder:
    ticket: int
    symbol: str
    type: int
    volume: float
    price: float
    sl: float
    tp: float
    magic: int = 999001
    comment: str = ""


class MockMT5Server:
    """Simulateur MT5 — matching engine simplifié"""

    def __init__(self, initial_balance=200000.0, spread=0.0002, slippage_pts=0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.spread = spread
        self.slippage_pts = slippage_pts
        self._positions: list[MockPosition] = []
        self._deals: list[MockDeal] = []
        self._pending_orders: list[MockOrder] = []
        self._next_ticket = 1000
        self._prices = {}  # symbol -> (bid, ask)
        self._ticks = 0
        self.connected = True

        # MT5 constants
        self.ORDER_TYPE_BUY = 0
        self.ORDER_TYPE_SELL = 1
        self.ORDER_FILLING_IOC = 1
        self.ORDER_TIME_GTC = 0

    def set_price(self, symbol, bid, ask):
        self._prices[symbol] = (bid, ask)

    def get_price(self, symbol):
        p = self._prices.get(symbol)
        if p is None:
            base = 1.10 if symbol.startswith("EUR") or symbol.startswith("GBP") else 1.05 if symbol == "USDCHF" else 1.35
            bid = base + random.uniform(-0.001, 0.001)
            ask = bid + self.spread
            self._prices[symbol] = (bid, ask)
            p = (bid, ask)
        return p

    def connect(self):
        self.connected = True
        return True

    def disconnect(self):
        self.connected = False

    def health_check(self):
        return self.connected

    def get_account_info(self):
        info = MagicMock()
        info.balance = self.balance
        info.equity = self.equity
        info.margin = 0.0
        info.margin_free = self.equity
        info.name = "Mock Account"
        info.login = 12345
        info.server = "MockServer"
        info.currency = "USD"
        return info

    def get_symbol_info(self, symbol):
        bid, ask = self.get_price(symbol)
        info = MagicMock()
        info.bid = bid
        info.ask = ask
        info.point = 0.0001 if symbol != "USDJPY" else 0.001
        info.digits = 5 if symbol != "USDJPY" else 3
        info.spread = int(self.spread / (info.point or 0.0001))
        info.trade_calc_mode = 0
        return info

    def get_positions(self):
        return self._positions

    def get_pending_orders(self):
        return self._pending_orders

    def get_history(self, since, until):
        return self._deals

    def order_send(self, request):
        self._ticks += 1
        if not self.connected:
            result = MagicMock()
            result.retcode = 10004  # MARKET_UNKNOWN
            result.order = 0
            return result

        symbol = request.get("symbol", "EURUSD")
        direction = request.get("type", 0)
        volume = request.get("volume", 0.01)
        price = request.get("price", self.get_price(symbol)[0 if direction == 1 else 1])
        sl = request.get("sl", 0.0)
        tp = request.get("tp", 0.0)
        magic = request.get("magic", 999001)
        comment = request.get("comment", "")
        deviation = request.get("deviation", 20)

        # Simulate slippage
        if self.slippage_pts > 0 and deviation > 0:
            point = 0.0001
            slip = random.randint(0, min(self.slippage_pts, deviation)) * point
            if direction == 0:  # BUY
                price += slip
            else:
                price -= slip

        ticket = self._next_ticket
        self._next_ticket += 1

        pos = MockPosition(
            ticket=ticket, symbol=symbol, type=direction,
            volume=volume, price_open=price, sl=sl, tp=tp,
            profit=0.0, magic=magic, comment=comment,
            time=int(time.time()),
        )
        self._positions.append(pos)

        result = MagicMock()
        result.retcode = 10009  # TRADE_RETCODE_DONE
        result.order = ticket
        result.price = price
        result.volume = volume
        result.comment = comment
        return result

    def close_position(self, ticket=None, symbol=None):
        """Simule la fermeture d'une position (appelé par le test)"""
        for i, pos in enumerate(self._positions):
            if (ticket and pos.ticket == ticket) or (symbol and pos.symbol == symbol):
                # Calculate profit
                bid, ask = self.get_price(pos.symbol)
                close_price = bid if pos.type == 0 else ask
                if pos.type == 0:  # BUY
                    profit = (close_price - pos.price_open) * pos.volume * 100000
                else:
                    profit = (pos.price_open - close_price) * pos.volume * 100000
                profit = round(profit, 2)

                # Record deal
                deal = MockDeal(
                    position_id=pos.ticket, symbol=pos.symbol,
                    type=1 if pos.type == 0 else 0,  # opposite type
                    volume=pos.volume, price=close_price,
                    profit=profit, magic=pos.magic,
                    time=int(time.time()),
                )
                self._deals.append(deal)
                self.balance += profit
                self.equity = self.balance

                # Remove position
                removed = self._positions.pop(i)
                return removed, profit

        return None, 0.0

    def calc_profit(self, order_type, symbol, volume, price_open, price_close):
        bid, ask = self.get_price(symbol)
        if order_type == self.ORDER_TYPE_BUY:
            return (price_close - price_open) * volume * 100000 if price_close else 0
        return (price_open - price_close) * volume * 100000 if price_close else 0

    def get_rates(self, symbol, tf, count):
        """Generate mock price data"""
        base = self.get_price(symbol)[0]
        times = [int(time.time()) - i * 3600 for i in range(count)]
        closes = [base + random.uniform(-0.005, 0.005) for _ in range(count)]
        highs = [c + random.uniform(0, 0.003) for c in closes]
        lows = [c - random.uniform(0, 0.003) for c in closes]
        opens = [c - random.uniform(-0.002, 0.002) for c in closes]
        volumes = [random.randint(100, 10000) for _ in range(count)]
        return list(zip(times, opens, highs, lows, closes, volumes, strict=False))

    def get_rates_multi_tf(self, symbol, tfs, count=100):
        return {tf: self.get_rates(symbol, tf, count) for tf in tfs}

    @property
    def open_positions_count(self):
        return len(self._positions)

    @property
    def deals_count(self):
        return len(self._deals)
