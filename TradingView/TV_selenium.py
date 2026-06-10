from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options

import os, time, pickle 
from datetime import datetime
from admin import DOWNLOAD_DIR, USERNAME, EMAIL, PASSWORD, COOKIES_FILE

TRADING_VIEW_SCREENER = "https://www.tradingview.com/screener/"

################################################
# --------- PART 1: Helper Functions --------- #
################################################

# Load cookies if they exist
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

# Log into the account
# NOTE: Future errors possible if TradingView updates their UI and thus HTML for the fields
# TIP: Tutorial to finding HTML: https://www.youtube.com/watch?v=j-cqGs75zo4
def login(driver):
    try:
        # Locate the user menu button using its class name and click it
        print("Locating user menu button...")
        user_menu_button = WebDriverWait(driver, 40).until(
            EC.element_to_be_clickable((By.CLASS_NAME, "js-header-user-menu-button"))
        )
        user_menu_button.click()
        print("User menu button clicked.")

        # Locate the Sign In in drop down of user menu
        print("Locating sign in button...")
        sign_in_button = WebDriverWait(driver, 40).until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'js-main-menu-dropdown-link-title') and contains(text(), 'Sign')]"))
        )
        sign_in_button.click()
        print("Sign in button clicked.")

        # Locate the email button now
        print("Locating email button...")
        email_button = WebDriverWait(driver, 40).until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'ellipsisContainer') and contains(text(),'Email')]"))
        )
        email_button.click()
        print("Email button clicked.")

        # Find and fill the username/email field
        print("Locating username/email field...")
        username_input = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//input[@name='id_username']"))
        )
        username_input.send_keys(EMAIL)

        # Find and fill the password field
        print("Locating password field...")
        username_input = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//input[@name='id_password']"))
        )
        username_input.send_keys(PASSWORD)
        print("Password entered.")
        
        # Find and click the login button
        print("Locating login button...")
        submit_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'submitButton') and @data-overflow-tooltip-text='Sign in']"))
        )
        submit_button.click()
        print("Logged in successfully.")
        
    except Exception as e:
        print(f"An error occurred during login: {e}")
        raise SystemExit(1)


################################################################################
# --------- PART 2: Instantiating the Driver & Checking Login Status --------- #
################################################################################

# Set up the WebDriver
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
driver.get(TRADING_VIEW_SCREENER) 

try:
    # Wait for the page to load completely
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//body")))

    # Locate the button (user profile dropdown) using its attributes
    button = WebDriverWait(driver, 90).until(
        EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Open user menu' and contains(@class, 'tv-header__user-menu-button')]"))
    )
    button.click()
    print("Button clicked successfully.")
except Exception as e:
    print(f"An error occurred: {e}")
    raise SystemExit(1)

# Setting up preferences for Chrome options
options = Options()
options.add_experimental_option("detach", True)

prefs = {"download.default_directory": DOWNLOAD_DIR}
options.add_experimental_option("prefs", prefs)

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)


###############################################
# --------- PART 3: Account Sign-in --------- #
###############################################

# Open the login page directly
driver.get(TRADING_VIEW_SCREENER)  
driver.maximize_window()

# Try loading cookies and refreshing
try:
    load_cookies(driver)
    driver.refresh()
    print("Cookies loaded and page refreshed.")
except Exception as e:
    print(f"An error occurred while loading cookies: {e}")
    
# Attempt to login (checks "if not already logged in" by comparing with username)
try:
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, f"//span[contains(@class, 'username') and text()='{USERNAME}']"))
    )
    print("Already logged in using cookies.")
except:
    print("Not logged in, proceeding to login.")
    login(driver)
    # Wait to ensure login completes and save cookies
    time.sleep(10)
    save_cookies(driver)
    print("Cookies saved.")
    raise SystemExit(1)


#######################################################
# --------- PART 4: Retrieve Data from Menu --------- #
#######################################################

driver.get(TRADING_VIEW_SCREENER)  

button = WebDriverWait(driver, 40).until(
    EC.element_to_be_clickable((By.CSS_SELECTOR, "div.menu-FZEHkLiT.button-merBkM5y.apply-common-tooltip[data-role='button'][data-name='screener-topbar-screen-title']"))
)

button.click()
