from tradingview_ta import TA_Handler, Interval

# --- TEST SETTINGS ---
TEST_STOCK = "RELIANCE" # Ticker name as it appears on TradingView
INTERVAL = Interval.INTERVAL_1_WEEK

def verify_fix():
    print(f"--- VERIFICATION START: {TEST_STOCK} ---")
    try:
        # 1. Fetch from TradingView
        handler = TA_Handler(
            symbol=TEST_STOCK,
            exchange="NSE",
            screener="india",
            interval=INTERVAL
        )
        analysis = handler.get_analysis()
        ind = analysis.indicators

        # 2. Extract Data
        ao = ind.get("AO")
        bb_u = ind.get("BB.upper")
        bb_l = ind.get("BB.lower")
        
        # This was the problematic line - checking multiple keys now
        bb_basis = ind.get("BB.basis")
        sma20 = ind.get("SMA20")
        
        print(f"[DATA CHECK]")
        print(f" > Awesome Oscillator (AO): {ao}")
        print(f" > BB Upper: {bb_u}")
        print(f" > BB Lower: {bb_l}")
        print(f" > BB Basis Key: {bb_basis}")
        print(f" > SMA20 Key: {sma20}")

        # 3. Verify Logic
        # Use whatever is NOT None
        final_mid = bb_basis or sma20
        
        if final_mid is None:
            print("\n[!] VERIFICATION FAILED: Mid-line is still None. TradingView keys have changed.")
        elif ao is None or bb_u is None or bb_l is None:
            print("\n[!] VERIFICATION FAILED: Other indicators are None.")
        else:
            bandwidth = (bb_u - bb_l) / final_mid
            print(f"\n[SUCCESS] Fix verified.")
            print(f" > Final Mid-line used: {final_mid}")
            print(f" > Calculated Bandwidth: {bandwidth:.4f}")
            
            if bandwidth < 0.18:
                print(" > Status: SQUEEZE DETECTED")
            else:
                print(" > Status: NO SQUEEZE (Bands are too wide)")

    except Exception as e:
        print(f"\n[!] ERROR DURING TEST: {e}")

if __name__ == "__main__":
    verify_fix()




