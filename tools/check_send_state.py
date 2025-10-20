import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _main():
    # import here to avoid modifying top-level import order
    from scripts import control
    from scripts import safety

    print("kill_switch active:", control.is_kill_switch_active())
    try:
        can_send, reason = safety.can_send_live(check_kill_switch=True)
        print("safety.can_send_live ->", can_send, reason)
    except Exception as e:
        print("safety.can_send_live raised:", e)
    try:
        print(
            "is_symbol_allowed EURUSD ->", safety.is_symbol_allowed("EURUSD")
        )
    except Exception as e:
        print("is_symbol_allowed raised:", e)


if __name__ == "__main__":
    _main()
