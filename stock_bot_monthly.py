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
# --- NEW LIBRARY ADDED ---
from tradingview_ta import TA_Handler, Interval

# --- SETTINGS ---
MARKET_CAP_LIMIT = 5000 * 10**7
MONTHLY_HISTORY = "15y"
WEEKLY_HISTORY = "max"
PORTFOLIO_FILE = "portfolio.xlsx"
SPREADSHEET_NAME = "Stock Bot Dashboard"
TELEGRAM_TOKEN = "8630503074:AAHgONEVwJB_QVZ1GeKBaVGl9Z3Ct0E_yLw"
CHAT_ID = "8258280498"

# --- YOUR ORIGINAL FUNCTIONS (UNCHANGED) ---
def super_smoother(price, period):
    a1 = np.exp(-1.414 * np.pi / period)
    b1 = 2 * a1 * np.cos(1.414 * np.pi / period)
    c2, c3 = b1, -a1 * a1
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
    return True if (cross_found and close[-1] > ssf[-1]) else False

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

# --- UPDATED PREDICTIVE QUANT ENGINE (USING TRADINGVIEW DATA) ---
def get_predictive_signal(stock_symbol):
    try:
        # Format symbol for TradingView (Remove .NS and set exchange)
        tv_symbol = stock_symbol.replace(".NS", "")
        
        handler = TA_Handler(
            symbol=tv_symbol,
            exchange="NSE",
            screener="india",
            interval=Interval.INTERVAL_1_WEEK
        )
        
        analysis = handler.get_analysis()
        ind = analysis.indicators
        
        # 1. CMF Score (40% Weight) - Verified against TradingView Standard
        cmf = ind["Chaikin Money Flow"]
        cmf_pts = 40 if cmf > 0.1 else (-40 if cmf < -0.05 else 0)
        
        # 2. RSI Score (20% Weight)
        rsi = ind["RSI"]
        rsi_pts = 20 if rsi > 60 else (-20 if rsi < 40 else 0)
        
        # 3. MACD Momentum Score (20% Weight)
        macd_h = ind["MACD.macd"] - ind["MACD.signal"]
        macd_pts = 20 if macd_h > 0 else -20
        
        # 4. ADX Trend Strength Score (20% Weight)
        adx = ind["ADX"]
        adx_pts = 20 if adx > 25 else 0
        
        composite_score = cmf_pts + rsi_pts + macd_pts + adx_pts
        
        if composite_score >= 15:
            return "PREDICT_UP", composite_score
        elif composite_score <= -15:
            return "PREDICT_DOWN", composite_score
        return "HOLD", 0
    except:
        return "HOLD", 0

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": message})
    except: pass

# --- SHEETS SETUP ---
creds = Credentials.from_service_account_file("credentials.json", scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
client = gspread.authorize(creds)
spreadsheet = client.open(SPREADSHEET_NAME)

def update_sheet(sheet_name, data):
    try: sheet = spreadsheet.worksheet(sheet_name)
    except: sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=5)
    sheet.clear()
    if not data: sheet.update([["No Stocks"]])
    else: sheet.update([["Stock"]] + [[x] for x in data])

# --- MAIN SCANNER ---
stocks_df = pd.read_csv("nse_stocks.csv")
stocks = [s + ".NS" for s in stocks_df['SYMBOL'].dropna().tolist()]

fundamental_pass = []
weekly_buy_scored, monthly_buy_scored = [], []
weekly_sell_signals, sell_signals = [], []
predictive_up, predictive_down = [], []

for stock in stocks:
    print(f"Processing {stock}...")
    try:
        ticker = yf.Ticker(stock)
        
        # --- WEEKLY DATA STABILITY LOGIC (UNCHANGED) ---
        raw_w_df = ticker.history(period=WEEKLY_HISTORY, interval="1wk")
        now = datetime.now()
        if now.weekday() > 4 or (now.weekday() == 4 and now.hour >= 16):
            w_df = raw_w_df.copy() # Resolved Warning
        else:
            w_df = raw_w_df.iloc[:-1].copy() # Resolved Warning

        if len(w_df) >= 300:
            w_close = w_df['Close'].values
            w_df['SSF_20'] = super_smoother(w_close, 20)
            w_df['SSF_50'] = super_smoother(w_close, 50)
            w_df['SSF_100'] = super_smoother(w_close, 100)
            w_df['SSF_200'] = super_smoother(w_close, 200)
            w_df['SSF_250'] = super_smoother(w_close, 250)
            
            # --- UPDATED PREDICTIVE CALL (NOW USING TRADINGVIEW LIB) ---
            p_res, p_score = get_predictive_signal(stock)
            if p_res == "PREDICT_UP": predictive_up.append((stock, p_score))
            elif p_res == "PREDICT_DOWN": predictive_down.append((stock, p_score))

            # Weekly Buy Original (UNCHANGED)
            rsi_w = RSIIndicator(w_df['Close'], window=14).rsi()
            rsi_ma_w = rsi_w.rolling(14).mean()
            if (rolling_setup_weekly(w_df, 20) and rolling_cross(w_close, w_df['SSF_50'].values, 6) and 
                rsi_w.iloc[-1] > rsi_ma_w.iloc[-1] and w_df['SSF_50'].iloc[-1] < w_df['SSF_200'].iloc[-1]):
                
                info = ticker.info
                if info.get("marketCap", 0) >= MARKET_CAP_LIMIT and info.get("profitMargins", 0) > 0:
                    fundamental_pass.append(stock)
                    score = rsi_w.iloc[-1] + ((w_close[-1] - w_df['SSF_50'].iloc[-1]) / w_df['SSF_50'].iloc[-1]) * 100
                    weekly_buy_scored.append((stock, score, w_df['SSF_50'].iloc[-1]))

            if len(w_df) >= 2:
                prev_healthy = (w_df['Close'].iloc[-2] > w_df['SSF_20'].iloc[-2] and 
                               w_df['Close'].iloc[-2] > w_df['SSF_50'].iloc[-2])
                curr_broken = w_df['Close'].iloc[-1] < w_df['SSF_20'].iloc[-1]
                if prev_healthy and curr_broken:
                    weekly_sell_signals.append(stock)

        # --- MONTHLY DATA STABILITY LOGIC (UNCHANGED) ---
        raw_m_df = ticker.history(period=MONTHLY_HISTORY, interval="1mo")
        if now.day == 1 and now.hour < 16:
             m_df = raw_m_df.iloc[:-1].copy() # Resolved Warning
        else:
             m_df = raw_m_df.iloc[:-1].copy() # Resolved Warning
        
        if len(m_df) >= 80:
            m_close = m_df['Close'].values
            m_df['SSF_20'] = super_smoother(m_close, 20)
            m_df['SSF_50'] = super_smoother(m_close, 50)
            m_df['SSF_200'] = super_smoother(m_close, 200)
            m_df['SSF_250'] = super_smoother(m_close, 250)

            if rolling_setup_monthly(m_df, 12) and rolling_cross(m_close, m_df['SSF_50'].values, 3):
                score_m = 50 + ((m_close[-1] - m_df['SSF_50'].iloc[-1]) / m_df['SSF_50'].iloc[-1]) * 100
                monthly_buy_scored.append((stock, score_m, m_df['SSF_50'].iloc[-1]))

            if m_close[-2] > m_df['SSF_20'].iloc[-2] and m_close[-1] < m_df['SSF_20'].iloc[-1]:
                sell_signals.append(stock)
    except: continue

# --- SORTING SECTION (UNCHANGED) ---
weekly_buy_scored = sorted(weekly_buy_scored, key=lambda x: x[1], reverse=True)
top_weekly, rest_weekly = weekly_buy_scored[:5], weekly_buy_scored[5:]
monthly_buy_scored = sorted(monthly_buy_scored, key=lambda x: x[1], reverse=True)
top_monthly, rest_monthly = monthly_buy_scored[:5], monthly_buy_scored[5:]

predictive_up = sorted(predictive_up, key=lambda x: x[1], reverse=True)
predictive_up = [x[0] for x in predictive_up]
predictive_down = sorted(predictive_down, key=lambda x: x[1])
predictive_down = [x[0] for x in predictive_down]

# Update Sheets (UNCHANGED)
update_sheet("Top_Weekly", [x[0] for x in top_weekly])
update_sheet("Rest_Weekly", [x[0] for x in rest_weekly])
update_sheet("Top_Monthly", [x[0] for x in top_monthly])
update_sheet("Rest_Monthly", [x[0] for x in rest_monthly])
update_sheet("Weekly_Sell", weekly_sell_signals)
update_sheet("Sell_Signals", sell_signals)
update_sheet("Predictive_UP", predictive_up)
update_sheet("Predictive_DOWN", predictive_down)

# --- TELEGRAM FORMATTING (UNCHANGED) ---
msg1 = f"""
🚀 ORIGINAL STRATEGY OUTPUTS

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

Monthly Sell:
{sell_signals}
"""

msg2 = f"""
🔮 PREDICTIVE QUANT (RANKED BY COMPOSITE SCORE)

Predictive UP (High Conviction First):
{predictive_up}

Predictive DOWN (High Conviction First):
{predictive_down}
"""

send_telegram_message(msg1)
send_telegram_message(msg2)
print("Process finished successfully.")




