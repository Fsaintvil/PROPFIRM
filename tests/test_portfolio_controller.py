"""Tests pour PortfolioController — corrélation, limites, allocation de risque."""

import pytest
from engine_simple.portfolio_controller import (
    PortfolioController,
    Position,
    PortfolioState,
    POSITION_GROUPS,
    MAX_TRADES_PER_GROUP,
    MAX_TRADES_PER_DIRECTION_IN_GROUP,
    can_open_position,
    get_risk_allocation,
)
from config_simple import MAX_POSITIONS as MAX_POSITIONS_TOTAL
from config_simple import MAX_POSITIONS_PER_SYMBOL

# ============================================================================
# Helpers
# ============================================================================

MAKE_POSITION = Position  # alias

# ============================================================================
# Position dataclass
# ============================================================================


class TestPosition:
    """Position dataclass — structure de base."""

    def test_default_attributes(self):
        p = MAKE_POSITION("EURUSD", "BUY", 0.1, 1.10500)
        assert p.symbol == "EURUSD"
        assert p.direction == "BUY"
        assert p.lot == 0.1
        assert p.entry_price == 1.10500
        assert p.current_price == 0.0
        assert p.pnl == 0.0
        assert p.open_time == ""
        assert p.regime == "RANGING"

    def test_sell_all_attributes(self):
        p = MAKE_POSITION(
            "BTCUSD",
            "SELL",
            0.05,
            62000.0,
            current_price=61500.0,
            pnl=25.0,
            open_time="2026-07-05",
            regime="TREND_DOWN",
        )
        assert p.symbol == "BTCUSD"
        assert p.direction == "SELL"
        assert p.lot == 0.05
        assert p.entry_price == 62000.0
        assert p.current_price == 61500.0
        assert p.pnl == 25.0
        assert p.open_time == "2026-07-05"
        assert p.regime == "TREND_DOWN"


# ============================================================================
# PortfolioState dataclass
# ============================================================================


class TestPortfolioState:
    """PortfolioState dataclass — snapshot du portefeuille."""

    def test_defaults(self):
        s = PortfolioState(total_positions=0, long_positions=0, short_positions=0, net_exposure=0.0)
        assert s.total_positions == 0
        assert s.long_positions == 0
        assert s.short_positions == 0
        assert s.net_exposure == 0.0
        assert s.symbols_exposure == {}

    def test_with_exposure(self):
        s = PortfolioState(
            total_positions=3,
            long_positions=2,
            short_positions=1,
            net_exposure=0.33,
            symbols_exposure={"EURUSD": 0.2, "BTCUSD": 0.1},
        )
        assert s.total_positions == 3
        assert s.symbols_exposure["EURUSD"] == 0.2


# ============================================================================
# POSITION_GROUPS — constante de corrélation
# ============================================================================


class TestPositionGroups:
    """POSITION_GROUPS — 6 groupes de corrélation."""

    def test_all_groups_present(self):
        assert set(POSITION_GROUPS.keys()) == {
            "FOREX_MAJORS",
            "FOREX_CROSSES",
            "CRYPTO",
            "INDICES",
            "EUROPE_INDICES",
            "COMMODITIES",
        }

    def test_forex_majors_symbols(self):
        assert "EURUSD" in POSITION_GROUPS["FOREX_MAJORS"]
        assert "GBPUSD" in POSITION_GROUPS["FOREX_MAJORS"]
        assert "USDJPY" in POSITION_GROUPS["FOREX_MAJORS"]

    def test_forex_crosses_symbols(self):
        assert "EURJPY" in POSITION_GROUPS["FOREX_CROSSES"]
        assert "GBPJPY" in POSITION_GROUPS["FOREX_CROSSES"]

    def test_crypto_symbols(self):
        assert "BTCUSD" in POSITION_GROUPS["CRYPTO"]
        assert "ETHUSD" in POSITION_GROUPS["CRYPTO"]

    def test_indices_symbols(self):
        assert "US500.cash" in POSITION_GROUPS["INDICES"]
        assert "US30.cash" in POSITION_GROUPS["INDICES"]

    def test_europe_indices(self):
        assert "GER40.cash" in POSITION_GROUPS["EUROPE_INDICES"]
        assert "UK100.cash" in POSITION_GROUPS["EUROPE_INDICES"]

    def test_commodities_symbols(self):
        assert "XAUUSD" in POSITION_GROUPS["COMMODITIES"]
        assert "USOIL.cash" in POSITION_GROUPS["COMMODITIES"]


# ============================================================================
# PortfolioController — tests unitaires
# ============================================================================


class TestGetGroupForSymbol:
    """_get_group_for_symbol — mapping symbole → groupe."""

    def test_forex_major(self):
        assert PortfolioController._get_group_for_symbol("EURUSD") == "FOREX_MAJORS"

    def test_forex_cross(self):
        assert PortfolioController._get_group_for_symbol("GBPJPY") == "FOREX_CROSSES"

    def test_crypto(self):
        assert PortfolioController._get_group_for_symbol("BTCUSD") == "CRYPTO"

    def test_index(self):
        assert PortfolioController._get_group_for_symbol("US500.cash") == "INDICES"

    def test_europe_index(self):
        assert PortfolioController._get_group_for_symbol("GER40.cash") == "EUROPE_INDICES"

    def test_commodity(self):
        assert PortfolioController._get_group_for_symbol("XAUUSD") == "COMMODITIES"

    def test_unknown_symbol(self):
        assert PortfolioController._get_group_for_symbol("UNKNOWN") is None


class TestGetDir:
    """_get_dir — extraction de direction depuis Position ou MT5 TradePosition."""

    def test_position_buy(self):
        p = MAKE_POSITION("EURUSD", "BUY", 0.1, 1.10)
        assert PortfolioController._get_dir(p) == "BUY"

    def test_position_sell(self):
        p = MAKE_POSITION("BTCUSD", "SELL", 0.05, 60000)
        assert PortfolioController._get_dir(p) == "SELL"

    def test_mt5_like_buy(self):
        class FakeMT5Position:
            type = 0  # BUY

        assert PortfolioController._get_dir(FakeMT5Position()) == "BUY"

    def test_mt5_like_sell(self):
        class FakeMT5Position:
            type = 1  # SELL

        assert PortfolioController._get_dir(FakeMT5Position()) == "SELL"


class TestSetPositions:
    """set_positions — mise à jour des positions internes."""

    def test_stores_positions(self):
        pc = PortfolioController()
        positions = [MAKE_POSITION("EURUSD", "BUY", 0.1, 1.10)]
        pc.set_positions(positions)
        state = pc.get_portfolio_state()
        assert state.total_positions == 1

    def test_empty_positions(self):
        pc = PortfolioController()
        pc.set_positions([])
        state = pc.get_portfolio_state()
        assert state.total_positions == 0


class TestCanOpenNormal:
    """can_open_position — mode normal (high_confidence=False)."""

    def test_ok_when_no_positions(self):
        pc = PortfolioController()
        can, reason = pc.can_open_position("EURUSD", "BUY", [])
        assert can is True
        assert reason == "OK"

    def test_max_total_positions(self):
        pc = PortfolioController()
        many_positions = [MAKE_POSITION(f"SYM{i}", "BUY", 0.1, 1.0) for i in range(MAX_POSITIONS_TOTAL)]
        can, reason = pc.can_open_position("EURUSD", "BUY", many_positions)
        assert can is False
        assert "Max positions total" in reason

    def test_max_per_symbol(self):
        pc = PortfolioController()
        sym_positions = [MAKE_POSITION("EURUSD", "BUY", 0.1, 1.10 + i * 0.001) for i in range(MAX_POSITIONS_PER_SYMBOL)]
        can, reason = pc.can_open_position("EURUSD", "BUY", sym_positions)
        assert can is False
        assert "Max positions EURUSD" in reason

    def test_max_per_direction(self):
        pc = PortfolioController()
        max_dir = MAX_POSITIONS_TOTAL // 2
        dir_positions = [MAKE_POSITION(f"SYM{i}", "BUY", 0.1, 1.0) for i in range(max_dir)]
        can, reason = pc.can_open_position("EURUSD", "BUY", dir_positions)
        assert can is False
        assert "Max positions BUY" in reason

    def test_correlation_group_total_limit(self):
        pc = PortfolioController()
        # Remplir le groupe FOREX_MAJORS avec MAX_TRADES_PER_GROUP positions
        majors = ["EURUSD", "GBPUSD", "USDCHF", "USDCAD"]
        group_positions = [MAKE_POSITION(sym, "BUY", 0.1, 1.10) for sym in majors[:MAX_TRADES_PER_GROUP]]
        # Essayer d'ouvrir une 5e dans le même groupe
        can, reason = pc.can_open_position("AUDUSD", "BUY", group_positions)
        assert can is False
        assert "Groupe FOREX_MAJORS" in reason

    def test_correlation_group_direction_limit(self):
        pc = PortfolioController()
        # 3 positions BUY dans FOREX_MAJORS
        majors = ["EURUSD", "GBPUSD", "USDCHF"]
        group_positions = [MAKE_POSITION(sym, "BUY", 0.1, 1.10) for sym in majors[:MAX_TRADES_PER_DIRECTION_IN_GROUP]]
        # Essayer une 4e BUY dans le même groupe
        can, reason = pc.can_open_position("AUDUSD", "BUY", group_positions)
        assert can is False
        assert "Groupe FOREX_MAJORS" in reason
        assert "BUY" in reason

    def test_allow_opposite_direction_in_group(self):
        pc = PortfolioController()
        # 3 positions BUY dans FOREX_MAJORS — SELL est OK
        majors = ["EURUSD", "GBPUSD", "USDCHF"]
        group_positions = [MAKE_POSITION(sym, "BUY", 0.1, 1.10) for sym in majors]
        can, reason = pc.can_open_position("AUDUSD", "SELL", group_positions)
        assert can is True

    def test_dd_per_symbol_too_high(self):
        pc = PortfolioController()
        pc.update_symbol_dd("EURUSD", 0.09)  # 9% > 8%
        can, reason = pc.can_open_position("EURUSD", "BUY", [])
        assert can is False
        assert "DD EURUSD" in reason

    def test_dd_boundary_ok(self):
        pc = PortfolioController()
        pc.update_symbol_dd("EURUSD", 0.08)  # exactly 8% — not > 8%
        can, reason = pc.can_open_position("EURUSD", "BUY", [])
        assert can is True

    def test_daily_loss_exceeded(self):
        pc = PortfolioController()
        pc.update_daily_pnl(-5000.0)  # -$5,000 > -$4,000 (2% of $200k)
        can, reason = pc.can_open_position("EURUSD", "BUY", [])
        assert can is False
        assert "Daily loss" in reason

    def test_daily_loss_boundary_ok(self):
        pc = PortfolioController()
        pc.update_daily_pnl(-4000.0)  # exactly -$4,000 — not < -$4,000
        can, reason = pc.can_open_position("EURUSD", "BUY", [])
        assert can is True

    def test_mixed_positions_ok(self):
        """Position dans un groupe différent ne bloque pas."""
        pc = PortfolioController()
        positions = [
            MAKE_POSITION("BTCUSD", "BUY", 0.05, 60000),  # CRYPTO
            MAKE_POSITION("US500.cash", "SELL", 0.1, 4500),  # INDICES
        ]
        can, reason = pc.can_open_position("EURUSD", "BUY", positions)  # FOREX_MAJORS
        assert can is True

    def test_ok_within_limits(self):
        """Plein de positions mais encore de la place."""
        pc = PortfolioController()
        positions = [MAKE_POSITION(f"SYM{i}", "BUY" if i % 2 == 0 else "SELL", 0.1, 1.0) for i in range(6)]
        can, reason = pc.can_open_position("EURUSD", "BUY", positions)
        assert can is True


class TestCanOpenHighConfidence:
    """can_open_position — mode high_confidence (high_confidence=True)."""

    def test_bypass_per_symbol_limit(self):
        pc = PortfolioController()
        # 🔧 FIX 6 Juillet 2026: high_confidence permet max 2 positions par direction par symbole
        # 1 seule position BUY → autorisé (dans la limite de 2)
        sym_positions = [MAKE_POSITION("ZZZ_USD", "BUY", 0.1, 1.10)]
        can, reason = pc.can_open_position("ZZZ_USD", "BUY", sym_positions, high_confidence=True)
        assert can is True
        assert "HIGH CONFIDENCE" in reason

    def test_still_block_at_direction_limit_hc(self):
        pc = PortfolioController()
        # 2 positions BUY ZZZ_USD → la 3e est bloquée (max 2 par direction en high confidence)
        sym_positions = [MAKE_POSITION("ZZZ_USD", "BUY", 0.1, 1.10 + i * 0.001) for i in range(2)]
        can, reason = pc.can_open_position("ZZZ_USD", "BUY", sym_positions, high_confidence=True)
        assert can is False
        assert "ZZZ_USD" in reason
        assert "déjà 2" in reason

    def test_correlation_still_active(self):
        pc = PortfolioController()
        # Groupe FOREX_MAJORS rempli — même en high confidence, corrélation protégée
        majors = ["EURUSD", "GBPUSD", "USDCHF", "USDCAD"]
        group_positions = [MAKE_POSITION(sym, "BUY", 0.1, 1.10) for sym in majors[:MAX_TRADES_PER_GROUP]]
        can, reason = pc.can_open_position("AUDUSD", "BUY", group_positions, high_confidence=True)
        assert can is False
        assert "Groupe FOREX_MAJORS" in reason

    def test_dd_still_checked(self):
        pc = PortfolioController()
        pc.update_symbol_dd("EURUSD", 0.09)
        can, reason = pc.can_open_position("EURUSD", "BUY", [], high_confidence=True)
        assert can is False
        assert "DD EURUSD" in reason

    def test_daily_loss_still_checked(self):
        pc = PortfolioController()
        pc.update_daily_pnl(-5000.0)
        can, reason = pc.can_open_position("EURUSD", "BUY", [], high_confidence=True)
        assert can is False
        assert "Daily loss" in reason

    def test_bypass_per_direction_limit(self):
        pc = PortfolioController()
        max_dir = MAX_POSITIONS_TOTAL // 2
        dir_positions = [MAKE_POSITION(f"SYM{i}", "BUY", 0.1, 1.0) for i in range(max_dir)]
        # En high conf, la limite par direction est doublée (16), donc 9 BUY passent
        can, reason = pc.can_open_position("EURUSD", "BUY", dir_positions, high_confidence=True)
        assert can is True
        assert "HIGH CONFIDENCE" in reason


class TestRiskAllocation:
    """get_risk_allocation — ajustement du risque par DD et symbole."""

    def test_no_dd_full_allocation(self):
        pc = PortfolioController()
        alloc = pc.get_risk_allocation("EURUSD", 0.0)
        assert alloc == 0.7  # 1.0 * 0.7 (poids défaut)

    def test_dd_3_to_5_percent(self):
        pc = PortfolioController()
        alloc = pc.get_risk_allocation("EURUSD", 0.04)
        assert alloc == pytest.approx(0.75 * 0.7, rel=0.01)

    def test_dd_5_to_7_percent(self):
        pc = PortfolioController()
        alloc = pc.get_risk_allocation("EURUSD", 0.06)
        assert alloc == pytest.approx(0.5 * 0.7, rel=0.01)

    def test_dd_above_7_percent(self):
        pc = PortfolioController()
        alloc = pc.get_risk_allocation("EURUSD", 0.08)
        assert alloc == pytest.approx(0.2 * 0.7, rel=0.01)

    def test_symbol_weight_xauusd(self):
        pc = PortfolioController()
        alloc = pc.get_risk_allocation("XAUUSD", 0.0)
        assert alloc == 1.0  # XAUUSD a poids 1.0

    def test_symbol_weight_btcusd(self):
        pc = PortfolioController()
        alloc = pc.get_risk_allocation("BTCUSD", 0.0)
        assert alloc == 0.8

    def test_symbol_weight_default_low(self):
        pc = PortfolioController()
        alloc = pc.get_risk_allocation("EURJPY", 0.0)
        assert alloc == 0.7  # poids défaut


class TestGetPortfolioState:
    """get_portfolio_state — snapshot du portefeuille."""

    def test_empty(self):
        pc = PortfolioController()
        state = pc.get_portfolio_state([])
        assert state.total_positions == 0
        assert state.long_positions == 0
        assert state.short_positions == 0
        assert state.net_exposure == 0.0
        assert state.symbols_exposure == {}

    def test_counts(self):
        pc = PortfolioController()
        positions = [
            MAKE_POSITION("EURUSD", "BUY", 0.1, 1.10),
            MAKE_POSITION("GBPUSD", "BUY", 0.1, 1.25),
            MAKE_POSITION("BTCUSD", "SELL", 0.05, 60000),
        ]
        state = pc.get_portfolio_state(positions)
        assert state.total_positions == 3
        assert state.long_positions == 2
        assert state.short_positions == 1

    def test_net_exposure_neutral(self):
        pc = PortfolioController()
        positions = [
            MAKE_POSITION("EURUSD", "BUY", 0.1, 1.10),
            MAKE_POSITION("GBPUSD", "SELL", 0.1, 1.25),
        ]
        state = pc.get_portfolio_state(positions)
        assert state.net_exposure == 0.0

    def test_net_exposure_long_biased(self):
        pc = PortfolioController()
        positions = [
            MAKE_POSITION("EURUSD", "BUY", 0.2, 1.10),
            MAKE_POSITION("GBPUSD", "SELL", 0.1, 1.25),
        ]
        state = pc.get_portfolio_state(positions)
        # (0.2 - 0.1) / (0.2 + 0.1) = 0.1 / 0.3 = 0.333
        assert state.net_exposure == pytest.approx(0.333, rel=0.01)

    def test_symbol_exposure_aggregated(self):
        pc = PortfolioController()
        positions = [
            MAKE_POSITION("EURUSD", "BUY", 0.1, 1.10),
            MAKE_POSITION("EURUSD", "BUY", 0.2, 1.11),
            MAKE_POSITION("BTCUSD", "SELL", 0.05, 60000),
        ]
        state = pc.get_portfolio_state(positions)
        assert state.symbols_exposure["EURUSD"] == pytest.approx(0.3, rel=1e-9)
        assert state.symbols_exposure["BTCUSD"] == 0.05


class TestUpdateMethods:
    """update_symbol_dd, update_daily_pnl, reset_daily."""

    def test_update_symbol_dd(self):
        pc = PortfolioController()
        pc.update_symbol_dd("EURUSD", 0.05)
        assert pc._symbol_dd["EURUSD"] == 0.05

    def test_update_daily_pnl(self):
        pc = PortfolioController()
        pc.update_daily_pnl(1500.0)
        assert pc._daily_pnl == 1500.0

    def test_reset_daily(self):
        pc = PortfolioController()
        pc.update_daily_pnl(5000.0)
        pc.reset_daily()
        assert pc._daily_pnl == 0.0


class TestConvenienceFunctions:
    """Fonctions convenience module-level."""

    def test_can_open_position(self):
        can, reason = can_open_position("EURUSD", "BUY", [])
        assert can is True

    def test_can_open_position_blocked(self):
        many = [MAKE_POSITION(f"SYM{i}", "BUY", 0.1, 1.0) for i in range(MAX_POSITIONS_TOTAL)]
        can, reason = can_open_position("EURUSD", "BUY", many)
        assert can is False

    def test_get_risk_allocation(self):
        alloc = get_risk_allocation("EURUSD", 0.0)
        assert alloc == 0.7
