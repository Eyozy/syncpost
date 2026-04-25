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
                create table if not exists rate_limits (
                    user_id bigint primary key,
                    request_count integer not null,
                    window_started_at timestamptz not null
                )
                """
            )
        conn.commit()

    logger.info("数据库表初始化完成")
    return None
