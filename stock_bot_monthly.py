import yfinance as yf
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator


def super_smoother(price, period):

    a1 = np.exp(-1.414 * np.pi / period)
    b1 = 2 * a1 * np.cos(1.414 * np.pi / period)
    c2 = b1
    c3 = -a1 * a1
    c1 = 1 - c2 - c3

    filt = np.zeros(len(price))

    for i in range(2, len(price)):
        filt[i] = (
            c1 * (price[i] + price[i - 1]) / 2
            + c2 * filt[i - 1]
            + c3 * filt[i - 2]
        )

    return filt


def debug_stock(stock):

    print("\n" + "="*60)
    print(f"🔍 DEBUGGING: {stock}")
    print("="*60)

    ticker = yf.Ticker(stock)

    # ✅ FIX APPLIED HERE
    df = ticker.history(period="max", interval="1wk")

    print(f"Data Length: {len(df)}")

    if len(df) < 300:
        print("❌ STILL NOT ENOUGH DATA (unexpected)")
        return

    close = df['Close'].values

    df['SSF_50'] = super_smoother(close, 50)
    df['SSF_200'] = super_smoother(close, 200)
    df['SSF_250'] = super_smoother(close, 250)

    # RSI
    rsi = RSIIndicator(df['Close'], window=14)
    df['RSI'] = rsi.rsi()
    df['RSI_MA'] = df['RSI'].rolling(14).mean()

    print("\n📊 LAST 10 VALUES:")
    print(df[['Close', 'SSF_50', 'SSF_200', 'SSF_250']].tail(10))

    # ---------------- CROSS CHECK ----------------
    print("\n🔁 CROSS CHECK (Last 6 weeks):")

    cross = False
    for i in range(1, 6):

        prev_close = close[-i - 1]
        curr_close = close[-i]

        prev_ssf = df['SSF_50'].iloc[-i - 1]
        curr_ssf = df['SSF_50'].iloc[-i]

        print(f"Week {-i}: PrevC={prev_close:.2f} PrevSSF={prev_ssf:.2f} | CurrC={curr_close:.2f} CurrSSF={curr_ssf:.2f}")

        if prev_close < prev_ssf and curr_close > curr_ssf:
            cross = True
            print("✅ CROSS DETECTED")

    print("Final Cross:", cross)

    # ---------------- SETUP CHECK ----------------
    print("\n📉 SETUP CHECK (Last 20 weeks):")

    setup = False

    for i in range(1, 20):

        c = df['Close'].iloc[-i]
        s50 = df['SSF_50'].iloc[-i]
        s200 = df['SSF_200'].iloc[-i]
        s250 = df['SSF_250'].iloc[-i]

        if c < s50 and c < s200 and c < s250:
            setup = True
            print(f"✅ Setup found at week {-i}")
            break

    print("Final Setup:", setup)

    # ---------------- RSI CHECK ----------------
    latest = df.iloc[-1]

    print("\n📈 RSI CHECK:")
    print(f"RSI: {latest['RSI']:.2f} | RSI_MA: {latest['RSI_MA']:.2f}")

    rsi_ok = latest['RSI'] > latest['RSI_MA']
    print("RSI Condition:", rsi_ok)

    # ---------------- STRUCTURE CHECK ----------------
    print("\n🏗️ STRUCTURE CHECK:")

    s50 = latest['SSF_50']
    s200 = latest['SSF_200']
    s250 = latest['SSF_250']

    print(f"SSF50: {s50:.2f} | SSF200: {s200:.2f} | SSF250: {s250:.2f}")

    structure_ok = (s50 < s200) and (s50 < s250)
    print("Structure Condition:", structure_ok)

    # ---------------- FINAL DECISION ----------------
    print("\n🎯 FINAL DECISION:")

    if cross and setup and rsi_ok and structure_ok:
        print("✅ SHOULD APPEAR IN WEEKLY BUY")
    else:
        print("❌ NOT IN WEEKLY BUY")
        print("Reasons:")
        if not cross:
            print("- Cross Failed")
        if not setup:
            print("- Setup Failed")
        if not rsi_ok:
            print("- RSI Failed")
        if not structure_ok:
            print("- Structure Failed")


# ---------------- RUN DEBUG ----------------

debug_stock("INGERRAND.NS")
debug_stock("WABAG.NS")











