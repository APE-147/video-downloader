#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Dispatch video URLs to Downie 4 using extractor strategies.

Usage:
  python3 downie_dispatch.py [--cookie COOKIE] [--cookie-file PATH] [--cookie-json PATH]
                             [--cookie-update]
                             <url> [more_urls...]

The script inspects each URL's domain and selects a strategy:
  * YouTube domains: forward the original URL to Downie 4 directly.
  * X/Twitter domains: resolve direct media links via cate.twitter_video.
  * Other domains: resolve via cate.other_video.

Resolved media URLs are then handed off to Downie 4 via ``open -g -a`` to keep the
application in the background.

When ``--cookie-update`` is supplied, the script looks up cookies for each
domain via the ``cookie-update`` project before extraction. If cookies cannot
be retrieved the URL is processed as usual.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urlparse

from cate import other_video, twitter_video
from common.cookie_update_bridge import CookieUpdateFetcher


DOWNIE_APP_CANDIDATES = ("Downie 4", "Downie")


class DispatchError(RuntimeError):
    pass


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send extracted video links to Downie 4.")
    parser.add_argument("urls", nargs="+", help="Video page URLs to process.")
    parser.add_argument("--cookie", dest="cookie", help="Inline cookie string for X/Twitter extractor.")
    parser.add_argument(
        "--cookie-file",
        dest="cookie_file",
        help="Cookie file path for X/Twitter extractor (Netscape or KEY=VALUE).",
    )
    parser.add_argument(
        "--cookie-json",
        dest="cookie_json",
        help="Cookie JSON file exported by rss-inbox (list of {name,value} objects).",
    )
    parser.add_argument(
        "--cookie-update",
        dest="use_cookie_update",
        action="store_true",
        help="Fetch per-domain cookies via the cookie-update project before extraction.",
    )
    return parser.parse_args(argv)


def classify_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or parsed.path).lower()
    if not host:
        raise DispatchError("URL must include a hostname")
    host = host.split("@")[-1]
    host = host.split(":", 1)[0]
    if host.endswith("youtube.com") or host.endswith("youtu.be"):
        return "youtube"
    if host.endswith("x.com") or host.endswith("twitter.com"):
        return "twitter"
    return "other"


def send_to_downie(urls: Iterable[str]) -> None:
    seen: Set[str] = set()
    for url in urls:
        normalized = url.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        last_error: Optional[str] = None
        for app in DOWNIE_APP_CANDIDATES:
            result = subprocess.run(
                ["open", "-g", "-a", app, normalized],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                break
            last_error = result.stderr.strip() or result.stdout.strip() or f"open -a {app} failed"
        else:
            raise DispatchError(f"Failed to hand off to Downie 4: {last_error or 'unknown error'}")
        print(f"  Sent to Downie: {normalized}")


def resolve_twitter_cookies(args: argparse.Namespace) -> Optional[Dict[str, str]]:
    temp_args = argparse.Namespace(
        cookie=args.cookie,
        cookie_file=args.cookie_file,
        cookie_json=getattr(args, "cookie_json", None),
    )
    try:
        return twitter_video.resolve_cookies(temp_args)
    except Exception as exc:
        print(f"警告: 加载 X/Twitter cookie 失败 ({exc})", file=sys.stderr)
        return None


def merge_cookie_dicts(
    base: Optional[Dict[str, str]],
    extra: Optional[Dict[str, str]],
) -> Optional[Dict[str, str]]:
    if not base and not extra:
        return None
    if not base:
        return dict(extra) if extra else None
    if not extra:
        return dict(base)
    merged = dict(base)
    merged.update(extra)
    return merged


def extract_links(url: str, strategy: str, cookies: Optional[Dict[str, str]]) -> List[str]:
    if strategy == "youtube":
        return [url]
    if strategy == "twitter":
        videos = twitter_video.extract_with_vxtwitter(url, cookies=cookies)
        return [item.url for item in videos if item.url]
    results = other_video.extract_videos(url, cookies=cookies)
    return [item.url for item in results if item.url]


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    cookie_fetcher: Optional[CookieUpdateFetcher] = None
    cookie_status: Dict[str, bool] = {}
    twitter_cookies: Optional[Dict[str, str]] = None
    twitter_cookies_loaded = False
    if getattr(args, "use_cookie_update", False):
        cookie_fetcher = CookieUpdateFetcher()
    exit_code = 0

    for original_url in args.urls:
        print(f"URL: {original_url}")
        try:
            strategy = classify_url(original_url)
        except DispatchError as exc:
            print(f"  Error: {exc}")
            exit_code = 1
            continue

        if strategy == "twitter" and not twitter_cookies_loaded:
            twitter_cookies = resolve_twitter_cookies(args)
            twitter_cookies_loaded = True

        domain_cookies: Optional[Dict[str, str]] = None
        if cookie_fetcher:
            bundle = cookie_fetcher.get_bundle(original_url)
            if bundle and bundle.requests:
                domain_cookies = bundle.requests
                if bundle.domain not in cookie_status:
                    print(
                        f"  cookie-update: loaded {len(bundle.requests)} cookies for {bundle.domain}"
                    )
                    cookie_status[bundle.domain] = True
            else:
                normalized = CookieUpdateFetcher._normalize_domain(original_url)  # type: ignore[attr-defined]
                if normalized and normalized not in cookie_status:
                    print(
                        f"  cookie-update: no cookies available for {normalized}, proceeding without them"
                    )
                    cookie_status[normalized] = False

        strategy_cookies = domain_cookies
        if strategy == "twitter":
            strategy_cookies = merge_cookie_dicts(twitter_cookies, domain_cookies)

        try:
            links = extract_links(original_url, strategy, strategy_cookies)
        except Exception as exc:
            print(f"  Error extracting links: {exc}")
            exit_code = 1
            continue

        if not links:
            print("  No video links found.")
            exit_code = 1
            continue

        try:
            send_to_downie(links)
        except DispatchError as exc:
            print(f"  Downie error: {exc}")
            exit_code = 1
            continue
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
