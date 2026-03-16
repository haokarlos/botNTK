create extension if not exists pgcrypto;

create table if not exists platforms (
    id uuid primary key default gen_random_uuid(),
    slug text not null unique,
    name text not null,
    created_at timestamptz not null default now()
);

create table if not exists storefronts (
    id uuid primary key default gen_random_uuid(),
    platform_id uuid not null references platforms(id) on delete cascade,
    slug text not null unique,
    name text not null,
    region text,
    device_type text,
    source_url text,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists games (
    id uuid primary key default gen_random_uuid(),
    canonical_name text not null,
    canonical_name_normalized text not null,
    developer text,
    publisher text,
    release_date date,
    adult_only boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists games_canonical_name_normalized_idx
    on games (canonical_name_normalized);

create table if not exists game_aliases (
    id uuid primary key default gen_random_uuid(),
    game_id uuid not null references games(id) on delete cascade,
    platform_id uuid not null references platforms(id) on delete cascade,
    storefront_id uuid references storefronts(id) on delete set null,
    store_game_id text,
    url text,
    title text not null,
    title_normalized text not null,
    developer_raw text,
    publisher_raw text,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop index if exists game_aliases_platform_store_game_id_unique;
drop index if exists game_aliases_platform_url_unique;
alter table game_aliases
    drop constraint if exists game_aliases_platform_store_game_id_unique;
alter table game_aliases
    drop constraint if exists game_aliases_platform_url_unique;

create unique index if not exists game_aliases_platform_store_game_id_unique
    on game_aliases (platform_id, store_game_id)
    where store_game_id is not null;

create unique index if not exists game_aliases_platform_url_unique
    on game_aliases (platform_id, url)
    where url is not null;

create index if not exists game_aliases_game_id_idx
    on game_aliases (game_id);

create index if not exists game_aliases_title_normalized_idx
    on game_aliases (title_normalized);

create unique index if not exists game_aliases_storefront_title_normalized_idx
    on game_aliases (storefront_id, title_normalized);

create table if not exists ranking_snapshots (
    id uuid primary key default gen_random_uuid(),
    storefront_id uuid not null references storefronts(id) on delete cascade,
    captured_at timestamptz not null,
    capture_date date not null,
    source_url text,
    status text not null default 'success',
    data_source text not null default 'observed',
    copied_from_snapshot_id uuid references ranking_snapshots(id) on delete set null,
    notes text,
    raw_payload jsonb,
    created_at timestamptz not null default now()
);

alter table ranking_snapshots
    add column if not exists data_source text not null default 'observed';
alter table ranking_snapshots
    add column if not exists copied_from_snapshot_id uuid references ranking_snapshots(id) on delete set null;
alter table ranking_snapshots
    add column if not exists notes text;

create unique index if not exists ranking_snapshots_storefront_capture_date_idx
    on ranking_snapshots (storefront_id, capture_date);

create index if not exists ranking_snapshots_storefront_captured_at_idx
    on ranking_snapshots (storefront_id, captured_at desc);

create table if not exists ranking_entries (
    id uuid primary key default gen_random_uuid(),
    snapshot_id uuid not null references ranking_snapshots(id) on delete cascade,
    game_alias_id uuid not null references game_aliases(id) on delete cascade,
    rank integer not null check (rank > 0),
    rank_score numeric,
    is_featured boolean,
    created_at timestamptz not null default now(),
    constraint ranking_entries_snapshot_rank_unique unique (snapshot_id, rank),
    constraint ranking_entries_snapshot_game_alias_unique unique (snapshot_id, game_alias_id)
);

create index if not exists ranking_entries_game_alias_id_idx
    on ranking_entries (game_alias_id);

create index if not exists ranking_entries_rank_idx
    on ranking_entries (rank);

create table if not exists game_metadata_snapshots (
    id uuid primary key default gen_random_uuid(),
    game_alias_id uuid not null references game_aliases(id) on delete cascade,
    captured_at timestamptz not null,
    price_amount numeric(12, 2),
    currency text,
    discount_text text,
    genres jsonb not null default '[]'::jsonb,
    tags jsonb not null default '[]'::jsonb,
    description text,
    developer text,
    publisher text,
    image_url text,
    raw_payload jsonb,
    created_at timestamptz not null default now()
);

create index if not exists game_metadata_snapshots_alias_captured_at_idx
    on game_metadata_snapshots (game_alias_id, captured_at desc);

create table if not exists external_signals (
    id uuid primary key default gen_random_uuid(),
    game_id uuid not null references games(id) on delete cascade,
    source text not null,
    captured_at timestamptz not null,
    metric_name text not null,
    metric_value_numeric numeric,
    metric_value_text text,
    raw_payload jsonb,
    created_at timestamptz not null default now()
);

create index if not exists external_signals_game_metric_idx
    on external_signals (game_id, source, metric_name, captured_at desc);

create table if not exists derived_game_metrics_daily (
    id uuid primary key default gen_random_uuid(),
    game_id uuid not null references games(id) on delete cascade,
    metric_date date not null,
    best_rank integer,
    avg_rank numeric(10, 2),
    rank_velocity numeric(10, 2),
    days_in_top_10 integer,
    days_in_top_50 integer,
    momentum_score numeric(10, 2),
    visibility_score numeric(10, 2),
    estimated_downloads_low integer,
    estimated_downloads_high integer,
    estimated_revenue_low numeric(14, 2),
    estimated_revenue_high numeric(14, 2),
    confidence text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint derived_game_metrics_daily_game_date_unique unique (game_id, metric_date)
);

create index if not exists derived_game_metrics_daily_metric_date_idx
    on derived_game_metrics_daily (metric_date desc);

insert into platforms (slug, name)
values
    ('nutaku', 'Nutaku'),
    ('erolabs', 'EroLabs')
on conflict (slug) do nothing;

insert into storefronts (platform_id, slug, name, region, device_type, source_url)
select p.id, v.slug, v.name, v.region, v.device_type, v.source_url
from platforms p
join (
    values
        ('nutaku', 'nutaku-browser-ranking', 'Nutaku Browser Ranking', 'global', 'browser', 'https://www.nutaku.net/games/genre/tag/pc-browser/os/dev/pub/lang/filter/price/features/status/ranking/'),
        ('nutaku', 'nutaku-mobile-ranking', 'Nutaku Mobile Ranking', 'global', 'mobile', 'https://www.nutaku.net/games/genre/tag/mobile/os/dev/pub/lang/filter/price/features/status/ranking/'),
        ('nutaku', 'nutaku-all-games', 'Nutaku All Games', 'global', 'all', 'https://www.nutaku.net/games/'),
        ('erolabs', 'erolabs-home-ranking', 'EroLabs Home Ranking', 'global', 'all', 'https://www.ero-labs.com/en/')
) as v(platform_slug, slug, name, region, device_type, source_url)
    on p.slug = v.platform_slug
on conflict (slug) do nothing;
