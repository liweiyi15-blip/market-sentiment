import time
import math
import yfinance as yf
from datetime import datetime

# ==========================================
# ⚙️ 配置区 (只留这一行日期即可)
# ==========================================
NEXT_MEETING_DATE = "2025-12-18" # 下次议息会议日期
TICKER_SYMBOL = "ZQ=F"           # 联邦基金期货代码
CHECK_INTERVAL = 7200            # 刷新间隔 (秒)

# ==========================================
# 1. 数据获取与自动计算模块
# ==========================================
def get_market_data_and_rate():
    """
    同时获取：
    1. 自动推导出的【当前官方基准利率】(Current Fed Rate)
    2. 市场对于未来的【隐含利率】(Implied Rate)
    3. 昨日的隐含利率 (用于算趋势)
    """
    try:
        ticker = yf.Ticker(TICKER_SYMBOL)
        hist = ticker.history(period="5d")
        
        if hist.empty:
            print("⚠️ 警告: 无法获取 Yahoo 数据，使用备用数据演示。")
            return 4.50, 4.35, 4.40 # [官方, 今日, 昨日]
            
        # A. 获取原始价格
        current_price = hist['Close'].iloc[-1]
        prev_price = hist['Close'].iloc[-2]
        
        # B. 算出市场真实利率 (EFFR)
        # 逻辑: 100 - 95.65 = 4.35%
        today_implied = 100 - current_price
        yesterday_implied = 100 - prev_price
        
        # C. 【核心算法】自动推导官方基准利率 (Upper Bound)
        # 逻辑：市场利率通常在目标区间的下半部分。
        # 例如：如果目标是 4.25-4.50，市场利率通常在 4.33 左右。
        # 算法：将市场利率除以 0.25，四舍五入取整，再乘以 0.25，得到下限。
        # 下限 + 0.25 = 上限 (我们通常显示的基准)
        
        lower_bound = round(today_implied / 0.25) * 0.25
        auto_fed_rate = lower_bound + 0.25
        
        # 防抖动修正: 如果算出 4.33 -> Lower 4.25 -> Upper 4.50 (正确)
        # 如果市场极度恐慌跌到 4.10 -> Lower 4.00 -> Upper 4.25 (正确)
        
        return auto_fed_rate, today_implied, yesterday_implied

    except Exception as e:
        print(f"⚠️ 数据异常: {e}")
        return 4.50, 4.35, 4.40

# ==========================================
# 2. 核心显示模块 (完美单行版)
# ==========================================
def render_cme_card(current_fed_rate, today_implied, yesterday_implied):
    
    # --- 内部计算 ---
    def get_prob(implied):
        # 使用自动获取的 current_fed_rate 进行计算
        diff = current_fed_rate - implied
        prob = (diff / 0.25) * 100
        return max(0.0, min(100.0, prob))

    prob_today = get_prob(today_implied)
    prob_yesterday = get_prob(yesterday_implied)
    prob_hold = 100.0 - prob_today
    
    delta = prob_today - prob_yesterday
    
    # 趋势文案
    if delta > 0.1:
        trend_text = f"降息概率上升 {delta:.1f}%"
        trend_icon = "🔥"
    elif delta < -0.1:
        trend_text = f"降息概率下降 {abs(delta):.1f}%"
        trend_icon = "❄️"
    else:
        trend_text = "预期保持稳定"
        trend_icon = "⚖️"

    # --- 渲染 UI ---
    now_str = datetime.now().strftime("%H:%M")
    
    print("\n" + "> " + "="*38)
    print(f"> ## 🏛️ CME FedWatch™ 市场观察")
    print(f">")
    print(f"> **🗓️ 下次会议:** `{NEXT_MEETING_DATE}`")
    # 显示当前自动锁定的基准利率，方便你核对
    print(f"> **⚓ 当前基准:** `{current_fed_rate-0.25:.2f}-{current_fed_rate:.2f}%` (Auto)")
    print(f">")
    print(f"> **🎯 目标区间分布 (Probabilities)**")
    print(f">")
    
    # 辅助绘图函数
    def print_row(icon, target_range, label, prob, color_char):
        bar_len = int(prob // 12.5)
        bar_visual = color_char * bar_len + "░" * (8 - bar_len)
        print(f"> {icon} **目标: {target_range} ({label})** {bar_visual} {prob:.1f}%")

    # 动态生成目标区间文字
    range_cut_str = f"{current_fed_rate-0.25:.2f}-{current_fed_rate:.2f}%"
    range_hold_str = f"{current_fed_rate:.2f}-{current_fed_rate+0.25:.2f}%"

    # 行 1: 降息
    print_row("📉", range_cut_str, "降息", prob_today, "🟩")
    print(">") 
    
    # 行 2: 维持
    print_row("⏸️", range_hold_str, "维持", prob_hold, "🟦")
    
    print("> " + "-"*38)
    
    # 底部总结
    print(f"> {trend_icon} **趋势:** {trend_text}")
    
    consensus = "降息 25bps" if prob_today > 50 else "维持利率"
    print(f"> 💡 **共识:** {consensus}")
    
    print("> " + "="*38 + "\n")

# ==========================================
# 3. 主程序
# ==========================================
def main():
    print(f"🚀 全自动监控已启动 | 自动校准基准利率...")
    
    while True:
        # 1. 获取全套数据 (含自动基准)
        auto_rate, t_rate, y_rate = get_market_data_and_rate()
        
        # 2. 渲染
        render_cme_card(auto_rate, t_rate, y_rate)
        
        # 3. 等待
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
