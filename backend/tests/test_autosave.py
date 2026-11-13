"""自动落盘的安全网：并发写、版本校验、尺寸不被刷成 0。

这些用例针对的是「请求连得太紧」时真正会丢数据的几条路径：
固定 `.tmp` 名互相截断、读-改-写交错吃掉 state、切页后落盘把 width/height 刷成 0。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from app import store
from app.core.box import Box
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("grid_pdf")


def _boxes(n: int) -> list[Box]:
    return [Box(x=i * 10, y=i * 10, w=20, h=20, seq=i + 1) for i in range(n)]


def test_concurrent_saves_keep_all_data(work_dir: Path):
    """8 个线程同时写同一页：最终文件必须是完整 JSON，且 rev 恰好加了 8。"""
    page = "concurrent-1"
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            store.save_boxes("hd", page, _boxes(i + 1), width=400, height=600, autosave=True)
        except BaseException as exc:  # 线程里要把异常带回主线程
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    data = store.load_boxes("hd", page)
    assert data is not None
    assert data["rev"] == 8
    assert 1 <= len(data["boxes"]) <= 8  # 最后一次写入者的内容
    # 文件没有被截断：能完整读回 JSON
    path = store._page_file("hd", page)
    json.loads(path.read_text(encoding="utf-8"))


def test_state_merge_not_lost_under_concurrency(work_dir: Path):
    """并发保存时 state 的合并不能被别的写入者覆盖掉。"""
    page = "concurrent-state"
    store.save_boxes("hd", page, _boxes(1), width=400, height=600)

    done = threading.Barrier(4)

    def mark(i: int) -> None:
        done.wait()
        store.save_boxes(
            "hd",
            page,
            _boxes(2),
            width=400,
            height=600,
            state={f"k{i}": True},
            autosave=True,
        )

    threads = [threading.Thread(target=mark, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    state = (store.load_boxes("hd", page) or {}).get("state", {})
    assert all(state.get(f"k{i}") for i in range(4))


def test_stale_base_rev_is_rejected(work_dir: Path):
    page = "rev-check"
    first = store.save_boxes("hd", page, _boxes(1), width=400, height=600)
    assert first["rev"] == 1

    with pytest.raises(store.RevisionConflict) as exc:
        store.save_boxes("hd", page, _boxes(2), width=400, height=600, base_rev=0)
    assert exc.value.rev == 1
    # 冲突时磁盘内容不动
    assert len((store.load_boxes("hd", page) or {})["boxes"]) == 1

    ok = store.save_boxes("hd", page, _boxes(2), width=400, height=600, base_rev=1)
    assert ok["rev"] == 2


def test_size_preserved_when_client_omits_it(work_dir: Path):
    """切页后才落盘的请求拿不到 meta，width/height 传 0 时不能覆盖原值。"""
    page = "size-keep"
    store.save_boxes("hd", page, _boxes(1), width=400, height=600)
    store.save_boxes("hd", page, _boxes(2), width=0, height=0, autosave=True)
    data = store.load_boxes("hd", page) or {}
    assert (data["width"], data["height"], len(data["boxes"])) == (400, 600, 2)


def test_autosave_throttles_history_but_manual_always_snapshots(work_dir: Path):
    page = "snapshot-throttle"
    store.save_boxes("hd", page, _boxes(1), width=400, height=600)
    before = len(store.list_history("hd", page))

    for n in range(2, 6):
        store.save_boxes("hd", page, _boxes(n), width=400, height=600, autosave=True)
    # 30 秒内的自动落盘不再刷历史
    assert len(store.list_history("hd", page)) == before

    store.save_boxes("hd", page, _boxes(9), width=400, height=600)
    assert len(store.list_history("hd", page)) == before + 1


def test_put_boxes_conflict_returns_409(client: TestClient):
    page = "1:1"
    base = client.get(f"/api/books/hd/pages/{page}/boxes").json()
    rev = base.get("rev", 0)

    ok = client.put(
        f"/api/books/hd/pages/{page}/boxes",
        json={"boxes": [{"x": 1, "y": 2, "w": 30, "h": 30}], "width": 400, "height": 600, "base_rev": rev},
    )
    assert ok.status_code == 200
    assert ok.json()["rev"] == rev + 1

    stale = client.put(
        f"/api/books/hd/pages/{page}/boxes",
        json={"boxes": [], "base_rev": rev},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["conflict"] is True
