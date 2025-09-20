#!/usr/bin/env python3
"""Dry-run video extraction from CSV feeds and update RSS inbox state."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import downie_dispatch

DEFAULT_DATA_DIR = Path(__file__).parent / "data"
DEFAULT_STATE_FILE = Path(
    "/Users/niceday/Developer/Cloud/Dropbox/-Code-/Data/srv/rss_inbox/logs/state.json"
)
DEFAULT_FEED_URL = "https://bg.raindrop.io/rss/public/36726420"
DEFAULT_FAILURES_FILE = Path(
    "/Users/niceday/Developer/Cloud/Dropbox/-Code-/Data/srv/rss_inbox/logs/failures.csv"
)
DEFAULT_COOKIE_FILE = "/Users/niceday/Developer/cookie/singlefile/xcom.cookies.json"

__all__ = [
    "DEFAULT_DATA_DIR",
    "DEFAULT_STATE_FILE",
    "DEFAULT_FEED_URL",
    "DEFAULT_FAILURES_FILE",
    "discover_csv_files",
    "extract_urls",
    "collect_urls",
    "load_state",
    "save_state",
    "append_processed",
    "write_failures",
    "evaluate_urls",
]


def discover_csv_files(root: Path) -> List[Path]:
    files: Set[Path] = set()
    if not root.exists():
        return []

    for path in root.rglob("*.csv"):
        if path.is_file():
            files.add(path)

    special = root / "csv"
    if special.is_file():
        files.add(special)

    return sorted(files)


def extract_urls(path: Path) -> Iterable[str]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        try:
            reader = csv.DictReader(fh)
            if reader.fieldnames:
                preferred = ["url", "link", "href", "source"]
                fallback = reader.fieldnames[0]
                for row in reader:
                    value = None
                    for key in preferred:
                        candidate = row.get(key)
                        if candidate:
                            value = candidate
                            break
                    if value is None:
                        value = row.get(fallback)
                    if value:
                        yield value.strip()
                return
        except Exception:
            pass

    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row:
                continue
            yield row[0].strip()


def collect_urls(data_dir: Path) -> List[str]:
    csv_files = discover_csv_files(data_dir)
    urls: List[str] = []
    seen: Set[str] = set()
    for file in csv_files:
        for url in extract_urls(file):
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)
    return urls


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run Downie extraction from CSV")
    parser.add_argument(
        "--data",
        dest="data_dir",
        default=str(DEFAULT_DATA_DIR),
        help="Directory containing CSV exports (default: data/)",
    )
    parser.add_argument(
        "--cookie",
        dest="cookie",
        help="Inline cookie string passed to downie_dispatch extractors",
    )
    parser.add_argument(
        "--cookie-file",
        dest="cookie_file",
        default=DEFAULT_COOKIE_FILE,
        help="Cookie file passed to downie_dispatch for X/Twitter",
    )
    parser.add_argument(
        "--state",
        dest="state_file",
        default=str(DEFAULT_STATE_FILE),
        help="rss-inbox state.json path",
    )
    parser.add_argument(
        "--feed",
        dest="feed_url",
        default=DEFAULT_FEED_URL,
        help="Feed URL key in state.json to update",
    )
    parser.add_argument(
        "--output",
        dest="failures_file",
        default=str(DEFAULT_FAILURES_FILE),
        help="Where to append failure records",
    )
    return parser.parse_args(argv)


def load_state(path: Path) -> Dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def append_processed(state: Dict, feed_url: str, entries: Iterable[str]) -> None:
    processed = state.setdefault("processed_entries", {})
    existing = processed.setdefault(feed_url, [])
    seen = set(existing)
    for entry in entries:
        if entry not in seen:
            existing.append(entry)
            seen.add(entry)


def write_failures(
    path: Path,
    failures: List[Tuple[str, str]],
    *,
    feed_url: str,
    action: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat()
    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow([
                "timestamp",
                "feed_url",
                "entry_id",
                "url",
                "action",
                "reason",
            ])
        for url, reason in failures:
            entry_id = url
            writer.writerow([
                timestamp,
                feed_url,
                entry_id,
                url,
                action,
                " ".join((reason or "").split()),
            ])


def evaluate_urls(
    urls: Iterable[str],
    *,
    cookie_file: Optional[str] = None,
    cookie: Optional[str] = None,
) -> Tuple[List[str], List[Tuple[str, str]], Dict[str, List[str]]]:
    successes: List[str] = []
    failures: List[Tuple[str, str]] = []
    extracted: Dict[str, List[str]] = {}

    twitter_cookies = None
    cookies_loaded = False
    namespace = argparse.Namespace(cookie=cookie, cookie_file=cookie_file)

    for url in urls:
        try:
            strategy = downie_dispatch.classify_url(url)
        except Exception as exc:
            failures.append((url, f"classify: {exc}"))
            continue

        if strategy == "twitter" and not cookies_loaded:
            twitter_cookies = downie_dispatch.resolve_twitter_cookies(namespace)
            cookies_loaded = True

        try:
            links = downie_dispatch.extract_links(url, strategy, twitter_cookies)
        except Exception as exc:
            failures.append((url, f"extract: {exc}"))
            continue

        if not links:
            failures.append((url, "no video links"))
            continue

        successes.append(url)
        extracted[url] = links

    return successes, failures, extracted


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    data_dir = Path(args.data_dir).expanduser()
    state_path = Path(args.state_file).expanduser()
    failures_path = Path(args.failures_file).expanduser()
    feed_url = args.feed_url

    urls = collect_urls(data_dir)
    if not urls:
        print("CSV 中没有可用链接")
        return 1

    print(f"准备检查 {len(urls)} 个链接")

    successes, failures, _ = evaluate_urls(
        urls,
        cookie_file=args.cookie_file,
        cookie=args.cookie,
    )

    print(f"成功解析: {len(successes)} 条, 失败: {len(failures)} 条")

    state = load_state(state_path)
    append_processed(state, feed_url, successes)
    save_state(state_path, state)

    if failures:
        write_failures(
            failures_path,
            failures,
            feed_url=feed_url,
            action="video_downloader",
        )
        print(f"已写入失败记录: {failures_path}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
