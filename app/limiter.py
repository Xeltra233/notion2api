from slowapi import Limiter
from slowapi.util import get_remote_address

# 全局默认限制：每个 IP 每分钟 20 次请求
limiter = Limiter(key_func=get_remote_address, default_limits=["20/minute"])
