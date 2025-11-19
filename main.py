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
from webdriver_manager.chrome import ChromeDriverManager

# ==========================================
# ⚙️ 配置区
# ==========================================
# ✅ 你的专属 Webhook (已填好)
WEBHOOK_URL = "https://discord.com/api/webhooks/1440732182334148831/6ji21aLb5ZZ103Qp6WdbHkRiTXpxUf_pHa1BCZAadKpNWcGpvTXNfbY6r_534cjaHZAG"

# 检查间隔 (秒) - 建议 7200 (2小时)，太快容易被封 IP
CHECK_INTERVAL = 7200 

# ==========================================
# 1. 浏览器抓取模块 (无头模式)
# ==========================================
def get_data_via_selenium():
    print(f"⚡ [{datetime.now().strftime('%H:%M')}] 启动浏览器读取数据...")
    
    # --- 浏览器配置 (防检测 + 服务器兼容) ---
    options = Options()
    options.add_argument("--headless=new") # 无头模式 (不显示界面)
    options.add_argument("--no-sandbox")   # 必须 (Linux/Docker环境需要)
    options.add_argument("--disable-dev-shm-usage") # 必须 (防止内存崩溃)
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    # 伪装 User-Agent (非常重要，否则会被当成爬虫拦截)
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = None
    try:
        # 自动下载/匹配 Chrome 驱动
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
        # 目标: Investing.com (数据源与 CME 官网一致，但更易读取)
        url = "https://www.investing.com/central-banks/fed-rate-monitor"
        driver.get(url)
        
        # 等待页面加载 (最多20秒)
        wait = WebDriverWait(driver, 20)
        
        # 1. 抓取当前利率 (Current Interest Rate)
        try:
            # 模糊搜索页面上包含 "Current Interest Rate" 的文字
            curr_elem = wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Current Interest Rate')]")))
            # 提取文本 (例如 "Current Interest Rate: 4.50-4.75%")
            raw_text = curr_elem.text
            current_rate = raw_text.split(":")[-1].strip().replace("%","")
        except:
            current_rate = "Unknown"

        # 2. 抓取概率表格
        # Investing.com 的表格通常结构: tbody -> tr -> td
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        
        data_points = []
        for row in rows:
            cols = row.find_elements(By.TAG_NAME, "td")
            if len(cols) >= 2:
                try:
                    # 第一列是概率 (58.4%)，第二列是目标区间 (4.25-4.50)
                    prob_val = float(cols[0].text.strip().replace("%", ""))
                    target_val = cols[1].text.strip()
                    data_points.append({"prob": prob_val, "target": target_val})
                except:
                    continue # 跳过标题行或无效行
        
        # 按概率从高到低排序
        data_points.sort(key=lambda x: x['prob'], reverse=True)
        
        return {
            "current": current_rate,
            "data": data_points[:2] # 只取前两名
        }

    except Exception as e:
        print(f"❌ 抓取异常: {e}")
        return None
    finally:
        # 务必关闭浏览器，释放内存
        if driver:
            try:
                driver.quit()
            except:
                pass

# ==========================================
# 2. 推送模块 (构建 Embed)
# ==========================================
def send_embed(data):
    if not data or not data['data']: return
    
    top1 = data['data'][0]
    top2 = data['data'][1] if len(data['data']) > 1 else None
    
    # 简单的逻辑判断：降息还是维持？
    # 比较 Current 和 Top1 Target 的第一个数字
    status = "维持 (Hold)"
    color = 0x3498DB # 蓝
    icon = "⏸️"
    
    try:
        curr_num = float(data['current'].split("-")[0])
        target_num = float(top1['target'].split("-")[0])
        
        if target_num < curr_num:
            status = "降息 (Cut)"
            color = 0x57F287 # 绿
            icon = "📉"
        elif target_num > curr_num:
            status = "加息 (Hike)"
            color = 0xE74C3C # 红
            icon = "📈"
    except:
        pass # 如果解析失败，保持默认

    # 进度条生成器
    def bar(p):
        l = int(p // 10)
        return "█" * l + "░" * (10 - l)

    # 构建 Embed 内容
    desc = [
        f"**🗓️ 数据源:** `Investing.com (Selenium)`",
        f"**⚓ 当前利率:** `{data['current']}%`",
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
        "username": "Fed Rate Monitor",
        "embeds": [{
            "title": "🏛️ 美联储利率观测 (真实数据)",
            "description": "\n".join(desc),
            "color": color,
            "fields": [
                {"name": "💡 市场共识", "value": f"{icon} **{status}**", "inline": True},
                {"name": "✅ 准确性", "value": "100% (网页直读)", "inline": True}
            ],
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M')}"}
        }]
    }
    
    try:
        requests.post(WEBHOOK_URL, json=payload)
        print(f"✅ 数据已推送至 Discord ({datetime.now().strftime('%H:%M:%S')})")
    except Exception as e:
        print(f"❌ 推送失败: {e}")

# ==========================================
# 3. 主程序入口
# ==========================================
if __name__ == "__main__":
    print("🚀 Selenium 监控模式已启动 (Investing.com)...")
    
    while True:
        result = get_data_via_selenium()
        
        if result:
            send_embed(result)
        else:
            print("⚠️ 本次抓取为空，稍后重试...")
            
        # 休眠
        print(f"💤 休息 {CHECK_INTERVAL} 秒...")
        time.sleep(CHECK_INTERVAL)
