"""Celery application for async LLM tasks.

Enabled only when CELERY_ENABLED=true (default: false).
When disabled, all imports from this module are no-ops or stubs — the
rest of the codebase calls celery_app.send_llm_task() which falls back
to a synchronous call if Celery is not running.

Usage:
    # Start worker (in a separate terminal):
    celery -A celery_app worker --loglevel=info --concurrency=4

    # Or via run.sh when CELERY_ENABLED=true (automatically started)
"""

from __future__ import annotations

import config

_CELERY_ENABLED = getattr(config, "CELERY_ENABLED", False)
_REDIS_URL = getattr(config, "REDIS_URL", "redis://localhost:6379/0")

celery = None  # type: ignore

if _CELERY_ENABLED:
    try:
        from celery import Celery  # type: ignore

        celery = Celery(
            "drishi",
            broker=_REDIS_URL,
            backend=_REDIS_URL,
        )
        celery.conf.update(
            task_serializer="json",
            result_serializer="json",
            accept_content=["json"],
            task_track_started=True,
            task_acks_late=True,
            worker_prefetch_multiplier=1,
            result_expires=300,
        )
        print(f"[CELERY] Worker ready — broker={_REDIS_URL}")
    except ImportError:
        print("[CELERY] celery package not installed — running synchronously")
        celery = None
else:
    pass  # Celery disabled — all tasks run synchronously


# ── Task definitions ───────────────────────────────────────────────────────────

if celery is not None:
    @celery.task(bind=True, max_retries=2, default_retry_delay=1)
    def llm_interview_task(self, question: str, resume_text: str = "",
                           job_description: str = "", active_user_context: str = "",
                           session_id: str = None):
        """Async LLM interview answer task.
        Result is published to Redis pub/sub so SSE can pick it up.
        """
        try:
            import llm_client
            import redis as _redis  # type: ignore

            r = _redis.from_url(_REDIS_URL)
            channel = f"drishi:answer:{question[:40]}"

            full = "-"
            for chunk in llm_client.get_streaming_interview_answer(
                question, resume_text, job_description, active_user_context,
                session_id=session_id
            ):
                if chunk:
                    full += chunk
                    r.publish(channel, chunk)
            r.publish(channel, "__DONE__")
            return full
        except Exception as exc:
            raise self.retry(exc=exc)


def send_llm_task(question: str, resume_text: str = "", job_description: str = "",
                  active_user_context: str = "", session_id: str = None):
    """Dispatch an LLM task to Celery if enabled, otherwise run inline.
    Returns a channel name for Redis pub/sub (or None for inline mode).
    """
    if celery is None:
        # Synchronous fallback
        import llm_client
        return None, llm_client.get_streaming_interview_answer(
            question, resume_text, job_description, active_user_context,
            session_id=session_id
        )

    channel = f"drishi:answer:{question[:40]}"
    llm_interview_task.delay(
        question, resume_text, job_description, active_user_context, session_id
    )
    return channel, None
