import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import time
import requests
import re

# --- 1. 頁面配置 ---
st.set_page_config(page_title="專業級多週期共振監控系統", layout="wide")

# --- 2. 市場環境與核心計算 ---
def get_market_context():
    try:
        # 修正：確保獲取足夠天數計算漲跌
        vix_data = yf.download("^VIX", period="5d", interval="1d", progress=False)
        spy_data = yf.download("SPY", period="5d", interval="1d", progress=False)
        if isinstance(vix_data.columns, pd.MultiIndex): vix_data.columns = vix_data.columns.get_level_values(0)
        if isinstance(spy_data.columns, pd.MultiIndex): spy_data.columns = spy_data.columns.get_level_values(0)
        
        vix_price = float(vix_data['Close'].iloc[-1])
        vix_prev = float(vix_data['Close'].iloc[-2])
        spy_change = ((spy_data['Close'].iloc[-1] - spy_data['Close'].iloc[-2]) / spy_data['Close'].iloc[-2]) * 100
        
        v_status = "🔴 極端恐慌" if vix_price > 28 else "🟡 波動放大" if vix_price > 20 else "🟢 環境平穩"
        v_trend = "📈 升溫" if vix_price > vix_prev else "📉 緩解"
        return vix_price, spy_change, v_status, v_trend
    except:
        return 20.0, 0.0, "數據讀取中", "N/A"

def get_pivot_levels(df_daily):
    try:
        if len(df_daily) < 2: return None
        prev = df_daily.iloc[-2]
        p = (prev['High'] + prev['Low'] + prev['Close']) / 3
        return {"R1": (2 * p) - prev['Low'], "S1": (2 * p) - prev['High'], "P": p}
    except: return None

# --- 3. 數據抓取優化 (解決 EMA200 預熱問題) ---
def fetch_pro_data(symbol, interval_p):
    try:
        # 修正：根據週期自動調整下載量，確保 EMA200 準確
        fetch_range = "60d" if interval_p in ["30m", "15m"] else "7d"
        df = yf.download(symbol, period=fetch_range, interval=interval_p, progress=False)
        
        if df.empty or len(df) < 200: 
            # 如果數據還是不夠，嘗試抓取最大範圍
            df = yf.download(symbol, period="max", interval=interval_p, progress=False)
            
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        
        close = df['Close']
        # 指標計算
        df['EMA20'] = close.ewm(span=20, adjust=False).mean()
        df['EMA60'] = close.ewm(span=60, adjust=False).mean()
        df['EMA200'] = close.ewm(span=200, adjust=False).mean()
        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        
        # MACD 修正
        exp1 = close.ewm(span=12, adjust=False).mean()
        exp2 = close.ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        df['Hist'] = macd - macd.ewm(span=9, adjust=False).mean()
        
        return df.dropna(subset=['EMA200']) # 確保只回傳指標計算完整的數據
    except: return None

# --- 4. 訊號判定 ---
def check_signals(df, p_limit, v_limit, use_brk, use_macd, lookback_k):
    if df is None or len(df) < lookback_k + 2: return None, "", "SIDE"
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(last['Close'])
    pc = ((price - prev['Close']) / prev['Close']) * 100
    vr = float(last['Volume'] / last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    
    # 趨勢判定
    is_bull_trend = price > last['EMA200'] and last['EMA20'] > last['EMA60']
    is_bear_trend = price < last['EMA200'] and last['EMA20'] < last['EMA60']
    
    reasons = []
    # 形態訊號
    is_brk_h = price > df.iloc[-6:-1]['High'].max() if use_brk else False
    is_brk_l = price < df.iloc[-6:-1]['Low'].min() if use_brk else False
    
    # MACD 訊號
    m_bull = m_bear = False
    if use_macd:
        hw = df['Hist'].iloc[-(lookback_k + 1):].values
        m_bull = all(x < 0 for x in hw[:-1]) and hw[-1] > 0
        m_bear = all(x > 0 for x in hw[:-1]) and hw[-1] < 0

    sig = None
    if (is_bull_trend and pc >= p_limit and vr >= v_limit) or is_brk_h or m_bull:
        sig = "BULL"
        if is_bull_trend and pc >= p_limit: reasons.append(f"量價強勢({pc:+.2f}%)")
        if is_brk_h: reasons.append("5K向上突破")
        if m_bull: reasons.append(f"MACD{lookback_k}根回正")
    elif (is_bear_trend and pc <= -p_limit and vr >= v_limit) or is_brk_l or m_bear:
        sig = "BEAR"
        if is_bear_trend and pc <= -p_limit: reasons.append(f"量價跌穿({pc:+.2f}%)")
        if is_brk_l: reasons.append("5K向下破位")
        if m_bear: reasons.append(f"MACD{lookback_k}根轉負")
        
    trend = "BULL" if is_bull_trend else "BEAR" if is_bear_trend else "SIDE"
    return sig, " | ".join(reasons), trend

# --- 5. Telegram 與 主邏輯 (保持穩定) ---
def send_pro_notification(sym, action, res_details, price, pc, vr, adr_u, vix_info, levels, lookback_k):
    try:
        token = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        v_val, spy_c, v_stat, v_trend = vix_info
        
        lv_msg = f"R1:{levels['R1']:.2f} | S1:{levels['S1']:.2f}" if levels else "N/A信号"
        
        message = (
            f"🔔 {action}: {sym}\n"
            f"💰 價格: {price:.2f} ({pc:+.2f}%)\n"
            f"📊 量比: {vr:.1f}x | ADR: {adr_u:.1f}%\n"
            f"📍 位置: {lv_msg}\n"
            f"🌐 VIX: {v_val:.2f} | SPY: {spy_c:+.2f}%\n"
            f"📋 細節: {res_details}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        requests.get(f"https://api.telegram.org/bot{token}/sendMessage", params={"chat_id": chat_id, "text": message}, timeout=5)
    except: pass

# --- UI 設置 ---
st.title("🛡️ 專業級智能監控終端 (V3.0)")
# (側邊欄部分保持不變...)
with st.sidebar:
    sym_input = st.text_input("代碼名單", value="TSLA, NVDA, AAPL, QQQ, BTC-USD").upper()
    symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    selected_intervals = st.multiselect("共振週期", ["1m", "5m", "15m", "30m"], default=["5m", "15m"])
    lookback_k = st.slider("MACD 衰竭 K 線數", 3, 15, 7)
    refresh_rate = st.slider("刷新頻率(秒)", 30, 300, 60)
    p_thr = st.number_input("異動閾值(%)", value=1.0)
    v_thr = st.number_input("量爆倍數", value=1.5)
    use_brk = st.checkbox("啟用 5K 突破", True)
    use_macd = st.checkbox("啟用 MACD 反轉", True)

placeholder = st.empty()

while True:
    vix_val, spy_c, v_stat, v_trend = get_market_context()
    with placeholder.container():
        st.markdown(f'<div class="vix-banner">市場診斷：{v_stat} | VIX: {vix_val:.2f} | SPY: {spy_change:+.2f}%</div>', unsafe_allow_html=True)
        
        if symbols:
            cols = st.columns(len(symbols))
            for i, sym in enumerate(symbols):
                # 抓取日線數據計算 ADR 和 Pivot
                try:
                    df_d = yf.download(sym, period="20d", interval="1d", progress=False)
                    if isinstance(df_d.columns, pd.MultiIndex): df_d.columns = df_d.columns.get_level_values(0)
                    adr = (df_d['High'] - df_d['Low']).mean()
                    adr_u = ((df_d['High'].iloc[-1] - df_daily['Low'].iloc[-1]) / adr) * 100
                    levels = get_pivot_levels(df_d)
                except: adr_u, levels = 0, None

                res_sigs, res_trends, res_details = [], [], {}
                last_df = None
                
                for interval in selected_intervals:
                    df = fetch_pro_data(sym, interval)
                    sig, det, trend = check_signals(df, p_thr, v_thr, use_brk, use_macd, lookback_k)
                    res_sigs.append(sig); res_trends.append(trend)
                    if sig: res_details[interval] = det
                    last_df = df
                
                if last_df is not None:
                    cp = float(last_df['Close'].iloc[-1])
                    c_pc = ((cp - last_df['Close'].iloc[-2]) / last_df['Close'].iloc[-2]) * 100
                    c_vr = float(last_df['Volume'].iloc[-1] / last_df['Vol_Avg'].iloc[-1])
                    
                    # 判定共振：小週期有訊號 + 大週期趨勢一致
                    is_bull = (res_sigs[0] == "BULL") and (res_trends[-1] == "BULL")
                    is_bear = (res_sigs[0] == "BEAR") and (res_trends[-1] == "BEAR")
                    
                    color = "#00ff00" if is_bull else "#ff4b4b" if is_bear else "#888"
                    label = "🚀 多頭共振" if is_bull else "🔻 空頭共振" if is_bear else "⚖️ 觀望"
                    
                    if is_bull or is_bear:
                        send_pro_notification(sym, label, str(res_details), cp, c_pc, c_vr, adr_u, (vix_val, spy_c, v_stat, v_trend), levels, lookback_k)

                    cols[i].markdown(f"<div style='border:1px solid #444; padding:10px; border-radius:10px; text-align:center;'><h4>{sym}</h4><h3 style='color:{color}'>{label}</h3><p>{cp:.2f}</p></div>", unsafe_allow_html=True)

    time.sleep(refresh_rate)
