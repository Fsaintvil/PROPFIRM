import importlib.machinery
import importlib.util
from pathlib import Path
base = Path(__file__).resolve().parent
mod_path = base / 'MT5_FTMO_IA' / 'ai_trader_ftmo_7indicators.py'
loader = importlib.machinery.SourceFileLoader('ai_trader', str(mod_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
AITraderFTMOAutonome = mod.AITraderFTMOAutonome

trader = AITraderFTMOAutonome()

sym = trader.config['symbols'][0]
print('Testing symbol:', sym)
for i in range(12):
    sig = {'symbol': sym, 'signal': 'BUY', 'timestamp': 'now'}
    r = trader.execute_order(sig)
    print(i+1, '-> session count', trader.session_trades_count.get(sym))
print('Final session count', trader.session_trades_count.get(sym))
