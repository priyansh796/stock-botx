import yfinance as yf
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator

# ===== SELECT STOCKS TO TEST =====
stocks = ["INGERRAND.NS", "WABAG.NS"]  # change here

def debug_predictive(stock):
    print("\n" + "="*60)
    print(f"🔍 DEBUGGING: {stock}")
    print("="*60)

    try:
        ticker = yf.Ticker(stock)
        df = ticker.history(period="1y", interval="1wk")

        if len(df) < 30:
            print("❌ Not enough data")
            return

        close = df['Close']
        volume = df['Volume']

        # ===== VOLATILITY COMPRESSION =====
        std = close.rolling(20).std()
        vol_now = std.iloc[-1]
        vol_prev = std.iloc[-5:-1].mean()

        print("\n📉 VOLATILITY:")
        print(f"Current Vol: {vol_now:.4f}")
        print(f"Previous Avg Vol: {vol_prev:.4f}")

        if vol_now < vol_prev:
            vol_pts = 30
            print("✅ Compression detected (+30)")
        else:
            vol_pts = -15
            print("❌ No compression (-15)")

        # ===== MOMENTUM =====
        roc_4 = close.pct_change(4)
        roc_8 = close.pct_change(8)
        momentum = roc_4.iloc[-1] - roc_8.iloc[-1]

        print("\n⚡ MOMENTUM:")
        print(f"Momentum value: {momentum:.4f}")

        if momentum > 0:
            mom_pts = 25
            print("✅ Positive acceleration (+25)")
        else:
            mom_pts = -25
            print("❌ Negative acceleration (-25)")

        # ===== VOLUME (INSTITUTIONAL PROXY) =====
        vol_avg = volume.rolling(20).mean().iloc[-1]
        vol_now_actual = volume.iloc[-1]
        price_change = close.pct_change().iloc[-1]

        print("\n🏦 VOLUME (Institutional Flow):")
        print(f"Current Volume: {vol_now_actual:.0f}")
        print(f"Avg Volume: {vol_avg:.0f}")
        print(f"Price Change: {price_change:.4f}")

        if vol_now_actual > 1.5 * vol_avg and price_change > 0:
            flow_pts = 25
            print("✅ Accumulation detected (+25)")
        elif vol_now_actual > 1.5 * vol_avg and price_change < 0:
            flow_pts = -25
            print("❌ Distribution detected (-25)")
        else:
            flow_pts = 0
            print("⚠️ No strong institutional signal (0)")

        # ===== PRICE POSITION =====
        high_20 = close.rolling(20).max().iloc[-1]
        low_20 = close.rolling(20).min().iloc[-1]
        price = close.iloc[-1]

        print("\n📊 PRICE POSITION:")
        print(f"Price: {price:.2f}")
        print(f"20W High: {high_20:.2f}")
        print(f"20W Low: {low_20:.2f}")

        if price > 0.9 * high_20:
            pos_pts = 20
            print("✅ Near breakout zone (+20)")
        elif price < 1.1 * low_20:
            pos_pts = -20
            print("❌ Near breakdown zone (-20)")
        else:
            pos_pts = 0
            print("⚠️ Neutral zone (0)")

        # ===== FINAL SCORE =====
        total_score = vol_pts + mom_pts + flow_pts + pos_pts

        print("\n🎯 FINAL SCORE:")
        print(f"Score = {total_score}")

        if total_score >= 50:
            print("🚀 PREDICT_UP")
        elif total_score <= -50:
            print("🔻 PREDICT_DOWN")
        else:
            print("⏸ HOLD")

    except Exception as e:
        print("❌ Error:", e)


# ===== RUN DEBUG =====
for stock in stocks:
    debug_predictive(stock)




