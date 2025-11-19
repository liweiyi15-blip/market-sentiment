import time
import math
import requests
import yfinance as yf
from datetime import datetime

# ==========================================
# ⚙️ 核心配置
# ==========================================
# ✅ 已填入你的 Webhook (请妥善保管此链接，不要发给别人)
WEBHOOK_URL = "https://discord.com/api/webhooks/1440732182334148831/6ji21aLb5ZZ103Qp6WdbHkRiTXpxUf_pHa1BCZAadKpNWcGpvTXNfbY6r_534cjaHZAG"

NEXT_MEETING_DATE = "2025-12-18" # 下次会议日期
TICKER_SYMBOL = "ZQ=F"           # 30天联邦基金期货
CHECK_INTERVAL = 7200            # 刷新间隔: 7200秒 (2小时)

# ==========================================
# 1. 数据获取与自动校准模块
# ==========================================
def get_market_data_and_rate():
    """
    获取 Yahoo Finance 数据并自动推导当前美联储基准利率
    """
    try:
        ticker = yf.Ticker(TICKER_SYMBOL)
        # 获取5天数据以确保有昨天的数据
        hist = ticker.history(period="5d")
        
        if hist.empty:
            print(f"⚠️ [{datetime.now()}] 无法获取 Yahoo 数据，正在重试...")
            return None, None, None
            
        # 获取最新和前一天的收盘价
        current_price = hist['Close'].iloc[-1]
        prev_price = hist['Close'].iloc[-2]
        
        # 算出隐含利率
        today_implied = 100 - current_price
        yesterday_implied = 100 - prev_price
        
        # --- 自动推导官方基准利率 (Auto-Calibration) ---
        # 逻辑: 将市场利率按 0.25 取整，找到最近的区间下限
        lower_bound = round(today_implied / 0.25) * 0.25
        auto_fed_rate = lower_bound + 0.25
        
        return auto_fed_rate, today_implied, yesterday_implied

    except Exception as e:
        print(f"❌ 数据错误: {e}")
        return None, None, None

# ==========================================
# 2. Discord Embed 构建与发送模块
# ==========================================
def send_embed_to_discord(current_fed_rate, today_implied, yesterday_implied):
    
    # --- A. 概率计算 ---
    def get_prob(implied):
        diff = current_fed_rate - implied
        prob = (diff / 0.25) * 100
        return max(0.0, min(100.0, prob))

    prob_today = get_prob(today_implied)
    prob_yesterday = get_prob(yesterday_implied)
    prob_hold = 100.0 - prob_today
    
    # 计算趋势变化
    delta = prob_today - prob_yesterday
    
    # --- B. 样式逻辑 ---
    
    # 1. 确定卡片颜色 (Color Bar)
    if prob_today > 50:
        embed_color = 0x57F287 # 🟩 绿色 (降息预期强)
        consensus_text = "降息 25bps (Cut)"
        consensus_icon = "📉"
    else:
        embed_color = 0x3498DB # 🟦 蓝色 (维持预期强)
        consensus_text = "维持利率 (Hold)"
        consensus_icon = "⏸️"

    # 2. 确定趋势文案
    if delta > 0.1:
        trend_text = f"降息概率上升 **{delta:.1f}%**"
        trend_icon = "🔥"
    elif delta < -0.1:
        trend_text = f"降息概率下降 **{abs(delta):.1f}%**"
        trend_icon = "❄️"
    else:
        trend_text = "预期保持稳定"
        trend_icon = "⚖️"

    # --- C. 构建 Embed 正文 (单行紧凑布局) ---
    
    # 目标区间文字
    target_cut = f"{current_fed_rate-0.25:.2f}-{current_fed_rate:.2f}%"
    target_hold = f"{current_fed_rate:.2f}-{current_fed_rate+0.25:.2f}%"
    
    # 进度条生成器 (8格长度)
    def make_bar(prob, char):
        length = int(prob // 12.5)
        return char * length + "░" * (8 - length)

    bar_cut = make_bar(prob_today, "🟩")
    bar_hold = make_bar(prob_hold, "🟦")

    # 组合 Description 内容
    # 使用 \n 换行，保持你喜欢的紧凑格式
    desc_lines = [
        f"**🗓️ 下次会议:** `{NEXT_MEETING_DATE}`",
        f"**⚓ 当前基准:** `{target_cut}` (Auto)",  # 显示当前的基准区间
        "",
        "**🎯 目标区间分布 (Probabilities)**",
        f"📉 **目标: {target_cut} (降息)** {bar_cut} **{prob_today:.1f}%**",
        f"⏸️ **目标: {target_hold} (维持)** {bar_hold} {prob_hold:.1f}%",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]
    description_text = "\n".join(desc_lines)

    # --- D. 组装 JSON Payload ---
    payload = {
        "username": "CME FedWatch Bot",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/CME_Group_logo.svg/1200px-CME_Group_logo.svg.png",
        "embeds": [
            {
                "title": "🏛️ CME FedWatch™ 市场观察",
                "description": description_text,
                "color": embed_color,
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
                    "text": f"Updated at {datetime.now().strftime('%H:%M')} | Data: Yahoo Finance (ZQ=F)"
                }
            }
        ]
    }
    
    # --- E. 发送请求 ---
    try:
        res = requests.post(WEBHOOK_URL, json=payload)
        if 200 <= res.status_code < 300:
            print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] 推送成功!")
        else:
            print(f"❌ 推送失败: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ 网络错误: {e}")

# ==========================================
# 3. 主程序入口
# ==========================================
def main():
    print("🚀 监控服务已启动...")
    print(f"🎯 目标: {TICKER_SYMBOL}")
    print(f"⏱️ 频率: 每 {CHECK_INTERVAL} 秒 (2小时)")
    
    # 立即执行一次，然后进入循环
    while True:
        rate, today, yesterday = get_market_data_and_rate()
        
        if rate is not None:
            send_embed_to_discord(rate, today, yesterday)
        
        # 倒计时休眠
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
