# TRADING BOT SERVER v10 - Pepperstone cTrader
import os, json, logging
from flask import Flask, request, jsonify
from bias_engine import (
    update_bias_from_tradingview, get_bias,
    get_all_biases, get_all_cached_biases
)
from bot import (
    process_signal, get_account_info, get_price,
    get_stats, resume_bot, check_breakeven,
    ALLOWED_SYMBOLS, MAX_TRADES_DAY, MIN_RR,
    MAX_LOSS_DAY_PCT, MAX_CONSECUTIVE_SL,
    BREAKEVEN_PCT, CTRADER_ENV
)
from scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log    = logging.getLogger(__name__)
app    = Flask(__name__)
SECRET = os.getenv("WEBHOOK_SECRET", "clave_secreta_1234")

start_scheduler()

@app.route("/callback", methods=["GET"])
def callback():
    """Captura el código OAuth de cTrader."""
    code = request.args.get("code", "")
    if code:
        log.info(f"✅ Código OAuth recibido: {code}")
        return jsonify({"code": code, "message": "Copiá este código y pegalo en el chat"})
    return jsonify({"error": "No se recibió código"}), 400

@app.route("/", methods=["GET"])
def health():
    stats  = get_stats()
    biases = get_all_cached_biases()
    return jsonify({
        "status":  "pausado 🛑" if stats["paused"] else "activo ✅",
        "broker":  f"Pepperstone cTrader ({CTRADER_ENV})",
        "biases":  {k: {"bias": v["bias"], "confidence": round(v.get("confidence",0),2)} if v else "-" for k,v in biases.items()},
        "stats":   stats
    })

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw = request.get_data(as_text=True)
        log.info(f"Webhook: {raw[:200]}")
        try:
            data = json.loads(raw)
        except Exception:
            return jsonify({"error": "JSON invalido"}), 400

        if data.get("secret") != SECRET:
            return jsonify({"error": "No autorizado"}), 403

        data.pop("secret", None)
        action = data.get("action", "").upper()

        if action == "BIAS":
            symbol     = data.get("symbol", "").upper().replace("/","")
            bias       = data.get("bias", "").lower()
            confidence = float(data.get("confidence", 0.85))
            reason     = data.get("reason", "")
            if bias not in ["bullish", "bearish", "ranging"]:
                return jsonify({"error": "bias: bullish|bearish|ranging"}), 400
            result = update_bias_from_tradingview(symbol, bias, confidence, reason)
            return jsonify({"status": "ok", "bias": result})
        else:
            raw_sym    = data.get("symbol","").upper().replace("/","").replace(".","")
            instrument = ALLOWED_SYMBOLS.get(raw_sym, raw_sym)
            if not data.get("daily_bias"):
                cached   = get_all_cached_biases()
                sym_bias = cached.get(instrument)
                if sym_bias and sym_bias.get("bias"):
                    data["daily_bias"] = sym_bias["bias"]
                    data["htf_trend"]  = sym_bias["bias"]
            process_signal(data)
            return jsonify({"status": "ok"})

    except Exception as e:
        log.error(f"Error webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/status", methods=["GET"])
def status():
    try:
        info   = get_account_info()
        xau    = get_price("XAUUSD")
        btc    = get_price("BTCUSD")
        stats  = get_stats()
        biases = get_all_cached_biases()
        for sym in list(stats["open_positions"].keys()):
            try: check_breakeven(sym)
            except Exception: pass
        return jsonify({
            "status":      "pausado" if stats["paused"] else "activo ✅",
            "broker":      f"Pepperstone cTrader ({CTRADER_ENV})",
            "cuenta":      info,
            "precios":     {"XAUUSD": xau, "BTCUSD": btc},
            "sesgos":      {sym: {"bias": b["bias"] if b else "-", "confidence": round(b.get("confidence",0),2) if b else 0, "reason": b.get("reason","-") if b else "-"} for sym,b in biases.items()},
            "rendimiento": stats,
        })
    except Exception as e:
        log.error(f"Error status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/recalculate-bias", methods=["POST"])
def recalculate_bias():
    try:
        data = request.get_json() or {}
        if data.get("secret") != SECRET:
            return jsonify({"error": "No autorizado"}), 403
        symbol  = data.get("symbol","").upper().replace("/","")
        results = {symbol: get_bias(symbol, force_recalc=True)} if symbol in ["XAUUSD","BTCUSD"] else get_all_biases()
        return jsonify({"status": "ok", "biases": {sym: {"bias": r["bias"], "confidence": r["confidence"]} for sym,r in results.items()}})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/resume", methods=["POST"])
def resume():
    try:
        data = request.get_json() or {}
        if data.get("secret") != SECRET:
            return jsonify({"error": "No autorizado"}), 403
        resume_bot()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
