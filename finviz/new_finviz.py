import requests
import csv
import io
import importlib
from . import api_keys
from pymongo import MongoClient, UpdateOne

# MongoDB connection
client = MongoClient("mongodb://localhost:27017/")
db = client["finviz_db"]
collection = db["candles"]

BASE_URL = "https://elite.finviz.com/quote_export"
session = requests.Session()


class FinvizTokenError(Exception):
    """Raised when FinViz rejects the auth token (HTTP 401/403)."""
    pass


def get_candle_data(ticker: str, timeframe: str = 'd') -> list[dict]:
    # Re-read token from disk each call so a regenerated token takes effect
    # without restarting the process.
    importlib.reload(api_keys)
    url = f"{BASE_URL}?t={ticker}&p={timeframe}&auth={api_keys.FINVIZ_AUTH_TOKEN}"
    response = session.get(url)

    # 401/403 = the token is wrong/expired → signal the caller to regenerate it.
    if response.status_code in (401, 403):
        raise FinvizTokenError(
            f"FinViz rejected token (HTTP {response.status_code}) for {ticker} ({timeframe})"
        )

    if response.status_code != 200:
        print(f"[FinViz] Error {response.status_code} for {ticker} ({timeframe})")
        return []

    # Response is CSV — parse it
    content = response.content.decode('utf-8')
    reader = csv.DictReader(io.StringIO(content))

    candles = []
    for row in reader:
        try:
            candles.append({
                'date':   row.get('Date') or row.get('date'),
                'open':   float(row.get('Open') or row.get('open')),
                'high':   float(row.get('High') or row.get('high')),
                'low':    float(row.get('Low')  or row.get('low')),
                'close':  float(row.get('Close') or row.get('close')),
                'volume': float(row.get('Volume') or row.get('volume') or 0),
            })
        except (ValueError, TypeError) as e:
            print(f"[FinViz] Skipping row (parse error): {e} | row={row}")
            continue

    print(f"[FinViz] Fetched {len(candles)} candles for {ticker} ({timeframe})")
    return candles


def test_connection(ticker: str = 'NVDA') -> bool:
    print(f"[FinViz] Testing connection with ticker={ticker}...")
    data = get_candle_data(ticker, timeframe='d')

    if data:
        print(f"[FinViz] ✅ Connection OK — sample row: {data[-1]}")
        return True
    else:
        print(f"[FinViz] ❌ Connection failed or no data returned")
        return False


def save_candles_to_mongo(ticker: str, timeframe: str, candles: list[dict]) -> int:
    """
    Save candles to MongoDB with upsert (no duplicates).
    Uses ticker + timeframe + date as unique key.

    Returns number of upserted/modified documents.
    """
    if not candles:
        print("[MongoDB] No candles to save.")
        return 0

    operations = [
        UpdateOne(
            {"ticker": ticker, "timeframe": timeframe, "date": c["date"]},
            {"$set": {**c, "ticker": ticker, "timeframe": timeframe}},
            upsert=True
        )
        for c in candles
    ]

    result = collection.bulk_write(operations)
    total = result.upserted_count + result.modified_count
    print(f"[MongoDB] Saved {total} candles for {ticker} ({timeframe})")
    return total


def fetch_and_save(ticker: str, timeframe: str = 'i1') -> list[dict]:
    """
    Fetch OHLCV from FinViz and save to MongoDB in one step.
    Returns the fetched candles.
    """
    candles = get_candle_data(ticker, timeframe)
    save_candles_to_mongo(ticker, timeframe, candles)
    return candles


# Run test when executed directly
if __name__ == '__main__':
    test_connection('NVDA')
    data = fetch_and_save('NVDA', 'i1')
    print(f"Total: {len(data)} | Last: {data[-1]}")