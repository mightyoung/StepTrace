from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from download_meccano_subset import CachedHTTPRangeReader, URL, video_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Download only COCO-referenced MECCANO frames")
    parser.add_argument("--video", action="append", required=True)
    parser.add_argument("--annotation-root", default="datasets/MECCANO/MECCANO_active_objects_annotations")
    parser.add_argument("--output", default="datasets/MECCANO/active_object_frames_subset")
    parser.add_argument("--proxy", default="http://127.0.0.1:10808")
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()

    requested = {value.lstrip("0").zfill(2) for value in args.video}
    wanted: set[str] = set()
    for annotation in Path(args.annotation_root).glob("instances_meccano_*.json"):
        document = json.loads(annotation.read_text(encoding="utf-8"))
        wanted.update(
            Path(image["file_name"]).name
            for image in document["images"]
            if video_id(image["file_name"]) in requested
        )

    remote = CachedHTTPRangeReader(URL, args.proxy)
    with zipfile.ZipFile(remote) as archive:
        members = [
            member for member in archive.infolist()
            if not member.is_dir() and PurePosixPath(member.filename).name in wanted
        ]
        found = {PurePosixPath(member.filename).name for member in members}
        report = {
            "videos": sorted(requested),
            "requested_frames": len(wanted),
            "archive_members": len(members),
            "missing_frames": len(wanted - found),
            "compressed_bytes": sum(member.compress_size for member in members),
            "uncompressed_bytes": sum(member.file_size for member in members),
        }
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if args.list_only:
            return 0
        if wanted - found:
            raise FileNotFoundError(f"archive is missing {len(wanted - found)} requested frames")

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
