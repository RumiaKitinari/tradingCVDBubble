# finwiz

Stock Data Automation Repository (FINVIZ)

This repository contains two Jupyter notebooks designed for automating stock data retrieval using different methods. The primary goals are to explore API calls and web scraping with automated login for data extraction.

Project Overview
This project involves two distinct approaches to retrieving stock data:

* Using the Finviz API: This approach demonstrates how to interact with the Finviz API to download stock data by selecting a ticker symbol. The ticker can be changed as needed. This method was primarily used to learn about API calls and data retrieval from external sources.

* Using Selenium and BeautifulSoup: This approach automates the login process to the Finviz Elite account by manipulating cookies and uses Selenium to download data at specified intervals. This method is tailored for users who require scheduled data downloads without manual intervention.

Files in This Repository
1. api_finwiz.ipynb
Description:
This notebook demonstrates how to use the Finviz API to download stock data. You can specify the ticker symbol, and the API call will retrieve relevant stock information.

Key Features:
API interaction with Finviz.
Customizable ticker symbol for data retrieval.
Simple and efficient way to get stock data without needing a web browser.

2. download_selenium.ipynb
Description:
This notebook uses Selenium and BeautifulSoup to automate the login process to the Finviz Elite account, manage cookies, and download stock data at user-defined intervals. This method is ideal for users who need to automate data downloads without manual input.

Key Features:
Automated login to Finviz Elite account.
Cookie manipulation for seamless access.
Scheduled data downloads based on user requirements.



Installation Instructions
Prerequisites:-
  Python 3.8 or above.
  Jupyter Notebook or JupyterLab installed.
  Required Python packages (install via pip):
    selenium
    beautifulsoup4
    finvizfinance
    pandas
    requests


