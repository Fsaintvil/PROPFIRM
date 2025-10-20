def validate_signal(signal):
    return all([
        signal.get('price') is not None,
        signal.get('sl') is not None,
        signal.get('tp') is not None
    ])

def initialize_ai_systems_with_retry():
    for attempt in range(3):
        try:
            return initialize_ai_systems()
        except:
            if attempt == 2:
                return "fallback_mode"  # Mode dégradé