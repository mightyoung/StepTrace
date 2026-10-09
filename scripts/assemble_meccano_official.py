"""Safely assemble and validate the official MECCANO RGB archive."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "MECCANO"
OFFICIAL = DATA / "official"
TOTAL = 11_920_575_383
PREFIX_END = 6_900_622_112
RANGES = [
    (DATA / "download-parts", PREFIX_END, 10_105_070_368),
    (DATA / "download-parts-shard3", 10_105_070_368, 10_633_552_672),
    (DATA / "download-parts-shard2", 10_633_552_672, TOTAL),
]


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verified_segments() -> list[pathlib.Path]:
    prefix = next(DATA.glob("*.crdownload"))
    if prefix.stat().st_size != PREFIX_END:
        raise RuntimeError("prefix size mismatch")
    print(
        json.dumps(
            {"prefix": prefix.name, "size": PREFIX_END, "sha256": sha256(prefix)},
            ensure_ascii=False,
        )
    )
    segments = [prefix]
    all_parts: list[tuple[int, int, pathlib.Path]] = []
    for folder, start, end in RANGES:
        index = json.loads((folder / "download-index.json").read_text(encoding="utf-8"))
        parts: list[tuple[int, int, pathlib.Path]] = []
        for item in index["parts"].values():
            offset, inclusive_end = int(item["offset"]), int(item["end"])
            path = folder / item["file"]
            expected = inclusive_end - offset + 1
            if (
                item.get("status") != "downloaded"
                or not path.is_file()
                or path.stat().st_size != expected
            ):
                raise RuntimeError(f"invalid segment {folder}/{path.name}")
            if sha256(path) != item["sha256"]:
                raise RuntimeError(f"segment hash mismatch {path}")
            parts.append((offset, inclusive_end, path))
        parts.sort()
        cursor = start
        for offset, inclusive_end, path in parts:
            if offset != cursor:
                raise RuntimeError(f"gap/overlap in {folder}: {cursor} != {offset}")
            cursor = inclusive_end + 1
            all_parts.append((offset, inclusive_end, path))
        if cursor != end:
            raise RuntimeError(f"range end mismatch in {folder}: {cursor} != {end}")
    all_parts.sort()
    cursor = PREFIX_END
    for offset, inclusive_end, path in all_parts:
        if offset != cursor:
            raise RuntimeError(f"global gap/overlap: {cursor} != {offset}")
        segments.append(path)
        cursor = inclusive_end + 1
    if cursor != TOTAL:
        raise RuntimeError(f"global end mismatch: {cursor} != {TOTAL}")
    print(
        json.dumps(
            {"segments": len(segments), "coverage_end": cursor}, ensure_ascii=False
        )
    )
    return segments


def assemble(segments: list[pathlib.Path]) -> pathlib.Path:
    OFFICIAL.mkdir(parents=True, exist_ok=True)
    target = OFFICIAL / "MECCANO_RGB_Videos.zip"
    if target.exists() and target.stat().st_size:
        return target
    assembling = OFFICIAL / "MECCANO_RGB_Videos.zip.assembling"
    with assembling.open("wb") as out:
        for segment in segments:
            with segment.open("rb") as source:
                shutil.copyfileobj(source, out, length=8 * 1024 * 1024)
        out.flush()
        os.fsync(out.fileno())
    if assembling.stat().st_size != TOTAL:
        raise RuntimeError("assembled size mismatch")
    os.replace(assembling, target)
    return target


def main() -> None:
    segments = verified_segments()
    archive = assemble(segments)
    archive_hash = sha256(archive)
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"CRC failure: {bad}")
        entries = [
            {"name": i.filename, "size": i.file_size, "crc": f"{i.CRC:08x}"}
            for i in zf.infolist()
        ]
    print(
        json.dumps(
            {
                "archive": str(archive),
                "size": archive.stat().st_size,
                "sha256": archive_hash,
                "entries": len(entries),
            },
            ensure_ascii=False,
        )
    )
    inventory = OFFICIAL / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "source_url": "https://iplab.dmi.unict.it/sharing/MECCANO/MECCANO_RGB_Videos.zip",
                "zip": {"size": archive.stat().st_size, "sha256": archive_hash},
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
