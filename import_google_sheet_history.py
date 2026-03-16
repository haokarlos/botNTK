from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
import os

import psycopg

from db_utils import get_storefront_id, save_snapshot_to_postgres
from EL_Games_Sheet import SHEET_NAME, load_google_credentials

import gspread


DATABASE_URL = os.getenv('DATABASE_URL')

WORKSHEETS = [
    {'sheet_index': 0, 'storefront_slug': 'nutaku-browser-ranking'},
    {'sheet_index': 1, 'storefront_slug': 'nutaku-mobile-ranking'},
    {'sheet_index': 2, 'storefront_slug': 'nutaku-all-games'},
    {'sheet_index': 3, 'storefront_slug': 'erolabs-home-ranking'},
]


def open_google_sheet():
    credentials = load_google_credentials()
    gc = gspread.authorize(credentials)
    return gc.open(SHEET_NAME)


def open_database_connection():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL es obligatorio para importar el historico.')
    return psycopg.connect(DATABASE_URL)


def parse_sheet_date(value):
    cleaned = value.strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%y', '%d/%m/%Y', '%m/%d/%y', '%m/%d/%Y'):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f'Fecha no reconocida en Google Sheets: {value}')


def clean_game_names(row_values):
    return [value.strip() for value in row_values if value and value.strip()]


def row_quality_score(game_names):
    non_empty = len(game_names)
    unique_titles = len(set(game_names))
    return (non_empty, unique_titles)


def choose_canonical_rows(records):
    canonical = {}
    duplicate_summary = []

    for record in records:
        existing = canonical.get(record['capture_date'])
        if existing is None:
            canonical[record['capture_date']] = record
            continue

        keep_current = record['quality'] > existing['quality']
        if record['quality'] == existing['quality']:
            keep_current = record['row_number'] > existing['row_number']

        if keep_current:
            duplicate_summary.append(
                f"{record['capture_date']}: kept row {record['row_number']} over row {existing['row_number']}"
            )
            canonical[record['capture_date']] = record
        else:
            duplicate_summary.append(
                f"{record['capture_date']}: kept row {existing['row_number']} over row {record['row_number']}"
            )

    return canonical, duplicate_summary


def load_sheet_records(worksheet):
    all_rows = worksheet.get_all_values()
    records = []

    for row_number, row in enumerate(all_rows, start=1):
        if not row:
            continue
        if not row[0].strip():
            continue

        capture_date = parse_sheet_date(row[0])
        game_names = clean_game_names(row[1:])
        if not game_names:
            continue

        records.append(
            {
                'row_number': row_number,
                'capture_date': capture_date,
                'game_names': game_names,
                'quality': row_quality_score(game_names),
            }
        )

    return records


def upsert_records(conn, storefront_slug, canonical_rows):
    imported_snapshot_ids = {}
    for capture_date in sorted(canonical_rows):
        record = canonical_rows[capture_date]
        captured_at = datetime.combine(capture_date, datetime.min.time(), tzinfo=UTC)
        snapshot_id = save_snapshot_to_postgres(
            conn,
            storefront_slug,
            source_url='google-sheet-import',
            game_names=record['game_names'],
            capture_date=capture_date,
            captured_at=captured_at,
            data_source='sheet_import',
            notes=f"Imported from Google Sheet row {record['row_number']}",
        )
        imported_snapshot_ids[capture_date] = snapshot_id
    return imported_snapshot_ids


def backfill_missing_dates(conn, storefront_slug, canonical_rows, imported_snapshot_ids):
    if not canonical_rows:
        return []

    known_dates = sorted(canonical_rows)
    current_date = known_dates[0]
    end_date = known_dates[-1]
    backfilled = []
    previous_real_date = None

    while current_date <= end_date:
        if current_date in canonical_rows:
            previous_real_date = current_date
            current_date += timedelta(days=1)
            continue

        source_record = canonical_rows[previous_real_date]
        source_snapshot_id = imported_snapshot_ids[previous_real_date]
        captured_at = datetime.combine(current_date, datetime.min.time(), tzinfo=UTC)
        snapshot_id = save_snapshot_to_postgres(
            conn,
            storefront_slug,
            source_url='google-sheet-imputed',
            game_names=source_record['game_names'],
            capture_date=current_date,
            captured_at=captured_at,
            data_source='imputed',
            copied_from_snapshot_id=source_snapshot_id,
            notes=f'Imputed from {previous_real_date.isoformat()}',
        )
        backfilled.append(
            {
                'capture_date': current_date,
                'copied_from_date': previous_real_date,
                'snapshot_id': snapshot_id,
            }
        )
        current_date += timedelta(days=1)

    return backfilled


def purge_existing_storefront_range(conn, storefront_slug, start_date, end_date):
    storefront_id = get_storefront_id(conn, storefront_slug)
    with conn.cursor() as cur:
        cur.execute(
            """
            delete from ranking_snapshots
            where storefront_id = %s
              and capture_date between %s and %s
            """,
            (storefront_id, start_date, end_date),
        )


def import_storefront_history(conn, sheet, config):
    worksheet = sheet.get_worksheet(config['sheet_index'])
    records = load_sheet_records(worksheet)
    canonical_rows, duplicate_summary = choose_canonical_rows(records)

    if not canonical_rows:
        return {
            'storefront_slug': config['storefront_slug'],
            'imported_count': 0,
            'imputed_count': 0,
            'duplicates': duplicate_summary,
        }

    min_date = min(canonical_rows)
    max_date = max(canonical_rows)
    purge_existing_storefront_range(conn, config['storefront_slug'], min_date, max_date)

    imported_snapshot_ids = upsert_records(conn, config['storefront_slug'], canonical_rows)
    backfilled = backfill_missing_dates(conn, config['storefront_slug'], canonical_rows, imported_snapshot_ids)

    return {
        'storefront_slug': config['storefront_slug'],
        'imported_count': len(imported_snapshot_ids),
        'imputed_count': len(backfilled),
        'duplicates': duplicate_summary,
        'backfilled_dates': backfilled,
    }


def main():
    sheet = open_google_sheet()
    with open_database_connection() as conn:
        summaries = []
        for config in WORKSHEETS:
            summary = import_storefront_history(conn, sheet, config)
            summaries.append(summary)
        conn.commit()

    for summary in summaries:
        print(
            f"{summary['storefront_slug']}: "
            f"{summary['imported_count']} observed, "
            f"{summary['imputed_count']} imputed"
        )
        for message in summary['duplicates'][:10]:
            print(f'  duplicate: {message}')
        if len(summary['duplicates']) > 10:
            print(f"  ... {len(summary['duplicates']) - 10} duplicates more")


if __name__ == '__main__':
    main()
