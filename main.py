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
# 1. 浏览器抓取模块 (保持不变)
# ==========================================
def get_data_via_selenium():
    print(f"⚡ [{datetime.now().strftime('%H:%M')}] 启动 Chromium (智能定位模式)...")
    
    options = Options()
    options.binary_location = "/usr/bin/chromium"
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")
    
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
# 2. 推送模块 (按要求修改视觉)
# ==========================================
def send_embed(data):
    global PREV_TOP_PROB
    
    if not data or not data['data']: return
    
    top1 = data['data'][0]
    top2 = data['data'][1] if len(data['data']) > 1 else None
    
    # --- 逻辑判定：谁是降息，谁是维持？---
    # 比较两个目标区间的数值大小
    # 数值小的 = 降息 (Cut)
    # 数值大的 = 维持 (Hold)
    
    try:
        # 提取区间里的第一个数字进行比较 (例如 "3.75-4.00" 取 3.75)
        val1 = float(top1['target'].split('-')[0])
        val2 = float(top2['target'].split('-')[0]) if top2 else 0
        
        # 默认标签
        label1_suffix = ""
        label2_suffix = ""
        
        if top2:
            if val1 < val2:
                label1_suffix = "(降息)"
                label2_suffix = "(维持)"
                # 既然 Top1 更小，说明市场主押降息 -> 绿色
                consensus_text = "降息 (Cut)"
                icon = "📉"
                color = 0x57F287 # 绿
            else:
                label1_suffix = "(维持)"
                label2_suffix = "(降息)"
                # 既然 Top1 更大，说明市场主押维持 -> 蓝色
                consensus_text = "维持利率 (Hold)"
                icon = "⏸️"
                color = 0x3498DB # 蓝
        else:
            # 如果只有一个选项，无法比较，默认维持
            label1_suffix = "(共识)"
            consensus_text = "趋势不明"
            icon = "⚖️"
            color = 0x3498DB

    except:
        # 容错
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

    # --- 进度条 ---
    def bar(p): return "█" * int(p//10) + "░" * (10 - int(p//10))

    # --- 构建 Embed 正文 ---
    # 删掉了 "当前基准"
    # 删掉了奖牌 emoji，换成了具体的 icon
    
    # 判断 Top1 图标
    icon1 = "📉" if "降息" in label1_suffix else "⏸️"
    
    desc = [
        f"**🗓️ 下次会议:** `{NEXT_MEETING_DATE}`",
        "",
        f"{icon1} **目标: {top1['target']} {label1_suffix}**",
        f"{bar(top1['prob'])} **{top1['prob']}%**",
        ""
    ]
    
    if top2:
        # 判断 Top2 图标
        icon2 = "📉" if "降息" in label2_suffix else "⏸️"
        desc.append(f"{icon2} **目标: {top2['target']} {label2_suffix}**")
        desc.append(f"{bar(top2['prob'])} **{top2['prob']}%**")

    desc.append("")
    desc.append("━━━━━━━━━━━━━━━━━━━━━━")

    payload = {
        "username": "CME FedWatch Bot",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/CME_Group_logo.svg/1200px-CME_Group_logo.svg.png",
        "embeds": [{
            "title": "🏛️ CME FedWatch™ (降息预期)", # 标题已修改
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
    print("🚀 视觉最终修正版已启动...")
    data = get_data_via_selenium()
    if data: send_embed(data)
    else: print("⚠️ 首次失败")

    while True:
        print(f"💤 休眠 {CHECK_INTERVAL} 秒...")
        time.sleep(CHECK_INTERVAL)
        data = get_data_via_selenium()
        if data: send_embed(data)
