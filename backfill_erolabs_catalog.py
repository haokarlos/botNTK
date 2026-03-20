import os
import re
from urllib.parse import urljoin

import psycopg
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from db_utils import get_storefront_id, normalize_title


DATABASE_URL = os.getenv("DATABASE_URL")
EROLABS_CATALOG_URL = "https://www.ero-labs.com/en/games.html"
EROLABS_STOREFRONT_SLUG = "erolabs-home-ranking"
HEADLESS = os.getenv("EROLABS_BACKFILL_HEADLESS", "1") not in {"0", "false", "False"}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL es obligatorio para backfill de EroLabs.")
    return psycopg.connect(DATABASE_URL, options="-c statement_timeout=0")


def get_driver():
    chrome_options = Options()
    if HEADLESS:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(f"--user-agent={USER_AGENT}")
    return webdriver.Chrome(options=chrome_options)


def simplify_title(value):
    normalized = normalize_title(value or "")
    normalized = re.sub(r"\([^)]*\)", " ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", "", normalized)
    return normalized


def find_card_container(anchor):
    current = anchor
    while current is not None:
        classes = current.get("class") or []
        class_blob = " ".join(classes)
        if any(token in class_blob for token in ("game_listBox", "game_card", "game_box", "game-item")):
            return current
        current = current.parent
    return anchor.parent


def infer_title_from_card(card, anchor):
    title_node = (
        (card.select_one(".game_name") if card else None)
        or (card.select_one(".game_title") if card else None)
        or (card.select_one('[class*="game_name"]') if card else None)
        or (card.select_one('[class*="game_title"]') if card else None)
        or (card.select_one("h3") if card else None)
        or (card.select_one("h4") if card else None)
    )
    if title_node:
        title = " ".join(title_node.get_text(" ", strip=True).split())
        if title and len(title) <= 120:
            return title

    image = card.select_one("img[alt]") if card else None
    if image and image.get("alt"):
        alt_title = " ".join(image.get("alt", "").split())
        if alt_title and len(alt_title) <= 120:
            return alt_title

    text_blob = card.get_text("\n", strip=True) if card else anchor.get_text("\n", strip=True)
    for line in text_blob.splitlines():
        candidate = " ".join(line.split()).lstrip("- ").strip()
        if not candidate:
            continue
        if len(candidate) <= 120:
            return candidate

    return " ".join(anchor.get_text(" ", strip=True).split())


def scrape_catalog_entries():
    driver = get_driver()
    try:
        driver.get(EROLABS_CATALOG_URL)
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href*="game.html?id="]')))

        previous_height = 0
        stable_rounds = 0
        while stable_rounds < 3:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
            current_height = driver.execute_script("return document.body.scrollHeight")
            if current_height == previous_height:
                stable_rounds += 1
            else:
                stable_rounds = 0
                previous_height = current_height

        soup = BeautifulSoup(driver.page_source, "html.parser")
    finally:
        driver.quit()

    entries = []
    seen = set()

    for anchor in soup.select('a[href*="game.html?id="]'):
        url = urljoin(EROLABS_CATALOG_URL, anchor.get("href"))
        if not url:
            continue

        card = find_card_container(anchor)
        title = infer_title_from_card(card, anchor)
        if not title or not url:
            continue

        key = normalize_title(title)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"title": title, "url": url})

    return entries


def load_aliases(conn, storefront_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            select id, title, title_normalized, url
            from game_aliases
            where storefront_id = %s
            """,
            (storefront_id,),
        )
        return cur.fetchall()


def backfill_alias_urls(conn, entries):
    storefront_id = get_storefront_id(conn, EROLABS_STOREFRONT_SLUG)
    aliases = load_aliases(conn, storefront_id)

    exact_lookup = {}
    simplified_lookup = {}
    for alias_id, title, title_normalized, url in aliases:
        exact_lookup.setdefault(title_normalized, []).append((alias_id, title, url))
        simplified_lookup.setdefault(simplify_title(title), []).append((alias_id, title, url))

    updated = 0
    newly_populated = 0
    unmatched = []

    with conn.cursor() as cur:
        for entry in entries:
            normalized_title = normalize_title(entry["title"])
            matched_aliases = exact_lookup.get(normalized_title)
            if not matched_aliases:
                matched_aliases = simplified_lookup.get(simplify_title(entry["title"]))

            if not matched_aliases:
                unmatched.append(entry["title"])
                continue

            for alias_id, alias_title, existing_url in matched_aliases:
                cur.execute(
                    """
                    update game_aliases
                    set url = coalesce(url, %s),
                        updated_at = now()
                    where id = %s
                    returning url
                    """,
                    (entry["url"], alias_id),
                )
                row = cur.fetchone()
                updated += 1
                if not existing_url and row and row[0]:
                    newly_populated += 1

        conn.commit()

    return updated, newly_populated, unmatched


def main():
    entries = scrape_catalog_entries()
    print(f"EroLabs catalog entries scraped: {len(entries)}", flush=True)
    if len(entries) <= 5:
        print(
            "Very few catalog entries were detected. EroLabs may be serving a JS shell to headless Chrome. "
            "Try again with EROLABS_BACKFILL_HEADLESS=0.",
            flush=True,
        )

    with get_conn() as conn:
        updated, newly_populated, unmatched = backfill_alias_urls(conn, entries)
        print(
            f"EroLabs aliases matched: {updated}, aliases with URL now populated: {newly_populated}",
            flush=True,
        )
        if unmatched:
            print("Sample unmatched titles:", flush=True)
            for title in unmatched[:20]:
                print(f"  - {title}", flush=True)


if __name__ == "__main__":
    main()
