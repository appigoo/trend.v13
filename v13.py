import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import time
import requests
import re

# --- 1. 頁面配置 ---
st.set_page_config(page_title="專業級多週期共振監控系統 V3.2", layout="wide")

st.markdown("""
<style>
@keyframes blink { 0% { border-color: #444; } 50% { border-color: #ff4b4b; box-shadow: 0 0 15px #ff4b4b; } 100% { border-color: #444; } }
.blink-bull { border: 3px solid #00ff00 !important; animation: blink 1s infinite; background-color: rgba(0, 255, 0, 0.05); }
.blink-bear { border: 3px solid #ff4b4b !important; animation: blink 1s infinite; background-color: rgba(255, 75, 75, 0.05); }
.vix-banner { padding: 15px; border-radius: 8px; text-align: center; margin-bottom: 20px; font-weight: bold; border: 1px solid #444; font-size: 1.1em; }
</style>
""", unsafe_allow_html=True)

# --- 2. 市場環境與核心函數 ---
def get_market_context():
    try:
        vix_data = yf.download("^VIX", period="5d", interval="1d", progress=False)
        spy_data = yf.download("SPY", period="5d", interval="1d", progress=False)
        if isinstance(vix_data.columns, pd.MultiIndex): vix_data.columns = vix_data.columns.get_level_values(0)
        if isinstance(spy_data.columns, pd.MultiIndex): spy_data.columns = spy_data.columns.get_level_values(0)
        v_p = float(vix_data['Close'].iloc[-1])
        s_c = ((spy_data['Close'].iloc[-1] - spy_data['Close'].iloc[-2]) / spy_data['Close'].iloc[-2]) * 100
        v_stat = "🔴 極端恐慌" if v_p > 28 else "🟡 波動放大" if v_p > 20 else "🟢 環境平穩"
        return v_p, s_c, v_stat
    except: return 20.0, 0.0, "數據讀取中"

def get_pivot_levels(df_daily):
    try:
        if len(df_daily) < 2: return None
        prev = df_daily.iloc[-2]
        p = (prev['High'] + prev['Low'] + prev['Close']) / 3
        return {"R1": (2 * p) - prev['Low'], "S1": (2 * p) - prev['High']}
    except: return None

# --- 3. 數據與指標 ---
def fetch_pro_data(symbol, interval_p):
    try:
        fetch_range = "60d" if interval_p in ["30m", "15m"] else "7d"
        df = yf.download(symbol, period=fetch_range, interval=interval_p, progress=False)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        
        c = df['Close']
        df['EMA5'] = c.ewm(span=5, adjust=False).mean()
        df['EMA10'] = c.ewm(span=10, adjust=False).mean()
        df['EMA20'] = c.ewm(span=20, adjust=False).mean()
        df['EMA60'] = c.ewm(span=60, adjust=False).mean()
        df['EMA200'] = c.ewm(span=200, adjust=False).mean()
        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        
        macd_diff = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
        df['Hist'] = macd_diff - macd_diff.ewm(span=9, adjust=False).mean()
        return df.dropna(subset=['EMA200'])
    except: return None

# --- 4. 訊號判定 (所有功能歸位) ---
def check_signals(df, p_limit, v_limit, use_brk, use_macd, lookback_k):
    if df is None or len(df) < lookback_k + 2: return None, "", "SIDE"
    last = df.iloc[-1]; prev = df.iloc[-2]
    price = float(last['Close'])
    pc = ((price - prev['Close']) / prev['Close']) * 100
    vr = float(last['Volume'] / last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    
    # 圖片特徵: 均線狀態
    is_ema_bull = last['EMA5'] > last['EMA10'] > last['EMA20'] > last['EMA60']
    is_ema_bear = last['EMA5'] < last['EMA10'] < last['EMA20'] < last['EMA60']
    
    reasons = []
    sig = None

    # 功能 A: 5K 突破判斷
    is_brk_h = price > df.iloc[-6:-1]['High'].max() if use_brk else False
    is_brk_l = price < df.iloc[-6:-1]['Low'].min() if use_brk else False

    # 功能 B: MACD 反轉判斷 (lookback)
    m_bull = m_bear = False
    if use_macd:
        hw = df['Hist'].iloc[-(lookback_k + 1):].values
        m_bull = all(x < 0 for x in hw[:-1]) and hw[-1] > 0
        m_bear = all(x > 0 for x in hw[:-1]) and hw[-1] < 0

    # 多頭組合判斷 (必須符合 EMA 趨勢)
    if is_ema_bull:
        if pc >= p_limit and vr >= v_limit: reasons.append(f"量價強勢({pc:+.2f}%)")
        if is_brk_h: reasons.append("5K向上突破")
        if m_bull: reasons.append(f"MACD反轉")
        if reasons: sig = "BULL"

    # 空頭組合判斷
    elif is_ema_bear:
        if pc <= -p_limit and vr >= v_limit: reasons.append(f"量價跌穿({pc:+.2f}%)")
        if is_brk_l: reasons.append("5K向下破位")
        if m_bear: reasons.append(f"MACD反轉")
        if reasons: sig = "BEAR"
        
    trend = "BULL" if price > last['EMA200'] else "BEAR" if price < last['EMA200'] else "SIDE"
    return sig, " | ".join(reasons), trend

# --- 5. 通知系統 ---
def send_pro_notification(sym, action, res_details, price, pc, vr, adr_u, vix_info, levels):
    try:
        token = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        v_p, spy_c, v_s = vix_info
        lv_msg = f"R1:{levels['R1']:.2f} | S1:{levels['S1']:.2f}" if levels else "N/A"
        message = (
            f"🔔 {action}: {sym}\n💰 價格: {price:.2f} ({pc:+.2f}%)\n📊 量比: {vr:.1f}x | ADR: {adr_u:.1f}%\n"
            f"📍 位置: {lv_msg}\n🌐 VIX: {v_p:.2f} | SPY: {spy_c:+.2f}%\n📋 明細: {res_details}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        requests.get(f"https://api.telegram.org/bot{token}/sendMessage", params={"chat_id": chat_id, "text": message}, timeout=5)
    except: pass

# --- 6. 側邊欄與 UI ---
with st.sidebar:
    st.header("🗄️ 交易者工作站 V3.2")
    sym_input = st.text_input("代碼名單", value="TSLA, NVDA, AAPL, QQQ, BTC-USD").upper()
    symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    selected_intervals = st.multiselect("共振週期", ["1m", "5m", "15m", "30m"], default=["5m", "15m"])
    lookback_k = st.slider("MACD 衰竭 K 線數", 3, 15, 7)
    refresh_rate = st.slider("刷新頻率(秒)", 30, 300, 60)
    
    st.divider()
    st.subheader("🎯 核心觸發開關")
    price_alerts = st.text_area("關鍵價位 (例如: TSLA > 400)", value="")
    p_thr = st.number_input("異動閾值(%)", value=1.0)
    v_thr = st.number_input("量爆倍數", value=1.5)
    use_brk = st.checkbox("啟用 5K 突破", True)
    use_macd = st.checkbox("啟用 MACD 反轉", True)

# --- 7. 主循環 ---
st.title("📈 旗艦級智能監控終端 V3.2")

placeholder = st.empty()

while True:
    vix_val, spy_c, v_stat = get_market_context()
    with placeholder.container():
        st.markdown(f'<div class="vix-banner">市場狀態：{v_stat} | VIX: {vix_val:.2f} | SPY: {spy_c:+.2f}%</div>', unsafe_allow_html=True)
        if symbols:
            cols = st.columns(len(symbols))
            for i, sym in enumerate(symbols):
                try:
                    df_d = yf.download(sym, period="20d", interval="1d", progress=False)
                    if isinstance(df_d.columns, pd.MultiIndex): df_d.columns = df_d.columns.get_level_values(0)
                    adr = (df_d['High'] - df_d['Low']).mean()
                    adr_u = ((df_d['High'].iloc[-1] - df_d['Low'].iloc[-1]) / adr) * 100
                    levels = get_pivot_levels(df_d)
                except: adr_u, levels = 0, None

                res_sigs, res_trends, res_details = [], [], {}
                main_df = None
                for interval in selected_intervals:
                    df = fetch_pro_data(sym, interval)
                    sig, det, trend = check_signals(df, p_thr, v_thr, use_brk, use_macd, lookback_k)
                    res_sigs.append(sig); res_trends.append(trend)
                    if sig: res_details[interval] = det
                    main_df = df
                
                if main_df is not None:
                    cp = float(main_df['Close'].iloc[-1]); c_pc = ((cp - main_df['Close'].iloc[-2]) / main_df['Close'].iloc[-2]) * 100
                    c_vr = float(main_df['Volume'].iloc[-1] / main_df['Vol_Avg'].iloc[-1]) if main_df['Vol_Avg'].iloc[-1] > 0 else 1
                    
                    # 關鍵價位預警功能
                    match = re.search(rf"{sym}\s*([><]|升穿|跌穿)\s*(\d+\.?\d*)", price_alerts.upper())
                    if match:
                        op, target = match.group(1), float(match.group(2))
                        if (op in ['>', '升穿'] and cp >= target) or (op in ['<', '跌穿'] and cp <= target):
                            send_pro_notification(sym, "🎯 觸及關鍵價位", "手動設置價格到達", cp, c_pc, c_vr, adr_u, (vix_val, spy_c, v_stat), levels)

                    # 多週期共振判定
                    is_bull = (res_sigs[0] == "BULL") and (res_trends[-1] == "BULL")
                    is_bear = (res_sigs[0] == "BEAR") and (res_trends[-1] == "BEAR")
                    
                    color = "#00ff00" if is_bull else "#ff4b4b" if is_bear else "#888"
                    label = "🚀 多頭加速" if is_bull else "🔻 空頭加速" if is_bear else "⚖️ 觀望"
                    style = "blink-bull" if is_bull else "blink-bear" if is_bear else ""
                    
                    if is_bull or is_bear:
                        send_pro_notification(sym, label, str(res_details), cp, c_pc, c_vr, adr_u, (vix_val, spy_c, v_stat), levels)

                    cols[i].markdown(f"<div class='{style}' style='border:1px solid #444; padding:10px; border-radius:10px; text-align:center;'><h4>{sym}</h4><h3 style='color:{color}'>{label}</h3><p style='font-size:1.2em;'>{cp:.2f}</p><p style='font-size:0.7em; color:#aaa;'>ADR: {adr_u:.1f}%</p></div>", unsafe_allow_html=True)
    time.sleep(refresh_rate)
