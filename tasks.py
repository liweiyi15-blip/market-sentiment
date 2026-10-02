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
