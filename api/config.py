import os
from upstash_redis import Redis

# 环境变量
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
TG_TOKEN = os.getenv('TG_TOKEN')
TG_CHANNEL_ID = os.getenv('TG_CHANNEL_ID')
TG_WEBHOOK_SECRET = os.getenv('TG_WEBHOOK_SECRET')
MASTO_TOKEN = os.getenv('MASTO_TOKEN')
MASTO_INSTANCE = os.getenv('MASTO_INSTANCE')
KV_REST_API_URL = os.getenv('KV_REST_API_URL')
KV_REST_API_TOKEN = os.getenv('KV_REST_API_TOKEN')

TG_API = f'https://api.telegram.org/bot{TG_TOKEN}'

# Redis 连接
redis = Redis(url=KV_REST_API_URL, token=KV_REST_API_TOKEN) if KV_REST_API_URL and KV_REST_API_TOKEN else None

# 消息映射缓存 TTL（30天）
CACHE_TTL_MAPPING = 2592000

# 必需配置检查
REQUIRED_CONFIG = {
    'ADMIN_ID': lambda: bool(ADMIN_ID),
    'TG_TOKEN': lambda: bool(TG_TOKEN),
    'TG_CHANNEL_ID': lambda: bool(TG_CHANNEL_ID),
    'TG_WEBHOOK_SECRET': lambda: bool(TG_WEBHOOK_SECRET),
    'MASTO_INSTANCE': lambda: bool(MASTO_INSTANCE),
    'MASTO_TOKEN': lambda: bool(MASTO_TOKEN),
    'KV_REST_API_URL': lambda: bool(KV_REST_API_URL),
    'KV_REST_API_TOKEN': lambda: bool(KV_REST_API_TOKEN),
}

def get_missing_config():
    """返回缺失的配置项列表"""
    return [name for name, checker in REQUIRED_CONFIG.items() if not checker()]

def is_config_complete():
    """检查配置是否完整"""
    return len(get_missing_config()) == 0