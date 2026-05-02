import yfinance as yf
import pandas as pd
import numpy as np
import gspread
from google.oauth2.service_account import Credentials
from tradingview_ta import TA_Handler, Interval
from ta.momentum import AwesomeOscillatorIndicator
from ta.volatility import BollingerBands
import time

# --- SETTINGS ---
SPREADSHEET_NAME = "Stock Bot Dashboard" 
TEST_LIMIT = 10 

def fetch_tv_values(symbol):
    """Fetches raw numbers from TradingView."""
    try:
        tv_symbol = symbol.split('.')[0]
        handler = TA_Handler(symbol=tv_symbol, exchange="NSE", screener="india", interval=Interval.INTERVAL_1_WEEK)
        ind = handler.get_analysis().indicators
        ao = ind.get("AO")
        bb_u = ind.get("BB.upper")
        bb_l = ind.get("BB.lower")
        bb_m = ind.get("BB.basis") or ind.get("SMA20")
        bw = (bb_u - bb_l) / bb_m if bb_m else 0
        return ao, bw
    except:
        return None, None

def fetch_yf_ta_values(df):
    """Calculates numbers using YFinance data + TA Library."""
    try:
        # AO Calculation
        ao_ins = AwesomeOscillatorIndicator(high=df['High'], low=df['Low'])
        ao_val = ao_ins.awesome_oscillator().iloc[-1]
        
        # Bandwidth Calculation
        bb_ins = BollingerBands(close=df['Close'])
        bw_val = (bb_ins.bollinger_hband().iloc[-1] - bb_ins.bollinger_lband().iloc[-1]) / bb_ins.bollinger_mavg().iloc[-1]
        
        return ao_val, bw_val
    except:
        return None, None

# --- EXECUTION ---
creds = Credentials.from_service_account_file("credentials.json", 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
client = gspread.authorize(creds)
sheet = client.open(SPREADSHEET_NAME).worksheet("Top_Weekly")

stocks_df = pd.read_csv("nse_stocks.csv")
stocks = [s + ".NS" for s in stocks_df['SYMBOL'].dropna().head(TEST_LIMIT).tolist()]

print(f"🚀 Running Side-by-Side Comparison on {TEST_LIMIT} stocks...")

headers = [["Stock", "TV AO", "YF AO", "AO Diff", "TV Bandwidth", "YF Bandwidth", "BW Diff"]]
rows = []

for stock in stocks:
    print(f"Comparing {stock}...")
    try:
        # 1. Get TV Data
        tv_ao, tv_bw = fetch_tv_values(stock)
        
        # 2. Get YF Data
        ticker = yf.Ticker(stock)
        hist = ticker.history(period="1y", interval="1wk")
        yf_ao, yf_bw = fetch_yf_ta_values(hist)
        
        # 3. Calculate Differences
        ao_diff = abs(tv_ao - yf_ao) if (tv_ao and yf_ao) else "N/A"
        bw_diff = abs(tv_bw - yf_bw) if (tv_bw and yf_bw) else "N/A"
        
        rows.append([
            stock, 
            round(tv_ao, 2) if tv_ao else "TIMEOUT", 
            round(yf_ao, 2) if yf_ao else "ERR",
            round(ao_diff, 4) if isinstance(ao_diff, float) else "N/A",
            round(tv_bw, 4) if tv_bw else "TIMEOUT",
            round(yf_bw, 4) if yf_bw else "ERR",
            round(bw_diff, 6) if isinstance(bw_diff, float) else "N/A"
        ])
        
        time.sleep(2) # Extra delay to help TradingView stay connected
    except Exception as e:
        print(f"Error on {stock}: {e}")

sheet.clear()
sheet.update(headers + rows)
print("\n✅ COMPARISON COMPLETE. Check the 'Top_Weekly' sheet.")


