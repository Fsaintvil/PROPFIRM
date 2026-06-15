"""Clean stale trailing_peaks from robot_state.json."""
import json
from pathlib import Path

state_file = Path("runtime/robot_state.json")
state = json.loads(state_file.read_text())

# Stale tickets (confirmed closed from logs)
stale_tickets = ["462884797", "462884996", "463178198", "463178376", "463347836"]
# Current open tickets (AUDUSD)
open_tickets = ["463501964", "463502464"]

print("=== AVANT NETTOYAGE ===")
for key in ("trailing_peaks", "position_regime", "peak_profit"):
    d = state.get(key, {})
    print(f"{key}: {len(d)} entries")
    for k, v in sorted(d.items()):
        if k in stale_tickets:
            status = "FERMÉ (à supprimer)"
        elif k in open_tickets:
            status = "OUVERT"
        else:
            status = "INCONNU"
        print(f"  {k}: {v} -> {status}")

print(f"\npartial_closed: {len(state.get('partial_closed', []))} entries")
for t in state.get("partial_closed", []):
    status = "FERMÉ" if t in stale_tickets else "?"
    print(f"  {t} -> {status}")

# Clean up stale entries across all dicts
for key in ("trailing_peaks", "position_regime", "peak_profit"):
    d = state.get(key, {})
    for t in stale_tickets:
        d.pop(t, None)
    state[key] = d

# Clean up partial_closed list
state["partial_closed"] = [
    t for t in state.get("partial_closed", [])
    if t not in stale_tickets
]

state_file.write_text(json.dumps(state, default=str))

print("\n=== APRÈS NETTOYAGE ===")
for key in ("trailing_peaks", "position_regime", "peak_profit"):
    print(f"{key}: {len(state.get(key, {}))} entries")
print(f"partial_closed: {len(state.get('partial_closed', []))} entries")
print("✅ Nettoyage terminé")
