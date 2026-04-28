import yfinance as yf
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import os
import gspread
from google.oauth2.service_account import Credentials
import requests
from datetime import datetime

# --- YOUR ORIGINAL SETTINGS ---
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
    c2 = b1
    c3 = -a1 * a1
    c1 = 1 - c2 - c3
    filt = np.zeros(len(price))
    for i in range(2, len(price)):
        filt[i] = (c1 * (price[i] + price[i - 1]) / 2 + c2 * filt[i - 1] + c3 * filt[i - 2])
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
        if (df['Close'].iloc[-i] < df['SSF_50'].iloc[-i] and 
            df['Close'].iloc[-i] < df['SSF_200'].iloc[-i] and 
            df['Close'].iloc[-i] < df['SSF_250'].iloc[-i]):
            return True
    return False

def rolling_setup_weekly(df, lookback):
    for i in range(1, lookback):
        if (df['Close'].iloc[-i] < df['SSF_50'].iloc[-i] and 
            df['Close'].iloc[-i] < df['SSF_100'].iloc[-i] and 
            df['Close'].iloc[-i] < df['SSF_250'].iloc[-i]):
            return True
    return False

# --- NEW PREDICTIVE QUANT ENGINE (COMPLETELY SEPARATE) ---
def get_predictive_signal(df):
    if len(df) < 30: return "HOLD"
    # Bollinger Squeeze detection
    bb = BollingerBands(df['Close'], window=20)
    bw = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()
    # Money Flow calculation (to predict before the price cross)
    mfv = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / (df['High'] - df['Low'])
    mfv = mfv.fillna(0) * df['Volume']
    cmf = mfv.rolling(20).sum() / df['Volume'].rolling(20).sum()
    if bw.iloc[-1] < bw.rolling(20).mean().iloc[-1] and cmf.iloc[-1] > 0.1:
        return "PREDICT_UP"
    elif cmf.iloc[-1] < -0.05 and df['Close'].iloc[-1] > df['Close'].rolling(20).mean().iloc[-1]:
        return "PREDICT_DOWN"
    return "HOLD"

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": message})
    except: pass

# --- ORIGINAL GOOGLE SHEETS SETUP ---
creds = Credentials.from_service_account_file("credentials.json", 
    scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
client = gspread.authorize(creds)
spreadsheet = client.open(SPREADSHEET_NAME)

def update_sheet(sheet_name, data):
    try: sheet = spreadsheet.worksheet(sheet_name)
    except: sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=5)
    sheet.clear()
    if len(data) == 0: sheet.update([["No Stocks"]])
    else: sheet.update([["Stock"]] + [[x] for x in data])

def update_timestamp():
    sheet = spreadsheet.sheet1
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sheet.update_acell("H1", "Last Bot Run"); sheet.update_acell("H2", now)

# --- STARTING DATA COLLECTION ---
stocks_df = pd.read_csv("nse_stocks.csv")
stocks = [s + ".NS" for s in stocks_df['SYMBOL'].dropna().tolist()]

# YOUR ORIGINAL OUTPUT STORAGE
weekly_buy_scored, monthly_buy_scored = [], []
weekly_sell_signals, sell_signals = [], []
fundamental_pass = []

# NEW QUANT OUTPUT STORAGE
predictive_up, predictive_down = [], []

for stock in stocks:
    print(f"Processing {stock} ...")
    try:
        ticker = yf.Ticker(stock)
        
        # WEEKLY ANALYSIS
        w_df = ticker.history(period=WEEKLY_HISTORY, interval="1wk").iloc[:-1]
        if len(w_df) >= 300:
            w_close = w_df['Close'].values
            w_df['SSF_20'] = super_smoother(w_close, 20)
            w_df['SSF_50'] = super_smoother(w_close, 50)
            w_df['SSF_100'] = super_smoother(w_close, 100)
            w_df['SSF_250'] = super_smoother(w_close, 250)
            
            # Predictive Logic (Before the cross)
            p_val = get_predictive_signal(w_df)
            if p_val == "PREDICT_UP": predictive_up.append(stock)
            elif p_val == "PREDICT_DOWN": predictive_down.append(stock)

            # --- YOUR ORIGINAL WEEKLY LOGIC (UNTOUCHED) ---
            rsi_w = RSIIndicator(w_df['Close'], window=14).rsi()
            if (rolling_setup_weekly(w_df, 20) and rolling_cross(w_close, w_df['SSF_50'].values, 6)):
                info = ticker.info
                if info.get("marketCap", 0) >= MARKET_CAP_LIMIT and info.get("profitMargins", 0) > 0:
                    fundamental_pass.append(stock)
                    score = rsi_w.iloc[-1] + ((w_close[-1] - w_df['SSF_50'].iloc[-1]) / w_df['SSF_50'].iloc[-1]) * 100
                    weekly_buy_scored.append((stock, score, w_df['SSF_50'].iloc[-1]))
            
            if w_close[-2] > w_df['SSF_20'].iloc[-2] and w_close[-1] < w_df['SSF_20'].iloc[-1]:
                weekly_sell_signals.append(stock)

        # MONTHLY ANALYSIS
        m_df = ticker.history(period=MONTHLY_HISTORY, interval="1mo").iloc[:-1]
        if len(m_df) >= 80:
            m_close = m_df['Close'].values
            m_df['SSF_50'] = super_smoother(m_close, 50)
            m_df['SSF_20'] = super_smoother(m_close, 20)
            
            # --- YOUR ORIGINAL MONTHLY LOGIC (UNTOUCHED) ---
            if rolling_setup_monthly(m_df, 12) and rolling_cross(m_close, m_df['SSF_50'].values, 3):
                score_m = 50 + ((m_close[-1] - m_df['SSF_50'].iloc[-1]) / m_df['SSF_50'].iloc[-1]) * 100
                monthly_buy_scored.append((stock, score_m, m_df['SSF_50'].iloc[-1]))
            
            if m_close[-2] > m_df['SSF_20'].iloc[-2] and m_close[-1] < m_df['SSF_20'].iloc[-1]:
                sell_signals.append(stock)

    except: continue

# --- DATA SORTING ---
weekly_buy_scored = sorted(weekly_buy_scored, key=lambda x: x[1], reverse=True)
monthly_buy_scored = sorted(monthly_buy_scored, key=lambda x: x[1], reverse=True)
top_weekly, rest_weekly = weekly_buy_scored[:5], weekly_buy_scored[5:]
top_monthly, rest_monthly = monthly_buy_scored[:5], monthly_buy_scored[5:]

# --- ALL YOUR ORIGINAL OUTPUTS ---
update_sheet("Top_Weekly", [x[0] for x in top_weekly])
update_sheet("Rest_Weekly", [x[0] for x in rest_weekly])
update_sheet("Top_Monthly", [x[0] for x in top_monthly])
update_sheet("Rest_Monthly", [x[0] for x in rest_monthly])
update_sheet("Weekly_Sell", weekly_sell_signals)
update_sheet("Sell_Signals", sell_signals)
update_sheet("Predictive_Quant_UP", predictive_up)
update_sheet("Predictive_Quant_DOWN", predictive_down)
update_timestamp()

# --- THE TELEGRAM MESSAGE (SPLIT TO PREVENT "MESSAGE TOO LONG" ERROR) ---
msg1 = f"""
🚀 ORIGINAL STRATEGY OUTPUTS:

Top Weekly Buy: {[x[0] for x in top_weekly]}
Rest Weekly Buy: {[x[0] for x in rest_weekly]}
Top Monthly Buy: {[x[0] for x in top_monthly]}
Rest Monthly Buy: {[x[0] for x in rest_monthly]}
Weekly Sell: {weekly_sell_signals}
Monthly Sell: {sell_signals}
"""

msg2 = f"""
🔮 PREDICTIVE QUANT OUTPUTS (Leading Indicators):

Predictive UP (Coiling): {predictive_up}
Predictive DOWN (Exhaustion): {predictive_down}
"""

send_telegram_message(msg1)
send_telegram_message(msg2)

print("Process finished with all original and new outputs.")









