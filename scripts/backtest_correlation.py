import os
import sys
import pandas as pd
import numpy as np
from pymongo import MongoClient
import traceback

# Ensure we can import from the parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cvd.calculator import run_pipeline

TIERS = {
    "Large": ["NVDA", "AAPL", "TSLA", "MSFT", "JPM", "XOM", "UNH", "WMT", "CAT", "DIS"],
    "Mid":   ["RBLX", "DKNG", "AFRM", "CVNA", "CROX", "SOFI", "CHWY", "RIVN", "PLTR", "U"],
    "Small": ["GME", "AMC", "BYND", "UPST", "FUBO", "CLOV", "WKHS", "MULN", "SIRI", "RUM"]
}


def analyze_correlation(ticker: str):
    try:
        # We use the existing pipeline to get perfectly calculated CVD
        # We just need the base timeframe (1min)
        df, _ = run_pipeline(ticker)
        if df.empty:
            return None
            
        df = df.copy()
        
        # We need continuous data for rolling correlation, but market hours have gaps.
        # However, for a simple rolling Pearson, we can just use rolling on the rows.
        
        # 1. Rolling Pearson Correlation (Window = 390 rows, roughly 1 trading day of 1-min bars)
        window = 390
        rolling_corr = df["cvd_all"].rolling(window).corr(df["close"])
        avg_rolling_corr = rolling_corr.mean()
        
        # 2. Lead-Lag Cross Correlation
        # Does CVD lead price? We check the correlation of today's CVD with tomorrow's price (or +N minutes)
        # Shift close BACKWARDS to see if current CVD correlates with FUTURE close.
        # Positive lag means we are comparing CVD(t) with Close(t + lag).
        # We'll use 5 min, 15 min, and 60 min lags.
        lags = [1, 5, 15, 60]
        lead_corrs = {}
        
        # Since cumulative series have high autocorrelation, it's better to correlate changes (returns)
        # However, the user asked for cvd_all vs close. We'll provide both.
        
        # For cross-correlation, we'll use the difference (delta vs price change) to avoid spurious correlation
        delta = df["delta"]
        price_change = df["close"].diff()
        
        for lag in lags:
            shifted_price_change = price_change.shift(-lag)
            corr = delta.corr(shifted_price_change)
            lead_corrs[f"Lead {lag}m (Delta->Price)"] = corr
            
        return {
            "Ticker": ticker,
            "Total Bars": len(df),
            "Avg 1-Day Rolling Corr (cvd_all vs close)": avg_rolling_corr,
            **lead_corrs
        }
    except Exception as e:
        print(f"Error analyzing {ticker}: {e}")
        return None

def main():
    print("Starting CVD-Price Correlation Backtest...\n")
    
    results = []
    
    for tier, tickers in TIERS.items():
        print(f"--- Analyzing {tier} Cap ---")
        for ticker in tickers:
            print(f"Analyzing {ticker}...")
            stats = analyze_correlation(ticker)
            if stats:
                stats["Tier"] = tier
                results.append(stats)
                
    if not results:
        print("No results generated.")
        return
        
    res_df = pd.DataFrame(results)
    
    report = "# Price-CVD Correlation & Lead-Lag Backtest Report\n\n"
    report += "This report analyzes the correlation between price and Cumulative Volume Delta (CVD) across different market cap tiers.\n\n"
    
    report += "## Summary by Tier\n"
    summary = res_df.groupby("Tier").agg({
        "Avg 1-Day Rolling Corr (cvd_all vs close)": "mean",
        "Lead 1m (Delta->Price)": "mean",
        "Lead 5m (Delta->Price)": "mean",
        "Lead 15m (Delta->Price)": "mean",
    }).round(3).reset_index()
    
    summary["Tier"] = pd.Categorical(summary["Tier"], ["Large", "Mid", "Small"])
    summary = summary.sort_values("Tier")
    report += summary.to_markdown(index=False) + "\n\n"
    
    report += "## Detailed Results\n"
    res_df["Tier"] = pd.Categorical(res_df["Tier"], ["Large", "Mid", "Small"])
    res_df = res_df.sort_values(["Tier", "Avg 1-Day Rolling Corr (cvd_all vs close)"], ascending=[True, False])
    
    # Format floats for readability
    for col in res_df.columns:
        if "Corr" in col or "Lead" in col:
            res_df[col] = res_df[col].round(3)
            
    report += res_df.drop(columns=["Tier"]).to_markdown(index=False) + "\n\n"
    
    report += "## Conclusion\n"
    report += "1. **Rolling Correlation**: Shows whether the overall trend of CVD matches the price trend over a 1-day window.\n"
    report += "2. **Lead-Lag Analysis**: Compares the 1-minute Net Delta to future price changes. Positive values indicate CVD changes precede Price changes in the same direction.\n"
    report += "3. **Tier Comparison**: Lower liquidity stocks (Small caps) are expected to have lower correlation due to the inaccuracy of wick-based aggressive volume estimation.\n"
    
    report_path = os.path.join(os.path.dirname(__file__), "..", "Claude", "CVD_Correlation_Report.md")
    with open(report_path, "w") as f:
        f.write(report)
        
    print(f"\nReport generated at: {report_path}")

if __name__ == "__main__":
    main()
