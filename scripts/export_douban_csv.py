#!/usr/bin/env python3
"""Export Douban ratings from ratings_cache.json to CSV."""
import argparse
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "ratings_cache.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="douban_ratings.csv")
    parser.add_argument("--only-rated", action="store_true")
    args = parser.parse_args()

    if not CACHE_PATH.exists():
        raise SystemExit(f"ratings cache not found at {CACHE_PATH}")

    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        cache = json.load(f)

    out_path = Path(args.out)
    fieldnames = [
        "ISBN",
        "douban_average",
        "douban_votes",
        "douban_url",
        "douban_title",
        "douban_source",
        "status_code",
        "error",
    ]

    rows = []
    for isbn, entry in cache.items():
        db = entry.get("douban", {})
        avg = db.get("douban_average")
        if args.only_rated and not avg:
            continue
        rows.append({
            "ISBN": isbn,
            "douban_average": avg,
            "douban_votes": db.get("douban_votes"),
            "douban_url": db.get("douban_url"),
            "douban_title": db.get("douban_title"),
            "douban_source": db.get("douban_source"),
            "status_code": db.get("status_code"),
            "error": db.get("error"),
        })

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
