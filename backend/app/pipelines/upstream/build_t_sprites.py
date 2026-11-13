#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build T-series glyph sprites from the official CNS11643 TW-Sung font archive."""

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TILE = 128
_COLS = 32
_ROWS = 64
_PER_SHEET = _COLS * _ROWS
_FONT_NAMES = (
    "TW-Sung.ttf",
    "TW-Sung-Ext-B.ttf",
    "TW-Sung-Ext-G.ttf",
    "TW-Sung-Plus.ttf",
)


def _extract_fonts(archive_path: str, font_dir: str) -> list[str]:
    os.makedirs(font_dir, exist_ok=True)
    paths = []
    with zipfile.ZipFile(archive_path) as archive:
        members = {os.path.basename(name): name for name in archive.namelist() if name.lower().endswith(".ttf")}
        for font_name in _FONT_NAMES:
            member = members.get(font_name)
            if not member:
                raise ValueError(f"Font archive does not contain {font_name}")
            path = os.path.join(font_dir, font_name)
            if not os.path.isfile(path):
                with archive.open(member) as source, open(path, "wb") as target:
                    shutil.copyfileobj(source, target)
            paths.append(path)
    return paths


def _read_t_records(data_dir: str) -> list[dict]:
    dict_dir = os.path.join(data_dir, "dict")
    records = []
    for name in sorted(os.listdir(dict_dir)):
        if not (name.startswith("T") and name.endswith(".ndjson")):
            continue
        with open(os.path.join(dict_dir, name), "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                if "unicode_cp" in record:
                    records.append(record)
    records.sort(key=lambda record: record["ziid"])
    if not records:
        raise ValueError(f"No T-series records found in {dict_dir}")
    return records


def _font_coverage(font_paths: list[str]):
    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:
        raise RuntimeError("fontTools is required; install it with `python -m pip install fonttools`") from exc

    result = []
    for path in font_paths:
        font = TTFont(path, lazy=True)
        try:
            cmap = font.getBestCmap()
            result.append((path, set(cmap or {})))
        finally:
            font.close()
    return result


def build(data_dir: str, source_dir: str, font_dir: str, force: bool) -> tuple[int, int]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Pillow is required; install it with `python -m pip install pillow`") from exc

    records = _read_t_records(data_dir)
    font_archive = os.path.join(source_dir, "Fonts_Sung.zip")
    if not os.path.isfile(font_archive):
        raise FileNotFoundError(f"Official font archive not found: {font_archive}")
    font_paths = _extract_fonts(font_archive, font_dir)
    coverage = _font_coverage(font_paths)

    cps = []
    missing = []
    for record in records:
        cp = int(record["unicode_cp"], 16)
        cps.append(cp)
        if not any(cp in supported for _, supported in coverage):
            missing.append((record["ziid"], record["unicode_cp"]))
    if missing:
        raise ValueError(f"{len(missing)} T records lack a font glyph; first: {missing[:5]}")

    out_dir = os.path.join(data_dir, "sprites_t")
    if os.path.exists(out_dir) and not force:
        raise FileExistsError("sprites_t already exists; pass --force to replace it")
    stage = tempfile.mkdtemp(prefix=".sprites_t-", dir=data_dir)
    manifest_path = os.path.join(stage, "manifest.ndjson")
    sheets_dir = os.path.join(stage, "sheets")
    os.makedirs(sheets_dir)
    font_cache = {}
    manifest_count = 0
    sheet_count = 0

    try:
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as manifest:
            current_shard = None
            sheet_index = 0
            local_index = 0
            image = draw = None
            for record, cp in zip(records, cps):
                shard = record["ziid"][:2]
                if shard != current_shard:
                    if image is not None:
                        sheet_name = f"{current_shard}_{sheet_index:02d}.png"
                        image.save(os.path.join(sheets_dir, sheet_name), format="PNG", optimize=True)
                        sheet_count += 1
                    current_shard = shard
                    sheet_index = 0
                    local_index = 0
                elif local_index == _PER_SHEET:
                    sheet_name = f"{shard}_{sheet_index:02d}.png"
                    image.save(os.path.join(sheets_dir, sheet_name), format="PNG", optimize=True)
                    sheet_count += 1
                    sheet_index += 1
                    local_index = 0

                col = local_index % _COLS
                row = local_index // _COLS
                sheet_name = f"{shard}_{sheet_index:02d}.png"

                if local_index == 0:
                    image = Image.new("L", (_TILE * _COLS, _TILE * _ROWS), 255)
                    draw = ImageDraw.Draw(image)

                font_path = next(path for path, supported in coverage if cp in supported)
                font = font_cache.get(font_path)
                if font is None:
                    font = ImageFont.truetype(font_path, 112)
                    font_cache[font_path] = font
                char = chr(cp)
                left, top, right, bottom = font.getbbox(char)
                width, height = right - left, bottom - top
                x = col * _TILE + (_TILE - width) // 2 - left
                y = row * _TILE + (_TILE - height) // 2 - top
                draw.text((x, y), char, font=font, fill=0)

                entry = {
                    "ziid": record["ziid"],
                    "sheet": sheet_name,
                    "x": col * _TILE,
                    "y": row * _TILE,
                    "w": _TILE,
                    "h": _TILE,
                }
                manifest.write(json.dumps(entry, separators=(",", ":")) + "\n")
                manifest_count += 1
                local_index += 1

            if image is not None:
                sheet_name = f"{current_shard}_{sheet_index:02d}.png"
                image.save(os.path.join(sheets_dir, sheet_name), format="PNG", optimize=True)
                sheet_count += 1

        if os.path.exists(out_dir):
            shutil.rmtree(out_dir)
        os.replace(stage, out_dir)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    return manifest_count, sheet_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Build CNS11643 T-series font sprites")
    parser.add_argument("--data-dir", default=_REPO_ROOT)
    parser.add_argument("--source-dir", default=os.path.join(_REPO_ROOT, "_up", "cns11643"))
    parser.add_argument("--font-dir", default=None, help="directory for extracted official fonts")
    parser.add_argument("--force", action="store_true", help="replace the existing sprites_t directory")
    args = parser.parse_args()
    font_dir = args.font_dir or os.path.join(args.source_dir, "fonts")

    try:
        entries, sheets = build(args.data_dir, args.source_dir, font_dir, args.force)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"T sprite generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Generated {entries:,} T glyphs across {sheets} sheets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
