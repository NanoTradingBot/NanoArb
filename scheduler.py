"""
Scheduler — calcula sesgo cada hora desde cTrader
Sin MetaAPI, sin costos
"""
import logging, threading, time
from bias_engine import get_all_biases

log      = logging.getLogger(__name__)
INTERVAL = 3600

def recalculate_all():
    log.info("🔄 Calculando sesgos desde cTrader...")
    try:
        results = get_all_biases()
        for sym, r in results.items():
            emoji = "🟢" if r["bias"] == "bullish" else "🔴" if r["bias"] == "bearish" else "⚪"
            log.info(f"{emoji} {sym}: {r['bias'].upper()} ({r['confidence']:.0%}) — {r['reason']}")
        return results
    except Exception as e:
        log.error(f"Error scheduler: {e}")
        return {}

def run_scheduler():
    log.info("⏰ Scheduler cTrader iniciado")
    recalculate_all()
    while True:
        time.sleep(INTERVAL)
        try:
            recalculate_all()
        except Exception as e:
            log.error(f"Error loop scheduler: {e}")

def start_scheduler():
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    log.info("⏰ Scheduler corriendo en background")
