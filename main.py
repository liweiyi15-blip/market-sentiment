import time
import requests
import os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ==========================================
# ⚙️ 配置区
# ==========================================
# 你的 Webhook
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://discord.com/api/webhooks/1440732182334148831/6ji21aLb5ZZ103Qp6WdbHkRiTXpxUf_pHa1BCZAadKpNWcGpvTXNfbY6r_534cjaHZAG")

# 下次会议时间 (显示在卡片顶部，你可以随时改)
NEXT_MEETING_DATE = "2025-12-10"

# 刷新间隔 (秒) - 建议 7200 (2小时)
CHECK_INTERVAL = 7200 

# 全局变量：存储上一次的概率，用于计算趋势
PREV_TOP_PROB = None

# ==========================================
# 1. 浏览器抓取模块 (极速去广告版)
# ==========================================
def get_data_via_selenium():
    print(f"⚡ [{datetime.now().strftime('%H:%M')}] 启动 Chromium (去广告模式)...")
    
    options = Options()
    # 指定 Chromium 和 Driver 的系统路径 (对应 Dockerfile 安装位置)
    options.binary_location = "/usr/bin/chromium"
    
    # --- 基础性能参数 (防崩) ---
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")
    
    # --- 🔥 核心优化：禁止图片和CSS加载 (解决 Timeout) ---
    # 这会让页面加载飞快，因为不再下载几MB的广告图
    prefs = {
        "profile.managed_default_content_settings.images": 2, # 禁止图片
        "profile.managed_default_content_settings.stylesheets": 2, # 禁止CSS
        "profile.managed_default_content_settings.cookies": 2, 
        "profile.default_content_setting_values.notifications": 2
    }
    options.add_experimental_option("prefs", prefs)
    
    # 策略：Eager (HTML下载完就开干，不等外部脚本)
    options.page_load_strategy = 'eager'
    
    # 伪装 User-Agent
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = None
    try:
        # 使用系统自带驱动
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        
        # 设置脚本最长等待时间 20秒 (足够了)
        driver.set_page_load_timeout(20)
        
        url = "https://www.investing.com/central-banks/fed-rate-monitor"
        driver.get(url)
        
        # 等待直到关键元素出现 (最多10秒)
        wait = WebDriverWait(driver, 10)
        
        # 1. 抓取当前利率
        try:
            curr_elem = wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Current Interest Rate')]")))
            current_rate = curr_elem.text.split(":")[-1].strip().replace("%","")
        except:
            current_rate = "Unknown"

        # 2. 抓取表格
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        data_points = []
        for row in rows:
            cols = row.find_elements(By.TAG_NAME, "td")
            if len(cols) >= 2:
                try:
                    prob_val = float(cols[0].text.strip().replace("%", ""))
                    target_val = cols[1].text.strip()
                    data_points.append({"prob": prob_val, "target": target_val})
                except:
                    continue
        
        # 按概率排序
        data_points.sort(key=lambda x: x['prob'], reverse=True)
        return {"current": current_rate, "data": data_points[:2]}

    except Exception as e:
        print(f"❌ 抓取异常: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit() # 务必关闭浏览器释放内存
            except:
                pass

# ==========================================
# 2. 推送模块 (Discord Embed)
# ==========================================
def send_embed(data):
    global PREV_TOP_PROB
    
    if not data or not data['data']: return
    
    top1 = data['data'][0]
    top2 = data['data'][1] if len(data['data']) > 1 else None
    
    # --- 共识判断逻辑 ---
    status_text = "维持利率 (Hold)"
    icon = "⏸️"
    color = 0x3498DB # 蓝
    
    try:
        c_val = float(data['current'].split("-")[0])
        t_val = float(top1['target'].split("-")[0])
        
        if t_val < c_val:
            status_text = "降息 25bps (Cut)"
            icon = "📉"
            color = 0x57F287 # 绿
        elif t_val > c_val:
            status_text = "加息 25bps (Hike)"
            icon = "📈"
            color = 0xE74C3C # 红
    except:
        pass

    # --- 趋势计算逻辑 ---
    current_prob = top1['prob']
    delta = 0.0
    
    # 只有当有历史数据时才计算差值
    if PREV_TOP_PROB is not None:
        delta = current_prob - PREV_TOP_PROB
    
    # 更新历史记录
    PREV_TOP_PROB = current_prob

    if delta > 0.1:
        trend_str = f"概率上升 {delta:.1f}%"
        trend_emoji = "🔥"
    elif delta < -0.1:
        trend_str = f"概率下降 {abs(delta):.1f}%"
        trend_emoji = "❄️"
    else:
        trend_str = "预期保持稳定"
        trend_emoji = "⚖️"

    # --- 视觉构建 ---
    def bar(p): return "█" * int(p//10) + "░" * (10 - int(p//10))

    desc = [
        f"**🗓️ 下次会议:** `{NEXT_MEETING_DATE}`",
        f"**⚓ 当前基准:** `{data['current']}%`",
        "",
        f"🥇 **目标: {top1['target']}**",
        f"{bar(top1['prob'])} **{top1['prob']}%**",
        ""
    ]
    if top2:
        desc.append(f"🥈 **目标: {top2['target']}**")
        desc.append(f"{bar(top2['prob'])} **{top2['prob']}%**")

    desc.append("")
    desc.append("━━━━━━━━━━━━━━━━━━━━━━")

    payload = {
        "username": "CME FedWatch Bot",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/CME_Group_logo.svg/1200px-CME_Group_logo.svg.png",
        "embeds": [{
            "title": "🏛️ CME FedWatch™ 市场观察",
            "description": "\n".join(desc),
            "color": color,
            "fields": [
                {
                    "name": f"{trend_emoji} 趋势变动",
                    "value": f"**{status_text[:2]}{trend_str}**", 
                    "inline": True
                },
                {
                    "name": "💡 华尔街共识",
                    "value": f"{icon} **{status_text}**",
                    "inline": True
                }
            ],
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M')}"}
        }]
    }
    
    try:
        requests.post(WEBHOOK_URL, json=payload)
        print(f"✅ 推送成功: {status_text} | 概率: {current_prob}%")
    except Exception as e:
        print(f"❌ 推送失败: {e}")

# ==========================================
# 3. 主程序入口
# ==========================================
if __name__ == "__main__":
    print("🚀 Chromium 极速版已启动 (集成为 Railway 优化)...")
    
    # 首次启动立即执行一次
    print("⚡ 正在执行首次抓取...")
    data = get_data_via_selenium()
    if data: send_embed(data)
    else: print("⚠️ 首次抓取失败，将在下个周期重试")

    # 进入循环
    while True:
        print(f"💤 休眠 {CHECK_INTERVAL} 秒...")
        time.sleep(CHECK_INTERVAL)
        
        data = get_data_via_selenium()
        if data: send_embed(data)
        else: print("⚠️ 抓取失败，跳过本次推送")
