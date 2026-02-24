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

# --- 1. 頁面配置與專業 UI 樣式 ---
st.set_page_config(page_title="專業級 Day Trader 監控系統", layout="wide")

st.markdown("""
<style>
@keyframes blink { 0% { border-color: #444; } 50% { border-color: #ff4b4b; box-shadow: 0 0 15px #ff4b4b; } 100% { border-color: #444; } }
.blink-bull { border: 3px solid #00ff00 !important; animation: blink 1s infinite; background-color: rgba(0, 255, 0, 0.05); }
.blink-bear { border: 3px solid #ff4b4b !important; animation: blink 1s infinite; background-color: rgba(255, 75, 75, 0.05); }
.vix-banner { padding: 15px; border-radius: 8px; text-align: center; margin-bottom: 20px; font-weight: bold; font-size: 1.1em; border: 1px solid #444; }
</style>
""", unsafe_allow_html=True)

# --- 2. 市場背景診斷 (VIX & SPY) ---
def get_market_context():
    try:
        # 獲取 VIX 恐慌指數與 SPY 大盤
        vix_data = yf.download("^VIX", period="2d", interval="15m", progress=False)
        spy_data = yf.download("SPY", period="2d", interval="15m", progress=False)
        
        vix_price = vix_data['Close'].iloc[-1]
        vix_prev = vix_data['Close'].iloc[-2]
        spy_change = ((spy_data['Close'].iloc[-1] - spy_data['Close'].iloc[-2]) / spy_data['Close'].iloc[-2]) * 100
        
        v_status = "🔴 市場極端恐慌" if vix_price > 28 else "🟡 波動放大" if vix_price > 20 else "🟢 環境平穩"
        v_trend = "📈 恐慌升溫" if vix_price > vix_prev else "📉 恐慌緩解"
        return float(vix_price), float(spy_change), v_status, v_trend
    except:
        return 20.0, 0.0, "N/A", "N/A"

# --- 3. Telegram 詳盡通知系統 ---
def send_pro_notification(sym, action, res_details, price, pc, vr, adr_u, vix_info):
    try:
        token = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        
        v_val, spy_c, v_stat, v_trend = vix_info
        
        # 能量診斷說明
        energy_msg = "✅ 空間充足" if adr_u < 50 else "⚠️ 能量消耗中" if adr_u < 85 else "❌ 體力耗盡 (慎防追漲/追跌)"
        
        # 格式化各週期細節
        details_text = ""
        for interval, detail in res_details.items():
            details_text += f"⏰ 【{interval} 週期】:\n{detail}\n\n"

        message = (
            f"🔔 {action}: {sym}\n"
            f"💰 現價: {price:.2f} ({pc:+.2f}%)\n"
            f"📊 量比: {vr:.1f}x | ADR已用: {adr_u:.1f}%\n"
            f"🚩 能量狀態: {energy_msg}\n"
            f"--------------------\n"
            f"🌐 市場環境 (VIX): {v_val:.2f} | {v_stat}\n"
            f"📈 大盤走勢 (SPY): {spy_c:+.2f}% ({v_trend})\n"
            f"--------------------\n"
            f"📋 策略觸發細節:\n{details_text}"
            f"📅 時間: {datetime.now().strftime('%H:%M:%S')}"
        )
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.get(url, params={"chat_id": chat_id, "text": message}, timeout=5)
    except Exception as e:
        st.error(f"Telegram 發送失敗: {e}")

# --- 4. 專業數據計算 (含 ADR) ---
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
        
        # ADR 計算 (日線級別波幅)
        df_daily = yf.download(symbol, period="14d", interval="1d", progress=False)
        if not df_daily.empty:
            adr = (df_daily['High'] - df_daily['Low']).mean()
            today_range = df_daily['High'].iloc[-1] - df_daily['Low'].iloc[-1]
            df['ADR_Usage'] = (today_range / adr) * 100
        else:
            df['ADR_Usage'] = 0
            
        return df
    except: return None

# --- 5. 訊號判定邏輯 ---
def check_signals(df, p_limit, v_limit, use_brk, use_macd):
    if df is None or len(df) < 10: return None, ""
    last = df.iloc[-1]; prev = df.iloc[-2]
    price = float(last['Close'])
    pc = ((price - prev['Close']) / prev['Close']) * 100
    vr = float(last['Volume']) / float(last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    
    reasons = []
    sig_type = None

    # 1. 均線與量價 (Trend)
    is_bull_trend = price > last['EMA200'] and last['EMA20'] > last['EMA60']
    is_bear_trend = price < last['EMA200'] and last['EMA20'] < last['EMA60']
    
    # 2. 5K 突破 (Breakout)
    is_brk_h = price > df.iloc[-6:-1]['High'].max() if use_brk else False
    is_brk_l = price < df.iloc[-6:-1]['Low'].min() if use_brk else False

    # 3. MACD 7+1 反轉 (Reversal)
    if use_macd and len(df) >= 8:
        hw = df['Hist'].iloc[-8:].values
        m_bull = all(x < 0 for x in hw[:-1]) and hw[-1] > 0
        m_bear = all(x > 0 for x in hw[:-1]) and hw[-1] < 0
    else: m_bull = m_bear = False

    # 彙整做多理由
    if (is_bull_trend and pc >= p_limit and vr >= v_limit) or is_brk_h or m_bull:
        sig_type = "BULL"
        if is_bull_trend and pc >= p_limit: reasons.append(f"  ▫️ 量價強勢突破 ({pc:+.2f}%)")
        if is_brk_h: reasons.append("  ▫️ 突破前5根K線高點")
        if m_bull: reasons.append("  ▫️ MACD 7負轉1正 (底背離反轉)")

    # 彙整做空理由
    elif (is_bear_trend and pc <= -p_limit and vr >= v_limit) or is_brk_l or m_bear:
        sig_type = "BEAR"
        if is_bear_trend and pc <= -p_limit: reasons.append(f"  ▫️ 量價轉弱跌穿 ({pc:+.2f}%)")
        if is_brk_l: reasons.append("  ▫️ 跌破前5根K線低點")
        if m_bear: reasons.append("  ▫️ MACD 7正轉1負 (頂背離反轉)")

    return sig_type, "\n".join(reasons)

# --- 6. 側邊欄配置 ---
with st.sidebar:
    st.header("🗄️ Trader 策略中心")
    sym_input = st.text_input("監控代碼 (逗號分隔)", value="TSLA, NVDA, AAPL, BTC-USD").upper()
    symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    
    selected_intervals = st.multiselect("共振週期設定", ["1m", "5m", "15m", "30m", "1h"], default=["5m", "15m"])
    refresh_rate = st.slider("系統刷新頻率 (秒)", 30, 300, 60)
    
    st.divider()
    st.subheader("🎯 關鍵價位預警")
    price_alerts = st.text_area("格式: TSLA 升穿 420 (換行輸入多條)", value="")
    
    st.divider()
    p_thr = st.number_input("價格異動閾值 (%)", value=1.0, step=0.1)
    v_thr = st.number_input("成交量爆發倍數", value=2.0, step=0.5)
    use_brk = st.checkbox("啟用 5K 突破監控", value=True)
    use_macd = st.checkbox("啟用 MACD 反轉監控", value=True)

# --- 7. 主程式循環 ---
st.title("🚀 專業多週期共振監控系統")
placeholder = st.empty()

while True:
    vix_val, spy_c, v_stat, v_trend = get_market_context()
    vix_col = "#ff4b4b" if vix_val > 25 else "#ffa500" if vix_val > 20 else "#00ff00"
    
    with placeholder.container():
        # VIX 市場橫幅
        st.markdown(f"""
            <div class="vix-banner" style="background-color: {vix_col}22; border: 1px solid {vix_col}; color: {vix_col};">
                市場環境診斷：{v_stat} | VIX: {vix_val:.2f} | SPY: {spy_c:+.2f}% | 趨勢: {v_trend}
            </div>
        """, unsafe_allow_html=True)

        if symbols and selected_intervals:
            cols = st.columns(len(symbols))
            for i, sym in enumerate(symbols):
                res_types = []
                res_details = {}
                main_df = None
                
                # 遍歷週期抓取數據
                for interval in selected_intervals:
                    df = fetch_pro_data(sym, "5d", interval)
                    sig, detail = check_signals(df, p_thr, v_thr, use_brk, use_macd)
                    res_types.append(sig)
                    if sig: res_details[interval] = detail
                    main_df = df # 用於基準顯示

                if main_df is not None:
                    cur_p = main_df['Close'].iloc[-1]
                    cur_pc = ((main_df['Close'].iloc[-1] - main_df['Close'].iloc[-2]) / main_df['Close'].iloc[-2]) * 100
                    cur_vr = main_df['Volume'].iloc[-1] / main_df['Vol_Avg'].iloc[-1]
                    adr_u = main_df['ADR_Usage'].iloc[-1]
                    
                    # 邏輯 A: 關鍵價位判定
                    match = re.search(rf"{sym}\s*([><]|升穿|跌穿)\s*(\d+\.?\d*)", price_alerts.upper())
                    if match:
                        op, target = match.group(1), float(match.group(2))
                        if (op in ['>', '升穿'] and cur_p >= target) or (op in ['<', '跌穿'] and cur_p <= target):
                            send_pro_notification(sym, "🎯 關鍵位觸發", {"手動設定": f"價格觸及 {target}"}, cur_p, cur_pc, cur_vr, adr_u, (vix_val, spy_c, v_stat, v_trend))

                    # 邏輯 B: 多週期共振
                    is_bull = all(r == "BULL" for r in res_types)
                    is_bear = all(r == "BEAR" for r in res_types)
                    
                    status, color, style = "⚖️ 觀望", "#888", ""
                    if is_bull:
                        status, color, style = "🚀 多頭共振", "#00ff00", "blink-bull"
                        send_pro_notification(sym, "🔥 多頭共振觸發", res_details, cur_p, cur_pc, cur_vr, adr_u, (vix_val, spy_c, v_stat, v_trend))
                    elif is_bear:
                        status, color, style = "🔻 空頭共振", "#ff4b4b", "blink-bear"
                        send_pro_notification(sym, "❄️ 空頭共振觸發", res_details, cur_p, cur_pc, cur_vr, adr_u, (vix_val, spy_c, v_stat, v_trend))

                    cols[i].markdown(f"""
                        <div class='{style}' style='border:1px solid #444; padding:15px; border-radius:10px; text-align:center;'>
                            <h3 style='margin:0;'>{sym}</h3>
                            <h2 style='color:{color}; margin:10px 0;'>{status}</h2>
                            <p style='font-size:1.4em; margin:0;'><b>{cur_p:.2f}</b></p>
                            <hr style='margin:10px 0; border:0.5px solid #333;'>
                            <p style='font-size:0.85em;'>能量已用: <span style='color:{"#ff4b4b" if adr_u > 90 else "#ffa500"}'>{adr_u:.1f}%</span></p>
                            <p style='font-size:0.75em; color:#888;'>共振進度: {len(res_details)}/{len(selected_intervals)}</p>
                        </div>
                    """, unsafe_allow_html=True)

        st.divider()
        st.caption(f"📅 系統運行中 | 最後更新: {datetime.now().strftime('%H:%M:%S')} | 建議 VIX > 25 時謹慎操作。")
        
    time.sleep(refresh_rate)
