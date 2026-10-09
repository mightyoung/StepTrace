from __future__ import annotations

import argparse
import io
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


DEFAULT_URL = (
    "https://iplab.dmi.unict.it/sharing/MECCANO/"
    "MECCANO_active_object_frames.zip"
)


class HTTPRangeReader(io.RawIOBase):
    """Seekable read-only HTTP object backed by Range requests."""

    def __init__(self, url: str, *, proxy: str | None, timeout: int = 60) -> None:
        self.url = url
        self.timeout = timeout
        handlers = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        self.opener = urllib.request.build_opener(*handlers)
        request = urllib.request.Request(url, method="HEAD")
        with self.opener.open(request, timeout=timeout) as response:
            self.length = int(response.headers["Content-Length"])
            if "bytes" not in response.headers.get("Accept-Ranges", "").casefold():
                raise RuntimeError(f"server does not support byte ranges: {url}")
        self.position = 0

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
            raise ValueError("negative seek position")
        self.position = min(position, self.length)
        return self.position

    def read(self, size: int = -1) -> bytes:
        if self.position >= self.length:
            return b""
        if size is None or size < 0:
            end = self.length - 1
        else:
            end = min(self.length - 1, self.position + size - 1)
        request = urllib.request.Request(
            self.url, headers={"Range": f"bytes={self.position}-{end}"}
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            data = response.read()
        self.position += len(data)
        return data


def _video_id_from_member(name: str) -> str | None:
    stem = PurePosixPath(name).stem
    if "_" not in stem:
        return None
    prefix = stem.split("_", 1)[0]
    if not prefix.isdigit():
        return None
    return prefix.lstrip("0").zfill(2)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download selected MECCANO active-object frames via HTTP Range"
    )
    parser.add_argument("--video", action="append", required=True)
    parser.add_argument("--output", default="datasets/MECCANO/active_object_frames_subset")
    parser.add_argument("--proxy", default="http://127.0.0.1:10808")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--list-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    requested = {str(value).lstrip("0").zfill(2) for value in args.video}
    remote = HTTPRangeReader(args.url, proxy=args.proxy)
    with zipfile.ZipFile(remote) as archive:
        members = [
            item
            for item in archive.infolist()
            if not item.is_dir() and _video_id_from_member(item.filename) in requested
        ]
        report = {
            "videos": sorted(requested),
            "members": len(members),
            "compressed_bytes": sum(item.compress_size for item in members),
            "uncompressed_bytes": sum(item.file_size for item in members),
        }
        print(json.dumps(report, ensure_ascii=False))
        if args.list_only:
            return 0

        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        for index, member in enumerate(members, start=1):
            target = output / PurePosixPath(member.filename).name
            if target.exists() and target.stat().st_size == member.file_size:
                continue
            temporary = target.with_suffix(target.suffix + ".part")
            with archive.open(member) as source, temporary.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            temporary.replace(target)
            if index % 100 == 0 or index == len(members):
                print(json.dumps({"downloaded": index, "total": len(members)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
