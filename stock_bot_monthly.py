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
import ta
from ta.momentum import RSIIndicator, AwesomeOscillatorIndicator
from ta.volatility import BollingerBands
from ta.volume import ChaikinMoneyFlowIndicator
from ta.trend import ADXIndicator, MACD

# =====================================================================
# SETTINGS & CONSTRAINTS
# =====================================================================
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
    n = len(close)
    lookback = min(max_lookback, n - 1)
    
    for i in range(1, lookback):
        if close[-i - 1] < ssf[-i - 1] and close[-i] > ssf[-i]:
            ssf_at_cross = ssf[-i]
            bars_since = i - 1
            delta_pct = ((close[-1] - ssf_at_cross) / ssf_at_cross) * 100
            return delta_pct, bars_since

    if close[-1] > ssf[-1]:
        for i in range(1, lookback):
            if close[-i] < ssf[-i]:
                ssf_at_cross = ssf[-i + 1]
                bars_since = i - 1
                delta_pct = ((close[-1] - ssf_at_cross) / ssf_at_cross) * 100
                return delta_pct, bars_since
        
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

def check_macd_monthly_below_zero(df, lookback=3):
    if len(df) < 35: return False
    macd_ind = MACD(close=df['Close'], window_slow=26, window_fast=12, window_sign=9)
    macd_line = macd_ind.macd()
    signal_line = macd_ind.macd_signal()
    
    for i in range(1, lookback + 1):
        prev_idx = -i - 1
        curr_idx = -i
        if macd_line.iloc[prev_idx] < signal_line.iloc[prev_idx] and macd_line.iloc[curr_idx] > signal_line.iloc[curr_idx]:
            if macd_line.iloc[curr_idx] < 0 and signal_line.iloc[curr_idx] < 0:
                return True
    return False

def check_macd_weekly_below_zero(df, lookback=3):
    if len(df) < 35: return False
    macd_ind = MACD(close=df['Close'], window_slow=26, window_fast=12, window_sign=9)
    macd_line = macd_ind.macd()
    signal_line = macd_ind.macd_signal()
    
    for i in range(1, lookback + 1):
        prev_idx = -i - 1
        curr_idx = -i
        if macd_line.iloc[prev_idx] < signal_line.iloc[prev_idx] and macd_line.iloc[curr_idx] > signal_line.iloc[curr_idx]:
            if macd_line.iloc[curr_idx] < 0 and signal_line.iloc[curr_idx] < 0:
                return True
    return False

# =====================================================================
# TECHNICAL AUDIT DATA EXTRACTION
# =====================================================================
def get_audit_data(stock_symbol, local_df):
    try:
        local_df = local_df.dropna(subset=['Close', 'High', 'Low', 'Volume'])
        if local_df.empty: raise ValueError("Empty DataFrame")

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

        tp = (local_df['High'] + local_df['Low'] + local_df['Close']) / 3
        vwap_series = (tp * local_df['Volume']).rolling(20).sum() / local_df['Volume'].rolling(20).sum()
        current_vwap = vwap_series.iloc[-1] if not np.isnan(vwap_series.iloc[-1]) else current_close
        vwap_delta_pct = ((current_close - current_vwap) / current_vwap) * 100

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
            get_verdict, bandwidth, ao, current_cmf, round(adx_val, 2), round(p_di, 2), round(n_di, 2), 
            round(current_vwap, 2), f"{vwap_delta_pct:.2f}%"
        ]
    except Exception:
        return ["N/A", "N/A", "N/A", "ERROR", 0, 0, 0, 0, 0, 0, 0, "0.00%"]

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": message}, timeout=10)
    except Exception: pass

# =====================================================================
# GOOGLE SHEETS CONNECTOR & UPDATERS
# =====================================================================
creds = Credentials.from_service_account_file("credentials.json", scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
client_gspread = gspread.authorize(creds)
spreadsheet = client_gspread.open(SPREADSHEET_NAME)

def clean_val(val):
    try: return float(val.split(' ')[0].replace('%', ''))
    except Exception: return 0.0

def update_sheet(sheet_name, data_list, delta_map=None, time_frame_label="Weeks"):
    try: sheet = spreadsheet.worksheet(sheet_name)
    except Exception: sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=15)
    
    prev_rows = sheet.get_all_values()
    history = {}
    if len(prev_rows) > 1:
        for idx, row in enumerate(prev_rows[1:], start=1):
            if len(row) >= 4 and row[0]: 
                history[row[0]] = {"rank": idx, "bw": clean_val(row[1]), "ao": clean_val(row[2]), "cmf": clean_val(row[3])}

    sheet.clear()
    time.sleep(2)
    headers = [["Stock", "SSF 50 Cross Delta %", f"{time_frame_label} Since Cross", "Volatility (Squeeze)", "Momentum (AO %)", "Institutional (CMF)", "VWAP Delta %", "Rank Delta", "BW Delta", "AO Delta", "CMF Delta", "BOT VERDICT"]]
    rows = []
    
    if not data_list:
        sheet.update(range_name='A1', values=[["No Stocks"]])
        return

    is_monthly = (time_frame_label.lower() == "months")
    interval = "1mo" if is_monthly else "1wk"

    for current_rank, stock in enumerate(data_list, start=1):
        clean_stock = stock.strip().replace(".NS", "").replace(".BO", "")
        ticker = yf.Ticker(f"{clean_stock}.NS")
        df = ticker.history(period="5y" if is_monthly else "1y", interval=interval)
        
        if not df.empty:
            df = df.dropna(subset=['Close'])
            audit = get_audit_data(stock, df)
            
            if delta_map and stock in delta_map:
                c_delta_pct, bars_elapsed = delta_map[stock]
                c_delta_str = f"{c_delta_pct:.2f}%"
                bars_str = f"{bars_elapsed}"
            else:
                if len(df) >= 50:
                    close_arr = df['Close'].values
                    ssf_50 = super_smoother(close_arr, 50)
                    c_delta_pct, bars_elapsed = get_crossover_details(close_arr, ssf_50, max_lookback=50)
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
                stock, c_delta_str, bars_str, audit[0], audit[1], audit[2], audit[11],
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

def update_macd_dual_confirmed_sheet(dual_stocks):
    sheet_name = "MACD_Dual_Confirmed"
    try: sheet = spreadsheet.worksheet(sheet_name)
    except Exception: sheet = spreadsheet.add_worksheet(title=sheet_name, rows=500, cols=8)
    
    sheet.clear()
    time.sleep(2)

    headers = [["Stock", "Current Price", "Weekly MACD", "Monthly MACD", "Status", "Vol Squeeze", "Inst CMF", "Last Updated"]]
    if not dual_stocks:
        sheet.update(range_name='A1', values=[["No Dual MACD Confirmed Stocks Found"]])
        return

    rows = []
    for stock in dual_stocks:
        clean_stock = stock.strip().replace(".NS", "").replace(".BO", "")
        ticker = yf.Ticker(f"{clean_stock}.NS")
        df = ticker.history(period="1y", interval="1wk")
        
        curr_price_str = "N/A"
        audit = ["N/A", "N/A", "N/A", "N/A"]
        
        if not df.empty:
            df = df.dropna(subset=['Close'])
            curr_price = df['Close'].iloc[-1]
            curr_price_str = f"INR {curr_price:.2f}"
            audit = get_audit_data(stock, df)

        rows.append([
            stock, 
            curr_price_str, 
            "BULLISH (BUY)", 
            "BULLISH (BUY)", 
            "🎯 BUY CONFIRMED", 
            audit[0], 
            audit[2], 
            datetime.now().strftime("%Y-%m-%d %H:%M")
        ])

    sheet.update(range_name='A1', values=(headers + rows))
    
    format_requests = [{
        "range": f"A2:H{len(rows)+1}", 
        "format": {"backgroundColor": {"red": 0.8, "green": 1.0, "blue": 0.8}, "textFormat": {"bold": True}}
    }]
    try:
        sheet.batch_format(format_requests)
    except Exception:
        pass

# =====================================================================
# PORTFOLIO TRACKERS
# =====================================================================
def update_portfolio_tracker():
    headers = [["Stock", "Current Price", "SSF_20 Level", "SSF_20 Breach Status", "Action", "Last Updated"]]
    try: sheet = spreadsheet.worksheet("Portfolio_Tracker")
    except Exception:
        sheet = spreadsheet.add_worksheet(title="Portfolio_Tracker", rows=500, cols=6)
        sheet.update(range_name='A1', values=headers)
        return

    existing_rows = sheet.get_all_values()
    if not existing_rows or len(existing_rows) <= 1:
        sheet.update(range_name='A1', values=headers)
        return

    updated_rows, red_rows = [], []
    for idx, row in enumerate(existing_rows[1:], start=2):
        if not row or not row[0].strip(): continue
        raw_stock = row[0].strip()
        clean_stock = raw_stock.replace(".NS", "").replace(".BO", "")
        stock_symbol = f"{clean_stock}.NS"

        try:
            df = yf.Ticker(stock_symbol).history(period="1y", interval="1wk")
            if not df.empty: df = df.dropna(subset=['Close'])

            if len(df) >= 20:
                close_arr = df['Close'].values
                ssf_20 = super_smoother(close_arr, 20)
                curr_close, curr_ssf20 = close_arr[-1], ssf_20[-1]
                prev_close, prev_ssf20 = close_arr[-2], ssf_20[-2]

                if np.isnan(curr_close) or np.isnan(curr_ssf20): raise ValueError("NaN detected")

                if prev_close > prev_ssf20 and curr_close < curr_ssf20:
                    status, action = "⚠️ BREACHED SSF_20", "SELL / EXIT NOW"
                    red_rows.append(idx)
                elif curr_close < curr_ssf20:
                    status, action = "BELOW SSF_20", "HOLD CASH / EXIT"
                    red_rows.append(idx)
                else:
                    status, action = "ABOVE SSF_20", "HOLD POSITION"

                updated_rows.append([raw_stock, f"INR {curr_close:.2f}", f"INR {curr_ssf20:.2f}", status, action, datetime.now().strftime("%Y-%m-%d %H:%M")])
            else:
                updated_rows.append([raw_stock, "NO DATA", "NO DATA", "INSUFFICIENT HISTORY", "NONE", datetime.now().strftime("%Y-%m-%d %H:%M")])
        except Exception:
            updated_rows.append([raw_stock, "ERROR", "ERROR", "FETCH FAILED", "NONE", datetime.now().strftime("%Y-%m-%d %H:%M")])

    sheet.update(range_name=f'A2:F{len(updated_rows)+1}', values=updated_rows)
    if red_rows:
        try:
            format_requests = [{"range": f"A{r}:F{r}", "format": {"backgroundColor": {"red": 1.0, "green": 0.8, "blue": 0.8}, "textFormat": {"bold": True}}} for r in red_rows]
            sheet.batch_format(format_requests)
        except Exception: pass

def update_portfolio_tracker_monthly():
    headers = [["Stock", "Current Price", "Monthly SSF_20 Level", "Weekly MACD Status", "Monthly MACD Status", "Action", "Last Updated"]]
    try: sheet = spreadsheet.worksheet("Portfolio_Tracker_Monthly")
    except Exception:
        sheet = spreadsheet.add_worksheet(title="Portfolio_Tracker_Monthly", rows=500, cols=7)
        sheet.update(range_name='A1', values=headers)
        return

    existing_rows = sheet.get_all_values()
    if not existing_rows or len(existing_rows) <= 1:
        sheet.update(range_name='A1', values=headers)
        return

    updated_rows = []
    red_rows = []
    green_rows = []

    for idx, row in enumerate(existing_rows[1:], start=2):
        if not row or not row[0].strip(): continue
        raw_stock = row[0].strip()
        clean_stock = raw_stock.replace(".NS", "").replace(".BO", "")
        stock_symbol = f"{clean_stock}.NS"

        try:
            t = yf.Ticker(stock_symbol)
            
            # Fetch Monthly Data
            m_df = t.history(period="5y", interval="1mo")
            if m_df.empty or len(m_df) < 35: raise ValueError("Insufficient monthly data")
            m_df = m_df.dropna(subset=['Close']).copy()

            close_arr = m_df['Close'].values
            ssf_20 = super_smoother(close_arr, 20)
            curr_close, curr_ssf20 = close_arr[-1], ssf_20[-1]
            prev_close, prev_ssf20 = close_arr[-2], ssf_20[-2]

            # Monthly MACD
            m_macd_ind = MACD(close=m_df['Close'], window_slow=26, window_fast=12, window_sign=9)
            monthly_macd_buy = (m_macd_ind.macd().iloc[-1] > m_macd_ind.macd_signal().iloc[-1])
            m_macd_status = "BULLISH (BUY)" if monthly_macd_buy else "BEARISH"

            # Fetch Weekly Data
            w_df = t.history(period="2y", interval="1wk")
            if w_df.empty or len(w_df) < 35: raise ValueError("Insufficient weekly data")
            w_df = w_df.dropna(subset=['Close']).copy()

            now = datetime.now()
            if not (now.weekday() > 4 or (now.weekday() == 4 and now.hour >= 16)):
                w_df = w_df.iloc[:-1].copy()

            # Weekly MACD
            w_macd_ind = MACD(close=w_df['Close'], window_slow=26, window_fast=12, window_sign=9)
            weekly_macd_buy = (w_macd_ind.macd().iloc[-1] > w_macd_ind.macd_signal().iloc[-1])
            w_macd_status = "BULLISH (BUY)" if weekly_macd_buy else "BEARISH"

            # SSF 20 Sell Signal Checks
            if prev_close > prev_ssf20 and curr_close < curr_ssf20:
                status_action = "MACRO EXIT NOW"
                red_rows.append(idx)
            elif curr_close < curr_ssf20:
                status_action = "BEAR MARKET / HOLD CASH"
                red_rows.append(idx)
            elif monthly_macd_buy and weekly_macd_buy:
                status_action = "🎯 BUY (DUAL CONFIRMED)"
                green_rows.append(idx)
            else:
                status_action = "STRONG MACRO TREND"

            updated_rows.append([
                raw_stock, 
                f"INR {curr_close:.2f}", 
                f"INR {curr_ssf20:.2f}", 
                w_macd_status, 
                m_macd_status, 
                status_action, 
                datetime.now().strftime("%Y-%m-%d %H:%M")
            ])
        except Exception:
            updated_rows.append([raw_stock, "ERROR", "ERROR", "FETCH FAILED", "FETCH FAILED", "NONE", datetime.now().strftime("%Y-%m-%d %H:%M")])

    sheet.update(range_name=f'A2:G{len(updated_rows)+1}', values=updated_rows)

    format_requests = []
    for r in red_rows:
        format_requests.append({
            "range": f"A{r}:G{r}", 
            "format": {"backgroundColor": {"red": 1.0, "green": 0.8, "blue": 0.8}, "textFormat": {"bold": True}}
        })
    for g in green_rows:
        format_requests.append({
            "range": f"A{g}:G{g}", 
            "format": {"backgroundColor": {"red": 0.8, "green": 1.0, "blue": 0.8}, "textFormat": {"bold": True}}
        })

    if format_requests:
        try:
            sheet.batch_format(format_requests)
        except Exception:
            pass

# =====================================================================
# MAIN PIPELINE SCANNER
# =====================================================================
stocks_df = pd.read_csv("nse_stocks.csv")
stocks = [s.strip().replace(".NS", "").replace(".BO", "") + ".NS" for s in stocks_df['SYMBOL'].dropna().tolist()]

weekly_buy_scored, monthly_buy_scored = [], []
coiled_spring_scored = [] 
weekly_sell_signals, sell_signals = [], []

ssf_special_weekly = []
ssf_special_monthly = []
ssf_two_weeks_ago = []
ssf_two_months_ago = []
macd_monthly_below_zero = []
macd_dual_confirmed_stocks = []
macd_dual_below_zero_confirmed_stocks = []

for stock in stocks:
    print(f"Scanning {stock}...")
    try:
        ticker = yf.Ticker(stock)
        now = datetime.now()
        
        raw_w = ticker.history(period=WEEKLY_HISTORY, interval="1wk")
        w_df = raw_w.copy() if (now.weekday() > 4 or (now.weekday() == 4 and now.hour >= 16)) else raw_w.iloc[:-1].copy()

        if not w_df.empty:
            w_df = w_df.dropna(subset=['Close'])

        # Check Weekly MACD
        w_macd_buy = False
        w_macd_below_zero_buy = False
        if len(w_df) >= 35:
            w_macd_ind = MACD(close=w_df['Close'], window_slow=26, window_fast=12, window_sign=9)
            w_macd_buy = (w_macd_ind.macd().iloc[-1] > w_macd_ind.macd_signal().iloc[-1])
            w_macd_below_zero_buy = check_macd_weekly_below_zero(w_df, lookback=3)

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
        
        if not m_df.empty:
            m_df = m_df.dropna(subset=['Close'])

        # Check Monthly MACD & Dual Confirmation
        m_macd_buy = False
        m_macd_below_zero_buy = False
        if len(m_df) >= 35:
            m_macd_ind = MACD(close=m_df['Close'], window_slow=26, window_fast=12, window_sign=9)
            m_macd_buy = (m_macd_ind.macd().iloc[-1] > m_macd_ind.macd_signal().iloc[-1])

            m_macd_below_zero_buy = check_macd_monthly_below_zero(m_df, lookback=3)
            if m_macd_below_zero_buy:
                macd_monthly_below_zero.append(stock)

        # Append to Dual Confirmed list if BOTH are true
        if w_macd_buy and m_macd_buy:
            macd_dual_confirmed_stocks.append(stock)

        # Append to Dual Below Zero Confirmed list if BOTH Monthly and Weekly MACD Below Zero conditions are true
        if m_macd_below_zero_buy and w_macd_below_zero_buy:
            macd_dual_below_zero_confirmed_stocks.append(stock)

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
                cross_delta_pct_m, months_since = get_crossover_details(m_close, m_df['SSF_50'].values, 6)

                monthly_buy_scored.append((stock, score_m, cross_delta_pct_m, months_since))
            
            if check_ssf_special_monthly(m_df):
                ssf_special_monthly.append(stock)

            if rolling_setup_monthly(m_df, 20) and m_df['SSF_50'].iloc[-1] < m_df['SSF_200'].iloc[-1]:
                if check_ssf_two_weeks_ago_confirmed(m_df):
                    ssf_two_months_ago.append(stock)

            if len(m_df) >= 2:
                prev_h_m = (m_df['Close'].iloc[-2] > m_df['SSF_20'].iloc[-2] and m_df['Close'].iloc[-2] > w_df['SSF_50'].iloc[-2])
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

# Update Output Tabs
update_sheet("Top_Weekly", top_weekly, delta_map=cross_delta_map_weekly, time_frame_label="Weeks")
update_sheet("Rest_Weekly", rest_weekly, delta_map=cross_delta_map_weekly, time_frame_label="Weeks")
update_sheet("Top_Monthly", top_monthly, delta_map=cross_delta_map_monthly, time_frame_label="Months")
update_sheet("Rest_Monthly", rest_monthly, delta_map=cross_delta_map_monthly, time_frame_label="Months")
update_sheet("Coiled_Spring_Top", coiled_spring_top, time_frame_label="Weeks")
update_sheet("SSF_Special_Weekly", ssf_special_weekly, time_frame_label="Weeks")
update_sheet("SSF_Special_Monthly", ssf_special_monthly, time_frame_label="Months")
update_sheet("SSF_Two_Weeks_Ago", ssf_two_weeks_ago, time_frame_label="Weeks")
update_sheet("SSF_Two_Months_Ago", ssf_two_months_ago, time_frame_label="Months")
update_sheet("MACD_Monthly_Below_Zero", macd_monthly_below_zero, time_frame_label="Months")
update_sheet("MACD_Dual_Below_Zero_Confirmed", macd_dual_below_zero_confirmed_stocks, time_frame_label="Weeks")

# Dedicated Dual MACD Confirmed Sheet Update
update_macd_dual_confirmed_sheet(macd_dual_confirmed_stocks)

simple_update("Weekly_Sell", weekly_sell_signals)
simple_update("Sell_Signals", sell_signals)

# Safe Update of Portfolio Trackers
update_portfolio_tracker()
update_portfolio_tracker_monthly()

# Telegram Output
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
send_telegram_message(f"📉 MACD Monthly Bullish Cross (Below Zero):\n{macd_monthly_below_zero}")
time.sleep(1)
send_telegram_message(f"🎯 Dual MACD Confirmed (Weekly + Monthly Buy):\n{macd_dual_confirmed_stocks}")
time.sleep(1)
send_telegram_message(f"🎯 MACD Dual Below Zero Confirmed (Weekly + Monthly < 0):\n{macd_dual_below_zero_confirmed_stocks}")
time.sleep(1)
send_telegram_message(f"⚠️ Weekly Sell Signals:\n{weekly_sell_signals}")
time.sleep(1)
send_telegram_message(f"🚨 Monthly Sell Signals:\n{sell_signals}")

print("Process Completed Successfully.")
