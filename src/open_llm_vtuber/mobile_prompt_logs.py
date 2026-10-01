"""Temporary, account-scoped snapshots of mobile user prompts and background
summarizer jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock, Timer
from time import time
from uuid import uuid4


LOG_LIFETIME_SECONDS = 24 * 60 * 60
_lock = RLock()
_buckets: dict[tuple[str, str, str], dict] = {}

# Mobile log category -> background summarizer job kinds it exposes.
JOB_KINDS_BY_CATEGORY: dict[str, frozenset[str]] = {
    "short": frozenset({"rolling", "short_term_relationship"}),
    "long": frozenset({"long_term_memory"}),
    "score": frozenset({"current_relationship_score"}),
}
# 本轮输入及其它拼接内容、本轮 RAG 召回，外加上面三类后台任务。
LOG_CATEGORIES: frozenset[str] = frozenset({"input", "rag"}) | frozenset(
    JOB_KINDS_BY_CATEGORY
)


def _expire(key: tuple[str, str, str], expires_at: float) -> None:
    with _lock:
        bucket = _buckets.get(key)
        if bucket and bucket["expires_at"] == expires_at:
            _buckets.pop(key, None)


def _bucket_for(key: tuple[str, str, str], now: float) -> dict:
    """Return the live bucket for one account/character/history, creating it
    with a 24-hour expiry timer when missing or stale."""
    bucket = _buckets.get(key)
    if bucket is None or bucket["expires_at"] <= now:
        expires_at = now + LOG_LIFETIME_SECONDS
        bucket = {"expires_at": expires_at, "entries": [], "jobs": []}
        _buckets[key] = bucket
        timer = Timer(LOG_LIFETIME_SECONDS, _expire, args=(key, expires_at))
        timer.daemon = True
        timer.start()
    return bucket


def record_prompt(
    account: str,
    character: str,
    history: str,
    parts: dict[str, str | bool | list[str]],
) -> None:
    """Keep only this 24-hour window in process memory, never on disk."""
    if not account or not character or not history:
        return
    key = (account, character, history)
    with _lock:
        bucket = _bucket_for(key, time())
        bucket["entries"].append(
            {
                "id": uuid4().hex,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "parts": parts.copy(),
            }
        )


def record_summary_job(
    account: str,
    character: str,
    history: str,
    *,
    kind: str,
    label: str,
    system_prompt: str,
    input_text: str,
    output_text: str,
) -> None:
    """Keep one background summarizer's prompt/input/output for the log panel."""
    if not account or not character or not history:
        return
    key = (account, character, history)
    with _lock:
        bucket = _bucket_for(key, time())
        bucket["jobs"].append(
            {
                "id": uuid4().hex,
                "kind": kind,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "label": label,
                "system_prompt": system_prompt or "",
                "input": input_text or "",
                "output": output_text or "",
            }
        )


def _format_job(job: dict) -> str:
    sections = [f"【任务】{job.get('label') or job.get('kind', '')}"]
    if job.get("system_prompt"):
        sections.append(f"【系统提示】\n{job['system_prompt']}")
    sections.append(f"【输入素材】\n{job.get('input', '')}")
    sections.append(f"【输出】\n{job.get('output', '')}")
    return "\n\n".join(sections)


def read_prompts(
    account: str,
    character: str,
    history: str,
    categories: set[str],
) -> dict:
    key = (account, character, history)
    now = time()
    with _lock:
        bucket = _buckets.get(key)
        if bucket and bucket["expires_at"] <= now:
            _buckets.pop(key, None)
            bucket = None
        expires_at = bucket["expires_at"] if bucket else None
        snapshots = list(bucket["entries"]) if bucket else []
        jobs = list(bucket.get("jobs", [])) if bucket else []

    wanted_kinds: set[str] = set()
    for category in categories:
        wanted_kinds |= JOB_KINDS_BY_CATEGORY.get(category, set())

    entries: list[dict] = []
    for snapshot in snapshots:
        parts = snapshot["parts"]
        if "input" in categories:
            entries.append(
                {
                    "id": f"{snapshot['id']}:input",
                    "created_at": snapshot["created_at"],
                    "prompt": parts.get("request_text", ""),
                }
            )
        if "rag" in categories:
            retrieved = parts.get("rag_retrieved", [])
            entries.append(
                {
                    "id": f"{snapshot['id']}:rag",
                    "created_at": snapshot["created_at"],
                    "prompt": "\n\n".join(
                        f"{index}. {content}"
                        for index, content in enumerate(retrieved, start=1)
                        if isinstance(content, str) and content.strip()
                    ),
                }
            )
    for job in jobs:
        if job.get("kind") not in wanted_kinds:
            continue
        entries.append(
            {
                "id": job["id"],
                "created_at": job["created_at"],
                "prompt": _format_job(job),
            }
        )

    entries.sort(key=lambda item: item["created_at"])
    return {"entries": entries, "expires_at": expires_at}
