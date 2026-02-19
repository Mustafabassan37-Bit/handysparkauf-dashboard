"""
Daytrading Analyse Server
- StockTwits Sentiment Scraping
- Yahoo Finance Kursdaten
- Technische Indikatoren (RSI, MACD, Bollinger Bands, EMA, SMA)
- Trading Signale & Alerts
"""

import json
import time
import threading
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ─── Cache ───────────────────────────────────────────────────────────────
cache = {}
CACHE_TTL = 300  # 5 Minuten

def get_cached(key):
    if key in cache and time.time() - cache[key]["ts"] < CACHE_TTL:
        return cache[key]["data"]
    return None

def set_cached(key, data):
    cache[key] = {"data": data, "ts": time.time()}


# ─── StockTwits API ─────────────────────────────────────────────────────
STOCKTWITS_API = "https://api.stocktwits.com/api/2"

def fetch_stocktwits(ticker):
    """Holt StockTwits Posts + Sentiment fuer einen Ticker."""
    cached = get_cached(f"st_{ticker}")
    if cached:
        return cached

    url = f"{STOCKTWITS_API}/streams/symbol/{ticker}.json"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": str(e), "messages": [], "sentiment": {}}

    messages = []
    bull_count = 0
    bear_count = 0

    for msg in data.get("messages", []):
        sentiment = None
        if msg.get("entities", {}).get("sentiment"):
            sentiment = msg["entities"]["sentiment"].get("basic")
            if sentiment == "Bullish":
                bull_count += 1
            elif sentiment == "Bearish":
                bear_count += 1

        messages.append({
            "id": msg.get("id"),
            "body": msg.get("body", ""),
            "user": msg.get("user", {}).get("username", "unknown"),
            "sentiment": sentiment,
            "created_at": msg.get("created_at"),
            "likes": msg.get("likes", {}).get("total", 0),
        })

    total = bull_count + bear_count
    sentiment_score = round(bull_count / total * 100, 1) if total > 0 else 50.0

    result = {
        "ticker": ticker,
        "messages": messages[:30],
        "sentiment": {
            "bullish": bull_count,
            "bearish": bear_count,
            "total_rated": total,
            "score": sentiment_score,
            "label": "Bullish" if sentiment_score > 60 else "Bearish" if sentiment_score < 40 else "Neutral",
        },
    }
    set_cached(f"st_{ticker}", result)
    return result


# ─── Yahoo Finance Kursdaten ────────────────────────────────────────────
def fetch_price_data(ticker, period="3mo", interval="1d"):
    """Holt Kursdaten von Yahoo Finance."""
    cache_key = f"price_{ticker}_{period}_{interval}"
    cached = get_cached(cache_key)
    if cached:
        return cached

    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, interval=interval)
        if df.empty:
            return {"error": f"Keine Daten fuer {ticker}"}

        df.index = df.index.strftime("%Y-%m-%d %H:%M")
        info = stock.info

        result = {
            "ticker": ticker,
            "name": info.get("shortName", ticker),
            "currency": info.get("currency", "USD"),
            "exchange": info.get("exchange", ""),
            "market_cap": info.get("marketCap"),
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "prev_close": info.get("previousClose"),
            "day_high": info.get("dayHigh"),
            "day_low": info.get("dayLow"),
            "volume": info.get("volume"),
            "candles": {
                "dates": df.index.tolist(),
                "open": df["Open"].round(2).tolist(),
                "high": df["High"].round(2).tolist(),
                "low": df["Low"].round(2).tolist(),
                "close": df["Close"].round(2).tolist(),
                "volume": df["Volume"].tolist(),
            },
        }
        set_cached(cache_key, result)
        return result
    except Exception as e:
        return {"error": str(e)}


# ─── Technische Indikatoren ─────────────────────────────────────────────
def calculate_indicators(ticker, period="3mo", interval="1d"):
    """Berechnet RSI, MACD, Bollinger Bands, EMA, SMA."""
    cache_key = f"ind_{ticker}_{period}_{interval}"
    cached = get_cached(cache_key)
    if cached:
        return cached

    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, interval=interval)
        if df.empty or len(df) < 26:
            return {"error": "Nicht genug Daten fuer Indikatoren"}

        close = df["Close"]

        # RSI (14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta.where(delta < 0, 0.0))
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        # MACD (12, 26, 9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line

        # Bollinger Bands (20, 2)
        sma20 = close.rolling(window=20).mean()
        std20 = close.rolling(window=20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20

        # EMA 9 & 21
        ema9 = close.ewm(span=9, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()

        # SMA 50 & 200
        sma50 = close.rolling(window=50).mean()
        sma200 = close.rolling(window=200).mean()

        # Volume SMA
        vol_sma = df["Volume"].rolling(window=20).mean()

        dates = df.index.strftime("%Y-%m-%d %H:%M").tolist()

        def safe_list(series):
            return [None if pd.isna(v) else round(v, 2) for v in series]

        result = {
            "ticker": ticker,
            "dates": dates,
            "close": safe_list(close),
            "rsi": safe_list(rsi),
            "macd": {
                "line": safe_list(macd_line),
                "signal": safe_list(signal_line),
                "histogram": safe_list(macd_hist),
            },
            "bollinger": {
                "upper": safe_list(bb_upper),
                "middle": safe_list(sma20),
                "lower": safe_list(bb_lower),
            },
            "ema9": safe_list(ema9),
            "ema21": safe_list(ema21),
            "sma50": safe_list(sma50),
            "sma200": safe_list(sma200),
            "volume": df["Volume"].tolist(),
            "volume_sma": safe_list(vol_sma),
        }
        set_cached(cache_key, result)
        return result
    except Exception as e:
        return {"error": str(e)}


# ─── Trading Signale ────────────────────────────────────────────────────
def generate_signals(ticker):
    """Generiert Trading-Signale basierend auf Indikatoren + Sentiment."""
    indicators = calculate_indicators(ticker)
    sentiment_data = fetch_stocktwits(ticker)

    if "error" in indicators:
        return {"error": indicators["error"]}

    signals = []
    score = 0  # -100 bis +100

    rsi_vals = [v for v in indicators["rsi"] if v is not None]
    macd_lines = [v for v in indicators["macd"]["line"] if v is not None]
    macd_signals = [v for v in indicators["macd"]["signal"] if v is not None]
    close_vals = [v for v in indicators["close"] if v is not None]
    bb_lower = [v for v in indicators["bollinger"]["lower"] if v is not None]
    bb_upper = [v for v in indicators["bollinger"]["upper"] if v is not None]
    ema9_vals = [v for v in indicators["ema9"] if v is not None]
    ema21_vals = [v for v in indicators["ema21"] if v is not None]

    # RSI Signal
    if rsi_vals:
        current_rsi = rsi_vals[-1]
        if current_rsi < 30:
            signals.append({"type": "BUY", "source": "RSI", "reason": f"RSI ueberverkauft ({current_rsi:.1f})", "strength": "stark"})
            score += 25
        elif current_rsi < 40:
            signals.append({"type": "BUY", "source": "RSI", "reason": f"RSI niedrig ({current_rsi:.1f})", "strength": "mittel"})
            score += 10
        elif current_rsi > 70:
            signals.append({"type": "SELL", "source": "RSI", "reason": f"RSI ueberkauft ({current_rsi:.1f})", "strength": "stark"})
            score -= 25
        elif current_rsi > 60:
            signals.append({"type": "SELL", "source": "RSI", "reason": f"RSI hoch ({current_rsi:.1f})", "strength": "mittel"})
            score -= 10

    # MACD Crossover
    if len(macd_lines) >= 2 and len(macd_signals) >= 2:
        if macd_lines[-1] > macd_signals[-1] and macd_lines[-2] <= macd_signals[-2]:
            signals.append({"type": "BUY", "source": "MACD", "reason": "MACD Crossover (bullish)", "strength": "stark"})
            score += 20
        elif macd_lines[-1] < macd_signals[-1] and macd_lines[-2] >= macd_signals[-2]:
            signals.append({"type": "SELL", "source": "MACD", "reason": "MACD Crossover (bearish)", "strength": "stark"})
            score -= 20
        elif macd_lines[-1] > macd_signals[-1]:
            signals.append({"type": "BUY", "source": "MACD", "reason": "MACD ueber Signal-Linie", "strength": "schwach"})
            score += 5
        elif macd_lines[-1] < macd_signals[-1]:
            signals.append({"type": "SELL", "source": "MACD", "reason": "MACD unter Signal-Linie", "strength": "schwach"})
            score -= 5

    # Bollinger Bands
    if close_vals and bb_lower and bb_upper:
        price = close_vals[-1]
        if price <= bb_lower[-1]:
            signals.append({"type": "BUY", "source": "Bollinger", "reason": "Preis am unteren Bollinger Band", "strength": "mittel"})
            score += 15
        elif price >= bb_upper[-1]:
            signals.append({"type": "SELL", "source": "Bollinger", "reason": "Preis am oberen Bollinger Band", "strength": "mittel"})
            score -= 15

    # EMA Crossover (9/21)
    if len(ema9_vals) >= 2 and len(ema21_vals) >= 2:
        if ema9_vals[-1] > ema21_vals[-1] and ema9_vals[-2] <= ema21_vals[-2]:
            signals.append({"type": "BUY", "source": "EMA", "reason": "EMA 9/21 Golden Cross", "strength": "stark"})
            score += 20
        elif ema9_vals[-1] < ema21_vals[-1] and ema9_vals[-2] >= ema21_vals[-2]:
            signals.append({"type": "SELL", "source": "EMA", "reason": "EMA 9/21 Death Cross", "strength": "stark"})
            score -= 20

    # StockTwits Sentiment
    if "error" not in sentiment_data:
        sent = sentiment_data.get("sentiment", {})
        sent_score = sent.get("score", 50)
        if sent_score > 70:
            signals.append({"type": "BUY", "source": "Sentiment", "reason": f"StockTwits stark bullish ({sent_score}%)", "strength": "mittel"})
            score += 15
        elif sent_score > 55:
            signals.append({"type": "BUY", "source": "Sentiment", "reason": f"StockTwits leicht bullish ({sent_score}%)", "strength": "schwach"})
            score += 5
        elif sent_score < 30:
            signals.append({"type": "SELL", "source": "Sentiment", "reason": f"StockTwits stark bearish ({sent_score}%)", "strength": "mittel"})
            score -= 15
        elif sent_score < 45:
            signals.append({"type": "SELL", "source": "Sentiment", "reason": f"StockTwits leicht bearish ({sent_score}%)", "strength": "schwach"})
            score -= 5

    # Gesamtbewertung
    score = max(-100, min(100, score))
    if score >= 40:
        overall = "STRONG BUY"
    elif score >= 15:
        overall = "BUY"
    elif score > -15:
        overall = "NEUTRAL"
    elif score > -40:
        overall = "SELL"
    else:
        overall = "STRONG SELL"

    return {
        "ticker": ticker,
        "signals": signals,
        "score": score,
        "overall": overall,
        "timestamp": datetime.now().isoformat(),
    }


# ─── API Endpoints ──────────────────────────────────────────────────────

@app.route("/api/sentiment/<ticker>")
def api_sentiment(ticker):
    return jsonify(fetch_stocktwits(ticker.upper()))

@app.route("/api/price/<ticker>")
def api_price(ticker):
    period = request.args.get("period", "3mo")
    interval = request.args.get("interval", "1d")
    return jsonify(fetch_price_data(ticker.upper(), period, interval))

@app.route("/api/indicators/<ticker>")
def api_indicators(ticker):
    period = request.args.get("period", "3mo")
    interval = request.args.get("interval", "1d")
    return jsonify(calculate_indicators(ticker.upper(), period, interval))

@app.route("/api/signals/<ticker>")
def api_signals(ticker):
    return jsonify(generate_signals(ticker.upper()))

@app.route("/api/overview")
def api_overview():
    """Holt Uebersicht fuer mehrere Ticker gleichzeitig."""
    tickers_param = request.args.get("tickers", "AAPL,TSLA,NVDA,AMZN,BTC-USD,ETH-USD")
    tickers = [t.strip().upper() for t in tickers_param.split(",")]

    results = []
    for ticker in tickers:
        price = fetch_price_data(ticker, period="5d", interval="1d")
        sentiment = fetch_stocktwits(ticker.replace("-USD", ".X") if "-USD" in ticker else ticker)
        sigs = generate_signals(ticker)

        entry = {
            "ticker": ticker,
            "name": price.get("name", ticker),
            "price": price.get("current_price"),
            "prev_close": price.get("prev_close"),
            "sentiment": sentiment.get("sentiment", {}),
            "signal": sigs.get("overall", "N/A"),
            "signal_score": sigs.get("score", 0),
        }
        if entry["price"] and entry["prev_close"]:
            change = entry["price"] - entry["prev_close"]
            entry["change"] = round(change, 2)
            entry["change_pct"] = round(change / entry["prev_close"] * 100, 2)
        results.append(entry)

    return jsonify(results)

@app.route("/api/watchlist", methods=["GET", "POST", "DELETE"])
def api_watchlist():
    """Einfache Watchlist (in-memory, wird bei Neustart zurueckgesetzt)."""
    if not hasattr(app, "watchlist"):
        app.watchlist = ["AAPL", "TSLA", "NVDA", "AMZN", "BTC-USD", "ETH-USD"]

    if request.method == "POST":
        ticker = request.json.get("ticker", "").upper()
        if ticker and ticker not in app.watchlist:
            app.watchlist.append(ticker)
        return jsonify(app.watchlist)

    if request.method == "DELETE":
        ticker = request.json.get("ticker", "").upper()
        if ticker in app.watchlist:
            app.watchlist.remove(ticker)
        return jsonify(app.watchlist)

    return jsonify(app.watchlist)


# ─── Start ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  DAYTRADING ANALYSE SERVER")
    print("  http://localhost:5000")
    print("  Dashboard: http://localhost:5000/dashboard")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=True)
