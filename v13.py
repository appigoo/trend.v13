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

# --- 1. 頁面配置與 CSS ---
st.set_page_config(page_title="多股實時監控系統", layout="wide")

st.markdown("""
<style>
@keyframes blink {
    0% { border-color: #444; box-shadow: none; }
    50% { border-color: #ff4b4b; box-shadow: 0 0 15px #ff4b4b; }
    100% { border-color: #444; box-shadow: none; }
}
.blink-bull { border: 3px solid #00ff00 !important; animation: blink 1s infinite; background-color: rgba(0, 255, 0, 0.05); }
.blink-bear { border: 3px solid #ff4b4b !important; animation: blink 1s infinite; background-color: rgba(255, 75, 75, 0.05); }
</style>
""", unsafe_allow_html=True)

# --- 2. Telegram 通知 ---
def send_telegram_msg(sym, action, reason, price, p_change, v_ratio):
    try:
        token = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        message = (
            f"🔔 【{action}預警】: {sym}\n"
            f"現價: {price:.2f} ({p_change:+.2f}%)\n"
            f"量比: {v_ratio:.1f}x\n"
            f"--------------------\n"
            f"📋 判定根據:\n{reason}"
        )
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        params = {"chat_id": chat_id, "text": message}
        requests.get(url, params=params, timeout=5)
    except Exception as e:
        st.error(f"Telegram 發送失敗: {e}")

# --- 3. 數據獲取 ---
def fetch_data(symbol, p, i):
    try:
        df = yf.download(symbol, period=p, interval=i, progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.loc[:, ~df.columns.duplicated()].copy()
        close = df['Close'].squeeze()
        df['EMA20'] = close.ewm(span=20, adjust=False).mean()
        df['EMA60'] = close.ewm(span=60, adjust=False).mean()
        df['EMA200'] = close.ewm(span=200, adjust=False).mean()
        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df['MACD'] = ema12 - ema26
        df['Sig'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['Hist'] = df['MACD'] - df['Sig']
        return df
    except: return None

# --- 4. 價格水平預警解析 ---
def check_custom_alerts(sym, price, alert_str):
    alerts = re.split(r'[,\n]', alert_str)
    for a in alerts:
        a = a.strip().upper()
        if not a: continue
        match = re.search(rf"{sym}\s*([><]|升穿|跌穿)\s*(\d+\.?\d*)", a)
        if match:
            op, target = match.group(1), float(match.group(2))
            if (op in ['>', '升穿'] and price >= target) or (op in ['<', '跌穿'] and price <= target):
                return True, f"🎯 自定義價格預警: {a}"
    return False, ""

# --- 5. 單一週期訊號判定 ---
def get_period_signal(df, p_limit, v_limit, use_breakout, use_macd_flip):
    if df is None or len(df) < 10: return None
    last = df.iloc[-1]; prev = df.iloc[-2]
    price = float(last['Close'])
    p_change = ((price - float(prev['Close'])) / float(prev['Close'])) * 100
    v_ratio = float(last['Volume']) / float(last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    
    # 均線趨勢
    is_bull = price > last['EMA200'] and last['EMA20'] > last['EMA60']
    is_bear = price < last['EMA200'] and last['EMA20'] < last['EMA60']
    
    # 條件
    cond_bull = (is_bull and p_change >= p_limit and v_ratio >= v_limit)
    cond_bear = (is_bear and p_change <= -p_limit and v_ratio >= v_limit)
    
    if use_breakout:
        max5, min5 = df.iloc[-6:-1]['High'].max(), df.iloc[-6:-1]['Low'].min()
        cond_bull = cond_bull or (price > max5)
        cond_bear = cond_bear or (price < min5)
    
    if use_macd_flip and len(df) >= 8:
        hw = df['Hist'].iloc[-8:].values
        cond_bull = cond_bull or (all(x < 0 for x in hw[:-1]) and hw[-1] > 0)
        cond_bear = cond_bear or (all(x > 0 for x in hw[:-1]) and hw[-1] < 0)
        
    if cond_bull: return "BULL", p_change, v_ratio
    if cond_bear: return "BEAR", p_change, v_ratio
    return None, p_change, v_ratio

# --- 6. 側邊欄配置 ---
with st.sidebar:
    st.header("⚙️ 參數設定")
    input_symbols = st.text_input("股票代碼", value="TSLA, NIO, TSLL, XPEV, META, GOOGL, AAPL, NVDA, AMZN, MSFT, TSM, BTC-USD").upper()
    symbols = [s.strip() for s in input_symbols.split(",") if s.strip()]
    
    # 多週期監控 (NEW)
    st.subheader("⏱ 多週期共振設定")
    selected_intervals = st.multiselect("選擇監測週期 (需全數符合才通知)", ["1m", "5m", "15m", "30m", "1h", "1d"], default=["5m"])
    sel_period = st.selectbox("數據讀取範圍", ["1d", "5d", "1mo"], index=1)
    
    refresh_rate = st.slider("刷新頻率 (秒)", 30, 600, 60)
    
    st.divider()
    custom_alert_input = st.text_area("🎯 自定義價格預警 (TSLA 升穿 420)", value="")
    st.divider()
    vol_threshold = st.number_input("成交量異常倍數", value=2.0, step=0.5)
    price_threshold = st.number_input("股價單根異動 (%)", value=1.0, step=0.1)
    use_breakout = st.checkbox("5K 突破監控", value=True)
    use_macd_flip = st.checkbox("MACD 7+1 反轉監控", value=True)

# --- 7. 主介面循環 ---
st.title("📈 智能多週期共振監控系統")
placeholder = st.empty()

while True:
    all_data = {} # 僅存儲最後一個週期的 df 用於繪圖
    with placeholder.container():
        st.subheader(f"🔍 即時警報摘要 (監測週期: {', '.join(selected_intervals)})")
        if symbols and selected_intervals:
            cols = st.columns(len(symbols))
            for i, sym in enumerate(symbols):
                period_results = []
                last_df = None
                
                # 遍歷所有選定週期
                for interval in selected_intervals:
                    df = fetch_data(sym, sel_period, interval)
                    sig, pc, vr = get_period_signal(df, price_threshold, vol_threshold, use_breakout, use_macd_flip)
                    period_results.append(sig)
                    last_df = df # 用於展示與價格檢測
                
                if last_df is not None:
                    all_data[sym] = last_df
                    current_price = last_df['Close'].iloc[-1]
                    
                    # 判斷是否共振 (所有週期訊號一致且不為 None)
                    is_all_bull = all(r == "BULL" for r in period_results)
                    is_all_bear = all(r == "BEAR" for r in period_results)
                    
                    # 自定義價格預警 (獨立判斷)
                    hit_custom, custom_reason = check_custom_alerts(sym, current_price, custom_alert_input)
                    
                    # 決定狀態與通知
                    status, color, card_style = "⚖️ 觀望", "#aaaaaa", ""
                    if is_all_bull:
                        status, color, card_style = "🚀 多頭共振", "#00ff00", "blink-bull"
                        send_telegram_msg(sym, "🔥 多週期共振", f"✅ 週期 {selected_intervals} 全數看多", current_price, pc, vr)
                    elif is_all_bear:
                        status, color, card_style = "🔻 空頭共振", "#ff4b4b", "blink-bear"
                        send_telegram_msg(sym, "❄️ 多週期共振", f"❌ 週期 {selected_intervals} 全數看空", current_price, pc, vr)
                    
                    if hit_custom:
                        send_telegram_msg(sym, "🎯 價格預警", custom_reason, current_price, pc, vr)
                        status = "🎯 價格達標" if status == "⚖️ 觀望" else status + " + 🎯"

                    cols[i].markdown(f"""
                        <div class='{card_style}' style='border:1px solid #444; padding:15px; border-radius:10px; text-align:center;'>
                            <h3 style='margin:0;'>{sym}</h3>
                            <h2 style='color:{color}; margin:10px 0;'>{status}</h2>
                            <p style='font-size:1.3em; margin:0;'><b>{current_price:.2f}</b></p>
                            <hr style='margin:5px 0; border:0.5px solid #333;'>
                            <p style='font-size:0.8em; color:#ffa500;'>週期: {len([r for r in period_results if r])}/{len(selected_intervals)} 觸發</p>
                        </div>
                    """, unsafe_allow_html=True)

        st.divider()
        if all_data:
            tabs = st.tabs(list(all_data.keys()))
            for i, (sym, df) in enumerate(all_data.items()):
                with tabs[i]:
                    plot_df = df.tail(35).copy()
                    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
                    fig.add_trace(go.Candlestick(x=plot_df.index, open=plot_df['Open'], high=plot_df['High'], low=plot_df['Low'], close=plot_df['Close'], name='K線'), row=1, col=1)
                    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA20'], name='EMA20', line=dict(color='yellow', width=1)), row=1, col=1)
                    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA200'], name='EMA200', line=dict(color='red', width=1.5)), row=1, col=1)
                    colors = ['#00ff00' if x >= 0 else '#ff4b4b' for x in plot_df['Hist']]
                    fig.add_trace(go.Bar(x=plot_df.index, y=plot_df['Hist'], name='MACD Hist', marker_color=colors), row=2, col=1)
                    fig.update_layout(height=450, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=10,r=10,t=10,b=10))
                    st.plotly_chart(fig, use_container_width=True, key=f"fig_{sym}")
        st.caption(f"📅 更新: {datetime.now().strftime('%H:%M:%S')}")
    time.sleep(refresh_rate)
