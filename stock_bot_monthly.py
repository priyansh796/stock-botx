import yfinance as yf
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from ta.volume import MoneyFlowIndexIndicator
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

# --- NEW PREDICTIVE QUANT FUNCTIONS (COMPLETELY INDEPENDENT) ---
def get_predictive_signal(df):
    if len(df) < 35: return "HOLD"
    # Bollinger Squeeze (Energy accumulation)
    bb = BollingerBands(df['Close'], window=20)
    bw = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()
    # Money Flow Index (Institutional leading indicator)
    mfi = MoneyFlowIndexIndicator(df['High'], df['Low'], df['Close'], df['Volume'], window=14).money_flow_index()
    
    # PREDICT UP: Tight squeeze + MFI rising above 50 (Quiet accumulation)
    if bw.iloc[-1] < bw.rolling(20).mean().iloc[-1] and mfi.iloc[-1] > 55 and mfi.iloc[-1] > mfi.iloc[-2]:
        return "PREDICT_UP"
    # PREDICT DOWN: MFI dropping below 50 while price is still high (Secret distribution)
    elif mfi.iloc[-1] < 45 and mfi.iloc[-1] < mfi.iloc[-2]:
        return "PREDICT_DOWN"
    return "HOLD"
# --------------------------------------------------------------

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        response = requests.post(url, data={"chat_id": CHAT_ID, "text": message})
        print(f"Telegram Response: {response.status_code}")
    except Exception as e:
        print(f"Telegram Failed: {e}")

creds = Credentials.from_service_account_file("credentials.json", 
    scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
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

weekly_buy_scored, monthly_buy_scored = [], []
weekly_sell_signals, sell_signals = [], []
fundamental_pass = []

# --- NEW PREDICTIVE LISTS ---
predict_w_up, predict_w_down = [], []
predict_m_up, predict_m_down = [], []

for stock in stocks:
    print(f"Processing {stock} ...")
    try:
        ticker = yf.Ticker(stock)
        
        # WEEKLY DATA
        weekly_df = ticker.history(period=WEEKLY_HISTORY, interval="1wk").iloc[:-1]
        if len(weekly_df) < 300: continue
        
        w_close = weekly_df['Close'].values
        weekly_df['SSF_20'] = super_smoother(w_close, 20)
        weekly_df['SSF_50'] = super_smoother(w_close, 50)
        weekly_df['SSF_100'] = super_smoother(w_close, 100)
        weekly_df['SSF_200'] = super_smoother(w_close, 200)
        weekly_df['SSF_250'] = super_smoother(w_close, 250)
        
        # PREDICTIVE WEEKLY
        pw_res = get_predictive_signal(weekly_df)
        if pw_res == "PREDICT_UP": predict_w_up.append(stock)
        elif pw_res == "PREDICT_DOWN": predict_w_down.append(stock)

        # ORIGINAL WEEKLY LOGIC
        rsi_w = RSIIndicator(weekly_df['Close'], window=14).rsi()
        rsi_ma_w = rsi_w.rolling(14).mean()
        
        if (rolling_setup_weekly(weekly_df, 20) and rolling_cross(w_close, weekly_df['SSF_50'].values, 6) and 
            rsi_w.iloc[-1] > rsi_ma_w.iloc[-1] and weekly_df['SSF_50'].iloc[-1] < weekly_df['SSF_200'].iloc[-1]):
            
            info = ticker.info
            if info.get("marketCap", 0) >= MARKET_CAP_LIMIT and info.get("profitMargins", 0) > 0:
                fundamental_pass.append(stock)
                score = rsi_w.iloc[-1] + ((w_close[-1] - weekly_df['SSF_50'].iloc[-1]) / weekly_df['SSF_50'].iloc[-1]) * 100
                weekly_buy_scored.append((stock, score, weekly_df['SSF_50'].iloc[-1]))

        if w_close[-2] > weekly_df['SSF_20'].iloc[-2] and w_close[-1] < weekly_df['SSF_20'].iloc[-1]:
            weekly_sell_signals.append(stock)

        # MONTHLY DATA
        monthly_df = ticker.history(period=MONTHLY_HISTORY, interval="1mo").iloc[:-1]
        if len(monthly_df) < 80: continue
        
        m_close = monthly_df['Close'].values
        monthly_df['SSF_20'] = super_smoother(m_close, 20)
        monthly_df['SSF_50'] = super_smoother(m_close, 50)
        monthly_df['SSF_100'] = super_smoother(m_close, 100)
        monthly_df['SSF_200'] = super_smoother(m_close, 200)
        monthly_df['SSF_250'] = super_smoother(m_close, 250)

        # PREDICTIVE MONTHLY
        pm_res = get_predictive_signal(monthly_df)
        if pm_res == "PREDICT_UP": predict_m_up.append(stock)
        elif pm_res == "PREDICT_DOWN": predict_m_down.append(stock)

        # ORIGINAL MONTHLY LOGIC
        rsi_m = RSIIndicator(monthly_df['Close'], window=14).rsi()
        rsi_ma_m = rsi_m.rolling(14).mean()
        
        if (rolling_setup_monthly(monthly_df, 12) and rolling_cross(m_close, monthly_df['SSF_50'].values, 3) and 
            rsi_m.iloc[-1] > rsi_ma_m.iloc[-1] and monthly_df['SSF_50'].iloc[-1] < monthly_df['SSF_200'].iloc[-1]):
            
            info = ticker.info
            if info.get("marketCap", 0) >= MARKET_CAP_LIMIT and info.get("profitMargins", 0) > 0:
                if stock not in fundamental_pass: fundamental_pass.append(stock)
                score = rsi_m.iloc[-1] + ((m_close[-1] - monthly_df['SSF_50'].iloc[-1]) / monthly_df['SSF_50'].iloc[-1]) * 100
                monthly_buy_scored.append((stock, score, monthly_df['SSF_50'].iloc[-1]))

        if m_close[-2] > monthly_df['SSF_20'].iloc[-2] and m_close[-1] < monthly_df['SSF_20'].iloc[-1]:
            sell_signals.append(stock)

    except Exception as e:
        print(f"Error: {stock} {e}")
        continue

# --- OUTPUT GENERATION ---
weekly_buy_scored = sorted(weekly_buy_scored, key=lambda x: x[1], reverse=True)
monthly_buy_scored = sorted(monthly_buy_scored, key=lambda x: x[1], reverse=True)

top_weekly, rest_weekly = weekly_buy_scored[:5], weekly_buy_scored[5:]
top_monthly, rest_monthly = monthly_buy_scored[:5], monthly_buy_scored[5:]

# EXCEL
with pd.ExcelWriter(PORTFOLIO_FILE, engine="openpyxl", mode="w") as writer:
    pd.DataFrame(fundamental_pass, columns=["Stock"]).to_excel(writer, sheet_name="Fundamentals", index=False)
    pd.DataFrame(top_weekly, columns=["Stock","Score","StopLoss"]).to_excel(writer, sheet_name="Top_Weekly", index=False)
    pd.DataFrame(rest_weekly, columns=["Stock","Score","StopLoss"]).to_excel(writer, sheet_name="Rest_Weekly", index=False)
    pd.DataFrame(top_monthly, columns=["Stock","Score","StopLoss"]).to_excel(writer, sheet_name="Top_Monthly", index=False)
    pd.DataFrame(rest_monthly, columns=["Stock","Score","StopLoss"]).to_excel(writer, sheet_name="Rest_Monthly", index=False)
    pd.DataFrame(weekly_sell_signals, columns=["Stock"]).to_excel(writer, sheet_name="Weekly_Sell", index=False)
    pd.DataFrame(sell_signals, columns=["Stock"]).to_excel(writer, sheet_name="Sell_Signals", index=False)
    # Predictive Sheets
    pd.DataFrame(predict_w_up, columns=["Stock"]).to_excel(writer, sheet_name="Predictive_Weekly_UP", index=False)
    pd.DataFrame(predict_w_down, columns=["Stock"]).to_excel(writer, sheet_name="Predictive_Weekly_DOWN", index=False)

# GOOGLE SHEETS
update_sheet("Top_Weekly", [x[0] for x in top_weekly])
update_sheet("Rest_Weekly", [x[0] for x in rest_weekly])
update_sheet("Top_Monthly", [x[0] for x in top_monthly])
update_sheet("Rest_Monthly", [x[0] for x in rest_monthly])
update_sheet("Weekly_Sell", weekly_sell_signals)
update_sheet("Sell_Signals", sell_signals)
update_sheet("Predictive_UP", predict_w_up)
update_sheet("Predictive_DOWN", predict_w_down)
update_timestamp()

# TELEGRAM
msg1 = f"🚀 Part 1: Original Strategy\nTop Weekly: {[x[0] for x in top_weekly]}\nRest Weekly: {[x[0] for x in rest_weekly]}\nTop Monthly: {[x[0] for x in top_monthly]}\nWeekly Sell: {weekly_sell_signals}\nMonthly Sell: {sell_signals}"
msg2 = f"🔮 Part 2: Predictive Quant\nPREDICT UP (Before SSF50): {predict_w_up}\nPREDICT DOWN (Before SSF20): {predict_w_down}"

send_telegram_message(msg1)
send_telegram_message(msg2)
print("Finished successfully.")









