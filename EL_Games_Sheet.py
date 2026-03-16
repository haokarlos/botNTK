from datetime import UTC, datetime
import json
import os
from pathlib import Path

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

GOOGLE_SCOPES = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]
DATABASE_URL = os.getenv('DATABASE_URL')


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


def normalize_title(value):
    return ' '.join(value.casefold().split())


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

    return psycopg.connect(DATABASE_URL)


def get_storefront_id(conn, storefront_slug):
    with conn.cursor() as cur:
        cur.execute(
            """
            select id
            from storefronts
            where slug = %s
            """,
            (storefront_slug,),
        )
        row = cur.fetchone()

    if not row:
        raise ValueError(f'No existe el storefront {storefront_slug} en la base de datos.')

    return row[0]


def upsert_game_alias(conn, storefront_id, title):
    normalized_title = normalize_title(title)

    with conn.cursor() as cur:
        cur.execute(
            """
            with storefront_platform as (
                select s.id as storefront_id, s.platform_id
                from storefronts s
                where s.id = %s
            ),
            existing_alias as (
                select ga.id, ga.game_id, sp.platform_id
                from game_aliases ga
                join storefront_platform sp on sp.platform_id = ga.platform_id
                where ga.title_normalized = %s
                order by ga.created_at asc
                limit 1
            ),
            inserted_game as (
                insert into games (canonical_name, canonical_name_normalized)
                select %s, %s
                where not exists (select 1 from existing_alias)
                on conflict (canonical_name_normalized) do update
                    set updated_at = now()
                returning id
            ),
            resolved_game as (
                select game_id as id from existing_alias
                union all
                select id from inserted_game
                union all
                select g.id
                from games g
                where g.canonical_name_normalized = %s
                limit 1
            )
            insert into game_aliases (
                game_id,
                platform_id,
                storefront_id,
                title,
                title_normalized,
                first_seen_at,
                last_seen_at
            )
            select
                rg.id,
                sp.platform_id,
                sp.storefront_id,
                %s,
                %s,
                now(),
                now()
            from resolved_game rg
            cross join storefront_platform sp
            on conflict (storefront_id, title_normalized) do nothing
            returning id
            """,
            (
                storefront_id,
                normalized_title,
                title,
                normalized_title,
                normalized_title,
                title,
                normalized_title,
            ),
        )
        row = cur.fetchone()
        if row:
            return row[0]

        cur.execute(
            """
            update game_aliases
            set last_seen_at = now(),
                updated_at = now(),
                title = %s
            where storefront_id = %s
              and title_normalized = %s
            returning id
            """,
            (title, storefront_id, normalized_title),
        )
        row = cur.fetchone()

    if not row:
        raise ValueError(f'No se pudo crear o actualizar el alias para {title}.')

    return row[0]


def save_snapshot_to_postgres(conn, storefront_slug, source_url, game_names):
    captured_at = datetime.now(UTC)
    capture_date = captured_at.date()
    storefront_id = get_storefront_id(conn, storefront_slug)

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into ranking_snapshots (
                storefront_id,
                captured_at,
                capture_date,
                source_url,
                status,
                raw_payload
            )
            values (%s, %s, %s, %s, 'success', %s::jsonb)
            on conflict (storefront_id, capture_date) do update
                set captured_at = excluded.captured_at,
                    source_url = excluded.source_url,
                    status = excluded.status,
                    raw_payload = excluded.raw_payload
            returning id
            """,
            (
                storefront_id,
                captured_at,
                capture_date,
                source_url,
                json.dumps({'games': game_names}),
            ),
        )
        snapshot_id = cur.fetchone()[0]
        cur.execute(
            """
            delete from ranking_entries
            where snapshot_id = %s
            """,
            (snapshot_id,),
        )

    for rank, game_name in enumerate(game_names, start=1):
        game_alias_id = upsert_game_alias(conn, storefront_id, game_name)
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into ranking_entries (snapshot_id, game_alias_id, rank)
                values (%s, %s, %s)
                """,
                (snapshot_id, game_alias_id, rank),
            )

    print(f'Snapshot guardado en PostgreSQL para {storefront_slug}')


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

            if sheet is not None:
                write_to_google_sheets(sheet, game_names, storefront['sheet_index'])

            if conn is not None:
                save_snapshot_to_postgres(conn, storefront['slug'], source_url, game_names)

        if conn is not None:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()


if __name__ == '__main__':
    main()
