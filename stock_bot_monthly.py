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
from tradingview_ta import TA_Handler, Interval

# --- SETTINGS ---
MARKET_CAP_LIMIT = 5000 * 10**7
MONTHLY_HISTORY = "15y"
WEEKLY_HISTORY = "max"
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

# --- PREDICTIVE ENGINE (ONLY AWESOME OSCILLATOR) ---
def get_predictive_signal(stock_symbol):
    try:
        tv_symbol = stock_symbol.replace(".NS", "")
        handler = TA_Handler(symbol=tv_symbol, exchange="NSE", screener="india", interval=Interval.INTERVAL_1_WEEK)
        analysis = handler.get_analysis()
        ind = analysis.indicators
        
        # Pull Awesome Oscillator directly from TradingView
        ao = ind.get("AO")
        
        if ao is not None:
            if ao > 0:
                return "PREDICT_UP", ao
            elif ao < 0:
                return "PREDICT_DOWN", ao
                
        return "HOLD", 0
    except:
        return "HOLD", 0

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": message})
    except: pass

# --- MAIN ENGINE ---
creds = Credentials.from_service_account_file("credentials.json", scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
client = gspread.authorize(creds)
spreadsheet = client.open(SPREADSHEET_NAME)

def update_sheet(sheet_name, data):
    try: sheet = spreadsheet.worksheet(sheet_name)
    except: sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=5)
    sheet.clear()
    if not data: sheet.update([["No Stocks"]])
    else: sheet.update([["Stock"]] + [[x] for x in data])

stocks_df = pd.read_csv("nse_stocks.csv")
stocks = [s + ".NS" for s in stocks_df['SYMBOL'].dropna().tolist()]

weekly_buy_scored, monthly_buy_scored = [], []
weekly_sell_signals, sell_signals = [], []
predictive_up, predictive_down = [], []

for stock in stocks:
    print(f"Processing {stock}...")
    try:
        ticker = yf.Ticker(stock)
        now = datetime.now()
        raw_w = ticker.history(period=WEEKLY_HISTORY, interval="1wk")
        w_df = raw_w.copy() if (now.weekday() > 4 or (now.weekday() == 4 and now.hour >= 16)) else raw_w.iloc[:-1].copy()

        if len(w_df) >= 300:
            w_close = w_df['Close'].values
            w_df['SSF_20'] = super_smoother(w_close, 20)
            w_df['SSF_50'] = super_smoother(w_close, 50)
            w_df['SSF_100'] = super_smoother(w_close, 100)
            w_df['SSF_200'] = super_smoother(w_close, 200)
            w_df['SSF_250'] = super_smoother(w_close, 250)
            
            # Predictive Logic using ONLY Awesome Oscillator
            p_res, p_score = get_predictive_signal(stock)
            if p_res == "PREDICT_UP": predictive_up.append((stock, p_score))
            elif p_res == "PREDICT_DOWN": predictive_down.append((stock, p_score))

            rsi_w = RSIIndicator(w_df['Close'], window=14).rsi()
            rsi_ma_w = rsi_w.rolling(14).mean()
            if (rolling_setup_weekly(w_df, 20) and rolling_cross(w_close, w_df['SSF_50'].values, 6) and 
                rsi_w.iloc[-1] > rsi_ma_w.iloc[-1] and w_df['SSF_50'].iloc[-1] < w_df['SSF_200'].iloc[-1]):
                
                info = ticker.info
                if info.get("marketCap", 0) >= MARKET_CAP_LIMIT and info.get("profitMargins", 0) > 0:
                    score = rsi_w.iloc[-1] + ((w_close[-1] - w_df['SSF_50'].iloc[-1]) / w_df['SSF_50'].iloc[-1]) * 100
                    weekly_buy_scored.append((stock, score))

            if len(w_df) >= 2:
                prev_h = (w_df['Close'].iloc[-2] > w_df['SSF_20'].iloc[-2] and w_df['Close'].iloc[-2] > w_df['SSF_50'].iloc[-2])
                if prev_h and w_df['Close'].iloc[-1] < w_df['SSF_20'].iloc[-1]:
                    weekly_sell_signals.append(stock)

        raw_m = ticker.history(period=MONTHLY_HISTORY, interval="1mo")
        m_df = raw_m.iloc[:-1].copy()
        if len(m_df) >= 80:
            m_close = m_df['Close'].values
            m_df['SSF_20'] = super_smoother(m_close, 20)
            m_df['SSF_50'] = super_smoother(m_close, 50)
            if rolling_setup_monthly(m_df, 12) and rolling_cross(m_close, m_df['SSF_50'].values, 3):
                score_m = 50 + ((m_close[-1] - m_df['SSF_50'].iloc[-1]) / m_df['SSF_50'].iloc[-1]) * 100
                monthly_buy_scored.append((stock, score_m))
            if m_close[-2] > m_df['SSF_20'].iloc[-2] and m_close[-1] < m_df['SSF_20'].iloc[-1]:
                sell_signals.append(stock)
    except: continue

# Sorting
weekly_buy_scored = sorted(weekly_buy_scored, key=lambda x: x[1], reverse=True)
top_weekly, rest_weekly = [x[0] for x in weekly_buy_scored[:5]], [x[0] for x in weekly_buy_scored[5:]]
monthly_buy_scored = sorted(monthly_buy_scored, key=lambda x: x[1], reverse=True)
top_monthly, rest_monthly = [x[0] for x in monthly_buy_scored[:5]], [x[0] for x in monthly_buy_scored[5:]]

# Predictive sorted by absolute value of AO
predictive_up = [x[0] for x in sorted(predictive_up, key=lambda x: x[1], reverse=True)]
predictive_down = [x[0] for x in sorted(predictive_down, key=lambda x: x[1])]

# Sheets
update_sheet("Top_Weekly", top_weekly)
update_sheet("Rest_Weekly", rest_weekly)
update_sheet("Top_Monthly", top_monthly)
update_sheet("Rest_Monthly", rest_monthly)
update_sheet("Weekly_Sell", weekly_sell_signals)
update_sheet("Sell_Signals", sell_signals)
update_sheet("Predictive_UP", predictive_up)
update_sheet("Predictive_DOWN", predictive_down)

# Telegram Formatting
msg1 = f"""🚀 ORIGINAL STRATEGY OUTPUTS

Top Weekly Buy:
{top_weekly}

Rest Weekly Buy:
{rest_weekly}

Top Monthly Buy:
{top_monthly}

Rest Monthly Buy:
{rest_monthly}

Weekly Sell:
{weekly_sell_signals}

Monthly Sell:
{sell_signals}"""

msg2 = f"""🔮 PREDICTIVE QUANT

Predictive UP:
{predictive_up[:15]}

Predictive DOWN:
{predictive_down[:15]}"""

send_telegram_message(msg1)
send_telegram_message(msg2)
print("Process finished successfully.")




