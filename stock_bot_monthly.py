import yfinance as yf
import pandas as pd
import numpy as np
import gspread
from google.oauth2.service_account import Credentials
from ta.volume import ChaikinMoneyFlowIndicator
from tradingview_ta import TA_Handler, Interval
import time

# --- SETTINGS ---
SPREADSHEET_NAME = "Stock Bot Dashboard" 
TEST_LIMIT = 100  # Running on exactly 100 stocks

def fetch_tv_data(stock_symbol):
    """Attempt to get TV indicators. Returns None if it fails/times out."""
    try:
        tv_symbol = stock_symbol.split('.')[0]
        handler = TA_Handler(symbol=tv_symbol, exchange="NSE", screener="india", interval=Interval.INTERVAL_1_WEEK)
        analysis = handler.get_analysis()
        return analysis.indicators
    except:
        return None

def process_audit(stock_symbol, local_df, tv_ind):
    """Creates the readable output with labels."""
    try:
        # CMF Calculation
        cmf_func = ChaikinMoneyFlowIndicator(high=local_df['High'], low=local_df['Low'], close=local_df['Close'], volume=local_df['Volume'], window=20)
        cmf_val = cmf_func.chaikin_money_flow().iloc[-1]
        
        if tv_ind is None:
            return ["N/A", "N/A", "N/A", "TV TIMEOUT"]

        ao = tv_ind.get("AO")
        bb_u, bb_l = tv_ind.get("BB.upper"), tv_ind.get("BB.lower")
        # Fallback for middle line
        bb_m = tv_ind.get("BB.basis") or tv_ind.get("SMA20") or local_df['Close'].rolling(20).mean().iloc[-1]

        bandwidth = (bb_u - bb_l) / bb_m if all(v is not None for v in [bb_u, bb_l, bb_m]) else 0
        
        # Labels for humans
        sq_label = "READY" if bandwidth < 0.18 else "LOOSE"
        mo_label = "BULLISH" if (ao is not None and ao > 0) else "BEARISH"
        inst_label = "BUYING" if cmf_val > 0.05 else ("EXITING" if cmf_val < -0.05 else "NEUTRAL")
        
        # Verdict Logic
        if bandwidth < 0.18 and ao > 0 and cmf_val > 0.05:
            verdict = "⭐ EXCELLENT"
        elif cmf_val < -0.07:
            verdict = "⛔ DANGEROUS"
        else:
            verdict = "WATCH"

        return [f"{bandwidth:.4f} ({sq_label})", f"{ao:.2f} ({mo_label})", f"{cmf_val:.4f} ({inst_label})", verdict]
    except Exception as e:
        return ["N/A", "N/A", "N/A", f"ERR: {str(e)[:5]}"]

# --- EXECUTION ---
print("Authenticating with Google...")
creds = Credentials.from_service_account_file("credentials.json", 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
client = gspread.authorize(creds)
sheet = client.open(SPREADSHEET_NAME).worksheet("Top_Weekly")

# Load 100 stocks
stocks_df = pd.read_csv("nse_stocks.csv")
stocks = [s + ".NS" for s in stocks_df['SYMBOL'].dropna().head(TEST_LIMIT).tolist()]

print(f"🚀 Starting Test on {len(stocks)} stocks...")
final_rows = [["Stock", "Volatility (Squeeze)", "Momentum (AO)", "Institutional (CMF)", "BOT VERDICT"]]

for i, stock in enumerate(stocks):
    print(f"[{i+1}/{TEST_LIMIT}] Processing {stock}...", end="\r")
    try:
        ticker = yf.Ticker(stock)
        hist = ticker.history(period="1y", interval="1wk")
        
        if not hist.empty:
            # Step 1: Call TradingView once
            tv_data = fetch_tv_data(stock)
            # Step 2: Generate readable audit
            audit_row = process_audit(stock, hist, tv_data)
            final_rows.append([stock] + audit_row)
            
            # Step 3: Stability Pause
            time.sleep(1.2) 
    except:
        final_rows.append([stock, "ERROR", "ERROR", "ERROR", "CRASHED"])

# Update the Sheet
sheet.clear()
sheet.update(final_rows)
print("\n\n✅ TEST COMPLETE. Open your 'Top_Weekly' tab in Google Sheets to see results.")


