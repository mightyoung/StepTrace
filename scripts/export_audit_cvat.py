import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from display_project.data.cvat import audit_cvat_export

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("export")
    p.add_argument("--output")
    p.add_argument("--expected-count", type=int)
    p.add_argument("--expected-width", type=int)
    p.add_argument("--expected-height", type=int)
    a = p.parse_args()
    try:
        report = audit_cvat_export(
            a.export,
            expected_count=a.expected_count,
            expected_width=a.expected_width,
            expected_height=a.expected_height,
        )
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        print(f"malformed/internal: {exc}")
        raise SystemExit(1)
    report = dict(report)
    report["schema_version"] = "cvat-export-audit-1.1.0"
    report["canonical_sha256"] = ""
    canonical = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    report["canonical_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    if a.output:
        Path(a.output).write_text(text + "\n", encoding="utf-8", newline="\n")
    raise SystemExit(0 if report.get("status") == "PASS" else 2)
