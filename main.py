import time
import requests
import os
import pytz
import holidays
import pandas as pd
import yfinance as yf
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ==========================================
# ⚙️ 全局配置区
# ==========================================

WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
# 注意：本版本彻底移除了 FMP_API_KEY，因为改用免费的 Yahoo 源

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
    return "█" * int(p//10) + "░" * (10 - int(p//10))

# ==========================================
# 🟢 模块 1: 降息概率 (Selenium) - 保持不变
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
        driver.set_page_load_timeout(45) # 稍微增加超时
        
        driver.get("https://www.investing.com/central-banks/fed-rate-monitor")
        time.sleep(8) # 等待页面加载
        
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
# 🔵 模块 2: 市场广度 (Yahoo Finance 免费版)
# ==========================================
def run_breadth_task():
    print("📊 启动市场广度统计 (Yahoo Finance)...")
    
    try:
        # 1. 获取标普500名单 (从维基百科抓取，最稳)
        print("📥 正在获取成分股名单 (Wikipedia)...")
        try:
            # Pandas 自动解析网页表格
            table = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
            df_tickers = table[0]
            tickers = df_tickers['Symbol'].tolist()
            # 修正符号: Yahoo使用 'BRK-B' 而不是 'BRK.B'
            tickers = [t.replace('.', '-') for t in tickers]
        except Exception as e:
            print(f"❌ 维基百科抓取失败: {e}, 使用备用列表")
            tickers = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK-B', 'LLY', 'AVGO']

        print(f"✅ 准备下载 {len(tickers)} 只股票数据...")
        
        # 2. 批量下载数据 (Yahoo Finance)
        # 下载过去 300 天的数据，足以计算 200日均线
        data = yf.download(tickers, period="1y", progress=False)
        
        # 只取收盘价
        if 'Close' in data.columns:
            closes = data['Close']
        else:
            closes = data
            
        print("✅ 数据下载完成，正在计算均线...")

        # 3. 计算指标
        # 获取最新价格 (最后一行)
        current_prices = closes.iloc[-1]
        
        # 计算均线 (利用 Pandas 强大的整表计算)
        # axis=0 表示按列(每只股票)计算
        ma50 = closes.rolling(window=50).mean().iloc[-1]
        ma200 = closes.rolling(window=200).mean().iloc[-1]
        
        # 统计
        above_50 = (current_prices > ma50).sum()
        above_200 = (current_prices > ma200).sum()
        total_valid = closes.shape[1] # 列数即为股票数
        
        if total_valid == 0:
            print("⚠️ 有效数据为 0")
            return

        p50 = (above_50 / total_valid) * 100
        p200 = (above_200 / total_valid) * 100
        
        # 4. 推送
        payload = {
            "username": BREADTH_BOT_NAME,
            "avatar_url": BREADTH_BOT_AVATAR,
            "embeds": [{
                "title": "📊 S&P 500 市场广度",
                "description": f"**日期:** `{datetime.now().strftime('%Y-%m-%d')}`\n"
                               f"*(数据源: Yahoo Finance)*\n\n"
                               f"🟢 **股价 > 50日均线:** **{p50:.1f}%**\n"
                               f"{get_bar(p50)}\n"
                               f"*(中期趋势判断)*\n\n"
                               f"🔵 **股价 > 200日均线:** **{p200:.1f}%**\n"
                               f"{get_bar(p200)}\n"
                               f"*(长期牛熊分界)*",
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
    print("🚀 机器人启动 (Yahoo源终极版)")
    
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
        time.sleep(30)
