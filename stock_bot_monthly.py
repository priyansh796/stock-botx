from tradingview_ta import TA_Handler, Interval
import sys

def debug_stock(stock_symbol):
    print(f"--- DEBUGGING: {stock_symbol} ---")
    try:
        # Standardize symbol for TradingView
        tv_symbol = stock_symbol.replace(".NS", "")
        print(f"Targeting TV Symbol: {tv_symbol} on NSE exchange")
        
        handler = TA_Handler(
            symbol=tv_symbol,
            exchange="NSE",
            screener="india",
            interval=Interval.INTERVAL_1_WEEK
        )
        
        analysis = handler.get_analysis()
        ind = analysis.indicators
        
        # Capture raw values
        cmf = ind.get("Chaikin Money Flow")
        rsi = ind.get("RSI")
        macd = ind.get("MACD.macd")
        signal = ind.get("MACD.signal")
        adx = ind.get("ADX")
        
        print(f"\nRAW VALUES FROM TRADINGVIEW:")
        print(f"CMF: {cmf}")
        print(f"RSI: {rsi}")
        print(f"MACD: {macd}")
        print(f"MACD Signal: {signal}")
        print(f"ADX: {adx}")

        # Calculate score points based on your logic
        cmf_pts = 40 if (cmf and cmf > 0.1) else (-40 if (cmf and cmf < -0.05) else 0)
        rsi_pts = 20 if (rsi and rsi > 60) else (-20 if (rsi and rsi < 40) else 0)
        macd_pts = 20 if (macd and signal and (macd - signal) > 0) else -20
        adx_pts = 20 if (adx and adx > 25) else 0
        
        total_score = cmf_pts + rsi_pts + macd_pts + adx_pts
        
        print("\n--- SCORE BREAKDOWN ---")
        print(f"CMF Points:  {cmf_pts}")
        print(f"RSI Points:  {rsi_pts}")
        print(f"MACD Points: {macd_pts}")
        print(f"ADX Points:  {adx_pts}")
        print(f"TOTAL SCORE: {total_score}")
        
        if total_score >= 15:
            print("\nRESULT: This stock SHOULD appear in PREDICT_UP list.")
        elif total_score <= -15:
            print("\nRESULT: This stock SHOULD appear in PREDICT_DOWN list.")
        else:
            print("\nRESULT: Stock is in HOLD/NEUTRAL zone (Score between -15 and 15).")

    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")

if __name__ == '__main__':
    # Checking MIDHANI (Mishra Dhatu Nigam Limited)
    debug_stock("MIDHANI.NS")




