"""手工对齐表：建表、存盘版本校验、导出 TSV。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from app import sheets, store


def _png(path: Path) -> None:
    import cv2

    img = np.full((40, 40), 255, np.uint8)
    img[10:30, 10:30] = 0
    ok, buf = cv2.imencode(".png", img)
    assert ok
    path.write_bytes(buf.tobytes())


@pytest.fixture()
def img_dir(work_dir: Path) -> Path:
    folder = work_dir / "imgs"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("p10.png", "p2.png", "p1.png"):
        _png(folder / name)
    (folder / "readme.txt").write_text("忽略非图片", encoding="utf-8")
    return folder


def test_dir_import_sorted_naturally(img_dir: Path):
    sheet = sheets.create_sheet(folder=str(img_dir), texts="甲\n乙\n丙")
    names = [Path(ref["path"]).name for ref in sheet["images"]]
    assert names == ["p1.png", "p2.png", "p10.png"]
    assert [r["text"] for r in sheet["rows"]] == ["甲", "乙", "丙"]
    assert [r["img"] for r in sheet["rows"]] == [0, 1, 2]


def test_pasted_tsv_picks_the_text_column(img_dir: Path):
    """认本工具导出的 4 列（号/图/文本/备注）和 `号\t文本` 两列。"""
    exported = "1\tp1.png\t甲\t\n2\tp2.png\t乙\t\n"
    assert [r["text"] for r in sheets.create_sheet(folder=str(img_dir), texts=exported)["rows"][:2]] == [
        "甲",
        "乙",
    ]

    two_col = "1\t丙\n2\t丁\n"
    assert [r["text"] for r in sheets.create_sheet(folder=str(img_dir), texts=two_col)["rows"][:2]] == [
        "丙",
        "丁",
    ]

    plain = "戊\n己\n"
    assert [r["text"] for r in sheets.create_sheet(folder=str(img_dir), texts=plain)["rows"][:2]] == [
        "戊",
        "己",
    ]


def test_save_bumps_rev_and_rejects_stale(img_dir: Path):
    sheet = sheets.create_sheet(folder=str(img_dir), texts="甲\n乙\n丙")
    assert sheet["rev"] == 1  # 建表时已经写过一次

    second = sheets.save_sheet(sheet, base_rev=sheet["rev"])
    assert second["rev"] == 2

    # 拿旧版本号再写就是冲突（别处已经改过这张表）
    with pytest.raises(store.RevisionConflict):
        sheets.save_sheet(sheet, base_rev=1)


def test_export_tsv_writes_rows(img_dir: Path):
    sheet = sheets.create_sheet(folder=str(img_dir), texts="甲\n乙\n丙")
    sheet["rows"][1]["img"] = None
    sheet["rows"][1]["note"] = "缺图"
    result = sheets.export_tsv(sheet, str(Path("exports") / "t.tsv"))
    text = Path(result["path"]).read_text(encoding="utf-8").strip().splitlines()
    assert text[0] == "#\timg\ttext\tnote"
    assert text[1].startswith("1\tp1.png\t甲")
    assert text[2].split("\t")[1] == ""  # 解绑后留空
    assert result["rows"] == 3


def test_tsv_roundtrip_keeps_bindings_and_notes(img_dir: Path):
    """自己导出的 TSV 能原样读回来：图绑定（含解绑）与备注都不能丢。"""
    sheet = sheets.create_sheet(folder=str(img_dir), texts="甲\n乙\n丙")
    sheet["rows"][1]["img"] = None
    sheet["rows"][1]["note"] = "手工解绑"
    sheets.save_sheet(sheet, base_rev=sheet["rev"])

    out = sheets.export_tsv(sheet, str(Path("exports") / "rt.tsv"))
    back = sheets.create_sheet(folder=str(img_dir), tsv_path="exports/rt.tsv")
    assert out["rows"] == 3
    assert [(r["img"], r["text"], r["note"]) for r in back["rows"]] == [
        (0, "甲", ""),
        (None, "乙", "手工解绑"),
        (2, "丙", ""),
    ]
    assert back["source"]["tsv"].endswith("rt.tsv")
