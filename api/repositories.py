import logging
from typing import Any, Dict, List, Optional

from api.db import get_db_connection, is_database_configured
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

Mapping = Dict[str, Any]
Job = Dict[str, Any]


def claim_webhook_update(update_id: int) -> Optional[bool]:
    if not is_database_configured():
        return None

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into webhook_updates (update_id, status, updated_at)
                    values (%s, 'processing', now())
                    on conflict (update_id)
                    do update set
                        status = 'processing',
                        updated_at = now()
                    where webhook_updates.status = 'processing'
                      and webhook_updates.updated_at <= now() - interval '5 minutes'
                    returning update_id
                    """,
                    (update_id,),
                )
                claimed = cur.fetchone() is not None
            conn.commit()
        return claimed
    except Exception as e:
        logger.error(f"领取 Webhook 更新失败：{e}")
        return None


def complete_webhook_update(update_id: int) -> None:
    if not is_database_configured():
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update webhook_updates
                    set status = 'completed', updated_at = now()
                    where update_id = %s
                    """,
                    (update_id,),
                )
                cur.execute(
                    """
                    delete from webhook_updates
                    where status = 'completed'
                      and updated_at < now() - interval '7 days'
                    """
                )
            conn.commit()
    except Exception as e:
        logger.error(f"完成 Webhook 更新失败：{e}")


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
    mastodon_media_ids: Optional[List[str]] = None,
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
                        media_group_id,
                        mastodon_media_ids
                    )
                    values (%s, %s, %s, %s, %s, %s)
                    on conflict (source_message_id)
                    do update set
                        tg_channel_message_id = excluded.tg_channel_message_id,
                        tg_channel_message_ids = excluded.tg_channel_message_ids,
                        mastodon_status_id = excluded.mastodon_status_id,
                        media_group_id = excluded.media_group_id,
                        mastodon_media_ids = excluded.mastodon_media_ids
                    """,
                    (
                        source_msg_id,
                        tg_channel_msg_id,
                        ",".join(str(message_id) for message_id in tg_channel_message_ids)
                        if tg_channel_message_ids
                        else None,
                        masto_status_id,
                        media_group_id,
                        ",".join(mastodon_media_ids) if mastodon_media_ids else None,
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
                        mastodon_media_ids,
                        media_group_id,
                        created_at::text as timestamp
                    from message_mappings
                    where source_message_id = %s or tg_channel_message_id = %s
                    order by
                        case
                            when source_message_id = %s then 0
                            else 1
                        end,
                        created_at desc
                    limit 1
                    """,
                    (source_msg_id, source_msg_id, source_msg_id),
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
                mastodon_media_ids = mapping.get("mastodon_media_ids")
                mapping["mastodon_media_id_list"] = (
                    [
                        media_id
                        for media_id in mastodon_media_ids.split(",")
                        if media_id
                    ]
                    if mastodon_media_ids
                    else []
                )
                return mapping
    except Exception as e:
        logger.error(f"获取映射失败：{e}")
        return None


def update_mapping_mastodon_media_ids(source_msg_id: int, media_ids: List[str]) -> None:
    if not is_database_configured():
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update message_mappings
                    set mastodon_media_ids = %s
                    where source_message_id = %s
                    """,
                    (",".join(media_ids) if media_ids else None, source_msg_id),
                )
            conn.commit()
    except Exception as e:
        logger.error(f"更新 Mastodon 媒体映射失败：{e}")


def save_private_message_alias(alias_message_id: int, source_message_id: int) -> None:
    if not is_database_configured():
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into private_message_aliases (
                        alias_message_id,
                        source_message_id
                    )
                    values (%s, %s)
                    on conflict (alias_message_id)
                    do update set
                        source_message_id = excluded.source_message_id
                    """,
                    (alias_message_id, source_message_id),
                )
            conn.commit()
        logger.info(
            "保存私聊别名：alias=%s -> source=%s",
            alias_message_id,
            source_message_id,
        )
    except Exception as e:
        logger.error(f"保存私聊别名失败：{e}")


def resolve_source_message_id(message_id: int) -> int:
    if not is_database_configured():
        return message_id

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select source_message_id
                    from private_message_aliases
                    where alias_message_id = %s
                    limit 1
                    """,
                    (message_id,),
                )
                row = cur.fetchone()
                if row and row.get("source_message_id"):
                    logger.info(
                        "解析私聊别名成功：alias=%s -> source=%s",
                        message_id,
                        row["source_message_id"],
                    )
                    return row["source_message_id"]
    except Exception as e:
        logger.error(f"解析私聊别名失败：{e}")
    return message_id


def get_mapping_by_media_group_id(media_group_id: str) -> Optional[Mapping]:
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
                        mastodon_media_ids,
                        media_group_id,
                        created_at::text as timestamp
                    from message_mappings
                    where media_group_id = %s
                    order by source_message_id asc
                    limit 1
                    """,
                    (media_group_id,),
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
                mastodon_media_ids = mapping.get("mastodon_media_ids")
                mapping["mastodon_media_id_list"] = (
                    [
                        media_id
                        for media_id in mastodon_media_ids.split(",")
                        if media_id
                    ]
                    if mastodon_media_ids
                    else []
                )
                return mapping
    except Exception as e:
        logger.error(f"按相册获取映射失败：{e}")
        return None


def get_media_group_source_message_ids(media_group_id: str) -> List[int]:
    if not is_database_configured():
        return []

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select source_message_id
                    from message_mappings
                    where media_group_id = %s
                    order by source_message_id asc
                    """,
                    (media_group_id,),
                )
                rows = cur.fetchall() or []
                return [row["source_message_id"] for row in rows]
    except Exception as e:
        logger.error(f"获取相册源消息列表失败：{e}")
        return []


def has_media_group_mapping(media_group_id: str) -> bool:
    if not is_database_configured():
        return False

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select 1
                    from message_mappings
                    where media_group_id = %s
                    limit 1
                    """,
                    (media_group_id,),
                )
                return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"检查相册映射失败：{e}")
        return False


def has_pending_media_group_job(media_group_id: str) -> bool:
    if not is_database_configured():
        return False

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select 1
                    from job_queue
                    where job_type = 'process_media_group'
                      and dedupe_key = %s
                      and status in ('pending', 'processing')
                    limit 1
                    """,
                    (f"media_group:{media_group_id}",),
                )
                return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"检查相册队列任务失败：{e}")
        return False


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
) -> bool:
    if not is_database_configured():
        return False

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
                    (media_group_id, source_message_id, Jsonb(payload_json)),
                )
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"保存相册临时消息失败：{e}")
        return False


def touch_media_group_state(
    media_group_id: str,
    source_message_id: int,
    settle_seconds: int,
) -> bool:
    if not is_database_configured():
        return False

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into media_group_states (
                        media_group_id,
                        first_source_message_id,
                        latest_source_message_id,
                        publish_after,
                        stable_checks,
                        updated_at
                    )
                    values (
                        %s,
                        %s,
                        %s,
                        now() + make_interval(secs => %s),
                        0,
                        now()
                    )
                    on conflict (media_group_id)
                    do update set
                        first_source_message_id = least(
                            media_group_states.first_source_message_id,
                            excluded.first_source_message_id
                        ),
                        latest_source_message_id = greatest(
                            media_group_states.latest_source_message_id,
                            excluded.latest_source_message_id
                        ),
                        publish_after = now() + make_interval(secs => %s),
                        stable_checks = 0,
                        updated_at = now()
                    """,
                    (
                        media_group_id,
                        source_message_id,
                        source_message_id,
                        settle_seconds,
                        settle_seconds,
                    ),
                )
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"更新相册状态失败：{e}")
        return False


def get_media_group_state(media_group_id: str) -> Optional[Dict[str, Any]]:
    if not is_database_configured():
        return None

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select
                        media_group_id,
                        first_source_message_id,
                        latest_source_message_id,
                        publish_after,
                        stable_checks,
                        published_at,
                        created_at,
                        updated_at
                    from media_group_states
                    where media_group_id = %s
                    limit 1
                    """,
                    (media_group_id,),
                )
                return cur.fetchone()
    except Exception as e:
        logger.error(f"读取相册状态失败：{e}")
        return None


def bump_media_group_stable_check(media_group_id: str) -> Optional[int]:
    if not is_database_configured():
        return None

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update media_group_states
                    set
                        stable_checks = stable_checks + 1,
                        updated_at = now()
                    where media_group_id = %s
                    returning stable_checks
                    """,
                    (media_group_id,),
                )
                row = cur.fetchone()
            conn.commit()
        return row["stable_checks"] if row else None
    except Exception as e:
        logger.error(f"递增相册稳定计数失败：{e}")
        return None


def mark_media_group_published(media_group_id: str) -> None:
    if not is_database_configured():
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update media_group_states
                    set
                        published_at = now(),
                        updated_at = now()
                    where media_group_id = %s
                    """,
                    (media_group_id,),
                )
            conn.commit()
    except Exception as e:
        logger.error(f"标记相册已发布失败：{e}")


def delete_media_group_state(media_group_id: str) -> None:
    if not is_database_configured():
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "delete from media_group_states where media_group_id = %s",
                    (media_group_id,),
                )
            conn.commit()
    except Exception as e:
        logger.error(f"删除相册状态失败：{e}")


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
    """查找所有达到首个静默窗口的待处理相册。"""
    if not is_database_configured():
        return []

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select media_group_id
                    from media_group_states
                    where publish_after <= now()
                      and published_at is null
                    order by publish_after asc, media_group_id asc
                    """,
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


def enqueue_job(
    job_type: str,
    payload_json: Dict[str, Any],
    dedupe_key: Optional[str] = None,
    delay_seconds: int = 0,
) -> bool:
    if not is_database_configured():
        return False

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into job_queue (
                        job_type,
                        dedupe_key,
                        payload_json,
                        run_after,
                        updated_at
                    )
                    values (
                        %s,
                        %s,
                        %s,
                        now() + make_interval(secs => %s),
                        now()
                    )
                    on conflict (dedupe_key)
                    do update set
                        payload_json = excluded.payload_json,
                        run_after = excluded.run_after,
                        status = 'pending',
                        locked_at = null,
                        updated_at = now()
                    """,
                    (job_type, dedupe_key, Jsonb(payload_json), delay_seconds),
                )
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"入队任务失败：{e}")
        return False


def claim_next_job() -> Optional[Job]:
    if not is_database_configured():
        return None

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    with next_job as (
                        select id
                        from job_queue
                        where status = 'pending'
                          and run_after <= now()
                        order by run_after asc, id asc
                        for update skip locked
                        limit 1
                    )
                    update job_queue
                    set
                        status = 'processing',
                        locked_at = now(),
                        attempts = attempts + 1,
                        updated_at = now()
                    where id in (select id from next_job)
                    returning id, job_type, dedupe_key, payload_json, attempts
                    """
                )
                job = cur.fetchone()
            conn.commit()
        return job
    except Exception as e:
        logger.error(f"领取任务失败：{e}")
        return None


def complete_job(job_id: int) -> None:
    if not is_database_configured():
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from job_queue where id = %s", (job_id,))
            conn.commit()
    except Exception as e:
        logger.error(f"完成任务失败：{e}")


def retry_job(job_id: int, delay_seconds: int = 2) -> None:
    if not is_database_configured():
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update job_queue
                    set
                        status = 'pending',
                        locked_at = null,
                        run_after = now() + make_interval(secs => %s),
                        updated_at = now()
                    where id = %s
                    """,
                    (delay_seconds, job_id),
                )
            conn.commit()
    except Exception as e:
        logger.error(f"重试任务失败：{e}")


def cancel_jobs_for_source_message(source_message_id: int) -> int:
    if not is_database_configured():
        return 0

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    delete from job_queue
                    where status = 'pending'
                      and (
                        (job_type = 'publish_message' and payload_json->>'message_id' = %s)
                        or (
                            job_type = 'delete_message'
                            and payload_json->'reply_to_message'->>'message_id' = %s
                        )
                      )
                    returning id
                    """,
                    (str(source_message_id), str(source_message_id)),
                )
                rows = cur.fetchall() or []
            conn.commit()
        return len(rows)
    except Exception as e:
        logger.error(f"取消源消息任务失败：{e}")
        return 0


def cancel_jobs_for_media_group(media_group_id: str) -> int:
    if not is_database_configured():
        return 0

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    delete from job_queue
                    where status = 'pending'
                      and job_type = 'process_media_group'
                      and dedupe_key = %s
                    returning id
                    """,
                    (f"media_group:{media_group_id}",),
                )
                rows = cur.fetchall() or []
            conn.commit()
        return len(rows)
    except Exception as e:
        logger.error(f"取消相册任务失败：{e}")
        return 0


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


def get_mappings_by_media_group_id(media_group_id: str) -> List[Mapping]:
    if not is_database_configured():
        return []

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
                        mastodon_media_ids,
                        media_group_id,
                        created_at::text as timestamp
                    from message_mappings
                    where media_group_id = %s
                    order by source_message_id asc
                    """,
                    (media_group_id,),
                )
                rows = cur.fetchall() or []
                for mapping in rows:
                    tg_channels = mapping.get("tg_channels")
                    mapping["tg_channel_messages"] = (
                        [int(message_id) for message_id in tg_channels.split(",")]
                        if tg_channels
                        else []
                    )
                    mastodon_media_ids = mapping.get("mastodon_media_ids")
                    mapping["mastodon_media_id_list"] = (
                        [
                            media_id
                            for media_id in mastodon_media_ids.split(",")
                            if media_id
                        ]
                        if mastodon_media_ids
                        else []
                    )
                return rows
    except Exception as e:
        logger.error(f"按相册获取全部映射失败：{e}")
        return []
