import os
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# ==========================================
# 1. CONFIGURATION & ENVIRONMENT SETUP
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# Updated default sheet name to match your Google Sheet
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Stock Bot Dashboard")

# Define Google Sheets scope & auth
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
gc = gspread.authorize(creds)

# Open Google Sheet Workbook
spreadsheet = gc.open(GOOGLE_SHEET_NAME)


# ==========================================
# 2. INDICATOR MATHEMATICS (SuperSmoother)
# ==========================================
def super_smoother(df, period=20):
    """
    Ehlers 2-Pole SuperSmoother Filter
    """
    close = df['Close'].values
    f = (np.sqrt(2) * np.pi) / period
    a1 = np.exp(-f)
    b1 = 2 * a1 * np.cos(f)
    c2 = b1
    c3 = -a1 * a1
    c1 = 1 - c2 - c3

    ssf = np.zeros_like(close)
    if len(close) > 2:
        ssf[0] = close[0]
        ssf[1] = close[1]
        for i in range(2, len(close)):
            ssf[i] = c1 * (close[i] + close[i - 1]) / 2 + c2 * ssf[i - 1] + c3 * ssf[i - 2]
    return ssf


def calculate_indicators(df):
    """
    Computes SuperSmoother Filters and essential trend indicators
    """
    df['SSF_20'] = super_smoother(df, period=20)
    df['SSF_50'] = super_smoother(df, period=50)
    df['SSF_200'] = super_smoother(df, period=200)
    return df


# ==========================================
# 3. TELEGRAM ALERT HELPER
# ==========================================
def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing. Skipping notification.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")


# ==========================================
# 4. SHEET WORKFLOW MANAGEMENT
# ==========================================
def get_or_create_worksheet(title):
    try:
        return spreadsheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows="100", cols="20")


def setup_and_clear_strategy_sheets():
    """
    Wipes dynamic strategy sheets so previous outputs are clean,
    leaving Portfolio_Tracker untouched.
    """
    strategy_sheets = ["Top_Weekly", "Rest_Weekly"]
    worksheets = {}
    
    for title in strategy_sheets:
        ws = get_or_create_worksheet(title)
        ws.clear()
        worksheets[title] = ws
        
    # Ensure Portfolio Tracker exists without wiping it
    worksheets["Portfolio_Tracker"] = get_or_create_worksheet("Portfolio_Tracker")
    return worksheets


# ==========================================
# 5. CORE STRATEGY & SCANNING LOGIC
# ==========================================
def scan_symbol(symbol):
    try:
        # Fetch 1 year of daily historical data
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1y")
        if df.empty or len(df) < 200:
            return None

        df = calculate_indicators(df)
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        close = latest['Close']
        ssf20 = latest['SSF_20']
        ssf50 = latest['SSF_50']
        ssf200 = latest['SSF_200']

        # Trend Checks
        is_above_ssf200 = close > ssf200
        is_ssf20_above_ssf50 = ssf20 > ssf50
        fresh_cross_20 = (prev['Close'] <= prev['SSF_20']) and (close > ssf20)

        result = {
            "Symbol": symbol,
            "Close": round(close, 2),
            "SSF_20": round(ssf20, 2),
            "SSF_50": round(ssf50, 2),
            "SSF_200": round(ssf200, 2),
            "Strategy": None
        }

        # Strategy Classifications
        if is_above_ssf200 and is_ssf20_above_ssf50 and fresh_cross_20:
            result["Strategy"] = "Top_Weekly"
        elif is_above_ssf200 and fresh_cross_20:
            result["Strategy"] = "Rest_Weekly"

        return result
    except Exception as e:
        print(f"Error scanning {symbol}: {e}")
        return None


# ==========================================
# 6. PORTFOLIO TRACKER UPDATE LOGIC
# ==========================================
def update_portfolio_tracker(ws_portfolio):
    """
    Reads active stocks from Portfolio_Tracker (Cols A-C),
    updates current prices and stop loss alerts without wiping user inputs.
    """
    records = ws_portfolio.get_all_records()
    if not records:
        print("Portfolio Tracker is empty or missing headers.")
        return

    # Expecting Headers: Symbol | Buy_Price | Quantity | Current_Price | SSF_20 | Status
    for idx, row in enumerate(records, start=2):  # Row 1 is Header
        symbol = str(row.get("Symbol", "")).strip()
        if not symbol:
            continue
            
        try:
            df = yf.Ticker(symbol).history(period="6mo")
            if not df.empty:
                df = calculate_indicators(df)
                latest_close = round(df['Close'].iloc[-1], 2)
                latest_ssf20 = round(df['SSF_20'].iloc[-1], 2)
                
                status = "HOLD"
                if latest_close < latest_ssf20:
                    status = "EXIT TRIGGERED (Below SSF_20)"

                # Prepare updates for columns D, E, F
                ws_portfolio.update(f"D{idx}:F{idx}", [[latest_close, latest_ssf20, status]])
        except Exception as e:
            print(f"Error updating portfolio stock {symbol}: {e}")


# ==========================================
# 7. MAIN EXECUTION PIPELINE
# ==========================================
def main():
    print("Starting Scanner Pipeline...")
    
    # 1. Setup worksheets & clear old strategy tabs
    worksheets = setup_and_clear_strategy_sheets()

    # 2. Define universe of stocks to scan
    ticker_universe = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS", "TATAMOTORS.NS"]

    top_weekly_results = []
    rest_weekly_results = []

    # 3. Scan stocks
    for symbol in ticker_universe:
        res = scan_symbol(symbol)
        if res:
            if res["Strategy"] == "Top_Weekly":
                top_weekly_results.append(res)
            elif res["Strategy"] == "Rest_Weekly":
                rest_weekly_results.append(res)

    # 4. Write new results to Google Sheets
    headers = ["Symbol", "Close", "SSF_20", "SSF_50", "SSF_200"]
    
    if top_weekly_results:
        df_top = pd.DataFrame(top_weekly_results)[headers]
        worksheets["Top_Weekly"].update([df_top.columns.values.tolist()] + df_top.values.tolist())

    if rest_weekly_results:
        df_rest = pd.DataFrame(rest_weekly_results)[headers]
        worksheets["Rest_Weekly"].update([df_rest.columns.values.tolist()] + df_rest.values.tolist())

    # 5. Update user portfolio status (if Portfolio_Tracker has data)
    update_portfolio_tracker(worksheets["Portfolio_Tracker"])

    # 6. Send Telegram notification summary
    top_symbols = [r["Symbol"] for r in top_weekly_results]
    rest_symbols = [r["Symbol"] for r in rest_weekly_results]

    msg = f"🚀 *Scan Complete* ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    msg += f"🔥 *Top Weekly Signals ({len(top_symbols)}):*\n"
    msg += f"`{', '.join(top_symbols) if top_symbols else 'None'}`\n\n"
    msg += f"📈 *Rest Weekly Signals ({len(rest_symbols)}):*\n"
    msg += f"`{', '.join(rest_symbols) if rest_symbols else 'None'}`\n\n"
    msg += "📊 *Dashboard:* Google Sheets updated successfully."

    send_telegram_message(msg)
    print("Execution complete.")


if __name__ == "__main__":
    main()
