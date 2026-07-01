#!/bin/bash
# Watchdog Production — Surveillance continue drastique
# Tourne en boucle, vérifie le robot toutes les 2 minutes
# Détecte : plantage, dérive mémoire, DD critique, daily loss

RUNTIME_DIR="C:/Users/saint/Documents/MT5_FTMO_IA.7/runtime"
LOG_DIR="C:/Users/saint/Documents/MT5_FTMO_IA.7/logs"
ROBOT_LOG="$LOG_DIR/simple_robot.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  PRODUCTION WATCHDOG — Surveillance Continue${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo "Started at $(date)"
echo "PID: $$"
echo ""

CYCLE=0
ALERTS=0

while true; do
    CYCLE=$((CYCLE + 1))
    NOW=$(date '+%H:%M:%S')
    echo -e "\n${YELLOW}[Cycle $CYCLE — $NOW]${NC}"

    # 1. Vérifier PID
    if [ -f "$RUNTIME_DIR/robot.pid" ]; then
        ROBOT_PID=$(cat "$RUNTIME_DIR/robot.pid")
        if tasklist //FI "PID eq $ROBOT_PID" 2>/dev/null | grep -q python; then
            echo -e "  PID: ${GREEN}$ROBOT_PID (running)${NC}"
        else
            echo -e "  PID: ${RED}$ROBOT_PID (NOT FOUND!)${NC}"
            ALERTS=$((ALERTS + 1))
        fi
    else
        echo -e "  PID: ${RED}NO PID FILE — ROBOT PLANTER?${NC}"
        ALERTS=$((ALERTS + 1))
    fi

    # 2. Vérifier les métriques FTMO
    if [ -f "$RUNTIME_DIR/ftmo_report.json" ]; then
        BALANCE=$(python -c "import json; d=json.load(open('$RUNTIME_DIR/ftmo_report.json')); print(d.get('balance',0))" 2>/dev/null)
        DD=$(python -c "import json; d=json.load(open('$RUNTIME_DIR/ftmo_report.json')); print(d.get('dd_from_peak',0))" 2>/dev/null)
        DAILY=$(python -c "import json; d=json.load(open('$RUNTIME_DIR/ftmo_report.json')); print(abs(d.get('daily_equity_pnl',0)))" 2>/dev/null)
        WR=$(python -c "import json; d=json.load(open('$RUNTIME_DIR/ftmo_report.json')); print(d.get('win_rate',0))" 2>/dev/null)
        LOSSES=$(python -c "import json; d=json.load(open('$RUNTIME_DIR/ftmo_report.json')); print(d.get('consecutive_losses',0))" 2>/dev/null)
        TRADES=$(python -c "import json; d=json.load(open('$RUNTIME_DIR/ftmo_report.json')); print(d.get('total_trades',0))" 2>/dev/null)

        echo -e "  Balance: \$$BALANCE"
        echo -e "  DD: ${DD}%"
        echo -e "  Trades: $TRADES | WR: ${WR}%"

        # Alertes critiques
        if (( $(echo "$DD > 6.0" | bc -l 2>/dev/null) )); then
            echo -e "  ${RED}⚠️ ALERTE DD > 6%!${NC}"
            ALERTS=$((ALERTS + 1))
        fi
        if (( $(echo "$DAILY > 4000" | bc -l 2>/dev/null) )); then
            echo -e "  ${RED}⚠️ ALERTE Daily Loss > 2%!${NC}"
            ALERTS=$((ALERTS + 1))
        fi
        if [ "$LOSSES" -ge 5 ]; then
            echo -e "  ${RED}⚠️ ALERTE $LOSSES pertes consécutives!${NC}"
            ALERTS=$((ALERTS + 1))
        fi
    else
        echo -e "  FTMO Report: ${RED}N/A${NC}"
    fi

    # 3. Vérifier les erreurs récentes dans les logs
    if [ -f "$ROBOT_LOG" ]; then
        RECENT_ERRORS=$(tail -50 "$ROBOT_LOG" | grep -c "ERROR\|CRITICAL" 2>/dev/null)
        if [ "$RECENT_ERRORS" -gt 0 ]; then
            echo -e "  ${YELLOW}⚠️ $RECENT_ERRORS erreurs dans les 50 dernières lignes${NC}"
            tail -10 "$ROBOT_LOG" | grep "ERROR\|CRITICAL" | tail -3 | sed 's/^/    /'
        else
            echo -e "  Erreurs récentes: 0 ✅"
        fi
    fi

    # 4. Alerter si trop d'alertes cumulées
    if [ "$ALERTS" -ge 10 ]; then
        echo -e "\n${RED}═══════════════════════════════════════════════${NC}"
        echo -e "${RED}  ⚠️ $ALERTS ALERTES DÉTECTÉES — INTERVENTION NÉCESSAIRE${NC}"
        echo -e "${RED}═══════════════════════════════════════════════${NC}"
    fi

    sleep 120  # Vérification toutes les 2 minutes
done
