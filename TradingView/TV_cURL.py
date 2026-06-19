import asyncio
import json
from curl_cffi import AsyncSession

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options

import os, pickle
from admin import DOWNLOAD_DIR, USERNAME, EMAIL, PASSWORD, COOKIES_FILE

################################################
# --------- PART 1: Helper Functions --------- #
################################################

TRADING_VIEW_URL = "https://www.tradingview.com/"
LOGIN_URL = "https://www.tradingview.com/accounts/signin/"

def load_cookies(driver):
    if os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE, 'rb') as cookiesfile:
            cookies = pickle.load(cookiesfile)
            for cookie in cookies:
                driver.add_cookie(cookie)

# Save cookies to a file
def save_cookies(driver):
    with open(COOKIES_FILE, 'wb') as cookiesfile:
        pickle.dump(driver.get_cookies(), cookiesfile)

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))

#############################################################
# --------- PART 2: TradingView Scraping via curl --------- #
#############################################################

async def tv_login_and_scrape():
    async with AsyncSession(impersonate="chrome") as s:
        # --- STEP 1: INITIALIZE SESSION STATE ---
        print("Visiting homepage to capture CSRF configurations and cookies...")
        init_response = await s.get(TRADING_VIEW_URL)
        
        if init_response.status_code != 200:
            print(f"Initialization blocked by firewall. Status: {init_response.status_code}")
            return

        # --- STEP 2: EXECUTE AUTHENTICATION PIPELINE ---
        print(f"Transmitting credentials for profile: {EMAIL}...")
        login_res = await s.post(
            LOGIN_URL, 
            data={"username": EMAIL, "password": PASSWORD},
            impersonate="chrome"
        )
        
        print(f"Server response received. Status code: {login_res.status_code}")
        
        if login_res.status_code != 200:
            print("Authentication rejected. Debug details below:")
            print(login_res.text[:500])  # Print parsing fragments to detect CAPTCHA requirements
            return

        print("Login completely successful. Persistent cookies bound to active AsyncSession.")

        # --- STEP 3: INTERACT WITH INTERNAL SCREENER PIPELINE ---
        screener_api_url = "https://scanner.tradingview.com/global/scan"
        
        screener_payload = {
            "filter": [{"left": "name", "operation": "nempty"}],
            "options": {"lang": "en"},
            "markets": ["america"],
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": ["base_currency", "logoid", "name", "close", "change", "volume"]
        }

        print("Querying backend screener matrix...")
        response = await s.post(screener_api_url, json=screener_payload)
        
        if response.status_code == 200:
            data = response.json()
            print("\n--- Market Overview Metrics ---")
            for stock in data.get("data", [])[:5]:
                print(f"Ticker: {stock['d'][2]} | Price: ${stock['d'][3]:.2f} | Volume: {stock['d'][5]}")
        else:
            print(f"Screener connection dropped. Status: {response.status_code}")

async def tradingview_login_secure():
    async with AsyncSession(impersonate="chrome") as s:
        # 1. GET request to initialize session and retrieve CSRF cookie
        resp = await s.get(LOGIN_URL)
        
        # 2. Extract the 'csrf' token from the session cookies
        # curl_cffi handles cookiejar automatically
        csrf_token = s.cookies.get("csrf")
        
        if not csrf_token:
            print("Failed to retrieve CSRF token. The site may be blocking initial access.")
            return

        # 3. Prepare authenticated headers
        # TradingView requires the CSRF token in both the cookie AND the header
        auth_headers = {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": csrf_token,
            "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/accounts/signin/"
        }

        # 4. Perform the Login
        print("Attempting authenticated login...")
        login_res = await s.post(
            LOGIN_URL,
            data={"email": EMAIL, "password": PASSWORD},
            headers=auth_headers,
            impersonate="chrome"
        )

        if login_res.status_code == 200:
            print("Login successful!")
        else:
            print(f"Login failed with status: {login_res.status_code}")
            # If it's still 403, check if you need to solve a CAPTCHA
            print(login_res.text[:200])

if __name__ == "__main__":
    asyncio.run(tv_login_and_scrape())
    # asyncio.run(tradingview_login_secure())
