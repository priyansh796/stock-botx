from tradingview_ta import TA_Handler, Interval
import time

def debug_cmf_value(stock_symbol):
    print(f"--- STARTING CMF DEBUG FOR: {stock_symbol} ---")
    try:
        tv_symbol = stock_symbol.replace(".NS", "")
        
        # We will try to fetch 1-Week and 1-Day intervals to see if one is missing data
        intervals = [Interval.INTERVAL_1_WEEK, Interval.INTERVAL_1_DAY]
        
        for timeframe in intervals:
            print(f"\nChecking Timeframe: {timeframe}")
            handler = TA_Handler(
                symbol=tv_symbol,
                exchange="NSE",
                screener="india",
                interval=timeframe
            )
            
            analysis = handler.get_analysis()
            ind = analysis.indicators
            
            # 1. Check if the key exists at all
            if "Chaikin Money Flow" in ind:
                val = ind["Chaikin Money Flow"]
                print(f"SUCCESS: 'Chaikin Money Flow' key found.")
                print(f"VALUE: {val}")
                if val is None:
                    print("ISSUE: Value is None (TradingView has no CMF data for this stock right now).")
            else:
                print("ISSUE: The key 'Chaikin Money Flow' does not exist in the indicator list.")
                # Print all available keys to see if it's named something else
                print("Available keys related to Money Flow:")
                mf_keys = [k for k in ind.keys() if "Flow" in k or "MF" in k or "Chaikin" in k]
                print(mf_keys if mf_keys else "None found.")

            # 2. Check Volume (CMF requires Volume to calculate)
            vol = ind.get("volume")
            print(f"Current Volume reported by TV: {vol}")
            if vol is None or vol == 0:
                print("ALERT: Volume is 0 or None. CMF cannot be calculated without volume data.")

    except Exception as e:
        print(f"CRITICAL ERROR during debug: {e}")

if __name__ == '__main__':
    # We check MIDHANI and one major stock like RELIANCE for comparison
    debug_cmf_value("MIDHANI.NS")
    print("\n" + "="*30 + "\n")
    debug_cmf_value("RELIANCE.NS")




