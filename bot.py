"""
TRADING BOT — XAU/USD & BTC/USD
Pepperstone cTrader Open API (gratuita)
"""
import os, logging, requests, time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────────────────────────
CTRADER_CLIENT_ID     = os.getenv("CTRADER_CLIENT_ID", "")
CTRADER_CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET", "")
CTRADER_ACCOUNT_ID    = os.getenv("CTRADER_ACCOUNT_ID", "")
CTRADER_ACCESS_TOKEN  = os.getenv("CTRADER_ACCESS_TOKEN", "")
CTRADER_ENV           = os.getenv("CTRADER_ENV", "demo")  # demo o live

RISK_PER_TRADE     = float(os.getenv("RISK_PER_TRADE",    "1.0"))
MAX_TRADES_DAY     = int(os.getenv("MAX_TRADES_DAY",      "10"))
MAX_LOSS_DAY_PCT   = float(os.getenv("MAX_LOSS_DAY_PCT",  "2.0"))
MIN_RR             = float(os.getenv("MIN_RR",            "1.5"))
MAX_CONSECUTIVE_SL = int(os.getenv("MAX_CONSECUTIVE_SL",  "2"))
BREAKEVEN_PCT      = float(os.getenv("BREAKEVEN_PCT",     "50"))

# cTrader API URLs
BASE_URL = "https://api.spotware.com"

KILL_ZONES = [(7, 9), (12, 14)]

# Símbolos cTrader
ALLOWED_SYMBOLS = {
    "XAUUSD":  "XAUUSD",
    "BTCUSD":  "BTCUSD",
    "XAU/USD": "XAUUSD",
    "BTC/USD": "BTCUSD",
    "XAUUSD.": "XAUUSD",
    "BTCUSD.": "BTCUSD",
}

SYMBOL_CONFIG = {
    "XAUUSD": {"pip_size": 0.01, "default_tp_pips": 30,  "default_sl_pips": 18,  "min_volume": 1000,  "volume_step": 1000},
    "BTCUSD": {"pip_size": 1.0,  "default_tp_pips": 200, "default_sl_pips": 120, "min_volume": 10000, "volume_step": 10000},
}

# ─── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

if not CTRADER_ACCESS_TOKEN:
    log.error("❌ CTRADER_ACCESS_TOKEN no configurado")
if not CTRADER_ACCOUNT_ID:
    log.error("❌ CTRADER_ACCOUNT_ID no configurado")
else:
    log.info(f"✅ cTrader conectado — cuenta: {CTRADER_ACCOUNT_ID} ({CTRADER_ENV})")

# ─── TOKEN REFRESH ────────────────────────────────────────────────────────────
_token_cache = {"token": CTRADER_ACCESS_TOKEN, "expires": 0}

def get_access_token():
    """Retorna token válido, refresca si expiró."""
    if _token_cache["token"] and _token_cache["expires"] > time.time():
        return _token_cache["token"]

    if not CTRADER_CLIENT_ID or not CTRADER_CLIENT_SECRET:
        return CTRADER_ACCESS_TOKEN

    try:
        r = requests.post(
            "https://id.ctrader.com/oauth/v2/token",
            data={
                "grant_type":    "client_credentials",
                "client_id":     CTRADER_CLIENT_ID,
                "client_secret": CTRADER_CLIENT_SECRET,
            },
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        _token_cache["token"]   = data["access_token"]
        _token_cache["expires"] = time.time() + data.get("expires_in", 3600) - 60
        log.info("✅ Token cTrader refrescado")
        return _token_cache["token"]
    except Exception as e:
        log.warning(f"Error refrescando token: {e}")
        return CTRADER_ACCESS_TOKEN

def ctrader_headers():
    return {"Authorization": f"Bearer {get_access_token()}"}

# ─── ESTADO ───────────────────────────────────────────────────────────────────
class BotState:
    def __init__(self):
        self.trades_today    = 0
        self.wins_today      = 0
        self.losses_today    = 0
        self.pnl_today       = 0.0
        self.balance_start   = 0.0
        self.last_reset      = datetime.utcnow().date()
        self.open_positions  = {}
        self.daily_bias      = {}
        self.rejected_today  = 0
        self.rejection_log   = []
        self.consecutive_sl  = 0
        self.paused          = False
        self.last_results    = []
        self.pending_signals = {}

    def win_rate(self):
        total = self.wins_today + self.losses_today
        return (self.wins_today / total * 100) if total > 0 else 0.0

    def register_result(self, result: str):
        self.last_results.append(result)
        if len(self.last_results) > 20:
            self.last_results.pop(0)
        if result == "sl":
            self.losses_today   += 1
            self.consecutive_sl += 1
            if self.consecutive_sl >= MAX_CONSECUTIVE_SL:
                self.paused = True
                log.warning(f"🛑 BOT PAUSADO — {MAX_CONSECUTIVE_SL} SL consecutivos")
        else:
            self.wins_today    += 1
            self.consecutive_sl = 0

    def reset_if_new_day(self):
        today = datetime.utcnow().date()
        if self.last_reset != today:
            self.trades_today    = 0
            self.wins_today      = 0
            self.losses_today    = 0
            self.pnl_today       = 0.0
            self.balance_start   = 0.0
            self.rejected_today  = 0
            self.rejection_log   = []
            self.consecutive_sl  = 0
            self.paused          = False
            self.last_results    = []
            self.daily_bias      = {}
            self.pending_signals = {}
            self.last_reset      = today
            log.info("🔄 Nuevo día — contadores reseteados")

state = BotState()

def send_telegram(msg: str):
    pass  # Desactivado

# ─── CTRADER API ──────────────────────────────────────────────────────────────

def get_account_info():
    r = requests.get(
        f"{BASE_URL}/v2/tradingaccounts/{CTRADER_ACCOUNT_ID}",
        headers=ctrader_headers(),
        timeout=10
    )
    r.raise_for_status()
    data = r.json()
    return {
        "balance":    data.get("balance", 0) / 100,
        "equity":     data.get("equity",  0) / 100,
        "currency":   data.get("depositCurrency", "USD"),
    }

def get_symbol_id(symbol: str):
    """Obtiene el symbolId de cTrader para el símbolo."""
    r = requests.get(
        f"{BASE_URL}/v2/tradingaccounts/{CTRADER_ACCOUNT_ID}/symbols",
        headers=ctrader_headers(),
        timeout=10
    )
    r.raise_for_status()
    symbols = r.json().get("symbol", [])
    for s in symbols:
        if s.get("symbolName") == symbol:
            return s.get("symbolId")
    return None

def get_price(symbol: str):
    """Retorna bid/ask del símbolo."""
    symbol_id = get_symbol_id(symbol)
    if not symbol_id:
        raise ValueError(f"Símbolo no encontrado: {symbol}")
    r = requests.get(
        f"{BASE_URL}/v2/tradingaccounts/{CTRADER_ACCOUNT_ID}/symbols/{symbol_id}/price",
        headers=ctrader_headers(),
        timeout=10
    )
    r.raise_for_status()
    data = r.json()
    bid  = data.get("bid", 0) / 100000
    ask  = data.get("ask", 0) / 100000
    return {"bid": bid, "ask": ask}

def get_candles(symbol: str, count: int = 100):
    """Retorna velas H1 del símbolo."""
    symbol_id = get_symbol_id(symbol)
    if not symbol_id:
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
        candles.append({"time": c.get("utcTimestampInMinutes"), "open": open_, "high": high, "low": low, "close": close})
    log.info(f"📊 {len(candles)} velas cTrader para {symbol}")
    return candles

def calculate_volume(symbol: str, sl_pips: float, balance: float):
    cfg      = SYMBOL_CONFIG.get(symbol, SYMBOL_CONFIG["XAUUSD"])
    risk_usd = balance * (RISK_PER_TRADE / 100)
    pip      = cfg["pip_size"]
    step     = cfg["volume_step"]
    volume   = int((risk_usd / (sl_pips * pip)) * step / step) * step
    return max(cfg["min_volume"], min(volume, cfg["min_volume"] * 100))

def open_trade(symbol: str, side: str, volume: int, sl: float, tp: float):
    symbol_id  = get_symbol_id(symbol)
    trade_side = "BUY" if side == "BUY" else "SELL"
    order = {
        "symbolId":  symbol_id,
        "orderType": "MARKET",
        "tradeSide": trade_side,
        "volume":    volume,
        "stopLoss":  int(sl * 100000),
        "takeProfit":int(tp * 100000),
    }
    r = requests.post(
        f"{BASE_URL}/v2/tradingaccounts/{CTRADER_ACCOUNT_ID}/orders",
        headers=ctrader_headers(),
        json=order,
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def close_trade(position_id: str):
    r = requests.delete(
        f"{BASE_URL}/v2/tradingaccounts/{CTRADER_ACCOUNT_ID}/positions/{position_id}",
        headers=ctrader_headers(),
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def modify_sl(position_id: str, new_sl: float):
    r = requests.patch(
        f"{BASE_URL}/v2/tradingaccounts/{CTRADER_ACCOUNT_ID}/positions/{position_id}",
        headers=ctrader_headers(),
        json={"stopLoss": int(new_sl * 100000)},
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def check_breakeven(symbol: str):
    pos = state.open_positions.get(symbol)
    if not pos or pos.get("breakeven_done"):
        return
    try:
        prices  = get_price(symbol)
        current = prices["bid"] if pos["side"] == "SELL" else prices["ask"]
        entry   = pos["entry"]
        tp      = pos["tp"]
        total   = abs(tp - entry)
        done    = abs(tp - current)
        pct     = ((total - done) / total * 100) if total > 0 else 0
        if pct >= BREAKEVEN_PCT:
            new_sl = round(entry, 5)
            if pos["side"] == "BUY"  and new_sl <= pos["sl"]: return
            if pos["side"] == "SELL" and new_sl >= pos["sl"]: return
            modify_sl(pos["id"], new_sl)
            state.open_positions[symbol]["sl"]             = new_sl
            state.open_positions[symbol]["breakeven_done"] = True
            log.info(f"🔒 Breakeven activado: {symbol} SL → {new_sl}")
    except Exception as e:
        log.warning(f"Breakeven error {symbol}: {e}")

# ─── FILTROS ──────────────────────────────────────────────────────────────────
def is_kill_zone():
    hour = datetime.utcnow().hour
    for start, end in KILL_ZONES:
        if start <= hour < end:
            return True, "London Open" if start == 7 else "New York Open"
    return False, ""

def run_all_filters(signal, symbol, balance):
    state.reset_if_new_day()
    action    = signal.get("action", "").upper()
    htf_trend = signal.get("htf_trend", "").lower()
    bias      = signal.get("daily_bias", "").lower()
    tp_pips   = float(signal.get("take_profit_pips", SYMBOL_CONFIG[symbol]["default_tp_pips"]))
    sl_pips   = float(signal.get("stop_loss_pips",   SYMBOL_CONFIG[symbol]["default_sl_pips"]))
    passed, failed = [], []

    if state.paused:
        failed.append("Bot pausado por 2 SL consecutivos")
        return False, passed, failed

    if state.trades_today >= MAX_TRADES_DAY:
        failed.append(f"Límite diario {state.trades_today}/{MAX_TRADES_DAY}")
    else:
        passed.append(f"✅ Trades {state.trades_today}/{MAX_TRADES_DAY}")

    in_kz, kz_name = is_kill_zone()
    if not in_kz:
        failed.append(f"Fuera de Kill Zone (hora UTC: {datetime.utcnow().hour}h)")
    else:
        passed.append(f"✅ Kill Zone: {kz_name}")

    current_bias = state.daily_bias.get(symbol) or bias
    if current_bias and current_bias not in ["neutral", ""]:
        state.daily_bias[symbol] = current_bias
        if action == "BUY" and current_bias == "bearish":
            failed.append("BUY contra sesgo bajista")
        elif action == "SELL" and current_bias == "bullish":
            failed.append("SELL contra sesgo alcista")
        else:
            passed.append(f"✅ Sesgo: {current_bias}")

    if htf_trend and htf_trend != "ranging":
        if action == "BUY" and htf_trend == "bearish":
            failed.append("BUY contra tendencia bajista 1h")
        elif action == "SELL" and htf_trend == "bullish":
            failed.append("SELL contra tendencia alcista 1h")
        else:
            passed.append(f"✅ Tendencia 1h: {htf_trend}")

    if sl_pips > 0:
        rr = tp_pips / sl_pips
        if rr < MIN_RR:
            failed.append(f"R:R {rr:.1f} < mínimo {MIN_RR}")
        else:
            passed.append(f"✅ R:R 1:{rr:.1f}")

    if symbol in state.open_positions:
        failed.append(f"Ya hay posición abierta en {symbol}")

    return len(failed) == 0, passed, failed

# ─── PROCESAR SEÑAL ───────────────────────────────────────────────────────────
def process_signal(signal: dict):
    raw    = signal.get("symbol", "").upper().replace("/", "").replace(" ", "").replace(".", "")
    symbol = ALLOWED_SYMBOLS.get(raw)
    if not symbol:
        log.warning(f"Símbolo no permitido: {raw}")
        return

    action    = signal.get("action", "").upper()
    confirmed = signal.get("confirmed", True)
    tf        = signal.get("timeframe", "5m")
    cfg       = SYMBOL_CONFIG.get(symbol, SYMBOL_CONFIG["XAUUSD"])
    tp_pips   = float(signal.get("take_profit_pips", cfg["default_tp_pips"]))
    sl_pips   = float(signal.get("stop_loss_pips",   cfg["default_sl_pips"]))
    pip       = cfg["pip_size"]

    if action == "RESULT":
        result = signal.get("result", "").lower()
        if result in ["win", "sl"]:
            state.register_result(result)
            state.open_positions.pop(symbol, None)
        return

    if action == "CLOSE":
        pos = state.open_positions.get(symbol)
        if pos:
            try:
                close_trade(pos["id"])
                state.open_positions.pop(symbol, None)
                log.info(f"🔒 Posición cerrada: {symbol}")
            except Exception as e:
                log.error(f"Error cerrando: {e}")
        return

    if not confirmed:
        state.pending_signals[symbol] = signal
        log.info(f"⏳ Señal pendiente: {symbol}")
        return

    if symbol in state.pending_signals:
        original = state.pending_signals.pop(symbol)
        signal   = {**original, **signal}

    try:
        info    = get_account_info()
        balance = info["balance"]
        if state.balance_start == 0:
            state.balance_start = balance
    except Exception as e:
        log.error(f"Error balance: {e}")
        balance = state.balance_start or 100

    approved, passed_f, failed_f = run_all_filters(signal, symbol, balance)
    if not approved:
        state.rejected_today += 1
        state.rejection_log.append({"time": datetime.utcnow().strftime("%H:%M"), "symbol": symbol, "reason": failed_f[0]})
        log.warning(f"🚫 Rechazada {symbol} {action}: {failed_f[0]}")
        return

    try:
        prices = get_price(symbol)
        ask    = prices["ask"]
        bid    = prices["bid"]
        rr     = round(tp_pips / sl_pips, 1)

        if action == "BUY":
            entry = ask
            sl    = round(entry - sl_pips * pip, 5)
            tp    = round(entry + tp_pips * pip, 5)
            be    = round(entry + (tp_pips * BREAKEVEN_PCT / 100) * pip, 5)
        else:
            entry = bid
            sl    = round(entry + sl_pips * pip, 5)
            tp    = round(entry - tp_pips * pip, 5)
            be    = round(entry - (tp_pips * BREAKEVEN_PCT / 100) * pip, 5)

        volume = calculate_volume(symbol, sl_pips, balance)
        result = open_trade(symbol, action, volume, sl, tp)
        pos_id = str(result.get("positionId") or result.get("orderId", ""))

        state.open_positions[symbol] = {
            "id": pos_id, "entry": entry, "sl": sl, "tp": tp,
            "side": action, "volume": volume, "breakeven_done": False, "be_price": be
        }
        state.trades_today += 1

        risk_usd   = round(balance * RISK_PER_TRADE / 100, 2)
        reward_usd = round(risk_usd * rr, 2)
        _, kz      = is_kill_zone()
        emoji      = "🟢" if action == "BUY" else "🔴"

        log.info(
            f"{emoji} {action} {symbol} @ {entry} | "
            f"SL:{sl} TP:{tp} BE:{be} | "
            f"Vol:{volume} R:R 1:{rr} | ${balance:.2f}"
        )

    except Exception as e:
        log.error(f"Error ejecutando orden: {e}")


def get_stats():
    return {
        "trades_today":    state.trades_today,
        "wins_today":      state.wins_today,
        "losses_today":    state.losses_today,
        "win_rate":        round(state.win_rate(), 1),
        "pnl_today":       round(state.pnl_today, 2),
        "rejected_today":  state.rejected_today,
        "consecutive_sl":  state.consecutive_sl,
        "paused":          state.paused,
        "open_positions":  {k: {"side": v["side"], "entry": v["entry"], "sl": v["sl"], "tp": v["tp"], "breakeven_done": v["breakeven_done"]} for k, v in state.open_positions.items()},
        "pending_signals": list(state.pending_signals.keys()),
        "daily_bias":      state.daily_bias,
        "rejection_log":   state.rejection_log[-5:],
    }


def resume_bot():
    state.paused         = False
    state.consecutive_sl = 0
    log.info("▶️ Bot reanudado")
