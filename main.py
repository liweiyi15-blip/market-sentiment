import time
import requests
import os
import pytz
import holidays
import pandas as pd
import yfinance as yf
import io
import json
import warnings
import re
import shutil 
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ==========================================
# ⚙️ 全局配置区
# ==========================================

WEBHOOK_URL = os.getenv("WEBHOOK_URL") 

# ⏰ 时间表 (美东时间 ET)
# 对应北京时间：
# 冬令时: 21:31, 23:31, 04:01
# 夏令时: 20:31, 22:31, 03:01
FED_SCHEDULE_TIMES = ["08:31", "10:31", "15:01"]

# 市场广度时间 (美东 16:30)
BREADTH_SCHEDULE_TIME = "16:30"

# ------------------------------------------
# 🏛️ FedWatch 配置
# ------------------------------------------
# [开关] 控制 CME FedWatch Bot 是否运行
# True = 开启 (会启动 Chromium 爬虫)
# False = 关闭 (跳过执行，节省 Railway 资源)
ENABLE_FED_BOT = False 

FED_BOT_NAME = "CME FedWatch Bot"
FED_BOT_AVATAR = "https://i.imgur.com/d8KLt6Z.png"

# ------------------------------------------
# 📊 市场广度 配置
# ------------------------------------------
BREADTH_BOT_NAME = "标普500 广度日报" 
BREADTH_BOT_AVATAR = "https://i.imgur.com/Segc5PF.jpeg"

PREV_CUT_PROB = None

# 【保底策略】万一爬虫死活抓不到日期，才用这个表
BACKUP_SCHEDULE = [
    "2025-12-10", "2026-01-28", "2026-03-18", "2026-05-06", 
    "2026-06-17", "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16"
]
# 【保底策略】万一爬虫抓不到当前利率
DEFAULT_BACKUP_RATE = 3.50 

# ==========================================
# 🛠️ 辅助函数
# ==========================================

def parse_date_string(date_text):
    """
    尝试将网页上的各种日期文本 (e.g., 'Dec 10, 2025') 转化为 '2025-12-10'
    """
    if not date_text: return None
    try:
        # 清理文本，只保留字母数字和逗号
        clean_text = re.sub(r'[^\w\s,]', '', date_text).strip()
        
        # 常见格式 1: Dec 10, 2025
        try:
            dt = datetime.strptime(clean_text, "%b %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except: pass
        
        # 常见格式 2: December 10, 2025
        try:
            dt = datetime.strptime(clean_text, "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except: pass

        return None
    except:
        return None

def get_backup_meeting_date():
    """从硬编码列表中找下一个日期（仅作为爬虫失败的备选）"""
    tz = pytz.timezone('US/Eastern')
    today_str = datetime.now(tz).strftime("%Y-%m-%d")
    for meeting_date in BACKUP_SCHEDULE:
        if meeting_date >= today_str:
            return meeting_date
    return "TBD"

def is_market_holiday(now_et):
    # 周末判断 (5=周六, 6=周日)
    if now_et.weekday() >= 5: return True, "周末休市"
    # 美股假期判断
    us_holidays = holidays.US(years=now_et.year) 
    if now_et.date() in us_holidays: return True, f"假期: {us_holidays.get(now_et.date())}"
    return False, None

def get_bar(p):
    length = 15
    filled = int(p / 100 * length)
    return "█" * filled + "░" * (length - filled)

def format_target_label(target_str, current_rate_base):
    try:
        lower_bound = float(target_str.split('-')[0].strip())
        # 允许微小误差
        if abs(lower_bound - current_rate_base) <= 0.05:
            return f"{target_str} (维持)"
        elif lower_bound < current_rate_base:
            return f"{target_str} (降息)"
        else:
            return f"{target_str} (加息)"
    except:
        return target_str

# ==========================================
# 🟢 模块 1: 降息概率 (全自动爬虫版)
# ==========================================

def scrape_header_info(driver, page_text):
    """
    尝试从页面抓取：
    1. 当前利率 (Current Rate)
    2. 下次会议日期 (Next Meeting Date)
    """
    rate = None
    meeting_date = None

    # --- 1. 抓取当前利率 ---
    # 尝试正则匹配 "Current Rate: 4.25" 或 "Current Target Rate: 4.25-4.50"
    # 我们只取区间的下限或单一数值
    try:
        # 寻找类似于 "Current Rate 4.50" 的文本
        rate_match = re.search(r"Current.*?Rate.*?(\d+\.\d+)", page_text, re.IGNORECASE)
        if rate_match:
            val = float(rate_match.group(1))
            # 过滤异常值
            if 0.0 <= val <= 10.0:
                rate = val
    except: pass

    # --- 2. 抓取会议日期 ---
    # Investing.com 通常有个下拉框或者标题显示 Meeting Date
    try:
        # 策略 A: 找含有 class="date" 或 id="meetingDate" 的元素
        # 这是一个通用猜测，具体依赖页面结构
        date_elements = driver.find_elements(By.XPATH, "//*[contains(@class, 'date') or contains(@id, 'date')]")
        
        # 策略 B: 直接搜寻月份单词 (Jan, Feb...) + 数字 + 年份
        # 这是一种暴力但有效的方法
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        date_pattern = re.compile(r'(?:' + '|'.join(months) + r')\.?\s+\d{1,2},?\s+\d{4}', re.IGNORECASE)
        
        # 在页面前 2000 个字符里找日期（通常日期在顶部）
        top_text = page_text[:2000]
        dates_found = date_pattern.findall(top_text)
        
        tz = pytz.timezone('US/Eastern')
        today = datetime.now(tz).date()

        for d_str in dates_found:
            parsed = parse_date_string(d_str)
            if parsed:
                # 必须是未来或者今天的日期才算数
                p_date = datetime.strptime(parsed, "%Y-%m-%d").date()
                if p_date >= today:
                    meeting_date = parsed
                    break # 找到了最近的一个未来日期
    except: pass

    return rate, meeting_date

def get_fed_data():
    if not ENABLE_FED_BOT:
        print("⏸️ [系统] FedWatch Bot 已禁用，跳过抓取...")
        return None

    print(f"⚡ 启动 Chromium...")
    options = Options()
    options.binary_location = "/usr/bin/chromium" 
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

    driver = None
    
    # 最终结果容器
    result = {
        "current_base_rate": None,
        "next_meeting": None,
        "data": []
    }
      
    try:
        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.get("https://www.investing.com/central-banks/fed-rate-monitor")
        time.sleep(5) 
        
        # 获取页面全文本用于分析
        page_text = driver.find_element(By.TAG_NAME, "body").text
        
        # 🧠 智能分析：抓取头部信息 (利率 + 日期)
        scraped_rate, scraped_date = scrape_header_info(driver, page_text)
        
        if scraped_rate:
            print(f"✅ 自动检测到当前利率: {scraped_rate}%")
            result["current_base_rate"] = scraped_rate
        else:
            print(f"⚠️ 未检测到利率，使用保底值: {DEFAULT_BACKUP_RATE}%")
            result["current_base_rate"] = DEFAULT_BACKUP_RATE
            
        if scraped_date:
            print(f"✅ 自动检测到下次会议: {scraped_date}")
            result["next_meeting"] = scraped_date
        else:
            bk_date = get_backup_meeting_date()
            print(f"⚠️ 未检测到日期，使用保底表: {bk_date}")
            result["next_meeting"] = bk_date

        # --- 抓取概率表格 (原有逻辑) ---
        data_points = []
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
                        txt0, txt1 = cols[0].text.strip(), cols[1].text.strip()
                        try:
                            if "%" in txt0: prob, target = float(txt0.replace("%", "")), txt1
                            elif "%" in txt1: prob, target = float(txt1.replace("%", "")), txt0
                            else: continue
                            data_points.append({"prob": prob, "target": target})
                        except: continue
        except: pass

        if not data_points: return None
        
        result["data"] = data_points
        return result

    except Exception as e:
        print(f"❌ Error: {e}")
        return None
    finally:
        if driver:
            try: driver.quit()
            except: pass

def send_fed_embed(data):
    global PREV_CUT_PROB
    if not data or not data['data']: return
    
    base_rate = data.get("current_base_rate")
    next_meeting_date = data.get("next_meeting")
    
    # 1. 计算降息趋势
    current_cut_prob = 0.0
    target_cut_lower = base_rate - 0.25
    
    cut_item = None
    rest_items = []
    
    for item in data['data']:
        try:
            lower = float(item['target'].split('-')[0].strip())
            # 找到降息 25bp 的那一项
            if abs(lower - target_cut_lower) <= 0.05:
                cut_item = item
                current_cut_prob = item['prob']
            else:
                rest_items.append(item)
        except:
            rest_items.append(item)
    
    # 计算变动
    delta = 0.0
    if PREV_CUT_PROB is not None:
        delta = current_cut_prob - PREV_CUT_PROB
    PREV_CUT_PROB = current_cut_prob
    
    trend_title = "📉 降息趋势变动"
    if not cut_item and current_cut_prob == 0:
        trend_text = "无降息预期"
    elif delta > 0.1: 
        trend_text = f"概率上升 +{delta:.1f}% 🔥"
    elif delta < -0.1: 
        trend_text = f"概率下降 {delta:.1f}% ❄️"
    else:
        trend_text = "稳定"

    # ==================================================
    # 排序显示
    # ==================================================
    display_list = []
    if cut_item: display_list.append(cut_item)
    if rest_items:
        rest_items.sort(key=lambda x: x['prob'], reverse=True)
        display_list.append(rest_items[0])
    
    if not display_list:
        data['data'].sort(key=lambda x: x['prob'], reverse=True)
        display_list = data['data'][:2]

    # 华尔街共识
    all_sorted = sorted(data['data'], key=lambda x: x['prob'], reverse=True)
    top1_real = all_sorted[0]
    
    label1_raw = format_target_label(top1_real['target'], base_rate)
    if "(维持)" in label1_raw: consensus_short = "⏸️ 维持利率 (Hold)"
    elif "(降息)" in label1_raw: consensus_short = "📉 降息 (Cut)"
    else: consensus_short = "📈 加息 (Hike)"

    # 构建 Embed 
    # 这里直接使用爬虫爬到的 next_meeting_date
    desc_lines = [f"**🗓️ 下次会议:** `{next_meeting_date}`\n"]
    
    for item in display_list:
        label = format_target_label(item['target'], base_rate)
        desc_lines.append(f"**目标: {label}**")
        desc_lines.append(f"`{get_bar(item['prob'])}` **{item['prob']}%**\n")
    
    desc_lines.append("\n------------------------")

    payload = {
        "username": FED_BOT_NAME,
        "avatar_url": FED_BOT_AVATAR,
        "embeds": [{
            "title": "🏛️ CME FedWatch™",
            "description": "\n".join(desc_lines),
            "color": 0x3498DB,
            "fields": [
                {
                    "name": trend_title, 
                    "value": trend_text, 
                    "inline": True
                },
                {
                    "name": "💡 华尔街共识", 
                    "value": consensus_short, 
                    "inline": True
                },
                {
                    "name": "📊 当前基准利率", 
                    "value": f"{base_rate}%", 
                    "inline": False 
                }
            ],
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M')} ET | Auto-Scraped"}
        }]
    }
    try: requests.post(WEBHOOK_URL, json=payload)
    except Exception as e: print(f"❌ 推送失败: {e}")

# ==========================================
# 🔵 模块 2: 市场广度 (保持不变)
# ==========================================
# === 粘贴这段新代码 ===
def generate_breadth_chart(breadth_20_series, breadth_50_series):
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    # 绘制折线
    ax.plot(breadth_20_series.index, breadth_20_series.values, color='#f1c40f', linewidth=2, label='Stocks > 20 Day SMA %')
    ax.plot(breadth_50_series.index, breadth_50_series.values, color='#e74c3c', linewidth=2, label='Stocks > 50 Day SMA %')
    ax.fill_between(breadth_20_series.index, breadth_20_series.values, alpha=0.1, color='#f1c40f')
    
    # 绘制 80/20 警戒线
    ax.axhline(y=80, color='#ff5252', linestyle='--', linewidth=1, alpha=0.8)
    ax.text(breadth_20_series.index[0], 81, 'Overbought (80%)', color='#ff5252', fontsize=8)
    ax.axhline(y=20, color='#448aff', linestyle='--', linewidth=1, alpha=0.8)
    ax.text(breadth_20_series.index[0], 21, 'Oversold (20%)', color='#448aff', fontsize=8)

    # --- 【修改点1】强制横轴从最左边的数据开始 ---
    # left=... 设定了左边界，Right不设限让它自动适应
    ax.set_xlim(left=breadth_20_series.index[0], right=breadth_20_series.index[-1])

    # --- 【修改点2】在图表上标注当前数值 ---
    last_date = breadth_20_series.index[-1]
    last_val_20 = breadth_20_series.iloc[-1]
    last_val_50 = breadth_50_series.iloc[-1]

    # 给 20日线添加数值 (黄色)
    ax.annotate(f'{last_val_20:.1f}%', 
                xy=(last_date, last_val_20), 
                xytext=(-10, 10), textcoords='offset points', # 文字向左上方偏移一点，防止切断
                color='#f1c40f', fontsize=11, fontweight='bold', 
                ha='right', bbox=dict(boxstyle="round,pad=0.3", fc="#2f3136", ec="#f1c40f", alpha=0.8))

    # 给 50日线添加数值 (红色)
    ax.annotate(f'{last_val_50:.1f}%', 
                xy=(last_date, last_val_50), 
                xytext=(-10, -20), textcoords='offset points', # 文字向左下方偏移一点
                color='#e74c3c', fontsize=11, fontweight='bold', 
                ha='right', bbox=dict(boxstyle="round,pad=0.3", fc="#2f3136", ec="#e74c3c", alpha=0.8))
    # ----------------------------------------

    ax.set_title('S&P 500 Market Breadth (20 & 50 Day SMA)', fontsize=12, color='white', pad=15)
    ax.set_ylim(0, 100)
    ax.grid(True, linestyle=':', alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    plt.xticks(rotation=0)
    
    # 图例
    ax.legend(loc='upper left', frameon=True, facecolor='#2f3136', edgecolor='#2f3136', labelcolor='white')
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=100, facecolor='#2b2d31')
    buf.seek(0)
    plt.close()
    return buf

def get_market_sentiment(p):
    if p > 80: return "🔥🔥 **深度火热**"
    if p > 60: return "🔥 **火热**"      
    if p < 20: return "❄️❄️ **深度寒冷**"
    if p < 40: return "❄️ **寒冷**"      
    return "🍃 **稳定**"     

def run_breadth_task():
    print("📊 启动市场广度统计...")
    try:
        try:
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
            tables = pd.read_html(io.StringIO(resp.text))
            df_tickers = next((df for df in tables if 'Symbol' in df.columns), None)
            tickers = [t.replace('.', '-') for t in df_tickers['Symbol'].tolist()] 
        except:
            tickers = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK-B', 'LLY', 'AVGO']

        warnings.simplefilter(action='ignore', category=FutureWarning)
        try:
            if os.path.exists('yfinance.cache'): shutil.rmtree('yfinance.cache')
        except: pass

        data = yf.download(tickers, period="1y", progress=False) 
        if 'Close' in data.columns: closes = data['Close']
        else: closes = data

        sma20_df = closes.rolling(window=20).mean()
        sma50_df = closes.rolling(window=50).mean()
        
        daily_breadth_20 = ((closes > sma20_df).sum(axis=1) / closes.notna().sum(axis=1)) * 100
        daily_breadth_50 = ((closes > sma50_df).sum(axis=1) / closes.notna().sum(axis=1)) * 100
        
        chart_buffer = generate_breadth_chart(daily_breadth_20.tail(252), daily_breadth_50.tail(252))
        current_p20 = daily_breadth_20.iloc[-1]
        current_p50 = daily_breadth_50.iloc[-1]
        
        sentiment_20 = get_market_sentiment(current_p20)
        sentiment_50 = get_market_sentiment(current_p50)

        payload_data = {
            "username": BREADTH_BOT_NAME,
            "avatar_url": BREADTH_BOT_AVATAR,
            "embeds": [{
                "title": "S&P 500 市场广度",
                "description": f"**日期:** `{datetime.now().strftime('%Y-%m-%d')}`\n\n"
                               f"**股价 > 20日均线:** **{current_p20:.1f}%**\n"
                               f"{sentiment_20}\n\n"
                               f"**股价 > 50日均线:** **{current_p50:.1f}%**\n"
                               f"{sentiment_50}",
                "color": 0xF1C40F,
                "image": {"url": "attachment://chart.png"},
                "footer": {
                    "text": f"💡 标普500大于20日、50日均的数量\n💡 >80% 警惕回调，<20% 孕育反弹。\n（统计样本: {len(tickers)}只成分股）"
                }
            }]
        }
        
        files = {'file': ('chart.png', chart_buffer, 'image/png')}
        requests.post(WEBHOOK_URL, data={'payload_json': json.dumps(payload_data)}, files=files)
        print(f"✅ 广度报告已推送")

    except Exception as e:
        print(f"❌ 广度任务异常: {e}")

# ==========================================
# 🚀 主程序
# ==========================================
if __name__ == "__main__":
    print("🚀 监控服务已启动")
    
    if ENABLE_FED_BOT:
        print("🧪 [测试] 正在发送 FedWatch (智能爬虫版)...")
        fed_data = get_fed_data()
        if fed_data: 
            send_fed_embed(fed_data)
            print("✅ FedWatch 测试完成")
        else:
            print("⚠️ FedWatch 获取失败")
    else:
        print("⏸️ [测试] FedWatch 已禁用，跳过测试")

    print("🧪 [测试] 正在发送 市场广度...")
    run_breadth_task()
    
    print("✅ 所有测试结束，进入定时监听模式...")

    last_run_time_str = ""
    while True:
        tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(tz)
        current_str = now_et.strftime("%H:%M")
        is_holiday, holiday_name = is_market_holiday(now_et)

        if current_str != last_run_time_str:
            print(f"⏰ {current_str} ET (Market Open: {not is_holiday})")
            
            # 只有在非假期/非周末时才推送
            if not is_holiday:
                if current_str in FED_SCHEDULE_TIMES:
                    if ENABLE_FED_BOT:
                        print(f"🔔 触发 FedWatch 定时推送: {current_str}")
                        data = get_fed_data()
                        if data: send_fed_embed(data)
                    else:
                        print(f"⏸️ 时间到达 {current_str}，但 FedWatch 已禁用，跳过执行")
                
                if current_str == BREADTH_SCHEDULE_TIME:
                    print(f"🔔 触发 市场广度 定时推送: {current_str}")
                    run_breadth_task()
            else:
                # 假期/周末时，只打印心跳，不推送
                if current_str in FED_SCHEDULE_TIMES or current_str == BREADTH_SCHEDULE_TIME:
                    print(f"😴 今日休市 ({holiday_name})，跳过推送")

            last_run_time_str = current_str
        
        time.sleep(30)
