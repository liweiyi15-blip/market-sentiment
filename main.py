import discord
import os
import requests
import json
import asyncio
from discord.ext import commands, tasks
from datetime import datetime
import pytz

# --- 1. 配置与初始化 ---
TOKEN = os.getenv("DISCORD_TOKEN")
# 如果没有设置 CHANNEL_ID，这里默认写 0，但在 Railway 必须设置环境变量
TARGET_CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# 启用必要的 Intents
intents = discord.Intents.default()
intents.message_content = True # 允许读取消息内容

bot = commands.Bot(command_prefix="!", intents=intents)

# --- 2. 获取 FED 数据函数 (核心) ---
def get_fed_data():
    try:
        # CME 官方 API 接口
        url = "https://www.cmegroup.com/CmeWS/mvc/Tool/FedWatch/List"
        
        # 伪装浏览器头，防止被 CME 拦截
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html",
            "Origin": "https://www.cmegroup.com",
            "Accept": "application/json, text/plain, */*"
        }

        # 发起请求
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return f"⚠️ 数据获取失败: CME 响应码 {response.status_code}"

        data = response.json()

        if not data or len(data) == 0:
            return "⚠️ 未获取到会议数据 (数据为空)"

        # 获取最近的一次会议
        next_meeting = data[0]
        meeting_date_str = next_meeting.get('meetingDate', 'Unknown')
        
        # 格式化日期
        try:
            # CME 格式: "18 Dec 2024"
            dt = datetime.strptime(meeting_date_str, "%d %b %Y")
            formatted_date = dt.strftime("%Y年%m月%d日")
        except:
            formatted_date = meeting_date_str

        # 获取概率列表
        prob_list = next_meeting.get('groupList', [])
        
        msg_body = ""
        best_prob = 0
        best_range = "Unknown"

        # 遍历概率
        for item in prob_list:
            probability = item.get('probability', 0)
            # 利率区间
            target_range = f"{item.get('targetRangeLower')}-{item.get('targetRangeUpper')}"
            
            # 找出最大概率
            if probability > best_prob:
                best_prob = probability
                best_range = target_range
            
            # 只显示概率 > 1% 的
            if probability > 1.0:
                msg_body += f"🔹 **{target_range} bps**: {probability:.1f}%\n"

        # 组装最终消息
        output = (
            f"📊 **FEDWatch 利率预测**\n"
            f"📅 **下次会议**: {formatted_date}\n"
            f"---------------------------\n"
            f"{msg_body}\n"
            f"🔥 **当前共识**: {best_range} bps (概率 {best_prob:.1f}%)"
        )
        return output

    except Exception as e:
        return f"❌ 程序内部错误: {e}"

# --- 3. 定时任务 ---
# 设置每天运行一次 (每24小时)
@tasks.loop(hours=24)
async def scheduled_task():
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        print("正在执行定时发送...")
        msg = get_fed_data()
        # 获取当前北京时间
        tz = pytz.timezone('Asia/Shanghai')
        current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
        
        await channel.send(f"{msg}\n🕒 更新时间: {current_time}")
    else:
        print(f"⚠️ 找不到频道 ID: {TARGET_CHANNEL_ID}，请检查环境变量")

@scheduled_task.before_loop
async def before_task():
    await bot.wait_until_ready()

# --- 4. Bot 事件与指令 ---
@bot.event
async def on_ready():
    print(f'✅ 已登录为 {bot.user}')
    print('🚀 定时任务已启动')
    if not scheduled_task.is_running():
        scheduled_task.start()

# 手动测试指令：在 Discord 输入 !fed 即可立即查看结果
@bot.command()
async def fed(ctx):
    await ctx.send("正在获取最新数据...")
    msg = get_fed_data()
    await ctx.send(msg)

# --- 5. 启动 ---
if __name__ == "__main__":
    if not TOKEN:
        print("❌ 错误: 未设置 DISCORD_TOKEN 环境变量")
    else:
        bot.run(TOKEN)
