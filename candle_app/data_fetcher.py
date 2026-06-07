import datetime
import pytz
import yfinance as yf
from decimal import Decimal
from .models import HistoricalData
import math

def fetch_and_store_nvidia_data():
    print("Data fetcher is running...")
    # Define the ticker symbol
    ticker = 'NVDA'

    # Define timezones
    est = pytz.timezone('US/Eastern')
    utc = pytz.UTC

    # Calculate "yesterday" in EST
    now_est = datetime.datetime.now(est)
    yesterday_start_est = (now_est - datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_end_est = (now_est - datetime.timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)

    # Convert "yesterday" EST times to UTC for querying yfinance
    start_date_utc = yesterday_start_est.astimezone(utc)
    end_date_utc = yesterday_end_est.astimezone(utc)

    print(f"Fetching data for EST range: {yesterday_start_est} to {yesterday_end_est}")
    print(f"Converted to UTC range: {start_date_utc} to {end_date_utc}")

    # Fetch hourly data for NVIDIA using yfinance
    stock_data = yf.download(ticker, start=start_date_utc, end=end_date_utc, interval='1h')

    # Check if data exists
    if stock_data.empty:
        print(f"No data found for {ticker} on {yesterday_start_est.date()} (EST).")
        return

    # Clear old data for NVDA if needed
    HistoricalData.objects.filter(ticker=ticker, date=yesterday_start_est.date()).delete()

    # Iterate over the fetched data to store it in the database
    for index, row in stock_data.iterrows():
        try:
            # Skip rows with NaN values
            if math.isnan(row['Open']) or math.isnan(row['High']) or math.isnan(row['Low']) or math.isnan(row['Close']):
                print(f"Skipping row with NaN values at {index}.")
                continue

            # Convert timestamp to Python datetime in UTC
            hour_utc = index.to_pydatetime().replace(tzinfo=utc)

            # Extract scalar values and convert to Decimal
            open_price = Decimal(row['Open'])
            high_price = Decimal(row['High'])
            low_price = Decimal(row['Low'])
            close_price = Decimal(row['Close'])

            # Insert into the database
            HistoricalData.objects.create(
                ticker=ticker,
                hour=hour_utc,  # Store hour in UTC
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                date=hour_utc.astimezone(est).date()  # Convert UTC hour to EST and get the date
            )
            print(f"Saved entry for {hour_utc} (UTC) / {hour_utc.astimezone(est)} (EST).")
        except Exception as e:
            print(f"Error processing row {index}: {e}")

    print(f"NVIDIA data for {yesterday_start_est.date()} (EST) fetched and stored successfully.")
