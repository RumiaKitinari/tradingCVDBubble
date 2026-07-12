import pandas as pd
import numpy as np
from pymongo import MongoClient
import time
import math

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "trading_cvd"
COLLECTION_NAME = "level2_snapshots"

def get_l2_collection():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    return db[COLLECTION_NAME]

def calculate_center_of_gravity(orders):
    """
    Calculates the volume-weighted average price (Center of Gravity)
    for a list of orders [{"price": float, "size": float}, ...].
    """
    if not orders:
        return np.nan
    total_volume = sum(o['size'] for o in orders)
    if total_volume == 0:
        return np.nan
    weighted_price = sum(o['price'] * o['size'] for o in orders)
    return weighted_price / total_volume

def calculate_obi(bids, asks):
    """
    Order Book Imbalance (OBI) Ratio
    (Total Bid Volume - Total Ask Volume) / (Total Bid Volume + Total Ask Volume)
    """
    total_bid_vol = sum(b['size'] for b in bids)
    total_ask_vol = sum(a['size'] for a in asks)
    total_vol = total_bid_vol + total_ask_vol
    if total_vol == 0:
        return 0.0
    return (total_bid_vol - total_ask_vol) / total_vol

def calculate_weighted_liquidity(orders, decay=3.0):
    """
    Calculates weighted liquidity using exponential decay.
    orders: list of dicts sorted from best to worst.
    """
    weighted_vol = 0.0
    for level, o in enumerate(orders):
        weighted_vol += o['size'] * math.exp(-level / decay)
    return weighted_vol

def fetch_and_aggregate_l2_data(ticker, df_candles, max_candles=300):
    """
    Matches L2 snapshots to the provided candlestick dataframe.
    Returns the dataframe enriched with L2 metrics and the Z-matrix for the heatmap.
    df_candles index should be a datetime index.
    """
    col = get_l2_collection()
    
    # We only process the last `max_candles` to save performance
    if len(df_candles) > max_candles:
        df_subset = df_candles.iloc[-max_candles:].copy()
    else:
        df_subset = df_candles.copy()
        
    df_candles = df_candles.copy()
    for c in ['obi', 'bid_weighted_liq', 'ask_weighted_liq', 'bid_cog', 'ask_cog']:
        df_candles[c] = np.nan
        
    if df_subset.empty:
        return df_candles, None, None
        
    start_ts = df_subset.index[0].timestamp()
    end_ts = df_subset.index[-1].timestamp()
    
    # Fetch L2 snapshots in the time range + a little buffer (e.g. 1 minute)
    buffer = 60.0
    cursor = col.find({
        "ticker": ticker.upper(),
        "timestamp": {"$gte": start_ts - buffer, "$lte": end_ts + buffer}
    }).sort("timestamp", 1)
    
    snapshots = list(cursor)
    
    if not snapshots:
        return df_candles, None, None

    # Pre-process snapshots into a DataFrame for efficient mapping
    snap_df = pd.DataFrame({
        'timestamp': [s['timestamp'] for s in snapshots],
        'bids': [s['bids'] for s in snapshots],
        'asks': [s['asks'] for s in snapshots]
    })
    
    # For each candle, find the closest snapshot AT OR BEFORE the candle's timestamp.
    # We use merge_asof for efficient matching.
    # We need candle timestamps as a column.
    df_subset['candle_ts'] = df_subset.index.map(lambda x: x.timestamp())
    
    # Ensure snap_df is sorted by timestamp
    snap_df = snap_df.sort_values('timestamp')
    
    # Merge asof
    merged = pd.merge_asof(
        df_subset.reset_index(), 
        snap_df, 
        left_on='candle_ts', 
        right_on='timestamp', 
        direction='backward'
    )
    
    # Calculate metrics row by row
    obi_list = []
    bid_wl_list = []
    ask_wl_list = []
    bid_cog_list = []
    ask_cog_list = []
    
    # For Heatmap
    # We will build a unified price scale (y_levels) across all snapshots in the subset
    min_price = float('inf')
    max_price = float('-inf')
    
    for _, row in merged.iterrows():
        bids = row['bids'] if isinstance(row['bids'], list) else []
        asks = row['asks'] if isinstance(row['asks'], list) else []
        
        obi_list.append(calculate_obi(bids, asks))
        bid_wl_list.append(calculate_weighted_liquidity(bids))
        ask_wl_list.append(calculate_weighted_liquidity(asks))
        bid_cog_list.append(calculate_center_of_gravity(bids))
        ask_cog_list.append(calculate_center_of_gravity(asks))
        
    # Use iloc for assignment to avoid "cannot reindex from a duplicate axis" errors
    # which happen when df_candles has duplicate timestamps (e.g. in Raw Ticks data).
    N = len(obi_list)
    df_candles.iloc[-N:, df_candles.columns.get_loc('obi')] = obi_list
    df_candles.iloc[-N:, df_candles.columns.get_loc('bid_weighted_liq')] = bid_wl_list
    df_candles.iloc[-N:, df_candles.columns.get_loc('ask_weighted_liq')] = ask_wl_list
    df_candles.iloc[-N:, df_candles.columns.get_loc('bid_cog')] = bid_cog_list
    df_candles.iloc[-N:, df_candles.columns.get_loc('ask_cog')] = ask_cog_list
    
    # Build Heatmap Z-Matrix
    all_prices = set()
    for _, row in merged.iterrows():
        bids = row['bids'] if isinstance(row['bids'], list) else []
        asks = row['asks'] if isinstance(row['asks'], list) else []
        for b in bids:
            all_prices.add(b['price'])
        for a in asks:
            all_prices.add(a['price'])
            
    y_levels = None
    z_matrix = None
            
    if all_prices:
        y_levels = sorted(list(all_prices))
        price_to_idx = {p: i for i, p in enumerate(y_levels)}
        
        # z_matrix shape: (len(y_levels), len(df_subset))
        z_matrix = np.zeros((len(y_levels), len(df_subset)))
        
        for col_idx, (_, row) in enumerate(merged.iterrows()):
            bids = row['bids'] if isinstance(row['bids'], list) else []
            asks = row['asks'] if isinstance(row['asks'], list) else []
            for b in bids:
                row_idx = price_to_idx[b['price']]
                z_matrix[row_idx, col_idx] += b['size']
            for a in asks:
                row_idx = price_to_idx[a['price']]
                z_matrix[row_idx, col_idx] += a['size']
                
    return df_candles, y_levels, z_matrix
