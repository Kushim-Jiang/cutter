"""路径与配置。

本地盘路径（数据仓库、各字典的扫描 PDF）**不写进代码、也不提交**，
放在仓库根目录的 `config.local.yaml`（已在 `.gitignore` 里）。
`config.example.yaml` 是提交的模板。

配置优先级（从高到低）：

1. `backend/var/settings.json`——运行时从界面改的覆盖值（最后合并，最高）
2. 环境变量（`CUTTER_DATA_DIR` / `CUTTER_ZIHAI_PDF` / …）
3. `config.local.yaml`（`$CUTTER_CONFIG` 可指定别处）
4. 代码里的内置默认（只含结构，不含任何本地盘路径）
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value else default


def _load_local() -> dict[str, Any]:
    """读 `config.local.yaml`（不提交）。没有就返回空 dict。"""
    candidates = []
    custom = _env("CUTTER_CONFIG")
    if custom:
        candidates.append(Path(custom).expanduser())
    candidates += [REPO_ROOT / "config.local.yaml", BACKEND_ROOT / "config.local.yaml"]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            data["_path"] = str(path)
            return data
    return {}


LOCAL = _load_local()


def _path_value(raw: Any, default: Path) -> Path:
    """yaml 里的路径：留空用默认，相对路径按仓库根解释。"""
    if not raw:
        return default.resolve()
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _resolve_data_dir() -> Path:
    env = _env("CUTTER_DATA_DIR") or _env("YEDICT_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if LOCAL.get("data_dir"):
        return _path_value(LOCAL["data_dir"], REPO_ROOT.parent / "yedict-data")
    # 仓库同级目录是最常见的摆放方式，不再猜任何具体盘符
    return (REPO_ROOT.parent / "yedict-data").resolve()


DATA_DIR = _resolve_data_dir()
WORK_DIR = (
    Path(_env("CUTTER_WORK_DIR") or _path_value(LOCAL.get("work_dir"), BACKEND_ROOT / "var"))
    .expanduser()
    .resolve()
)
SETTINGS_FILE = WORK_DIR / "settings.json"
BOX_DIR = WORK_DIR / "boxes"
CACHE_DIR = WORK_DIR / "cache"
EXPORT_DIR_DEFAULT = WORK_DIR / "export"

for _d in (WORK_DIR, BOX_DIR, CACHE_DIR, EXPORT_DIR_DEFAULT):
    _d.mkdir(parents=True, exist_ok=True)


def _code_defaults() -> dict[str, Any]:
    """内置默认：只有结构和页码，路径一律留空（由 config.local.yaml 提供）。"""
    return {
        "data_dir": str(DATA_DIR),
        "export_dir": str(EXPORT_DIR_DEFAULT),
        "preview_width": 2200,
        "gap": 2,
        "use_seed": True,
        "cache": {"page_arrays": 4},
        "books": {
            "zihai": {
                "pdf": "",
                "first_page": 84,
                "last_page": 1864,
                "offset": 83,
                "volumes": {
                    "1": {
                        "title": "卷 1",
                        "pdf": "",
                        "first_page": 84,
                        "last_page": 1864,
                        "offset": 83,
                    },
                    "2": {
                        "title": "卷 2",
                        "pdf": "",
                        "first_page": 80,
                        "last_page": 1860,
                        "offset": 79,
                        "box_margin": 2,
                    },
                },
            },
            "hzh": {"pdf": "", "first_page": 452, "last_page": 2648, "offset": 451},
            "kx": {"book_dir": "", "group_file": ""},
            "hd": {
                "pdf": "",
                "first_page": 297,
                "last_page": 5106,
                "offset": 296,
                # 上游 hd1-heads.ndjson 是 300dpi 坐标，管线按同一分辨率渲染才能直接套用
                "dpi": 300,
                "volumes": {
                    "1": {
                        "title": "第一版",
                        "label": "第一版",
                        "pdf": "",
                        "first_page": 297,
                        "last_page": 5106,
                        "offset": 296,
                    },
                    "2": {
                        "title": "第二版",
                        "label": "第二版",
                        "pdf": "",
                        "first_page": 1,
                        "last_page": 5127,
                        "offset": 0,
                    },
                },
            },
        },
    }


def _with_data_dir(books: dict[str, Any]) -> dict[str, Any]:
    """康熙的数据在上游仓库里，没配就按 DATA_DIR 推。"""
    kx = books.get("kx") or {}
    if not kx.get("book_dir"):
        kx["book_dir"] = str(DATA_DIR / "_up" / "kangxi" / "book")
    if not kx.get("group_file"):
        kx["group_file"] = str(DATA_DIR / "_up" / "kangxi" / "group-simch-kx-KangXiDict.txt")
    books["kx"] = kx
    return books


_ENV_PDF = {
    ("zihai", None): "CUTTER_ZIHAI_PDF",
    ("hzh", None): "CUTTER_HZH_PDF",
    ("hd", None): "CUTTER_HD_PDF",
    ("zihai", "2"): "CUTTER_ZIHAI2_PDF",
    ("hd", "1"): "CUTTER_HD1_PDF",
    ("hd", "2"): "CUTTER_HD2_PDF",
}


def _apply_env(books: dict[str, Any]) -> None:
    """环境变量覆盖 config.local.yaml（临时切换书源用）。"""
    for (book, vol), name in _ENV_PDF.items():
        value = _env(name)
        if not value:
            continue
        node = books.get(book)
        if not isinstance(node, dict):
            continue
        if vol is None:
            node["pdf"] = value
            continue
        volumes = node.get("volumes")
        if isinstance(volumes, dict) and isinstance(volumes.get(vol), dict):
            volumes[vol]["pdf"] = value
    kx_env = _env("CUTTER_KX_BOOK_DIR")
    if kx_env and isinstance(books.get("kx"), dict):
        books["kx"]["book_dir"] = kx_env
    kx_group = _env("CUTTER_KX_GROUP")
    if kx_group and isinstance(books.get("kx"), dict):
        books["kx"]["group_file"] = kx_group


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if key == "volumes" and isinstance(value, dict):
            # 分卷是完整配置：用户给的 volumes 直接替换，方便删掉默认的某一卷
            out[key] = value
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def default_settings() -> dict[str, Any]:
    """内置默认 + `config.local.yaml` + 环境变量。"""
    settings = _code_defaults()
    yaml_books = LOCAL.get("books")
    if isinstance(yaml_books, dict) and yaml_books:
        settings["books"] = _deep_merge(settings["books"], yaml_books)
    for key in ("preview_width", "gap", "use_seed", "export_dir"):
        if key in LOCAL:
            settings[key] = LOCAL[key]
    settings["books"] = _with_data_dir(settings["books"])
    _apply_env(settings["books"])
    settings["data_dir"] = str(DATA_DIR)
    return settings


def load_settings() -> dict[str, Any]:
    settings = default_settings()
    if SETTINGS_FILE.exists():
        try:
            stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        if isinstance(stored, dict):
            settings = _deep_merge(settings, stored)
    return settings


def save_settings(partial: dict[str, Any]) -> dict[str, Any]:
    """把用户改动合并进 settings.json，返回合并后的完整设置。"""
    current: dict[str, Any] = {}
    if SETTINGS_FILE.exists():
        try:
            current = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
    merged = _deep_merge(current, partial)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_settings()


def book_setting(book_id: str) -> dict[str, Any]:
    return load_settings()["books"].get(book_id, {})


def config_source() -> str:
    """当前生效的本地配置来源（排查用）。"""
    return str(LOCAL.get("_path") or "")
