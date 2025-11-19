import discord
import os
import yfinance as yf
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

# --- 获取金融数据 ---
def get_market_data():
    try:
        # 1. 获取联邦基金期货 (ZQ=F) - 这是预测利率的核心
        # 注意：Yahoo的数据可能有15分钟延迟，但对预测来说足够了
        ticker_fed = yf.Ticker("ZQ=F")
        fed_data = ticker_fed.history(period="1d")
        
        if fed_data.empty:
            return "⚠️ 暂时无法获取期货数据 (Yahoo API 无响应)"
        
        # 获取最新价格
        last_price = fed_data['Close'].iloc[-1]
        
        # === 核心计算公式 ===
        # 市场预期的利率 = 100 - 期货价格
        implied_rate = 100 - last_price
        
        # 2. 获取 10年期国债 (市场风向标)
        ticker_10y = yf.Ticker("^TNX")
        tnx_data = ticker_10y.history(period="1d")
        tnx_rate = tnx_data['Close'].iloc[-1] if not tnx_data.empty else 0

        # 3. 获取 2年期国债 (对政策最敏感)
        ticker_2y = yf.Ticker("^IRX") # 通常用 IRX (13周) 或其他代码代替
        # 注: Yahoo 上 2年期代码不稳定，这里用 13周(^IRX) 作为短端利率参考
        irx_ticker = yf.Ticker("^IRX")
        irx_data = irx_ticker.history(period="1d")
        short_rate = irx_data['Close'].iloc[-1] if not irx_data.empty else 0

        # 4. 生成分析文案
        # 简单的趋势判断
        trend = ""
        # 假设当前基础利率约 4.5% (需根据实际调整，这里仅作基准对比)
        current_base_rate = 4.50 
        
        diff = implied_rate - current_base_rate
        if diff < -0.1:
            trend = "📉 市场正在押注 **降息**"
        elif diff > 0.1:
            trend = "📈 市场正在押注 **加息**"
        else:
            trend = "⚖️ 市场预期 **维持利率不变**"

        output = (
            f"💵 **Fed 利率市场预期 (Yahoo源)**\n"
            f"---------------------------\n"
            f"📊 **联邦基金期货 (ZQ)**: {last_price:.2f}\n"
            f"🔮 **市场隐含利率**: `{implied_rate:.2f}%`\n"
            f"💡 **信号**: {trend}\n\n"
            f"**参考指标**:\n"
            f"• 短期国债 (13周): {short_rate:.2f}%\n"
            f"• 长期国债 (10年): {tnx_rate:.2f}%\n"
            f"---------------------------\n"
            f"*(注: 隐含利率 < 当前利率 即代表降息预期)*"
        )
        return output

    except Exception as e:
        return f"❌ 数据获取错误: {e}"

# --- 定时任务 ---
@tasks.loop(hours=24)
async def scheduled_task():
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        msg = get_market_data()
        tz = pytz.timezone('Asia/Shanghai')
        current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
        await channel.send(f"{msg}\n🕒 更新: {current_time}")

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
    msg = await ctx.send("🔄 正在从 Yahoo Finance 计算隐含利率...")
    data = get_market_data()
    await msg.edit(content=data)

if __name__ == "__main__":
    bot.run(TOKEN)
