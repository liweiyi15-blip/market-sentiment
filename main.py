import discord
import os
from curl_cffi import requests # 核心修改：改用这个强力库
import json
import asyncio
from discord.ext import commands, tasks
from datetime import datetime
import pytz

# --- 1. 配置 ---
TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

# --- 2. 获取数据 (抗封锁版) ---
def get_fed_data():
    try:
        url = "https://www.cmegroup.com/CmeWS/mvc/Tool/FedWatch/List"
        
        # 使用 impersonate="chrome110" 模拟真实的 Chrome 浏览器
        # 这能绕过 Railway IP 的指纹封锁
        response = requests.get(
            url, 
            impersonate="chrome110", 
            timeout=10
        )
        
        if response.status_code != 200:
            return f"⚠️ 依然被拦截: 状态码 {response.status_code}"

        data = response.json()

        if not data:
            return "⚠️ 数据为空"

        next_meeting = data[0]
        meeting_date_str = next_meeting.get('meetingDate', 'Unknown')
        
        try:
            dt = datetime.strptime(meeting_date_str, "%d %b %Y")
            formatted_date = dt.strftime("%Y年%m月%d日")
        except:
            formatted_date = meeting_date_str

        prob_list = next_meeting.get('groupList', [])
        msg_body = ""
        best_prob = 0
        best_range = "Unknown"

        for item in prob_list:
            probability = item.get('probability', 0)
            target_range = f"{item.get('targetRangeLower')}-{item.get('targetRangeUpper')}"
            
            if probability > best_prob:
                best_prob = probability
                best_range = target_range
            
            if probability > 1.0:
                msg_body += f"🔹 **{target_range} bps**: {probability:.1f}%\n"

        output = (
            f"📊 **FEDWatch 利率预测**\n"
            f"📅 **下次会议**: {formatted_date}\n"
            f"---------------------------\n"
            f"{msg_body}\n"
            f"🔥 **当前共识**: {best_range} bps (概率 {best_prob:.1f}%)"
        )
        return output

    except Exception as e:
        return f"❌ 报错: {e}"

# --- 3. 定时任务 ---
@tasks.loop(hours=24)
async def scheduled_task():
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        print(f"正在向频道 {channel.name} 发送定时消息...")
        msg = get_fed_data()
        tz = pytz.timezone('Asia/Shanghai')
        current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
        await channel.send(f"{msg}\n🕒 更新时间: {current_time}")
    else:
        print(f"⚠️ 定时任务失败: 无法找到频道 ID {TARGET_CHANNEL_ID}")

@scheduled_task.before_loop
async def before_task():
    await bot.wait_until_ready()

# --- 4. 事件与调试 ---
@bot.event
async def on_ready():
    print(f'✅ 已登录: {bot.user}')
    
    # --- 频道 ID 调试自检 ---
    print("--- 正在检查频道权限 ---")
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        print(f"✅ 成功找到目标频道: {channel.name} (ID: {channel.id})")
    else:
        print(f"❌ 失败: 机器人找不到 ID 为 {TARGET_CHANNEL_ID} 的频道。")
        print("可能是以下原因：\n1. 机器人没有该频道的'查看频道'权限\n2. ID 填错了 (请务必复制频道ID，而不是服务器ID)")
        print("⬇️ 机器人当前能看到的所有频道 ⬇️")
        for guild in bot.guilds:
            for c in guild.text_channels:
                print(f" - {c.name}: {c.id}")
    
    if not scheduled_task.is_running():
        scheduled_task.start()

@bot.command()
async def fed(ctx):
    await ctx.send("🔍 正在绕过防火墙获取数据...")
    msg = get_fed_data()
    await ctx.send(msg)

if __name__ == "__main__":
    bot.run(TOKEN)
