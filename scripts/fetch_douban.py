#!/usr/bin/env python3
"""Fetch ratings from Douban for ISBNs listed in ratings_cache.json.

Usage: python3 scripts/fetch_douban.py --limit 50

This script is conservative: it fetches at most --limit new ISBNs without
existing `douban` data, sleeps between requests, and checkpoints updates to
`ratings_cache.json` frequently.
"""
import argparse
import json
import time
import sys
import random
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
import re


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "douban_html_logs"
LOG_DIR.mkdir(exist_ok=True)
MANIFEST_PATH = LOG_DIR / "manifest.jsonl"
CACHE_PATH = ROOT / "ratings_cache.json"
MASTER_CSV = ROOT / "03_books_master.csv"


def load_cache():
    if not CACHE_PATH.exists():
        print(f"ratings cache not found at {CACHE_PATH}")
        sys.exit(1)
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(cache):
    tmp = CACHE_PATH.with_suffix('.json.tmp')
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    tmp.replace(CACHE_PATH)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
}


def append_manifest(kind, key, url, path):
    entry = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": kind,
        "key": key,
        "url": url,
        "path": str(path),
    }
    with open(MANIFEST_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def safe_key(value):
    return re.sub(r"[^0-9A-Za-z_-]+", "_", str(value))


def search_subject(query, log_key, retry_403=0, min_delay=None, max_delay=None):
    q = quote_plus(str(query))
    url = f"https://www.douban.com/search?cat=1001&q={q}"
    search_path = LOG_DIR / f"search_{safe_key(log_key)}.html"
    if search_path.exists():
        with open(search_path, "r", encoding="utf-8") as f:
            return f.read(), 200, url, search_path
    attempts = 0
    while True:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 200:
            with open(search_path, "w", encoding="utf-8") as f:
                f.write(resp.text)
            append_manifest("search", log_key, url, search_path)
            return resp.text, resp.status_code, url, search_path

        if resp.status_code in (403, 429) and attempts < retry_403:
            attempts += 1
            backoff = 10 * attempts
            if min_delay is not None and max_delay is not None:
                backoff += random.uniform(min_delay, max_delay)
            time.sleep(backoff)
            continue

        return None, resp.status_code, resp.text, search_path


def extract_one_decimal(text):
    m = re.search(r"(?<!\d)(10\.0|[0-9]\.\d)(?!\d)", text)
    return m.group(1) if m else None


def parse_search_html(html):
    soup = BeautifulSoup(html, "lxml")
    result = soup.select_one('div.result')
    if not result:
        result = soup.select_one('div.result-list div.result')
    if not result:
        result = soup.select_one('div.content div.result')

    href = None
    title = None
    rating = None
    votes = None

    if result:
        link = result.select_one('a[href*="/subject/"]')
        if link:
            href = link.get('href')
            title = link.get_text(strip=True)
        rnode = result.select_one('span.rating_nums') or result.select_one('span.rating_num')
        if rnode:
            rating = extract_one_decimal(rnode.get_text(strip=True))
        text = result.get_text(" ", strip=True)
        if not rating:
            rating = extract_one_decimal(text)
        vm = re.search(r"(\d+)\s*人评价", text)
        if vm:
            votes = vm.group(1)

    if href and href.startswith('//'):
        href = 'https:' + href
    return {
        "href": href,
        "title": title,
        "rating": rating,
        "votes": votes,
    }


def fetch_subject_page(url, retry_403=0, min_delay=None, max_delay=None):
    # derive filename from url
    m = re.search(r"/subject/(\d+)", url)
    subj_id = m.group(1) if m else None
    subj_path = LOG_DIR / (f"subject_{subj_id}.html" if subj_id else None)
    if subj_path and subj_path.exists():
        with open(subj_path, "r", encoding="utf-8") as f:
            return 200, f.read()
    attempts = 0
    while True:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 200 and subj_path:
            with open(subj_path, "w", encoding="utf-8") as f:
                f.write(resp.text)
            append_manifest("subject", subj_id, url, subj_path)
            return resp.status_code, resp.text
        if resp.status_code in (403, 429) and attempts < retry_403:
            attempts += 1
            backoff = 10 * attempts
            if min_delay is not None and max_delay is not None:
                backoff += random.uniform(min_delay, max_delay)
            time.sleep(backoff)
            continue
        return resp.status_code, resp.text


def parse_subject_page(html):
    soup = BeautifulSoup(html, "lxml")
    # average rating
    avg = None
    votes = None
    title = None
    try:
        tnode = soup.select_one('h1 span[property="v:itemreviewed"]')
        if tnode:
            title = tnode.get_text(strip=True)
    except Exception:
        title = None
    try:
        # extract rating with one decimal (e.g., 6.8)
        rnode = soup.select_one('strong[property="v:average"]') or soup.select_one('strong.rating_num')
        if rnode:
            text = rnode.get_text(strip=True)
            m = re.search(r"(\d+\.\d)", text)
            if m:
                avg = m.group(1)
            else:
                # try plain number
                m2 = re.search(r"(\d+(?:\.\d+)?)", text)
                if m2:
                    avg = m2.group(1)
        # votes
    except Exception:
        avg = None
    try:
        vnode = soup.select_one('span[property="v:votes"]')
        if vnode:
            votes_text = vnode.get_text(strip=True)
        else:
            people = soup.select_one('span.rating_people')
            votes_text = None
            if people:
                vnode = people.find('span')
                if vnode:
                    votes_text = vnode.get_text(strip=True)
        if votes_text:
            vm = re.search(r"(\d+)", votes_text.replace(',', ''))
            if vm:
                votes = vm.group(1)
    except Exception:
        votes = None
    return {"title": title, "average": avg, "votes": votes}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--min-delay", type=float, default=None)
    parser.add_argument("--max-delay", type=float, default=None)
    parser.add_argument("--checkpoint-interval", type=int, default=10)
    parser.add_argument("--retry-403", type=int, default=1)
    parser.add_argument("--only-403", action="store_true")
    parser.add_argument("--gentle", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    cache = load_cache()

    if args.gentle and args.min_delay is None and args.max_delay is None:
        args.min_delay = 4.0
        args.max_delay = 7.0

    def sleep_between():
        if args.min_delay is not None and args.max_delay is not None:
            time.sleep(random.uniform(args.min_delay, args.max_delay))
        else:
            time.sleep(args.delay)

    def needs_refresh(entry):
        if args.force:
            return True
        db = entry.get('douban')
        if not db:
            return True
        if db.get('error'):
            return True
        if not db.get('douban_average'):
            return True
        return False

    def is_403(entry):
        db = entry.get('douban') or {}
        return db.get('status_code') == 403 or db.get('error') == 'search_failed'

    # collect ISBNs missing douban info or needing refresh
    if args.only_403:
        isbns = [k for k, v in cache.items() if is_403(v)]
    else:
        isbns = [k for k, v in cache.items() if needs_refresh(v)]
    if not isbns:
        print("No ISBNs missing 'douban' data in cache.")
        return
    to_process = isbns[: args.limit]
    print(f"Processing {len(to_process)} ISBNs from cache...")

    processed = 0
    for isbn in to_process:
        print(f"[{processed+1}/{len(to_process)}] ISBN: {isbn}")
        try:
            search_html, status, search_url, search_path = search_subject(
                isbn,
                isbn,
                retry_403=args.retry_403,
                min_delay=args.min_delay,
                max_delay=args.max_delay,
            )
            if search_html is None:
                cache.setdefault(isbn, {})['douban'] = {
                    'status_code': status,
                    'error': 'search_failed',
                    'search_url': search_url,
                    'search_html': str(search_path),
                }
                print(f" search failed status={status}")
                save_cache(cache)
                sleep_between()
                processed += 1
                continue

            info = parse_search_html(search_html)

            # fallback: search by title if rating not found
            if not info.get('rating'):
                title_query = cache.get(isbn, {}).get('title') or cache.get(isbn, {}).get('gbooks_title')
                if title_query:
                    search_html2, status2, search_url2, search_path2 = search_subject(
                        title_query,
                        f"{isbn}_title",
                        retry_403=args.retry_403,
                        min_delay=args.min_delay,
                        max_delay=args.max_delay,
                    )
                    if search_html2:
                        info = parse_search_html(search_html2)
                        search_url = search_url2
                        search_path = search_path2
                        status = status2

            if info.get('rating'):
                cache.setdefault(isbn, {})['douban'] = {
                    'status_code': status,
                    'douban_url': info.get('href'),
                    'douban_title': info.get('title'),
                    'douban_average': info.get('rating'),
                    'douban_votes': info.get('votes'),
                    'douban_source': 'search',
                    'search_url': search_url,
                    'search_html': str(search_path),
                }
                print(f"  got(search): title={info.get('title')}, avg={info.get('rating')}, votes={info.get('votes')}")
            else:
                # fetch subject page as fallback if we have a link
                href = info.get('href')
                if not href:
                    cache.setdefault(isbn, {})['douban'] = {
                        'status_code': status,
                        'error': 'no_match',
                        'search_url': search_url,
                        'search_html': str(search_path),
                    }
                    print(" no match on search page")
                    save_cache(cache)
                    sleep_between()
                    processed += 1
                    continue

                code, subj_html = fetch_subject_page(
                    href,
                    retry_403=args.retry_403,
                    min_delay=args.min_delay,
                    max_delay=args.max_delay,
                )
                if code != 200:
                    cache.setdefault(isbn, {})['douban'] = {'status_code': code, 'error': 'subject_fetch_failed', 'subject_url': href}
                    print(f" subject fetch failed {code}")
                    save_cache(cache)
                    sleep_between()
                    processed += 1
                    continue

                parsed = parse_subject_page(subj_html)
                cache.setdefault(isbn, {})['douban'] = {
                    'status_code': 200,
                    'douban_url': href,
                    'douban_title': parsed.get('title'),
                    'douban_average': parsed.get('average'),
                    'douban_votes': parsed.get('votes'),
                    'douban_source': 'subject',
                    'search_url': search_url,
                    'search_html': str(search_path),
                }
                print(f"  got(subject): title={parsed.get('title')}, avg={parsed.get('average')}, votes={parsed.get('votes')}")

            processed += 1
            if processed % args.checkpoint_interval == 0:
                save_cache(cache)
                print(" checkpoint saved")

            sleep_between()

        except KeyboardInterrupt:
            print("Interrupted by user, saving cache and exiting")
            save_cache(cache)
            raise
        except Exception as e:
            print(f"Unexpected error for {isbn}: {e}")
            cache.setdefault(isbn, {})['douban'] = {'status_code': 0, 'error': str(e)}
            save_cache(cache)
            sleep_between()
            processed += 1

    # final save
    save_cache(cache)
    print("Done. Cache updated with douban fields.")


if __name__ == '__main__':
    main()
