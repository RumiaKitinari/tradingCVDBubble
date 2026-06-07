import requests
import csv
import io
from api_keys import FINVIZ_AUTH_TOKEN

BASE_URL = "https://elite.finviz.com/quote_export"

def get_candle_data(ticker: str, timeframe: str = 'd') -> list[dict]:
    url = f"{BASE_URL}?t={ticker}&p={timeframe}&auth={FINVIZ_AUTH_TOKEN}"
    response = requests.get(url)

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


# Run test when executed directly
if __name__ == '__main__':
    test_connection('NVDA')
    data=get_candle_data('NVDA', 'i1')
    print(len(data), data[-1])