import yfinance as yf
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from ta.volume import ChaikinMoneyFlowIndicator
from tradingview_ta import TA_Handler, Interval, Exchange
import time

# --- SETTINGS ---
SPREADSHEET_NAME = "Stock Bot Dashboard" # Must match your sheet name
TEST_STOCKS = ["RECLTD.NS", "HAL.NS"]

def get_test_data(stock_symbol):
    print(f"\n--- Testing Data for {stock_symbol} ---")
    
    # 1. Test yfinance
    ticker = yf.Ticker(stock_symbol)
    df = ticker.history(period="1y", interval="1wk")
    if df.empty:
        raise ValueError(f"yfinance returned NO DATA for {stock_symbol}")
    print(f"✅ yfinance: Data received ({len(df)} rows)")

    # 2. Test CMF (ta library)
    cmf_func = ChaikinMoneyFlowIndicator(
        high=df['High'], low=df['Low'], close=df['Close'], volume=df['Volume'], window=20
    )
    current_cmf = cmf_func.chaikin_money_flow().iloc[-1]
    print(f"✅ CMF calculated: {current_cmf:.4f}")

    # 3. Test TradingView (tradingview_ta library)
    tv_symbol = stock_symbol.split('.')[0]
    handler = TA_Handler(
        symbol=tv_symbol, 
        exchange="NSE", 
        screener="india", 
        interval=Interval.INTERVAL_1_WEEK
    )
    analysis = handler.get_analysis()
    ao = analysis.indicators.get("AO")
    bb_u = analysis.indicators.get("BB.upper")
    bb_l = analysis.indicators.get("BB.lower")
    bb_m = analysis.indicators.get("BB.basis")
    
    if ao is None:
        raise ValueError(f"TradingView returned NO INDICATORS for {tv_symbol}")
    
    bandwidth = (bb_u - bb_l) / bb_m
    print(f"✅ TradingView: AO={ao:.2f}, BW={bandwidth:.4f}")
    
    return [stock_symbol, f"{bandwidth:.4f}", f"{ao:.2f}", f"{current_cmf:.4f}"]

# --- EXECUTION ---
try:
    # Authenticate
    creds = Credentials.from_service_account_file("credentials.json", 
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    sheet = client.open(SPREADSHEET_NAME).worksheet("Top_Weekly") # Testing on Top_Weekly sheet
    
    print("Connecting to Google Sheets...")
    
    final_rows = [["Stock", "Bandwidth", "AO Value", "CMF Value"]]
    
    for stock in TEST_STOCKS:
        row_data = get_test_data(stock)
        final_rows.append(row_data)
        time.sleep(1)

    # Update the sheet
    sheet.clear()
    sheet.update(final_rows)
    print("\n🚀 SUCCESS: Check your Excel sheet now!")

except Exception as e:
    print("\n🚨 DIAGNOSTIC FAILED!")
    print(f"Error Type: {type(e).__name__}")
    print(f"Error Message: {e}")
    import traceback
    traceback.print_exc()


