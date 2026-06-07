# # # -*- coding: utf-8 -*-
# # import numpy as np
# # import pandas as pd

# # #Data Source
# # import yfinance as yf

# # #Data viz
# # import plotly.graph_objs as go

# # #Interval required 1 minute
# # data = yf.download(tickers='UBER', period='1d', interval='1m')

# # #declare figure
# # fig = go.Figure()

# # #Candlestick
# # fig.add_trace(go.Candlestick(x=data.index,
# #                 open=data['Open'],
# #                 high=data['High'],
# #                 low=data['Low'],
# #                 close=data['Close'], name = 'market data'))

# # # Add titles
# # fig.update_layout(
# #     title='Uber live share price evolution',
# #     yaxis_title='Stock Price (USD per Shares)')

# # # X-Axes
# # fig.update_xaxes(
# #     rangeslider_visible=True,
# #     rangeselector=dict(
# #         buttons=list([
# #             dict(count=15, label="15m", step="minute", stepmode="backward"),
# #             dict(count=45, label="45m", step="minute", stepmode="backward"),
# #             dict(count=1, label="HTD", step="hour", stepmode="todate"),
# #             dict(count=3, label="3h", step="hour", stepmode="backward"),
# #             dict(step="all")
# #         ])
# #     )
# # )

# # #Show
# # fig.show()

# # candle_app/data_fetcher.py

# import datetime
# from .models import HistoricalData
# from .finviz import FinViz, Constant  # Assuming FinViz is in the same app directory

# def fetch_and_store_nvidia_data():
#     # Initialize FinViz API handler for stocks
#     finviz = FinViz(asset_type=Constant.STOCK)

#     # Calculate the date for "yesterday" to get data for the complete previous day
#     today = datetime.date.today()
#     yesterday = today - datetime.timedelta(days=1)

#     # Fetch data using FinViz class for ticker 'NVDA' with hourly time frame ('h')
#     all_volumes, all_opens, all_closes, all_dates = finviz.get_all_data(time_frame='h', ticker='NVDA')

#     # Clear old data for NVDA if needed
#     HistoricalData.objects.filter(ticker='NVDA', date=yesterday).delete()

#     # Store data for each hour of the previous day
#     for volume, open_price, close_price, date_timestamp in zip(all_volumes, all_opens, all_closes, all_dates):
#         # Convert timestamp to datetime for storing
#         date_time = datetime.datetime.fromtimestamp(date_timestamp)

#         # Ensure we only store data for "yesterday"
#         if date_time.date() == yesterday:
#             HistoricalData.objects.create(
#                 ticker='NVDA',
#                 hour=date_time,
#                 open=open_price,
#                 high=max(open_price, close_price),  # Simplified assumption for high
#                 low=min(open_price, close_price),   # Simplified assumption for low
#                 close=close_price,
#                 date=yesterday
#             )
#     print("NVIDIA data for yesterday fetched and stored successfully.")
