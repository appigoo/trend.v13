import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import requests
import re

# --- 1. 頁面配置 ---
st.set_page_config(page_title="專業級多週期共振監控系統", layout="wide")

if 'alert_cache' not in st.session_state:
    st.session_state.alert_cache = {}

# --- [NEW] 趨勢位階診斷函數 ---
def diagnose_trend_stage(df):
    if df is None or len(df) < 60: return "數據不足", "#888"
    
    last = df.iloc[-1]
    prev_5 = df.iloc[-6]
    p = last['Close']
    ema20, ema60, ema200 = last['EMA20'], last['EMA60'], last['EMA200']
    rsi = last['RSI']
    
    # 多頭排列判斷
    is_bull = p > ema200 and ema20 > ema60
    # 空頭排列判斷
    is_bear = p < ema200 and ema20 < ema60
    
    if is_bull:
        if rsi > 75: return "🚀 上升高位 (超買)", "#ff4b4b"
        if prev_5['EMA20'] < prev_5['EMA60']: return "🌱 上升初段 (金叉)", "#00ff00"
        return "漲 趨勢中段", "#00ff00"
    
    if is_bear:
        if rsi < 25: return "📉 下跌低位 (超賣)", "#00ff00"
        if prev_5['EMA20'] > prev_5['EMA60']: return "🥀 下跌初段 (死叉)", "#ff4b4b"
        return "跌 趨勢中段", "#ff4b4b"
        
    return "⚖️ 區間橫盤", "#aaa"

# --- 2. 市場環境診斷 ---
def get_market_context():
    try:
        spy_ticker = yf.Ticker("IVV")
        vix_data = yf.download("^VIX", period="5d", interval="15m", progress=False, repair=True)
        spy_data = spy_ticker.history(period="5d", interval="15m")
        #spy_data = yf.download("IVV", period="5d", interval="15m", progress=False, repair=True)
        vix_price = vix_data['Close'].iloc[-1]
        vix_prev = vix_data['Close'].iloc[-2]
        spy_change = ((spy_data['Close'].iloc[-1] - spy_data['Close'].iloc[-2]) / spy_data['Close'].iloc[-2]) * 100
        v_status = "🔴 極端恐慌" if vix_price > 28 else "🟡 波動放大" if vix_price > 20 else "🟢 環境平穩"
        v_trend = "📈 恐慌升溫" if vix_price > vix_prev else "📉 恐慌緩解"
        return float(vix_price), float(spy_change), v_status, v_trend
    except:
        return 20.0, 0.0, "N/A", "N/A"

# --- 3. Telegram 通知 (同步更新位階信息) ---
def send_pro_notification(sym, action, res_details, price, pc, vr, adr_u, vix_info, lookback_k, stage=""):
    cache_key = f"{sym}_{action}"
    now = datetime.now()
    if cache_key in st.session_state.alert_cache:
        if now < st.session_state.alert_cache[cache_key] + timedelta(minutes=30):
            return

    try:
        token = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        v_val, spy_c, v_stat, v_trend = vix_info
        
        message = (
            f"🔔 {action}: {sym}\n"
            f"💰 價格: {price:.2f} ({pc:+.2f}%)\n"
            f"📈 當前位階: {stage}\n" # [NEW]
            f"📊 量比: {vr:.1f}x | ADR: {adr_u:.1f}%\n"
            f"--------------------\n"
            f"📋 策略細節:\n{res_details}\n"
            f"📅 時間: {now.strftime('%H:%M:%S')}"
        )
        resp = requests.get(f"https://api.telegram.org/bot{token}/sendMessage", params={"chat_id": chat_id, "text": message}, timeout=5)
        if resp.status_code == 200:
            st.session_state.alert_cache[cache_key] = now
    except: pass

# --- 4. 數據與指標計算 ---
def fetch_pro_data(symbol, range_p, interval_p):
    try:
        df = yf.download(symbol, period=range_p, interval=interval_p, progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        
        close = df['Close'].squeeze()
        df['EMA20'] = close.ewm(span=20, adjust=False).mean()
        df['EMA60'] = close.ewm(span=60, adjust=False).mean()
        df['EMA200'] = close.ewm(span=200, adjust=False).mean()
        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        
        # [NEW] RSI 計算
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        df['RSI'] = 100 - (100 / (1 + (gain/loss)))

        ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
        df['Hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        df_daily = yf.download(symbol, period="14d", interval="1d", progress=False)
        df['ADR_Usage'] = (((df_daily['High'] - df_daily['Low']).iloc[-1] / (df_daily['High'] - df_daily['Low']).mean()) * 100) if not df_daily.empty else 0
        return df
    except: return None

# --- 5. 訊號判定邏輯 (保持完整) ---
def check_signals(df, p_limit, v_limit, use_brk, use_macd, lookback_k):
    if df is None or len(df) < lookback_k + 1: return None, ""
    last, prev = df.iloc[-1], df.iloc[-2]
    price, pc = float(last['Close']), ((float(last['Close']) - prev['Close']) / prev['Close']) * 100
    vr = float(last['Volume']) / float(last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    
    reasons, sig_type = [], None
    is_bull_t = price > last['EMA200'] and last['EMA20'] > last['EMA60']
    is_bear_t = price < last['EMA200'] and last['EMA20'] < last['EMA60']
    
    if use_brk:
        if price > df.iloc[-6:-1]['High'].max(): reasons.append("▫️ 5K 向上突破"); sig_type = "BULL"
        if price < df.iloc[-6:-1]['Low'].min(): reasons.append("▫️ 5K 向下破位"); sig_type = "BEAR"

    if use_macd:
        hw = df['Hist'].iloc[-(lookback_k + 1):].values
        if all(x < 0 for x in hw[:-1]) and hw[-1] > 0: reasons.append(f"▫️ MACD {lookback_k}負轉正"); sig_type = "BULL"
        if all(x > 0 for x in hw[:-1]) and hw[-1] < 0: reasons.append(f"▫️ MACD {lookback_k}正轉負"); sig_type = "BEAR"

    if is_bull_t and pc >= p_limit and vr >= v_limit: reasons.append(f"▫️ 趨勢量價強勢"); sig_type = "BULL"
    if is_bear_t and pc <= -p_limit and vr >= v_limit: reasons.append(f"▫️ 趨勢量價跌穿"); sig_type = "BEAR"

    return sig_type, "\n".join(reasons)

# --- 6. 側邊欄 ---
with st.sidebar:
    st.header("🗄️ 交易者工作站")
    sym_input = st.text_input("代碼名單", value="TSLA, NIO, TSLL, XPEV, META, GOOGL, AAPL, NVDA, AMZN, MSFT, TSM, BTC-USD,GLD,QQQ,VOO").upper()
    symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    selected_intervals = st.multiselect("共振週期", ["1m", "5m", "15m", "30m"], default=["5m", "15m"])
    lookback_k = st.slider("MACD 衰竭 K 線數", 3, 15, 7)
    refresh_rate = st.slider("刷新頻率(秒)", 30, 300, 60)
    p_thr, v_thr = st.number_input("異動閾值(%)", value=1.0), st.number_input("量爆倍數", value=2.0)
    use_brk, use_macd = st.checkbox("啟用 5K 突破", True), st.checkbox("啟用 MACD 反轉", True)
    price_alerts = st.text_area("🎯 關鍵價位 (如: TSLA > 420)", value="")

# --- 7. 主循環 ---
st.title("📈 專業級智能監控終端")
placeholder = st.empty()

while True:
    vix_val, spy_c, v_stat, v_trend = get_market_context()
    vix_col = "#ff4b4b" if vix_val > 25 else "#00ff00"
    
    with placeholder.container():
        st.markdown(f'<div class="vix-banner" style="background-color:{vix_col}22; border: 1px solid {vix_col}; color:{vix_col};">市場診斷：{v_stat} | VIX: {vix_val:.2f} | SPY: {spy_c:+.2f}%</div>', unsafe_allow_html=True)

        if symbols:
            rows = [symbols[i:i + 4] for i in range(0, len(symbols), 4)]
            for row_syms in rows:
                cols = st.columns(4)
                for i, sym in enumerate(row_syms):
                    res_types, main_df, det_msg = [], None, ""
                    for interval in selected_intervals:
                        df = fetch_pro_data(sym, "5d", interval)
                        sig, det = check_signals(df, p_thr, v_thr, use_brk, use_macd, lookback_k)
                        res_types.append(sig); main_df = df
                        if sig: det_msg += f"{interval}: {det}\n"

                    if main_df is not None:
                        cur_p = main_df['Close'].iloc[-1]
                        cur_pc = ((cur_p - main_df['Close'].iloc[-2]) / main_df['Close'].iloc[-2]) * 100
                        adr_u = main_df['ADR_Usage'].iloc[-1]
                        
                        # [NEW] 獲取趨勢位階
                        stage_text, stage_color = diagnose_trend_stage(main_df)
                        
                        is_bull = all(r == "BULL" for r in res_types) if res_types else False
                        is_bear = all(r == "BEAR" for r in res_types) if res_types else False
                        status, color, style = "⚖️ 觀望", "#888", ""
                        if is_bull: status, color, style = "🚀 多頭共振", "#00ff00", "blink-bull"
                        elif is_bear: status, color, style = "🔻 空頭共振", "#ff4b4b", "blink-bear"
                        
                        if is_bull or is_bear:
                            send_pro_notification(sym, status, det_msg, cur_p, cur_pc, 1.0, adr_u, (vix_val, spy_c, v_stat, v_trend), lookback_k, stage_text)

                        cols[i].markdown(f"""
                            <div class='{style}' style='border:1px solid #444; padding:15px; border-radius:10px; text-align:center; margin-bottom:10px;'>
                                <h3 style='margin:0;'>{sym}</h3>
                                <p style='color:{stage_color}; font-weight:bold; margin:5px 0;'>{stage_text}</p>
                                <h2 style='color:{color}; margin:5px 0;'>{status}</h2>
                                <p style='font-size:1.4em; margin:0;'><b>{cur_p:.2f}</b> <span style='font-size:0.6em; color:{color};'>{cur_pc:+.2f}%</span></p>
                                <hr style='border:0.5px solid #333;'>
                                <p style='font-size:0.8em; color:#aaa;'>ADR已用: {adr_u:.1f}%</p>
                            </div>
                        """, unsafe_allow_html=True)

        st.caption(f"最後更新: {datetime.now().strftime('%H:%M:%S')}")
    time.sleep(refresh_rate)
