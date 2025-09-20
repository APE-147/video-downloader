#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generic extractor for `/archives/<id>/` pages across mirror domains.

For each provided URL, the script finds embedded DPlayer configurations,
resolves their video URLs, and, for HLS streams, selects the highest
quality variant available.

Usage:
  python3 archives_video_extractor.py <archive_url> [more_urls...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from html import unescape
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

ARCHIVE_PATH_RE = re.compile(r"/archives/\d+/?$", re.IGNORECASE)
DPLAYER_RE = re.compile(
    r"<div[^>]*class=\"[^\"]*dplayer[^\"]*\"[^>]*data-config=(?P<quote>['\"])(?P<data>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

M3U8_HEADERS = {
    "User-Agent": DEFAULT_HEADERS["User-Agent"],
    "Accept": "*/*",
}


@dataclass
class VideoResult:
    index: int
    url: str
    note: Optional[str] = None


class ArchiveExtractionError(RuntimeError):
    pass


def ensure_archive_url(url: str) -> None:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ArchiveExtractionError("URL must include scheme and host")
    if not ARCHIVE_PATH_RE.search(parsed.path):
        raise ArchiveExtractionError("URL path must match /archives/<id>/ pattern")


def fetch_html(url: str) -> str:
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:  # pragma: no cover - network guard
        raise ArchiveExtractionError(f"Failed to fetch page: {exc}") from exc


def parse_dplayer_configs(html: str) -> List[Dict[str, object]]:
    configs: List[Dict[str, object]] = []
    for match in DPLAYER_RE.finditer(html):
        raw = unescape(match.group("data"))
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(cfg, dict):
            configs.append(cfg)
    return configs


def parse_stream_inf_attributes(attr: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for part in attr.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"')
        result[key] = value
    return result


def choose_best_hls_variant(master_url: str, referer: str) -> str:
    headers = dict(M3U8_HEADERS)
    headers["Referer"] = referer
    try:
        resp = requests.get(master_url, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - network guard
        raise ArchiveExtractionError(f"Failed to load m3u8: {exc}") from exc

    playlist = resp.text
    if "#EXT-X-STREAM-INF" not in playlist:
        return master_url

    best_url: Optional[str] = None
    best_score: Tuple[int, int] = (-1, -1)

    lines = [line.strip() for line in playlist.splitlines() if line.strip()]
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = parse_stream_inf_attributes(line.split(":", 1)[1])
            resolution = attrs.get("RESOLUTION", "")
            height = -1
            if "x" in resolution:
                parts = resolution.lower().split("x", 1)
                try:
                    height = int(parts[1])
                except ValueError:
                    height = -1
            bandwidth = -1
            bw = attrs.get("AVERAGE-BANDWIDTH") or attrs.get("BANDWIDTH")
            if bw:
                try:
                    bandwidth = int(bw)
                except ValueError:
                    bandwidth = -1

            j = i + 1
            stream_url: Optional[str] = None
            while j < len(lines):
                candidate = lines[j]
                if candidate.startswith("#"):
                    j += 1
                    continue
                stream_url = urljoin(master_url, candidate)
                break
            if stream_url:
                score = (height, bandwidth)
                if score > best_score:
                    best_score = score
                    best_url = stream_url
            i = j
        else:
            i += 1
    return best_url or master_url


def resolve_video_url(config: Dict[str, object], referer: str) -> Optional[str]:
    video_info = config.get("video") if isinstance(config, dict) else None
    if not isinstance(video_info, dict):
        return None
    url = video_info.get("url")
    if not isinstance(url, str) or not url:
        return None
    url = url.strip()
    if "m3u8" in url.split("?", 1)[0].lower():
        try:
            return choose_best_hls_variant(url, referer)
        except ArchiveExtractionError:
            return url
    return url


def extract_videos(url: str) -> List[VideoResult]:
    ensure_archive_url(url)
    html = fetch_html(url)
    configs = parse_dplayer_configs(html)
    videos: List[VideoResult] = []
    for idx, cfg in enumerate(configs, 1):
        resolved = resolve_video_url(cfg, referer=url)
        if resolved:
            videos.append(VideoResult(index=idx, url=resolved))
    return videos


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Extract direct video URLs from /archives/<id>/ pages.")
    parser.add_argument("urls", nargs="+", help="Archive URLs to inspect.")
    args = parser.parse_args(argv)

    exit_code = 0
    for target in args.urls:
        print(f"URL: {target}")
        try:
            results = extract_videos(target)
        except ArchiveExtractionError as exc:
            print(f"  Error: {exc}")
            exit_code = 1
            continue
        if not results:
            print("  No videos found.")
            continue
        for item in results:
            suffix = f" ({item.note})" if item.note else ""
            print(f"  Video {item.index}: {item.url}{suffix}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
