import logging

import MetaTrader5 as mt5

import config_simple as cfg

logger = logging.getLogger("mt5")


class MT5Connector:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_FILLING_IOC = 1
    ORDER_TIME_GTC = 0

    def __init__(self, login, password, server):
        self.login = login
        self.password = password
        self.server = server
        self.magic = cfg.ROBOT_MAGIC
        self.connected = False

    def connect(self):
        if not mt5.initialize(timeout=30000, portable=True):
            logger.error("MT5 initialization failed (timeout 30s)")
            return False
        if not mt5.login(self.login, password=self.password, server=self.server):
            logger.error("MT5 login failed")
            return False
        # Activer Market Watch pour tous les symboles du robot
        try:
            import config_simple as cfg
            for sym in cfg.SYMBOLS:
                mt5.symbol_select(sym, True)
        except Exception:
            pass
        self.connected = True
        info = mt5.account_info()
        if info is not None:
            logger.info(f"Connected: Balance={info.balance:.2f}, Equity={info.equity:.2f}")
        else:
            logger.warning("Connected but account_info() returned None")
        return True

    def disconnect(self):
        mt5.shutdown()
        self.connected = False

    def health_check(self):
        try:
            info = mt5.account_info()
            if info is None:
                return False
            terminal = mt5.terminal_info()
            if terminal is None:
                return False
            return terminal.connected
        except (RuntimeError, OSError, TypeError):
            return False

    def get_positions(self):
        all_pos = mt5.positions_get() or []
        our_pos = [p for p in all_pos if p.magic == self.magic]
        logger.debug(f"get_positions: total={len(all_pos)}, our={len(our_pos)}")
        return our_pos

    def get_pending_orders(self):
        return [o for o in (mt5.orders_get() or []) if o.magic == self.magic]

    def get_symbol_info(self, symbol):
        return mt5.symbol_info(symbol)

    def get_tick(self, symbol):
        return mt5.symbol_info_tick(symbol)

    def get_rates(self, symbol, timeframe, count=100):
        # Accepte string ("M1", "H1") ou int (mt5.TIMEFRAME_M1)
        if isinstance(timeframe, int):
            tf = timeframe
        else:
            tf_map = {
                "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
                "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1
            }
            tf = tf_map.get(timeframe, mt5.TIMEFRAME_H1)
        return mt5.copy_rates_from_pos(symbol, tf, 0, count)

    def get_rates_multi_tf(self, symbol, timeframes, count=100):
        result = {}
        for tf_name in timeframes:
            r = self.get_rates(symbol, tf_name, count)
            if r is not None and len(r) >= count * 0.8:
                result[tf_name] = r
        return result

    def calc_profit(self, order_type, symbol, volume, price_open, price_close):
        """Retourne le profit signé (None si MT5 déconnecté). Le caller gère abs() si besoin."""
        try:
            return mt5.order_calc_profit(order_type, symbol, volume, price_open, price_close)
        except (RuntimeError, OSError, TypeError):
            return None

    def order_send(self, request):
        return mt5.order_send(request)

    def close_position(self, position):
        tick = mt5.symbol_info_tick(position.symbol)
        if tick is None:
            logger.error(f"Cannot close {position.symbol}: tick is None (market closed?)")
            return None
        ct = 1 if position.type == 0 else 0
        price = tick.ask if ct == 0 else tick.bid
        req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=position.symbol,
            volume=position.volume, type=ct, position=position.ticket,
            price=price, magic=self.magic, comment="CLOSE_SIMPLE")
        return mt5.order_send(req)

    def update_sl(self, position, new_sl):
        req = dict(action=mt5.TRADE_ACTION_SLTP, position=position.ticket,
            symbol=position.symbol, sl=new_sl, tp=position.tp, magic=self.magic)
        return mt5.order_send(req)

    def get_account_info(self):
        return mt5.account_info()

    def ping(self) -> bool:
        """Keepalive : vérifie la connexion MT5, tente un appel léger.
        Retourne True si la connexion est active."""
        try:
            info = mt5.terminal_info()
            if info is None:
                return False
            return bool(info.connected)
        except (RuntimeError, OSError, TypeError):
            return False

    def reconnect(self) -> bool:
        """Tente une reconnexion complète à MT5."""
        logger.info("[MT5] Tentative de reconnexion...")
        try:
            mt5.shutdown()
        except Exception:
            pass
        import time as _time
        _time.sleep(2)
        if self.connect():
            logger.info("[MT5] Reconnexion réussie")
            return True
        logger.error("[MT5] Échec de reconnexion")
        return False

    def get_history(self, from_time, to_time):
        return mt5.history_deals_get(from_time, to_time)
