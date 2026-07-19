import pandas as pd
from pymongo import MongoClient
from datetime import datetime
import os

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "trading_cvd"
COLLECTION_NAME = "level2_snapshots"

def insert_mock_data_to_mongo(csv_path="mock_level2_nvda.csv"):
    if not os.path.exists(csv_path):
        print(f"Error: {csv_path} not found. Please run mock_level2_generator.py first.")
        return
        
    print(f"Loading {csv_path}...")
    df = pd.read_csv(csv_path)
    
    # Connect to MongoDB
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    col = db[COLLECTION_NAME]
    
    print(f"Connected to MongoDB: {DB_NAME}.{COLLECTION_NAME}")
    
    # Optional: Clear existing mock data for this symbol to avoid duplicates during testing
    symbol = df['symbol'].iloc[0]
    col.delete_many({"ticker": symbol})
    print(f"Cleared existing data for {symbol} in MongoDB.")
    
    documents = []
    for _, row in df.iterrows():
        # Convert string timestamp to float epoch
        dt = datetime.strptime(row['timestamp'], "%Y-%m-%d %H:%M:%S.%f")
        ts = dt.timestamp()
        
        # Build bids list
        bids = []
        for i in range(1, 6):
            bids.append({
                "price": row[f"bid_price_{i}"],
                "size": row[f"bid_size_{i}"]
            })
            
        # Build asks list
        asks = []
        for i in range(1, 6):
            asks.append({
                "price": row[f"ask_price_{i}"],
                "size": row[f"ask_size_{i}"]
            })
            
        doc = {
            "ticker": row['symbol'],
            "timestamp": ts,
            "bids": bids,
            "asks": asks,
            "mid_price": row['mid_price']
        }
        documents.append(doc)
        
    print(f"Inserting {len(documents)} documents into MongoDB...")
    # Insert in batches for performance
    batch_size = 1000
    for i in range(0, len(documents), batch_size):
        col.insert_many(documents[i:i+batch_size])
        
    print("Insertion complete! You can now test the level2_webapp.")

if __name__ == "__main__":
    insert_mock_data_to_mongo()
