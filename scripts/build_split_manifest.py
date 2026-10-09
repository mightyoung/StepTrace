import argparse
import csv
import json

from display_project.data.split_manifest import build_split_manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.ledger, encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    result = build_split_manifest(rows)
    if args.output.endswith(".json"):
        with open(args.output, "w", encoding="utf-8") as file:
            file.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    else:
        with open(args.output, "w", encoding="utf-8", newline="") as file:
            fieldnames = result[0].keys() if result else rows[0].keys()
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(result)
