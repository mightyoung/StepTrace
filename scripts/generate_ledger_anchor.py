"""Generate a development-only external anchor for a verified ledger."""

from __future__ import annotations
import argparse
from scripts.ledger_anchor import write_anchor


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger")
    parser.add_argument("anchor")
    args = parser.parse_args()
    print(write_anchor(args.ledger, args.anchor))


if __name__ == "__main__":
    main()
