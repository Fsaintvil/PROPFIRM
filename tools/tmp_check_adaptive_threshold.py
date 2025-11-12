from scripts.advanced_decision_engine import AdvancedDecisionEngine, MarketContext
eng = AdvancedDecisionEngine()
print('adaptive_threshold_range=', eng.config['adaptive_threshold_range'])
ctx = MarketContext(volatility_regime='normal', trend_strength=0.5, momentum_quality=0.5, support_resistance_distance=0.5, session_characteristics={}, news_impact_score=0.0)
print('computed adaptive_threshold=', eng._get_adaptive_threshold('EURUSD', ctx))
