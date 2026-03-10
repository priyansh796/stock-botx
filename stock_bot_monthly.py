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

    requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        }
    )


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

weekly_buy = []
monthly_buy = []
sell_signals = []
ssf_special = []
weekly_ssf_special = []
fundamental_pass = []


for stock in stocks:

    print(f"Processing {stock} ...")

    try:

        ticker = yf.Ticker(stock)
        info = ticker.info

        market_cap = info.get("marketCap")
        if market_cap is None or market_cap < MARKET_CAP_LIMIT:
            continue

        eps = info.get("trailingEps")
        if eps is None or eps <= 0:
            continue

        dividend_yield = info.get("dividendYield")
        if dividend_yield is not None and dividend_yield <= 0:
            continue

        roe = info.get("returnOnEquity")
        if roe is not None and roe < 0.10:
            continue

        debt_equity = info.get("debtToEquity")
        if debt_equity is not None and debt_equity > 3:
            continue

        current_ratio = info.get("currentRatio")
        if current_ratio is not None and current_ratio < 1.5:
            continue

        peg_ratio = info.get("pegRatio")
        if peg_ratio is not None and peg_ratio < 1:
            continue

        operating_cashflow = info.get("operatingCashflow")
        if operating_cashflow is not None and operating_cashflow <= 0:
            continue

        free_cashflow = info.get("freeCashflow")
        if free_cashflow is not None and free_cashflow <= 0:
            continue

        profit_margin = info.get("profitMargins")
        if profit_margin is not None and profit_margin <= 0:
            continue

        fundamental_pass.append(stock)

        monthly_df = ticker.history(period=MONTHLY_HISTORY, interval="1mo")

        if len(monthly_df) < 80:
            continue

        m_close = monthly_df['Close'].values

        monthly_df['SSF_50'] = super_smoother(m_close, 50)
        monthly_df['SSF_100'] = super_smoother(m_close, 100)
        monthly_df['SSF_150'] = super_smoother(m_close, 150)
        monthly_df['SSF_200'] = super_smoother(m_close, 200)
        monthly_df['SSF_250'] = super_smoother(m_close, 250)

        ssf50 = monthly_df['SSF_50'].values

        rsi_m = RSIIndicator(monthly_df['Close'], window=14)
        monthly_df['RSI'] = rsi_m.rsi()
        monthly_df['RSI_MA'] = monthly_df['RSI'].rolling(14).mean()

        rsi_m_latest = monthly_df.iloc[-1]

        monthly_cross = rolling_cross(m_close, ssf50, lookback=3)
        monthly_setup = rolling_setup_monthly(monthly_df, lookback=12)

        if monthly_setup and monthly_cross and rsi_m_latest['RSI'] > rsi_m_latest['RSI_MA']:
            monthly_buy.append(stock)

        m_latest = monthly_df.iloc[-1]

        ssf_structure_ok = (
            m_latest['Close'] > m_latest['SSF_200']
            and m_latest['Close'] > m_latest['SSF_150']
        )

        if monthly_cross and ssf_structure_ok and rsi_m_latest['RSI'] > rsi_m_latest['RSI_MA']:
            ssf_special.append(stock)

        weekly_df = ticker.history(period=WEEKLY_HISTORY, interval="1wk")

        if len(weekly_df) < 30:
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

        weekly_cross = rolling_cross(w_close, w_ssf50, lookback=6)
        weekly_setup = rolling_setup_weekly(weekly_df, lookback=20)

        if weekly_setup and weekly_cross and rsi_w_latest['RSI'] > rsi_w_latest['RSI_MA']:
            weekly_buy.append(stock)

        w_latest = weekly_df.iloc[-1]

        weekly_structure_ok = w_latest['Close'] > w_latest['SSF_200']

        if weekly_cross and weekly_structure_ok and rsi_w_latest['RSI'] > rsi_w_latest['RSI_MA']:
            weekly_ssf_special.append(stock)

        rsi_prev = monthly_df.iloc[-2]
        rsi_latest = monthly_df.iloc[-1]

        if rsi_prev['RSI'] > rsi_prev['RSI_MA'] and rsi_latest['RSI'] < rsi_latest['RSI_MA']:
            sell_signals.append(stock)

    except Exception as e:

        print("Error:", stock, e)
        continue


print("\n===== FUNDAMENTALS PASSED =====")
print(fundamental_pass)

print("\n===== WEEKLY BUY =====")
print(weekly_buy)

print("\n===== MONTHLY BUY =====")
print(monthly_buy)

print("\n===== SSF SPECIAL (MONTHLY) =====")
print(ssf_special)

print("\n===== SSF SPECIAL (WEEKLY) =====")
print(weekly_ssf_special)

print("\n===== SELL SIGNALS =====")
print(sell_signals)


with pd.ExcelWriter(PORTFOLIO_FILE, engine="openpyxl", mode="w") as writer:

    pd.DataFrame(fundamental_pass, columns=["Stock"]).to_excel(writer, sheet_name="Fundamentals", index=False)
    pd.DataFrame(weekly_buy, columns=["Stock"]).to_excel(writer, sheet_name="Weekly_Buy", index=False)
    pd.DataFrame(monthly_buy, columns=["Stock"]).to_excel(writer, sheet_name="Monthly_Buy", index=False)
    pd.DataFrame(ssf_special, columns=["Stock"]).to_excel(writer, sheet_name="SSF_Special_M", index=False)
    pd.DataFrame(weekly_ssf_special, columns=["Stock"]).to_excel(writer, sheet_name="SSF_Special_W", index=False)
    pd.DataFrame(sell_signals, columns=["Stock"]).to_excel(writer, sheet_name="Sell_Signals", index=False)


update_sheet("Fundamentals", fundamental_pass)
update_sheet("Weekly_Buy", weekly_buy)
update_sheet("Monthly_Buy", monthly_buy)
update_sheet("SSF_Special_M", ssf_special)
update_sheet("SSF_Special_W", weekly_ssf_special)
update_sheet("Sell_Signals", sell_signals)

update_timestamp()

print("\nExcel & Google Sheets updated successfully.")


message = f"""
Stock Bot Run Completed

Fundamentals Passed: {len(fundamental_pass)}
{', '.join(fundamental_pass)}

Weekly Buy: {len(weekly_buy)}
{', '.join(weekly_buy)}

Monthly Buy: {len(monthly_buy)}
{', '.join(monthly_buy)}

SSF Monthly: {len(ssf_special)}
{', '.join(ssf_special)}

SSF Weekly: {len(weekly_ssf_special)}
{', '.join(weekly_ssf_special)}

Sell Signals: {len(sell_signals)}
{', '.join(sell_signals)}

Google Sheet Updated Successfully
"""

send_telegram_message(message)

print("Telegram notification sent.")












