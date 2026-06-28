import asyncio
import json
from curl_cffi import AsyncSession

import os, pickle
from admin import DOWNLOAD_DIR, USERNAME, EMAIL, PASSWORD, COOKIES_FILE

LOGIN_URL = "https://www.tradingview.com/accounts/signin/"

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
    asyncio.run(tradingview_login_secure())