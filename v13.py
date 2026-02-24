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

# --- 1. 頁面配置 ---
st.set_page_config(page_title="多週期共振及價格監控系統", layout="wide")

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

# --- 2. Telegram 通知函式 ---
def send_telegram_msg(sym, action, reason_text, price, pc=0, vr=0):
    try:
        token = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        
        message = (
            f"🔔 {action}: {sym}\n"
            f"現價: {price:.2f} ({pc:+.2f}%)\n"
            f"--------------------\n"
            f"📋 預警詳情:\n{reason_text}"
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

# --- 4. 解析價格水平預警 (獨立邏輯) ---
def process_custom_price_alerts(sym, current_price, alert_input):
    if not alert_input.strip(): return False, ""
    
    lines = re.split(r'[,\n]', alert_input)
    for line in lines:
        line = line.strip().upper()
        if not line: continue
        
        # 匹配 TSLA > 420 或 TSLA 升穿 420 等格式
        match = re.search(rf"{sym}\s*([><]|升穿|跌穿)\s*(\d+\.?\d*)", line)
        if match:
            operator = match.group(1)
            target_p = float(match.group(2))
            
            if (operator in ['>', '升穿'] and current_price >= target_p) or \
               (operator in ['<', '跌穿'] and current_price <= target_p):
                return True, f"🎯 觸及設定水平: {line}"
    return False, ""

# --- 5. 單週期指標判定 ---
def get_indicators_signal(df, p_limit, v_limit, use_breakout, use_macd_flip):
    if df is None or len(df) < 10: return None, ""
    last = df.iloc[-1]; prev = df.iloc[-2]
    price = float(last['Close'])
    p_change = ((price - float(prev['Close'])) / float(prev['Close'])) * 100
    v_ratio = float(last['Volume']) / float(last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    
    reasons = []
    sig_type = None

    # 1. 均線量價
    is_bull = price > last['EMA200'] and last['EMA20'] > last['EMA60']
    is_bear = price < last['EMA200'] and last['EMA20'] < last['EMA60']
    
    # 2. 5K 突破
    is_break_h, is_break_l = False, False
    if use_breakout:
        max5, min5 = df.iloc[-6:-1]['High'].max(), df.iloc[-6:-1]['Low'].min()
        is_break_h, is_break_l = price > max5, price < min5

    # 3. MACD 反轉
    m_bull, m_bear = False, False
    if use_macd_flip and len(df) >= 8:
        hw = df['Hist'].iloc[-8:].values
        m_bull = all(x < 0 for x in hw[:-1]) and hw[-1] > 0
        m_bear = all(x > 0 for x in hw[:-1]) and hw[-1] < 0

    if (is_bull and p_change >= p_limit and v_ratio >= v_limit) or is_break_h or m_bull:
        sig_type = "BULL"
        if is_bull and p_change >= p_limit: reasons.append(f"• 量價強勢({p_change:+.2f}%)")
        if is_break_h: reasons.append("• 突破前5K高點")
        if m_bull: reasons.append("• MACD 7負轉1正")
        
    elif (is_bear and p_change <= -p_limit and v_ratio >= v_limit) or is_break_l or m_bear:
        sig_type = "BEAR"
        if is_bear and p_change <= -p_limit: reasons.append(f"• 量價轉弱({p_change:+.2f}%)")
        if is_break_l: reasons.append("• 跌破前5K低點")
        if m_bear: reasons.append("• MACD 7正轉1負")

    return sig_type, "\n".join(reasons)

# --- 6. 側邊欄 ---
with st.sidebar:
    st.header("⚙️ 參數設定")
    input_symbols = st.text_input("股票代碼", value="TSLA, NIO, TSLL, XPEV, META, GOOGL, AAPL, NVDA, AMZN, MSFT, TSM, GLD, BTC-USD").upper()
    symbols = [s.strip() for s in input_symbols.split(",") if s.strip()]
    
    st.subheader("⏱ 週期共振設定")
    selected_intervals = st.multiselect("監測週期 (需同步才通知)", ["1m", "5m", "15m", "30m", "1h"], default=["5m", "15m"])
    sel_range = st.selectbox("數據範圍", ["1d", "5d", "1mo"], index=1)
    refresh_rate = st.slider("刷新頻率 (秒)", 30, 600, 60)
    
    st.divider()
    st.subheader("🎯 獨立價格預警")
    custom_alert_input = st.text_area("格式: TSLA > 420\n(多條請換行)", value="", placeholder="TSLA 升穿 420\nAAPL < 200")
    
    st.divider()
    vol_threshold = st.number_input("成交量倍數", value=2.0, step=0.5)
    price_threshold = st.number_input("價格異動(%)", value=1.0, step=0.1)
    use_breakout = st.checkbox("5K 突破監控", value=True)
    use_macd_flip = st.checkbox("MACD 反轉監控", value=True)

# --- 7. 主循環 ---
st.title("📈 智能共振與價格監控")
placeholder = st.empty()

while True:
    all_dfs = {}
    with placeholder.container():
        if symbols and selected_intervals:
            cols = st.columns(len(symbols))
            for i, sym in enumerate(symbols):
                res_types = []
                res_details = {}
                main_df = None

                for interval in selected_intervals:
                    df = fetch_data(sym, sel_range, interval)
                    sig, detail = get_indicators_signal(df, price_threshold, vol_threshold, use_breakout, use_macd_flip)
                    res_types.append(sig)
                    if sig: res_details[interval] = detail
                    main_df = df # 以最後一個週期為顯示基準

                if main_df is not None:
                    all_dfs[sym] = main_df
                    cur_p = main_df['Close'].iloc[-1]
                    cur_pc = ((main_df['Close'].iloc[-1] - main_df['Close'].iloc[-2]) / main_df['Close'].iloc[-2]) * 100
                    
                    # 邏輯 A: 自定義價格觸發 (單獨觸發)
                    hit_price, price_reason = process_custom_price_alerts(sym, cur_p, custom_alert_input)
                    if hit_price:
                        send_telegram_msg(sym, "🎯 【價格水平達標】", price_reason, cur_p, cur_pc)

                    # 邏輯 B: 多週期共振觸發
                    is_all_bull = all(r == "BULL" for r in res_types)
                    is_all_bear = all(r == "BEAR" for r in res_types)
                    
                    status, color, style = "⚖️ 觀望", "#aaaaaa", ""
                    if is_all_bull:
                        status, color, style = "🚀 多頭共振", "#00ff00", "blink-bull"
                        send_telegram_msg(sym, "🔥 【多週期共振-多】", "\n".join([f"[{k}]\n{v}" for k,v in res_details.items()]), cur_p, cur_pc)
                    elif is_all_bear:
                        status, color, style = "🔻 空頭共振", "#ff4b4b", "blink-bear"
                        send_telegram_msg(sym, "❄️ 【多週期共振-空】", "\n".join([f"[{k}]\n{v}" for k,v in res_details.items()]), cur_p, cur_pc)

                    cols[i].markdown(f"""
                        <div class='{style}' style='border:1px solid #444; padding:15px; border-radius:10px; text-align:center;'>
                            <h3 style='margin:0;'>{sym}</h3>
                            <h2 style='color:{color}; margin:10px 0;'>{status}</h2>
                            <p style='font-size:1.3em; margin:0;'><b>{cur_p:.2f}</b></p>
                            <p style='font-size:0.8em; color:{"#00ff00" if hit_price else "#888"}; margin-top:5px;'>
                                {"🎯 價格預警已觸發" if hit_price else f"共振進度: {len(res_details)}/{len(selected_intervals)}"}
                            </p>
                        </div>
                    """, unsafe_allow_html=True)

        st.divider()
        if all_dfs:
            tabs = st.tabs(list(all_dfs.keys()))
            for i, (sym, df) in enumerate(all_dfs.items()):
                with tabs[i]:
                    pdf = df.tail(35)
                    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
                    fig.add_trace(go.Candlestick(x=pdf.index, open=pdf['Open'], high=pdf['High'], low=pdf['Low'], close=pdf['Close'], name='K線'), row=1, col=1)
                    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['EMA20'], name='EMA20', line=dict(color='yellow', width=1)), row=1, col=1)
                    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['EMA200'], name='EMA200', line=dict(color='red', width=1.5)), row=1, col=1)
                    colors = ['#00ff00' if x >= 0 else '#ff4b4b' for x in pdf['Hist']]
                    fig.add_trace(go.Bar(x=pdf.index, y=pdf['Hist'], name='MACD', marker_color=colors), row=2, col=1)
                    fig.update_layout(height=450, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=10,r=10,t=10,b=10))
                    st.plotly_chart(fig, use_container_width=True, key=f"cht_{sym}")
        st.caption(f"📅 最後更新: {datetime.now().strftime('%H:%M:%S')}")
    time.sleep(refresh_rate)
