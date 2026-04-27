import logging
from typing import Any, Dict, Optional

from api.db import get_db_connection, is_database_configured

logger = logging.getLogger(__name__)

Mapping = Dict[str, Any]


def check_rate_limit(user_id: int) -> bool:
    if not is_database_configured():
        return True

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into rate_limits (user_id, request_count, window_started_at)
                    values (%s, 1, now())
                    on conflict (user_id)
                    do update set
                        request_count = case
                            when rate_limits.window_started_at <= now() - interval '60 seconds' then 1
                            else rate_limits.request_count + 1
                        end,
                        window_started_at = case
                            when rate_limits.window_started_at <= now() - interval '60 seconds' then now()
                            else rate_limits.window_started_at
                        end
                    returning request_count
                    """,
                    (user_id,),
                )
                count = cur.fetchone()["request_count"]
            conn.commit()

        if count > 10:
            logger.warning("用户 %s 触发速率限制：%s/分钟", user_id, count)
            return False

        return True
    except Exception as e:
        logger.error(f"速率限制检查失败：{e}")
        return True


def save_mapping(
    source_msg_id: int, tg_channel_msg_id: int, masto_status_id: Optional[str]
) -> None:
    if not is_database_configured():
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into message_mappings (
                        source_message_id,
                        tg_channel_message_id,
                        mastodon_status_id
                    )
                    values (%s, %s, %s)
                    on conflict (source_message_id)
                    do update set
                        tg_channel_message_id = excluded.tg_channel_message_id,
                        mastodon_status_id = excluded.mastodon_status_id
                    """,
                    (source_msg_id, tg_channel_msg_id, masto_status_id),
                )
            conn.commit()
        logger.info(
            "保存映射：source=%s, tg=%s, masto=%s",
            source_msg_id,
            tg_channel_msg_id,
            masto_status_id,
        )
    except Exception as e:
        logger.error(f"保存映射失败：{e}")


def get_mapping(source_msg_id: int) -> Optional[Mapping]:
    if not is_database_configured():
        return None

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select
                        source_message_id as source,
                        tg_channel_message_id as tg_channel,
                        mastodon_status_id as masto,
                        created_at::text as timestamp
                    from message_mappings
                    where source_message_id = %s or tg_channel_message_id = %s
                    limit 1
                    """,
                    (source_msg_id, source_msg_id),
                )
                return cur.fetchone()
    except Exception as e:
        logger.error(f"获取映射失败：{e}")
        return None


def delete_mapping(source_msg_id: int) -> None:
    if not is_database_configured():
        return

    try:
        mapping = get_mapping(source_msg_id)
        if not mapping:
            return

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "delete from message_mappings where source_message_id = %s",
                    (mapping["source"],),
                )
            conn.commit()
        logger.info("删除映射：source=%s", source_msg_id)
    except Exception as e:
        logger.error(f"删除映射失败：{e}")
