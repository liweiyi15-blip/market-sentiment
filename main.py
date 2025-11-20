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

# 🔑 密钥与 URL (从环境变量获取)
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
FMP_API_KEY = os.getenv("FMP_API_KEY") # 必须在 Railway 变量中设置

# 📅 下次会议时间
NEXT_MEETING_DATE = "2025-12-10"

# ⏰ 时间表 (美东时间 ET)
# 1. 降息预测: 盘前/盘中整点
FED_SCHEDULE_TIMES = ["08:31", "09:31", "11:31", "13:31", "15:31"]
# 2. 市场广度: 收盘后 (建议 16:30 确保数据已结算)
BREADTH_SCHEDULE_TIME = "16:30"

# 🤖 机器人角色配置 (双面人)
# 角色 A: 降息预测
FED_BOT_NAME = "🏛️ 美联储利率观察"
FED_BOT_AVATAR = "https://cdn-icons-png.flaticon.com/512/2156/2156009.png" 

# 角色 B: 市场广度
BREADTH_BOT_NAME = "📊 标普500 广度日报"
BREADTH_BOT_AVATAR = "https://cdn-icons-png.flaticon.com/512/3310/3310665.png" 

# 全局变量记录上次概率，用于计算变动
PREV_CUT_PROB = None

# ==========================================
# 🛠️ 辅助函数
# ==========================================
def is_market_holiday(now_et):
    """判断是否为周末或美股节假日"""
    # 1. 周末 (5=Sat, 6=Sun)
    if now_et.weekday() >= 5:
        return True, "周末休市"
    
    # 2. 节假日
    us_holidays = holidays.US(years=now_et.year) 
    if now_et.date() in us_holidays:
        return True, f"假期: {us_holidays.get(now_et.date())}"
        
    return False, None

def get_bar(p):
    """生成进度条"""
    return "█" * int(p//10) + "░" * (10 - int(p//10))

# ==========================================
# 🟢 模块 1: 降息概率 (Selenium)
# ==========================================
def get_fed_data():
    print(f"⚡ 启动 Chromium 抓取 FedWatch...")
    options = Options()
    # Railway/Docker 常用路径配置
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
        
        url = "https://www.investing.com/central-banks/fed-rate-monitor"
        driver.get(url)
        time.sleep(5) # 等待渲染
        
        data_points = []
        current_rate = "Unknown"
        
        # 寻找包含 % 的表格
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
                    txt0 = cols[0].text.strip()
                    txt1 = cols[1].text.strip()
                    try:
                        # 解析概率和目标利率
                        if "%" in txt0:
                            prob = float(txt0.replace("%", ""))
                            target = txt1
                        elif "%" in txt1:
                            prob = float(txt1.replace("%", ""))
                            target = txt0
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
    top2 = data['data'][1] if len(data['data']) > 1 else None
    
    # 简单逻辑：假设 Target 越小越可能是降息
    # 这里简化处理，只取 Top1 的概率作为主要指标
    cut_prob_value = top1['prob'] 

    # 趋势计算
    delta = 0.0
    if PREV_CUT_PROB is not None:
        delta = cut_prob_value - PREV_CUT_PROB
    PREV_CUT_PROB = cut_prob_value
    
    trend_str = "稳定"
    if delta > 0.1: trend_str = f"概率上升 +{delta:.1f}% 🔥"
    elif delta < -0.1: trend_str = f"概率下降 {delta:.1f}% ❄️"

    desc = [
        f"**🗓️ 下次会议:** `{NEXT_MEETING_DATE}`",
        "",
        f"**目标: {top1['target']} (主要)**", 
        f"{get_bar(top1['prob'])} **{top1['prob']}%**",
        ""
    ]
    if top2:
        desc.append(f"**目标: {top2['target']} (次要)**")
        desc.append(f"{get_bar(top2['prob'])} **{top2['prob']}%**")

    payload = {
        "username": FED_BOT_NAME,     # <--- 角色 A 名称
        "avatar_url": FED_BOT_AVATAR, # <--- 角色 A 头像
        "embeds": [{
            "title": "🏛️ CME FedWatch™ (降息预期)",
            "description": "\n".join(desc),
            "color": 0x3498DB, # 蓝色
            "fields": [
                {"name": "📊 趋势变动", "value": trend_str, "inline": True},
                {"name": "💡 市场共识", "value": f"押注 {top1['target']}", "inline": True}
            ],
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M')} ET"}
        }]
    }
    try:
        requests.post(WEBHOOK_URL, json=payload)
        print(f"✅ Fed 报告已推送")
    except Exception as e: print(f"❌ 推送失败: {e}")

# ==========================================
# 🔵 模块 2: 市场广度 (FMP API)
# ==========================================
def run_breadth_task():
    print("📊 启动市场广度统计 (FMP API)...")
    if not FMP_API_KEY:
        print("❌ 错误: 未设置 FMP_API_KEY")
        return

    try:
        # 1. 获取成分股列表
        sp500_url = f"https://financialmodelingprep.com/api/v3/sp500_constituent?apikey={FMP_API_KEY}"
        tickers = [item['symbol'] for item in requests.get(sp500_url).json()]
        
        # 2. 批量获取报价 (含 priceAvg50, priceAvg200)
        # 每次请求 100 个以加快速度
        batch_size = 100
        above_50, above_200, total = 0, 0, 0
        
        for i in range(0, len(tickers), batch_size):
            batch = ",".join(tickers[i:i+batch_size])
            url = f"https://financialmodelingprep.com/api/v3/quote/{batch}?apikey={FMP_API_KEY}"
            data = requests.get(url).json()
            
            for stock in data:
                p = stock.get('price')
                ma50 = stock.get('priceAvg50')
                ma200 = stock.get('priceAvg200')
                
                if p and ma50 and ma200:
                    total += 1
                    if p > ma50: above_50 += 1
                    if p > ma200: above_200 += 1
        
        if total == 0: return

        p50 = (above_50 / total) * 100
        p200 = (above_200 / total) * 100
        
        # 构建 Embed
        payload = {
            "username": BREADTH_BOT_NAME,     # <--- 角色 B 名称
            "avatar_url": BREADTH_BOT_AVATAR, # <--- 角色 B 头像
            "embeds": [{
                "title": "📊 S&P 500 市场广度日报",
                "description": f"**日期:** `{datetime.now().strftime('%Y-%m-%d')}`\n"
                               f"*(美股收盘统计)*\n\n"
                               f"🟢 **股价 > 50日均线:** **{p50:.1f}%**\n"
                               f"{get_bar(p50)}\n"
                               f"*(中期趋势判断)*\n\n"
                               f"🔵 **股价 > 200日均线:** **{p200:.1f}%**\n"
                               f"{get_bar(p200)}\n"
                               f"*(长期牛熊分界)*",
                "color": 0xF1C40F, # 金色
                "footer": {"text": f"统计样本: {total} 只成分股 • Data via FMP"}
            }]
        }
        requests.post(WEBHOOK_URL, json=payload)
        print(f"✅ 广度报告已推送: >50MA={p50:.1f}%")

    except Exception as e:
        print(f"❌ 广度统计失败: {e}")

# ==========================================
# 🚀 主程序循环
# ==========================================
if __name__ == "__main__":
    print("🚀 双功能机器人已启动 (FedWatch + MarketBreadth)")
    print(f"📅 Fed 时间点: {FED_SCHEDULE_TIMES}")
    print(f"📅 广度 时间点: {BREADTH_SCHEDULE_TIME}")
    
    last_run_time_str = ""

    while True:
        # 获取当前美东时间
        tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(tz)
        current_str = now_et.strftime("%H:%M")
        
        # 检查是否为休市日
        is_holiday, reason = is_market_holiday(now_et)

        if current_str != last_run_time_str:
            print(f"⏰ 时间检查: {current_str} ET (Holiday: {is_holiday})")
            
            # --- 任务 1: 降息预测 (仅在交易日运行) ---
            if not is_holiday and current_str in FED_SCHEDULE_TIMES:
                print(f"⚡ 触发 Fed 任务...")
                data = get_fed_data()
                if data: send_fed_embed(data)
            
            # --- 任务 2: 市场广度 (仅在交易日运行) ---
            # 如果需要在收盘后运行，确保时间在 SCHEDULE 设置正确
            if not is_holiday and current_str == BREADTH_SCHEDULE_TIME:
                print(f"⚡ 触发 广度 任务...")
                run_breadth_task()
            
            # --- (可选) 周末心跳包，防止认为挂了 ---
            # if is_holiday and current_str == "12:00":
            #    print("😴 周末休眠中...")

            last_run_time_str = current_str
        
        # 避免 CPU 占用过高，每次检查间隔 30 秒
        time.sleep(30)
