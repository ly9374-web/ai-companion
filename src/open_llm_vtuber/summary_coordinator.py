"""Per-client serial execution for background summary work."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass
class _SummaryJob:
    label: str
    run: Callable[[], Awaitable[Any]]
    future: asyncio.Future


class SummaryCoordinator:
    """Run summary requests one at a time without blocking normal chat."""

    def __init__(self) -> None:
        self._jobs: deque[_SummaryJob] = deque()
        self._worker: asyncio.Task | None = None
        self._running_label: str | None = None

    def has_any(self, labels: set[str]) -> bool:
        if self._running_label in labels:
            return True
        return any(job.label in labels for job in self._jobs)

    def has_prefix(self, prefixes: tuple[str, ...]) -> bool:
        if self._running_label and self._running_label.startswith(prefixes):
            return True
        return any(job.label.startswith(prefixes) for job in self._jobs)

    def enqueue(
        self,
        label: str,
        run: Callable[[], Awaitable[Any]],
    ) -> asyncio.Future:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._jobs.append(_SummaryJob(label=label, run=run, future=future))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())
        return future

    async def _run(self) -> None:
        while self._jobs:
            job = self._jobs.popleft()
            self._running_label = job.label
            try:
                result = await job.run()
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.cancel()
                raise
            except Exception as exc:
                logger.exception("Background summary job {} failed: {}", job.label, exc)
                if not job.future.done():
                    job.future.set_result(None)
            else:
                if not job.future.done():
                    job.future.set_result(result)
            finally:
                self._running_label = None

    async def close(self) -> None:
        while self._jobs:
            job = self._jobs.popleft()
            if not job.future.done():
                job.future.cancel()
        if self._worker and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        self._worker = None
