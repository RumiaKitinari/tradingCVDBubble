import os, time
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")
EMAIL    = os.getenv("FINVIZ_USERNAME")
PASSWORD = os.getenv("FINVIZ_PASSWORD")

def get_driver():
    options = webdriver.ChromeOptions()
    options.add_experimental_option("detach", True)
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def login(driver):
    wait = WebDriverWait(driver, 20)

    print("Opening FinViz login page...")
    driver.get("https://elite.finviz.com/login.ashx")
    driver.maximize_window()

    # Step 1: Click "Email" button
    print("Clicking Email button...")
    wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(@href, 'login-email')]"))).click()

    # Step 2: Fill in credentials
    print("Entering credentials...")
    wait.until(EC.presence_of_element_located((By.NAME, "email"))).send_keys(EMAIL)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)

    # Step 3: Submit
    driver.find_element(By.XPATH, "//button[@type='submit']").click()
    print("Login submitted, waiting for redirect...")
    time.sleep(3)

    if "login" not in driver.current_url:
        print(f"✅ Login successful! URL: {driver.current_url}")
        return True
    else:
        print("❌ Login failed — still on login page")
        return False

if __name__ == "__main__":
    driver = get_driver()
    login(driver)