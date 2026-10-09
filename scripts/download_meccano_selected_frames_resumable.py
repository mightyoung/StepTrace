from __future__ import annotations

import argparse
import json
import time
import urllib.request
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from download_meccano_subset import CachedHTTPRangeReader, URL, video_id


def _opener(proxy: str):
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )


def _range(opener, start: int, end: int, *, attempts: int = 8) -> bytes:
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(URL, headers={"Range": f"bytes={start}-{end}"})
            with opener.open(request, timeout=180) as response:
                data = response.read()
            if len(data) != end - start + 1:
                raise IOError(f"short range read: {len(data)} bytes")
            return data
        except (OSError, TimeoutError) as exc:
            if attempt == attempts - 1:
                raise
            print(json.dumps({"retry": attempt + 1, "error": str(exc)}), flush=True)
            time.sleep(min(30, 2 ** attempt))
    raise AssertionError("unreachable")


def _wanted(annotation_root: Path, videos: set[str]) -> set[str]:
    result = set()
    for source in annotation_root.glob("instances_meccano_*.json"):
        document = json.loads(source.read_text(encoding="utf-8"))
        result.update(
            Path(image["file_name"]).name
            for image in document["images"]
            if video_id(image["file_name"]) in videos
        )
    return result


def _build_index(cache: Path, proxy: str, wanted: set[str]) -> list[dict[str, int | str]]:
    remote = CachedHTTPRangeReader(URL, proxy=proxy, timeout=180)
    unique = {}
    with zipfile.ZipFile(remote) as archive:
        for member in archive.infolist():
            basename = PurePosixPath(member.filename).name
            if not member.is_dir() and basename in wanted:
                unique.setdefault(
                    basename,
                    {
                        "name": basename,
                        "header_offset": member.header_offset,
                        "compress_size": member.compress_size,
                        "file_size": member.file_size,
                        "compress_type": member.compress_type,
                        "crc": member.CRC,
                    },
                )
    missing = wanted - set(unique)
    if missing:
        raise FileNotFoundError(f"archive is missing {len(missing)} requested frames")
    entries = list(unique.values())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return entries


def _decode_entry(opener, entry: dict[str, int | str]) -> bytes:
    offset = int(entry["header_offset"])
    header = _range(opener, offset, offset + 29)
    if header[:4] != b"PK\x03\x04":
        raise ValueError(f"invalid local ZIP header for {entry['name']}")
    name_length = int.from_bytes(header[26:28], "little")
    extra_length = int.from_bytes(header[28:30], "little")
    data_start = offset + 30 + name_length + extra_length
    compressed = _range(opener, data_start, data_start + int(entry["compress_size"]) - 1)
    method = int(entry["compress_type"])
    if method == zipfile.ZIP_STORED:
        data = compressed
    elif method == zipfile.ZIP_DEFLATED:
        data = zlib.decompress(compressed, -zlib.MAX_WBITS)
    else:
        raise ValueError(f"unsupported ZIP compression method {method}")
    if len(data) != int(entry["file_size"]):
        raise IOError(f"size mismatch for {entry['name']}")
    if zlib.crc32(data) & 0xFFFFFFFF != int(entry["crc"]):
        raise IOError(f"CRC mismatch for {entry['name']}")
    return data


def _replace(part: Path, target: Path) -> None:
    for attempt in range(20):
        try:
            part.replace(target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumable selective MECCANO frame downloader")
    parser.add_argument("--video", action="append", required=True)
    parser.add_argument("--annotation-root", default="datasets/MECCANO/MECCANO_active_objects_annotations")
    parser.add_argument("--output", default="datasets/MECCANO/active_object_frames_subset")
    parser.add_argument("--proxy", default="http://127.0.0.1:10808")
    parser.add_argument("--index-cache", default="datasets/MECCANO/meccano-selected-frame-index.json")
    args = parser.parse_args()
    videos = {value.lstrip("0").zfill(2) for value in args.video}
    wanted = _wanted(Path(args.annotation_root), videos)
    cache = Path(args.index_cache)
    if cache.exists():
        entries = json.loads(cache.read_text(encoding="utf-8"))
        if {entry["name"] for entry in entries} != wanted:
            entries = _build_index(cache, args.proxy, wanted)
    else:
        entries = _build_index(cache, args.proxy, wanted)
    print(json.dumps({"videos": sorted(videos), "frames": len(entries)}, ensure_ascii=False), flush=True)

    opener = _opener(args.proxy)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    for number, entry in enumerate(entries, 1):
        target = output / str(entry["name"])
        if target.exists() and target.stat().st_size == int(entry["file_size"]):
            continue
        data = _decode_entry(opener, entry)
        part = target.with_suffix(target.suffix + ".part")
        part.write_bytes(data)
        _replace(part, target)
        if number % 100 == 0 or number == len(entries):
            print(json.dumps({"completed": number, "total": len(entries)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
