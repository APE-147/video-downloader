#!/usr/bin/env python3
"""Batch-dispatch CSV video links to Downie with success/failure logging."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Optional

import downie_dispatch

from dry_run_from_csv import (
    DEFAULT_DATA_DIR,
    DEFAULT_FAILURES_FILE,
    DEFAULT_FEED_URL,
    DEFAULT_STATE_FILE,
    collect_urls,
    load_state,
    save_state,
    append_processed,
    write_failures,
    evaluate_urls,
)

DEFAULT_COOKIE_FILE = "/Users/niceday/Developer/cookie/singlefile/xcom.cookies.json"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dispatch CSV-listed videos to Downie")
    parser.add_argument(
        "--data",
        dest="data_dir",
        default=str(DEFAULT_DATA_DIR),
        help="Directory containing CSV exports (default: data/)",
    )
    parser.add_argument(
        "--cookie",
        dest="cookie",
        help="Inline cookie string forwarded to downie_dispatch",
    )
    parser.add_argument(
        "--cookie-file",
        dest="cookie_file",
        default=DEFAULT_COOKIE_FILE,
        help="Cookie file passed to downie_dispatch",
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print evaluation results without sending to Downie",
    )
    return parser.parse_args(argv)


def dispatch_media(media_map: Dict[str, List[str]]) -> None:
    for links in media_map.values():
        if not links:
            continue
        downie_dispatch.send_to_downie(links)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    data_dir = Path(args.data_dir).expanduser()
    state_path = Path(args.state_file).expanduser()
    failures_path = Path(args.failures_file).expanduser()
    feed_url = args.feed_url

    urls = collect_urls(data_dir)
    if not urls:
        print("CSV 文件中没有找到有效的链接")
        return 1

    print(f"发现 {len(urls)} 个唯一链接")

    successes, failures, extracted = evaluate_urls(
        urls,
        cookie_file=args.cookie_file,
        cookie=args.cookie,
    )

    print(f"成功解析: {len(successes)} 条, 失败: {len(failures)} 条")

    if not args.dry_run:
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

        if successes:
            dispatch_media({url: extracted.get(url, []) for url in successes})
        else:
            print("没有可发送的链接")
    else:
        if failures:
            write_failures(
                failures_path,
                failures,
                feed_url=feed_url,
                action="video_downloader",
            )
            print(f"[DRY RUN] 失败详情已写入: {failures_path}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
