import os
import logging

import redis
from rq import Queue, Worker


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("worker")

    # Same guard as the backend: legacy *_TEXAS_* keys are silently ignored and
    # disable the analysis/ML pipeline. Fail loudly before consuming jobs.
    from app.core.config import assert_no_legacy_texas_settings

    assert_no_legacy_texas_settings()

    # The worker is what actually runs tracking, so it must have the FRC
    # detector. Best-effort download if configured; never blocks startup
    # (the pipeline flags degraded runs when the generic fallback is used).
    try:
        from app.services.vision.model_provisioning import ensure_primary_model_available

        logger.info("worker.model_provisioning %s", ensure_primary_model_available())
    except Exception:
        logger.exception("worker.model_provisioning unexpected failure (continuing)")

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    queue_name = os.getenv("WORKER_QUEUE_NAME", "default")
    connection = redis.from_url(redis_url)
    queue = Queue(queue_name, connection=connection)
    logger.info("Starting worker queue=%s redis=%s", queue.name, redis_url)
    worker = Worker([queue], connection=connection)
    # Enable scheduler so delayed retries and scheduled jobs are actually executed.
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
