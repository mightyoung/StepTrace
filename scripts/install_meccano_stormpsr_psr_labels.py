"""Install STORM-PSR MECCANO PSR/ASD annotations with project sealing rules.

Source: https://github.com/TimSchoonbeek/PSR-annotations (download the branch zip from
codeload.github.com and pass the extracted ``PSR-annotations-main`` directory).

- Original archives are copied byte-for-byte to
  ``datasets/MECCANO/psr_annotations_stormpsr/original/``.
- PSR labels for development videos are extracted to ``.../psr_labels/<split>/<video>/``.
- Formal-test candidates (0008, 0010, 0011, 0012, 0019, 0020) are SEALED: content is not
  extracted; only name/size/CRC-32 from the zip directory are recorded.
- 0009 is extracted but marked DEVELOPMENT_ONLY (TOMBSTONE-MECCANO-0009).

Usage::

    python scripts/install_meccano_stormpsr_psr_labels.py <PSR-annotations-main dir> \
        --commit <sha> [--date 20261001]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DST = REPO / "datasets" / "MECCANO" / "psr_annotations_stormpsr"
SEALED = {"0008", "0010", "0011", "0012", "0019", "0020"}
TOMBSTONED = {"0009"}
ORIGINALS = ["MECCANO_PSR_Annotations.zip", "MECCANO_ASD_Annotations.zip",
             "Overview of the states in MECCANO.pdf", "readme.md", "PSR_labeler.py",
             "auto_labeling.py"]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = ap.parse_args()

    (DST / "original").mkdir(parents=True, exist_ok=True)
    originals = {}
    for name in ORIGINALS:
        data = (args.source / name).read_bytes()
        (DST / "original" / name).write_bytes(data)
        originals[name] = {"bytes": len(data), "sha256": sha256(data)}

    extracted, sealed = [], []
    with zipfile.ZipFile(args.source / "MECCANO_PSR_Annotations.zip") as zf:
        for info in zf.infolist():
            parts = info.filename.strip("/").split("/")
            if len(parts) != 4 or info.is_dir():
                continue
            _, split, video, fname = parts
            row = {"split_stormpsr": split, "video_id": video, "file": fname,
                   "file_size": info.file_size, "crc32": f"{info.CRC:08x}"}
            if video in SEALED:
                row["project_status"] = "SEALED_FORMAL_TEST_CANDIDATE_CONTENT_NOT_EXTRACTED"
                sealed.append(row)
                continue
            data = zf.read(info)
            rel = Path("datasets/MECCANO/psr_annotations_stormpsr/psr_labels") / split / video / fname
            (REPO / rel).parent.mkdir(parents=True, exist_ok=True)
            (REPO / rel).write_bytes(data)
            row.update(sha256=sha256(data), path=rel.as_posix(),
                       project_status=("DEVELOPMENT_ONLY_CONTAMINATED_TOMBSTONE-MECCANO-0009"
                                       if video in TOMBSTONED else "DEVELOPMENT"))
            extracted.append(row)

    manifest = {
        "schema_version": "meccano-stormpsr-psr-acquisition-1.0.0",
        "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_repository": "https://github.com/TimSchoonbeek/PSR-annotations",
        "source_commit": args.commit,
        "source_paper_doi": "10.1016/j.cviu.2025.104528",
        "license": "NOT_STATED_IN_REPOSITORY (no LICENSE file; readme requests citation). "
                   "Do not redistribute in the reproduction package; reference URL + commit instead.",
        "sealed_videos": sorted(SEALED),
        "sealed_policy": "Formal-test candidate PSR content not extracted; original zip kept byte-identical for unseal.",
        "tombstoned_videos": sorted(TOMBSTONED),
        "originals": originals,
        "extracted_files": extracted,
        "sealed_inventory": sealed,
    }
    out = REPO / "research" / "audits" / f"meccano-stormpsr-psr-acquisition-{args.date}.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"extracted={len(extracted)} sealed={len(sealed)} -> {out.relative_to(REPO).as_posix()}")


if __name__ == "__main__":
    main()
