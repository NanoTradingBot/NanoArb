"""
Motor de sesgo automático
HH/HL + EMA50 desde OANDA API (gratuita)
"""
import os, logging, requests
from datetime import datetime

log = logging.getLogger(__name__)

OANDA_API_KEY    = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_ENV        = os.getenv("OANDA_ENV", "practice")
BASE_URL         = "https://api-fxpractice.oanda.com" if OANDA_ENV == "practice" else "https://api-fxtrade.oanda.com"

EMA_PERIOD     = 50
SWING_LOOKBACK = 3
BIAS_CACHE_TTL = 3600
MIN_SWING_SIZE = {"XAU_USD": 0.5, "BTC_USD": 100.0}

_bias_cache = {}


def oanda_headers():
    return {"Authorization": f"Bearer {OANDA_API_KEY}"}


def calculate_ema(prices, period):
    if len(prices) < period:
        return []
    k   = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema


def find_swings(highs, lows, lookback=3):
    sh, sl = [], []
    for i in range(lookback, len(highs) - lookback):
        if all(highs[i] > highs[i-j] for j in range(1, lookback+1)) and \
           all(highs[i] > highs[i+j] for j in range(1, lookback+1)):
            sh.append({"index": i, "price": highs[i]})
        if all(lows[i] < lows[i-j] for j in range(1, lookback+1)) and \
           all(lows[i] < lows[i+j] for j in range(1, lookback+1)):
            sl.append({"index": i, "price": lows[i]})
    return {"highs": sh, "lows": sl}


def calculate_bias(candles, instrument):
    if len(candles) < EMA_PERIOD + 5:
        return {"bias": "ranging", "confidence": 0.0, "reason": "Insuficientes velas",
                "timestamp": datetime.utcnow(), "source": "error"}

    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    price  = closes[-1]

    ema_vals  = calculate_ema(closes, EMA_PERIOD)
    ema50     = ema_vals[-1] if ema_vals else price
    above_ema = price > ema50

    swings = find_swings(highs, lows, SWING_LOOKBACK)
    sh     = swings["highs"][-3:]
    sl     = swings["lows"][-3:]
    ms     = MIN_SWING_SIZE.get(instrument, 0.5)

    hh = sum(1 for i in range(1, len(sh)) if sh[i]["price"] - sh[i-1]["price"] > ms)
    hl = sum(1 for i in range(1, len(sl)) if sl[i]["price"] - sl[i-1]["price"] > ms)
    lh = sum(1 for i in range(1, len(sh)) if sh[i-1]["price"] - sh[i]["price"] > ms)
    ll = sum(1 for i in range(1, len(sl)) if sl[i-1]["price"] - sl[i]["price"] > ms)

    bull = hh + hl
    bear = lh + ll

    if bull >= 2 and bull > bear and above_ema:
        return {"bias": "bullish", "confidence": min(0.6 + bull * 0.15, 0.95),
                "ema50": round(ema50, 5), "price": round(price, 5),
                "reason": f"HH×{hh}+HL×{hl} sobre EMA50",
                "timestamp": datetime.utcnow(), "source": "oanda_calculated"}

    if bear >= 2 and bear > bull and not above_ema:
        return {"bias": "bearish", "confidence": min(0.6 + bear * 0.15, 0.95),
                "ema50": round(ema50, 5), "price": round(price, 5),
                "reason": f"LH×{lh}+LL×{ll} bajo EMA50",
                "timestamp": datetime.utcnow(), "source": "oanda_calculated"}

    return {"bias": "ranging", "confidence": 0.3,
            "ema50": round(ema50, 5), "price": round(price, 5),
            "reason": "Estructura mixta",
            "timestamp": datetime.utcnow(), "source": "oanda_calculated"}


def fetch_candles(instrument, count=100):
    try:
        r = requests.get(
            f"{BASE_URL}/v3/instruments/{instrument}/candles",
            headers=oanda_headers(),
            params={"count": count, "granularity": "H1", "price": "M"},
            timeout=15
        )
        r.raise_for_status()
        raw = r.json().get("candles", [])
        candles = [{
            "time":  c["time"],
            "open":  float(c["mid"]["o"]),
            "high":  float(c["mid"]["h"]),
            "low":   float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
        } for c in raw if c.get("complete")]
        log.info(f"📊 {len(candles)} velas OANDA para {instrument}")
        return candles
    except Exception as e:
        log.error(f"Error velas OANDA {instrument}: {e}")
        return []


def update_bias_from_tradingview(symbol, bias, confidence=0.9, reason=""):
    result = {"bias": bias, "confidence": confidence,
              "reason": reason or "TradingView",
              "timestamp": datetime.utcnow(), "source": "tradingview"}
    _bias_cache[symbol] = result
    return result


def get_bias(instrument, force_recalc=False):
    cached = _bias_cache.get(instrument)
    if cached and not force_recalc:
        age = (datetime.utcnow() - cached["timestamp"]).seconds
        if age < BIAS_CACHE_TTL:
            return cached

    candles = fetch_candles(instrument, 100)
    if candles:
        result = calculate_bias(candles, instrument)
        _bias_cache[instrument] = result
        log.info(f"📅 {instrument}: {result['bias']} ({result['confidence']:.0%}) — {result['reason']}")
        return result

    return {"bias": "ranging", "confidence": 0.0, "reason": "Sin datos",
            "timestamp": datetime.utcnow(), "source": "fallback"}


def get_all_biases():
    return {
        "XAU_USD": get_bias("XAU_USD"),
        "BTC_USD": get_bias("BTC_USD"),
    }


def get_all_cached_biases():
    return {sym: _bias_cache.get(sym) for sym in ["XAU_USD", "BTC_USD"]}
