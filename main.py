import time
import requests
import os
import pytz
import holidays
import pandas as pd
import yfinance as yf
import io
import warnings
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ==========================================
# ⚙️ 全局配置区
# ==========================================

WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
NEXT_MEETING_DATE = "2025-12-10"

# ⏰ 时间表 (美东时间 ET)
FED_SCHEDULE_TIMES = ["08:31", "09:31", "11:31", "13:31", "15:31"]
BREADTH_SCHEDULE_TIME = "16:30"

# 🎭 机器人配置
FED_BOT_NAME = "🏛️ 美联储利率观察"
FED_BOT_AVATAR = "https://cdn-icons-png.flaticon.com/512/2156/2156009.png" 

BREADTH_BOT_NAME = "📊 标普500 广度日报"
BREADTH_BOT_AVATAR = "https://cdn-icons-png.flaticon.com/512/3310/3310665.png" 

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
    """Fed机器人仍需使用进度条，保留此函数"""
    return "█" * int(p//10) + "░" * (10 - int(p//10))

def get_market_status(p):
    """根据百分比判断市场冷热"""
    if p > 80: return "🔥 **市场火热**"
    if p < 20: return "❄️ **市场冰冷**"
    return "" # 中间状态不显示，保持简洁

# ==========================================
# 🟢 模块 1: 降息概率 (Selenium)
# ==========================================
def get_fed_data():
    print(f"⚡ 启动 Chromium 抓取 FedWatch...")
    options = Options()
    options.binary_location = "/usr/bin/chromium"
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

    driver = None
    try:
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(45)
        
        driver.get("https://www.investing.com/central-banks/fed-rate-monitor")
        time.sleep(8)
        
        data_points = []
        
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
        
        if not data_points: return None
        data_points.sort(key=lambda x: x['prob'], reverse=True)
        return {"current": "Unknown", "data": data_points[:2]}

    except Exception as e:
        print(f"❌ Selenium 抓取错误: {e}")
        return None
    finally:
        if driver:
            try: driver.quit()
            except: pass

def send_fed_embed(data):
    global PREV_CUT_PROB
    if not data or not data['data']: return
    
    top1 = data['data'][0]
    cut_prob_value = top1['prob'] 

    delta = 0.0
    if PREV_CUT_PROB is not None: delta = cut_prob_value - PREV_CUT_PROB
    PREV_CUT_PROB = cut_prob_value
    
    trend_str = "稳定"
    if delta > 0.1: trend_str = f"概率上升 +{delta:.1f}% 🔥"
    elif delta < -0.1: trend_str = f"概率下降 {delta:.1f}% ❄️"

    desc = [
        f"**🗓️ 下次会议:** `{NEXT_MEETING_DATE}`",
        "",
        f"**目标: {top1['target']}**", 
        f"{get_bar(top1['prob'])} **{top1['prob']}%**",
        ""
    ]
    
    payload = {
        "username": FED_BOT_NAME,
        "avatar_url": FED_BOT_AVATAR,
        "embeds": [{
            "title": "🏛️ CME FedWatch™ (降息预期)",
            "description": "\n".join(desc),
            "color": 0x3498DB,
            "fields": [{"name": "📊 趋势变动", "value": trend_str, "inline": True}],
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M')} ET"}
        }]
    }
    try: requests.post(WEBHOOK_URL, json=payload)
    except Exception as e: print(f"❌ 推送失败: {e}")

# ==========================================
# 🔵 模块 2: 市场广度 (Yahoo Finance)
# ==========================================
def run_breadth_task():
    print("📊 启动市场广度统计...")
    
    try:
        # 1. 获取标普500名单 (伪装浏览器抓取 Wikipedia)
        print("📥 获取成分股名单...")
        try:
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            
            table = pd.read_html(io.StringIO(resp.text))
            tickers = table[0]['Symbol'].tolist()
            tickers = [t.replace('.', '-') for t in tickers] # 修正 BRK.B -> BRK-B
            print(f"✅ 成功获取 {len(tickers)} 只成分股")
            
        except Exception as e:
            print(f"❌ 抓取列表失败: {e}, 使用备用列表")
            tickers = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK-B', 'LLY', 'AVGO']

        # 2. 批量下载数据
        warnings.simplefilter(action='ignore', category=FutureWarning)
        print(f"📥 下载 {len(tickers)} 只股票数据...")
        data = yf.download(tickers, period="1y", progress=False)
        
        if 'Close' in data.columns:
            closes = data['Close']
        else:
            closes = data # 如果只有1只股票的情况

        # 3. 计算指标
        current_prices = closes.iloc[-1]
        ma50 = closes.rolling(window=50).mean().iloc[-1]
        ma200 = closes.rolling(window=200).mean().iloc[-1]
        
        above_50 = (current_prices > ma50).sum()
        above_200 = (current_prices > ma200).sum()
        total_valid = closes.shape[1]
        
        if total_valid == 0: return

        p50 = (above_50 / total_valid) * 100
        p200 = (above_200 / total_valid) * 100
        
        # 4. 构建 Embed (应用你的样式要求)
        status_50 = get_market_status(p50)
        status_200 = get_market_status(p200)

        payload = {
            "username": BREADTH_BOT_NAME,
            "avatar_url": BREADTH_BOT_AVATAR,
            "embeds": [{
                "title": "📊 S&P 500 市场广度",
                "description": f"**日期:** `{datetime.now().strftime('%Y-%m-%d')}`\n\n"
                               f"**股价 > 50日均线:** **{p50:.1f}%** {status_50}\n"
                               f"*(中期趋势)*\n\n"
                               f"**股价 > 200日均线:** **{p200:.1f}%** {status_200}\n"
                               f"*(长期牛熊)*",
                "color": 0xF1C40F,
                "footer": {"text": f"统计样本: {total_valid} 只成分股"}
            }]
        }
        requests.post(WEBHOOK_URL, json=payload)
        print(f"✅ 广度报告已推送: >50MA={p50:.1f}%")

    except Exception as e:
        print(f"❌ 广度任务异常: {e}")

# ==========================================
# 🚀 主程序
# ==========================================
if __name__ == "__main__":
    print("🚀 机器人启动 (样式优化版)")
    
    print("🧪 启动测试：立即发送一次广度报告...")
    run_breadth_task()
    print("✅ 测试结束，进入监听...")

    last_run_time_str = ""
    while True:
        tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(tz)
        current_str = now_et.strftime("%H:%M")
        is_holiday, _ = is_market_holiday(now_et)

        if current_str != last_run_time_str:
            print(f"⏰ {current_str} ET (Holiday: {is_holiday})")
            
            if not is_holiday and current_str in FED_SCHEDULE_TIMES:
                data = get_fed_data()
                if data: send_fed_embed(data)
            
            if not is_holiday and current_str == BREADTH_SCHEDULE_TIME:
                run_breadth_task()
            
            last_run_time_str = current_str
        time
