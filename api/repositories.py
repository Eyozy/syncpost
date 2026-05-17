import logging
from typing import Any, Dict, List, Optional

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
    source_msg_id: int,
    tg_channel_msg_id: int,
    masto_status_id: Optional[str],
    tg_channel_message_ids: Optional[List[int]] = None,
    media_group_id: Optional[str] = None,
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
                        tg_channel_message_ids,
                        mastodon_status_id,
                        media_group_id
                    )
                    values (%s, %s, %s, %s, %s)
                    on conflict (source_message_id)
                    do update set
                        tg_channel_message_id = excluded.tg_channel_message_id,
                        tg_channel_message_ids = excluded.tg_channel_message_ids,
                        mastodon_status_id = excluded.mastodon_status_id,
                        media_group_id = excluded.media_group_id
                    """,
                    (
                        source_msg_id,
                        tg_channel_msg_id,
                        ",".join(str(message_id) for message_id in tg_channel_message_ids)
                        if tg_channel_message_ids
                        else None,
                        masto_status_id,
                        media_group_id,
                    ),
                )
            conn.commit()
        logger.info(
            "保存映射：source=%s, tg=%s, masto=%s, group=%s",
            source_msg_id,
            tg_channel_msg_id,
            masto_status_id,
            media_group_id,
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
                        tg_channel_message_ids as tg_channels,
                        mastodon_status_id as masto,
                        media_group_id,
                        created_at::text as timestamp
                    from message_mappings
                    where source_message_id = %s or tg_channel_message_id = %s
                    limit 1
                    """,
                    (source_msg_id, source_msg_id),
                )
                mapping = cur.fetchone()
                if not mapping:
                    return None
                tg_channels = mapping.get("tg_channels")
                mapping["tg_channel_messages"] = (
                    [int(message_id) for message_id in tg_channels.split(",")]
                    if tg_channels
                    else []
                )
                return mapping
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
                if mapping.get("media_group_id"):
                    cur.execute(
                        "delete from message_mappings where media_group_id = %s",
                        (mapping["media_group_id"],),
                    )
                else:
                    cur.execute(
                        "delete from message_mappings where source_message_id = %s",
                        (mapping["source"],),
                    )
            conn.commit()
        logger.info("删除映射：source=%s", source_msg_id)
    except Exception as e:
        logger.error(f"删除映射失败：{e}")


def save_pending_media_group_item(
    media_group_id: str, source_message_id: int, payload_json: Dict[str, Any]
) -> None:
    if not is_database_configured():
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into pending_media_group_items (
                        media_group_id,
                        source_message_id,
                        payload_json
                    )
                    values (%s, %s, %s)
                    on conflict (source_message_id)
                    do update set
                        media_group_id = excluded.media_group_id,
                        payload_json = excluded.payload_json
                    """,
                    (media_group_id, source_message_id, payload_json),
                )
            conn.commit()
    except Exception as e:
        logger.error(f"保存相册临时消息失败：{e}")


def get_pending_media_group_items(media_group_id: str) -> List[Dict[str, Any]]:
    if not is_database_configured():
        return []

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select payload_json
                    from pending_media_group_items
                    where media_group_id = %s
                    order by source_message_id asc
                    """,
                    (media_group_id,),
                )
                rows = cur.fetchall() or []
                return [row["payload_json"] for row in rows]
    except Exception as e:
        logger.error(f"读取相册临时消息失败：{e}")
        return []


def get_ready_pending_media_group_ids(min_age_seconds: int = 3) -> List[str]:
    """查找所有已就绪的待处理相册（最新一条消息存入超过 min_age_seconds 秒）。"""
    if not is_database_configured():
        return []

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select media_group_id
                    from pending_media_group_items
                    group by media_group_id
                    having max(created_at) < now() - make_interval(secs => %s)
                    """,
                    (min_age_seconds,),
                )
                rows = cur.fetchall() or []
                return [row["media_group_id"] for row in rows]
    except Exception as e:
        logger.error(f"查询就绪相册失败：{e}")
        return []


def pop_ready_pending_media_group_items(
    media_group_id: str, min_age_seconds: int = 3
) -> List[Dict[str, Any]]:
    if not is_database_configured():
        return []

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    with ready_group as (
                        select media_group_id
                        from pending_media_group_items
                        where media_group_id = %s
                        group by media_group_id
                        having max(created_at) <= now() - make_interval(secs => %s)
                    )
                    delete from pending_media_group_items
                    where media_group_id in (select media_group_id from ready_group)
                    returning payload_json
                    """,
                    (media_group_id, min_age_seconds),
                )
                rows = cur.fetchall() or []
            conn.commit()
        return [row["payload_json"] for row in rows]
    except Exception as e:
        logger.error(f"领取待处理相册失败：{e}")
        return []


def delete_pending_media_group_items(media_group_id: str) -> None:
    if not is_database_configured():
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "delete from pending_media_group_items where media_group_id = %s",
                    (media_group_id,),
                )
            conn.commit()
    except Exception as e:
        logger.error(f"删除相册临时消息失败：{e}")
