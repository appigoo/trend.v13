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

st.markdown("""
<style>
@keyframes blink { 0% { border-color: #444; } 50% { border-color: #ff4b4b; box-shadow: 0 0 15px #ff4b4b; } 100% { border-color: #444; } }
.blink-bull { border: 3px solid #00ff00 !important; animation: blink 1s infinite; background-color: rgba(0, 255, 0, 0.05); }
.blink-bear { border: 3px solid #ff4b4b !important; animation: blink 1s infinite; background-color: rgba(255, 75, 75, 0.05); }
.vix-banner { padding: 15px; border-radius: 8px; text-align: center; margin-bottom: 20px; font-weight: bold; border: 1px solid #444; font-size: 1.1em; }
</style>
""", unsafe_allow_html=True)

# --- 2. 支撐阻力與市場診斷函數 ---
def get_market_context():
    try:
        vix_data = yf.download("^VIX", period="5d", interval="1d", progress=False)
        spy_data = yf.download("SPY", period="5d", interval="1d", progress=False)
        if isinstance(vix_data.columns, pd.MultiIndex): vix_data.columns = vix_data.columns.get_level_values(0)
        if isinstance(spy_data.columns, pd.MultiIndex): spy_data.columns = spy_data.columns.get_level_values(0)
        vix_price = float(vix_data['Close'].iloc[-1])
        vix_prev = float(vix_data['Close'].iloc[-2])
        spy_change = ((spy_data['Close'].iloc[-1] - spy_data['Close'].iloc[-2]) / spy_data['Close'].iloc[-2]) * 100
        v_status = "🔴 極端恐慌" if vix_price > 28 else "🟡 波動放大" if vix_price > 20 else "🟢 環境平穩"
        v_trend = "📈 恐慌升溫" if vix_price > vix_prev else "📉 恐慌緩解"
        return vix_price, spy_change, v_status, v_trend
    except:
        return 20.0, 0.0, "數據讀取中", "N/A"

def get_pivot_levels(df_daily):
    """計算經典樞軸點 (Pivot Points)"""
    try:
        if len(df_daily) < 2: return None
        prev_day = df_daily.iloc[-2] # 取前一交易日
        high = prev_day['High']
        low = prev_day['Low']
        close = prev_day['Close']
        
        pivot = (high + low + close) / 3
        r1 = (2 * pivot) - low
        s1 = (2 * pivot) - high
        return {"P": pivot, "R1": r1, "S1": s1}
    except:
        return None

# --- 3. Telegram 通知系統 (整合支撐阻力) ---
def send_pro_notification(sym, action, res_details, price, pc, vr, adr_u, vix_info, levels, lookback_k):
    try:
        token = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        v_val, spy_c, v_stat, v_trend = vix_info
        
        # 支撐阻力文本
        level_text = "N/A"
        if levels:
            dist_r1 = ((levels['R1'] - price) / price) * 100
            level_text = f"上壓 R1: {levels['R1']:.2f} (距 {dist_r1:+.1f}%)\n   • 下撐 S1: {levels['S1']:.2f}"

        period_brief = ""
        for interval, detail in res_details.items():
            if detail: period_brief += f"⏰ 【{interval}】\n{detail}\n\n"

        message = (
            f"🔔 {action}: {sym}\n"
            f"💰 價格: {price:.2f} ({pc:+.2f}%)\n"
            f"📊 量比: {vr:.1f}x | ADR: {adr_u:.1f}%\n"
            f"--------------------\n"
            f"🚩 關鍵位置 (Pivot):\n"
            f"   • {level_text}\n"
            f"--------------------\n"
            f"🌐 市場環境: {v_stat}\n"
            f"   • VIX: {v_val:.2f} | SPY: {spy_c:+.2f}%\n"
            f"--------------------\n"
            f"📋 策略細節:\n{period_brief}"
            f"📅 {datetime.now().strftime('%H:%M:%S')}"
        )
        requests.get(f"https://api.telegram.org/bot{token}/sendMessage", params={"chat_id": chat_id, "text": message}, timeout=5)
    except:
        pass

# --- 4. 數據抓取 ---
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
        diff = ema12 - ema26
        df['Hist'] = diff - diff.ewm(span=9, adjust=False).mean()
        return df
    except: return None

# --- 5. 訊號邏輯 (保持穩定) ---
def check_signals(df, p_limit, v_limit, use_brk, use_macd, lookback_k):
    if df is None or len(df) < lookback_k + 2: return None, "", False
    last = df.iloc[-1]; prev = df.iloc[-2]
    price = float(last['Close']); pc = ((price - prev['Close']) / prev['Close']) * 100
    vr = float(last['Volume']) / float(last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    reasons = []
    is_bull_trend = price > last['EMA200'] and last['EMA20'] > last['EMA60']
    is_bear_trend = price < last['EMA200'] and last['EMA20'] < last['EMA60']
    is_brk_h = price > df.iloc[-6:-1]['High'].max() if use_brk else False
    is_brk_l = price < df.iloc[-6:-1]['Low'].min() if use_brk else False
    m_bull = m_bear = False
    if use_macd:
        hw = df['Hist'].iloc[-(lookback_k + 1):].values
        m_bull = all(x < 0 for x in hw[:-1]) and hw[-1] > 0
        m_bear = all(x > 0 for x in hw[:-1]) and hw[-1] < 0
    sig_type = "BULL" if (is_bull_trend and pc >= p_limit and vr >= v_limit) or is_brk_h or m_bull else "BEAR" if (is_bear_trend and pc <= -p_limit and vr >= v_limit) or is_brk_l or m_bear else None
    if sig_type == "BULL":
        if is_bull_trend and pc >= p_limit: reasons.append(f"  ▫️ 量價強勢({pc:+.2f}%)")
        if is_brk_h: reasons.append("  ▫️ 5K向上突破")
        if m_bull: reasons.append(f"  ▫️ MACD {lookback_k}負轉正")
    elif sig_type == "BEAR":
        if is_bear_trend and pc <= -p_limit: reasons.append(f"  ▫️ 量價跌穿({pc:+.2f}%)")
        if is_brk_l: reasons.append("  ▫️ 5K向下破位")
        if m_bear: reasons.append(f"  ▫️ MACD {lookback_k}正轉負")
    trend_status = "BULL" if is_bull_trend else "BEAR" if is_bear_trend else "SIDE"
    return sig_type, "\n".join(reasons), trend_status

# --- 6. 側邊欄與 UI ---
with st.sidebar:
    st.header("🗄️ 交易者工作站")
    sym_input = st.text_input("代碼名單", value="TSLA, NVDA, AAPL, QQQ, BTC-USD").upper()
    symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    selected_intervals = st.multiselect("共振週期", ["1m", "5m", "15m", "30m"], default=["5m", "15m"])
    lookback_k = st.slider("MACD 衰竭 K 線數", 3, 15, 7)
    refresh_rate = st.slider("刷新頻率(秒)", 30, 300, 60)
    st.divider()
    price_alerts = st.text_area("關鍵價位預警", value="")
    p_thr = st.number_input("異動閾值(%)", value=1.0)
    v_thr = st.number_input("量爆倍數", value=1.5)
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
                adr_u, levels = 0, None
                try:
                    df_daily = yf.download(sym, period="14d", interval="1d", progress=False)
                    if isinstance(df_daily.columns, pd.MultiIndex): df_daily.columns = df_daily.columns.get_level_values(0)
                    adr = (df_daily['High'] - df_daily['Low']).mean()
                    adr_u = ((df_daily['High'].iloc[-1] - df_daily['Low'].iloc[-1]) / adr) * 100
                    levels = get_pivot_levels(df_daily) # 計算支撐阻力
                except: pass

                res_signals, res_details, res_trends = [], {}, []
                main_df = None
                for interval in selected_intervals:
                    df = fetch_pro_data(sym, "5d", interval)
                    sig, det, trend = check_signals(df, p_thr, v_thr, use_brk, use_macd, lookback_k)
                    res_signals.append(sig); res_trends.append(trend)
                    if sig: res_details[interval] = det
                    main_df = df

                if main_df is not None:
                    cur_p = float(main_df['Close'].iloc[-1])
                    cur_pc = ((cur_p - main_df['Close'].iloc[-2]) / main_df['Close'].iloc[-2]) * 100
                    cur_vr = float(main_df['Volume'].iloc[-1] / main_df['Vol_Avg'].iloc[-1]) if main_df['Vol_Avg'].iloc[-1] > 0 else 1.0
                    
                    # 價格警報邏輯
                    match = re.search(rf"{sym}\s*([><]|升穿|跌穿)\s*(\d+\.?\d*)", price_alerts.upper())
                    if match:
                        op, target = match.group(1), float(match.group(2))
                        if (op in ['>', '升穿'] and cur_p >= target) or (op in ['<', '跌穿'] and cur_p <= target):
                            send_pro_notification(sym, "🎯 關鍵位報警", {"價格預警": f"觸及目標 {target}"}, cur_p, cur_pc, cur_vr, adr_u, (vix_val, spy_c, v_stat, v_trend), levels, lookback_k)

                    # 共振判定
                    is_bull = (res_signals[0] == "BULL") and (res_trends[-1] == "BULL")
                    is_bear = (res_signals[0] == "BEAR") and (res_trends[-1] == "BEAR")
                    
                    status, color, style = "⚖️ 觀望", "#888", ""
                    if is_bull:
                        status, color, style = "🚀 多頭共振", "#00ff00", "blink-bull"
                        send_pro_notification(sym, "🔥 多頭共振觸發", res_details, cur_p, cur_pc, cur_vr, adr_u, (vix_val, spy_c, v_stat, v_trend), levels, lookback_k)
                    elif is_bear:
                        status, color, style = "🔻 空頭共振", "#ff4b4b", "blink-bear"
                        send_pro_notification(sym, "❄️ 空頭共振觸發", res_details, cur_p, cur_pc, cur_vr, adr_u, (vix_val, spy_c, v_stat, v_trend), levels, lookback_k)

                    cols[i].markdown(f"<div class='{style}' style='border:1px solid #444; padding:15px; border-radius:10px; text-align:center;'><h3>{sym}</h3><h2 style='color:{color};'>{status}</h2><p style='font-size:1.4em;'><b>{cur_p:.2f}</b></p><hr style='border:0.5px solid #333;'><p style='font-size:0.8em;'>R1: {levels['R1']:.2f} | S1: {levels['S1']:.2f}</p></div>", unsafe_allow_html=True)

        st.divider()
        st.caption(f"系統運行中 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    time.sleep(refresh_rate)
