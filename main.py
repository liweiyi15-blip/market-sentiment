import time
import requests
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from datetime import datetime

# ==========================================
# ⚙️ 配置区
# ==========================================
# 建议把 Webhook 放在 Railway 的环境变量里 (Variables)，这里用 os.getenv 读取
# 如果你懒得设，直接填字符串也行
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://discord.com/api/webhooks/1440732182334148831/6ji21aLb5ZZ103Qp6WdbHkRiTXpxUf_pHa1BCZAadKpNWcGpvTXNfbY6r_534cjaHZAG")
CHECK_INTERVAL = 7200 

# ==========================================
# 1. 浏览器抓取模块 (Server版)
# ==========================================
def get_cme_data_via_browser():
    print(f"⚡ [{datetime.now().strftime('%H:%M')}] 启动浏览器抓取...")
    
    options = Options()
    # --- 核心配置 (Railway必填) ---
    options.add_argument("--headless=new") # 新版无头模式
    options.add_argument("--no-sandbox")   # 必须: 绕过沙盒权限
    options.add_argument("--disable-dev-shm-usage") # 必须: 解决容器内存不足问题
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    # 伪装 User-Agent，防止被Investing.com拦截
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = None
    try:
        # 自动匹配安装好的 Chrome
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
        # 访问 Investing.com (Fed Rate Monitor)
        # 这个页面通常比较稳定，比CME官网好抓
        url = "https://www.investing.com/central-banks/fed-rate-monitor"
        driver.get(url)
        
        wait = WebDriverWait(driver, 20)
        
        # 抓取当前利率
        try:
            # 尝试定位包含 "Current Interest Rate" 的元素
            curr_elem = wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Current Interest Rate')]")))
            current_text = curr_elem.text.split(":")[-1].strip().replace("%","")
        except:
            current_text = "Unknown"

        # 抓取表格数据
        # 定位表格行 (Investing.com 的结构)
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
        
        # 排序取前两名
        data_points.sort(key=lambda x: x['prob'], reverse=True)
        
        return {"current": current_text, "data": data_points[:2]}

    except Exception as e:
        print(f"❌ 抓取失败: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

# ==========================================
# 2. 发送模块
# ==========================================
def send_embed(data):
    if not data or not data['data']: return
    
    top1 = data['data'][0]
    top2 = data['data'][1] if len(data['data']) > 1 else None
    
    # 简单的颜色判定
    color = 0x3498DB
    status = "维持 (Hold)"
    try:
        c = float(data['current'].split("-")[0])
        t = float(top1['target'].split("-")[0])
        if t < c:
            color = 0x57F287
            status = "降息 (Cut)"
    except:
        pass

    def bar(p): return "█" * int(p//10) + "░" * (10 - int(p//10))

    desc = [
        f"**🗓️ 来源:** `Investing.com`",
        f"**⚓ 当前:** `{data['current']}%`",
        "",
        f"🥇 **{top1['target']}**",
        f"{bar(top1['prob'])} **{top1['prob']}%**",
        ""
    ]
    if top2:
        desc.append(f"🥈 **{top2['target']}**")
        desc.append(f"{bar(top2['prob'])} **{top2['prob']}%**")

    payload = {
        "username": "Fed Monitor",
        "embeds": [{
            "title": "🏛️ 真实概率 (Browser)",
            "description": "\n".join(desc),
            "color": color,
            "fields": [{"name": "共识", "value": status, "inline": True}],
            "footer": {"text": datetime.now().strftime('%H:%M')}
        }]
    }
    requests.post(WEBHOOK_URL, json=payload)
    print("✅ 已推送")

# ==========================================
# 3. 主程序
# ==========================================
if __name__ == "__main__":
    print("🚀 Railway 监控启动...")
    while True:
        data = get_cme_data
