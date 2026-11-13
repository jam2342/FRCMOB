from contextlib import contextmanager
import uuid

import redis

from app.core.config import settings


@contextmanager
def rating_lock(name, *, seconds=180):
    # Fail closed on Redis failure: a second full replay can starve the web workers.
    conn = redis.from_url(settings.redis_url)
    token = uuid.uuid4().hex
    key = f"rating:{name}"
    acquired = False
    try:
        acquired = bool(conn.set(key, token, nx=True, ex=seconds))
        yield acquired
    finally:
        if acquired:
            conn.eval("if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end", 1, key, token)
