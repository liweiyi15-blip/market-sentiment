import discord
import os
from curl_cffi import requests
from bs4 import BeautifulSoup
import asyncio
from discord.ext import commands, tasks
from datetime import datetime
import pytz

# --- 配置 ---
TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- 核心：抓取 Investing.com ---
def get_investing_data():
    try:
        url = "https://www.investing.com/central-banks/fed/rate-monitor"
        
        # 模拟真实用户访问
        response = requests.get(
            url, 
            impersonate="chrome120", 
            timeout=15
        )
        
        if response.status_code != 200:
            return f"⚠️ 访问失败 (Code {response.status_code}): Investing.com 也可能限制了 IP"

        # 解析 HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. 寻找概率表格
        # Investing.com 的类名通常比较固定，寻找 'fedRateMonitorTable'
        table = soup.find("table", class_="fedRateMonitorTable")
        if not table:
            # 尝试备用选择器（网站可能会改版）
            return "⚠️ 抓取失败: 找不到数据表格 (网站结构可能已变)"

        # 2. 提取数据行
        rows = table.find('tbody').find_all('tr')
        
        msg_body = ""
        best_prob = 0.0
        best_range = "Unknown"

        # 遍历每一行 (通常第一行是当前的或者最可能的)
        for row in rows:
            cols = row.find_all('td')
            if len(cols) >= 2:
                # 格式通常是: [利率区间, 概率, ...]
                # 例如: [4.50-4.75, 75.5%, ...]
                rate_range = cols[0].get_text(strip=True)
                prob_str = cols[1].get_text(strip=True).replace('%', '')
                
                try:
                    prob = float(prob_str)
                except:
                    continue

                if prob > best_prob:
                    best_prob = prob
                    best_range = rate_range
                
                # 只显示大概率的
                if prob > 1.0:
                    msg_body += f"🔹 **{rate_range}**: {prob}%\n"

        # 3. 获取下次会议时间
        # 尝试从页面标题或特定div获取，这里简化处理，直接提取页面上的日期信息
        # Investing.com 页面顶部通常有 "Next Meeting: Dec 18, 2025"
        date_info = "未知日期"
        # 尝试找一下通用的日期容器
        top_info = soup.find("div", class_="fedMonitorInfo")
        if top_info:
             # 简单的文本提取，可能包含多余空格
            date_text = top_info.get_text()
            if "Meeting:" in date_text:
                 # 粗略提取
                 date_info = date_text.split("Meeting:")[-1].strip().split("\n")[0]

        output = (
            f"📊 **Investing.com 利率观测**\n"
            f"📅 **下次会议**: {date_info}\n"
            f"---------------------------\n"
            f"{msg_body}\n"
            f"🔥 **当前共识**: {best_range} (概率 {best_prob}%)\n"
            f"🔗 源: Investing.com"
        )
        return output

    except Exception as e:
        return f"❌ 解析错误: {e}"

# --- 定时任务 ---
@tasks.loop(hours=24)
async def scheduled_task():
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        msg = get_investing_data()
        tz = pytz.timezone('Asia/Shanghai')
        current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
        await channel.send(f"{msg}\n🕒 更新时间: {current_time}")

@scheduled_task.before_loop
async def before_task():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    print(f'✅ 已登录: {bot.user}')
    if not scheduled_task.is_running():
        scheduled_task.start()

@bot.command()
async def fed(ctx):
    msg = await ctx.send("🌍 正在前往 Investing.com 获取数据...")
    data = get_investing_data()
    await msg.edit(content=data)

if __name__ == "__main__":
    bot.run(TOKEN)
