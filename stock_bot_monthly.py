import yfinance as yf
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from ta.volume import ChaikinMoneyFlowIndicator
from tradingview_ta import TA_Handler, Interval
import time

# --- SETTINGS ---
SPREADSHEET_NAME = "Stock Bot Dashboard" 
TEST_STOCKS = ["RECLTD.NS", "HAL.NS"]

def get_test_data(stock_symbol):
    print(f"\n--- Testing Data for {stock_symbol} ---")
    
    # 1. Fetch Yahoo Data
    ticker = yf.Ticker(stock_symbol)
    df = ticker.history(period="1y", interval="1wk")
    if df.empty:
        raise ValueError(f"yfinance returned NO DATA for {stock_symbol}")
    print(f"✅ yfinance: Data received")

    # 2. Calculate CMF
    cmf_func = ChaikinMoneyFlowIndicator(high=df['High'], low=df['Low'], close=df['Close'], volume=df['Volume'], window=20)
    current_cmf = cmf_func.chaikin_money_flow().iloc[-1]
    print(f"✅ CMF calculated: {current_cmf:.4f}")

    # 3. Fetch TradingView with "NoneType" Fallback
    tv_symbol = stock_symbol.split('.')[0]
    handler = TA_Handler(symbol=tv_symbol, exchange="NSE", screener="india", interval=Interval.INTERVAL_1_WEEK)
    analysis = handler.get_analysis()
    ind = analysis.indicators
    
    ao = ind.get("AO")
    bb_u = ind.get("BB.upper")
    bb_l = ind.get("BB.lower")
    
    # FIXED LOGIC: Hunt for the middle line or calculate it manually
    bb_m = ind.get("BB.basis") or ind.get("SMA20") or ind.get("MA")
    
    if bb_m is None:
        print("⚠️ BB.basis missing from TV, calculating from yfinance...")
        bb_m = df['Close'].rolling(window=20).mean().iloc[-1]

    # Final Calculation Check
    if all(v is not None for v in [bb_u, bb_l, bb_m]):
        bandwidth = (bb_u - bb_l) / bb_m
        print(f"✅ Success: AO={ao:.2f}, BW={bandwidth:.4f}")
    else:
        bandwidth = 0
        print("❌ Could not resolve Bandwidth")

    return [stock_symbol, f"{bandwidth:.4f}", f"{ao:.2f}", f"{current_cmf:.4f}"]

# --- EXECUTION ---
try:
    creds = Credentials.from_service_account_file("credentials.json", 
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    sheet = client.open(SPREADSHEET_NAME).worksheet("Top_Weekly")
    
    print("Connecting to Google Sheets...")
    final_rows = [["Stock", "Bandwidth", "AO Value", "CMF Value"]]
    
    for stock in TEST_STOCKS:
        row_data = get_test_data(stock)
        final_rows.append(row_data)
        time.sleep(1)

    sheet.clear()
    sheet.update(final_rows)
    print("\n🚀 SUCCESS: Check your Excel sheet now! Numbers should be there.")

except Exception as e:
    print(f"\n🚨 STILL FAILED: {e}")
    import traceback
    traceback.print_exc()


