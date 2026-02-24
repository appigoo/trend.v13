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
st.set_page_config(page_title="專業級 Day Trader 監控系統", layout="wide")

st.markdown("""
<style>
@keyframes blink { 0% { border-color: #444; } 50% { border-color: #ff4b4b; box-shadow: 0 0 15px #ff4b4b; } 100% { border-color: #444; } }
.blink-bull { border: 3px solid #00ff00 !important; animation: blink 1s infinite; background-color: rgba(0, 255, 0, 0.05); }
.blink-bear { border: 3px solid #ff4b4b !important; animation: blink 1s infinite; background-color: rgba(255, 75, 75, 0.05); }
.vix-banner { padding: 10px; border-radius: 5px; text-align: center; margin-bottom: 20px; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- 2. 核心數據獲取 (含 VIX & SPY) ---
def get_market_context():
    try:
        # 抓取 VIX 和 SPY
        data = yf.download(["^VIX", "SPY"], period="2d", interval="5m", progress=False)
        vix_price = data['Close']['^VIX'].iloc[-1]
        vix_prev = data['Close']['^VIX'].iloc[-2]
        spy_change = ((data['Close']['SPY'].iloc[-1] - data['Close']['SPY'].iloc[-2]) / data['Close']['SPY'].iloc[-2]) * 100
        
        v_status = "🔴 恐慌" if vix_price > 25 else "🟡 波動" if vix_price > 20 else "🟢 平穩"
        v_trend = "📈 急升" if vix_price > vix_prev * 1.01 else "📉 緩解"
        return vix_price, spy_change, v_status, v_trend
    except:
        return 20.0, 0.0, "N/A", "N/A"

def fetch_pro_data(symbol, p, i):
    try:
        # 下載主數據
        df = yf.download(symbol, period=p, interval=i, progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        
        # 計算基礎指標
        close = df['Close'].squeeze()
        df['EMA20'] = close.ewm(span=20, adjust=False).mean()
        df['EMA200'] = close.ewm(span=200, adjust=False).mean()
        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        
        # MACD
        df['Hist'] = close.ewm(span=12).mean() - close.ewm(span=26).mean() - \
                     (close.ewm(span=12).mean() - close.ewm(span=26).mean()).ewm(span=9).mean()
        
        # --- 專業指標: ADR (Average Daily Range) ---
        df_d = yf.download(symbol, period="10d", interval="1d", progress=False)
        if not df_d.empty:
            adr = (df_d['High'] - df_d['Low']).mean()
            today_range = df_d['High'].iloc[-1] - df_d['Low'].iloc[-1]
            df['ADR_Pct'] = (today_range / adr) * 100 # 今日已跑波幅百分比
        
        return df
    except: return None

# --- 3. Telegram 通知 (含市場背景說明) ---
# --- 優化後的 Telegram 詳盡通知函式 ---
def send_pro_notification(sym, action, res_details, price, vix_info, pc, vr, adr_u):
    try:
        token = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        
        # 1. 市場環境診斷
        vix_val, spy_c, v_stat, v_trend = vix_info
        market_summary = f"{v_stat} ({v_trend}) | SPY: {spy_c:+.2f}%"
        
        # 2. 能量狀態診斷
        energy_warning = ""
        if adr_u > 90:
            energy_warning = "⚠️ 【警告：能量耗盡】今日波動已達 ADR 90% 以上，小心假突破！\n"
        elif adr_u < 30:
            energy_warning = "✅ 【空間充足】今日波幅尚小，突破後潛力較大。\n"

        # 3. 彙整各週期訊號細節
        period_brief = ""
        for interval, detail in res_details.items():
            # 將內部的細節符號化
            clean_detail = detail.replace("•", "  ▫️")
            period_brief += f"⏰ {interval} 週期:\n{clean_detail}\n"

        # 4. 組合最終訊息
        message = (
            f"🔔 {action}: {sym}\n"
            f"💰 現價: {price:.2f} ({pc:+.2f}%)\n"
            f"📊 量比: {vr:.1f}x | ADR已耗: {adr_u:.1f}%\n"
            f"--------------------\n"
            f"🌐 市場環境: {market_summary}\n"
            f"{energy_warning}"
            f"--------------------\n"
            f"📋 觸發細節:\n{period_brief}"
            f"--------------------\n"
            f"📅 時間: {datetime.now().strftime('%H:%M:%S')}"
        )
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.get(url, params={"chat_id": chat_id, "text": message}, timeout=5)
    except Exception as e:
        st.error(f"Telegram 發送出錯: {e}")
# --- 4. 單週期訊號判定 ---
def get_signal_pro(df, p_limit, v_limit, use_break, use_macd, vix_price):
    if df is None or len(df) < 10: return None, ""
    last = df.iloc[-1]
    price = float(last['Close'])
    pc = ((price - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100
    vr = float(last['Volume']) / float(last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    adr_usage = df['ADR_Pct'].iloc[-1] if 'ADR_Pct' in df.columns else 0
    
    reasons = []
    sig_type = None

    # 邏輯 A: 均線量價
    is_bull = price > last['EMA200'] and last['EMA20'] > last['EMA200']
    
    # 邏輯 B: 5K 突破
    is_brk_h = price > df.iloc[-6:-1]['High'].max() if use_break else False
    
    # 邏輯 C: MACD 反轉
    hw = df['Hist'].iloc[-8:].values
    m_flip = all(x < 0 for x in hw[:-1]) and hw[-1] > 0 if use_macd else False

    if (is_bull and pc >= p_limit and vr >= v_limit) or is_brk_h or m_flip:
        sig_type = "BULL"
        reasons.append(f"• 週期訊號觸發")
        if adr_usage > 90: reasons.append(f"⚠️ 體力警告: ADR已達{adr_usage:.0f}% (追多風險高)")
        if vix_price > 25: reasons.append("⚠️ 市場極端恐慌 (VIX > 25)")

    return sig_type, "\n".join(reasons), adr_usage

# --- 5. 側邊欄 ---
with st.sidebar:
    st.header("🏢 Trader 工作站")
    input_symbols = st.text_input("監控名單", value="TSLA, NIO, TSLL, XPEV, META, GOOGL, AAPL, NVDA, AMZN, MSFT, TSM, GLD, BTC-USD").upper()
    symbols = [s.strip() for s in input_symbols.split(",") if s.strip()]
    selected_intervals = st.multiselect("共振週期", ["1m", "5m", "15m", "30m"], default=["5m", "15m"])
    
    st.divider()
    custom_prices = st.text_area("🎯 關鍵位預警", placeholder="TSLA 升穿 420")
    
    st.divider()
    st.subheader("策略開關")
    use_brk = st.checkbox("5K 突破監控", value=True)
    use_macd = st.checkbox("MACD 7+1 反轉", value=True)
    refresh = st.slider("刷新頻率(秒)", 30, 300, 60)

# --- 6. 主介面 ---
vix, spy_c, v_stat, v_trend = get_market_context()
vix_col = "#ff4b4b" if vix > 25 else "#ffa500" if vix > 20 else "#00ff00"

st.markdown(f"""
    <div class="vix-banner" style="background-color: {vix_col}22; border: 1px solid {vix_col}; color: {vix_col};">
        市場背景狀況 | VIX: {vix:.2f} ({v_stat}) | SPY: {spy_c:+.2f}% | 趨勢: {v_trend}
    </div>
""", unsafe_allow_html=True)

placeholder = st.empty()

while True:
    all_dfs = {}
    vix, spy_c, v_stat, v_trend = get_market_context()
    
    with placeholder.container():
        cols = st.columns(len(symbols))
        for i, sym in enumerate(symbols):
            res_types = []
            res_details = {}
            main_df = None
            
            for interval in selected_intervals:
                df = fetch_pro_data(sym, "5d", interval)
                sig, det, adr_u = get_signal_pro(df, 1.0, 2.0, use_brk, use_macd, vix)
                res_types.append(sig)
                if sig: res_details[interval] = det
                main_df = df

            if main_df is not None:
                all_dfs[sym] = main_df
                cur_p = main_df['Close'].iloc[-1]
                
                # 獨立價格監控
                match = re.search(rf"{sym}\s*([><]|升穿|跌穿)\s*(\d+\.?\d*)", custom_prices.upper())
                hit_price = False
                if match:
                    op, target = match.group(1), float(match.group(2))
                    if (op in ['>', '升穿'] and cur_p >= target) or (op in ['<', '跌穿'] and cur_p <= target):
                        hit_price = True
                        send_pro_notification(sym, "🎯 價格位達標", f"觸及設定價格: {target}", cur_p, (vix,0,v_stat,v_trend))

                # 共振邏輯
                is_all_bull = all(r == "BULL" for r in res_types)
                status, color, style = "⚖️ 觀望", "#888", ""
                
                if is_all_bull:
                    status, color, style = "🚀 多頭共振", "#00ff00", "blink-bull"
                    send_pro_notification(sym, "🔥 多頭共振", "\n".join([f"[{k}] {v}" for k,v in res_details.items()]), cur_p, (vix,0,v_stat,v_trend))

                cols[i].markdown(f"""
                    <div class='{style}' style='border:1px solid #444; padding:15px; border-radius:10px; text-align:center;'>
                        <h3 style='margin:0;'>{sym}</h3>
                        <h2 style='color:{color}; margin:10px 0;'>{status}</h2>
                        <p style='font-size:1.3em; margin:0;'><b>{cur_p:.2f}</b></p>
                        <p style='font-size:0.8em; color:#ffa500;'>ADR已耗: {adr_u:.1f}%</p>
                    </div>
                """, unsafe_allow_html=True)

        # 繪製圖表 (略, 維持原有 Plotly 邏輯)
        st.divider()
        if all_dfs:
            tabs = st.tabs(list(all_dfs.keys()))
            for i, (sym, df) in enumerate(all_dfs.items()):
                with tabs[i]:
                    pdf = df.tail(35)
                    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05)
                    fig.add_trace(go.Candlestick(x=pdf.index, open=pdf['Open'], high=pdf['High'], low=pdf['Low'], close=pdf['Close']), row=1, col=1)
                    fig.add_trace(go.Bar(x=pdf.index, y=pdf['Hist'], name='MACD'), row=2, col=1)
                    fig.update_layout(height=400, template="plotly_dark", showlegend=False, margin=dict(l=10,r=10,t=10,b=10))
                    st.plotly_chart(fig, use_container_width=True)

    time.sleep(refresh)
