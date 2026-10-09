import hashlib
import json
from pathlib import Path

from PIL import ImageFont


ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "models" / "fonts" / "NotoSansSC-VF.ttf"
PROVENANCE = ROOT / "models" / "fonts" / "NotoSansSC-VF.provenance.json"
CONFIG = ROOT / "configs" / "experiments" / "paired-development-example.yaml"


def test_registered_font_loads_and_matches_provenance():
    payload = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    digest = hashlib.sha256(FONT.read_bytes()).hexdigest().upper()
    assert digest == payload["sha256"]
    font = ImageFont.truetype(str(FONT), 32)
    assert font.getbbox("安全装配") is not None


def test_paired_development_config_uses_registered_font():
    text = CONFIG.read_text(encoding="utf-8")
    assert text.count("font_path: ../../models/fonts/NotoSansSC-VF.ttf") == 5
    assert "NotoSansCJK-Regular.ttc" not in text
