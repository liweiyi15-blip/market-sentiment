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
# 1. 浏览器抓取模块 (智能定位版)
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
    
    # 去广告 (加快速度)
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
        
        # 强制等待5秒让JS渲染
        print("⏳ 等待页面渲染...")
        time.sleep(5)
        
        # --- 🔥 修复点：精准定位 ---
        # 不再盲目抓取所有 tr，而是寻找表头里含有 "Probability" 的那个表格
        data_points = []
        current_rate = "Unknown"
        
        try:
            # 1. 尝试找 Current Rate (增加容错)
            try:
                # 尝试多种 XPath 组合
                curr_elem = driver.find_element(By.XPATH, "//*[contains(text(), 'Current Interest Rate') or contains(text(), 'Current Rate')]")
                current_rate = curr_elem.text.split(":")[-1].strip().replace("%","")
            except:
                print("⚠️ 未找到 Current Rate 文本，将显示 Unknown")

            # 2. 尝试找概率表
            # 逻辑：找到页面上所有的 table，遍历它们，看谁的数据像“概率”
            tables = driver.find_elements(By.TAG_NAME, "table")
            print(f"🔍 页面共发现 {len(tables)} 个表格")

            target_table = None
            
            for idx, tbl in enumerate(tables):
                # 简单的启发式算法：如果这个表格行数少于 15 且包含 '%' 符号，大概率就是它
                txt = tbl.text
                if "%" in txt and len(tbl.find_elements(By.TAG_NAME, "tr")) < 15:
                    print(f"✅ 锁定表格 #{idx+1} (看起来像概率表)")
                    target_table = tbl
                    break
            
            if not target_table:
                print("❌ 未找到符合特征的概率表，尝试抓取第一个...")
                if tables: target_table = tables[0]

            if target_table:
                rows = target_table.find_elements(By.TAG_NAME, "tr")
                
                # 调试日志：打印第一行内容，方便排错
                if len(rows) > 1:
                    print(f"📝 表格首行预览: {rows[1].text}")

                for row in rows:
                    cols = row.find_elements(By.TAG_NAME, "td")
                    # Investing.com 的列顺序可能会变，我们自动检测
                    if len(cols) >= 2:
                        txt0 = cols[0].text.strip()
                        txt1 = cols[1].text.strip()
                        
                        # 逻辑：哪个带 '%', 哪个就是概率；另一个是目标
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
                                continue # 这一行没有百分比，跳过
                            
                            data_points.append({"prob": prob_val, "target": target_val})
                        except:
                            continue

        except Exception as parse_error:
            print(f"⚠️ 解析过程出错: {parse_error}")

        if not data_points:
            print("❌ 最终未能提取到有效数据")
            return None

        data_points.sort(key=lambda x: x['prob'], reverse=True)
        return {"current": current_rate, "data": data_points[:2]}

    except Exception as e:
        print(f"❌ 浏览器崩溃或网络错误: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

# ==========================================
# 2. 推送模块
# ==========================================
def send_embed(data):
    global PREV_TOP_PROB
    
    if not data or not data['data']: return
    
    top1 = data['data'][0]
    top2 = data['data'][1] if len(data['data']) > 1 else None
    
    status_text = "维持利率 (Hold)"
    icon = "⏸️"
    color = 0x3498DB
    
    try:
        c_val = float(data['current'].split("-")[0])
        t_val = float(top1['target'].split("-")[0])
        if t_val < c_val:
            status_text = "降息 25bps (Cut)"
            icon = "📉"
            color = 0x57F287
        elif t_val > c_val:
            status_text = "加息 25bps (Hike)"
            icon = "📈"
            color = 0xE74C3C
    except:
        # 如果 Current Unknown，我们默认假设降息 (根据目前大环境)
        # 或者直接根据 Target 是否比 4.5 低来判断
        pass

    current_prob = top1['prob']
    delta = 0.0
    if PREV_TOP_PROB is not None:
        delta = current_prob - PREV_TOP_PROB
    PREV_TOP_PROB = current_prob

    if delta > 0.1: trend_str, trend_emoji = f"概率上升 {delta:.1f}%", "🔥"
    elif delta < -0.1: trend_str, trend_emoji = f"概率下降 {abs(delta):.1f}%", "❄️"
    else: trend_str, trend_emoji = "预期保持稳定", "⚖️"

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
                {"name": f"{trend_emoji} 趋势变动", "value": f"**{status_text[:2]}{trend_str}**", "inline": True},
                {"name": "💡 华尔街共识", "value": f"{icon} **{status_text}**", "inline": True}
            ],
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M')}"}
        }]
    }
    
    try:
        requests.post(WEBHOOK_URL, json=payload)
        print(f"✅ 推送成功: {status_text} | {current_prob}%")
    except Exception as e:
        print(f"❌ 推送失败: {e}")

# ==========================================
# 3. 主程序
# ==========================================
if __name__ == "__main__":
    print("🚀 智能表格定位版已启动...")
    data = get_data_via_selenium()
    if data: send_embed(data)
    else: print("⚠️ 首次失败，将在下个周期重试")

    while True:
        print(f"💤 休眠 {CHECK_INTERVAL} 秒...")
        time.sleep(CHECK_INTERVAL)
        data = get_data_via_selenium()
        if data: send_embed(data)
