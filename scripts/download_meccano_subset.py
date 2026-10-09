from __future__ import annotations

import argparse
import io
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


URL = "https://iplab.dmi.unict.it/sharing/MECCANO/MECCANO_active_object_frames.zip"


class CachedHTTPRangeReader(io.RawIOBase):
    def __init__(self, url: str, proxy: str, timeout: int = 90) -> None:
        self.url, self.timeout = url, timeout
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        with self.opener.open(urllib.request.Request(url, method="HEAD"), timeout=timeout) as res:
            self.length = int(res.headers["Content-Length"])
            if "bytes" not in res.headers.get("Accept-Ranges", "").casefold():
                raise RuntimeError("MECCANO server does not support HTTP Range")
        self.position, self.cache_start, self.cache = 0, 0, b""

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self.position + offset
        elif whence == io.SEEK_END:
            position = self.length + offset
        else:
            raise ValueError(f"invalid whence: {whence}")
        if position < 0:
            raise ValueError("negative seek")
        self.position = min(position, self.length)
        return self.position

    def read(self, size: int = -1) -> bytes:
        if self.position >= self.length:
            return b""
        requested = self.length - self.position if size is None or size < 0 else size
        offset = self.position - self.cache_start
        if 0 <= offset and offset + requested <= len(self.cache):
            data = self.cache[offset : offset + requested]
            self.position += len(data)
            return data
        fetch_size = max(requested, 4 * 1024 * 1024)
        end = min(self.length - 1, self.position + fetch_size - 1)
        request = urllib.request.Request(
            self.url, headers={"Range": f"bytes={self.position}-{end}"}
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            self.cache_start, self.cache = self.position, response.read()
        data = self.cache[:requested]
        self.position += len(data)
        return data


def video_id(name: str) -> str | None:
    stem = PurePosixPath(name).stem
    prefix = stem.split("_", 1)[0]
    return prefix.lstrip("0").zfill(2) if "_" in stem and prefix.isdigit() else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Selectively download MECCANO frames")
    parser.add_argument("--video", action="append", required=True)
    parser.add_argument("--output", default="datasets/MECCANO/active_object_frames_subset")
    parser.add_argument("--proxy", default="http://127.0.0.1:10808")
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()
    requested = {value.lstrip("0").zfill(2) for value in args.video}
    remote = CachedHTTPRangeReader(URL, args.proxy)
    with zipfile.ZipFile(remote) as archive:
        members = [m for m in archive.infolist() if not m.is_dir() and video_id(m.filename) in requested]
        report = {
            "videos": sorted(requested),
            "members": len(members),
            "compressed_bytes": sum(m.compress_size for m in members),
            "uncompressed_bytes": sum(m.file_size for m in members),
        }
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if args.list_only:
            return 0
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        for number, member in enumerate(members, 1):
            target = output / PurePosixPath(member.filename).name
            if target.exists() and target.stat().st_size == member.file_size:
                continue
            part = target.with_suffix(target.suffix + ".part")
            with archive.open(member) as source, part.open("wb") as destination:
                shutil.copyfileobj(source, destination, 1024 * 1024)
            part.replace(target)
            if number % 100 == 0 or number == len(members):
                print(json.dumps({"downloaded": number, "total": len(members)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
