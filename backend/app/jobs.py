"""后台任务：批量检测 / 批量导出。

单进程线程池实现，任务状态放内存（进程重启即丢）。切图是重 CPU 的活，
放在线程里跑（numpy/opencv 会释放 GIL），避免堵住 FastAPI 事件循环。
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import config, store
from .core import export as export_core
from .pipelines import registry


class Job:
    def __init__(self, kind: str, total: int, meta: dict[str, Any] | None = None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.total = total
        self.done = 0
        self.failed = 0
        self.status = "pending"  # pending | running | done | failed | cancelled
        self.message = ""
        self.errors: list[str] = []
        self.result: list[dict[str, Any]] = []
        self.meta = meta or {}
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._lock = threading.Lock()

    def push(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.result.append(record)
            self.done += 1
            self.updated_at = time.time()

    def fail(self, message: str) -> None:
        with self._lock:
            self.failed += 1
            self.errors.append(message)
            self.updated_at = time.time()

    def as_dict(self, include_result: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "failed": self.failed,
            "message": self.message,
            "errors": self.errors[:20],
            "meta": self.meta,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_result:
            data["result"] = self.result
        return data


class JobManager:
    def __init__(self, max_jobs: int = 50) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2)

    def submit(self, job: Job, fn: Callable[[Job], None]) -> Job:
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > 50:
                self._jobs.popitem(last=False)
        self._executor.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: Callable[[Job], None]) -> None:
        job.status = "running"
        try:
            fn(job)
            if job.status != "cancelled":
                job.status = "done"
                job.message = f"完成 {job.done}/{job.total}"
        except Exception as exc:
            job.status = "failed"
            job.message = f"{type(exc).__name__}: {exc}"
            job.errors.append(traceback.format_exc(limit=3))
        finally:
            job.updated_at = time.time()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())


jobs = JobManager()


def run_detect_job(book: str, page_ids: list[str], params: dict[str, Any]) -> Job:
    pipeline = registry.get_pipeline(book)

    def work(job: Job) -> None:
        for page_id in page_ids:
            if job.status == "cancelled":
                return
            try:
                result = pipeline.detect(page_id, params)
                ordered = pipeline.order(result.boxes)
                previous = store.load_boxes(book, page_id) or {}
                width, height = pipeline.page_size(page_id)
                store.save_boxes(
                    book,
                    page_id,
                    ordered,
                    width=width,
                    height=height,
                    extra=result.meta,
                    state=previous.get("state", {}),
                )
                job.push({"page": page_id, "count": len(ordered)})
            except Exception as exc:
                job.fail(f"{page_id}: {type(exc).__name__}: {exc}")

    return jobs.submit(Job("detect", len(page_ids), {"book": book}), work)


def run_export_job(book: str, page_ids: list[str], params: dict[str, Any], out_dir: str | None = None) -> Job:
    pipeline = registry.get_pipeline(book)
    settings = config.load_settings()
    target = Path(out_dir or settings.get("export_dir") or config.EXPORT_DIR_DEFAULT)

    def work(job: Job) -> None:
        digits = pipeline.export_digits()
        for page_id in page_ids:
            if job.status == "cancelled":
                return
            try:
                saved = store.load_boxes(book, page_id)
                if not saved:
                    raise ValueError("该页还没有保存字框，先检测或手动画框")
                from .core.box import Box

                boxes = [Box.from_dict(b) for b in saved["boxes"]]
                ordered = pipeline.order(boxes)
                images = pipeline.export_images(page_id, ordered, params, saved.get("extra", {}))
                records = []
                prefix = pipeline.file_prefix(page_id)
                for idx, (box, image) in enumerate(zip(ordered, images, strict=True), 1):
                    name = f"{prefix}_{idx:0{digits}d}.png"
                    from .core import imaging

                    imaging.save_png(target / name, image)
                    records.append(
                        {
                            "file": str(target / name),
                            "book": book,
                            "page": page_id,
                            "index": idx,
                            "x": box.x,
                            "y": box.y,
                            "w": box.w,
                            "h": box.h,
                            "pos": box.pos,
                        }
                    )
                store.export_log.append(records)
                job.push({"page": page_id, "count": len(records), "dir": str(target)})
            except Exception as exc:
                job.fail(f"{page_id}: {type(exc).__name__}: {exc}")

    return jobs.submit(Job("export", len(page_ids), {"book": book, "out_dir": str(target)}), work)


__all__ = ["Job", "export_core", "jobs", "run_detect_job", "run_export_job"]
