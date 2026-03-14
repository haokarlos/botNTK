from datetime import datetime
import json
import os
from pathlib import Path

import gspread
import requests
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

GOOGLE_SCOPES = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]


def get_nutaku_top_game_names(nutaku_url):
    response = requests.get(nutaku_url, timeout=30)
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        general_titles = [title.text for title in soup.find_all('span', class_='general-title')]
        return general_titles

    print(f'Error al acceder a la página de Nutaku. Código de estado: {response.status_code}')
    return []


def get_ero_labs_top_game_names():
    ero_labs_url = 'https://www.ero-labs.com/en/'

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(options=chrome_options)

    try:
        driver.get(ero_labs_url)

        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.home__topGameName')))

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        game_name_elements = soup.select('.home__topGameName h4')
        return [element.get_text(strip=True) for element in game_name_elements]
    finally:
        driver.quit()


def write_to_google_sheets(sheet, game_names, sheet_index):
    today = datetime.now().strftime('%Y-%m-%d')
    worksheet = sheet.get_worksheet(sheet_index)
    data = [today] + game_names
    worksheet.append_row(data)
    print(f'Los resultados se han guardado en la hoja {sheet_index} de Google Sheets')


BASE_DIR = Path(__file__).resolve().parent
GOOGLE_CREDENTIALS_PATH = BASE_DIR / 'topgamesntk-bef66ad4669f.json'
SHEET_NAME = os.getenv('GOOGLE_SHEET_NAME', 'EF Nutaku top games bot')

nutaku_queries = [
    ('https://www.nutaku.net/games/genre/tag/pc-browser/os/dev/pub/lang/filter/price/features/status/ranking/', 0),
    ('https://www.nutaku.net/games/genre/tag/mobile/os/dev/pub/lang/filter/price/features/status/ranking/', 1),
    ('https://www.nutaku.net/games/', 2)
]


def load_google_credentials():
    service_account_json = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if service_account_json:
        return service_account.Credentials.from_service_account_info(
            json.loads(service_account_json),
            scopes=GOOGLE_SCOPES,
        )

    service_account_file = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE')
    if service_account_file:
        return service_account.Credentials.from_service_account_file(
            service_account_file,
            scopes=GOOGLE_SCOPES,
        )

    if GOOGLE_CREDENTIALS_PATH.exists():
        return service_account.Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_PATH,
            scopes=GOOGLE_SCOPES,
        )

    raise FileNotFoundError(
        'No se encontraron credenciales de Google. Usa GOOGLE_SERVICE_ACCOUNT_JSON, '
        'GOOGLE_SERVICE_ACCOUNT_FILE o el archivo local topgamesntk-bef66ad4669f.json.'
    )


def main():
    credentials = load_google_credentials()
    gc = gspread.authorize(credentials)
    sheet = gc.open(SHEET_NAME)

    for query_url, sheet_index in nutaku_queries:
        nutaku_top_game_names = get_nutaku_top_game_names(query_url)
        write_to_google_sheets(sheet, nutaku_top_game_names, sheet_index)

    ero_labs_top_game_names = get_ero_labs_top_game_names()
    ero_labs_top_game_names = ero_labs_top_game_names[:19]
    write_to_google_sheets(sheet, ero_labs_top_game_names, 3)


if __name__ == '__main__':
    main()
