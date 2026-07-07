"""
ibkr/level2_collector.py - Phase 4: Level 2 Order Book Collector
Connects to IB Gateway via ib_async, subscribes to reqMktDepth, 
and batch-inserts top 10 levels of bids and asks to MongoDB.
"""

import asyncio
import argparse
import logging
import time
from ib_async import IB, Stock, Future
from pymongo import MongoClient

# Configure MongoDB connection
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "trading_cvd"
COLLECTION_NAME = "level2_snapshots"

# Batch settings
BATCH_INTERVAL = 0.1  # 100ms snapshots

async def collect_level2(ticker: str = "NVDA", port: int = 7497, client_id: int = 5):
    """Connect to IB, subscribe to L2, and save to DB."""
    
    # DB Setup
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    col = db[COLLECTION_NAME]
    
    # Ensure index on timestamp for fast querying
    col.create_index("timestamp")
    
    ib = IB()
    
    logging.info(f"Connecting to IB Gateway at 127.0.0.1:{port} (clientId={client_id})...")
    try:
        await ib.connectAsync("127.0.0.1", port, clientId=client_id)
    except Exception as e:
        logging.error(f"Connection failed: {e}")
        return

    logging.info(f"Connected. Server version: {ib.client.serverVersion()}")

    # For testing, we use Stock.
    contract = Stock(ticker.upper(), "SMART", "USD")
    
    try:
        [qualified] = await ib.qualifyContractsAsync(contract)
        logging.info(f"Contract qualified: {qualified}")
    except Exception as e:
        logging.error(f"Contract qualification failed: {e}")
        ib.disconnect()
        return

    # Request Market Depth (Level 2)
    # Note: numRows=10 for standard L2. isSmartDepth=True is required for SMART routed Level 2.
    ticker_data = ib.reqMktDepth(qualified, numRows=10, isSmartDepth=True)
    logging.info(f"Subscribed to Market Depth for {ticker.upper()} (SMART)")

    batch = []
    last_batch_time = time.time()
    first_update = False

    def onMktDepth(t):
        nonlocal last_batch_time, batch, first_update
        
        if not first_update:
            logging.info(f"✅ Received FIRST L2 update! Data is flowing.")
            first_update = True
            
        current_time = time.time()
        
        # We take a snapshot only every BATCH_INTERVAL (e.g. 100ms) to avoid DB spam
        if current_time - last_batch_time >= BATCH_INTERVAL:
            
            bids = [{"price": b.price, "size": float(b.size)} for b in t.domBids]
            asks = [{"price": a.price, "size": float(a.size)} for a in t.domAsks]
            
            if not bids and not asks:
                return

            snapshot = {
                "timestamp": current_time,
                "ticker": ticker.upper(),
                "bids": bids,
                "asks": asks
            }
            batch.append(snapshot)
            last_batch_time = current_time
            
            # Flush batch to MongoDB if it reaches 100 items (10 seconds)
            if len(batch) >= 100:
                col.insert_many(batch)
                logging.info(f"Inserted {len(batch)} L2 snapshots to DB.")
                batch.clear()

    ticker_data.updateEvent += onMktDepth

    logging.info("Listening for L2 updates... Press Ctrl+C to stop.")
    
    try:
        # Keep the script running forever
        last_print = time.time()
        while True:
            await asyncio.sleep(1)
            now = time.time()
            if now - last_print > 30.0:
                logging.info(f"[Heartbeat] Still listening... (first_update={first_update}, current_batch_size={len(batch)})")
                last_print = now
                
            # We can also handle periodic flushing here if needed
            if len(batch) > 0 and now - last_batch_time > 5.0:
                col.insert_many(batch)
                logging.info(f"Flushed {len(batch)} L2 snapshots to DB (idle).")
                batch.clear()
                
    except asyncio.CancelledError:
        logging.info("Collector shutting down...")
    finally:
        if batch:
            col.insert_many(batch)
        ib.cancelMktDepth(ticker_data)
        ib.disconnect()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="IBKR Level 2 Collector")
    parser.add_argument("--ticker", default="NVDA", help="Stock ticker (default: NVDA)")
    parser.add_argument("--port", type=int, default=7497, help="Gateway port (7497 paper)")
    args = parser.parse_args()

    try:
        asyncio.run(collect_level2(args.ticker, args.port))
    except KeyboardInterrupt:
        print("Interrupted by user.")

if __name__ == "__main__":
    main()
