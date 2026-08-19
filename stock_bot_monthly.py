import os
import sys
import time
import pandas as pd
import numpy as np
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials
import requests
from datetime import datetime
from pydantic import BaseModel, Field
import ta
from ta.momentum import RSIIndicator, AwesomeOscillatorIndicator
from ta.volatility import BollingerBands
from ta.volume import ChaikinMoneyFlowIndicator
from ta.trend import ADXIndicator

# --- GOOGLE-GENAI SDK INITIALIZATION ---
try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Error: Please make sure 'google-genai' is installed in your environment.")
    sys.exit(1)

# --- SETTINGS & CONSTRAINTS ---
MARKET_CAP_LIMIT = 5000 * 10**7
MONTHLY_HISTORY = "max"
WEEKLY_HISTORY = "max"
SPREADSHEET_NAME = "Stock Bot Dashboard"
TELEGRAM_TOKEN = "8630503074:AAHgONEVwJB_QVZ1GeKBaVGl9Z3Ct0E_yLw"
CHAT_ID = "8258280498"

# =====================================================================
# CORE SSF & STRATEGY LOGIC
# =====================================================================
def super_smoother(price, period):
    a1 = np.exp(-1.414 * np.pi / period)
    b1 = 2 * a1 * np.cos(1.414 * np.pi / period)
    c2, c3 = b1, -a1 * a1
    c1 = 1 - c2 - c3
    filt = np.zeros(len(price))
    for i in range(2, len(price)):
        filt[i] = (c1 * (price[i] + price[i - 1]) / 2 + c2 * filt[i - 1] + c3 * filt[i - 2])
    return filt

def rolling_cross(close, ssf, lookback):
    cross_found = False
    for i in range(1, lookback):
        if close[-i - 1] < ssf[-i - 1] and close[-i] > ssf[-i]:
            cross_found = True
            break
    return True if (cross_found and close[-1] > ssf[-1]) else False

def get_crossover_details(close, ssf, max_lookback=50):
    """
    Calculates percentage difference between CURRENT PRICE and the 
    SSF_50 VALUE at the time the crossover occurred.
    """
    n = len(close)
    lookback = min(max_lookback, n - 1)
    
    # 1. Search for explicit crossover event: Close[t-1] < SSF[t-1] AND Close[t] > SSF[t]
    for i in range(1, lookback):
        if close[-i - 1] < ssf[-i - 1] and close[-i] > ssf[-i]:
            ssf_at_cross = ssf[-i]  # Exact SSF_50 level on breakout week
            bars_since = i - 1      # 0 = crossed on latest completed bar
            
            # Gain calculated relative to SSF_50 level
            delta_pct = ((close[-1] - ssf_at_cross) / ssf_at_cross) * 100
            return delta_pct, bars_since

    # 2. Fallback: Walk backward to find when price was last below SSF_50
    if close[-1] > ssf[-1]:
        for i in range(1, lookback):
            if close[-i] < ssf[-i]:
                ssf_at_cross = ssf[-i + 1]
                bars_since = i - 1
                delta_pct = ((close[-1] - ssf_at_cross) / ssf_at_cross) * 100
                return delta_pct, bars_since
        
        # If continuously above SSF_50 for entire lookback
        ssf_at_cross = ssf[-lookback]
        delta_pct = ((close[-1] - ssf_at_cross) / ssf_at_cross) * 100
        return delta_pct, lookback

    return 0.0, 0

def rolling_setup_monthly(df, lookback):
    for i in range(1, lookback):
        if (df['Close'].iloc[-i] < df['SSF_50'].iloc[-i] and 
            df['Close'].iloc[-i] < df['SSF_100'].iloc[-i] and 
            df['Close'].iloc[-i] < df['SSF_250'].iloc[-i]):
            return True
    return False

def rolling_setup_weekly(df, lookback):
    for i in range(1, lookback):
        if (df['Close'].iloc[-i] < df['SSF_50'].iloc[-i] and 
            df['Close'].iloc[-i] < df['SSF_100'].iloc[-i] and 
            df['Close'].iloc[-i] < df['SSF_250'].iloc[-i]):
            return True
    return False

def check_ssf_special_weekly(df):
    if len(df) < 7: return False
    current_close = df['Close'].iloc[-1]
    current_ssf50 = df['SSF_50'].iloc[-1]
    current_ssf200 = df['SSF_200'].iloc[-1]
    current_ssf250 = df['SSF_250'].iloc[-1]
    if not (current_close > current_ssf50 and current_close > current_ssf200 and current_close > current_ssf250): return False
    cross_found = False
    for i in range(1, 7):
        prev_idx = -i - 1
        curr_idx = -i
        if df['Close'].iloc[prev_idx] < df['SSF_50'].iloc[prev_idx] and df['Close'].iloc[curr_idx] > df['SSF_50'].iloc[curr_idx]:
            cross_found = True
            break
    return cross_found

def check_ssf_special_monthly(df):
    if len(df) < 4: return False
    current_close = df['Close'].iloc[-1]
    current_ssf50 = df['SSF_50'].iloc[-1]
    current_ssf200 = df['SSF_200'].iloc[-1]
    current_ssf250 = df['SSF_250'].iloc[-1]
    if not (current_close > current_ssf50 and current_close > current_ssf200 and current_close > current_ssf250): return False
    cross_found = False
    for i in range(1, 4):
        prev_idx = -i - 1
        curr_idx = -i
        if df['Close'].iloc[prev_idx] < df['SSF_50'].iloc[prev_idx] and df['Close'].iloc[curr_idx] > df['SSF_50'].iloc[curr_idx]:
            cross_found = True
            break
    return cross_found

def check_ssf_two_weeks_ago_confirmed(df):
    if len(df) < 5: return False
    crossed_two_weeks_ago = (df['Close'].iloc[-4] < df['SSF_50'].iloc[-4]) and (df['Close'].iloc[-3] > df['SSF_50'].iloc[-3])
    remained_above = (df['Close'].iloc[-2] > df['SSF_50'].iloc[-2]) and (df['Close'].iloc[-1] > df['SSF_50'].iloc[-1])
    return crossed_two_weeks_ago and remained_above

# --- TECHNICAL AUDIT DATA EXTRACTION ---
def get_audit_data(stock_symbol, local_df):
    try:
        local_df = local_df.dropna(subset=['Close', 'High', 'Low', 'Volume'])
        
        cmf_series = ChaikinMoneyFlowIndicator(high=local_df['High'], low=local_df['Low'], close=local_df['Close'], volume=local_df['Volume'], window=20).chaikin_money_flow()
        current_cmf = cmf_series.iloc[-1] if not np.isnan(cmf_series.iloc[-1]) else cmf_series.iloc[-2]
        
        ao_series = AwesomeOscillatorIndicator(high=local_df['High'], low=local_df['Low']).awesome_oscillator()
        raw_ao = ao_series.iloc[-1] if not np.isnan(ao_series.iloc[-1]) else ao_series.iloc[-2]
        current_close = local_df['Close'].iloc[-1]
        ao = (raw_ao / current_close) * 100

        bb = BollingerBands(close=local_df['Close'])
        bw_series = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()
        bandwidth = bw_series.iloc[-1] if not np.isnan(bw_series.iloc[-1]) else bw_series.iloc[-2]
        
        adx_ind = ADXIndicator(high=local_df['High'], low=local_df['Low'], close=local_df['Close'], window=14)
        adx_val = adx_ind.adx().iloc[-1] if not np.isnan(adx_ind.adx().iloc[-1]) else 0.0
        p_di = adx_ind.adx_pos().iloc[-1] if not np.isnan(adx_ind.adx_pos().iloc[-1]) else 0.0
        n_di = adx_ind.adx_neg().iloc[-1] if not np.isnan(adx_ind.adx_neg().iloc[-1]) else 0.0

        vwap_series = (local_df['Volume'] * (local_df['High'] + local_df['Low'] + local_df['Close']) / 3).cumsum() / local_df['Volume'].cumsum()
        vwap_val = vwap_series.iloc[-1] if not np.isnan(vwap_series.iloc[-1]) else current_close

        bandwidth = 0.0 if np.isnan(bandwidth) else bandwidth
        ao = 0.0 if np.isnan(ao) else ao
        current_cmf = 0.0 if np.isnan(current_cmf) else current_cmf

        sq_label = "READY" if bandwidth < 0.18 else "LOOSE"
        mo_label = "BULLISH" if ao > 0 else "BEARISH"
        inst_label = "BUYING" if current_cmf > 0.05 else ("EXITING" if current_cmf < -0.05 else "NEUTRAL")
        
        if bandwidth < 0.18 and ao > 0 and current_cmf > 0.05 and adx_val > 20:
            get_verdict = "⭐ EXCELLENT"
        elif current_cmf < -0.07 or n_di > p_di:
            get_verdict = "⛔ DANGEROUS"
        else:
            get_verdict = "WATCH"

        return [
            f"{bandwidth:.4f} ({sq_label})", f"{ao:.4f}% ({mo_label})", f"{current_cmf:.4f} ({inst_label})", 
            get_verdict, bandwidth, ao, current_cmf, round(adx_val, 2), round(p_di, 2), round(n_di, 2), round(vwap_val, 2)
        ]
    except Exception:
        return ["N/A", "N/A", "N/A", "ERROR", 0, 0, 0, 0, 0, 0, 0]

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": message}, timeout=10)
    except Exception: pass

# --- GOOGLE SHEETS CONNECTOR ---
creds = Credentials.from_service_account_file("credentials.json", scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
client_gspread = gspread.authorize(creds)
spreadsheet = client_gspread.open(SPREADSHEET_NAME)

def clean_val(val):
    try: return float(val.split(' ')[0].replace('%', ''))
    except Exception: return 0.0

# --- SHEET WIPING & RE-WRITING ENGINE (FOR SCANNER TABS) ---
def update_sheet(sheet_name, data_list, delta_map=None, time_frame_label="Weeks"):
    try: sheet = spreadsheet.worksheet(sheet_name)
    except Exception: sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=14)
    
    prev_rows = sheet.get_all_values()
    history = {}
    if len(prev_rows) > 1:
        for idx, row in enumerate(prev_rows[1:], start=1):
            if len(row) >= 4 and row[0]: 
                history[row[0]] = {"rank": idx, "bw": clean_val(row[1]), "ao": clean_val(row[2]), "cmf": clean_val(row[3])}

    sheet.clear()
    time.sleep(2)
    headers = [["Stock", "SSF 50 Cross Delta %", f"{time_frame_label} Since Cross", "Volatility (Squeeze)", "Momentum (AO %)", "Institutional (CMF)", "Rank Delta", "BW Delta", "AO Delta", "CMF Delta", "BOT VERDICT"]]
    rows = []
    
    if not data_list:
        sheet.update(range_name='A1', values=[["No Stocks"]])
        return

    for current_rank, stock in enumerate(data_list, start=1):
        ticker = yf.Ticker(stock)
        df = ticker.history(period="1y", interval="1wk")
        if not df.empty:
            audit = get_audit_data(stock, df)
            
            # Fetch SSF 50 Crossover details
            if delta_map and stock in delta_map:
                c_delta_pct, bars_elapsed = delta_map[stock]
                c_delta_str = f"{c_delta_pct:.2f}%"
                bars_str = f"{bars_elapsed}"
            else:
                c_delta_str = "N/A"
                bars_str = "N/A"

            if stock in history:
                prev = history[stock]
                r_delta = prev['rank'] - current_rank
                bw_delta = audit[4] - prev['bw']
                ao_delta = audit[5] - prev['ao']
                cmf_delta = audit[6] - prev['cmf']
                r_str = f"⬆️ {r_delta}" if r_delta > 0 else (f"⬇️ {abs(r_delta)}" if r_delta < 0 else "—")
            else:
                r_str, bw_delta, ao_delta, cmf_delta = "🆕 NEW", 0, 0, 0

            rows.append([
                stock, c_delta_str, bars_str, audit[0], audit[1], audit[2], 
                r_str, f"{bw_delta:.4f}", f"{ao_delta:.4f}%", f"{cmf_delta:.4f}", 
                audit[3]
            ])
    
    sheet.update(range_name='A1', values=(headers + rows))

def simple_update(sheet_name, data_list):
    try: sheet = spreadsheet.worksheet(sheet_name)
    except Exception: sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=10)
    sheet.clear()
    time.sleep(2)
    headers = [["Stock"]]
    rows = [[s] for s in data_list]
    sheet.update(range_name='A1', values=(headers + rows))

# =====================================================================
# SAFE PORTFOLIO TRACKER ENGINE (PRESERVES EXISTING USER ROWS)
# =====================================================================
def update_portfolio_tracker():
    headers = [["Stock", "Current Price", "SSF_20 Level", "SSF_20 Breach Status", "Action", "Last Updated"]]
    try:
        sheet = spreadsheet.worksheet("Portfolio_Tracker")
    except Exception:
        sheet = spreadsheet.add_worksheet(title="Portfolio_Tracker", rows=500, cols=6)
        sheet.update(range_name='A1', values=headers)
        print("Portfolio_Tracker initialized.")
        return

    existing_rows = sheet.get_all_values()
    
    if not existing_rows:
        sheet.update(range_name='A1', values=headers)
        return

    if existing_rows[0] != headers[0]:
        sheet.update(range_name='A1', values=headers)

    if len(existing_rows) <= 1:
        return

    updated_rows = []
    red_rows = []

    for idx, row in enumerate(existing_rows[1:], start=2):
        if not row or not row[0].strip(): continue
        stock = row[0].strip()
        stock_symbol = stock if stock.endswith(".NS") or stock.endswith(".BO") else stock + ".NS"

        try:
            df = yf.Ticker(stock_symbol).history(period="1y", interval="1wk")
            if len(df) >= 20:
                close_arr = df['Close'].values
                ssf_20 = super_smoother(close_arr, 20)
                
                curr_close, curr_ssf20 = close_arr[-1], ssf_20[-1]
                prev_close, prev_ssf20 = close_arr[-2], ssf_20[-2]

                if prev_close > prev_ssf20 and curr_close < curr_ssf20:
                    status, action = "⚠️ BREACHED SSF_20", "SELL / EXIT NOW"
                    red_rows.append(idx)
                elif curr_close < curr_ssf20:
                    status, action = "BELOW SSF_20", "HOLD CASH / EXIT"
                    red_rows.append(idx)
                else:
                    status, action = "ABOVE SSF_20", "HOLD POSITION"

                updated_rows.append([
                    stock, f"INR {curr_close:.2f}", f"INR {curr_ssf20:.2f}", 
                    status, action, datetime.now().strftime("%Y-%m-%d %H:%M")
                ])
            else:
                updated_rows.append([stock, "NO DATA", "NO DATA", "INSUFFICIENT HISTORY", "NONE", datetime.now().strftime("%Y-%m-%d %H:%M")])
        except Exception:
            updated_rows.append([stock, "ERROR", "ERROR", "FETCH FAILED", "NONE", datetime.now().strftime("%Y-%m-%d %H:%M")])

    sheet.update(range_name=f'A2:F{len(updated_rows)+1}', values=updated_rows)

    if red_rows:
        try:
            format_requests = [{"range": f"A{r}:F{r}", "format": {"backgroundColor": {"red": 1.0, "green": 0.8, "blue": 0.8}, "textFormat": {"bold": True}}} for r in red_rows]
            sheet.batch_format(format_requests)
        except Exception: pass

# --- ADVANCED AI STRUCTURED SCHEMAS & DEEP DIVE ENGINE ---
class SwingMomentumAnalysis(BaseModel):
    ticker: str = Field(description="Ticker symbol")
    signal_source_list: str = Field(description="Strategy sources (e.g. Top_Weekly, SSF_Two_Weeks_Ago)")
    overall_score: int = Field(description="Overall Quantitative Momentum Score from 0 to 100 combining technicals & macro outlook.")
    comprehensive_momentum_drivers: str = Field(description="Detailed analysis of ADX trend strength, VWAP position, AO momentum expansion, and price velocity.")
    institutional_risk_analysis: str = Field(description="Evaluation of CMF institutional order flow, volume trends, and macro headwinds.")
    investment_verdict: str = Field(description="Definitive rating: STRONG BUY, ACCUMULATE, HOLD, or AVOID with target profit and dynamic stop loss coordinates.")

class PortfolioAuditPayload(BaseModel):
    analyses: list[SwingMomentumAnalysis]

def run_batch_portfolio_ai_audit(unified_stock_list, top_w, rest_w, ssf_2w):
    if not unified_stock_list: return []

    try: sheet = spreadsheet.worksheet("AI_SSF_2Weeks_Deep_Dive")
    except Exception: sheet = spreadsheet.add_worksheet(title="AI_SSF_2Weeks_Deep_Dive", rows=1000, cols=6)
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key: return []

    ai_client = genai.Client(api_key=api_key)
    all_final_rows = []
    
    CHUNK_SIZE = 15
    stock_chunks = [unified_stock_list[i:i + CHUNK_SIZE] for i in range(0, len(unified_stock_list), CHUNK_SIZE)]

    for idx, chunk in enumerate(stock_chunks, start=1):
        try:
            compiled_stock_data_context = ""
            for stock in chunk:
                try:
                    origins = []
                    if stock in top_w: origins.append("Top_Weekly")
                    if stock in rest_w: origins.append("Rest_Weekly")
                    if stock in ssf_2w: origins.append("SSF_Two_Weeks_Ago")
                    source_str = ", ".join(origins) if origins else "Runtime_Scanner_Pool"

                    ticker = yf.Ticker(stock)
                    df = ticker.history(period="1y", interval="1wk")
                    if df.empty: continue
                        
                    close_arr = df['Close'].values
                    df['SSF_50'] = super_smoother(close_arr, 50)
                    
                    current_volume = df['Volume'].iloc[-1]
                    avg_volume_20 = df['Volume'].rolling(window=20).mean().iloc[-1]
                    volume_ratio = current_volume / avg_volume_20 if avg_volume_20 > 0 else 1.0
                    
                    rsi_vals = RSIIndicator(close=df['Close'], window=14).rsi()
                    rsi_current = rsi_vals.iloc[-1] if not np.isnan(rsi_vals.iloc[-1]) else 50.0
                    
                    audit = get_audit_data(stock, df)
                    
                    support_52wk = df['Low'].rolling(window=52, min_periods=1).min().iloc[-1]
                    resistance_52wk = df['High'].rolling(window=52, min_periods=1).max().iloc[-1]
                    
                    compiled_stock_data_context += f"""
                    === TICKER: {stock} (Strategy Sources: {source_str}) ===
                    - Current Price: INR {df['Close'].iloc[-1]:.2f} | VWAP: INR {audit[10]}
                    - Trend Indicators: ADX (14) = {audit[7]} (+DI: {audit[8]}, -DI: {audit[9]})
                    - Oscillators & Squeeze: 14-Wk RSI = {rsi_current:.2f} | AO % = {audit[1]} | Bandwidth = {audit[0]}
                    - Orderflow & Volume: Chaikin Money Flow = {audit[2]} | Vol Ratio = {volume_ratio:.2f}x
                    - Support/Resistance: 52-Wk Low = INR {support_52wk:.2f} | 52-Wk High = INR {resistance_52wk:.2f}
                    """
                except Exception: continue

            if not compiled_stock_data_context.strip(): continue

            prompt = f"""
            You are a Senior Quantitative Portfolio Manager and Strategic Macro Trader.
            
            Evaluate every single ticker provided below. Perform a thorough analysis by combining:
            1. Advanced Technical Indicators: ADX trend strength (>25 indicates strong trend), +DI vs -DI direction, price vs VWAP benchmark, CMF institutional accumulation, and RSI momentum.
            2. Macro & Sector Insights: Current broader market environment, interest rate trends, sector dynamics, and commodity/economic factors affecting Indian equities (NSE).

            INSTRUCTIONS:
            - Compute a comprehensive Quantitative Momentum Score (0-100).
            - Deliver actionable insights on momentum drivers, volume structure, and risk factors.
            - State a definitive investment verdict (e.g. STRONG BUY, ACCUMULATE, HOLD, or AVOID) along with precise technical stop-loss and price target coordinates.

            PAYLOAD DATA:
            {compiled_stock_data_context}
            """

            config = types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=PortfolioAuditPayload,
            )
            
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash', contents=prompt, config=config
            )
            
            result_payload = PortfolioAuditPayload.model_validate_json(response.text)
            for item in result_payload.analyses:
                all_final_rows.append([
                    item.ticker, item.signal_source_list, item.overall_score,
                    item.comprehensive_momentum_drivers, item.institutional_risk_analysis,
                    item.investment_verdict
                ])
                
        except Exception as e:
            print(f"AI Batch Execution Error: {e}")

        if idx < len(stock_chunks):
            time.sleep(10)

    try:
        sheet.clear()
        time.sleep(2)
        headers = [["Stock", "Source Strategy", "Overall Score (/100)", "Comprehensive Momentum Drivers", "Institutional Risk & Volume Analysis", "Investment Verdict & Trade Coordinates"]]
        sheet.update(range_name='A1', values=(headers + all_final_rows))
    except Exception as e:
        print(f"Spreadsheet write error: {e}")

    return all_final_rows

# =====================================================================
# MAIN PIPELINE SCANNER
# =====================================================================
stocks_df = pd.read_csv("nse_stocks.csv")
stocks = [s + ".NS" for s in stocks_df['SYMBOL'].dropna().tolist()]

weekly_buy_scored, monthly_buy_scored = [], []
coiled_spring_scored = [] 
weekly_sell_signals, sell_signals = [], []

ssf_special_weekly = []
ssf_special_monthly = []
ssf_two_weeks_ago = []
ssf_two_months_ago = []

for stock in stocks:
    print(f"Scanning {stock}...")
    try:
        ticker = yf.Ticker(stock)
        now = datetime.now()
        
        raw_w = ticker.history(period=WEEKLY_HISTORY, interval="1wk")
        w_df = raw_w.copy() if (now.weekday() > 4 or (now.weekday() == 4 and now.hour >= 16)) else raw_w.iloc[:-1].copy()

        if len(w_df) >= 300:
            w_close = w_df['Close'].values
            w_df['SSF_20'] = super_smoother(w_close, 20)
            w_df['SSF_50'] = super_smoother(w_close, 50)
            w_df['SSF_100'] = super_smoother(w_close, 100)
            w_df['SSF_200'] = super_smoother(w_close, 200)
            w_df['SSF_250'] = super_smoother(w_close, 250)

            rsi_w = RSIIndicator(w_df['Close'], window=14).rsi()
            rsi_ma_w = rsi_w.rolling(14).mean()
            if (rolling_setup_weekly(w_df, 20) and rolling_cross(w_close, w_df['SSF_50'].values, 6) and 
                rsi_w.iloc[-1] > rsi_ma_w.iloc[-1] and w_df['SSF_50'].iloc[-1] < w_df['SSF_200'].iloc[-1]):
                
                score = rsi_w.iloc[-1] + ((w_close[-1] - w_df['SSF_50'].iloc[-1]) / w_df['SSF_50'].iloc[-1]) * 100
                
                # Fetch SSF 50 Crossover Delta % and Weeks Elapsed
                cross_delta_pct_w, weeks_since = get_crossover_details(w_close, w_df['SSF_50'].values, 6)

                weekly_buy_scored.append((stock, score, cross_delta_pct_w, weeks_since))
                
                dist_from_ssf = (w_close[-1] - w_df['SSF_50'].iloc[-1]) / w_df['SSF_50'].iloc[-1]
                potential_score = rsi_w.iloc[-1] / max(dist_from_ssf, 0.01)
                coiled_spring_scored.append((stock, potential_score))

            if check_ssf_special_weekly(w_df):
                ssf_special_weekly.append(stock)

            if rolling_setup_weekly(w_df, 20) and w_df['SSF_50'].iloc[-1] < w_df['SSF_200'].iloc[-1]:
                if check_ssf_two_weeks_ago_confirmed(w_df):
                    ssf_two_weeks_ago.append(stock)

            if len(w_df) >= 2:
                prev_h = (w_df['Close'].iloc[-2] > w_df['SSF_20'].iloc[-2] and w_df['Close'].iloc[-2] > w_df['SSF_50'].iloc[-2])
                if prev_h and w_df['Close'].iloc[-1] < w_df['SSF_20'].iloc[-1]:
                    weekly_sell_signals.append(stock)

        raw_m = ticker.history(period=MONTHLY_HISTORY, interval="1mo")
        m_df = raw_m.iloc[:-1].copy() if not raw_m.empty else pd.DataFrame()
        
        if len(m_df) >= 300: 
            m_close = m_df['Close'].values
            m_df['SSF_20'] = super_smoother(m_close, 20)
            m_df['SSF_50'] = super_smoother(m_close, 50)
            m_df['SSF_100'] = super_smoother(m_close, 100)
            m_df['SSF_200'] = super_smoother(m_close, 200)
            m_df['SSF_250'] = super_smoother(m_close, 250)
            
            rsi_m = RSIIndicator(m_df['Close'], window=14).rsi()
            rsi_ma_m = rsi_m.rolling(14).mean()
            if (rolling_setup_monthly(m_df, 20) and rolling_cross(m_close, m_df['SSF_50'].values, 6) and 
                rsi_m.iloc[-1] > rsi_ma_m.iloc[-1] and m_df['SSF_50'].iloc[-1] < m_df['SSF_200'].iloc[-1]):
                
                score_m = rsi_m.iloc[-1] + ((m_close[-1] - m_df['SSF_50'].iloc[-1]) / m_df['SSF_50'].iloc[-1]) * 100
                
                # Fetch SSF 50 Crossover Delta % and Months Elapsed
                cross_delta_pct_m, months_since = get_crossover_details(m_close, m_df['SSF_50'].values, 6)

                monthly_buy_scored.append((stock, score_m, cross_delta_pct_m, months_since))
            
            if check_ssf_special_monthly(m_df):
                ssf_special_monthly.append(stock)

            if rolling_setup_monthly(m_df, 20) and m_df['SSF_50'].iloc[-1] < m_df['SSF_200'].iloc[-1]:
                if check_ssf_two_weeks_ago_confirmed(m_df):
                    ssf_two_months_ago.append(stock)

            if len(m_df) >= 2:
                # Corrected scoping bug: m_df used for both SSF_20 and SSF_50
                prev_h_m = (m_df['Close'].iloc[-2] > m_df['SSF_20'].iloc[-2] and m_df['Close'].iloc[-2] > m_df['SSF_50'].iloc[-2])
                if prev_h_m and m_df['Close'].iloc[-1] < m_df['SSF_20'].iloc[-1]:
                    sell_signals.append(stock)
    except Exception:
        continue

# Sort and bucket strategy rankings
weekly_buy_scored = sorted(weekly_buy_scored, key=lambda x: x[1], reverse=True)
cross_delta_map_weekly = {item[0]: (item[2], item[3]) for item in weekly_buy_scored}
top_weekly, rest_weekly = [x[0] for x in weekly_buy_scored[:5]], [x[0] for x in weekly_buy_scored[5:]]

monthly_buy_scored = sorted(monthly_buy_scored, key=lambda x: x[1], reverse=True)
cross_delta_map_monthly = {item[0]: (item[2], item[3]) for item in monthly_buy_scored}
top_monthly, rest_monthly = [x[0] for x in monthly_buy_scored[:5]], [x[0] for x in monthly_buy_scored[5:]]

coiled_spring_top = [x[0] for x in sorted(coiled_spring_scored, key=lambda x: x[1], reverse=True)[:10]]

# Wipe & Re-write Dynamic Scanner Output Tabs
update_sheet("Top_Weekly", top_weekly, delta_map=cross_delta_map_weekly, time_frame_label="Weeks")
update_sheet("Rest_Weekly", rest_weekly, delta_map=cross_delta_map_weekly, time_frame_label="Weeks")
update_sheet("Top_Monthly", top_monthly, delta_map=cross_delta_map_monthly, time_frame_label="Months")
update_sheet("Rest_Monthly", rest_monthly, delta_map=cross_delta_map_monthly, time_frame_label="Months")
update_sheet("Coiled_Spring_Top", coiled_spring_top)
update_sheet("SSF_Special_Weekly", ssf_special_weekly)
update_sheet("SSF_Special_Monthly", ssf_special_monthly)
update_sheet("SSF_Two_Weeks_Ago", ssf_two_weeks_ago)
update_sheet("SSF_Two_Months_Ago", ssf_two_months_ago)
simple_update("Weekly_Sell", weekly_sell_signals)
simple_update("Sell_Signals", sell_signals)

# Safe Update of Portfolio Tracker
update_portfolio_tracker()

# Run AI Deep Dive Batch Audit
unified_pool = list(set(top_weekly + rest_weekly + ssf_two_weeks_ago))
batch_ai_results = run_batch_portfolio_ai_audit(
    unified_stock_list=unified_pool,
    top_w=top_weekly,
    rest_w=rest_weekly,
    ssf_2w=ssf_two_weeks_ago
)

# =====================================================================
# INDIVIDUAL TELEGRAM NOTIFICATIONS
# =====================================================================
send_telegram_message(f"🚀 Top Weekly Buy:\n{top_weekly}")
time.sleep(1)
send_telegram_message(f"📈 Rest Weekly Buy:\n{rest_weekly}")
time.sleep(1)
send_telegram_message(f"🌟 Top Monthly Buy:\n{top_monthly}")
time.sleep(1)
send_telegram_message(f"📊 Rest Monthly Buy:\n{rest_monthly}")
time.sleep(1)
send_telegram_message(f"⚡ Coiled Spring Top 10:\n{coiled_spring_top}")
time.sleep(1)
send_telegram_message(f"🔥 SSF Special Weekly:\n{ssf_special_weekly}")
time.sleep(1)
send_telegram_message(f"💥 SSF Special Monthly:\n{ssf_special_monthly}")
time.sleep(1)
send_telegram_message(f"⏳ SSF Two Weeks Ago Confirmed:\n{ssf_two_weeks_ago}")
time.sleep(1)
send_telegram_message(f"⌛ SSF Two Months Ago Confirmed:\n{ssf_two_months_ago}")
time.sleep(1)
send_telegram_message(f"⚠️ Weekly Sell Signals:\n{weekly_sell_signals}")
time.sleep(1)
send_telegram_message(f"🚨 Monthly Sell Signals:\n{sell_signals}")

print("Process Completed Successfully.")
