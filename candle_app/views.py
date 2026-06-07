from django.shortcuts import render
from django.http import HttpResponse
from .models import HistoricalData
from datetime import datetime, date, timedelta
import pytz


def home(request):
    return render(request, 'candlestick_home.html') 



def real_time_chart(request):
    # Define EST timezone
    est = pytz.timezone('US/Eastern')

    # Get current time in EST
    now_est = datetime.now(est)

    # Calculate the start and end of yesterday in EST
    yesterday_start = (now_est - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_end = (now_est - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)

    # Convert to UTC for querying the database
    utc = pytz.UTC
    yesterday_start_utc = yesterday_start.astimezone(utc)
    yesterday_end_utc = yesterday_end.astimezone(utc)

    print(f"Query Range in EST: {yesterday_start} to {yesterday_end}")
    print(f"Query Range in UTC: {yesterday_start_utc} to {yesterday_end_utc}")

    # Query the database for records in the UTC range
    data = HistoricalData.objects.filter(
        ticker='NVDA',
        hour__range=(yesterday_start_utc, yesterday_end_utc)
    ).order_by('hour')

    # Transform the query results into D3.js format
    chart_data = [
        {
            "x": entry.hour.astimezone(est).isoformat(),  # Convert UTC to EST for display
            "o": float(entry.open),
            "h": float(entry.high),
            "l": float(entry.low),
            "c": float(entry.close)
        }
        for entry in data
    ]

    is_empty = len(chart_data) == 0
    return render(request, 'real_time.html', {'chart_data': chart_data, 'is_empty': is_empty})