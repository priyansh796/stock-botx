import yfinance as yf
from ta.volume import ChaikinMoneyFlowIndicator

def verify_cmf():
    stock = "RELIANCE.NS"
    print(f"--- Verifying Institutional CMF for {stock} ---")
    
    df = yf.Ticker(stock).history(period="6mo", interval="1wk")
    
    # Standard 20-period CMF
    cmf_func = ChaikinMoneyFlowIndicator(
        high=df['High'], low=df['Low'], close=df['Close'], volume=df['Volume'], window=20
    )
    df['CMF'] = cmf_func.chaikin_money_flow()
    
    val = df['CMF'].iloc[-1]
    
    print(f"Latest CMF Value: {val:.4f}")
    if val > 0:
        print("Result: ✅ Institutions are ACCUMULATING (Buying)")
    else:
        print("Result: ⚠️ Institutions are DISTRIBUTING (Selling)")

if __name__ == "__main__":
    verify_cmf()


