import os
import time
import requests
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────
TOKEN    = os.environ.get("TG_TOKEN", "8541120079:AAFfHCbn6hMPB8TrrLX_xHeb-AEcWUzHj34")   # Telegram bot token
CHAT_ID  = os.environ.get("TG_CHAT_ID", "8053005814") # Your Telegram chat ID
CAPITAL  = float(os.environ.get("CAPITAL", "500"))
MIN_ADJ  = float(os.environ.get("MIN_ADJ", "0.8"))  # Min adjusted profit %
INTERVAL = int(os.environ.get("INTERVAL", "60"))     # Seconds between scans

FEE_MEXC    = 0.001
FEE_BITMART = 0.0025
TOTAL_FEES  = FEE_MEXC + FEE_BITMART

HOURLY_VOL = {5: 0.8, 6: 1.2, 7: 2.0, 8: 3.2, 9: 5.0}

# ── Tokens: sym, mexc_symbol, bitmart_symbol, vol, net, wfee_usd, wtime_min
TOKENS = [
    ("ALGO",  "ALGOUSDT",   "ALGO_USDT",  6, "ALGO",  0.001, 5),
    ("ROSE",  "ROSEUSDT",   "ROSE_USDT",  7, "ROSE",  0.007, 8),
    ("CFX",   "CFXUSDT",    "CFX_USDT",   7, "CFX",   0.05,  6),
    ("ONE",   "ONEUSDT",    "ONE_USDT",   8, "ONE",   0.012, 10),
    ("KAVA",  "KAVAUSDT",   "KAVA_USDT",  7, "KAVA",  0.019, 8),
    ("ZIL",   "ZILUSDT",    "ZIL_USDT",   7, "ZIL",   0.017, 5),
    ("ICX",   "ICXUSDT",    "ICX_USDT",   7, "ICX",   0.095, 8),
    ("IOST",  "IOSTUSDT",   "IOST_USDT",  8, "IOST",  0.08,  5),
    ("WAN",   "WANUSDT",    "WAN_USDT",   8, "WAN",   0.042, 8),
    ("NKN",   "NKNUSDT",    "NKN_USDT",   8, "NKN",   0.062, 6),
    ("BAND",  "BANDUSDT",   "BAND_USDT",  7, "BAND",  0.112, 10),
    ("CELO",  "CELOUSDT",   "CELO_USDT",  7, "CELO",  0.024, 5),
    ("RUNE",  "RUNEUSDT",   "RUNE_USDT",  6, "THOR",  0.044, 8),
    ("RAY",   "RAYUSDT",    "RAY_USDT",   7, "SOL",   0.01,  3),
    ("GRT",   "GRTUSDT",    "GRT_USDT",   5, "ERC20", 1.50,  20),
    ("LRC",   "LRCUSDT",    "LRC_USDT",   6, "ERC20", 1.20,  20),
    ("ENJ",   "ENJUSDT",    "ENJ_USDT",   6, "ERC20", 1.00,  20),
    ("STORJ", "STORJUSDT",  "STORJ_USDT", 7, "ERC20", 1.80,  20),
    ("FLUX",  "FLUXUSDT",   "FLUX_USDT",  7, "FLUX",  0.038, 8),
    ("RVN",   "RVNUSDT",    "RVN_USDT",   8, "RVN",   0.019, 10),
    ("HBAR",  "HBARUSDT",   "HBAR_USDT",  5, "HBAR",  0.071, 5),
    ("ZEN",   "ZENUSDT",    "ZEN_USDT",   7, "ZEN",   0.068, 10),
    ("MINA",  "MINAUSDT",   "MINA_USDT",  7, "MINA",  0.41,  8),
    ("VET",   "VETUSDT",    "VET_USDT",   5, "VET",   0.22,  5),
    ("KLAY",  "KLAYUSDT",   "KLAY_USDT",  7, "KLAY",  0.02,  5),
    ("CHZ",   "CHZUSDT",    "CHZ_USDT",   7, "ERC20", 1.00,  20),
    ("ONT",   "ONTUSDT",    "ONT_USDT",   7, "ONT",   0.10,  8),
    ("QTUM",  "QTUMUSDT",   "QTUM_USDT",  7, "QTUM",  0.01,  8),
]

# ── Fetch prices ──────────────────────────────────────────────
def fetch_mexc():
    try:
        r = requests.get("https://api.mexc.com/api/v3/ticker/price", timeout=10)
        r.raise_for_status()
        data = r.json()
        return {item["symbol"]: float(item["price"]) for item in data if "price" in item}
    except Exception as e:
        log.error(f"MEXC fetch error: {e}")
        return {}

def fetch_bitmart():
    try:
        r = requests.get("https://api-cloud.bitmart.com/spot/quotation/v3/tickers", timeout=10)
        r.raise_for_status()
        data = r.json()
        result = {}
        for row in data.get("data", []):
            if len(row) >= 2:
                result[row[0]] = float(row[1])
        return result
    except Exception as e:
        log.error(f"BitMart fetch error: {e}")
        return {}

# ── Calculate opportunity ─────────────────────────────────────
def calc_opp(sym, mx_sym, bm_sym, vol, net, wfee, wtime, mx_prices, bm_prices):
    p_mx = mx_prices.get(mx_sym)
    p_bm = bm_prices.get(bm_sym)
    if not p_mx or not p_bm or p_mx <= 0:
        return None

    raw = (p_bm - p_mx) / p_mx
    if raw <= 0:
        return None

    wfee_pct  = (wfee / CAPITAL) * 100
    net_pct   = raw * 100 - TOTAL_FEES * 100 - wfee_pct
    risk_pct  = HOURLY_VOL.get(min(vol, 9), 2.0) * (wtime / 60) * 1.5
    adj_pct   = net_pct - risk_pct
    adj_usd   = CAPITAL * (adj_pct / 100)

    if adj_pct < MIN_ADJ:
        return None

    strength = "🔴 HIGH" if adj_pct > 2 else "🟡 MED" if adj_pct > 0.5 else "🟢 LOW"

    return {
        "sym":      sym,
        "net":      net,
        "p_mx":     p_mx,
        "p_bm":     p_bm,
        "raw_pct":  raw * 100,
        "adj_pct":  adj_pct,
        "adj_usd":  adj_usd,
        "wtime":    wtime,
        "wfee":     wfee,
        "strength": strength,
    }

# ── Send Telegram message ─────────────────────────────────────
def send(text):
    if not TOKEN or not CHAT_ID:
        log.warning("TG_TOKEN or TG_CHAT_ID not set")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def get_chat_id():
    """Call once to get your chat ID"""
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", timeout=10)
        updates = r.json().get("result", [])
        if updates:
            return updates[-1]["message"]["chat"]["id"]
    except:
        pass
    return None

# ── Main loop ─────────────────────────────────────────────────
def main():
    if not TOKEN:
        log.error("TG_TOKEN environment variable not set!")
        return

    log.info("ARB Bot started")
    log.info(f"Capital: ${CAPITAL} | Min profit: {MIN_ADJ}% | Interval: {INTERVAL}s")

    # Auto-detect chat ID if not set
    chat_id = CHAT_ID
    if not chat_id:
        log.info("TG_CHAT_ID not set, trying to auto-detect...")
        detected = get_chat_id()
        if detected:
            chat_id = str(detected)
            log.info(f"Detected chat ID: {chat_id}")
        else:
            log.error("Could not detect chat ID. Send a message to your bot first, then set TG_CHAT_ID.")
            return

    # Override global CHAT_ID
    global CHAT_ID
    CHAT_ID = chat_id

    send("🤖 <b>ARB Bot iniciado</b>\n"
         f"💰 Capital: <b>${CAPITAL}</b>\n"
         f"📊 Profit mínimo: <b>{MIN_ADJ}%</b>\n"
         f"⏱ Escaneo cada: <b>{INTERVAL}s</b>\n"
         f"🪙 Tokens monitoreados: <b>{len(TOKENS)}</b>")

    scan_count = 0
    last_alerts = {}  # sym -> last alert time, to avoid spam

    while True:
        try:
            scan_count += 1
            log.info(f"Scan #{scan_count}")

            mx = fetch_mexc()
            bm = fetch_bitmart()

            if not mx:
                log.warning("MEXC returned no prices")
            if not bm:
                log.warning("BitMart returned no prices")

            opps = []
            for (sym, mx_sym, bm_sym, vol, net, wfee, wtime) in TOKENS:
                opp = calc_opp(sym, mx_sym, bm_sym, vol, net, wfee, wtime, mx, bm)
                if opp:
                    opps.append(opp)

            opps.sort(key=lambda x: x["adj_pct"], reverse=True)

            now = time.time()
            for opp in opps:
                sym = opp["sym"]
                # Don't alert same token more than once per 10 minutes
                if now - last_alerts.get(sym, 0) < 600:
                    continue

                last_alerts[sym] = now
                msg = (
                    f"{opp['strength']} <b>OPORTUNIDAD DETECTADA</b>\n\n"
                    f"🪙 Token: <b>{sym}</b> ({opp['net']})\n"
                    f"📈 MEXC: <b>${opp['p_mx']:.6f}</b> → comprar\n"
                    f"📉 BitMart: <b>${opp['p_bm']:.6f}</b> → vender\n\n"
                    f"📊 Spread bruto: <b>{opp['raw_pct']:+.3f}%</b>\n"
                    f"✅ Profit ajustado: <b>{opp['adj_pct']:+.3f}%</b>\n"
                    f"💵 Ganancia est.: <b>${opp['adj_usd']:+.2f}</b> en ${CAPITAL:.0f}\n\n"
                    f"🚀 Transferencia: <b>~{opp['wtime']} min</b>\n"
                    f"💸 Fee retiro: <b>${opp['wfee']}</b>\n"
                    f"🕐 {datetime.now().strftime('%H:%M:%S')}"
                )
                send(msg)
                log.info(f"Alert sent: {sym} {opp['adj_pct']:+.3f}%")

            if scan_count % 10 == 0:
                # Every 10 scans send a summary
                if opps:
                    summary = f"📊 <b>Resumen scan #{scan_count}</b>\n"
                    summary += f"🔍 {len(opps)} oportunidades ≥ {MIN_ADJ}%\n"
                    summary += f"🏆 Mejor: <b>{opps[0]['sym']}</b> {opps[0]['adj_pct']:+.3f}%\n"
                    summary += f"⏱ {datetime.now().strftime('%H:%M:%S')}"
                    send(summary)
                else:
                    send(f"📊 Scan #{scan_count} — sin oportunidades ≥ {MIN_ADJ}%")

        except Exception as e:
            log.error(f"Scan error: {e}")

        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
