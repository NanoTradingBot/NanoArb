"""
TRADING BOT — XAU/USD & BTC/USD
OANDA API v20 (gratuita)
"""
import os, asyncio, logging, requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────────────────────────
OANDA_API_KEY    = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_ENV        = os.getenv("OANDA_ENV", "practice")  # practice=demo, live=real

RISK_PER_TRADE     = float(os.getenv("RISK_PER_TRADE",    "1.0"))
MAX_TRADES_DAY     = int(os.getenv("MAX_TRADES_DAY",      "10"))
MAX_LOSS_DAY_PCT   = float(os.getenv("MAX_LOSS_DAY_PCT",  "2.0"))
MIN_RR             = float(os.getenv("MIN_RR",            "1.5"))
MAX_CONSECUTIVE_SL = int(os.getenv("MAX_CONSECUTIVE_SL",  "2"))
BREAKEVEN_PCT      = float(os.getenv("BREAKEVEN_PCT",     "50"))

# URL base según entorno
BASE_URL = (
    "https://api-fxpractice.oanda.com"
    if OANDA_ENV == "practice"
    else "https://api-fxtrade.oanda.com"
)

KILL_ZONES = [(7, 9), (12, 14)]

# Instrumentos OANDA
ALLOWED_SYMBOLS = {
    "XAUUSD":  "XAU_USD",
    "BTCUSD":  "BTC_USD",
    "XAU/USD": "XAU_USD",
    "BTC/USD": "BTC_USD",
    "XAUUSD.": "XAU_USD",
    "BTCUSD.": "BTC_USD",
    "XAU_USD": "XAU_USD",
    "BTC_USD": "BTC_USD",
}

SYMBOL_CONFIG = {
    "XAU_USD": {"pip_size": 0.01, "default_tp_pips": 30,  "default_sl_pips": 18,  "units": 1},
    "BTC_USD": {"pip_size": 1.0,  "default_tp_pips": 200, "default_sl_pips": 120, "units": 1},
}

# ─── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

if not OANDA_API_KEY:
    log.error("❌ OANDA_API_KEY no configurado")
if not OANDA_ACCOUNT_ID:
    log.error("❌ OANDA_ACCOUNT_ID no configurado")
else:
    log.info(f"✅ OANDA conectado — cuenta: {OANDA_ACCOUNT_ID[:8]}... ({OANDA_ENV})")

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

# ─── TELEGRAM (opcional) ──────────────────────────────────────────────────────
def send_telegram(msg: str):
    pass  # Desactivado — usar /status para monitorear

# ─── OANDA API ────────────────────────────────────────────────────────────────
def oanda_headers():
    return {
        "Authorization": f"Bearer {OANDA_API_KEY}",
        "Content-Type": "application/json"
    }

def get_account_info():
    """Retorna balance y NAV de la cuenta."""
    r = requests.get(
        f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/summary",
        headers=oanda_headers(),
        timeout=10
    )
    r.raise_for_status()
    data    = r.json()
    account = data.get("account", {})
    return {
        "balance":    float(account.get("balance", 0)),
        "nav":        float(account.get("NAV", 0)),
        "unrealized": float(account.get("unrealizedPL", 0)),
        "margin":     float(account.get("marginAvailable", 0)),
    }

def get_price(instrument: str):
    """Retorna bid/ask del instrumento."""
    r = requests.get(
        f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing",
        headers=oanda_headers(),
        params={"instruments": instrument},
        timeout=10
    )
    r.raise_for_status()
    prices = r.json().get("prices", [{}])[0]
    bid    = float(prices.get("bids", [{"price": 0}])[0]["price"])
    ask    = float(prices.get("asks", [{"price": 0}])[0]["price"])
    return {"bid": bid, "ask": ask}

def get_candles(instrument: str, count: int = 100, granularity: str = "H1"):
    """Retorna velas OHLC."""
    r = requests.get(
        f"{BASE_URL}/v3/instruments/{instrument}/candles",
        headers=oanda_headers(),
        params={"count": count, "granularity": granularity, "price": "M"},
        timeout=15
    )
    r.raise_for_status()
    raw = r.json().get("candles", [])
    return [{
        "time":  c["time"],
        "open":  float(c["mid"]["o"]),
        "high":  float(c["mid"]["h"]),
        "low":   float(c["mid"]["l"]),
        "close": float(c["mid"]["c"]),
    } for c in raw if c.get("complete")]

def calculate_units(instrument: str, sl_pips: float, balance: float):
    """Calcula unidades basado en riesgo fijo."""
    cfg      = SYMBOL_CONFIG.get(instrument, SYMBOL_CONFIG["XAU_USD"])
    risk_usd = balance * (RISK_PER_TRADE / 100)
    pip      = cfg["pip_size"]
    units    = int(risk_usd / (sl_pips * pip))
    units    = max(1, min(units, 10000))
    return units

def open_trade(instrument: str, side: str, units: int, sl: float, tp: float):
    """Abre una orden de mercado en OANDA."""
    direction = units if side == "BUY" else -units
    order = {
        "order": {
            "type":       "MARKET",
            "instrument": instrument,
            "units":      str(direction),
            "stopLossOnFill":   {"price": str(round(sl, 5))},
            "takeProfitOnFill": {"price": str(round(tp, 5))},
        }
    }
    r = requests.post(
        f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/orders",
        headers=oanda_headers(),
        json=order,
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def close_trade(trade_id: str):
    """Cierra un trade abierto."""
    r = requests.put(
        f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/close",
        headers=oanda_headers(),
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def modify_trade_sl(trade_id: str, new_sl: float):
    """Modifica SL de un trade (para breakeven)."""
    r = requests.put(
        f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/orders",
        headers=oanda_headers(),
        json={"stopLoss": {"price": str(round(new_sl, 5))}},
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def check_breakeven(symbol: str):
    """Mueve SL a breakeven cuando el precio llega al 50% del TP."""
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
            modify_trade_sl(pos["id"], new_sl)
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

def run_all_filters(signal, instrument, balance):
    state.reset_if_new_day()
    action    = signal.get("action", "").upper()
    htf_trend = signal.get("htf_trend", "").lower()
    bias      = signal.get("daily_bias", "").lower()
    tp_pips   = float(signal.get("take_profit_pips", SYMBOL_CONFIG[instrument]["default_tp_pips"]))
    sl_pips   = float(signal.get("stop_loss_pips",   SYMBOL_CONFIG[instrument]["default_sl_pips"]))

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

    # Sesgo
    current_bias = state.daily_bias.get(instrument) or bias
    if current_bias and current_bias not in ["neutral", ""]:
        state.daily_bias[instrument] = current_bias
        if action == "BUY" and current_bias == "bearish":
            failed.append("BUY contra sesgo bajista")
        elif action == "SELL" and current_bias == "bullish":
            failed.append("SELL contra sesgo alcista")
        else:
            passed.append(f"✅ Sesgo: {current_bias}")

    # Tendencia 1h
    if htf_trend and htf_trend != "ranging":
        if action == "BUY" and htf_trend == "bearish":
            failed.append("BUY contra tendencia bajista 1h")
        elif action == "SELL" and htf_trend == "bullish":
            failed.append("SELL contra tendencia alcista 1h")
        else:
            passed.append(f"✅ Tendencia 1h: {htf_trend}")

    # R:R
    if sl_pips > 0:
        rr = tp_pips / sl_pips
        if rr < MIN_RR:
            failed.append(f"R:R {rr:.1f} < mínimo {MIN_RR}")
        else:
            passed.append(f"✅ R:R 1:{rr:.1f}")

    if instrument in state.open_positions:
        failed.append(f"Ya hay posición abierta en {instrument}")

    return len(failed) == 0, passed, failed

# ─── PROCESAR SEÑAL ───────────────────────────────────────────────────────────
def process_signal(signal: dict):
    raw        = signal.get("symbol", "").upper().replace("/", "").replace(" ", "").replace(".", "")
    instrument = ALLOWED_SYMBOLS.get(raw)
    if not instrument:
        log.warning(f"Símbolo no permitido: {raw}")
        return

    action    = signal.get("action", "").upper()
    confirmed = signal.get("confirmed", True)
    tf        = signal.get("timeframe", "5m")
    cfg       = SYMBOL_CONFIG.get(instrument, SYMBOL_CONFIG["XAU_USD"])
    tp_pips   = float(signal.get("take_profit_pips", cfg["default_tp_pips"]))
    sl_pips   = float(signal.get("stop_loss_pips",   cfg["default_sl_pips"]))
    pip       = cfg["pip_size"]

    # Registrar resultado
    if action == "RESULT":
        result = signal.get("result", "").lower()
        if result in ["win", "sl"]:
            state.register_result(result)
            state.open_positions.pop(instrument, None)
        return

    # Cerrar posición
    if action == "CLOSE":
        pos = state.open_positions.get(instrument)
        if pos:
            try:
                close_trade(pos["id"])
                state.open_positions.pop(instrument, None)
                log.info(f"🔒 Posición cerrada: {instrument}")
            except Exception as e:
                log.error(f"Error cerrando: {e}")
        return

    # Señal pendiente
    if not confirmed:
        state.pending_signals[instrument] = signal
        log.info(f"⏳ Señal pendiente: {instrument}")
        return

    if instrument in state.pending_signals:
        original = state.pending_signals.pop(instrument)
        signal   = {**original, **signal}

    # Balance
    try:
        info    = get_account_info()
        balance = info["balance"]
        if state.balance_start == 0:
            state.balance_start = balance
    except Exception as e:
        log.error(f"Error balance OANDA: {e}")
        balance = state.balance_start or 100

    # Filtros
    approved, passed_f, failed_f = run_all_filters(signal, instrument, balance)
    if not approved:
        state.rejected_today += 1
        state.rejection_log.append({
            "time": datetime.utcnow().strftime("%H:%M"),
            "symbol": instrument, "reason": failed_f[0]
        })
        log.warning(f"🚫 Rechazada {instrument} {action}: {failed_f[0]}")
        return

    # Ejecutar orden
    try:
        prices = get_price(instrument)
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

        units  = calculate_units(instrument, sl_pips, balance)
        result = open_trade(instrument, action, units, sl, tp)

        # Obtener trade ID
        trade_id = (
            result.get("orderFillTransaction", {}).get("tradeOpened", {}).get("tradeID") or
            result.get("relatedTransactionIDs", [""])[0]
        )

        state.open_positions[instrument] = {
            "id": trade_id, "entry": entry, "sl": sl, "tp": tp,
            "side": action, "units": units, "breakeven_done": False, "be_price": be
        }
        state.trades_today += 1

        risk_usd   = round(balance * RISK_PER_TRADE / 100, 2)
        reward_usd = round(risk_usd * rr, 2)
        _, kz      = is_kill_zone()
        emoji      = "🟢" if action == "BUY" else "🔴"

        log.info(
            f"{emoji} {action} {instrument} @ {entry} | "
            f"SL:{sl} TP:{tp} BE:{be} | "
            f"Units:{units} R:R 1:{rr} | "
            f"Balance:${balance:.2f}"
        )

    except Exception as e:
        log.error(f"Error ejecutando orden: {e}")


def get_stats():
    return {
        "trades_today":   state.trades_today,
        "wins_today":     state.wins_today,
        "losses_today":   state.losses_today,
        "win_rate":       round(state.win_rate(), 1),
        "pnl_today":      round(state.pnl_today, 2),
        "rejected_today": state.rejected_today,
        "consecutive_sl": state.consecutive_sl,
        "paused":         state.paused,
        "open_positions": {
            k: {"side": v["side"], "entry": v["entry"],
                "sl": v["sl"], "tp": v["tp"], "breakeven_done": v["breakeven_done"]}
            for k, v in state.open_positions.items()
        },
        "pending_signals": list(state.pending_signals.keys()),
        "daily_bias":      state.daily_bias,
        "rejection_log":   state.rejection_log[-5:],
    }


def resume_bot():
    state.paused         = False
    state.consecutive_sl = 0
    log.info("▶️ Bot reanudado manualmente")
