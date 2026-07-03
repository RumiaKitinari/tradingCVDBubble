import os
import sys
import pandas as pd
from pymongo import MongoClient

# Ensure we can import from the parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cvd.calculator import _flag_auction_1min

# ─────────────────────────────────────────
# Ticker Lists
# ─────────────────────────────────────────
TIERS = {
    "Large": ["NVDA", "AAPL", "TSLA", "MSFT", "JPM", "XOM", "UNH", "WMT", "CAT", "DIS"],
    "Mid":   ["RBLX", "DKNG", "AFRM", "CVNA", "CROX", "SOFI", "CHWY", "RIVN", "PLTR", "U"],
    "Small": ["GME", "AMC", "BYND", "UPST", "FUBO", "CLOV", "WKHS", "MULN", "SIRI", "RUM"]
}

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "finviz_db"


def analyze_moc(ticker: str, df: pd.DataFrame):
    """
    Analyze the MOC (Closing Auction) characteristics for a single ticker.
    """
    if df.empty:
        return None
        
    df = df.copy()
    minute = df.index.hour * 60 + df.index.minute
    reg = (minute >= 570) & (minute < 960)                 # 09:30-16:00
    reg_med = df.loc[reg].groupby(lambda ix: ix.date())["volume"].median()
    
    # Run our actual auction flagging logic (which uses 10x threshold)
    auction_flag = _flag_auction_1min(df, mult=10.0, spill_mult=3.0)
    
    # Identify candidates (15:59-16:01 or 12:59-13:01)
    is_candidate = ((minute >= 779) & (minute <= 781)) | ((minute >= 959) & (minute <= 961))
    candidates = df[is_candidate]
    
    total_days = 0
    detected_days = 0
    ratios = []
    
    for d, g in candidates.groupby(lambda ix: ix.date()):
        total_days += 1
        med = reg_med.get(d, float("nan"))
        if pd.isna(med) or med == 0:
            continue
            
        anchor = g["volume"].idxmax()
        anchor_vol = g.loc[anchor, "volume"]
        ratio = anchor_vol / med
        ratios.append(ratio)
        
        if auction_flag.loc[anchor]:
            detected_days += 1
            
    avg_ratio = sum(ratios) / len(ratios) if ratios else 0
    detect_rate = (detected_days / total_days * 100) if total_days > 0 else 0
    
    return {
        "Ticker": ticker,
        "Total Days": total_days,
        "Detected Days": detected_days,
        "Detection Rate (%)": round(detect_rate, 1),
        "Avg Vol/Median Ratio": round(avg_ratio, 1)
    }


def main():
    print("Starting MOC Validation across 30 Tickers...\n")
    client = MongoClient(MONGO_URI)
    coll = client[DB_NAME]["candles"]
    
    results = []
    
    for tier, tickers in TIERS.items():
        print(f"--- Analyzing {tier} Cap ---")
        for ticker in tickers:
            cursor = coll.find({"ticker": ticker, "timeframe": "i1"})
            data = list(cursor)
            if not data:
                print(f"{ticker}: No data in MongoDB.")
                continue
                
            df = pd.DataFrame(data)
            
            # Format dates to match how cvd/calculator handles it
            if "date" in df.columns:
                if pd.api.types.is_string_dtype(df["date"]):
                    df["date"] = pd.to_datetime(
                        df["date"].astype(str).str.replace(r'\s*(AM|PM)$', '', regex=True),
                        format="%m/%d/%Y %H:%M",
                        errors="coerce"
                    )
                else:
                    df["date"] = pd.to_datetime(df["date"], errors="coerce")
                df = df.set_index("date").sort_index()
                
            stats = analyze_moc(ticker, df)
            if stats:
                stats["Tier"] = tier
                results.append(stats)
                print(f"{ticker:4s} | Rate: {stats['Detection Rate (%)']:5.1f}% | Avg Ratio: {stats['Avg Vol/Median Ratio']:6.1f}x")
                
    # Create Markdown Report
    res_df = pd.DataFrame(results)
    
    report = "# MOC (Closing Auction) Detection Validation Report\n\n"
    report += "This report analyzes whether the fixed 10x median threshold for detecting the closing auction is appropriate across Large, Mid, and Small Cap stocks.\n\n"
    
    report += "## Summary by Tier\n"
    summary = res_df.groupby("Tier").agg(
        avg_detection_rate=("Detection Rate (%)", "mean"),
        avg_vol_ratio=("Avg Vol/Median Ratio", "mean")
    ).round(1).reset_index()
    # Sort to order: Large, Mid, Small
    summary["Tier"] = pd.Categorical(summary["Tier"], ["Large", "Mid", "Small"])
    summary = summary.sort_values("Tier")
    report += summary.to_markdown(index=False) + "\n\n"
    
    report += "## Detailed Results\n"
    res_df["Tier"] = pd.Categorical(res_df["Tier"], ["Large", "Mid", "Small"])
    res_df = res_df.sort_values(["Tier", "Detection Rate (%)"], ascending=[True, False])
    report += res_df.drop(columns=["Tier"]).to_markdown(index=False) + "\n\n"
    
    report += "## Conclusion & Proposed Solution\n"
    report += "Based on the data, if small caps consistently fail to reach the 10x threshold, we should propose an adaptive threshold (e.g. median-based scaling) or an absolute volume fallback for lower liquidity tickers.\n"
    
    report_path = os.path.join(os.path.dirname(__file__), "..", "Claude", "MOC_Validation_Report.md")
    with open(report_path, "w") as f:
        f.write(report)
        
    print(f"\nReport generated at: {report_path}")

if __name__ == "__main__":
    main()
