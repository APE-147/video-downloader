#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
X/Twitter video URL extractor (vxtwitter implementation)

- Input: One or more X/Twitter post URLs
- Output: Direct download URLs (one per detected video), preferring the
  highest-bitrate MP4 variant exposed by the vxtwitter API.

The script automatically refreshes cookies by invoking the
desktop/info/cookie-update/download_cookies.py helper (same as running
``python3 download_cookies.py -d x.com``). CLI or environment-supplied
cookies merge on top of the refreshed set.

Usage:
  python3 twitter_video_analysis.py [options] <tweet_url> [more_urls...]

Options:
  --cookie "k=v; ..."        Inline cookie string passed to requests.
  --cookie-file PATH         Netscape-style cookies.txt file or simple KEY=VALUE
                             lines. Values merge with inline cookies.

Environment variables:
  TWITTER_COOKIE             Same as --cookie
  TWITTER_COOKIE_FILE        Same as --cookie-file
  TWITTER_COOKIE_DOMAIN      Domain for automatic cookie refresh (default: x.com)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import requests  # type: ignore
except Exception as exc:  # pragma: no cover - import guard
    print(
        "This script requires the requests package. Install with: pip3 install requests",
        file=sys.stderr,
    )
    raise


TWEET_URL_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/(\d+)")
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}
COOKIE_FETCH_DOMAIN = os.environ.get("TWITTER_COOKIE_DOMAIN", "x.com")

VXTWITTER_ERROR_META_RE = re.compile(
    r"<meta[^>]+property=['\"]og:description['\"][^>]*>",
    re.IGNORECASE,
)
META_CONTENT_RE = re.compile(r"content=['\"](?P<msg>[^'\"]+)['\"]", re.IGNORECASE)
HTML_TITLE_RE = re.compile(r"<title>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)

TWEET_STATUS_URL = "https://cdn.syndication.twimg.com/tweet-result"


def _locate_cookie_fetch_script() -> Optional[Path]:
    script_path = Path(__file__).resolve()
    for parent in script_path.parents:
        for parts in (
            ("desktop", "info", "cookie-update", "download_cookies.py"),
            ("desktop", "cookie-update", "download_cookies.py"),
            ("other", "cookie-update", "download_cookies.py"),
        ):
            candidate = parent.joinpath(*parts)
            if candidate.exists():
                return candidate
    return None


def _fetch_cookies_via_command(domain: str) -> Optional[Dict[str, str]]:
    helper = _locate_cookie_fetch_script()
    if helper is None:
        return None

    cmd = [sys.executable, str(helper), "-d", domain]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(helper.parent),
        )
    except OSError as exc:
        raise RuntimeError(f"Failed to execute {helper}: {exc}") from exc

    output = (result.stdout or "").strip()
    error_output = (result.stderr or "").strip()
    if result.returncode != 0:
        raise RuntimeError(
            f"Cookie helper exited with {result.returncode}: {error_output or output}"
        )

    lines = output.splitlines()
    json_start = None
    for idx, line in enumerate(lines):
        if line.strip().startswith("{"):
            json_start = idx
            break
    if json_start is None:
        raise RuntimeError("Could not locate JSON payload in cookie helper output")

    json_text = "\n".join(lines[json_start:])
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse cookie JSON: {exc}") from exc

    cookies = payload.get("cookies") or []
    cookie_map: Dict[str, str] = {}
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            cookie_map[name] = value
    return cookie_map or None


def _extract_html_error_message(payload: str) -> Optional[str]:
    meta_match = VXTWITTER_ERROR_META_RE.search(payload)
    if meta_match:
        tag = meta_match.group(0)
        content_match = META_CONTENT_RE.search(tag)
        if content_match:
            message = unescape(content_match.group("msg")).strip()
            if message:
                return message

    title_match = HTML_TITLE_RE.search(payload)
    if title_match:
        title = unescape(title_match.group("title")).strip()
        if title:
            return title

    for line in payload.splitlines():
        line = unescape(line.strip())
        if line:
            return line
    return None


def _describe_tweet_unavailability(tweet_id: str) -> Optional[str]:
    params = {"id": tweet_id, "lang": "en", "token": "Bearer"}
    try:
        resp = requests.get(TWEET_STATUS_URL, params=params, headers=DEFAULT_HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    if isinstance(data, dict):
        typename = data.get("__typename")
        if typename == "TweetTombstone":
            tombstone = data.get("tombstone") or {}
            if isinstance(tombstone, dict):
                text_obj = tombstone.get("text") or {}
                if isinstance(text_obj, dict):
                    text_value = text_obj.get("text")
                    if isinstance(text_value, str) and text_value.strip():
                        return text_value.strip()
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return None


@dataclass
class ExtractedVideo:
    identifier: str
    url: str
    bitrate: Optional[int]
    height: Optional[int]
    width: Optional[int]


def parse_cookie_string(cookie_str: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for part in cookie_str.split(";"):
        segment = part.strip()
        if not segment or "=" not in segment:
            continue
        name, value = segment.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def load_cookie_file(path: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "\t" in stripped:
                    parts = stripped.split("\t")
                    if len(parts) >= 7:
                        cookies[parts[5]] = parts[6]
                        continue
                if "=" in stripped:
                    name, value = stripped.split("=", 1)
                    cookies[name.strip()] = value.strip()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Cookie file not found: {path}") from exc
    return cookies


def build_cookie_jar(*sources: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    merged: Dict[str, str] = {}
    for source in sources:
        if not source:
            continue
        merged.update(source)
    return merged or None


def choose_best_variant(variants: Iterable[Dict[str, object]]) -> Optional[Dict[str, object]]:
    materialized = list(variants)
    if not materialized:
        return None
    def score(v: Dict[str, object]) -> Tuple[int, int, int, int]:
        content_type = (v.get("content_type") or "").lower()
        mp4_bonus = 1 if "mp4" in content_type else 0
        bitrate = int(v.get("bitrate") or 0)
        height = int(v.get("height") or 0)
        width = int(v.get("width") or 0)
        return (mp4_bonus, bitrate, height, width)
    candidates = [v for v in materialized if (v.get("content_type") or "").lower() == "video/mp4"]
    ranked_pool = candidates or materialized
    return max(ranked_pool, key=score)


def extract_videos_from_media(tweet_id: str, media_obj: Dict[str, object]) -> Optional[ExtractedVideo]:
    variants = media_obj.get("variants") or []
    if isinstance(variants, list) and variants:
        best_variant = choose_best_variant(variants)
        if best_variant and best_variant.get("url"):
            return ExtractedVideo(
                identifier=f"{tweet_id}-{media_obj.get('id', 'media')}",
                url=str(best_variant.get("url")),
                bitrate=int(best_variant.get("bitrate") or 0) or None,
                height=int(best_variant.get("height") or 0) or None,
                width=int(best_variant.get("width") or 0) or None,
            )
    video_url = media_obj.get("url")
    if isinstance(video_url, str) and video_url:
        return ExtractedVideo(
            identifier=f"{tweet_id}-{media_obj.get('id', 'media')}",
            url=video_url,
            bitrate=None,
            height=int(media_obj.get("height") or 0) or None,
            width=int(media_obj.get("width") or 0) or None,
        )
    return None


def extract_with_vxtwitter(url: str, cookies: Optional[Dict[str, str]] = None) -> List[ExtractedVideo]:
    match = TWEET_URL_RE.match(url)
    if not match:
        raise ValueError("URL does not look like a valid X status link.")
    tweet_id = match.group(1)
    api_url = f"https://api.vxtwitter.com/Twitter/status/{tweet_id}"

    try:
        response = requests.get(api_url, headers=DEFAULT_HEADERS, cookies=cookies, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Request to vxtwitter failed: {exc}") from exc

    content_type = (response.headers.get("Content-Type") or "").lower()
    text_payload = response.text

    if "application/json" not in content_type:
        message = _extract_html_error_message(text_payload)
        details = []
        if message:
            details.append(message)
        else:
            snippet = text_payload.strip().splitlines()
            preview = snippet[0][:120] if snippet else ""
            details.append(
                f"unexpected non-JSON response (content-type: {content_type or 'unknown'}; payload: {preview!r})"
            )

        tombstone = _describe_tweet_unavailability(tweet_id)
        if tombstone:
            details.append(f"tweet status: {tombstone}")

        raise RuntimeError(f"vxtwitter error: {'; '.join(details)}")

    try:
        data = json.loads(text_payload)
    except json.JSONDecodeError as exc:
        message = _extract_html_error_message(text_payload)
        details = [message or f"unexpected payload starting with {text_payload[:120]!r}"]
        tombstone = _describe_tweet_unavailability(tweet_id)
        if tombstone:
            details.append(f"tweet status: {tombstone}")
        raise RuntimeError(f"vxtwitter JSON parse error: {'; '.join(details)}") from exc

    videos: List[ExtractedVideo] = []
    media_ext = data.get("media_extended") or []
    if isinstance(media_ext, list) and media_ext:
        for media_obj in media_ext:
            if not isinstance(media_obj, dict):
                continue
            media_type = (media_obj.get("type") or "").lower()
            if media_type not in {"video", "gif"}:
                continue
            item = extract_videos_from_media(tweet_id, media_obj)
            if item:
                videos.append(item)
        if videos:
            return videos

    media = data.get("media") or []
    if isinstance(media, list):
        for media_obj in media:
            if not isinstance(media_obj, dict):
                continue
            media_type = (media_obj.get("type") or "").lower()
            if media_type not in {"video", "gif"}:
                continue
            item = extract_videos_from_media(tweet_id, media_obj)
            if item:
                videos.append(item)
        if videos:
            return videos

    media_urls = data.get("mediaURLs") or []
    if isinstance(media_urls, list):
        for idx, video_url in enumerate(media_urls):
            if isinstance(video_url, str) and video_url:
                videos.append(
                    ExtractedVideo(
                        identifier=f"{tweet_id}-url-{idx}",
                        url=video_url,
                        bitrate=None,
                        height=None,
                        width=None,
                    )
                )
    return videos


def _print_results(url: str, videos: List[ExtractedVideo]) -> None:
    print(f"URL: {url}")
    if not videos:
        print("  No video URLs found.")
        return
    for idx, video in enumerate(videos, 1):
        meta_bits = []
        if video.height:
            meta_bits.append(f"{video.height}p")
        if video.bitrate:
            meta_bits.append(f"{video.bitrate}k")
        meta = f" ({', '.join(meta_bits)})" if meta_bits else ""
        print(f"  Video {idx}{meta}: {video.url}")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract direct video URLs from X posts via vxtwitter.")
    parser.add_argument("tweet_urls", nargs="+", help="Tweet URLs to inspect.")
    parser.add_argument("--cookie", dest="cookie", help="Inline cookie string (e.g. 'auth_token=...; ct0=...').")
    parser.add_argument("--cookie-file", dest="cookie_file", help="Path to cookies.txt file.")
    return parser.parse_args(argv)


def resolve_cookies(args: argparse.Namespace) -> Optional[Dict[str, str]]:
    inline_cookie = args.cookie or os.environ.get("TWITTER_COOKIE")
    cookie_file = args.cookie_file or os.environ.get("TWITTER_COOKIE_FILE")
    inline = parse_cookie_string(inline_cookie) if inline_cookie else None
    file_based = load_cookie_file(cookie_file) if cookie_file else None
    command_cookies: Optional[Dict[str, str]] = None
    try:
        command_cookies = _fetch_cookies_via_command(COOKIE_FETCH_DOMAIN)
    except Exception as exc:
        print(f"警告: 自动获取 cookie 失败 ({exc})", file=sys.stderr)
    return build_cookie_jar(command_cookies, file_based, inline)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        cookies = resolve_cookies(args)
    except Exception as exc:
        print(f"Failed to load cookies: {exc}", file=sys.stderr)
        return 1

    for tweet_url in args.tweet_urls:
        try:
            videos = extract_with_vxtwitter(tweet_url, cookies=cookies)
        except Exception as exc:
            print(f"URL: {tweet_url}")
            print(f"  Error: {exc}")
            continue
        _print_results(tweet_url, videos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
