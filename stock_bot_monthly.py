import yfinance as yf
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
import os
import gspread
from google.oauth2.service_account import Credentials
import requests
from datetime import datetime

MARKET_CAP_LIMIT = 10000 * 10**7
MONTHLY_HISTORY = "15y"
WEEKLY_HISTORY = "5y"
PORTFOLIO_FILE = "portfolio.xlsx"

SPREADSHEET_NAME = "Stock Bot Dashboard"

TELEGRAM_TOKEN = "8630503074:AAHgONEVwJB_QVZ1GeKBaVGl9Z3Ct0E_yLw"
CHAT_ID = "8258280498"

# 🔥 Add stocks here for deep debugging
DEBUG_STOCKS = ["INGERRAND.NS", "WABAG.NS"]

# ================= DEBUG COUNTERS =================
stats = {
    "total": 0,
    "monthly_cross_fail": 0,
    "weekly_cross_fail": 0,
    "monthly_setup_fail": 0,
    "weekly_setup_fail": 0,
    "rsi_monthly_fail": 0,
    "rsi_weekly_fail": 0,
    "structure_monthly_fail": 0,
    "structure_weekly_fail": 0,
    "fundamental_fail": 0,
    "selected": 0
}

# ================= FUNCTIONS =================

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


def rolling_setup_monthly(df, lookback):
    for i in range(1, lookback):
        if (
            df['Close'].iloc[-i] < df['SSF_50'].iloc[-i]
            and df['Close'].iloc[-i] < df['SSF_200'].iloc[-i]
            and df['Close'].iloc[-i] < df['SSF_250'].iloc[-i]
        ):
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
    requests.post(url, data={"chat_id": CHAT_ID, "text": message})


# ================= GOOGLE SHEETS =================

creds = Credentials.from_service_account_file(
    "credentials.json",
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

client = gspread.authorize(creds)
spreadsheet = client.open(SPREADSHEET_NAME)


def update_sheet(sheet_name, data):
    try:
        sheet = spreadsheet.worksheet(sheet_name)
    except:
        sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=5)

    sheet.clear()

    if len(data) == 0:
        sheet.update([["No Stocks"]])
    else:
        sheet.update([["Stock"]] + [[x] for x in data])


def update_timestamp():
    sheet = spreadsheet.sheet1
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sheet.update_acell("H1", "Last Bot Run")
    sheet.update_acell("H2", now)


# ================= LOAD STOCKS =================

stocks_df = pd.read_csv("nse_stocks.csv")
symbols = stocks_df['SYMBOL'].dropna().tolist()
stocks = [symbol + ".NS" for symbol in symbols]

weekly_buy_scored = []
monthly_buy_scored = []
weekly_sell_signals = []
sell_signals = []
fundamental_pass = []


# ================= MAIN LOOP =================

for stock in stocks:

    stats["total"] += 1
    debug = stock in DEBUG_STOCKS

    print(f"\nProcessing {stock} ...")

    try:
        ticker = yf.Ticker(stock)

        # ===== MONTHLY =====
        monthly_df = ticker.history(period=MONTHLY_HISTORY, interval="1mo")
        if len(monthly_df) < 80:
            continue

        m_close = monthly_df['Close'].values

        monthly_df['SSF_50'] = super_smoother(m_close, 50)
        monthly_df['SSF_200'] = super_smoother(m_close, 200)
        monthly_df['SSF_250'] = super_smoother(m_close, 250)

        rsi_m = RSIIndicator(monthly_df['Close'], window=14)
        monthly_df['RSI'] = rsi_m.rsi()
        monthly_df['RSI_MA'] = monthly_df['RSI'].rolling(14).mean()

        rsi_m_latest = monthly_df.iloc[-1]
        m_latest = monthly_df.iloc[-1]

        monthly_cross = rolling_cross(m_close, monthly_df['SSF_50'].values, 3)
        monthly_setup = rolling_setup_monthly(monthly_df, 12)

        # ===== WEEKLY =====
        weekly_df = ticker.history(period=WEEKLY_HISTORY, interval="1wk")
        if len(weekly_df) < 30:
            continue

        w_close = weekly_df['Close'].values

        weekly_df['SSF_20'] = super_smoother(w_close, 20)
        weekly_df['SSF_50'] = super_smoother(w_close, 50)
        weekly_df['SSF_100'] = super_smoother(w_close, 100)
        weekly_df['SSF_200'] = super_smoother(w_close, 200)
        weekly_df['SSF_250'] = super_smoother(w_close, 250)

        rsi_w = RSIIndicator(weekly_df['Close'], window=14)
        weekly_df['RSI'] = rsi_w.rsi()
        weekly_df['RSI_MA'] = weekly_df['RSI'].rolling(14).mean()

        rsi_w_latest = weekly_df.iloc[-1]
        w_latest = weekly_df.iloc[-1]

        weekly_cross = rolling_cross(w_close, weekly_df['SSF_50'].values, 6)
        weekly_setup = rolling_setup_weekly(weekly_df, 20)

        # ===== DEBUG PRINT =====
        if debug:
            print("DEBUG DATA:")
            print("Monthly Cross:", monthly_cross)
            print("Monthly Setup:", monthly_setup)
            print("Weekly Cross:", weekly_cross)
            print("Weekly Setup:", weekly_setup)
            print("RSI Weekly:", rsi_w_latest['RSI'], "MA:", rsi_w_latest['RSI_MA'])
            print("Structure:", w_latest['SSF_50'], w_latest['SSF_200'], w_latest['SSF_250'])

        # ===== FUNDAMENTALS =====
        info = ticker.info

        market_cap = info.get("marketCap")
        profit_margin = info.get("profitMargins")

        if market_cap is None or market_cap < MARKET_CAP_LIMIT:
            stats["fundamental_fail"] += 1
            continue

        if profit_margin is not None and profit_margin <= 0:
            stats["fundamental_fail"] += 1
            continue

        fundamental_pass.append(stock)

        # ===== WEEKLY BUY =====
        if not weekly_cross:
            stats["weekly_cross_fail"] += 1
        if not weekly_setup:
            stats["weekly_setup_fail"] += 1
        if not (rsi_w_latest['RSI'] > rsi_w_latest['RSI_MA']):
            stats["rsi_weekly_fail"] += 1
        if not (w_latest['SSF_50'] < w_latest['SSF_200'] and w_latest['SSF_50'] < w_latest['SSF_250']):
            stats["structure_weekly_fail"] += 1

        if (
            weekly_setup
            and weekly_cross
            and rsi_w_latest['RSI'] > rsi_w_latest['RSI_MA']
            and w_latest['SSF_50'] < w_latest['SSF_200']
            and w_latest['SSF_50'] < w_latest['SSF_250']
        ):
            score = rsi_w_latest['RSI']
            weekly_buy_scored.append((stock, score))
            stats["selected"] += 1

    except Exception as e:
        print("Error:", stock, e)
        continue


# ================= DEBUG SUMMARY =================

debug_message = f"""
DEBUG SUMMARY

Total: {stats['total']}
Selected: {stats['selected']}

Weekly Cross Fail: {stats['weekly_cross_fail']}
Weekly Setup Fail: {stats['weekly_setup_fail']}
RSI Weekly Fail: {stats['rsi_weekly_fail']}
Structure Weekly Fail: {stats['structure_weekly_fail']}
Fundamental Fail: {stats['fundamental_fail']}
"""

send_telegram_message(debug_message)

print(debug_message)












