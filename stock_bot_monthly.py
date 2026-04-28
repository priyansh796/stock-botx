import yfinance as yf
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
import os
import gspread
from google.oauth2.service_account import Credentials
import requests
from datetime import datetime

MARKET_CAP_LIMIT = 5000 * 10**7
MONTHLY_HISTORY = "15y"
WEEKLY_HISTORY = "max"
PORTFOLIO_FILE = "portfolio.xlsx"
SPREADSHEET_NAME = "Stock Bot Dashboard"
TELEGRAM_TOKEN = "8630503074:AAHgONEVwJB_QVZ1GeKBaVGl9Z3Ct0E_yLw"
CHAT_ID = "8258280498"

# --- YOUR ORIGINAL FUNCTIONS (UNTOUCHED) ---
def super_smoother(price, period):
    a1 = np.exp(-1.414 * np.pi / period)
    b1 = 2 * a1 * np.cos(1.414 * np.pi / period)
    c2 = b1; c3 = -a1 * a1; c1 = 1 - c2 - c3
    filt = np.zeros(len(price))
    for i in range(2, len(price)):
        filt[i] = (c1 * (price[i] + price[i - 1]) / 2 + c2 * filt[i - 1] + c3 * filt[i - 2])
    return filt

def rolling_cross(close, ssf, lookback):
    cross_found = False
    for i in range(1, lookback):
        if close[-i - 1] < ssf[-i - 1] and close[-i] > ssf[-i]:
            cross_found = True; break
    return True if (cross_found and close[-1] > ssf[-1]) else False

def rolling_setup_monthly(df, lookback):
    for i in range(1, lookback):
        if (df['Close'].iloc[-i] < df['SSF_50'].iloc[-i] and df['Close'].iloc[-i] < df['SSF_200'].iloc[-i] and df['Close'].iloc[-i] < df['SSF_250'].iloc[-i]):
            return True
    return False

def rolling_setup_weekly(df, lookback):
    for i in range(1, lookback):
        if (df['Close'].iloc[-i] < df['SSF_50'].iloc[-i] and df['Close'].iloc[-i] < df['SSF_100'].iloc[-i] and df['Close'].iloc[-i] < df['SSF_250'].iloc[-i]):
            return True
    return False

# --- NEW QUANT STRATEGY FUNCTIONS ---
def get_hurst_exponent(prices):
    lags = range(2, 20)
    tau = [np.sqrt(np.std(np.subtract(prices[lag:], prices[:-lag]))) for lag in lags]
    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    return poly[0] * 2

def get_quant_decision(prices):
    if len(prices) < 40: return "HOLD"
    h_exp = get_hurst_exponent(prices)
    series = pd.Series(prices)
    z_score = (prices[-1] - series.rolling(30).mean().iloc[-1]) / series.rolling(30).std().iloc[-1]
    if h_exp < 0.48 and z_score < -2.2: return "BUY"
    elif z_score > 1.5 or (h_exp > 0.6 and z_score < -1.0): return "SELL"
    return "HOLD"

# --- UTILITIES ---
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": message})

creds = Credentials.from_service_account_file("credentials.json", scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
client = gspread.authorize(creds)
spreadsheet = client.open(SPREADSHEET_NAME)

def update_sheet(sheet_name, data):
    try: sheet = spreadsheet.worksheet(sheet_name)
    except: sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=5)
    sheet.clear()
    sheet.update([["Stock"]] + [[x] for x in data]) if len(data) > 0 else sheet.update([["No Stocks"]])

def update_timestamp():
    sheet = spreadsheet.sheet1
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sheet.update_acell("H1", "Last Bot Run"); sheet.update_acell("H2", now)

# --- MAIN LOOP ---
stocks_df = pd.read_csv("nse_stocks.csv")
symbols = stocks_df['SYMBOL'].dropna().tolist()
stocks = [symbol + ".NS" for symbol in symbols]

# ALL ORIGINAL LISTS RESTORED
weekly_buy_scored, monthly_buy_scored = [], []
weekly_sell_signals, sell_signals = [], []
fundamental_pass = []

# QUANT LISTS
quant_w_buys, quant_w_sells = [], []
quant_m_buys, quant_m_sells = [], []

for stock in stocks:
    print(f"Processing {stock} ...")
    try:
        ticker = yf.Ticker(stock)
        
        # WEEKLY BLOCK (ORIGINAL + QUANT)
        w_df = ticker.history(period=WEEKLY_HISTORY, interval="1wk").iloc[:-1]
        if len(w_df) >= 300:
            w_close = w_df['Close'].values
            w_df['SSF_20'] = super_smoother(w_close, 20)
            w_df['SSF_50'] = super_smoother(w_close, 50)
            w_df['SSF_100'] = super_smoother(w_close, 100)
            w_df['SSF_200'] = super_smoother(w_close, 200)
            w_df['SSF_250'] = super_smoother(w_close, 250)
            rsi_w = RSIIndicator(w_df['Close'], window=14).rsi()
            
            # Original Weekly Logic
            if (rolling_setup_weekly(w_df, 20) and rolling_cross(w_close, w_df['SSF_50'].values, 6) and 
                rsi_w.iloc[-1] > rsi_w.rolling(14).mean().iloc[-1] and w_df['SSF_50'].iloc[-1] < w_df['SSF_200'].iloc[-1]):
                score = rsi_w.iloc[-1] + ((w_df['Close'].iloc[-1] - w_df['SSF_50'].iloc[-1]) / w_df['SSF_50'].iloc[-1]) * 100
                weekly_buy_scored.append((stock, score, w_df['SSF_50'].iloc[-1]))
            
            # Original Weekly Sell
            if w_df['Close'].iloc[-2] > w_df['SSF_20'].iloc[-2] and w_df['Close'].iloc[-1] < w_df['SSF_20'].iloc[-1]:
                weekly_sell_signals.append(stock)

            # Quant Weekly
            q_w_sig = get_quant_decision(w_close[-100:])
            if q_w_sig == "BUY": quant_w_buys.append(stock)
            elif q_w_sig == "SELL": quant_w_sells.append(stock)

        # MONTHLY BLOCK (ORIGINAL + QUANT)
        m_df = ticker.history(period=MONTHLY_HISTORY, interval="1mo").iloc[:-1]
        if len(m_df) >= 80:
            m_close = m_df['Close'].values
            m_df['SSF_20'] = super_smoother(m_close, 20)
            m_df['SSF_50'] = super_smoother(m_close, 50)
            m_df['SSF_200'] = super_smoother(m_close, 200)
            m_df['SSF_250'] = super_smoother(m_close, 250)
            rsi_m = RSIIndicator(m_df['Close'], window=14).rsi()

            # Original Monthly Logic
            if (rolling_setup_monthly(m_df, 12) and rolling_cross(m_close, m_df['SSF_50'].values, 3) and 
                rsi_m.iloc[-1] > rsi_m.rolling(14).mean().iloc[-1] and m_df['SSF_50'].iloc[-1] < m_df['SSF_200'].iloc[-1]):
                score_m = rsi_m.iloc[-1] + ((m_df['Close'].iloc[-1] - m_df['SSF_50'].iloc[-1]) / m_df['SSF_50'].iloc[-1]) * 100
                monthly_buy_scored.append((stock, score_m, m_df['SSF_50'].iloc[-1]))
            
            # Original Monthly Sell
            if m_df['Close'].iloc[-2] > m_df['SSF_20'].iloc[-2] and m_df['Close'].iloc[-1] < m_df['SSF_20'].iloc[-1]:
                sell_signals.append(stock)

            # Quant Monthly
            q_m_sig = get_quant_decision(m_close[-60:])
            if q_m_sig == "BUY": quant_m_buys.append(stock)
            elif q_m_sig == "SELL": quant_m_sells.append(stock)

        # Fundamentals
        info = ticker.info
        if info.get("marketCap", 0) > MARKET_CAP_LIMIT and info.get("profitMargins", 0) > 0:
            fundamental_pass.append(stock)

    except Exception as e:
        print(f"Error {stock}: {e}"); continue

# --- DATA SORTING ---
weekly_buy_scored = sorted(weekly_buy_scored, key=lambda x: x[1], reverse=True)
top_weekly = weekly_buy_scored[:5]; rest_weekly = weekly_buy_scored[5:]

monthly_buy_scored = sorted(monthly_buy_scored, key=lambda x: x[1], reverse=True)
top_monthly = monthly_buy_scored[:5]; rest_monthly = monthly_buy_scored[5:]

# --- FINAL OUTPUTS ---
with pd.ExcelWriter(PORTFOLIO_FILE, engine="openpyxl", mode="w") as writer:
    pd.DataFrame(fundamental_pass, columns=["Stock"]).to_excel(writer, sheet_name="Fundamentals", index=False)
    pd.DataFrame(top_weekly, columns=["Stock","Score","StopLoss"]).to_excel(writer, sheet_name="Top_Weekly", index=False)
    pd.DataFrame(rest_weekly, columns=["Stock","Score","StopLoss"]).to_excel(writer, sheet_name="Rest_Weekly", index=False)
    pd.DataFrame(top_monthly, columns=["Stock","Score","StopLoss"]).to_excel(writer, sheet_name="Top_Monthly", index=False)
    pd.DataFrame(rest_monthly, columns=["Stock","Score","StopLoss"]).to_excel(writer, sheet_name="Rest_Monthly", index=False)
    pd.DataFrame(weekly_sell_signals, columns=["Stock"]).to_excel(writer, sheet_name="Weekly_Sell", index=False)
    pd.DataFrame(sell_signals, columns=["Stock"]).to_excel(writer, sheet_name="Sell_Signals", index=False)
    pd.DataFrame(quant_w_buys, columns=["Stock"]).to_excel(writer, sheet_name="Quant_Weekly_Buy", index=False)
    pd.DataFrame(quant_m_buys, columns=["Stock"]).to_excel(writer, sheet_name="Quant_Monthly_Buy", index=False)

update_sheet("Fundamentals", fundamental_pass)
update_sheet("Top_Weekly", [x[0] for x in top_weekly])
update_sheet("Rest_Weekly", [x[0] for x in rest_weekly])
update_sheet("Top_Monthly", [x[0] for x in top_monthly])
update_sheet("Rest_Monthly", [x[0] for x in rest_monthly])
update_sheet("Weekly_Sell", weekly_sell_signals)
update_sheet("Sell_Signals", sell_signals)
update_sheet("Quant_Weekly_Buy", quant_w_buys)
update_sheet("Quant_Monthly_Buy", quant_m_buys)
update_timestamp()

message = f"""
Stock Bot Run Completed

Top Weekly Buy: {[x[0] for x in top_weekly]}
Rest Weekly Buy: {[x[0] for x in rest_weekly]}
Top Monthly Buy: {[x[0] for x in top_monthly]}
Rest Monthly Buy: {[x[0] for x in rest_monthly]}
Weekly Sell: {weekly_sell_signals}
Sell Signals: {sell_signals}

[QUANT STRATEGY]
Quant Weekly Buys: {quant_w_buys}
Quant Monthly Buys: {quant_m_buys}
"""
send_telegram_message(message)










