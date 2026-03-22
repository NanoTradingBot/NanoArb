"""
Motor de sesgo - cTrader API directa
"""
import os, logging, requests, time
from datetime import datetime

log            = logging.getLogger(__name__)
BIAS_CACHE_TTL = 3600
MIN_SWING_SIZE = {"XAUUSD": 0.5, "BTCUSD": 100.0}
_bias_cache    = {}

CTRADER_ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN", "")
CTRADER_ACCOUNT_ID   = os.getenv("CTRADER_ACCOUNT_ID", "")
BASE_URL             = "https://api.spotware.com"

def ctrader_headers():
    return {"Authorization": f"Bearer {CTRADER_ACCESS_TOKEN}"}

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

def calculate_bias(candles, symbol):
    if len(candles) < 55:
        return {"bias": "ranging", "confidence": 0.0,
                "reason": "Insuficientes velas",
                "timestamp": datetime.utcnow(), "source": "error"}
    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    price  = closes[-1]
    ema_vals  = calculate_ema(closes, 50)
    ema50     = ema_vals[-1] if ema_vals else price
    above_ema = price > ema50
    swings = find_swings(highs, lows, 3)
    sh     = swings["highs"][-3:]
    sl     = swings["lows"][-3:]
    ms     = MIN_SWING_SIZE.get(symbol, 0.5)
    hh = sum(1 for i in range(1, len(sh)) if sh[i]["price"] - sh[i-1]["price"] > ms)
    hl = sum(1 for i in range(1, len(sl)) if sl[i]["price"] - sl[i-1]["price"] > ms)
    lh = sum(1 for i in range(1, len(sh)) if sh[i-1]["price"] - sh[i]["price"] > ms)
    ll = sum(1 for i in range(1, len(sl)) if sl[i-1]["price"] - sl[i]["price"] > ms)
    bull = hh + hl
    bear = lh + ll
    if bull >= 2 and bull > bear and above_ema:
        return {"bias": "bullish", "confidence": min(0.6 + bull * 0.15, 0.95),
                "ema50": round(ema50, 5), "price": round(price, 5),
                "reason": f"HH×{hh}+HL×{hl} sobre EMA50({ema50:.2f})",
                "timestamp": datetime.utcnow(), "source": "ctrader"}
    if bear >= 2 and bear > bull and not above_ema:
        return {"bias": "bearish", "confidence": min(0.6 + bear * 0.15, 0.95),
                "ema50": round(ema50, 5), "price": round(price, 5),
                "reason": f"LH×{lh}+LL×{ll} bajo EMA50({ema50:.2f})",
                "timestamp": datetime.utcnow(), "source": "ctrader"}
    return {"bias": "ranging", "confidence": 0.3,
            "ema50": round(ema50, 5), "price": round(price, 5),
            "reason": "Estructura mixta",
            "timestamp": datetime.utcnow(), "source": "ctrader"}

def fetch_candles(symbol, count=100):
    try:
        # Obtener symbolId
        r = requests.get(
            f"{BASE_URL}/v2/tradingaccounts/{CTRADER_ACCOUNT_ID}/symbols",
            headers=ctrader_headers(), timeout=10
        )
        r.raise_for_status()
        symbol_id = None
        for s in r.json().get("symbol", []):
            if s.get("symbolName") == symbol:
                symbol_id = s.get("symbolId")
                break
        if not symbol_id:
            log.error(f"Símbolo no encontrado: {symbol}")
            return []

        now   = int(time.time() * 1000)
        start = now - (count * 3600 * 1000)
        r = requests.get(
            f"{BASE_URL}/v2/tradingaccounts/{CTRADER_ACCOUNT_ID}/symbols/{symbol_id}/trendbars",
            headers=ctrader_headers(),
            params={"period": "H1", "fromTimestamp": start, "toTimestamp": now, "count": count},
            timeout=15
        )
        r.raise_for_status()
        raw = r.json().get("trendbar", [])
        candles = []
        for c in raw:
            low   = c.get("low", 0) / 100000
            open_ = low + c.get("deltaOpen",  0) / 100000
            high  = low + c.get("deltaHigh",  0) / 100000
            close = low + c.get("deltaClose", 0) / 100000
            candles.append({"time": c.get("utcTimestampInMinutes"),
                            "open": open_, "high": high, "low": low, "close": close})
        log.info(f"📊 {len(candles)} velas cTrader para {symbol}")
        return candles
    except Exception as e:
        log.error(f"Error velas cTrader {symbol}: {e}")
        return []

def update_bias_from_tradingview(symbol, bias, confidence=0.9, reason=""):
    result = {"bias": bias, "confidence": confidence,
              "reason": reason or "TradingView",
              "timestamp": datetime.utcnow(), "source": "tradingview"}
    _bias_cache[symbol] = result
    return result

def get_bias(symbol, force_recalc=False):
    cached = _bias_cache.get(symbol)
    if cached and not force_recalc:
        age = (datetime.utcnow() - cached["timestamp"]).seconds
        if age < BIAS_CACHE_TTL:
            return cached
    candles = fetch_candles(symbol, 100)
    if candles:
        result = calculate_bias(candles, symbol)
        _bias_cache[symbol] = result
        log.info(f"📅 {symbol}: {result['bias']} ({result['confidence']:.0%})")
        return result
    return {"bias": "ranging", "confidence": 0.0, "reason": "Sin datos",
            "timestamp": datetime.utcnow(), "source": "fallback"}

def get_all_biases():
    return {"XAUUSD": get_bias("XAUUSD"), "BTCUSD": get_bias("BTCUSD")}

def get_all_cached_biases():
    return {sym: _bias_cache.get(sym) for sym in ["XAUUSD", "BTCUSD"]}
