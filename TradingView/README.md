# TradingView
TradingView Data Automation Repository

This repository contains two Jupyter notebooks that explore different methods for automating data downloads from the TradingView website. The project includes a trial using Selenium, which faced security challenges, and a successful approach using PyAutoGUI, customized for specific intervals on a local machine.

Project Overview
This project involves two approaches for automating data downloads from TradingView:

Using Selenium: This approach was attempted to automate the login and data retrieval process from TradingView. However, it was unsuccessful due to the high-security measures on the website, which made it impossible to manipulate cookies and bypass the login process using Selenium.

Using PyAutoGUI: This approach successfully automates the data download process by simulating user interactions on the TradingView website. The script is highly customized to the specific environment in which it was developed, and it is set to download data at regular intervals as per the user's requirements.

Files in This Repository
1. tradingview_selenium_attempt.ipynb
  Description:
    This notebook documents the attempt to use Selenium for automating data downloads from TradingView. Due to the website's robust security measures, such as preventing cookie         
    manipulation, this approach was ultimately unsuccessful.
  Key Features:
    Demonstrates the challenges of using Selenium on websites with strong security.
    Includes the steps and code used in the attempt.
2. tradingview_pyautogui.ipynb
  Description:
    This notebook provides a working solution using PyAutoGUI to automate the process of downloading data from TradingView. The script is customized to the developer's local machine 
    and is designed to run at regular intervals.
  Key Features:
    Successfully automates data downloads by simulating user actions.
    Customizable intervals for data retrieval.
    Highly tailored to the developer's environment.

   
Installation Instructions
    Prerequisites
    Python 3.8 or above.
    Jupyter Notebook or JupyterLab installed.
    Required Python packages (install via pip):
    selenium
    pyautogui
    pandas
