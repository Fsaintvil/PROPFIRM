from __future__ import annotations

import concurrent.futures
import logging
import os
from typing import Any, Callable, Optional

import MetaTrader5 as mt5

import config_simple as cfg

logger = logging.getLogger("mt5")

# Chemin du terminal MT5 depuis .env (prioritaire) ou auto-détection
_MT5_TERMINAL_PATH = os.environ.get("MT5_TERMINAL_PATH", "")
if _MT5_TERMINAL_PATH and not os.path.exists(_MT5_TERMINAL_PATH):
    logger.warning(f"MT5_TERMINAL_PATH invalide: {_MT5_TERMINAL_PATH} — fallback auto-detect")
    _MT5_TERMINAL_PATH = ""


class MT5Connector:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_FILLING_IOC = 1
    ORDER_TIME_GTC = 0

    # ⏱ Thread pool partagé pour les appels MT5 avec timeout
    # Un seul worker suffit — MT5 est monothreadé en interne.
    _executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5_timeout")

    def __init__(self, login: int, password: str, server: str) -> None:
        self.login = login
        self.password = password
        self.server = server
        self.magic = cfg.ROBOT_MAGIC
        self.connected = False

    def _call_with_timeout(self, fn: Callable, timeout: int = 30, name: str = "mt5_call", default: Any = None) -> Any:
        """Exécute un appel MT5 dans un thread séparé avec timeout.

        🔧 FIX 6 Juillet 2026: Empêche les appels MT5 bloquants de geler
        la boucle principale. Si l'appel dépasse le timeout, on retourne
        la valeur par défaut et on log une erreur.

        Args:
            fn: Fonction à exécuter (sans arguments — déjà liée via lambda)
            timeout: Timeout en secondes
            name: Nom de l'appel pour le logging
            default: Valeur retournée si timeout

        Retourne:
            Résultat de fn() ou default si timeout/erreur
        """
        future = self._executor.submit(fn)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.error(f"[MT5 TIMEOUT] {name} bloque depuis >{timeout}s")
            return default
        except Exception as e:
            logger.error(f"[MT5 ERROR] {name}: {e}")
            return default

    def connect(self) -> bool:
        import subprocess as _sp
        import time as _time

        # 🔧 FIX DU 2 JUILLET 2026: Ne plus tuer systématiquement terminal64.exe.
        # L'ancien code tuait TOUS les terminaux, ce qui causait une boucle infinie
        # quand 2 instances du robot coexistaient (chaque instance tuait le terminal de l'autre).
        # Nouvelle approche: essayer d'abord de se connecter au terminal existant.
        # Si ça échoue avec IPC timeout, ALORS seulement tuer les zombies.
        for attempt in range(2):
            if attempt == 1:
                # Seconde tentative: tuer les terminaux zombies
                try:
                    logger.warning("[CONNECT] Attempt 2: killing zombie terminal64.exe before retry...")
                    _sp.run(["taskkill", "/F", "/IM", "terminal64.exe"], capture_output=True, timeout=10)
                    _time.sleep(1)
                except Exception as _e:
                    logger.warning(f"[CONNECT] taskkill ignoré: {_e}")

            # 🔧 FIX IPC TIMEOUT: Passer les credentials DANS initialize() pour que le terminal
            # FTMO se connecte immédiatement au serveur au lieu de rester sur l'écran de login.
            # Quand initialize() est appelé sans login/password/server, le terminal FTMO démarre
            # mais reste bloqué sur l'écran de connexion → IPC timeout (-10005) après 60s.
            init_kwargs = {
                "login": self.login,
                "password": self.password,
                "server": self.server,
                "timeout": 60000,  # 60s timeout pour FTMO terminal (démarrage lent)
            }
            if _MT5_TERMINAL_PATH:
                init_kwargs["path"] = _MT5_TERMINAL_PATH
                logger.info(f"Using terminal path: {_MT5_TERMINAL_PATH}")
            logger.info(f"Connecting to MT5: server={self.server}, login={self.login}")
            if not mt5.initialize(**init_kwargs):
                err = mt5.last_error()
                logger.error(f"MT5 initialization failed: {err}")
                if attempt == 0:
                    logger.warning("[CONNECT] Will retry after killing zombie terminals...")
                    mt5.shutdown()
                    continue  # Retry with taskkill
                return False
            # Succès: sortir de la boucle
            break

        else:
            # La boucle s'est terminée sans break → les 2 tentatives ont échoué
            return False

        logger.info("MT5 initialize + login OK (credentials passed in initialize)")
        # Activer Market Watch pour tous les symboles du robot
        try:
            import config_simple as cfg

            for sym in cfg.SYMBOLS:
                mt5.symbol_select(sym, True)
        except Exception as e:
            logger.warning(f"[CONNECT] symbol_select error during connect: {e}")
        self.connected = True
        info = mt5.account_info()
        if info is not None:
            logger.info(f"Connected: Balance={info.balance:.2f}, Equity={info.equity:.2f}")
        else:
            logger.warning("Connected but account_info() returned None")
        return True

    def disconnect(self) -> None:
        mt5.shutdown()
        self.connected = False

    def health_check(self) -> bool:
        """Vérifie la connexion MT5 avec cache court (10s) et 1 retry.
        Évite les faux positifs dus à des ralentissements réseau MT5.

        🔧 FIX 6 Juillet 2026: Timeout 15s avec wrapper pour éviter freeze.
        """
        import time as _time

        now = _time.time()
        # Cache: ne pas appeler MT5 plus d'une fois toutes les 10s
        if hasattr(self, "_hc_cache") and hasattr(self, "_hc_cache_time"):
            if now - self._hc_cache_time < 10:
                return self._hc_cache

        for attempt in range(2):  # 1 retry
            try:
                info = self._call_with_timeout(
                    lambda: mt5.account_info(),
                    timeout=15,
                    name="health_check.account_info",
                )
                if info is None:
                    _time.sleep(0.5)
                    continue
                terminal = self._call_with_timeout(
                    lambda: mt5.terminal_info(),
                    timeout=15,
                    name="health_check.terminal_info",
                )
                if terminal is None:
                    _time.sleep(0.5)
                    continue
                result = bool(terminal.connected)
                if result:
                    self._hc_cache = result
                    self._hc_cache_time = now
                return result
            except (RuntimeError, OSError, TypeError):
                _time.sleep(0.5)
                continue
        self._hc_cache = False
        self._hc_cache_time = now
        return False

    def get_positions(self) -> list[Any]:
        # 🔧 FIX 6 Juillet 2026: Timeout 15s pour éviter freeze
        all_pos = self._call_with_timeout(
            lambda: mt5.positions_get() or [],
            timeout=15,
            name="positions_get",
            default=[],
        )
        if not isinstance(all_pos, list):
            logger.warning(f"get_positions: type inattendu {type(all_pos)} — fallback liste vide")
            all_pos = []
        our_pos = [p for p in all_pos if p.magic == self.magic]
        logger.debug(f"get_positions: total={len(all_pos)}, our={len(our_pos)}")
        return our_pos

    def get_pending_orders(self) -> list[Any]:
        # 🔧 FIX 6 Juillet 2026: Timeout 15s
        orders = self._call_with_timeout(
            lambda: mt5.orders_get() or [],
            timeout=15,
            name="orders_get",
            default=[],
        )
        if not isinstance(orders, list):
            return []
        return [o for o in orders if o.magic == self.magic]

    def get_symbol_info(self, symbol: str) -> Any:
        return self._call_with_timeout(
            lambda: mt5.symbol_info(symbol),
            timeout=10,
            name=f"symbol_info({symbol})",
        )

    def get_tick(self, symbol: str) -> Any:
        return self._call_with_timeout(
            lambda: mt5.symbol_info_tick(symbol),
            timeout=10,
            name=f"symbol_info_tick({symbol})",
        )

    def get_rates(self, symbol: str, timeframe: str | int, count: int = 10000) -> Any:
        """Récupère les bougies MT5. Cap à 10000 bars (MAX dispo: 33K H4, 9K H1 US500).
        count=100000 causait None sur tous les symboles (MT5 retourne None si count > dispo).

        🔧 FIX 6 Juillet 2026: Timeout 30s pour éviter freeze de 5.7h.
        Le thread externe watchdog servira de filet de sécurité si le timeout échoue.
        """
        # Accepte string ("M1", "H1") ou int (mt5.TIMEFRAME_M1)
        if isinstance(timeframe, int):
            tf = timeframe
        else:
            tf_map = {
                "M1": mt5.TIMEFRAME_M1,
                "M5": mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15,
                "H1": mt5.TIMEFRAME_H1,
                "H4": mt5.TIMEFRAME_H4,
                "D1": mt5.TIMEFRAME_D1,
            }
            tf = tf_map.get(timeframe, mt5.TIMEFRAME_H1)
        return self._call_with_timeout(
            lambda: mt5.copy_rates_from_pos(symbol, tf, 0, count),
            timeout=30,
            name=f"copy_rates_from_pos({symbol}, {timeframe})",
        )

    def get_rates_multi_tf(self, symbol: str, timeframes: list[str], count: int = 10000) -> dict[str, Any]:
        result = {}
        for tf_name in timeframes:
            r = self.get_rates(symbol, tf_name, count)
            if r is not None and len(r) >= count * 0.8:
                result[tf_name] = r
        return result

    def calc_profit(self, order_type: int, symbol: str, volume: float, price_open: float, price_close: float) -> Any:
        """Retourne le profit signé (None si MT5 déconnecté). Le caller gère abs() si besoin.

        🔧 FIX 6 Juillet 2026: Timeout 15s pour éviter freeze."""
        return self._call_with_timeout(
            lambda: mt5.order_calc_profit(order_type, symbol, volume, price_open, price_close),
            timeout=15,
            name=f"order_calc_profit({symbol})",
        )

    def order_send(self, request: dict[str, Any]) -> Any:
        """Envoie un ordre MT5 avec timeout 30s (🔧 FIX 6 Juillet 2026: évite freeze)."""
        return self._call_with_timeout(
            lambda: mt5.order_send(request),
            timeout=30,
            name="order_send",
        )

    def close_position(self, position: Any) -> Any:
        """Ferme une position avec timeout (🔧 FIX 6 Juillet 2026)."""
        tick = self._call_with_timeout(
            lambda: mt5.symbol_info_tick(position.symbol),
            timeout=10,
            name="close_position.symbol_info_tick",
        )
        if tick is None:
            logger.error(f"Cannot close {position.symbol}: tick is None (market closed?)")
            return None
        ct = 1 if position.type == 0 else 0
        price = tick.ask if ct == 0 else tick.bid
        req = dict(
            action=mt5.TRADE_ACTION_DEAL,
            symbol=position.symbol,
            volume=position.volume,
            type=ct,
            position=position.ticket,
            price=price,
            magic=self.magic,
            comment="CLOSE_SIMPLE",
        )
        return self._call_with_timeout(
            lambda: mt5.order_send(req),
            timeout=30,
            name="close_position.order_send",
        )

    def close_all_positions(self, magic: Optional[int] = None) -> Optional[dict[str, int]]:
        """Ferme toutes les positions du bot via MT5.

        🔧 FIX 6 Juillet 2026: Utilise get_positions() avec timeout au lieu de
        mt5.positions_get() direct (qui peut bloquer indéfiniment).

        Args:
            magic: Magic number filtré (None = toutes les positions)
        """
        try:
            if magic is None:
                # Besoin de toutes les positions, pas seulement les nôtres
                positions = self._call_with_timeout(
                    lambda: mt5.positions_get() or [],
                    timeout=15,
                    name="close_all.positions_get",
                    default=[],
                )
            else:
                positions = self.get_positions()
        except Exception as e:
            logger.error(f"close_all_positions: positions_get failed: {e}")
            return

        if not positions:
            logger.warning("close_all_positions: aucune position ou positions_get=None")
            return

        closed = 0
        errors = 0
        for pos in positions:
            if magic is not None and pos.magic != magic:
                continue
            result = self.close_position(pos)
            if result is None:
                logger.error(f"close_all_positions: echec fermeture #{pos.ticket} {pos.symbol} (tick indisponible)")
                errors += 1
            elif result.retcode != 10009:
                logger.error(f"close_all_positions: retcode={result.retcode} pour #{pos.ticket} {pos.symbol}")
                errors += 1
            else:
                logger.info(f"close_all_positions: #{pos.ticket} {pos.symbol} {pos.volume} fermee OK")
                closed += 1

        logger.info(f"close_all_positions: {closed} fermee(s), {errors} erreur(s)")
        return {"closed": closed, "errors": errors}

    def update_sl(self, position: Any, new_sl: float) -> Any:
        """Met à jour le SL avec timeout (🔧 FIX 6 Juillet 2026)."""
        req = dict(
            action=mt5.TRADE_ACTION_SLTP,
            position=position.ticket,
            symbol=position.symbol,
            sl=new_sl,
            tp=position.tp,
            magic=self.magic,
        )
        return self._call_with_timeout(
            lambda: mt5.order_send(req),
            timeout=30,
            name="update_sl",
        )

    def symbol_select(self, symbol: str, enable: bool = True) -> Any:
        """Sélectionne un symbole dans Market Watch avec timeout (🔧 FIX 6 Juillet 2026)."""
        return self._call_with_timeout(
            lambda: mt5.symbol_select(symbol, enable),
            timeout=15,
            name=f"symbol_select({symbol})",
        )

    def get_account_info(self) -> Any:
        return self._call_with_timeout(
            lambda: mt5.account_info(),
            timeout=15,
            name="account_info",
        )

    def ping(self) -> bool:
        """Keepalive : vérifie la connexion MT5, tente un appel léger.
        Retourne True si la connexion est active.

        🔧 FIX 6 Juillet 2026: Timeout 10s pour éviter freeze."""
        info = self._call_with_timeout(
            lambda: mt5.terminal_info(),
            timeout=10,
            name="ping.terminal_info",
        )
        if info is None:
            return False
        try:
            return bool(info.connected)
        except (RuntimeError, OSError, TypeError):
            return False

    def reconnect(self) -> bool:
        """Tente une reconnexion complète à MT5 avec retry rapide."""
        logger.info("[MT5] Tentative de reconnexion...")
        for attempt in range(3):
            try:
                mt5.shutdown()
            except Exception as e:
                logger.warning(f"  [MT5] reconnect shutdown: {e}")
                pass
            import time as _time

            _time.sleep(1)
            if self.connect():
                logger.info(f"[MT5] Reconnexion réussie (tentative #{attempt + 1})")
                return True
            logger.warning(f"[MT5] Tentative #{attempt + 1}/3 échouée")
        logger.error("[MT5] Échec de reconnexion après 3 tentatives")
        return False

    def get_history(self, from_time: int, to_time: int) -> Any:
        return self._call_with_timeout(
            lambda: mt5.history_deals_get(from_time, to_time),
            timeout=30,
            name=f"history_deals_get({from_time}, {to_time})",
        )
