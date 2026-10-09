"""Fetch IndustReal official PSR labels via HTTP range requests (no full-archive download).

- train/val archives: extract only ``<recording>/PSR_labels*.csv`` into
  ``datasets/IndustReal/official/psr_labels/<split>/<recording>/`` (CRC-verified by zipfile,
  SHA-256 recorded).
- test archives: SEALED. Only the zip central directory is read; for each PSR member the
  name, sizes, CRC-32 and local-header offset are recorded. No member content is downloaded.

Writes the acquisition manifest to
``research/audits/industreal-psr-labels-acquisition-<date>.json``.

Usage::

    python scripts/fetch_industreal_psr_labels.py [--date 20261001] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RO_CRATE = REPO / "sources" / "industreal_official_4tu_ro_crate_v2_20260812.json"
OUT_ROOT = REPO / "datasets" / "IndustReal" / "official" / "psr_labels"
PSR_RE = re.compile(r"^(?P<rec>[^/]+)/(?P<file>PSR_labels(?:_raw|_with_errors)?\.csv)$")
ARCHIVE_RE = re.compile(r"^(?P<split>train|val|test)_p\d+\.zip$")
SEALED_SPLITS = {"test"}


class RangeFile(io.RawIOBase):
    """Seekable read-only file over HTTP range requests with a small block cache."""

    def __init__(self, url: str, size: int, block: int = 1 << 16) -> None:
        self.url, self.size, self.block, self.pos = url, size, block, 0
        self.cache: dict[int, bytes] = {}
        self.requests = 0
        self.bytes_fetched = 0

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, off: int, whence: int = 0) -> int:
        self.pos = {0: off, 1: self.pos + off, 2: self.size + off}[whence]
        return self.pos

    def _fetch(self, lo: int, hi: int) -> bytes:
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={lo}-{hi - 1}"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            if resp.status != 206:
                raise RuntimeError(f"range request not honoured: HTTP {resp.status}")
            data = resp.read()
        if len(data) != hi - lo:
            raise RuntimeError(f"short range read {len(data)} != {hi - lo}")
        self.requests += 1
        self.bytes_fetched += len(data)
        return data

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        a, b = self.pos, self.pos + n
        first, last = a // self.block, (b - 1) // self.block
        missing = [k for k in range(first, last + 1) if k not in self.cache]
        if missing:
            lo = missing[0] * self.block
            hi = min((missing[-1] + 1) * self.block, self.size)
            data = self._fetch(lo, hi)
            for k in range(missing[0], missing[-1] + 1):
                off = (k - missing[0]) * self.block
                self.cache[k] = data[off:off + self.block]
        buf = b"".join(self.cache[k] for k in range(first, last + 1))
        start = a - first * self.block
        self.pos = b
        out = buf[start:start + n]
        if len(self.cache) > 2048:
            self.cache.clear()
        return out

    def readinto(self, b) -> int:  # type: ignore[override]
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)


def archives_from_ro_crate() -> list[dict]:
    crate = json.loads(RO_CRATE.read_text(encoding="utf-8"))
    rows = []
    for e in crate["@graph"]:
        m = ARCHIVE_RE.match(e.get("name", ""))
        if m and "contentUrl" in e:
            rows.append({"name": e["name"], "split": m["split"], "url": e["contentUrl"],
                         "content_size": int(e["contentSize"])})
    return sorted(rows, key=lambda r: r["name"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--dry-run", action="store_true", help="read directories only, write nothing")
    args = ap.parse_args()

    manifest: dict = {
        "schema_version": "industreal-psr-acquisition-1.0.0",
        "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "4TU.ResearchData IndustReal v2 (RO-crate snapshot sources/industreal_official_4tu_ro_crate_v2_20260812.json)",
        "method": "HTTP range requests on zip central directory; only PSR_labels*.csv members fetched for non-sealed splits",
        "sealed_splits": sorted(SEALED_SPLITS),
        "sealed_policy": "test PSR member CONTENT NOT DOWNLOADED; only name/size/CRC-32/header offset recorded for later unseal verification",
        "archives": [],
        "extracted_files": [],
        "sealed_inventory": [],
    }
    for arc in archives_from_ro_crate():
        rf = RangeFile(arc["url"], arc["content_size"])
        zf = zipfile.ZipFile(rf)
        infos = zf.infolist()
        recordings = sorted({i.filename.split("/")[0] for i in infos if "/" in i.filename})
        psr = [(PSR_RE.match(i.filename), i) for i in infos]
        psr = [(m, i) for m, i in psr if m]
        sealed = arc["split"] in SEALED_SPLITS
        for m, info in psr:
            row = {"archive": arc["name"], "split": arc["split"], "recording_id": m["rec"],
                   "file": m["file"], "file_size": info.file_size,
                   "compress_size": info.compress_size, "crc32": f"{info.CRC:08x}"}
            if sealed:
                row["header_offset"] = info.header_offset
                manifest["sealed_inventory"].append(row)
                continue
            data = zf.read(info)  # zipfile verifies CRC-32
            row["sha256"] = hashlib.sha256(data).hexdigest()
            rel = Path("datasets/IndustReal/official/psr_labels") / arc["split"] / m["rec"] / m["file"]
            row["path"] = rel.as_posix()
            if not args.dry_run:
                dst = REPO / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
            manifest["extracted_files"].append(row)
        manifest["archives"].append({**arc, "member_count": len(infos),
                                     "recording_ids": recordings,
                                     "psr_member_count": len(psr), "sealed": sealed,
                                     "range_requests": rf.requests, "bytes_fetched": rf.bytes_fetched})
        print(f"{arc['name']}: members={len(infos)} recordings={len(recordings)} psr={len(psr)} "
              f"sealed={sealed} fetched={rf.bytes_fetched/1e6:.1f}MB")
    out = REPO / "research" / "audits" / f"industreal-psr-labels-acquisition-{args.date}.json"
    if not args.dry_run:
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(out.relative_to(REPO).as_posix())


if __name__ == "__main__":
    main()
