import hashlib
import json
from pathlib import Path


def test_yolo11s_base_model_matches_provenance_and_is_not_best_weight():
    provenance_path = Path("models/yolo-base/yolo11s.provenance.json")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    model_path = Path(provenance["local_path"])

    assert provenance["artifact_role"] == "PRETRAINED_BASE_MODEL"
    assert provenance["training_status"] == "NOT_FINE_TUNED"
    assert provenance["formal_admission"] == "PROHIBITED_AS_MECCANO_BEST_PT"
    assert model_path != Path("models/yolo-small-objects/best.pt")
    assert model_path.stat().st_size == provenance["bytes"]
    assert hashlib.sha256(model_path.read_bytes()).hexdigest() == provenance["sha256"]
