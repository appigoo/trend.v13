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

# --- 4. 信號計算核心 (純邏輯，不發送通知) ---
def compute_signal_logic(df, p_limit, v_limit, use_breakout, use_macd_flip):
    if len(df) < 10: return None, []
    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(last['Close'])
    p_change = ((price - float(prev['Close'])) / float(prev['Close'])) * 100
    v_ratio = float(last['Volume']) / float(last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    
    reasons = []
    # 趨勢判定
    is_bull = price > last['EMA200'] and last['EMA20'] > last['EMA60']
    is_bear = price < last['EMA200'] and last['EMA20'] < last['EMA60']
    
    # 子條件
    base_bull = is_bull and p_change >= p_limit and v_ratio >= v_limit
    base_bear = is_bear and p_change <= -p_limit and v_ratio >= v_limit
    
    is_break_high, is_break_low = False, False
    if use_breakout:
        max_h5 = df.iloc[-6:-1]['High'].max(); min_l5 = df.iloc[-6:-1]['Low'].min()
        is_break_high, is_break_low = price > max_h5, price < min_l5

    macd_bull_flip, macd_bear_flip = False, False
    if use_macd_flip and len(df) >= 8:
        hist_window = df['Hist'].iloc[-8:].values
        macd_bull_flip = all(x < 0 for x in hist_window[:-1]) and hist_window[-1] > 0
        macd_bear_flip = all(x > 0 for x in hist_window[:-1]) and hist_window[-1] < 0

    if base_bull or is_break_high or macd_bull_flip:
        if base_bull: reasons.append("量價")
        if is_break_high: reasons.append("5K突破")
        if macd_bull_flip: reasons.append("MACD翻轉")
        return "BULL", reasons
    
    if base_bear or is_break_low or macd_bear_flip:
        if base_bear: reasons.append("量價")
        if is_break_low: reasons.append("5K跌破")
        if macd_bear_flip: reasons.append("MACD翻轉")
        return "BEAR", reasons
    
    return "NONE", []

# --- 5. 側邊欄 ---
with st.sidebar:
    st.header("⚙️ 參數設定")
    input_symbols = st.text_input("股票代碼", value="TSLA, NIO, NVDA, BTC-USD").upper()
    symbols = [s.strip() for s in input_symbols.split(",") if s.strip()]
    
    # 週期共振選項 (NEW)
    st.subheader("🔄 週期共振設定")
    selected_intervals = st.multiselect("選擇共振監測週期", ["1m", "5m", "15m", "30m", "1h", "1d"], default=["1m", "5m"])
    main_interval = st.selectbox("主顯示週期 (圖表)", ["1m", "5m", "15m", "1h", "1d"], index=1)
    
    refresh_rate = st.slider("刷新頻率 (秒)", 60, 600, 300)
    st.divider()
    custom_alert_input = st.text_area("🎯 價格預警 (TSLA 升穿 420)", value="")
    st.divider()
    vol_threshold = st.number_input("成交量異常倍數", value=2.0, step=0.5)
    price_threshold = st.number_input("股價單根異動 (%)", value=1.0, step=0.1)
    use_breakout = st.checkbox("5K 突破監控", value=False)
    use_macd_flip = st.checkbox("MACD 7+1 反轉監控", value=False)

# --- 6. 主介面循環 ---
st.title("📈 智能多週期監控系統")
placeholder = st.empty()

while True:
    all_data = {}
    with placeholder.container():
        st.subheader("🔍 即時警報摘要")
        if symbols:
            cols = st.columns(len(symbols))
            for i, sym in enumerate(symbols):
                res_list = [] # 存放各週期結果
                main_df = None
                
                # 遍歷監測所有勾選週期
                for interval in selected_intervals:
                    df = fetch_data(sym, "5d", interval)
                    sig, reas = compute_signal_logic(df, price_threshold, vol_threshold, use_breakout, use_macd_flip)
                    res_list.append(sig)
                    if interval == main_interval: main_df = df
                
                # 價格預警判斷 (獨立於週期)
                current_price = main_df['Close'].iloc[-1] if main_df is not None else 0
                hit_price, price_reason = False, ""
                if current_price > 0:
                    alerts = re.split(r'[,\n]', custom_alert_input)
                    for a in alerts:
                        if not a.strip(): continue
                        match = re.search(rf"{sym}\s*(升穿|跌穿|>|<)\s*(\d+\.?\d*)", a.upper())
                        if match:
                            op, target = match.group(1), float(match.group(2))
                            if (op in ['>', '升穿'] and current_price >= target) or (op in ['<', '跌穿'] and current_price <= target):
                                hit_price, price_reason = True, f"價格達標: {a}"

                # 共振邏輯：所有選擇週期信號一致
                is_resonate = len(set(res_list)) == 1 and res_list[0] != "NONE" and len(selected_intervals) > 0
                final_sig = res_list[0] if is_resonate else "NONE"
                
                # Telegram 通知
                if is_resonate:
                    send_telegram_msg(sym, f"🌀 {selected_intervals} 共振", f"週期共振發出 {final_sig} 信號", current_price, 0, 0)
                if hit_price:
                    send_telegram_msg(sym, "🎯 價格預警", price_reason, current_price, 0, 0)

                # UI 顯示
                card_style = "blink-bull" if final_sig == "BULL" else "blink-bear" if final_sig == "BEAR" else ""
                color = "#00ff00" if final_sig == "BULL" else "#ff4b4b" if final_sig == "BEAR" else "#aaaaaa"
                status = "🚀 共振做多" if final_sig == "BULL" else "🔻 共振做空" if final_sig == "BEAR" else "⚖️ 觀望"
                
                if main_df is not None:
                    all_data[sym] = main_df
                    cols[i].markdown(f"""
                        <div class='{card_style}' style='border:1px solid #444; padding:15px; border-radius:10px; text-align:center;'>
                            <h3 style='margin:0;'>{sym}</h3>
                            <h2 style='color:{color}; margin:10px 0;'>{status}</h2>
                            <p style='font-size:1.1em;'>{current_price:.2f}</p>
                            <p style='font-size:0.8em; color:gray;'>監測: {selected_intervals}</p>
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
                    fig.add_trace(go.Bar(x=plot_df.index, y=plot_df['Hist'], marker_color=colors), row=2, col=1)
                    fig.update_layout(height=500, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=10,r=10,t=10,b=10))
                    st.plotly_chart(fig, use_container_width=True, key=f"fig_{sym}")
        st.caption(f"📅 更新: {datetime.now().strftime('%H:%M:%S')}")
    time.sleep(refresh_rate)
