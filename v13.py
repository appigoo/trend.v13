import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import time
import requests
import re

# --- 1. 頁面配置與專業 UI ---
st.set_page_config(page_title="專業級位階與共振監控終端", layout="wide")

st.markdown("""
<style>
@keyframes blink { 0% { border-color: #444; } 50% { border-color: #ff4b4b; box-shadow: 0 0 15px #ff4b4b; } 100% { border-color: #444; } }
.blink-bull { border: 3px solid #00ff00 !important; animation: blink 1s infinite; background-color: rgba(0, 255, 0, 0.05); }
.blink-bear { border: 3px solid #ff4b4b !important; animation: blink 1s infinite; background-color: rgba(255, 75, 75, 0.05); }
.vix-banner { padding: 12px; border-radius: 8px; text-align: center; margin-bottom: 15px; font-weight: bold; border: 1px solid #444; }
.card { border:1px solid #444; padding:15px; border-radius:10px; text-align:center; background-color: #1e1e1e; }
</style>
""", unsafe_allow_html=True)

# --- 2. 市場環境診斷 (加強穩定性) ---
def get_market_context():
    try:
        # 分開抓取避免 MultiIndex 混亂
        vix_df = yf.download("^VIX", period="2d", interval="15m", progress=False)
        spy_df = yf.download("SPY", period="2d", interval="15m", progress=False)
        
        if vix_df.empty or spy_df.empty:
            return 20.0, 0.0, "🟡 數據加載中", "穩定"
            
        v_p = float(vix_df['Close'].iloc[-1])
        s_p = float(spy_df['Close'].iloc[-1])
        s_prev = float(spy_df['Close'].iloc[-2])
        spy_pc = ((s_p - s_prev) / s_prev) * 100
        
        v_stat = "🔴 極端恐慌" if v_p > 28 else "🟡 波動放大" if v_p > 21 else "🟢 環境平穩"
        v_trend = "📈 升溫" if v_p > vix_df['Close'].iloc[-2] else "📉 緩解"
        return v_p, spy_pc, v_stat, v_trend
    except:
        return 20.0, 0.0, "⚠️ 數據延遲", "N/A"

# --- 3. 位階推斷邏輯 ---
def estimate_position(df):
    try:
        if df is None or len(df) < 60: return "分析中...", "#888"
        last = df.iloc[-1]
        p = last['Close']
        e20, e60, e200 = last['EMA20'], last['EMA60'], last['EMA200']
        
        # 計算 60 根 K 線相對位置
        low_60 = df['Low'].tail(60).min()
        high_60 = df['High'].tail(60).max()
        pos_score = (p - low_60) / (high_60 - low_60) if (high_60 - low_60) != 0 else 0.5

        if p > e200: # 牛市格局
            if e20 > e60:
                if pos_score > 0.85: return "🚀 上升高位 (慎防派發)", "#ff4b4b"
                if pos_score < 0.40: return "🐣 上升初位 (潛力極大)", "#00ff00"
                return "↗️ 上升中位", "#00ff00"
            return "🌀 牛市回調中", "#ffa500"
        else: # 熊市格局
            if e20 < e60:
                if pos_score < 0.15: return "💀 下跌低位 (超跌反彈近)", "#00ff00"
                if pos_score > 0.60: return "⚠️ 下跌初位 (剛破位)", "#ff4b4b"
                return "📉 下跌中位", "#ff4b4b"
            return "🌪️ 熊市反彈中", "#ffa500"
    except:
        return "⚖️ 震盪持平", "#aaa"

# --- 4. 數據獲取與指標 ---
def fetch_pro_data(symbol, range_p, interval_p):
    try:
        df = yf.download(symbol, period=range_p, interval=interval_p, progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        
        close = df['Close']
        df['EMA20'] = close.ewm(span=20, adjust=False).mean()
        df['EMA60'] = close.ewm(span=60, adjust=False).mean()
        df['EMA200'] = close.ewm(span=200, adjust=False).mean()
        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df['Hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        df_daily = yf.download(symbol, period="14d", interval="1d", progress=False)
        if not df_daily.empty:
            adr = (df_daily['High'] - df_daily['Low']).mean()
            df['ADR_Usage'] = ((df_daily['High'].iloc[-1] - df_daily['Low'].iloc[-1]) / adr) * 100
        else: df['ADR_Usage'] = 0
        return df
    except: return None

# --- 5. 訊號判定 ---
def check_signals(df, p_limit, v_limit, use_brk, use_macd, lookback_k):
    if df is None or len(df) < lookback_k + 1: return None, ""
    last = df.iloc[-1]
    pc = ((last['Close'] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100
    vr = last['Volume'] / last['Vol_Avg'] if last['Vol_Avg'] > 0 else 1
    
    reasons = []
    sig_type = None
    
    # 5K 突破
    is_brk_h = last['Close'] > df.iloc[-6:-1]['High'].max() if use_brk else False
    is_brk_l = last['Close'] < df.iloc[-6:-1]['Low'].min() if use_brk else False

    # 動態 MACD 反轉
    hw = df['Hist'].iloc[-(lookback_k+1):].values
    m_bull = all(x < 0 for x in hw[:-1]) and hw[-1] > 0
    m_bear = all(x > 0 for x in hw[:-1]) and hw[-1] < 0

    if (last['Close'] > last['EMA200'] and pc >= p_limit and vr >= v_limit) or is_brk_h or m_bull:
        sig_type = "BULL"
        if is_brk_h: reasons.append("▫️ 5K 向上突破")
        if m_bull: reasons.append(f"▫️ MACD {lookback_k}負轉1正")
    elif (last['Close'] < last['EMA200'] and pc <= -p_limit and vr >= v_limit) or is_brk_l or m_bear:
        sig_type = "BEAR"
        if is_brk_l: reasons.append("▫️ 5K 向下破位")
        if m_bear: reasons.append(f"▫️ MACD {lookback_k}正轉1負")
    
    return sig_type, "\n".join(reasons)

# --- 6. Telegram 通知 ---
def send_pro_notification(sym, action, res_details, price, pc, vr, adr_u, vix_info, pos_text, lookback_k):
    try:
        token, chat_id = st.secrets["TELEGRAM_BOT_TOKEN"], st.secrets["TELEGRAM_CHAT_ID"]
        v_val, spy_c, v_stat, v_trend = vix_info
        details = "\n".join([f"⏰ 【{k}】:\n{v}" for k,v in res_details.items()])
        msg = (
            f"🔔 {action}: {sym}\n💰 報價: {price:.2f} ({pc:+.2f}%)\n📍 位階: {pos_text}\n"
            f"📊 量比: {vr:.1f}x | ADR: {adr_u:.1f}%\n--------------------\n"
            f"🌐 市場: VIX {v_val:.2f} ({v_stat}) | SPY {spy_c:+.2f}%\n--------------------\n"
            f"📋 細節 ({lookback_k}K 反轉):\n{details}"
        )
        requests.get(f"https://api.telegram.org/bot{token}/sendMessage", params={"chat_id": chat_id, "text": msg})
    except: pass

# --- 7. 側邊欄與主程序 ---
with st.sidebar:
    st.header("⚙️ 專業設置")
    sym_in = st.text_input("代碼 (TSLA, NVDA...)", "TSLA, NIO, TSLL, XPEV, META, GOOGL, AAPL, NVDA, AMZN, MSFT, TSM, GLD, BTC-USD, QQQ").upper()
    symbols = [s.strip() for s in sym_in.split(",") if s.strip()]
    intervals = st.multiselect("共振週期", ["1m", "5m", "15m", "30m"], default=["5m", "15m"])
    lb_k = st.slider("MACD 衰竭 K 數", 3, 15, 7)
    refresh = st.slider("刷新頻率(秒)", 30, 300, 60)
    alerts = st.text_area("🎯 價格預警 (TSLA > 420)", "")

st.title("📈 全功能智能交易監控終端")
placeholder = st.empty()

while True:
    vix_info = get_market_context()
    with placeholder.container():
        st.markdown(f'<div class="vix-banner">市場診斷：{vix_info[2]} | VIX: {vix_info[0]:.2f} | SPY: {vix_info[1]:+.2f}% ({vix_info[3]})</div>', unsafe_allow_html=True)
        
        if symbols and intervals:
            cols = st.columns(len(symbols))
            for i, sym in enumerate(symbols):
                res_types, res_details, main_df = [], {}, None
                for interval in intervals:
                    df = fetch_pro_data(sym, "5d", interval)
                    if df is not None:
                        sig, det = check_signals(df, 1.0, 2.0, True, True, lb_k)
                        res_types.append(sig)
                        if sig: res_details[interval] = det
                        main_df = df

                if main_df is not None:
                    p = main_df['Close'].iloc[-1]
                    pc = ((p - main_df['Close'].iloc[-2]) / main_df['Close'].iloc[-2]) * 100
                    vr = main_df['Volume'].iloc[-1] / main_df['Vol_Avg'].iloc[-1]
                    adr_u = main_df['ADR_Usage'].iloc[-1]
                    pos_text, pos_col = estimate_position(main_df)
                    
                    # 獨立價格監控
                    match = re.search(rf"{sym}\s*([><]|升穿|跌穿)\s*(\d+\.?\d*)", alerts.upper())
                    if match:
                        op, target = match.group(1), float(match.group(2))
                        if (op in ['>', '升穿'] and p >= target) or (op in ['<', '跌穿'] and p <= target):
                            send_pro_notification(sym, "🎯 價格達標", {"預警": f"觸及 {target}"}, p, pc, vr, adr_u, vix_info, pos_text, lb_k)

                    # 共振觸發
                    is_bull = all(r == "BULL" for r in res_types)
                    is_bear = all(r == "BEAR" for r in res_types)
                    status, color, style = "⚖️ 觀望", "#888", ""
                    if is_bull:
                        status, color, style = "🚀 多頭共振", "#00ff00", "blink-bull"
                        send_pro_notification(sym, "🔥 多頭共振", res_details, p, pc, vr, adr_u, vix_info, pos_text, lb_k)
                    elif is_bear:
                        status, color, style = "🔻 空頭共振", "#ff4b4b", "blink-bear"
                        send_pro_notification(sym, "❄️ 空頭共振", res_details, p, pc, vr, adr_u, vix_info, pos_text, lb_k)

                    cols[i].markdown(f"""<div class='card {style}'><h3>{sym}</h3><p style='color:{pos_col};font-weight:bold;'>{pos_text}</p>
                        <h2 style='color:{color};'>{status}</h2><h2>{p:.2f}</h2><p style='font-size:0.8em;color:#888;'>ADR: {adr_u:.1f}%</p></div>""", unsafe_allow_html=True)
    time.sleep(refresh)
