import requests
import pandas as pd
import schedule
import time
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
from datetime import datetime, timezone, timedelta
from pytz import timezone as pytz_timezone  # pip install pytz 如果需要

# 配置
WEBHOOK_URL = "YOUR_DISCORD_WEBHOOK_URL_HERE"  # 替换为你的Discord Webhook URL
FMP_API_KEY = "your_fmp_api_key_here"  # 从FMP dashboard获取并替换
HISTORY_DAYS = 30  # 图表显示最近30天
UPDATE_HOUR = "00"  # 每小时整点 (HH, 如"00" for 00:00, 但用schedule.every().hour.do() 简化)

def get_fear_greed_history(days=HISTORY_DAYS):
    """获取CNN Fear & Greed最近days天历史"""
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days*2)  # 多取点防缺失
    url = f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{today}"
    try:
        response = requests.get(url)
        data = response.json()
        historical = data['fear_and_greed_historical']['data']
        df = pd.DataFrame(historical)
        df['date'] = pd.to_datetime(df['x'], unit='ms').dt.date
        df = df[df['date'] >= start_date].tail(days)  # 最近days天
        df = df.sort_values('date').set_index('date')
        return df['y']  # score
    except Exception as e:
        print(f"获取Fear & Greed历史失败: {e}")
        return pd.Series()

def get_sp500_tickers():
    """从FMP获取S&P 500股票列表"""
    url = f"https://financialmodelingprep.com/api/v3/sp500_constituent?apikey={FMP_API_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        return [stock['symbol'] for stock in data]
    except Exception as e:
        print(f"获取S&P 500列表失败: {e}")
        return []

def get_historical_prices(symbol, days=HISTORY_DAYS * 2):
    """从FMP获取单个股票最近days天历史价格"""
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=days + 50)).strftime('%Y-%m-%d')  # 多取50天防SMA
    url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}?from={start_date}&to={end_date}&apikey={FMP_API_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        if 'historical' in data:
            df = pd.DataFrame(data['historical'])
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').set_index('date')
            return df['close']
        return pd.Series()
    except Exception as e:
        print(f"获取 {symbol} 历史价格失败: {e}")
        return pd.Series()

def calculate_market_participation_history(tickers, days=HISTORY_DAYS):
    """计算最近days天高于20日/50日SMA的百分比历史"""
    dates = pd.date_range(end=datetime.now().date(), periods=days, freq='D')[:-1]  # 最近days-1个交易日
    participation_20 = pd.Series(index=dates, dtype=float)
    participation_50 = pd.Series(index=dates, dtype=float)
    
    for date in dates:
        # 为每个历史日期计算 (需历史价格到date+50天)
        hist_days_needed = 50 + (datetime.now() - date).days
        above_20, above_50, total = 0, 0, 0
        for ticker in tickers[:100]:  # 测试用前100只，实际全用但慢；或缓存
            closes = get_historical_prices(ticker, hist_days_needed)
            if len(closes) < 50 or closes.index[-1].date() < date:
                continue
            df_up_to_date = closes[closes.index.date <= date]
            if len(df_up_to_date) < 50:
                continue
            close = df_up_to_date.iloc[-1]
            sma20 = df_up_to_date.rolling(window=20).mean().iloc[-1]
            sma50 = df_up_to_date.rolling(window=50).mean().iloc[-1]
            if pd.notna(close) and pd.notna(sma20) and pd.notna(sma50):
                total += 1
                if close > sma20:
                    above_20 += 1
                if close > sma50:
                    above_50 += 1
        if total > 0:
            participation_20[date] = (above_20 / total) * 100
            participation_50[date] = (above_50 / total) * 100
        time.sleep(0.05)  # 限流
    
    return participation_20, participation_50

def create_charts(fg_series, part20, part50):
    """生成图表并返回BytesIO图像"""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    
    # 上图：市场参与度
    ax1.plot(part20.index, part20.values, 'r-', label='高于20日SMA', linewidth=1.5)
    ax1.plot(part50.index, part50.values, 'b-', label='高于50日SMA', linewidth=1.5)
    ax1.set_title('S&P 500 市场参与度 (最近30天)')
    ax1.set_ylabel('百分比 (%)')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax1.tick_params(axis='x', rotation=45)
    
    # 下图：Fear & Greed
    ax2.plot(fg_series.index, fg_series.values, 'orange', label='CNN Fear & Greed Index', linewidth=1.5)
    ax2.set_title('CNN 恐慌与贪婪指数 (最近30天)')
    ax2.set_ylabel('指数 (0-100)')
    ax2.set_xlabel('日期')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax2.tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    plt.close()
    return buf

def send_chart_to_discord(image_buf, filename='market_update.png'):
    """发送图像到Discord"""
    files = {'file': (filename, image_buf, 'image/png')}
    data = {'content': f"**每小时市场更新 - {datetime.now().strftime('%Y-%m-%d %H:%M')} (美东时间)**\n数据来源: CNN & FMP"}
    try:
        response = requests.post(WEBHOOK_URL, data=data, files=files)
        if response.status_code == 200:
            print("图表发送成功")
        else:
            print(f"发送失败: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Discord发送错误: {e}")

def job():
    """每小时任务"""
    now = datetime.now(pytz_timezone('US/Eastern'))
    print(f"执行时间: {now}")
    
    # 获取数据
    fg_series = get_fear_greed_history()
    tickers = get_sp500_tickers()
    print(f"处理 {len(tickers)} 只股票的历史...")
    part20, part50 = calculate_market_participation_history(tickers)
    
    if len(fg_series) > 0 and len(part20) > 0:
        # 生成图表
        image_buf = create_charts(fg_series, part20, part50)
        # 发送
        send_chart_to_discord(image_buf)
    else:
        print("数据不足，跳过发送")

# 定时调度：每小时运行 (美东时间)
schedule.every().hour.do(job)

# 运行循环
print("图表机器人启动，每小时更新...")
while True:
    schedule.run_pending()
    time.sleep(60)
