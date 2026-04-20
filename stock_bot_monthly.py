import yfinance as yf
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
import os
import gspread
from google.oauth2.service_account import Credentials
import requests
from datetime import datetime

MARKET_CAP_LIMIT = 1000 * 10**7
MONTHLY_HISTORY = "15y"
WEEKLY_HISTORY = "10y"   # ✅ FIXED (was 5y)
PORTFOLIO_FILE = "portfolio.xlsx"

SPREADSHEET_NAME = "Stock Bot Dashboard"

TELEGRAM_TOKEN = "8630503074:AAHgONEVwJB_QVZ1GeKBaVGl9Z3Ct0E_yLw"
CHAT_ID = "8258280498"


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


def rolling_cross(close, ssf, lookback):

    cross_found = False

    for i in range(1, lookback):

        if close[-i - 1] < ssf[-i - 1] and close[-i] > ssf[-i]:
            cross_found = True
            break

    if cross_found and close[-1] > ssf[-1]:
        return True

    return False


def rolling_setup_weekly(df, lookback):

    for i in range(1, lookback):

        if (
            df['Close'].iloc[-i] < df['SSF_50'].iloc[-i]
            and df['Close'].iloc[-i] < df['SSF_100'].iloc[-i]
            and df['Close'].iloc[-i] < df['SSF_250'].iloc[-i]
        ):
            return True

    return False


def send_telegram_message(message):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        }
    )


stocks_df = pd.read_csv("nse_stocks.csv")
symbols = stocks_df['SYMBOL'].dropna().tolist()
stocks = [symbol + ".NS" for symbol in symbols]

weekly_buy = []

# DEBUG COUNTERS
total = 0
selected = 0
weekly_cross_fail = 0
weekly_setup_fail = 0
rsi_fail = 0
structure_fail = 0
nan_fail = 0


for stock in stocks:

    total += 1
    print(f"\nProcessing {stock} ...")

    try:

        ticker = yf.Ticker(stock)

        weekly_df = ticker.history(period=WEEKLY_HISTORY, interval="1wk")

        if len(weekly_df) < 60:
            continue

        w_close = weekly_df['Close'].values

        weekly_df['SSF_50'] = super_smoother(w_close, 50)
        weekly_df['SSF_100'] = super_smoother(w_close, 100)
        weekly_df['SSF_200'] = super_smoother(w_close, 200)
        weekly_df['SSF_250'] = super_smoother(w_close, 250)

        w_ssf50 = weekly_df['SSF_50'].values

        rsi_w = RSIIndicator(weekly_df['Close'], window=14)
        weekly_df['RSI'] = rsi_w.rsi()
        weekly_df['RSI_MA'] = weekly_df['RSI'].rolling(14).mean()

        rsi_w_latest = weekly_df.iloc[-1]
        w_latest = weekly_df.iloc[-1]

        # ✅ FIX: Skip NaN SSF values
        if (
            np.isnan(w_latest['SSF_50'])
            or np.isnan(w_latest['SSF_200'])
            or np.isnan(w_latest['SSF_250'])
        ):
            nan_fail += 1
            print("❌ SSF NAN ISSUE")
            continue

        weekly_cross = rolling_cross(w_close, w_ssf50, lookback=6)
        weekly_setup = rolling_setup_weekly(weekly_df, lookback=20)

        print("DEBUG DATA:")
        print("Weekly Cross:", weekly_cross)
        print("Weekly Setup:", weekly_setup)
        print("RSI:", rsi_w_latest['RSI'], "MA:", rsi_w_latest['RSI_MA'])
        print("SSF50:", w_latest['SSF_50'])
        print("SSF200:", w_latest['SSF_200'])
        print("SSF250:", w_latest['SSF_250'])

        if not weekly_cross:
            weekly_cross_fail += 1
            continue

        if not weekly_setup:
            weekly_setup_fail += 1
            continue

        if not (rsi_w_latest['RSI'] > rsi_w_latest['RSI_MA']):
            rsi_fail += 1
            continue

        if not (
            w_latest['SSF_50'] < w_latest['SSF_200']
            and w_latest['SSF_50'] < w_latest['SSF_250']
        ):
            structure_fail += 1
            continue

        weekly_buy.append(stock)
        selected += 1
        print("✅ SELECTED")

    except Exception as e:
        print("Error:", stock, e)
        continue


# DEBUG SUMMARY
summary = f"""
DEBUG SUMMARY

Total: {total}
Selected: {selected}

Weekly Cross Fail: {weekly_cross_fail}
Weekly Setup Fail: {weekly_setup_fail}
RSI Fail: {rsi_fail}
Structure Fail: {structure_fail}
SSF NaN Fail: {nan_fail}

Final Weekly Buy:
{', '.join(weekly_buy)}
"""

print(summary)

send_telegram_message(summary)

print("Debug Telegram sent.")












