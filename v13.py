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
st.set_page_config(page_title="專業級多週期共振監控系統", layout="wide")

st.markdown("""
<style>
@keyframes blink { 0% { border-color: #444; } 50% { border-color: #ff4b4b; box-shadow: 0 0 15px #ff4b4b; } 100% { border-color: #444; } }
.blink-bull { border: 3px solid #00ff00 !important; animation: blink 1s infinite; background-color: rgba(0, 255, 0, 0.05); }
.blink-bear { border: 3px solid #ff4b4b !important; animation: blink 1s infinite; background-color: rgba(255, 75, 75, 0.05); }
.vix-banner { padding: 15px; border-radius: 8px; text-align: center; margin-bottom: 20px; font-weight: bold; border: 1px solid #444; font-size: 1.1em; }
</style>
""", unsafe_allow_html=True)

# --- 2. 市場環境診斷 (VIX & SPY) ---
def get_market_context():
    try:
        # 抓取 VIX (波動率) 與 SPY (標普500)
        vix_data = yf.download("^VIX", period="2d", interval="5m", progress=False)
        spy_data = yf.download("SPY", period="2d", interval="5m", progress=False)
        vix_price = vix_data['Close'].iloc[-1]
        vix_prev = vix_data['Close'].iloc[-2]
        spy_change = ((spy_data['Close'].iloc[-1] - spy_data['Close'].iloc[-2]) / spy_data['Close'].iloc[-2]) * 100
        
        v_status = "🔴 極端恐慌" if vix_price > 28 else "🟡 波動放大" if vix_price > 20 else "🟢 環境平穩"
        v_trend = "📈 恐慌升溫" if vix_price > vix_prev else "📉 恐慌緩解"
        return float(vix_price), float(spy_change), v_status, v_trend
    except:
        return 20.0, 0.0, "N/A", "N/A"

# --- 3. Telegram 結構化通知系統 ---
def send_pro_notification(sym, action, res_details, price, pc, vr, adr_u, vix_info, lookback_k):
    try:
        token = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        v_val, spy_c, v_stat, v_trend = vix_info
        
        # 能量與風險診斷
        energy_status = "🔴 體力耗盡 (慎防回踩)" if adr_u > 90 else "🟡 剩餘有限" if adr_u > 70 else "🟢 空間充足"
        market_risk = "⚠️ 高風險環境" if v_val > 25 else "✅ 環境穩定"

        period_brief = ""
        for interval, detail in res_details.items():
            period_brief += f"⏰ 【{interval} 週期】\n{detail}\n\n"

        message = (
            f"🔔 {action}: {sym}\n"
            f"💰 當前報價: {price:.2f} ({pc:+.2f}%)\n"
            f"📊 即時量比: {vr:.1f}x | ADR已用: {adr_u:.1f}%\n"
            f"--------------------\n"
            f"🌐 市場環境診斷:\n"
            f"   • VIX 指數: {v_val:.2f} ({v_stat})\n"
            f"   • SPY 表現: {spy_c:+.2f}% ({v_trend})\n"
            f"   • 風險評估: {market_risk}\n"
            f"--------------------\n"
            f"🚩 個股動能診斷:\n"
            f"   • 剩餘體力: {energy_status}\n"
            f"   • MACD 設定: 已檢查連續 {lookback_k} 根K線\n"
            f"--------------------\n"
            f"📋 策略觸發明細:\n"
            f"{period_brief}"
            f"📅 發送時間: {datetime.now().strftime('%H:%M:%S')}"
        )
        
        requests.get(f"https://api.telegram.org/bot{token}/sendMessage", params={"chat_id": chat_id, "text": message}, timeout=5)
    except:
        pass

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
        
        # MACD Hist
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df['Hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        # ADR 計算
        df_daily = yf.download(symbol, period="14d", interval="1d", progress=False)
        if not df_daily.empty:
            adr = (df_daily['High'] - df_daily['Low']).mean()
            df['ADR_Usage'] = ((df_daily['High'].iloc[-1] - df_daily['Low'].iloc[-1]) / adr) * 100
        else:
            df['ADR_Usage'] = 0
            
        return df
    except: return None

# --- 5. 訊號判定邏輯 ---
def check_signals(df, p_limit, v_limit, use_brk, use_macd, lookback_k):
    if df is None or len(df) < lookback_k + 1: return None, ""
    last = df.iloc[-1]; prev = df.iloc[-2]
    price = float(last['Close'])
    pc = ((price - prev['Close']) / prev['Close']) * 100
    vr = float(last['Volume']) / float(last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    
    reasons = []
    sig_type = None

    # A. 趨勢過濾
    is_bull_trend = price > last['EMA200'] and last['EMA20'] > last['EMA60']
    is_bear_trend = price < last['EMA200'] and last['EMA20'] < last['EMA60']
    
    # B. 5K 突破
    is_brk_h = price > df.iloc[-6:-1]['High'].max() if use_brk else False
    is_brk_l = price < df.iloc[-6:-1]['Low'].min() if use_brk else False

    # C. 動態 MACD 反轉 (使用參數 lookback_k)
    m_bull = m_bear = False
    if use_macd:
        hw = df['Hist'].iloc[-(lookback_k + 1):].values
        m_bull = all(x < 0 for x in hw[:-1]) and hw[-1] > 0
        m_bear = all(x > 0 for x in hw[:-1]) and hw[-1] < 0

    if (is_bull_trend and pc >= p_limit and vr >= v_limit) or is_brk_h or m_bull:
        sig_type = "BULL"
        if is_bull_trend and pc >= p_limit: reasons.append(f"  ▫️ 趨勢量價強勢 ({pc:+.2f}%)")
        if is_brk_h: reasons.append("  ▫️ 5K 形態向上突破")
        if m_bull: reasons.append(f"  ▫️ MACD {lookback_k}負轉1正 (底背離)")

    elif (is_bear_trend and pc <= -p_limit and vr >= v_limit) or is_brk_l or m_bear:
        sig_type = "BEAR"
        if is_bear_trend and pc <= -p_limit: reasons.append(f"  ▫️ 趨勢量價跌穿 ({pc:+.2f}%)")
        if is_brk_l: reasons.append("  ▫️ 5K 形態向下破位")
        if m_bear: reasons.append(f"  ▫️ MACD {lookback_k}正轉1負 (頂背離)")

    return sig_type, "\n".join(reasons)

# --- 6. 側邊欄 ---
with st.sidebar:
    st.header("🗄️ 交易者工作站")
    sym_input = st.text_input("代碼名單", value="TSLA, NIO, TSLL, XPEV, META, GOOGL, AAPL, NVDA, AMZN, MSFT, TSM, GLD, BTC-USD, QQQ").upper()
    symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    selected_intervals = st.multiselect("共振週期", ["1m", "5m", "15m", "30m"], default=["5m", "15m"])
    
    st.divider()
    st.subheader("⚡ 策略調優")
    lookback_k = st.slider("MACD 衰竭 K 線數", 3, 15, 7)
    refresh_rate = st.slider("刷新頻率(秒)", 30, 300, 60)
    
    st.divider()
    price_alerts = st.text_area("🎯 關鍵價位 (TSLA > 420)", value="")
    p_thr = st.number_input("異動閾值(%)", value=1.0)
    v_thr = st.number_input("量爆倍數", value=2.0)
    use_brk = st.checkbox("啟用 5K 突破", True)
    use_macd = st.checkbox("啟用 MACD 反轉", True)

# --- 7. 主循環 ---
st.title("📈 專業級智能監控終端")
placeholder = st.empty()

while True:
    vix_val, spy_c, v_stat, v_trend = get_market_context()
    vix_col = "#ff4b4b" if vix_val > 25 else "#00ff00"
    
    with placeholder.container():
        st.markdown(f'<div class="vix-banner" style="background-color:{vix_col}22; border: 1px solid {vix_col}; color:{vix_col};">市場診斷：{v_stat} | VIX: {vix_val:.2f} | SPY: {spy_c:+.2f}% | {v_trend}</div>', unsafe_allow_html=True)

        if symbols and selected_intervals:
            cols = st.columns(len(symbols))
            for i, sym in enumerate(symbols):
                res_types, res_details = [], {}
                main_df = None
                
                for interval in selected_intervals:
                    df = fetch_pro_data(sym, "5d", interval)
                    sig, det = check_signals(df, p_thr, v_thr, use_brk, use_macd, lookback_k)
                    res_types.append(sig)
                    if sig: res_details[interval] = det
                    main_df = df

                if main_df is not None:
                    cur_p = main_df['Close'].iloc[-1]
                    cur_pc = ((cur_p - main_df['Close'].iloc[-2]) / main_df['Close'].iloc[-2]) * 100
                    cur_vr = main_df['Volume'].iloc[-1] / main_df['Vol_Avg'].iloc[-1]
                    adr_u = main_df['ADR_Usage'].iloc[-1]
                    
                    # 獨立價格監控
                    match = re.search(rf"{sym}\s*([><]|升穿|跌穿)\s*(\d+\.?\d*)", price_alerts.upper())
                    if match:
                        op, target = match.group(1), float(match.group(2))
                        if (op in ['>', '升穿'] and cur_p >= target) or (op in ['<', '跌穿'] and cur_p <= target):
                            send_pro_notification(sym, "🎯 關鍵位報警", {"價格預警": f"觸及設定目標 {target}"}, cur_p, cur_pc, cur_vr, adr_u, (vix_val, spy_c, v_stat, v_trend), lookback_k)

                    # 共振邏輯
                    is_bull = all(r == "BULL" for r in res_types)
                    is_bear = all(r == "BEAR" for r in res_types)
                    
                    status, color, style = "⚖️ 觀望", "#888", ""
                    if is_bull:
                        status, color, style = "🚀 多頭共振", "#00ff00", "blink-bull"
                        send_pro_notification(sym, "🔥 多頭共振觸發", res_details, cur_p, cur_pc, cur_vr, adr_u, (vix_val, spy_c, v_stat, v_trend), lookback_k)
                    elif is_bear:
                        status, color, style = "🔻 空頭共振", "#ff4b4b", "blink-bear"
                        send_pro_notification(sym, "❄️ 空頭共振觸發", res_details, cur_p, cur_pc, cur_vr, adr_u, (vix_val, spy_c, v_stat, v_trend), lookback_k)

                    cols[i].markdown(f"""
                        <div class='{style}' style='border:1px solid #444; padding:15px; border-radius:10px; text-align:center;'>
                            <h3>{sym}</h3>
                            <h2 style='color:{color};'>{status}</h2>
                            <p style='font-size:1.4em;'><b>{cur_p:.2f}</b></p>
                            <hr style='border:0.5px solid #333;'>
                            <p style='font-size:0.8em; color:{"#ff4b4b" if adr_u > 90 else "#aaa"};'>ADR已用: {adr_u:.1f}%</p>
                        </div>
                    """, unsafe_allow_html=True)

        st.divider()
        st.caption(f"系統運行中 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    time.sleep(refresh_rate)
