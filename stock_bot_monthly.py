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
from ta.momentum import RSIIndicator, AwesomeOscillatorIndicator
from ta.volatility import BollingerBands
from ta.volume import ChaikinMoneyFlowIndicator, OnBalanceVolumeIndicator
from ta.trend import MACD

# --- GOOGLE-GENAI SDK INITIALIZATION ---
try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Error: Please make sure 'google-genai' is listed in your stock_bot.yml workflow install list.")
    sys.exit(1)

# --- SETTINGS ---
MARKET_CAP_LIMIT = 5000 * 10**7
MONTHLY_HISTORY = "max"
WEEKLY_HISTORY = "max"
SPREADSHEET_NAME = "Stock Bot Dashboard"
TELEGRAM_TOKEN = "8630503074:AAHgONEVwJB_QVZ1GeKBaVGl9Z3Ct0E_yLw"
CHAT_ID = "8258280498"

# --- CORE MATH FUNCTIONS ---
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

# --- SSF SPECIAL LOGIC FUNCTIONS ---
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

def check_ssf_two_months_ago_confirmed(df):
    if len(df) < 5: return False
    crossed_two_months_ago = (df['Close'].iloc[-4] < df['SSF_50'].iloc[-4]) and (df['Close'].iloc[-3] > df['SSF_50'].iloc[-3])
    remained_above = (df['Close'].iloc[-2] > df['SSF_50'].iloc[-2]) and (df['Close'].iloc[-1] > df['SSF_50'].iloc[-1])
    return crossed_two_months_ago and remained_above

# --- AUDIT HELPER (NORMALIZED AWESOME OSCILLATOR) ---
def get_audit_data(stock_symbol, local_df):
    try:
        local_df = local_df.dropna(subset=['Close', 'High', 'Low'])
        cmf_series = ChaikinMoneyFlowIndicator(high=local_df['High'], low=local_df['Low'], close=local_df['Close'], volume=local_df['Volume'], window=20).chaikin_money_flow()
        current_cmf = cmf_series.iloc[-1] if not np.isnan(cmf_series.iloc[-1]) else cmf_series.iloc[-2]
        
        ao_series = AwesomeOscillatorIndicator(high=local_df['High'], low=local_df['Low']).awesome_oscillator()
        raw_ao = ao_series.iloc[-1] if not np.isnan(ao_series.iloc[-1]) else ao_series.iloc[-2]
        current_close = local_df['Close'].iloc[-1]
        ao = (raw_ao / current_close) * 100

        bb = BollingerBands(close=local_df['Close'])
        bw_series = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()
        bandwidth = bw_series.iloc[-1] if not np.isnan(bw_series.iloc[-1]) else bw_series.iloc[-2]
        
        bandwidth = 0.0 if np.isnan(bandwidth) else bandwidth
        ao = 0.0 if np.isnan(ao) else ao
        current_cmf = 0.0 if np.isnan(current_cmf) else current_cmf

        sq_label = "READY" if bandwidth < 0.18 else "LOOSE"
        mo_label = "BULLISH" if ao > 0 else "BEARISH"
        inst_label = "BUYING" if current_cmf > 0.05 else ("EXITING" if current_cmf < -0.05 else "NEUTRAL")
        
        if bandwidth < 0.18 and ao > 0 and current_cmf > 0.05:
            verdict = "⭐ EXCELLENT"
        elif current_cmf < -0.07:
            verdict = "⛔ DANGEROUS"
        else:
            verdict = "WATCH"

        return [f"{bandwidth:.4f} ({sq_label})", f"{ao:.4f}% ({mo_label})", f"{current_cmf:.4f} ({inst_label})", verdict, bandwidth, ao, current_cmf]
    except: return ["N/A", "N/A", "N/A", "ERROR", 0, 0, 0]

# --- PREDICTIVE ENGINE ---
def get_predictive_signal(stock_symbol, local_df):
    try:
        local_df = local_df.dropna(subset=['Close'])
        cmf_s = ChaikinMoneyFlowIndicator(high=local_df['High'], low=local_df['Low'], close=local_df['Close'], volume=local_df['Volume'], window=20).chaikin_money_flow()
        current_cmf = cmf_s.iloc[-1] if not np.isnan(cmf_s.iloc[-1]) else cmf_s.iloc[-2]
        ao_s = AwesomeOscillatorIndicator(high=local_df['High'], low=local_df['Low']).awesome_oscillator()
        raw_ao = ao_s.iloc[-1] if not np.isnan(ao_s.iloc[-1]) else ao_s.iloc[-2]
        ao = (raw_ao / local_df['Close'].iloc[-1]) * 100
        
        bb = BollingerBands(close=local_df['Close'])
        bw_s = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()
        bandwidth = bw_s.iloc[-1] if not np.isnan(bw_s.iloc[-1]) else bw_s.iloc[-2]
        if np.isnan(ao) or np.isnan(bandwidth) or np.isnan(current_cmf): return "HOLD", 0
        up_score = 0
        if bandwidth < 0.18: up_score += 1
        if ao > 0: up_score += 1
        if current_cmf > 0.05: up_score += 1
        down_score = 0
        if bandwidth < 0.18: down_score += 1
        if ao < 0: down_score += 1
        if current_cmf < -0.05: down_score += 1
        if up_score >= 2:
            return "PREDICT_UP", (up_score * 100) + ((1/max(bandwidth, 0.001)) * ao)
        if down_score >= 2:
            return "PREDICT_DOWN", (down_score * 100) + ((1/max(bandwidth, 0.001)) * abs(ao))
        return "HOLD", 0
    except: return "HOLD", 0

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": message})
    except: pass

# --- MAIN ENGINE INITIALIZATION ---
creds = Credentials.from_service_account_file("credentials.json", scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
client_gspread = gspread.authorize(creds)
spreadsheet = client_gspread.open(SPREADSHEET_NAME)

def clean_val(val):
    try: return float(val.split(' ')[0].replace('%', ''))
    except: return 0.0

def update_sheet(sheet_name, data_list):
    try: sheet = spreadsheet.worksheet(sheet_name)
    except: sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=12)
    
    prev_rows = sheet.get_all_values()
    history = {}
    
    if len(prev_rows) > 1:
        for idx, row in enumerate(prev_rows[1:], start=1):
            if len(row) >= 4 and row[0]: 
                history[row[0]] = {"rank": idx, "bw": clean_val(row[1]), "ao": clean_val(row[2]), "cmf": clean_val(row[3])}

    sheet.clear()
    headers = [["Stock", "Volatility (Squeeze)", "Momentum (AO %)", "Institutional (CMF)", "Rank Delta", "BW Delta", "AO Delta", "CMF Delta", "BOT VERDICT"]]
    rows = []
    
    if not data_list:
        sheet.update(range_name='A1', values=[["No Stocks"]])
        return

    for current_rank, stock in enumerate(data_list, start=1):
        ticker = yf.Ticker(stock)
        df = ticker.history(period="1y", interval="1wk")
        if not df.empty:
            audit = get_audit_data(stock, df)
            
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
                stock, audit[0], audit[1], audit[2], 
                r_str, f"{bw_delta:.4f}", f"{ao_delta:.4f}%", f"{cmf_delta:.4f}", 
                audit[3]
            ])
    
    sheet.update(range_name='A1', values=(headers + rows))

def simple_update(sheet_name, data_list):
    try: sheet = spreadsheet.worksheet(sheet_name)
    except: sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=10)
    sheet.clear()
    headers = [["Stock"]]
    rows = [[s] for s in data_list]
    sheet.update(range_name='A1', values=(headers + rows))

# --- DESIGNED STRUCTURAL MAPPING BLUEPRINT ---
class SwingMomentumAnalysis(BaseModel):
    ticker: str = Field(description="The ticker symbol of the stock")
    signal_source_list: str = Field(description="Strategy sheet origin list tags")
    verdict: str = Field(description="MUST BE EXACTLY ONE OF: 'SUSTAINED-MOMENTUM' or 'FAKE-BREAKOUT-TRAP'")
    momentum_drivers: str = Field(description="Comprehensive technical analysis breakdown combining current price velocity trends, RSI runway space, and relative volume confirmation details vs 20-week averages.")
    risk_mitigation: str = Field(description="Exhaustive evaluation of institutional flow metrics (CMF/OBV trends) explaining why velocity will hold structural support or fail right back beneath the SSF 50 line.")
    structural_stop_loss: str = Field(description="Logical protection price floor coordinate")
    target_profit: str = Field(description="Calculated logical profit taking target level")

class PortfolioAuditPayload(BaseModel):
    analyses: list[SwingMomentumAnalysis]

# =====================================================================
# SYSTEM OVERHAUL: FAULT-TOLERANT EXPLICIT CONTINUOUS BATCH RUNNER
# =====================================================================
def run_batch_portfolio_ai_audit(unified_stock_list, top_w, rest_w, ssf_2w):
    if not unified_stock_list:
        print("Unified pool empty. Exiting.")
        return []

    try: sheet = spreadsheet.worksheet("AI_SSF_2Weeks_Deep_Dive")
    except: sheet = spreadsheet.add_worksheet(title="AI_SSF_2Weeks_Deep_Dive", rows=1000, cols=7)
    
    sheet.clear()
    headers = [["Stock", "Source Strategy", "AI Swing Verdict", "Comprehensive Momentum Drivers", "Institutional Risk Mitigation Analysis", "Technical Stop-Loss", "Structural Profit Target"]]
    sheet.update(range_name='A1', values=headers)
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key == "YOUR_FREE_GEMINI_API_KEY":
        sheet.update(range_name='A1', values=[["API Key missing in environment secrets"]])
        return []

    ai_client = genai.Client(api_key=api_key)
    total_processed_results = []
    
    CHUNK_SIZE = 5
    stock_chunks = [unified_stock_list[i:i + CHUNK_SIZE] for i in range(0, len(unified_stock_list), CHUNK_SIZE)]

    print(f"Isolated continuous runner processing {len(unified_stock_list)} stocks across {len(stock_chunks)} chunks...")

    for idx, chunk in enumerate(stock_chunks, start=1):
        # The entire execution loop structure is sealed inside this block.
        # It cannot break out or quietly terminate early anymore.
        try:
            print(f"Starting processing block for Batch {idx}/{len(stock_chunks)}...")
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
                    
                    obv_indicator = OnBalanceVolumeIndicator(close=df['Close'], volume=df['Volume'])
                    obv_series = obv_indicator.on_balance_volume()
                    obv_trend = "ACCUMULATION_UPTREND" if obv_series.iloc[-1] > obv_series.rolling(10).mean().iloc[-1] else "DISTRIBUTION_DOWNTREND"
                    
                    rsi_vals = RSIIndicator(close=df['Close'], window=14).rsi()
                    rsi_current = rsi_vals.iloc[-1] if not np.isnan(rsi_vals.iloc[-1]) else 50.0
                    macd_obj = MACD(close=df['Close'])
                    macd_hist = macd_obj.macd_diff().iloc[-1]
                    
                    support_52wk = df['Low'].rolling(window=52, min_periods=1).min().iloc[-1]
                    resistance_52wk = df['High'].rolling(window=52, min_periods=1).max().iloc[-1]
                    metrics = get_audit_data(stock, df)
                    
                    compiled_stock_data_context += f"""
                    === STOCK: {stock} (Origin: {source_str}) ===
                    - Close Price: INR {df['Close'].iloc[-1]:.2f}
                    - SSF Metrics: Squeeze Bandwidth={metrics[0]} | AO %={metrics[1]} | CMF={metrics[2]}
                    - VOLUME/ORDERFLOW: Relative Volume Ratio={volume_ratio:.2f}x | OBV Trend Structure={obv_trend} | Net Current Volume={current_volume} (20-Wk Avg: {avg_volume_20:.0f})
                    - Oscillators: 14-Wk RSI={rsi_current:.2f} | MACD Hist={macd_hist:.4f}
                    - Technical Ranges: 52-Wk Low Support=INR {support_52wk:.2f} | 52-Wk High Resistance=INR {resistance_52wk:.2f}
                    """
                except Exception as inner_data_err:
                    print(f"Skipping text compilation for single ticker {stock}: {inner_data_err}")
                    continue

            if not compiled_stock_data_context.strip():
                print(f"Batch {idx} context empty, skipping data frame push.")
                continue

            prompt = f"""
            You are an elite institutional quantitative swing trader and order-flow specialist.
            Analyze this multi-strategy breakout candidates portfolio payload generated via our SSF crossover scanners.
            Your main goal is to protect capital by separating true momentum stocks from fake breakout retail traps.

            STRICT TRADING EVALUATION PROTOCOLS:
            1. SUSTAINED-MOMENTUM: Valid breakout setup. Price action expansion is backed by massive institutional accumulation (Relative Volume Ratio >= 1.50x), OBV shows distinct uptrend accumulation, CMF is positive (>0.05), and RSI has open runway space.
            2. FAKE-BREAKOUT-TRAP: Extreme risk of immediate failure. If price crossed above the SSF line but Relative Volume is thin (<1.0x), OBV is declining, or money flow is negative, identify this as a trap likely to break right back below the SSF 50 line.

            RAW PORTFOLIO PAYLOAD TO PROCESS:
            {compiled_stock_data_context}
            """

            config = types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=PortfolioAuditPayload,
            )
            
            print(f"Sending Batch {idx} over connection to Gemini...")
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash', contents=prompt, config=config
            )
            result_payload = PortfolioAuditPayload.model_validate_json(response.text)
            
            batch_rows = []
            for item in result_payload.analyses:
                row_data = [
                    item.ticker, item.signal_source_list, item.verdict, 
                    item.momentum_drivers, item.risk_mitigation, 
                    item.structural_stop_loss, item.target_profit
                ]
                batch_rows.append(row_data)
                total_processed_results.append(row_data)
            
            if batch_rows:
                sheet.append_rows(batch_rows, value_input_option='RAW')
                print(f"Successfully processed and appended Batch {idx} to sheet.")

        except Exception as batch_level_error:
            # If ANY anomaly occurs during runtime, this block intercepts it,
            # prints the tracking status, and safely forces the loop to continue to the next batch.
            print(f"Execution notice on Batch {idx}: {batch_level_error}. Forcing continuation...")
            pass

        if idx < len(stock_chunks):
            time.sleep(12)

    print(f"Execution lifecycle finished. Total saved: {len(total_processed_results)}")
    return total_processed_results

# =====================================================================
# CORE PIPELINE RUNTIME SCANNERS LOOP
# =====================================================================
stocks_df = pd.read_csv("nse_stocks.csv")
stocks = [s + ".NS" for s in stocks_df['SYMBOL'].dropna().tolist()]

weekly_buy_scored, monthly_buy_scored = [], []
coiled_spring_scored = [] 
weekly_sell_signals, sell_signals = [], []
predictive_up, predictive_down = [] , []

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
            
            p_res, p_rank = get_predictive_signal(stock, w_df)
            if p_res == "PREDICT_UP": predictive_up.append((stock, p_rank))
            elif p_res == "PREDICT_DOWN": predictive_down.append((stock, p_rank))

            rsi_w = RSIIndicator(w_df['Close'], window=14).rsi()
            rsi_ma_w = rsi_w.rolling(14).mean()
            if (rolling_setup_weekly(w_df, 20) and rolling_cross(w_close, w_df['SSF_50'].values, 6) and 
                rsi_w.iloc[-1] > rsi_ma_w.iloc[-1] and w_df['SSF_50'].iloc[-1] < w_df['SSF_200'].iloc[-1]):
                
                info = ticker.info
                if info.get("marketCap", 0) >= MARKET_CAP_LIMIT and info.get("profitMargins", 0) > 0:
                    score = rsi_w.iloc[-1] + ((w_close[-1] - w_df['SSF_50'].iloc[-1]) / w_df['SSF_50'].iloc[-1]) * 100
                    weekly_buy_scored.append((stock, score))
                    
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
        m_df = raw_m.iloc[:-1].copy()
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
                
                info = ticker.info
                if info.get("marketCap", 0) >= MARKET_CAP_LIMIT and info.get("profitMargins", 0) > 0:
                    score_m = rsi_m.iloc[-1] + ((m_close[-1] - m_df['SSF_50'].iloc[-1]) / m_df['SSF_50'].iloc[-1]) * 100
                    monthly_buy_scored.append((stock, score_m))
            
            if check_ssf_special_monthly(m_df):
                ssf_special_monthly.append(stock)

            if rolling_setup_monthly(m_df, 20) and m_df['SSF_50'].iloc[-1] < m_df['SSF_200'].iloc[-1]:
                if check_ssf_two_weeks_ago_confirmed(m_df):
                    ssf_two_months_ago.append(stock)

            if len(m_df) >= 2:
                prev_h_m = (m_df['Close'].iloc[-2] > m_df['SSF_20'].iloc[-2] and m_df['Close'].iloc[-2] > m_df['SSF_50'].iloc[-2])
                if prev_h_m and m_df['Close'].iloc[-1] < m_df['SSF_20'].iloc[-1]:
                    sell_signals.append(stock)
    except: continue

weekly_buy_scored = sorted(weekly_buy_scored, key=lambda x: x[1], reverse=True)
top_weekly, rest_weekly = [x[0] for x in weekly_buy_scored[:5]], [x[0] for x in weekly_buy_scored[5:]]

monthly_buy_scored = sorted(monthly_buy_scored, key=lambda x: x[1], reverse=True)
top_monthly, rest_monthly = [x[0] for x in monthly_buy_scored[:5]], [x[0] for x in monthly_buy_scored[5:]]

coiled_spring_top = [x[0] for x in sorted(coiled_spring_scored, key=lambda x: x[1], reverse=True)[:10]]
predictive_up_list = [x[0] for x in sorted(predictive_up, key=lambda x: x[1], reverse=True)]
predictive_down_list = [x[0] for x in sorted(predictive_down, key=lambda x: x[1], reverse=True)]

# Update standard technical tabs
update_sheet("Top_Weekly", top_weekly)
update_sheet("Rest_Weekly", rest_weekly)
update_sheet("Top_Monthly", top_monthly)
update_sheet("Rest_Monthly", rest_monthly)
update_sheet("Predictive_UP", predictive_up_list)
update_sheet("Predictive_DOWN", predictive_down_list)
update_sheet("Coiled_Spring_Top", coiled_spring_top)
update_sheet("SSF_Special_Weekly", ssf_special_weekly)
update_sheet("SSF_Special_Monthly", ssf_special_monthly)
update_sheet("SSF_Two_Weeks_Ago", ssf_two_weeks_ago)
update_sheet("SSF_Two_Months_Ago", ssf_two_months_ago)
simple_update("Weekly_Sell", weekly_sell_signals)
simple_update("Sell_Signals", sell_signals)

# --- RUNTIME UNIFIED POOL GENERATION ---
unified_pool = list(set(top_weekly + rest_weekly + ssf_two_weeks_ago))

batch_ai_results = run_batch_portfolio_ai_audit(
    unified_stock_list=unified_pool,
    top_w=top_weekly,
    rest_w=rest_weekly,
    ssf_2w=ssf_two_weeks_ago
)

# Dispatch Strategy Comms over Telegram
msg1 = f"🚀 STRATEGY OUTPUTS\n\nTop Weekly Buy:\n{top_weekly}\n\nRest Weekly Buy:\n{rest_weekly}\n\nTop Monthly Buy:\n{top_monthly}\n\nRest Monthly Buy:\n{rest_monthly}\n\nWeekly Sell:\n{weekly_sell_signals}\n\nMonthly Sell:\n{sell_signals}"
msg2 = f"🔮 PREDICTIVE QUANT\n\nPredictive UP:\n{predictive_up_list[:15]}\n\nPredictive DOWN:\n{predictive_down_list[:15]}"
msg3 = f"➰ COILED SPRING (POTENTIAL):\n{coiled_spring_top}"
msg4 = f"📈 SSF SPECIAL SIGNALS\n\nSSF Special Weekly:\n{ssf_special_weekly}\n\nSSF Special Monthly:\n{ssf_special_monthly}"
msg5 = f"⏰ SSF TWO WEEKS AGO:\n{ssf_two_weeks_ago}"
msg6 = f"📅 SSF TWO MONTHS AGO:\n{ssf_two_months_ago}"

send_telegram_message(msg1)
send_telegram_message(msg2)
send_telegram_message(msg3)
send_telegram_message(msg4)
send_telegram_message(msg5)
send_telegram_message(msg6)

print("Process Complete Safely.")
