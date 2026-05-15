#!/usr/bin/env python3
"""
从借阅明细生成一本书一行的总表，按桶排序后调用 API League Search Books API 查询评分。

流程：
1. 读取原始借阅 CSV。
2. 以 ISBN 优先、否则以标题/作者/出版社组合生成唯一书目键。
3. 汇总成“书本总表”，保证同一本书只请求一次 API。
4. 按桶排序后调用 Search Books API。
5. 输出带评分的总表 CSV。

官方请求格式：
    GET https://api.apileague.com/search-books?api-key=YOUR-API-KEY&query=...

API key 也可以通过请求头 x-api-key 传入，但本脚本默认使用 query 参数，和文档一致。
"""

from __future__ import annotations

import argparse
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
import requests


def normalize_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip('"\'“”‘’，,;:：。')


def normalize_isbn(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    text = text.replace("-", "").replace(" ", "")
    if text.endswith(".0"):
        text = text[:-2]
    text = re.sub(r"[^0-9Xx]", "", text)
    return text.upper()


def first_non_empty(series: pd.Series) -> str:
    for value in series:
        text = normalize_text(value)
        if text:
            return text
    return ""


def canonical_key(row: pd.Series) -> str:
    isbn = normalize_isbn(row.get("ISBN"))
    if isbn:
        return f"isbn:{isbn}"
    title = normalize_text(row.get("Title")).casefold()
    author = normalize_text(row.get("Author")).casefold()
    publisher = normalize_text(row.get("Publisher")).casefold()
    return f"book:{title}|{author}|{publisher}"


def bucket_key(title: str) -> str:
    text = normalize_text(title)
    if not text:
        return "#"
    ch = text[0]
    if ch.isdigit():
        return "0-9"
    if "A" <= ch.upper() <= "Z":
        return ch.upper()
    return ch


def bucket_order(value: str) -> tuple:
    if value == "0-9":
        return (0, "")
    if len(value) == 1 and "A" <= value <= "Z":
        return (1, value)
    if value == "#":
        return (3, value)
    return (2, value)


def flatten_books(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for group in payload.get("books", []) or []:
        if isinstance(group, list):
            for item in group:
                if isinstance(item, dict):
                    yield item


def rating_from_payload(payload: Dict[str, Any], query_title: str) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = list(flatten_books(payload))
    if not candidates:
        return {
            "api_ok": False,
            "api_match_found": False,
            "api_book_id": None,
            "api_title": None,
            "api_subtitle": None,
            "api_authors": None,
            "api_rating_average": None,
            "api_rating_5star": None,
            "api_image": None,
        }

    def norm_title(text: str) -> str:
        return re.sub(r"\s+", " ", normalize_text(text)).casefold()

    wanted = norm_title(query_title)

    best = None
    for candidate in candidates:
        candidate_title = norm_title(candidate.get("title", ""))
        if candidate_title and candidate_title == wanted:
            best = candidate
            break
    if best is None:
        for candidate in candidates:
            candidate_title = norm_title(candidate.get("title", ""))
            if wanted and (wanted in candidate_title or candidate_title in wanted):
                best = candidate
                break
    if best is None:
        best = candidates[0]

    authors = best.get("authors") or []
    author_names = []
    if isinstance(authors, list):
        for author in authors:
            if isinstance(author, dict) and author.get("name"):
                author_names.append(normalize_text(author.get("name")))

    rating = best.get("rating") or {}
    rating_avg = None
    if isinstance(rating, dict):
        rating_avg = rating.get("average")

    rating_5star = None
    if isinstance(rating_avg, (int, float)):
        rating_5star = round(float(rating_avg) * 5.0, 3)

    return {
        "api_ok": True,
        "api_match_found": True,
        "api_book_id": best.get("id"),
        "api_title": best.get("title"),
        "api_subtitle": best.get("subtitle"),
        "api_authors": "; ".join(author_names) if author_names else None,
        "api_rating_average": rating_avg,
        "api_rating_5star": rating_5star,
        "api_image": best.get("image"),
    }


def query_book(api_endpoint: str, api_key: str, query: str) -> Dict[str, Any]:
    params = {
        "api-key": api_key,
        "query": query,
    }
    response = requests.get(api_endpoint, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def build_master_table(input_csv: str) -> pd.DataFrame:
    df = pd.read_csv(input_csv, dtype=str, low_memory=False)

    for column in ["Number of borrow"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)

    for column in ["Beginning of borrow", "Return time"]:
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], errors="coerce")

    df["book_key"] = df.apply(canonical_key, axis=1)
    df["bucket"] = df["Title"].map(bucket_key)
    df["normalized_title"] = df["Title"].map(normalize_text)
    df["normalized_isbn"] = df["ISBN"].map(normalize_isbn)

    grouped = df.groupby("book_key", sort=False, dropna=False)
    master = grouped.agg(
        bucket=("bucket", "first"),
        Title=("Title", first_non_empty),
        ISBN=("ISBN", first_non_empty),
        Author=("Author", first_non_empty),
        Publisher=("Publisher", first_non_empty),
        Classification_Number=("Classification Number", first_non_empty),
        Price=("Price", first_non_empty),
        Grade=("Grade", first_non_empty),
        total_borrows=("Number of borrow", "sum"),
        record_count=("Collection Barcode", "count"),
        unique_barcodes=("Collection Barcode", pd.Series.nunique),
        first_borrow=("Beginning of borrow", "min"),
        last_borrow=("Beginning of borrow", "max"),
    ).reset_index(drop=False)

    master["bucket_sort"] = master["bucket"].map(bucket_order)
    master = master.sort_values(
        by=["bucket_sort", "total_borrows", "Title", "ISBN"],
        ascending=[True, False, True, True],
        kind="mergesort",
    ).drop(columns=["bucket_sort"])

    master["Title"] = master["Title"].map(normalize_text)
    master["ISBN"] = master["ISBN"].map(normalize_isbn)
    master["Author"] = master["Author"].map(normalize_text)
    master["Publisher"] = master["Publisher"].map(normalize_text)

    return master.reset_index(drop=True)


def build_query(row: pd.Series) -> str:
    title = normalize_text(row.get("Title"))
    author = normalize_text(row.get("Author"))
    if author and author not in title and len(title) + len(author) + 1 <= 95:
        return f"{title} {author}"
    return title


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="katrina_processed (3).csv")
    parser.add_argument("--output", default="03_books_master_with_ratings.csv")
    parser.add_argument("--master-output", default="03_books_master.csv")
    parser.add_argument("--api-endpoint", default="https://api.apileague.com/search-books")
    parser.add_argument("--api-key", default=os.environ.get("APILEAGUE_API_KEY") or os.environ.get("API_KEY") or os.environ.get("key"))
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--mode", choices=("isbn_only","all"), default="isbn_only", help="查询模式：isbn_only 仅对有 ISBN 的书查询；all 对所有书查询（可能更费配额）")
    parser.add_argument("--daily-limit", type=int, default=50, help="每日最大 API 请求数，0 表示不限制")
    parser.add_argument("--resume-file", default=".ratings_progress.json", help="进度文件，用于断点续跑")
    parser.add_argument("--max-books", type=int, default=0, help="仅处理前 N 本书，0 表示全部")
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("Missing API key. Pass --api-key or set APILEAGUE_API_KEY/API_KEY/key.")

    master = build_master_table(args.input)
    master.to_csv(args.master_output, index=False, encoding="utf-8-sig")

    results: List[Dict[str, Any]] = []
    total = len(master)
    rows = master.head(args.max_books) if args.max_books and args.max_books > 0 else master

    # load resume progress
    processed_keys = set()
    import json
    if args.resume_file and os.path.exists(args.resume_file):
        try:
            with open(args.resume_file, 'r', encoding='utf-8') as f:
                processed_keys = set(json.load(f).get('processed', []))
        except Exception:
            processed_keys = set()

    requests_made = 0
    for idx, row in rows.iterrows():
        key = row.get('book_key')
        if key in processed_keys:
            continue

        # mode filtering
        isbn_norm = row.get('ISBN') or ''
        if args.mode == 'isbn_only' and (not isbn_norm or str(isbn_norm).strip() == ''):
            # skip non-ISBN entries
            processed_keys.add(key)
            # record empty result
            results.append({
                "api_ok": False,
                "api_match_found": False,
                "api_book_id": None,
                "api_title": None,
                "api_subtitle": None,
                "api_authors": None,
                "api_rating_average": None,
                "api_rating_5star": None,
                "api_image": None,
                "api_query": None,
                "api_total_results": None,
                "api_number": None,
                "api_offset": None,
                "api_error": "skipped_no_isbn",
            })
            continue

        if args.daily_limit and args.daily_limit > 0 and requests_made >= args.daily_limit:
            print(f"Daily limit reached ({args.daily_limit}). Stopping.")
            break

        query = build_query(row)
        try:
            payload = query_book(args.api_endpoint, args.api_key, query)
            rating_info = rating_from_payload(payload, normalize_text(row.get("Title")))
            rating_info.update({
                "api_query": query,
                "api_total_results": payload.get("total_results"),
                "api_number": payload.get("number"),
                "api_offset": payload.get("offset"),
            })
            requests_made += 1
        except Exception as exc:
            rating_info = {
                "api_ok": False,
                "api_match_found": False,
                "api_book_id": None,
                "api_title": None,
                "api_subtitle": None,
                "api_authors": None,
                "api_rating_average": None,
                "api_rating_5star": None,
                "api_image": None,
                "api_query": query,
                "api_total_results": None,
                "api_number": None,
                "api_offset": None,
                "api_error": str(exc),
            }

        results.append(rating_info)
        processed_keys.add(key)

        # save progress periodically
        if args.resume_file:
            try:
                with open(args.resume_file, 'w', encoding='utf-8') as f:
                    json.dump({'processed': list(processed_keys)}, f)
            except Exception:
                pass

        if args.delay > 0:
            time.sleep(args.delay)

        if (len(results) % 25) == 0:
            print(f"Processed {len(results)}/{len(rows)} books (requests made: {requests_made})")

    rated = pd.concat([rows.reset_index(drop=True), pd.DataFrame(results).reset_index(drop=True)], axis=1)
    rated.to_csv(args.output, index=False, encoding="utf-8-sig")

    print(f"Master table saved: {args.master_output}")
    print(f"Rated table saved: {args.output}")
    print(f"Books processed: {len(rated)}/{total}")
    print(f"API matches found: {int(rated['api_match_found'].fillna(False).sum())}")


if __name__ == "__main__":
    main()
