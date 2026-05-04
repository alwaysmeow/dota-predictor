"""Shared helpers for Dotabuff parsers."""

from __future__ import annotations

import json
import os
import re
from html import unescape
from typing import Any
from urllib.error import HTTPError

try:
    from bs4 import Tag
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "These parsers need BeautifulSoup. Install it with: python3 -m pip install beautifulsoup4"
    ) from exc


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
DEFAULT_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_int(value: str | None) -> int | None:
    text = clean_text(value)
    if not text or text == "-":
        return None
    text = text.replace(",", "")
    multiplier = 1
    if text.lower().endswith("k"):
        multiplier = 1000
        text = text[:-1]
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text:
        return None
    return int(round(float(text) * multiplier))


def tag_text(tag: Tag | None, separator: str = " ") -> str:
    if tag is None:
        return ""
    return clean_text(tag.get_text(separator=separator))


def attr_contains(tag: Tag, name: str, needle: str) -> bool:
    values = tag.get(name) or []
    if isinstance(values, str):
        values = values.split()
    return needle in values


def first_class_match(tag: Tag, pattern: str) -> str | None:
    for class_name in tag.get("class") or []:
        match = re.search(pattern, class_name)
        if match:
            return match.group(1)
    return None


def absolute_dotabuff_url(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if path.startswith("//"):
        return "https:" + path
    return "https://www.dotabuff.com" + path


def load_env_file(path: str) -> None:
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as file_obj:
        for raw_line in file_obj:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def build_request_headers() -> dict[str, str]:
    headers = {
        "User-Agent": os.environ.get("DOTABUFF_USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": os.environ.get("DOTABUFF_ACCEPT", DEFAULT_ACCEPT),
        "Accept-Language": os.environ.get("DOTABUFF_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    optional_headers = {
        "Cookie": os.environ.get("DOTABUFF_COOKIE"),
        "Referer": os.environ.get("DOTABUFF_REFERER"),
    }
    headers.update({key: value for key, value in optional_headers.items() if value})
    extra_headers_json = os.environ.get("DOTABUFF_HEADERS_JSON")
    if extra_headers_json:
        try:
            extra_headers = json.loads(extra_headers_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"DOTABUFF_HEADERS_JSON is not valid JSON: {exc}") from exc
        if not isinstance(extra_headers, dict):
            raise SystemExit("DOTABUFF_HEADERS_JSON must be a JSON object.")
        headers.update({str(key): str(value) for key, value in extra_headers.items()})
    return headers


def dotabuff_http_error_message(exc: HTTPError, url: str) -> str:
    body = exc.read(1000).decode("utf-8", errors="replace")
    message = f"Dotabuff returned HTTP {exc.code} for {url}"
    if exc.code in {403, 503}:
        message += (
            "\nLikely Dotabuff/Cloudflare blocked this non-browser request. "
            "Open the page in a browser, copy cookies into DOTABUFF_COOKIE in .env, "
            "or save the HTML and parse it with --html-file."
        )
    if body:
        snippet = clean_text(re.sub(r"<[^>]+>", " ", body))
        if snippet:
            message += f"\nResponse snippet: {snippet[:500]}"
    return message
