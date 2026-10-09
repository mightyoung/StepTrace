from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from download_meccano_subset import CachedHTTPRangeReader, URL, video_id


class SelectiveReader(CachedHTTPRangeReader):
    block_size = 512 * 1024

    def read(self, size: int = -1) -> bytes:
        if self.position >= self.length:
            return b""
        requested = self.length - self.position if size is None or size < 0 else size
        offset = self.position - self.cache_start
        if 0 <= offset and offset + requested <= len(self.cache):
            data = self.cache[offset : offset + requested]
            self.position += len(data)
            return data
        fetch_size = max(requested, self.block_size)
        end = min(self.length - 1, self.position + fetch_size - 1)
        request = urllib.request.Request(
            self.url, headers={"Range": f"bytes={self.position}-{end}"}
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            self.cache_start, self.cache = self.position, response.read()
        data = self.cache[:requested]
        self.position += len(data)
        return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Download selected annotated MECCANO frames")
    parser.add_argument("--video", action="append", required=True)
    parser.add_argument("--annotation-root", default="datasets/MECCANO/MECCANO_active_objects_annotations")
    parser.add_argument("--output", default="datasets/MECCANO/active_object_frames_subset")
    parser.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = parser.parse_args()
    requested = {value.lstrip("0").zfill(2) for value in args.video}
    wanted: set[str] = set()
    for source in Path(args.annotation_root).glob("instances_meccano_*.json"):
        document = json.loads(source.read_text(encoding="utf-8"))
        wanted.update(
            Path(image["file_name"]).name
            for image in document["images"]
            if video_id(image["file_name"]) in requested
        )

    remote = SelectiveReader(URL, args.proxy)
    with zipfile.ZipFile(remote) as archive:
        unique = {}
        for member in archive.infolist():
            basename = PurePosixPath(member.filename).name
            if not member.is_dir() and basename in wanted:
                unique.setdefault(basename, member)
        members = list(unique.values())
        found = set(unique)
        report = {
            "videos": sorted(requested),
            "frames": len(members),
            "missing": len(wanted - found),
            "compressed_bytes": sum(member.compress_size for member in members),
            "uncompressed_bytes": sum(member.file_size for member in members),
        }
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if wanted - found:
            raise FileNotFoundError(f"archive is missing {len(wanted - found)} frames")

        remote.block_size = 256 * 1024
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
