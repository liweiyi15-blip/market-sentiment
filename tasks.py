"""Report generation. Network delivery is injected by the scheduled runner."""
import gc
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# ==========================================
# ⚙️ 全局配置区
# ==========================================


# ------------------------------------------
# 🤖 机器人信息配置
# ------------------------------------------

# Reddit 热度 Bot
REDDIT_BOT_NAME = "Stocksera 舆情热度"
REDDIT_BOT_AVATAR = "https://i.imgur.com/8Qj5X9A.png"

# Fear & Greed Bot
FEAR_BOT_NAME = "CNN 恐慌贪婪指数"
FEAR_BOT_AVATAR = "https://i.imgur.com/Segc5PF.jpeg" 

# ==========================================
# 🛠️ 辅助函数: 计算排名变化
# ==========================================
def calculate_rank_change(current_rank, old_rank):
    """
    计算排名变化并返回图标
    """
    if not old_rank or old_rank == 0:
        return "new" # 新上榜
    
    diff = old_rank - current_rank
    
    if diff > 0:
        return f"🔺{diff}" # 排名上升
    elif diff < 0:
        return f"🔻{abs(diff)}" # 排名下降
    else:
        return "➖" # 持平

# ==========================================
# 🔴 模块 1: Reddit 热度榜 (完整修复+完美对齐版)
# ==========================================

def get_apewisdom_data():
    """
    使用 ApeWisdom API 获取 Reddit (WSB/Stocks) 热门股票
    """
    print("📡 正在从 ApeWisdom 获取数据...")
    url = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1"
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=20)
        
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', [])
            return results[:30] # Top 30
        else:
            print(f"⚠️ ApeWisdom API 错误: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ 获取 ApeWisdom 数据失败: {e}")
        return None

def calculate_rank_change_reddit(current_rank, old_rank):
    """
    计算排名变化图标
    """
    if not old_rank or old_rank == 0:
        return "🆕"
    
    diff = old_rank - current_rank
    if diff > 0: return f"🔺{diff}"
    elif diff < 0: return f"🔻{abs(diff)}"
    else: return "➖"

def run_reddit_task(send):
    # 1. 获取数据
    data = get_apewisdom_data()
    if not data:
        raise RuntimeError("Reddit source returned no data")

    desc_lines = []
    
    for item in data:
        rank = item.get('rank', 0)
        ticker = item.get('ticker', 'Unknown')
        name = item.get('name', '')
        mentions = item.get('mentions', 0)
        rank_24h = item.get('rank_24h_ago', 0)
        
        # 2. 获取变动字符
        change_raw = calculate_rank_change_reddit(rank, rank_24h)
        
        # 3. 名字处理
        name = name.replace("&amp;", "&").replace("\n", " ").strip()
        if len(name) > 8: name = name[:8] + "."
        
        # 4. 头部排版 (保持黑底以维持对齐)
        header_block = f"` {change_raw:<5} {rank:02d}. `"
        
        # 5. 拼接
        line = f"{header_block} **${ticker}** ({name}) 提及 `{mentions}`次"
        
        desc_lines.append(line)

    date_str = datetime.now(ZoneInfo('America/New_York')).strftime('%m月%d日') 
    
    payload = {
        "username": "散户买什么？", 
        "avatar_url": "https://i.imgur.com/iXlOzKP.png", 
        "embeds": [{
            "title": f"Reddit 24H 热度榜（{date_str}）",
            "description": "\n".join(desc_lines),
            "color": 0xFF4500, 
        }]
    }
    
    try:
        send(payload)
        print("✅ ApeWisdom Top30 报告处理完成")
    except Exception as e:
        print(f"❌ 推送失败: {e}")
        raise
        
    gc.collect()

# ==========================================
# 🟠 模块 2: CNN 恐慌贪婪指数
# ==========================================
def run_fear_greed_task(send, previous_value=None):
    import fear_and_greed
    print("📊 启动恐慌贪婪指数抓取...")

    try:
        fg = fear_and_greed.get()
        current_value = round(fg.value, 1)
        
        # 将API获取的描述强制转为小写并去除空格
        stage_desc = str(fg.description).strip().lower()

        # 翻译阶段描述，并加上官方的数值区间
        stage_map = {
            "extreme greed": "极度贪婪 (76-100)",
            "greed": "贪婪 (56-75)",
            "neutral": "中性 (45-55)",
            "fear": "恐慌 (25-44)",
            "extreme fear": "极度恐慌 (0-24)"
        }
        stage_cn = stage_map.get(stage_desc, stage_desc)

        # 计算并格式化变动 (使用文本箭头，无emoji)
        change_text = "初始化 (无对比数据)"
        if previous_value is not None:
            diff = current_value - previous_value
            if diff > 0:
                change_text = f"→ 升高了 {diff:.1f}"
            elif diff < 0:
                change_text = f"→ 降低了 {abs(diff):.1f}"
            else:
                change_text = "→ 保持不变"


        # 构建Embed排版 (纯文本排版，无图表)
        payload = {
            "username": FEAR_BOT_NAME,
            "avatar_url": FEAR_BOT_AVATAR,
            "embeds": [{
                "title": "CNN 市场情绪监测",
                "description": f"**当前情绪:** {stage_cn}\n"
                               f"**当前数值:** `{current_value}`\n"
                               f"**环比上一期:** {change_text}",
                "color": 0x9B59B6
            }]
        }

        send(payload, state_updates={"previous_fear_value": current_value})
        print("✅ 恐慌贪婪指数报告处理完成")

    except Exception as e:
        print(f"❌ 获取恐慌贪婪指数失败: {e}")
        raise

