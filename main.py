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
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://discord.com/api/webhooks/1440732182334148831/6ji21aLb5ZZ103Qp6WdbHkRiTXpxUf_pHa1BCZAadKpNWcGpvTXNfbY6r_534cjaHZAG")
NEXT_MEETING_DATE = "2025-12-10"
CHECK_INTERVAL = 7200 
PREV_TOP_PROB = None

# ==========================================
# 1. 浏览器抓取模块 (智能表格定位)
# ==========================================
def get_data_via_selenium():
    print(f"⚡ [{datetime.now().strftime('%H:%M')}] 启动 Chromium...")
    
    options = Options()
    options.binary_location = "/usr/bin/chromium"
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")
    
    # 去广告
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    options.page_load_strategy = 'eager'
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = None
    try:
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(30)
        
        url = "https://www.investing.com/central-banks/fed-rate-monitor"
        driver.get(url)
        
        print("⏳ 等待页面渲染...")
        time.sleep(5)
        
        data_points = []
        current_rate = "Unknown"
        
        try:
            # 智能寻找含有 % 的短表格
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
                        prob_val = 0.0
                        target_val = ""
                        try:
                            if "%" in txt0:
                                prob_val = float(txt0.replace("%", ""))
                                target_val = txt1
                            elif "%" in txt1:
                                prob_val = float(txt1.replace("%", ""))
                                target_val = txt0
                            else:
                                continue
                            data_points.append({"prob": prob_val, "target": target_val})
                        except:
                            continue
        except Exception as e:
            print(f"⚠️ 解析错误: {e}")

        if not data_points: return None
        data_points.sort(key=lambda x: x['prob'], reverse=True)
        return {"current": current_rate, "data": data_points[:2]}

    except Exception as e:
        print(f"❌ 异常: {e}")
        return None
    finally:
        if driver:
            try: driver.quit()
            except: pass

# ==========================================
# 2. 推送模块 (新头像 + 无Emoji)
# ==========================================
def send_embed(data):
    global PREV_TOP_PROB
    
    if not data or not data['data']: return
    
    top1 = data['data'][0]
    top2 = data['data'][1] if len(data['data']) > 1 else None
    
    # --- 逻辑判定 ---
    try:
        val1 = float(top1['target'].split('-')[0])
        val2 = float(top2['target'].split('-')[0]) if top2 else 0
        
        label1_suffix = ""
        label2_suffix = ""
        
        if top2:
            if val1 < val2: # Top1 是降息
                label1_suffix = "(降息)"
                label2_suffix = "(维持)"
                consensus_text = "降息 (Cut)"
                icon = "📉"
                color = 0x57F287 # 绿
            else: # Top1 是维持
                label1_suffix = "(维持)"
                label2_suffix = "(降息)"
                consensus_text = "维持利率 (Hold)"
                icon = "⏸️"
                color = 0x3498DB # 蓝
        else:
            label1_suffix = "(共识)"
            consensus_text = "趋势不明"
            icon = "⚖️"
            color = 0x3498DB
    except:
        label1_suffix = ""
        label2_suffix = ""
        consensus_text = "未知"
        icon = "❓"
        color = 0x99AAB5

    # --- 趋势计算 ---
    current_prob = top1['prob']
    delta = 0.0
    if PREV_TOP_PROB is not None:
        delta = current_prob - PREV_TOP_PROB
    PREV_TOP_PROB = current_prob

    if delta > 0.1: trend_str, trend_emoji = f"概率上升 {delta:.1f}%", "🔥"
    elif delta < -0.1: trend_str, trend_emoji = f"概率下降 {abs(delta):.1f}%", "❄️"
    else: trend_str, trend_emoji = "预期保持稳定", "⚖️"

    def bar(p): return "█" * int(p//10) + "░" * (10 - int(p//10))

    # --- 构建正文 ---
    desc = [
        f"**🗓️ 下次会议:** `{NEXT_MEETING_DATE}`",
        "",
        f"**目标: {top1['target']} {label1_suffix}**", 
        f"{bar(top1['prob'])} **{top1['prob']}%**",
        ""
    ]
    
    if top2:
        desc.append(f"**目标: {top2['target']} {label2_suffix}**")
        desc.append(f"{bar(top2['prob'])} **{top2['prob']}%**")

    desc.append("")
    desc.append("━━━━━━━━━━━━━━━━━━━━━━")

    payload = {
        "username": "CME FedWatch Bot",
        # ✅ 新头像 (已转换为直链，确保 Discord 能显示)
        "avatar_url": "https://i.imgur.com/KLl4khv.png",
        "embeds": [{
            "title": "🏛️ CME FedWatch™ (降息预期)",
            "description": "\n".join(desc),
            "color": color,
            "fields": [
                {"name": f"{trend_emoji} 趋势变动", "value": f"**{consensus_text[:2]}{trend_str}**", "inline": True},
                {"name": "💡 华尔街共识", "value": f"{icon} **{consensus_text}**", "inline": True}
            ],
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M')}"}
        }]
    }
    
    try:
        requests.post(WEBHOOK_URL, json=payload)
        print(f"✅ 推送成功: {consensus_text} | {current_prob}%")
    except Exception as e:
        print(f"❌ 推送失败: {e}")

# ==========================================
# 3. 主程序
# ==========================================
if __name__ == "__main__":
    print("🚀 最终定稿版已启动...")
    data = get_data_via_selenium()
    if data: send_embed(data)
    else: print("⚠️ 首次失败，等待重试")

    while True:
        print(f"💤 休眠 {CHECK_INTERVAL} 秒...")
        time.sleep(CHECK_INTERVAL)
        data = get_data_via_selenium()
        if data: send_embed(data)
