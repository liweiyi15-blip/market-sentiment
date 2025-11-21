import time
import requests
import os
import pytz
import holidays
import pandas as pd
import yfinance as yf
import io
import json
import warnings
import re
import shutil 
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ==========================================
# ⚙️ 全局配置区
# ==========================================

WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
NEXT_MEETING_DATE = "2025-12-10"
DEFAULT_BASE_RATE = 3.75 

# ⏰ 时间表 (美东时间 ET)
FED_SCHEDULE_TIMES = ["08:31", "09:31", "11:31", "13:31", "15:31"]
BREADTH_SCHEDULE_TIME = "16:30"

# ------------------------------------------
# 🏛️ FedWatch 配置
# ------------------------------------------
FED_BOT_NAME = "CME FedWatch Bot"
FED_BOT_AVATAR = "https://i.imgur.com/d8KLt6Z.png"

# ------------------------------------------
# 📊 市场广度 配置
# ------------------------------------------
BREADTH_BOT_NAME = "标普500 广度日报" 
BREADTH_BOT_AVATAR = "https://i.imgur.com/Segc5PF.jpeg"

PREV_CUT_PROB = None

# ==========================================
# 🛠️ 辅助函数
# ==========================================
def is_market_holiday(now_et):
    if now_et.weekday() >= 5: return True, "周末休市"
    us_holidays = holidays.US(years=now_et.year) 
    if now_et.date() in us_holidays: return True, f"假期: {us_holidays.get(now_et.date())}"
    return False, None

def get_bar(p):
    length = 15
    filled = int(p / 100 * length)
    return "█" * filled + "░" * (length - filled)

def format_target_label(target_str, current_rate_base):
    try:
        lower_bound = float(target_str.split('-')[0].strip())
        if abs(lower_bound - current_rate_base) <= 0.05:
            return f"{target_str} (维持)"
        elif lower_bound < current_rate_base:
            return f"{target_str} (降息)"
        else:
            return f"{target_str} (加息)"
    except:
        return target_str

# ==========================================
# 🟢 模块 1: 降息概率 (强制排序版)
# ==========================================

def fetch_backup_rate_from_tradingeconomics(driver):
    """ Plan B """
    print("🔄 [Plan B] 正在尝试 TradingEconomics...")
    try:
        driver.get("https://tradingeconomics.com/united-states/interest-rate")
        time.sleep(5)
        row_element = driver.find_element(By.XPATH, "//tr[contains(., 'Fed Interest Rate')]")
        row_text = row_element.text
        match = re.search(r"(\d+\.\d+)", row_text)
        if match:
            upper = float(match.group(1))
            if 0.0 <= upper <= 10.0:
                lower = upper - 0.25
                print(f"✅ [Plan B] 成功: {lower}%")
                return lower
        return None
    except Exception as e:
        print(f"❌ [Plan B] 失败: {e}")
        return None

def get_fed_data():
    print(f"⚡ 启动 Chromium...")
    options = Options()
    options.binary_location = "/usr/bin/chromium" 
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

    driver = None
    detected_base_rate = None
    
    try:
        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.get("https://www.investing.com/central-banks/fed-rate-monitor")
        time.sleep(5) 
        
        # Plan A
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text
            match = re.search(r"Current.*?Rate.*?(\d+\.?\d*)", page_text, re.IGNORECASE | re.DOTALL)
            if match:
                val = float(match.group(1))
                if 3.0 <= val <= 6.0:
                    detected_base_rate = val
                    print(f"✅ [Plan A] 抓取成功: {detected_base_rate}%")
        except: pass

        data_points = []
        try:
            tables = driver.find_elements(By.TAG_NAME, "table")
            target_table = None
            for tbl in tables:
                if "%" in tbl.text and len(tbl.find_elements(By.TAG_NAME, "tr")) < 15:
                    target_table = tbl
                    break
            if not target_table and tables: target_table = tables[0]

            if target_table:
                rows = target_table.find_elements(By.TAG_NAME, "tr")
                for row in rows:
                    cols = row.find_elements(By.TAG_NAME, "td")
                    if len(cols) >= 2:
                        txt0, txt1 = cols[0].text.strip(), cols[1].text.strip()
                        try:
                            if "%" in txt0: prob, target = float(txt0.replace("%", "")), txt1
                            elif "%" in txt1: prob, target = float(txt1.replace("%", "")), txt0
                            else: continue
                            data_points.append({"prob": prob, "target": target})
                        except: continue
        except: pass

        if detected_base_rate is None:
            if data_points:
                bk = fetch_backup_rate_from_tradingeconomics(driver)
                detected_base_rate = bk if bk else DEFAULT_BASE_RATE
            else:
                 detected_base_rate = DEFAULT_BASE_RATE

        if not data_points: return None
        
        # 返回所有数据供 send_fed_embed 排序使用
        return {"current_base_rate": detected_base_rate, "data": data_points}

    except Exception as e:
        print(f"❌ Error: {e}")
        return None
    finally:
        if driver:
            try: driver.quit()
            except: pass

def send_fed_embed(data):
    global PREV_CUT_PROB
    if not data or not data['data']: return
    
    base_rate = data.get("current_base_rate", DEFAULT_BASE_RATE)
    
    # 1. 计算降息趋势 (逻辑不变，依然追踪特定降息项)
    current_cut_prob = 0.0
    target_cut_lower = base_rate - 0.25
    
    # 将数据分类：降息项 vs 其他项
    cut_item = None
    rest_items = []
    
    for item in data['data']:
        try:
            lower = float(item['target'].split('-')[0].strip())
            # 找到降息 25bp 的那一项
            if abs(lower - target_cut_lower) <= 0.05:
                cut_item = item
                current_cut_prob = item['prob']
            else:
                rest_items.append(item)
        except:
            rest_items.append(item)
    
    # 计算变动
    delta = 0.0
    if PREV_CUT_PROB is not None:
        delta = current_cut_prob - PREV_CUT_PROB
    PREV_CUT_PROB = current_cut_prob
    
    # 趋势文案
    trend_title = "📉 降息趋势变动"
    if not cut_item and current_cut_prob == 0:
        trend_text = "无降息预期"
    elif delta > 0.1: 
        trend_text = f"概率上升 +{delta:.1f}% 🔥"
    elif delta < -0.1: 
        trend_text = f"概率下降 {delta:.1f}% ❄️"
    else:
        trend_text = "稳定"

    # ==================================================
    # 🟢 排序逻辑调整：强制降息排第一
    # ==================================================
    display_list = []
    
    # 1. 第一行：必须是降息项 (如果找到了)
    if cut_item:
        display_list.append(cut_item)
    
    # 2. 第二行：在剩下的项里，选概率最高的一个 (通常是维持)
    if rest_items:
        # 按概率从高到低排序
        rest_items.sort(key=lambda x: x['prob'], reverse=True)
        display_list.append(rest_items[0])
    
    # 如果没找到降息项 (极罕见)，就回退到只显示概率最高的两项
    if not display_list:
        data['data'].sort(key=lambda x: x['prob'], reverse=True)
        display_list = data['data'][:2]

    # ==================================================

    # 准备第一名的 Label (用于华尔街共识)
    # 注意：这里的 Top1 应该是概率最高的那个，而不是我们强制置顶的那个
    # 所以要重新在全部数据里找概率第一
    all_sorted = sorted(data['data'], key=lambda x: x['prob'], reverse=True)
    top1_real = all_sorted[0]
    
    label1_raw = format_target_label(top1_real['target'], base_rate)
    if "(维持)" in label1_raw: consensus_short = "⏸️ 维持利率 (Hold)"
    elif "(降息)" in label1_raw: consensus_short = "📉 降息 (Cut)"
    else: consensus_short = "📈 加息 (Hike)"

    # 构建 Embed 描述
    desc_lines = [f"**🗓️ 下次会议:** `{NEXT_MEETING_DATE}`\n"]
    
    for item in display_list:
        label = format_target_label(item['target'], base_rate)
        desc_lines.append(f"**目标: {label}**")
        desc_lines.append(f"`{get_bar(item['prob'])}` **{item['prob']}%**\n")
    
    desc_lines.append("\n------------------------")

    payload = {
        "username": FED_BOT_NAME,
        "avatar_url": FED_BOT_AVATAR,
        "embeds": [{
            "title": "🏛️ CME FedWatch™ (降息预期)",
            "description": "\n".join(desc_lines),
            "color": 0x3498DB,
            "fields": [
                {"name": trend_title, "value": trend_text, "inline": True},
                {"name": "💡 华尔街共识", "value": consensus_short, "inline": True}
            ],
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M')} ET"}
        }]
    }
    try: requests.post(WEBHOOK_URL, json=payload)
    except Exception as e: print(f"❌ 推送失败: {e}")

# ==========================================
# 🔵 模块 2: 市场广度
# ==========================================
def generate_breadth_chart(breadth_20_series, breadth_50_series):
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(breadth_20_series.index, breadth_20_series.values, color='#f1c40f', linewidth=2, label='Stocks > 20 Day SMA %')
    ax.plot(breadth_50_series.index, breadth_50_series.values, color='#e74c3c', linewidth=2, label='Stocks > 50 Day SMA %')
    ax.fill_between(breadth_20_series.index, breadth_20_series.values, alpha=0.1, color='#f1c40f')
    ax.axhline(y=80, color='#ff5252', linestyle='--', linewidth=1, alpha=0.8) 
    ax.text(breadth_20_series.index[0], 81, 'Overbought (80%)', color='#ff5252', fontsize=8)
    ax.axhline(y=20, color='#448aff', linestyle='--', linewidth=1, alpha=0.8) 
    ax.text(breadth_20_series.index[0], 21, 'Oversold (20%)', color='#448aff', fontsize=8)
    ax.set_title('S&P 500 Market Breadth (20 & 50 Day SMA)', fontsize=12, color='white', pad=15)
    ax.set_ylim(0, 100)
    ax.grid(True, linestyle=':', alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    plt.xticks(rotation=0)
    ax.legend(loc='upper left', frameon=True, facecolor='#2f3136', edgecolor='#2f3136', labelcolor='white')
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=100, facecolor='#2b2d31')
    buf.seek(0)
    plt.close()
    return buf

def get_market_sentiment(p):
    if p > 80: return "🔥🔥 **深度火热**"
    if p > 60: return "🔥 **火热**"      
    if p < 20: return "❄️❄️ **深度寒冷**"
    if p < 40: return "❄️ **寒冷**"      
    return "🍃 **稳定**"    

def run_breadth_task():
    print("📊 启动市场广度统计...")
    try:
        try:
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
            tables = pd.read_html(io.StringIO(resp.text))
            df_tickers = next((df for df in tables if 'Symbol' in df.columns), None)
            tickers = [t.replace('.', '-') for t in df_tickers['Symbol'].tolist()] 
        except:
            tickers = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK-B', 'LLY', 'AVGO']

        warnings.simplefilter(action='ignore', category=FutureWarning)
        try:
            if os.path.exists('yfinance.cache'): shutil.rmtree('yfinance.cache')
        except: pass

        data = yf.download(tickers, period="1y", progress=False) 
        if 'Close' in data.columns: closes = data['Close']
        else: closes = data

        sma20_df = closes.rolling(window=20).mean()
        sma50_df = closes.rolling(window=50).mean()
        
        daily_breadth_20 = ((closes > sma20_df).sum(axis=1) / closes.notna().sum(axis=1)) * 100
        daily_breadth_50 = ((closes > sma50_df).sum(axis=1) / closes.notna().sum(axis=1)) * 100
        
        chart_buffer = generate_breadth_chart(daily_breadth_20.tail(252), daily_breadth_50.tail(252))
        current_p20 = daily_breadth_20.iloc[-1]
        current_p50 = daily_breadth_50.iloc[-1]
        
        sentiment_20 = get_market_sentiment(current_p20)
        sentiment_50 = get_market_sentiment(current_p50)

        payload_data = {
            "username": BREADTH_BOT_NAME,
            "avatar_url": BREADTH_BOT_AVATAR,
            "embeds": [{
                "title": "S&P 500 市场广度",
                "description": f"**日期:** `{datetime.now().strftime('%Y-%m-%d')}`\n\n"
                               f"**股价 > 20日均线:** **{current_p20:.1f}%**\n"
                               f"{sentiment_20}\n\n"
                               f"**股价 > 50日均线:** **{current_p50:.1f}%**\n"
                               f"{sentiment_50}",
                "color": 0xF1C40F,
                "image": {"url": "attachment://chart.png"},
                "footer": {
                    "text": f"💡 标普500大于20日、50日均的数量\n💡 >80% 警惕回调，<20% 孕育反弹。\n（统计样本: {len(tickers)}只成分股）"
                }
            }]
        }
        
        files = {'file': ('chart.png', chart_buffer, 'image/png')}
        requests.post(WEBHOOK_URL, data={'payload_json': json.dumps(payload_data)}, files=files)
        print(f"✅ 广度报告已推送")

    except Exception as e:
        print(f"❌ 广度任务异常: {e}")

# ==========================================
# 🚀 主程序
# ==========================================
if __name__ == "__main__":
    print("🚀 监控服务已启动")
    
    print("🧪 [测试] 正在发送 FedWatch (强制排序版)...")
    fed_data = get_fed_data()
    if fed_data: 
        send_fed_embed(fed_data)
        print("✅ FedWatch 测试完成")
    else:
        print("⚠️ FedWatch 获取失败")

    print("🧪 [测试] 正在发送 市场广度...")
    run_breadth_task()
    
    print("✅ 所有测试结束，进入定时监听模式...")

    last_run_time_str = ""
    while True:
        tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(tz)
        current_str = now_et.strftime("%H:%M")
        is_holiday, _ = is_market_holiday(now_et)

        if current_str != last_run_time_str:
            print(f"⏰ {current_str} ET")
            if not is_holiday and current_str in FED_SCHEDULE_TIMES:
                data = get_fed_data()
                if data: send_fed_embed(data)
            if not is_holiday and current_str == BREADTH_SCHEDULE_TIME:
                run_breadth_task()
            last_run_time_str = current_str
        
        time.sleep(30)
