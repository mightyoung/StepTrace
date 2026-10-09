import json
from scripts.build_cvat_meccano_seed import build


def test_seed_builder_never_writes_negative_labels(tmp_path):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "index,filename,source_video,source_frame\n1,im.jpg,data/x/0009.mp4,1\n",
        encoding="utf-8",
    )
    images = tmp_path / "images"
    images.mkdir()
    (images / "im.jpg").write_bytes(b"x")
    coco = tmp_path / "coco.json"
    coco.write_text(
        json.dumps({"images": [], "annotations": [], "categories": []}),
        encoding="utf-8",
    )
    hands = tmp_path / "hands.csv"
    hands.write_text(
        "filename,file_size,file_attributes,region_count,region_id,region_shape_attributes,region_attributes\n00001.jpg,1,{},0,0,{},{}\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    report = build(manifest, images, [coco, coco, coco], hands, out)
    assert report["status"] == "PARTIAL_SEED_REQUIRES_HUMAN_AUDIT"
    assert report["negative_labels_written"] is False
    assert not list((out / "labels").glob("*.txt"))
