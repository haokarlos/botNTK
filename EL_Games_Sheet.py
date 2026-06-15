from datetime import UTC, datetime
import json
import os
from pathlib import Path
from urllib.parse import urljoin

import gspread
import psycopg
import requests
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from db_utils import save_snapshot_to_postgres

GOOGLE_SCOPES = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]
DATABASE_URL = os.getenv('DATABASE_URL')


def absolutize_url(base_url, maybe_url):
    if not maybe_url:
        return None
    return urljoin(base_url, maybe_url)


def extract_nutaku_entries(soup, base_url):
    entries = []
    seen_titles = set()

    for title_node in soup.find_all('span', class_='general-title'):
        title = title_node.get_text(strip=True)
        if not title:
            continue
        if title in seen_titles:
            continue

        anchor = title_node.find_parent('a', href=True)
        if anchor is None:
            parent = title_node.parent
            if parent:
                anchor = parent.find('a', href=True)

        entries.append(
            {
                'title': title,
                'url': absolutize_url(base_url, anchor.get('href')) if anchor else None,
            }
        )
        seen_titles.add(title)

    return entries


def get_nutaku_top_game_names(nutaku_url):
    response = requests.get(nutaku_url, timeout=30)
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        return extract_nutaku_entries(soup, nutaku_url)

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

    def clean_erolabs_title(raw_title):
        blocked_lines = {'play', 'download', 'play now', 'online', 'web'}
        for line in raw_title.splitlines():
            candidate = ' '.join(line.split()).strip()
            if not candidate:
                continue
            if candidate.casefold() in blocked_lines:
                continue
            return candidate
        return ' '.join(raw_title.split()).strip()

    try:
        driver.get(ero_labs_url)

        wait = WebDriverWait(driver, 20)
        wait.until(
            lambda d: d.find_elements(By.CSS_SELECTOR, '.home__topGameName, a[href*="game.html?id="]')
        )

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        entries = []
        seen_urls = set()
        seen_titles = set()

        for anchor in soup.select('a.home__topGame[href*="game.html?id="], a[href*="game.html?id="]'):
            href = anchor.get('href')
            if not href or href in seen_urls:
                continue

            title_node = (
                anchor.select_one('.home__topGameH4')
                or anchor.select_one('.home__topGameName h4')
                or anchor.select_one('.home__topGameName')
                or anchor.select_one('[class*="GameName"]')
                or anchor.select_one('[class*="gameName"]')
                or anchor.select_one('h4')
                or anchor.select_one('h3')
            )
            title = clean_erolabs_title(title_node.get_text('\n', strip=True)) if title_node else ''

            if not title:
                title = clean_erolabs_title(anchor.get_text('\n', strip=True))

            if not title:
                alt = anchor.get('aria-label') or anchor.get('title')
                title = clean_erolabs_title(alt or '')

            normalized_title = title.casefold()
            if not title or normalized_title in seen_titles:
                continue

            entries.append({'title': title, 'url': absolutize_url(ero_labs_url, href)})
            seen_urls.add(href)
            seen_titles.add(normalized_title)

        if len(entries) < 5:
            raise RuntimeError(
                f'EroLabs devolvio muy pocas entradas ({len(entries)}). Probable cambio de DOM/selectors.'
            )

        return entries
    finally:
        driver.quit()


def extract_titles(game_entries):
    titles = []
    for entry in game_entries:
        if isinstance(entry, dict):
            title = (entry.get('title') or '').strip()
            if title:
                titles.append(title)
        else:
            title = str(entry).strip()
            if title:
                titles.append(title)
    return titles


def write_to_google_sheets(sheet, game_names, sheet_index):
    today = datetime.now().strftime('%Y-%m-%d')
    worksheet = sheet.get_worksheet(sheet_index)
    data = [today] + extract_titles(game_names)
    worksheet.append_row(data)
    print(f'Los resultados se han guardado en la hoja {sheet_index} de Google Sheets')


BASE_DIR = Path(__file__).resolve().parent
GOOGLE_CREDENTIALS_PATH = BASE_DIR / 'topgamesntk-bef66ad4669f.json'
SHEET_NAME = os.getenv('GOOGLE_SHEET_NAME', 'EF Nutaku top games bot')

STOREFRONTS = [
    {
        'slug': 'nutaku-browser-ranking',
        'url': 'https://www.nutaku.net/games/genre/tag/pc-browser/os/dev/pub/lang/filter/price/features/status/ranking/',
        'sheet_index': 0,
        'fetcher': get_nutaku_top_game_names,
    },
    {
        'slug': 'nutaku-mobile-ranking',
        'url': 'https://www.nutaku.net/games/genre/tag/mobile/os/dev/pub/lang/filter/price/features/status/ranking/',
        'sheet_index': 1,
        'fetcher': get_nutaku_top_game_names,
    },
    {
        'slug': 'nutaku-all-games',
        'url': 'https://www.nutaku.net/games/',
        'sheet_index': 2,
        'fetcher': get_nutaku_top_game_names,
    },
    {
        'slug': 'erolabs-home-ranking',
        'url': 'https://www.ero-labs.com/en/',
        'sheet_index': 3,
        'fetcher': get_ero_labs_top_game_names,
        'limit': 19,
    },
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


def open_google_sheet():
    try:
        credentials = load_google_credentials()
    except FileNotFoundError:
        print('No hay credenciales de Google configuradas. Se omite Google Sheets.')
        return None

    gc = gspread.authorize(credentials)
    return gc.open(SHEET_NAME)


def open_database_connection():
    if not DATABASE_URL:
        print('No hay DATABASE_URL configurada. Se omite PostgreSQL.')
        return None

    return psycopg.connect(DATABASE_URL, options='-c statement_timeout=0')


def main():
    sheet = open_google_sheet()
    conn = open_database_connection()

    try:
        for storefront in STOREFRONTS:
            fetcher = storefront['fetcher']
            source_url = storefront['url']
            game_names = fetcher(source_url) if fetcher is get_nutaku_top_game_names else fetcher()

            limit = storefront.get('limit')
            if limit:
                game_names = game_names[:limit]

            if conn is not None:
                try:
                    save_snapshot_to_postgres(conn, storefront['slug'], source_url, game_names)
                    conn.commit()
                    print(f'Snapshot guardado en PostgreSQL para {storefront["slug"]}')
                except Exception:
                    conn.rollback()
                    raise

            if sheet is not None:
                write_to_google_sheets(sheet, game_names, storefront['sheet_index'])
    finally:
        if conn is not None:
            conn.close()


if __name__ == '__main__':
    main()
