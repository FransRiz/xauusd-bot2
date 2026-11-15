import logging
import asyncio
from datetime import datetime
import yfinance as yf
import pandas as pd
import numpy as np
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# ================== KONFIGURASI ==================
BOT_TOKEN = "8967959083:AAHwc0UYdecH4yeVAv4iYA-716dmRCu2XdU"
ALLOWED_CHAT_ID = 820142205          # Chat ID Anda

# Default settings
DEFAULT_TF = "1h"                    # 15m, 30m, 1h, 4h
DEFAULT_STRATEGY = "all"             # ma, breakout, scalp, all
AUTO_SIGNAL = False
CHECK_INTERVAL = 300                 # detik (5 menit)

# =================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# State
user_settings = {
    "tf": DEFAULT_TF,
    "strategy": DEFAULT_STRATEGY,
    "auto": AUTO_SIGNAL
}

TF_MAP = {
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h"
}

def get_gold_data(interval="1h", period="5d"):
    """Ambil data XAUUSD (menggunakan GC=F sebagai proksi)"""
    try:
        ticker = yf.Ticker("GC=F")
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            return None
        df = df.reset_index()
        return df
    except Exception as e:
        logger.error(f"Error ambil data: {e}")
        return None

def calculate_indicators(df):
    """Hitung indikator teknis"""
    df = df.copy()
    df["EMA_fast"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA_slow"] = df["Close"].ewm(span=21, adjust=False).mean()
    df["SMA_50"] = df["Close"].rolling(50).mean()

    # RSI
    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    # ATR
    high_low = df["High"] - df["Low"]
    high_close = np.abs(df["High"] - df["Close"].shift())
    low_close = np.abs(df["Low"] - df["Close"].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df["ATR"] = true_range.rolling(14).mean()

    return df

def strategy_ma_rsi(df):
    """Strategi Moving Average + RSI"""
    if len(df) < 30:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    signal = None
    reason = []

    # Crossover
    if prev["EMA_fast"] < prev["EMA_slow"] and last["EMA_fast"] > last["EMA_slow"]:
        if last["RSI"] < 70:
            signal = "BUY"
            reason.append("EMA 9 cross above EMA 21")
            reason.append(f"RSI: {last['RSI']:.1f}")
    elif prev["EMA_fast"] > prev["EMA_slow"] and last["EMA_fast"] < last["EMA_slow"]:
        if last["RSI"] > 30:
            signal = "SELL"
            reason.append("EMA 9 cross below EMA 21")
            reason.append(f"RSI: {last['RSI']:.1f}")

    if signal:
        atr = last["ATR"]
        entry = last["Close"]
        if signal == "BUY":
            sl = entry - (1.5 * atr)
            tp = entry + (2.5 * atr)
        else:
            sl = entry + (1.5 * atr)
            tp = entry - (2.5 * atr)

        return {
            "strategy": "MA + RSI",
            "signal": signal,
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "reason": " | ".join(reason)
        }
    return None

def strategy_breakout(df):
    """Strategi Breakout High/Low"""
    if len(df) < 25:
        return None

    last = df.iloc[-1]
    recent = df.iloc[-21:-1]  # 20 candle sebelumnya

    high_20 = recent["High"].max()
    low_20 = recent["Low"].min()
    atr = last["ATR"]

    signal = None
    reason = []

    if last["Close"] > high_20 and last["Close"] > last["EMA_slow"]:
        signal = "BUY"
        reason.append(f"Breakout High 20: {high_20:.2f}")
    elif last["Close"] < low_20 and last["Close"] < last["EMA_slow"]:
        signal = "SELL"
        reason.append(f"Breakout Low 20: {low_20:.2f}")

    if signal:
        entry = last["Close"]
        if signal == "BUY":
            sl = entry - (1.8 * atr)
            tp = entry + (2.8 * atr)
        else:
            sl = entry + (1.8 * atr)
            tp = entry - (2.8 * atr)

        return {
            "strategy": "Breakout",
            "signal": signal,
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "reason": " | ".join(reason)
        }
    return None

def strategy_scalp(df):
    """Strategi Scalping sederhana (RSI + EMA)"""
    if len(df) < 20:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    signal = None
    reason = []

    # RSI ekstrem + arah EMA
    if last["RSI"] < 28 and last["EMA_fast"] > last["EMA_slow"]:
        signal = "BUY"
        reason.append(f"RSI Oversold ({last['RSI']:.1f})")
    elif last["RSI"] > 72 and last["EMA_fast"] < last["EMA_slow"]:
        signal = "SELL"
        reason.append(f"RSI Overbought ({last['RSI']:.1f})")

    if signal:
        atr = last["ATR"]
        entry = last["Close"]
        if signal == "BUY":
            sl = entry - (1.2 * atr)
            tp = entry + (1.8 * atr)
        else:
            sl = entry + (1.2 * atr)
            tp = entry - (1.8 * atr)

        return {
            "strategy": "Scalping",
            "signal": signal,
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "reason": " | ".join(reason)
        }
    return None

def generate_signals(tf="1h", strategy="all"):
    """Generate sinyal berdasarkan setting"""
    df = get_gold_data(interval=tf, period="10d")
    if df is None or len(df) < 30:
        return []

    df = calculate_indicators(df)
    signals = []

    if strategy in ["ma", "all"]:
        s = strategy_ma_rsi(df)
        if s:
            signals.append(s)

    if strategy in ["breakout", "all"]:
        s = strategy_breakout(df)
        if s:
            signals.append(s)

    if strategy in ["scalp", "all"]:
        s = strategy_scalp(df)
        if s:
            signals.append(s)

    return signals

def format_signal(sig, tf):
    emoji = "🟢" if sig["signal"] == "BUY" else "🔴"
    text = (
        f"{emoji} *SINYAL XAUUSD* {emoji}\n\n"
        f"Strategi : `{sig['strategy']}`\n"
        f"Timeframe: `{tf.upper()}`\n"
        f"Arah     : *{sig['signal']}*\n"
        f"Entry    : `{sig['entry']}`\n"
        f"SL       : `{sig['sl']}`\n"
        f"TP       : `{sig['tp']}`\n"
        f"Alasan   : {sig['reason']}\n\n"
        f"⚠️ Hanya sinyal. Entry manual di HFM.\n"
        f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    return text

async def restricted(update: Update):
    """Cek apakah user diizinkan"""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        await update.message.reply_text("Akses ditolak.")
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update):
        return
    text = (
        "🤖 *XAUUSD Signal Bot*\n\n"
        "Bot siap digunakan.\n"
        "Ketik /help untuk melihat perintah.\n\n"
        "⚠️ Trading berisiko tinggi. Gunakan dengan bijak."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update):
        return
    text = (
        "*Daftar Perintah:*\n\n"
        "/signal - Minta sinyal sekarang\n"
        "/harga - Harga XAUUSD saat ini\n"
        "/status - Status bot & setting\n"
        "/settf M15|M30|H1|H4 - Ganti timeframe\n"
        "/setstrategy ma|breakout|scalp|all - Ganti strategi\n"
        "/on - Aktifkan auto signal\n"
        "/off - Matikan auto signal\n"
        "/help - Bantuan ini"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def harga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update):
        return
    df = get_gold_data(interval="1m", period="1d")
    if df is None:
        await update.message.reply_text("Gagal mengambil data harga.")
        return
    last = df.iloc[-1]
    text = (
        f"💰 *XAUUSD (proksi GC=F)*\n\n"
        f"Harga : `{last['Close']:.2f}`\n"
        f"High  : `{last['High']:.2f}`\n"
        f"Low   : `{last['Low']:.2f}`\n"
        f"Waktu : {datetime.now().strftime('%H:%M:%S')}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def signal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update):
        return
    await update.message.reply_text("Sedang menganalisis...")
    tf = user_settings["tf"]
    strategy = user_settings["strategy"]
    signals = generate_signals(tf=tf, strategy=strategy)

    if not signals:
        await update.message.reply_text(
            f"Tidak ada sinyal valid saat ini.\nTF: {tf.upper()} | Strategi: {strategy}"
        )
        return

    for sig in signals:
        await update.message.reply_text(format_signal(sig, tf), parse_mode="Markdown")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update):
        return
    text = (
        f"*Status Bot*\n\n"
        f"Timeframe : `{user_settings['tf'].upper()}`\n"
        f"Strategi  : `{user_settings['strategy']}`\n"
        f"Auto Signal: `{'ON' if user_settings['auto'] else 'OFF'}`\n"
        f"Interval  : `{CHECK_INTERVAL} detik`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def settf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update):
        return
    if not context.args:
        await update.message.reply_text("Contoh: /settf H1")
        return
    tf_input = context.args[0].upper()
    if tf_input in TF_MAP:
        user_settings["tf"] = TF_MAP[tf_input]
        await update.message.reply_text(f"Timeframe diganti ke {tf_input}")
    else:
        await update.message.reply_text("Pilihan: M15, M30, H1, H4")

async def setstrategy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update):
        return
    if not context.args:
        await update.message.reply_text("Contoh: /setstrategy all")
        return
    strat = context.args[0].lower()
    if strat in ["ma", "breakout", "scalp", "all"]:
        user_settings["strategy"] = strat
        await update.message.reply_text(f"Strategi diganti ke {strat}")
    else:
        await update.message.reply_text("Pilihan: ma, breakout, scalp, all")

async def on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update):
        return
    user_settings["auto"] = True
    await update.message.reply_text("Auto signal diaktifkan.")

async def off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update):
        return
    user_settings["auto"] = False
    await update.message.reply_text("Auto signal dimatikan.")

async def auto_signal_job(context: ContextTypes.DEFAULT_TYPE):
    if not user_settings["auto"]:
        return
    tf = user_settings["tf"]
    strategy = user_settings["strategy"]
    signals = generate_signals(tf=tf, strategy=strategy)
    for sig in signals:
        try:
            await context.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=format_signal(sig, tf),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Gagal kirim auto signal: {e}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("signal", signal_cmd))
    app.add_handler(CommandHandler("harga", harga))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("settf", settf))
    app.add_handler(CommandHandler("setstrategy", setstrategy))
    app.add_handler(CommandHandler("on", on_cmd))
    app.add_handler(CommandHandler("off", off_cmd))

    # Job auto signal
    job_queue = app.job_queue
    job_queue.run_repeating(auto_signal_job, interval=CHECK_INTERVAL, first=10)

    print("Bot XAUUSD sedang berjalan...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()