from datetime import UTC, datetime, timedelta
import json
import os
import re
from urllib.parse import urljoin, urlparse

import psycopg
import requests
from bs4 import BeautifulSoup


DATABASE_URL = os.getenv('DATABASE_URL')
USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
)
DEFAULT_LIMIT = int(os.getenv('METADATA_REFRESH_LIMIT', '50'))
REFRESH_DAYS = int(os.getenv('METADATA_REFRESH_DAYS', '7'))
STOREFRONT_BASE_URLS = {
    'nutaku-browser-ranking': 'https://www.nutaku.net/',
    'nutaku-mobile-ranking': 'https://www.nutaku.net/',
    'nutaku-all-games': 'https://www.nutaku.net/',
    'erolabs-home-ranking': 'https://www.ero-labs.com/en/',
}


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL es obligatorio para refrescar metadata.')
    return psycopg.connect(DATABASE_URL, options='-c statement_timeout=0')


def clean_text(value):
    if not value:
        return None
    return re.sub(r'\s+', ' ', value).strip() or None


def absolutize_storefront_url(url, storefront_slug):
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url
    base_url = STOREFRONT_BASE_URLS.get(storefront_slug)
    if not base_url:
        return url
    return urljoin(base_url, url)


def get_meta_content(soup, *, prop=None, name=None):
    if prop:
        node = soup.find('meta', attrs={'property': prop})
        if node and node.get('content'):
            return clean_text(node['content'])
    if name:
        node = soup.find('meta', attrs={'name': name})
        if node and node.get('content'):
            return clean_text(node['content'])
    return None


def normalize_title(title):
    if not title:
        return None
    cleaned = clean_text(title)
    if not cleaned:
        return None
    for suffix in ('| Nutaku', '- Nutaku', '| Games on Nutaku'):
        if cleaned.endswith(suffix):
            return clean_text(cleaned[: -len(suffix)])
    return cleaned


def parse_json_ld(soup):
    items = []
    for node in soup.find_all('script', attrs={'type': 'application/ld+json'}):
        raw = node.string or node.get_text()
        if not raw or not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(payload, list):
            items.extend(payload)
        else:
            items.append(payload)
    return items


def find_structured_product(structured_items):
    for item in structured_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get('@type')
        if item_type == 'Product':
            return item
        if isinstance(item_type, list) and 'Product' in item_type:
            return item
    return None


def extract_list_values(soup, selectors):
    values = []
    seen = set()
    for selector in selectors:
        for node in soup.select(selector):
            text = clean_text(node.get_text(' ', strip=True))
            if not text or text in seen:
                continue
            seen.add(text)
            values.append(text)
    return values


def find_label_value(soup, labels):
    label_set = {label.casefold() for label in labels}

    for node in soup.find_all(['dt', 'strong', 'span', 'div', 'p']):
        label = clean_text(node.get_text(' ', strip=True))
        if not label:
            continue
        normalized = label.casefold().rstrip(':')
        if normalized not in label_set:
            continue

        sibling = node.find_next_sibling()
        if sibling:
            text = clean_text(sibling.get_text(' ', strip=True))
            if text:
                return text

        parent = node.parent
        if parent:
            combined = [clean_text(child.get_text(' ', strip=True)) for child in parent.find_all(recursive=False)]
            combined = [value for value in combined if value]
            if len(combined) >= 2:
                for value in combined:
                    if value.casefold().rstrip(':') != normalized:
                        return value
    return None


def scrape_nutaku_metadata(url):
    response = requests.get(
        url,
        timeout=30,
        headers={'User-Agent': USER_AGENT},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    structured_items = parse_json_ld(soup)
    product = find_structured_product(structured_items)

    title = (
        normalize_title(product.get('name')) if product else None
    ) or normalize_title(get_meta_content(soup, prop='og:title')) or normalize_title(soup.title.get_text(strip=True) if soup.title else None)

    description = (
        clean_text(product.get('description')) if product else None
    ) or get_meta_content(soup, prop='og:description') or get_meta_content(soup, name='description')

    image_url = None
    if product:
        image = product.get('image')
        if isinstance(image, list):
            image_url = image[0] if image else None
        elif isinstance(image, dict):
            image_url = image.get('url')
        else:
            image_url = image
    image_url = image_url or get_meta_content(soup, prop='og:image')

    brand = product.get('brand') if product else None
    developer = None
    publisher = None
    if isinstance(brand, dict):
        developer = clean_text(brand.get('name'))
    elif isinstance(brand, str):
        developer = clean_text(brand)

    developer = developer or find_label_value(soup, {'developer', 'developer/publisher'})
    publisher = find_label_value(soup, {'publisher'})
    if not publisher:
        publisher = developer

    genres = []
    if product:
        raw_genre = product.get('genre')
        if isinstance(raw_genre, list):
            genres = [clean_text(value) for value in raw_genre if clean_text(value)]
        elif raw_genre:
            genres = [clean_text(raw_genre)]
    if not genres:
        genres = extract_list_values(
            soup,
            [
                'a[href*="/games/genre/"]',
                'a[href*="/genre/"]',
            ],
        )

    tags = extract_list_values(
        soup,
        [
            'a[href*="/games/tag/"]',
            'a[href*="/tag/"]',
        ],
    )

    return {
        'title': title,
        'description': description,
        'image_url': image_url,
        'developer': developer,
        'publisher': publisher,
        'genres': genres,
        'tags': tags,
        'raw_payload': {
            'url': url,
            'structured_data': product,
            'meta': {
                'og_title': get_meta_content(soup, prop='og:title'),
                'og_description': get_meta_content(soup, prop='og:description'),
                'og_image': get_meta_content(soup, prop='og:image'),
            },
        },
    }


def scrape_erolabs_metadata(url):
    response = requests.get(
        url,
        timeout=30,
        headers={'User-Agent': USER_AGENT},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    structured_items = parse_json_ld(soup)
    product = find_structured_product(structured_items)

    title = (
        normalize_title(product.get('name')) if product else None
    ) or normalize_title(get_meta_content(soup, prop='og:title')) or normalize_title(soup.title.get_text(strip=True) if soup.title else None)

    description = (
        clean_text(product.get('description')) if product else None
    ) or get_meta_content(soup, prop='og:description') or get_meta_content(soup, name='description')

    image_url = None
    if product:
        image = product.get('image')
        if isinstance(image, list):
            image_url = image[0] if image else None
        elif isinstance(image, dict):
            image_url = image.get('url')
        else:
            image_url = image
    image_url = image_url or get_meta_content(soup, prop='og:image')

    brand = product.get('brand') if product else None
    developer = None
    publisher = None
    if isinstance(brand, dict):
        developer = clean_text(brand.get('name'))
    elif isinstance(brand, str):
        developer = clean_text(brand)

    developer = developer or find_label_value(soup, {'developer', 'developer/publisher'})
    publisher = find_label_value(soup, {'publisher'})
    if not publisher:
        publisher = developer

    genres = []
    if product:
        raw_genre = product.get('genre')
        if isinstance(raw_genre, list):
            genres = [clean_text(value) for value in raw_genre if clean_text(value)]
        elif raw_genre:
            genres = [clean_text(raw_genre)]

    tags = extract_list_values(
        soup,
        [
            'a[href*="/games/"]',
            'a[href*="/tag/"]',
        ],
    )

    return {
        'title': title,
        'description': description,
        'image_url': image_url,
        'developer': developer,
        'publisher': publisher,
        'genres': genres,
        'tags': tags,
        'raw_payload': {
            'url': url,
            'structured_data': product,
            'meta': {
                'og_title': get_meta_content(soup, prop='og:title'),
                'og_description': get_meta_content(soup, prop='og:description'),
                'og_image': get_meta_content(soup, prop='og:image'),
            },
        },
    }


def scrape_metadata(url, storefront_slug):
    url = absolutize_storefront_url(url, storefront_slug)
    hostname = urlparse(url).netloc.casefold()
    if 'ero-labs.com' in hostname or storefront_slug == 'erolabs-home-ranking':
        return scrape_erolabs_metadata(url)
    return scrape_nutaku_metadata(url)


def select_aliases_to_refresh(conn, limit, refresh_days):
    with conn.cursor() as cur:
        cur.execute(
            """
            with latest_metadata as (
                select
                    gms.game_alias_id,
                    max(gms.captured_at) as last_captured_at
                from game_metadata_snapshots gms
                group by gms.game_alias_id
            )
            select
                ga.id,
                ga.title,
                ga.url,
                sf.slug
            from game_aliases ga
            join storefronts sf on sf.id = ga.storefront_id
            left join latest_metadata lm on lm.game_alias_id = ga.id
            where (sf.slug like 'nutaku-%%' or sf.slug = 'erolabs-home-ranking')
              and ga.url is not null
              and (
                  lm.last_captured_at is null
                  or lm.last_captured_at < %s
              )
            order by lm.last_captured_at asc nulls first, ga.updated_at desc
            limit %s
            """,
            (datetime.now(UTC) - timedelta(days=refresh_days), limit),
        )
        return cur.fetchall()


def insert_metadata_snapshot(conn, game_alias_id, metadata):
    captured_at = datetime.now(UTC)
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into game_metadata_snapshots (
                game_alias_id,
                captured_at,
                genres,
                tags,
                description,
                developer,
                publisher,
                image_url,
                raw_payload
            )
            values (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                game_alias_id,
                captured_at,
                json.dumps(metadata['genres']),
                json.dumps(metadata['tags']),
                metadata['description'],
                metadata['developer'],
                metadata['publisher'],
                metadata['image_url'],
                json.dumps(metadata['raw_payload']),
            ),
        )
        cur.execute(
            """
            update game_aliases
            set developer_raw = coalesce(%s, developer_raw),
                publisher_raw = coalesce(%s, publisher_raw),
                updated_at = now()
            where id = %s
            """,
            (metadata['developer'], metadata['publisher'], game_alias_id),
        )


def main():
    with get_conn() as conn:
        aliases = select_aliases_to_refresh(conn, DEFAULT_LIMIT, REFRESH_DAYS)
        print(f'Aliases selected for metadata refresh: {len(aliases)}')

        for index, (game_alias_id, title, url, storefront_slug) in enumerate(aliases, start=1):
            target_url = absolutize_storefront_url(url, storefront_slug)
            print(f'[{index}/{len(aliases)}] Refreshing {title} ({storefront_slug}) -> {target_url}', flush=True)
            try:
                metadata = scrape_metadata(target_url, storefront_slug)
                insert_metadata_snapshot(conn, game_alias_id, metadata)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        update game_aliases
                        set url = %s,
                            updated_at = now()
                        where id = %s
                          and url is distinct from %s
                        """,
                        (target_url, game_alias_id, target_url),
                    )
                conn.commit()
                print(
                    f'  Saved metadata: developer={metadata["developer"] or "-"}, '
                    f'publisher={metadata["publisher"] or "-"}, '
                    f'image={"yes" if metadata["image_url"] else "no"}',
                    flush=True,
                )
            except Exception as exc:
                conn.rollback()
                print(f'  Error refreshing metadata for {title}: {exc}', flush=True)


if __name__ == '__main__':
    main()
