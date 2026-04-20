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


stocks_df = pd.read_csv("nse_stocks.csv")
symbols = stocks_df['SYMBOL'].dropna().tolist()
stocks = [symbol + ".NS" for symbol in symbols]

weekly_buy_scored = []
monthly_buy_scored = []
weekly_sell_signals = []
sell_signals = []
fundamental_pass = []


for stock in stocks:

    print(f"Processing {stock} ...")

    try:

        ticker = yf.Ticker(stock)
        info = ticker.info

        market_cap = info.get("marketCap")
        if market_cap is None or market_cap < MARKET_CAP_LIMIT:
            continue

        profit_margin = info.get("profitMargins")
        if profit_margin is not None and profit_margin <= 0:
            continue

        fundamental_pass.append(stock)

        # ---------- MONTHLY ----------
        monthly_df = ticker.history(period=MONTHLY_HISTORY, interval="1mo")

        if len(monthly_df) < 80:
            continue

        m_close = monthly_df['Close'].values

        monthly_df['SSF_50'] = super_smoother(m_close, 50)
        monthly_df['SSF_100'] = super_smoother(m_close, 100)
        monthly_df['SSF_150'] = super_smoother(m_close, 150)
        monthly_df['SSF_200'] = super_smoother(m_close, 200)
        monthly_df['SSF_250'] = super_smoother(m_close, 250)

        rsi_m = RSIIndicator(monthly_df['Close'], window=14)
        monthly_df['RSI'] = rsi_m.rsi()
        monthly_df['RSI_MA'] = monthly_df['RSI'].rolling(14).mean()

        rsi_m_latest = monthly_df.iloc[-1]
        m_latest = monthly_df.iloc[-1]

        monthly_cross = rolling_cross(m_close, monthly_df['SSF_50'].values, 3)
        monthly_setup = rolling_setup_monthly(monthly_df, 12)

        if (
            monthly_setup
            and monthly_cross
            and rsi_m_latest['RSI'] > rsi_m_latest['RSI_MA']
            and m_latest['SSF_50'] < m_latest['SSF_200']
            and m_latest['SSF_50'] < m_latest['SSF_250']
        ):
            score = rsi_m_latest['RSI'] + ((m_latest['Close'] - m_latest['SSF_50']) / m_latest['SSF_50']) * 100
            monthly_buy_scored.append((stock, score))

        # ---------- WEEKLY ----------
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

        if (
            weekly_setup
            and weekly_cross
            and rsi_w_latest['RSI'] > rsi_w_latest['RSI_MA']
            and w_latest['SSF_50'] < w_latest['SSF_200']
            and w_latest['SSF_50'] < w_latest['SSF_250']
        ):
            score = rsi_w_latest['RSI'] + ((w_latest['Close'] - w_latest['SSF_50']) / w_latest['SSF_50']) * 100
            weekly_buy_scored.append((stock, score))

        # ---------- WEEKLY SELL ----------
        close = weekly_df['Close']
        ssf20 = weekly_df['SSF_20']

        recent_above = any(close.iloc[-i] > ssf20.iloc[-i] for i in range(6, 10))

        recent_cross = any(
            close.iloc[-i - 1] > ssf20.iloc[-i - 1] and close.iloc[-i] < ssf20.iloc[-i]
            for i in range(1, 4)
        )

        downtrend = close.iloc[-1] < ssf20.iloc[-1] and close.iloc[-1] < close.iloc[-2]

        if recent_above and recent_cross and downtrend:
            weekly_sell_signals.append(stock)

        # ---------- MONTHLY SELL ----------
        rsi_prev = monthly_df.iloc[-2]
        if rsi_prev['RSI'] > rsi_prev['RSI_MA'] and rsi_m_latest['RSI'] < rsi_m_latest['RSI_MA']:
            sell_signals.append(stock)

    except Exception as e:
        print("Error:", stock, e)
        continue


weekly_buy_scored = sorted(weekly_buy_scored, key=lambda x: x[1], reverse=True)
monthly_buy_scored = sorted(monthly_buy_scored, key=lambda x: x[1], reverse=True)

top_weekly = weekly_buy_scored[:5]
rest_weekly = weekly_buy_scored[5:]

top_monthly = monthly_buy_scored[:5]
rest_monthly = monthly_buy_scored[5:]


with pd.ExcelWriter(PORTFOLIO_FILE, engine="openpyxl", mode="w") as writer:

    pd.DataFrame(fundamental_pass, columns=["Stock"]).to_excel(writer, sheet_name="Fundamentals", index=False)

    pd.DataFrame(top_weekly, columns=["Stock","Score"]).to_excel(writer, sheet_name="Top_Weekly", index=False)
    pd.DataFrame(rest_weekly, columns=["Stock","Score"]).to_excel(writer, sheet_name="Rest_Weekly", index=False)

    pd.DataFrame(top_monthly, columns=["Stock","Score"]).to_excel(writer, sheet_name="Top_Monthly", index=False)
    pd.DataFrame(rest_monthly, columns=["Stock","Score"]).to_excel(writer, sheet_name="Rest_Monthly", index=False)

    pd.DataFrame(weekly_sell_signals, columns=["Stock"]).to_excel(writer, sheet_name="Weekly_Sell", index=False)
    pd.DataFrame(sell_signals, columns=["Stock"]).to_excel(writer, sheet_name="Sell_Signals", index=False)


update_sheet("Fundamentals", fundamental_pass)
update_sheet("Top_Weekly", [x[0] for x in top_weekly])
update_sheet("Rest_Weekly", [x[0] for x in rest_weekly])
update_sheet("Top_Monthly", [x[0] for x in top_monthly])
update_sheet("Rest_Monthly", [x[0] for x in rest_monthly])
update_sheet("Weekly_Sell", weekly_sell_signals)
update_sheet("Sell_Signals", sell_signals)

update_timestamp()


message = f"""
Stock Bot Run Completed

Top Weekly Buy:
{[x[0] for x in top_weekly]}

Rest Weekly Buy:
{[x[0] for x in rest_weekly]}

Top Monthly Buy:
{[x[0] for x in top_monthly]}

Rest Monthly Buy:
{[x[0] for x in rest_monthly]}

Weekly Sell:
{weekly_sell_signals}

Sell Signals:
{sell_signals}
"""

send_telegram_message(message)

print("Telegram notification sent.")












