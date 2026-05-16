# Douban Ratings Scraper

This repo includes scripts to scrape Douban book ratings via the search page
and export results to CSV. HTML pages are saved under `douban_html_logs/` and
reused to avoid re-fetching.

## Setup

```bash
pip3 install -r requirements.txt
```

## Fetch ratings

```bash
python3 scripts/fetch_douban.py --limit 50 --delay 1.5 --force
```

Gentle mode with jitter and 403-only retry:

```bash
python3 scripts/fetch_douban.py --only-403 --limit 200 --gentle --retry-403 1
```

## Export CSV

```bash
python3 scripts/export_douban_csv.py --out douban_ratings.csv --only-rated
```

Outputs:
- `douban_html_logs/` contains saved HTML and `manifest.jsonl`.
- `douban_ratings.csv` contains ISBN + rating fields.
