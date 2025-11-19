import time
import math
import requests
import yfinance as yf
from datetime import datetime

# ==========================================
# ⚙️ 配置区
# ==========================================
# 🔴 必填: 你的 Discord Webhook URL
WEBHOOK_URL = "https://discord.com/api/webhooks/xxxxxxxx/xxxxxxx"

NEXT_MEETING_DATE = "2025-12-18"
TICKER_SYMBOL = "ZQ=F"
CHECK_INTERVAL = 7200 

# ==========================================
# 1. 数据获取模块 (不变)
# ==========================================
def get_market_data_and_rate():
    try:
        ticker = yf.Ticker(TICKER_SYMBOL)
        hist = ticker.history(period="5d")
        if hist.empty: return 4.00, 3.80, 3.90 # 模拟
        
        current_price = hist['Close'].iloc[-1]
        prev_price = hist['Close'].iloc[-2]
        
        today_implied = 100 - current_price
        yesterday_implied = 100 - prev_price
        
        lower_bound = round(today_implied / 0.25) * 0.25
        auto_fed_rate = lower_bound + 0.25
        
        return auto_fed_rate, today_implied, yesterday_implied
    except:
        return 4.00, 3.80, 3.90

# ==========================================
# 2. 核心: 构建 Embed JSON 对象
# ==========================================
def send_embed_to_discord(current_fed_rate, today_implied, yesterday_implied):
    
    # --- 计算逻辑 ---
    def get_prob(implied):
        diff = current_fed_rate - implied
        return max(0.0, min(100.0, (diff / 0.25) * 100))

    prob_today = get_prob(today_implied)
    prob_yesterday = get_prob(yesterday_implied)
    prob_hold = 100.0 - prob_today
    delta = prob_today - prob_yesterday
    
    # --- 颜色与文案逻辑 ---
    if prob_today > 50:
        # 降息主导 -> 绿色
        embed_color = 0x57F287 # Discord 亮绿
        consensus_text = "降息 25bps (Cut)"
        consensus_icon = "📉"
    else:
        # 维持主导 -> 蓝色
        embed_color = 0x3498DB # Discord 亮蓝
        consensus_text = "维持利率 (Hold)"
        consensus_icon = "⏸️"

    # 趋势文案
    if delta > 0.1:
        trend_text = f"降息概率上升 **{delta:.1f}%**"
        trend_icon = "🔥"
    elif delta < -0.1:
        trend_text = f"降息概率下降 **{abs(delta):.1f}%**"
        trend_icon = "❄️"
    else:
        trend_text = "预期保持稳定"
        trend_icon = "⚖️"

    # --- 构建 Description (为了保持你的单行布局) ---
    # 注意：在 Embed Description 里，我们不需要 ">" 引用符了，因为 Embed 本身就是个框
    # 但为了对齐，我们可以用代码块或者直接排版
    
    # 目标区间文本
    range_cut = f"{current_fed_rate-0.25:.2f}-{current_fed_rate:.2f}%"
    range_hold = f"{current_fed_rate:.2f}-{current_fed_rate+0.25:.2f}%"
    
    # 进度条绘制
    bar_len_cut = int(prob_today // 12.5)
    bar_visual_cut = "🟩" * bar_len_cut + "░" * (8 - bar_len_cut)
    
    bar_len_hold = int(prob_hold // 12.5)
    bar_visual_hold = "🟦" * bar_len_hold + "░" * (8 - bar_len_hold)

    # 拼装主内容区 (使用 \n 换行)
    # 这里我保留了你的单行格式，加了 ` ` 行内代码块让数字更清晰
    description_lines = [
        f"**🗓️ 下次会议:** `{NEXT_MEETING_DATE}`",
        f"**⚓ 当前基准:** `{current_fed_rate-0.25:.2f}-{current_fed_rate:.2f}%` (Auto)",
        "",
        "**🎯 目标区间分布 (Probabilities)**",
        f"📉 **目标: {range_cut} (降息)**",
        f"{bar_visual_cut} **{prob_today:.1f}%**", # 为了手机版显示正常，建议拆成两行，或者保持单行
        "",
        f"⏸️ **目标: {range_hold} (维持)**",
        f"{bar_visual_hold} {prob_hold:.1f}%",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━" # 分割线
    ]
    
    main_desc = "\n".join(description_lines)

    # --- 组装 JSON Payload ---
    payload = {
        "username": "CME Monitor",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/CME_Group_logo.svg/1200px-CME_Group_logo.svg.png",
        "embeds": [
            {
                "title": "🏛️ CME FedWatch™ 市场观察",
                "description": main_desc,
                "color": embed_color, # 动态颜色
                "fields": [
                    {
                        "name": f"{trend_icon} 趋势变动",
                        "value": trend_text,
                        "inline": True
                    },
                    {
                        "name": f"💡 华尔街共识",
                        "value": f"{consensus_icon} **{consensus_text}**",
                        "inline": True
                    }
                ],
                "footer": {
                    "text": f"Updated at {datetime.now().strftime('%H:%M')} | Source: Yahoo Finance"
                }
            }
        ]
    }
    
    # --- 发送 ---
    try:
        response = requests.post(WEBHOOK_URL, json=payload)
        if 200 <= response.status_code < 300:
            print(f"✅ Embed 推送成功! ({datetime.now().strftime('%H:%M:%S')})")
        else:
            print(f"❌ 推送失败: {response.text}")
    except Exception as e:
        print(f"❌ 网络错误: {e}")

# ==========================================
# 3. 主程序
# ==========================================
def main():
    print("🚀 Embed 监控模式已启动...")
    while True:
        a, t, y = get_market_data_and_rate()
        send_embed_to_discord(a, t, y)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
