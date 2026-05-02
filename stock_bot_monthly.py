import yfinance as yf
from tradingview_ta import TA_Handler, Interval
import pandas as pd

# --- SETTINGS FOR DEBUG ---
DEBUG_SYMBOL = "RELIANCE.NS"  # Change this to any stock you want to test
EXCHANGE = "NSE"
SCREENER = "india"
INTERVAL = Interval.INTERVAL_1_WEEK

def debug_predictive_engine(stock_symbol):
    print(f"\n--- DEBUG START: {stock_symbol} ---")
    
    try:
        # 1. TradingView Fetching Logic
        tv_symbol = stock_symbol.replace(".NS", "")
        print(f"[1] Connecting to TradingView for: {tv_symbol}...")
        
        handler = TA_Handler(
            symbol=tv_symbol,
            exchange=EXCHANGE,
            screener=SCREENER,
            interval=INTERVAL
        )
        
        analysis = handler.get_analysis()
        ind = analysis.indicators
        
        # 2. Extracting Specific Indicators
        ao = ind.get("AO")
        bb_u = ind.get("BB.upper")
        bb_l = ind.get("BB.lower")
        bb_m = ind.get("BB.basis")
        
        print(f"[2] Raw Data Received:")
        print(f"    > Awesome Oscillator (AO): {ao}")
        print(f"    > BB Upper: {bb_u}")
        print(f"    > BB Mid (Basis): {bb_m}")
        print(f"    > BB Lower: {bb_l}")

        # 3. Validation Logic
        if all(v is not None for v in [ao, bb_u, bb_l, bb_m]):
            # Calculate Bandwidth (Squeeze level)
            bandwidth = (bb_u - bb_l) / bb_m
            print(f"\n[3] Calculated Bandwidth (Squeeze): {bandwidth:.4f}")
            
            # 4. Scoring Logic Analysis
            print(f"[4] Analyzing Squeeze Threshold (Target < 0.15):")
            if bandwidth < 0.15:
                print(f"    ✅ SQUEEZE DETECTED!")
                if ao > 0:
                    score = (1/bandwidth) * ao
                    print(f"    📈 DIRECTION: UP | SCORE: {score:.2f}")
                else:
                    score = (1/bandwidth) * abs(ao)
                    print(f"    📉 DIRECTION: DOWN | SCORE: {score:.2f}")
            else:
                print(f"    ❌ NO SQUEEZE: Bandwidth {bandwidth:.4f} is too wide (> 0.15).")
        else:
            print("\n[!] DATA ERROR: One or more indicators returned 'None'.")

    except Exception as e:
        print(f"\n[!] CRITICAL ERROR: {e}")

    print("--- DEBUG END ---\n")

if __name__ == "__main__":
    debug_predictive_engine(DEBUG_SYMBOL)




