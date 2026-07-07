import datetime
import random
from pymongo import MongoClient
import pandas as pd
from zoneinfo import ZoneInfo
from cvd.calculator import run_pipeline
from cvd.visualizer import build_chart, write_chart_html
import webbrowser
import os

ET = ZoneInfo("America/New_York")

def inject_mock_ticks(ticker: str, num_seconds: int = 3600):
    """
    Injects fake 1-second ibkr_tick data into MongoDB to test the pipeline
    without waiting for the market to open.
    """
    client = MongoClient("mongodb://localhost:27017/")
    col = client["finviz_db"]["candles"]
    raw_col = client["finviz_db"]["raw_ticks"]
    
    # Generate fake 1-sec ticks for the last hour
    now = datetime.datetime.now(ET).replace(microsecond=0, tzinfo=None)
    start_time = now - datetime.timedelta(seconds=num_seconds)
    
    price = 100.0
    docs = []
    raw_docs = []
    
    print(f"[Mock] Generating {num_seconds} seconds of fake tick data for {ticker}...")
    for i in range(num_seconds):
        current_time = start_time + datetime.timedelta(seconds=i)
        
        # Random walk
        change = random.uniform(-0.05, 0.05)
        price += change
        
        # Fake real ticks
        volume = random.randint(100, 5000)
        
        # Let's create an artificial CVD trend: buy pressure increases
        if i < num_seconds / 2:
            buy_ratio = random.uniform(0.6, 0.9) # Strong buy
        else:
            buy_ratio = random.uniform(0.1, 0.4) # Strong sell
            
        buying_volume = volume * buy_ratio
        selling_volume = volume * (1 - buy_ratio)
        delta = buying_volume - selling_volume
        
        doc = {
            "ticker": ticker,
            "timeframe": "1sec",
            "date": current_time,
            "open": price - change,
            "high": price + abs(change),
            "low": price - abs(change),
            "close": price,
            "volume": volume,
            "buying_volume": buying_volume,
            "selling_volume": selling_volume,
            "delta": delta,
            "source": "ibkr_tick" # Crucial: tells calculator to skip wick decomp
        }
        docs.append(doc)
        
        # Raw tick
        raw_docs.append({
            "ticker": ticker,
            "date": current_time,
            "price": price,
            "size": volume,
            "delta": delta,
            "source": "ibkr_tick"
        })
        
    # Upsert to MongoDB
    print(f"[Mock] Saving to MongoDB...")
    for doc in docs:
        col.update_one(
            {"ticker": ticker, "timeframe": "1sec", "date": doc["date"]},
            {"$set": doc},
            upsert=True
        )
    
    if raw_docs:
        raw_col.insert_many(raw_docs)
    print(f"[Mock] Saved {len(docs)} fake ticks and {len(raw_docs)} raw ticks.")

def run_test():
    ticker = "MOCK_NVDA"
    inject_mock_ticks(ticker)
    
    print(f"\n[Test] Running pipeline on {ticker}...")
    df_base, frames = run_pipeline(ticker, base_timeframe="raw_tick")
    
    if df_base.empty:
        print("[Test] Failed to load data.")
        return
        
    print(f"\n[Test] Data successfully bypassed wick decomposition!")
    print(f"Sources in data: {df_base['source'].value_counts().to_dict()}")
    
    fig = build_chart(df_base, frames, ticker)
    path = f"{ticker}_tick_test_chart.html"
    write_chart_html(fig, path)
    
    print(f"\n[Test] Opening chart: {path}")
    webbrowser.open(f"file://{os.path.abspath(path)}")

if __name__ == "__main__":
    run_test()
