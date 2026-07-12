import os
import sys
import pandas as pd
import numpy as np
import traceback

# Ensure we can import from the parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cvd.calculator import run_pipeline

TIERS = {
    "Mega": ["NVDA", "AAPL", "MSFT"],
    "Micro": ["GME", "AMC", "PLTR"],
    "Nano": ["PENN", "CHWY", "RUM"]
}

def analyze_session(df_session, window=390):
    if len(df_session) < window:
        return np.nan
    rolling_corr = df_session["cvd_all"].rolling(window).corr(df_session["close"])
    return rolling_corr.mean()

def analyze_correlation(ticker: str):
    try:
        df, _ = run_pipeline(ticker)
        if df.empty:
            return None
            
        df = df.copy()
        # Ensure index is datetime
        df.index = pd.to_datetime(df.index)
        
        # Split into sessions
        # Pre: 04:00 - 09:30
        # Reg: 09:30 - 16:00
        # Aft: 16:00 - 20:00
        time = df.index.time
        
        from datetime import time as dtime
        
        pre_mask = (time >= dtime(4, 0)) & (time < dtime(9, 30))
        reg_mask = (time >= dtime(9, 30)) & (time < dtime(16, 0))
        aft_mask = (time >= dtime(16, 0)) & (time < dtime(20, 0))
        
        df_pre = df[pre_mask]
        df_reg = df[reg_mask]
        df_aft = df[aft_mask]
        
        # We use a smaller window for pre/after since they are shorter
        # Pre is 5.5 hours = 330 mins -> window 60
        # Reg is 6.5 hours = 390 mins -> window 120
        # Aft is 4.0 hours = 240 mins -> window 60
        
        corr_pre = analyze_session(df_pre, window=60)
        corr_reg = analyze_session(df_reg, window=120)
        corr_aft = analyze_session(df_aft, window=60)
        
        return {
            "Ticker": ticker,
            "Total Bars": len(df),
            "Pre-Market Corr": corr_pre,
            "Regular Corr": corr_reg,
            "After-Hours Corr": corr_aft
        }
    except Exception as e:
        print(f"Error analyzing {ticker}: {e}")
        return None

def main():
    print("Starting 3x3 CVD-Price Correlation Backtest...\n")
    
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
    
    report = "# 3x3 Matrix: Price-CVD Correlation Report\n\n"
    report += "This report analyzes the rolling correlation between price and Tick CVD across 3 Market Caps and 3 Trading Sessions.\n\n"
    
    report += "## Summary by Tier and Session\n"
    summary = res_df.groupby("Tier").agg({
        "Pre-Market Corr": "mean",
        "Regular Corr": "mean",
        "After-Hours Corr": "mean"
    }).round(3).reset_index()
    
    summary["Tier"] = pd.Categorical(summary["Tier"], ["Mega", "Micro", "Nano"])
    summary = summary.sort_values("Tier")
    report += summary.to_markdown(index=False) + "\n\n"
    
    report += "## Detailed Results\n"
    res_df["Tier"] = pd.Categorical(res_df["Tier"], ["Mega", "Micro", "Nano"])
    res_df = res_df.sort_values(["Tier", "Regular Corr"], ascending=[True, False])
    
    # Format floats
    for col in ["Pre-Market Corr", "Regular Corr", "After-Hours Corr"]:
        res_df[col] = res_df[col].round(3)
            
    report += res_df.drop(columns=["Tier"]).to_markdown(index=False) + "\n\n"
    
    report_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "CVD_3x3_Correlation_Report.md")
    with open(report_path, "w") as f:
        f.write(report)
        
    print(f"\nReport generated at: {report_path}")

if __name__ == "__main__":
    main()
