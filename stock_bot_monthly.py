import yfinance as yf
import pandas as pd
import numpy as np
from ta.volume import ChaikinMoneyFlowIndicator
from tradingview_ta import TA_Handler, Interval

# --- TEST SETTINGS ---
SYMBOL = "HAL.NS"
TV_SYMBOL = "HAL"
EXCHANGE = "NSE"
SCREENER = "india"

def test_hal_prediction():
    print(f"--- 🔍 PREDICTIVE ENGINE TEST: {SYMBOL} ---")
    
    try:
        # 1. Pull Weekly Data from Yahoo Finance
        ticker = yf.Ticker(SYMBOL)
        df = ticker.history(period="2y", interval="1wk")
        
        # 2. Calculate Institutional CMF (The "Big Money" Gate)
        cmf_func = ChaikinMoneyFlowIndicator(
            high=df['High'], low=df['Low'], 
            close=df['Close'], volume=df['Volume'], window=20
        )
        current_cmf = cmf_func.chaikin_money_flow().iloc[-1]

        # 3. Pull Squeeze & Momentum from TradingView
        handler = TA_Handler(
            symbol=TV_SYMBOL, exchange=EXCHANGE, 
            screener=SCREENER, interval=Interval.INTERVAL_1_WEEK
        )
        ind = handler.get_analysis().indicators
        
        ao = ind.get("AO")
        bb_u, bb_l = ind.get("BB.upper"), ind.get("BB.lower")
        bb_m = ind.get("BB.basis") or ind.get("SMA20") # Fallback to SMA20

        if all(v is not None for v in [ao, bb_u, bb_l, bb_m]):
            bandwidth = (bb_u - bb_l) / bb_m
            
            # --- WEIGHTED SCORING ---
            score = 0
            details = []
            
            # Point 1: Squeeze
            if bandwidth < 0.18:
                score += 1
                details.append(f"✅ SQUEEZE DETECTED (Bandwidth: {bandwidth:.4f})")
            else:
                details.append(f"❌ NO SQUEEZE (Bandwidth: {bandwidth:.4f})")
                
            # Point 2: Momentum
            if ao > 0:
                score += 1
                details.append(f"✅ MOMENTUM BULLISH (AO: {ao:.2f})")
            else:
                details.append(f"❌ MOMENTUM BEARISH/FLAT (AO: {ao:.2f})")
                
            # Point 3: Institutional
            if current_cmf > 0.05:
                score += 1
                details.append(f"✅ INSTITUTIONAL BUYING (CMF: {current_cmf:.4f})")
            else:
                details.append(f"❌ NO INSTITUTIONAL SUPPORT (CMF: {current_cmf:.4f})")

            # --- FINAL VERDICT ---
            print("\n".join(details))
            print("-" * 30)
            print(f"TOTAL WEIGHTED SCORE: {score}/3")
            
            if score >= 2:
                intensity = (1/bandwidth) * ao if bandwidth > 0 else 0
                print(f"VERDICT: 🚀 PREDICT_UP (Rank Intensity: {intensity:.2f})")
            else:
                print("VERDICT: ⚖️ HOLD (Not enough confirmation)")

        else:
            print("[!] Could not fetch all technical indicators from TradingView.")

    except Exception as e:
        print(f"[!] Error: {e}")

if __name__ == "__main__":
    test_hal_prediction()


