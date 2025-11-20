import time
import requests
import os
import pytz
import holidays
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ==========================================
# ⚙️ 全局配置区
# ==========================================

# 🔑 密钥与 URL
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
FMP_API_KEY = os.getenv("FMP_API_KEY") 

# 📅 下次美联储会议时间
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
        driver.set_page_load_timeout(30)
        
        driver.get("https://www.investing.com/central-banks/fed-rate-monitor")
        time.sleep(5)
        
        data_points = []
        current_rate = "Unknown"
        
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
        return {"current": current_rate, "data": data_points[:2]}

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
# 🔵 模块 2: 市场广度 (Github List + FMP Price)
# ==========================================
def run_breadth_task():
    print("📊 启动市场广度统计 (GitHub源 + FMP报价)...")
    if not FMP_API_KEY:
        print("❌ 错误: 未设置 FMP_API_KEY")
        return

    try:
        # 1. 【关键修改】从 GitHub 获取免费的 SP500 列表
        # 彻底绕过 FMP 的收费列表接口
        github_list_url = "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/all/sp500_tickers.json"
        print(f"📥 正在下载成分股名单: {github_list_url}")
        
        resp = requests.get(github_list_url, timeout=10)
        if resp.status_code != 200:
            print("❌ 无法从 GitHub 获取列表，尝试备用源...")
            # 备用：只测几大权重股，保证不报错
            tickers = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK.B', 'LLY', 'AVGO', 'JPM', 'V', 'UNH']
        else:
            tickers = resp.json()

        print(f"✅ 获取到 {len(tickers)} 只成分股，开始向 FMP 查询价格...")
        
        # 2. 批量向 FMP 查询价格 (这是允许的)
        batch_size = 50 # 降低每批数量，提高稳定性
        above_50, above_200, total = 0, 0, 0
        
        for i in range(0, len(tickers), batch_size):
            batch = ",".join(tickers[i:i+batch_size])
            # 这里的 endpoint 是 quote，Starter 用户可用
            url = f"https://financialmodelingprep.com/api/v3/quote/{batch}?apikey={FMP_API_KEY}"
            
            try:
                q_res = requests.get(url, timeout=10)
                if q_res.status_code == 200:
                    q_data = q_res.json()
                    if isinstance(q_data, list):
                        for stock in q_data:
                            p = stock.get('price')
                            ma50 = stock.get('priceAvg50')
                            ma200 = stock.get('priceAvg200')
                            
                            if p and ma50 and ma200:
                                total += 1
                                if p > ma50: above_50 += 1
                                if p > ma200: above_200 += 1
                else:
                    print(f"⚠️ 批次查询 FMP 失败: {q_res.text}")
            except Exception as e:
                print(f"⚠️ 网络波动跳过一批: {e}")
                continue

        if total == 0:
            print("⚠️ 未获取到有效数据")
            return

        p50 = (above_50 / total) * 100
        p200 = (above_200 / total) * 100
        
        payload = {
            "username": BREADTH_BOT_NAME,
            "avatar_url": BREADTH_BOT_AVATAR,
            "embeds": [{
                "title": "📊 S&P 500 市场广度",
                "description": f"**日期:** `{datetime.now().strftime('%Y-%m-%d')}`\n"
                               f"*(美股收盘统计)*\n\n"
                               f"🟢 **股价 > 50日均线:** **{p50:.1f}%**\n"
                               f"{get_bar(p50)}\n"
                               f"*(中期趋势判断)*\n\n"
                               f"🔵 **股价 > 200日均线:** **{p200:.1f}%**\n"
                               f"{get_bar(p200)}\n"
                               f"*(长期牛熊分界)*",
                "color": 0xF1C40F,
                "footer": {"text": f"统计样本: {total} 只 (Source: GitHub List + FMP Quote)"}
            }]
        }
        requests.post(WEBHOOK_URL, json=payload)
        print(f"✅ 广度报告已推送: >50MA={p50:.1f}%")

    except Exception as e:
        print(f"❌ 任务异常: {e}")

# ==========================================
# 🚀 主程序
# ==========================================
if __name__ == "__main__":
    print("🚀 机器人启动 (GitHub列表源版)")
    
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
