"""Build a partial, provenance-preserving YOLO seed from official MECCANO labels."""

from __future__ import annotations
import argparse
import csv
import json
import shutil
from pathlib import Path
from display_project.data.yolo_dataset import (
    MECCANO_YOLO_NAMES,
    coco_category_to_yolo,
    to_yolo_line,
)


def _load_coco(paths):
    images, anns = {}, []
    for path in paths:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        local_cats = {int(c["id"]): c["name"] for c in d["categories"]}
        for image in d["images"]:
            key = image["file_name"]
            images[key] = {
                **image,
                "source_json": str(path),
                "local_id": int(image["id"]),
            }
        for ann in d["annotations"]:
            image = next(
                (i for i in d["images"] if int(i["id"]) == int(ann["image_id"])), None
            )
            if image:
                anns.append(
                    {
                        **ann,
                        "file_name": image["file_name"],
                        "category_name": local_cats[int(ann["category_id"])],
                        "source_json": str(path),
                    }
                )
    return images, anns


def build(manifest, image_dir, coco_paths, hand_csv, output):
    out = Path(output)
    img_out = out / "images"
    lab_out = out / "labels"
    img_out.mkdir(parents=True, exist_ok=True)
    lab_out.mkdir(parents=True, exist_ok=True)
    coco_images, anns = _load_coco(coco_paths)
    by_name = {}
    for image in coco_images.values():
        stem = Path(image["file_name"]).stem
        parts = stem.split("_", 1)
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        by_name[(str(int(parts[0])), int(parts[1]))] = image
    anns_by = {}
    for a in anns:
        anns_by.setdefault(a["file_name"], []).append(a)
    hands = {}
    with Path(hand_csv).open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if int(row.get("region_count") or 0) <= 0:
                continue
            name = row["filename"]
            attrs = json.loads(row["region_shape_attributes"])
            ra = json.loads(row["region_attributes"] or "{}")
            label = str(ra.get("class", ra.get("label", "hand"))).lower()
            hands.setdefault(name, []).append((attrs, label))
    records = []
    mapped = 0
    target_mapped = 0
    overlap_audits = []
    object_counts = {}
    hand_frames = 0
    hand_instances = 0
    with Path(manifest).open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        target = row["filename"]
        frame = int(row["source_frame"])
        video_token = (
            Path(row["source_video"]).parent.name
            if Path(row["source_video"]).stem == "video"
            else Path(row["source_video"]).stem
        )
        video = video_token[-4:].lstrip("0") or "0"
        if not video.isdigit():
            video = "9"
        key = (str(int(video)), frame)
        image = by_name.get(key)
        coco_name = image["file_name"] if image else f"{int(video):02d}_{frame:05d}.jpg"
        lines = []
        prov = []
        if image:
            mapped += 1
            for a in anns_by.get(image["file_name"], []):
                name = coco_category_to_yolo(a["category_name"])
                if not name:
                    continue
                cid = MECCANO_YOLO_NAMES.index(name)
                lines.append(
                    to_yolo_line(
                        cid,
                        a["bbox"],
                        image_width=int(image["width"]),
                        image_height=int(image["height"]),
                    )
                )
                prov.append(
                    {
                        "source": "official_coco",
                        "category": a["category_name"],
                        "source_json": a["source_json"],
                        "image_id": a["image_id"],
                        "bbox": a["bbox"],
                        "annotation_id": a["id"],
                    }
                )
                object_counts[name] = object_counts.get(name, 0) + 1
        hand_name = f"{frame:05d}.jpg"
        for attrs, label in hands.get(hand_name, []):
            x, y, w, h = [float(attrs[k]) for k in ("x", "y", "width", "height")]
            lines.append(
                to_yolo_line(0, (x, y, w, h), image_width=1920, image_height=1080)
            )
            prov.append(
                {"source": "official_hand_csv", "label": label, "bbox": [x, y, w, h]}
            )
            hand_instances += 1
        if hands.get(hand_name):
            hand_frames += 1
        coco_prov = [p for p in prov if p.get("source") == "official_coco"]
        if coco_prov:
            target_mapped += 1
            for left in range(len(coco_prov)):
                for right in range(left + 1, len(coco_prov)):
                    a, b = coco_prov[left]["bbox"], coco_prov[right]["bbox"]
                    ax1, ay1, aw, ah = a
                    bx1, by1, bw, bh = b
                    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
                    ix2, iy2 = min(ax1 + aw, bx1 + bw), min(ay1 + ah, by1 + bh)
                    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
                    union = aw * ah + bw * bh - inter
                    if union and inter / union >= 0.8:
                        overlap_audits.append(
                            {
                                "filename": target,
                                "source_frame": frame,
                                "left": left,
                                "right": right,
                                "iou": round(inter / union, 4),
                            }
                        )
        src = Path(image_dir) / target
        shutil.copy2(src, img_out / target)
        if lines:
            (lab_out / (Path(target).stem + ".txt")).write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
        records.append(
            {
                "filename": target,
                "source_frame": frame,
                "coco_file_name": coco_name,
                "coco_exact_match": bool(image),
                "has_labels": bool(lines),
                "provenance": prov,
            }
        )
    report = {
        "status": "PARTIAL_SEED_REQUIRES_HUMAN_AUDIT",
        "images": len(records),
        "labels": sum(r["has_labels"] for r in records),
        "source_kind": "active_objects",
        "coco_covered_frames": mapped,
        "target_mapped_object_frames": target_mapped,
        "unmapped_only_coco_frames": mapped - target_mapped,
        "near_duplicate_box_count": len(overlap_audits),
        "near_duplicate_boxes": overlap_audits,
        "hand_frames": hand_frames,
        "hand_instances": hand_instances,
        "object_counts": object_counts,
        "unmapped_coco_frames": sum(not r["coco_exact_match"] for r in records),
        "negative_labels_written": False,
        "records": records,
    }
    (out / "provenance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--images", required=True)
    p.add_argument("--coco", nargs=3, required=True)
    p.add_argument("--hands", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    print(
        json.dumps(
            build(a.manifest, a.images, a.coco, a.hands, a.output),
            ensure_ascii=False,
            indent=2,
        )
    )
