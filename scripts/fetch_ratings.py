#!/usr/bin/env python3
"""
从 CSV 读取书名/ISBN，调用 apileague Search Books API 获取评分并写回一个带评分列的新 CSV。

官方访问形式：
    GET https://api.apileague.com/search-books?api-key=YOUR-API-KEY&query=...

API key 也可以通过请求头 x-api-key 传入。

用法示例：
    python3 scripts/fetch_ratings.py \
        --input "katrina_processed (3).csv" \
        --output ratings_output.csv \
        --api-key YOUR_KEY
"""
import argparse
import csv
import json
import os
import time
from typing import Any, Dict, Optional

import requests


def find_rating_from_json(obj: Any) -> Dict[str, Optional[Any]]:
    """尝试从 API 返回的 JSON 中抽取常见的评分字段。"""
    if not obj:
        return {"rating": None, "rating_count": None}

    # 若直接为字典，检查常见键
    if isinstance(obj, dict):
        for k in ("averageRating", "average_rating", "avg_rating", "ratingValue", "rating"):
            if k in obj and obj[k] not in (None, ""):
                # 可能 rating 本身是字典
                val = obj[k]
                if isinstance(val, dict):
                    r = val.get("value") or val.get("average") or val.get("rating")
                else:
                    r = val
                count = obj.get("ratingCount") or obj.get("ratings_count") or obj.get("count")
                return {"rating": r, "rating_count": count}

    # 若为列表，检查第一个元素
    if isinstance(obj, list) and len(obj) > 0:
        return find_rating_from_json(obj[0])

    # 深搜少量层级以防字段嵌套
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, (dict, list)):
                res = find_rating_from_json(v)
                if res["rating"] is not None:
                    return res

    return {"rating": None, "rating_count": None}


def search_book(api_endpoint: str, api_key: Optional[str], title: str, isbn: Optional[str]) -> Dict[str, Any]:
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    params = {"query": title}
    if isbn:
        params["isbn"] = isbn

    try:
        r = requests.get(api_endpoint, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"ok": False, "error": str(e), "raw": None}

    rating = find_rating_from_json(data)
    return {"ok": True, "rating": rating.get("rating"), "rating_count": rating.get("rating_count"), "raw": data}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="katrina_processed (3).csv")
    p.add_argument("--output", default="ratings_output.csv")
    p.add_argument("--api-endpoint", default="https://api.apileague.com/search-books")
    p.add_argument("--api-key", default=os.environ.get("APILEAGUE_API_KEY"))
    p.add_argument("--delay", type=float, default=0.4, help="每次请求的间隔（秒），以防被限速）")
    args = p.parse_args()

    in_path = args.input
    out_path = args.output

    with open(in_path, newline="", encoding="utf-8") as inf:
        reader = csv.DictReader(inf)
        fieldnames = list(reader.fieldnames or [])

        # 追加输出列
        extra_cols = ["API_Ok", "API_Rating", "API_RatingCount", "API_Raw"]
        with open(out_path, "w", newline="", encoding="utf-8") as outf:
            writer = csv.DictWriter(outf, fieldnames=fieldnames + extra_cols)
            writer.writeheader()

            for row in reader:
                title = row.get("Title") or row.get("title") or ""
                isbn = row.get("ISBN") or row.get("Isbn") or row.get("isbn") or ""
                if isbn:
                    # 有些 ISBN 字段带小数点（见原 CSV），去掉小数
                    isbn = isbn.split(".")[0]

                if not title and not isbn:
                    row.update({"API_Ok": False, "API_Rating": None, "API_RatingCount": None, "API_Raw": None})
                    writer.writerow(row)
                    continue

                res = search_book(args.api_endpoint, args.api_key, title, isbn)
                if res.get("ok"):
                    row.update({
                        "API_Ok": True,
                        "API_Rating": res.get("rating"),
                        "API_RatingCount": res.get("rating_count"),
                        "API_Raw": json.dumps(res.get("raw"), ensure_ascii=False)[:800],
                    })
                else:
                    row.update({"API_Ok": False, "API_Rating": None, "API_RatingCount": None, "API_Raw": res.get("error")})

                writer.writerow(row)
                time.sleep(args.delay)


if __name__ == "__main__":
    main()
