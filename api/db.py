import logging
import os
from contextlib import contextmanager
from typing import Generator, Optional

from psycopg import connect
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")


@contextmanager
def get_db_connection() -> Generator:
    conn = connect(DATABASE_URL, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


def is_database_configured() -> bool:
    return bool(DATABASE_URL)


def init_db() -> Optional[str]:
    if not DATABASE_URL:
        return "DATABASE_URL"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists message_mappings (
                    source_message_id bigint primary key,
                    tg_channel_message_id bigint unique,
                    mastodon_status_id text,
                    created_at timestamptz not null default now()
                )
                """
            )
            cur.execute(
                """
                alter table message_mappings
                add column if not exists tg_channel_message_ids text
                """
            )
            cur.execute(
                """
                alter table message_mappings
                add column if not exists media_group_id text
                """
            )
            cur.execute(
                """
                create table if not exists rate_limits (
                    user_id bigint primary key,
                    request_count integer not null,
                    window_started_at timestamptz not null
                    )
                """
            )
            cur.execute(
                """
                create table if not exists pending_media_group_items (
                    media_group_id text not null,
                    source_message_id bigint primary key,
                    payload_json jsonb not null,
                    created_at timestamptz not null default now()
                )
                """
            )
            cur.execute(
                """
                create table if not exists media_group_states (
                    media_group_id text primary key,
                    first_source_message_id bigint not null,
                    latest_source_message_id bigint not null,
                    publish_after timestamptz not null,
                    stable_checks integer not null default 0,
                    published_at timestamptz,
                    created_at timestamptz not null default now(),
                    updated_at timestamptz not null default now()
                )
                """
            )
            cur.execute(
                """
                create table if not exists private_message_aliases (
                    alias_message_id bigint primary key,
                    source_message_id bigint not null,
                    created_at timestamptz not null default now()
                )
                """
            )
            cur.execute(
                """
                create table if not exists job_queue (
                    id bigserial primary key,
                    job_type text not null,
                    dedupe_key text unique,
                    payload_json jsonb not null,
                    status text not null default 'pending',
                    run_after timestamptz not null default now(),
                    attempts integer not null default 0,
                    locked_at timestamptz,
                    created_at timestamptz not null default now(),
                    updated_at timestamptz not null default now()
                )
                """
            )
            cur.execute(
                """
                create table if not exists webhook_updates (
                    update_id bigint primary key,
                    status text not null default 'processing',
                    updated_at timestamptz not null default now()
                )
                """
            )
        conn.commit()

    logger.info("数据库表初始化完成")
    return None
