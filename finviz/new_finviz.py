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

# Index the upsert key. Without it, every upsert in save_candles_to_mongo does a
# full collection scan, so re-saving ~22k bars each loop was O(n^2) (~80s); with
# the index it's ~0.7s. create_index is idempotent (no-op if it already exists).
collection.create_index(
    [("ticker", 1), ("timeframe", 1), ("date", 1)],
    unique=True, name="ticker_tf_date",
)

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


def symbol_exists(ticker: str) -> bool:
    """Cheap validity probe: True if FinViz recognizes the symbol.

    A brand-new but VALID ticker returns daily rows; an UNKNOWN symbol comes
    back HTTP 400/404 with no data. Used by the app to tell "valid ticker still
    backfilling" apart from "bad symbol" so the chart can say which one it is
    instead of spinning on "Fetching…" forever. Conservative on ambiguity —
    ONLY a definitive "not found" (400/404) returns False. Everything else
    (network hiccup, token 401/403, rate-limit 429, server 5xx) returns True so
    a real ticker is never wrongly condemned by a transient response — and the
    caller can safely cache the verdict, since a False here is always a stable
    "symbol not found", never a passing outage.
    """
    importlib.reload(api_keys)
    url = f"{BASE_URL}?t={ticker}&p=d&auth={api_keys.FINVIZ_AUTH_TOKEN}"
    try:
        r = session.get(url, timeout=8)
    except Exception:
        return True                       # transient network issue, not the symbol
    if r.status_code in (400, 404):
        return False                      # FinViz definitively doesn't know this symbol
    if r.status_code != 200:
        return True                       # 401/403/429/5xx → transient, not the symbol's fault
    # 200 but header-only (no data rows) also means "not a tradable symbol here".
    return len((r.content or b"").strip().splitlines()) > 1


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
    try:
        candles = get_candle_data(ticker, timeframe)
    except FinvizTokenError:
        print("[FinViz] Token expired. Attempting auto-renewal via finviz_curl...")
        from finviz import finviz_curl
        session = finviz_curl.login()
        new_token = finviz_curl.get_token(session)
        finviz_curl.update_api_keys(new_token)
        print("[FinViz] Token successfully auto-renewed. Retrying fetch...")
        candles = get_candle_data(ticker, timeframe)
        
    save_candles_to_mongo(ticker, timeframe, candles)
    return candles


# Run test when executed directly
if __name__ == '__main__':
    test_connection('NVDA')
    data = fetch_and_save('NVDA', 'i1')
    print(f"Total: {len(data)} | Last: {data[-1]}")