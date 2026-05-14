"""Shared helpers for Dotabuff parsers."""

from __future__ import annotations

import json
import os
import re
import zlib
from gzip import decompress as gzip_decompress
from html import unescape
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse

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
DEFAULT_SEC_CH_UA = '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"'
DEFAULT_SEC_CH_UA_FULL_VERSION = "147.0.7727.138"
DEFAULT_SEC_CH_UA_FULL_VERSION_LIST = (
    '"Google Chrome";v="147.0.7727.138", '
    '"Not.A/Brand";v="8.0.0.0", '
    '"Chromium";v="147.0.7727.138"'
)


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


def unquote_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


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
            value = unquote_env_value(value)
            if key and key not in os.environ:
                os.environ[key] = value


def same_origin(url: str | None, referer: str | None) -> bool:
    if not url or not referer:
        return False
    parsed_url = urlparse(url)
    parsed_referer = urlparse(referer)
    return (
        parsed_url.scheme,
        parsed_url.hostname,
        parsed_url.port,
    ) == (
        parsed_referer.scheme,
        parsed_referer.hostname,
        parsed_referer.port,
    )


def build_request_headers(url: str | None = None) -> dict[str, str]:
    referer = os.environ.get("DOTABUFF_REFERER")
    sec_fetch_site = os.environ.get("DOTABUFF_SEC_FETCH_SITE")
    if not sec_fetch_site:
        sec_fetch_site = "same-origin" if same_origin(url, referer) else "none"

    headers = {
        "User-Agent": os.environ.get("DOTABUFF_USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": os.environ.get("DOTABUFF_ACCEPT", DEFAULT_ACCEPT),
        "Accept-Language": os.environ.get("DOTABUFF_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        "Accept-Encoding": os.environ.get("DOTABUFF_ACCEPT_ENCODING", "gzip, deflate"),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Priority": os.environ.get("DOTABUFF_PRIORITY", "u=0, i"),
        "Sec-CH-UA": os.environ.get("DOTABUFF_SEC_CH_UA", DEFAULT_SEC_CH_UA),
        "Sec-CH-UA-Arch": os.environ.get("DOTABUFF_SEC_CH_UA_ARCH", '"arm"'),
        "Sec-CH-UA-Bitness": os.environ.get("DOTABUFF_SEC_CH_UA_BITNESS", '"64"'),
        "Sec-CH-UA-Full-Version": os.environ.get(
            "DOTABUFF_SEC_CH_UA_FULL_VERSION",
            f'"{DEFAULT_SEC_CH_UA_FULL_VERSION}"',
        ),
        "Sec-CH-UA-Full-Version-List": os.environ.get(
            "DOTABUFF_SEC_CH_UA_FULL_VERSION_LIST",
            DEFAULT_SEC_CH_UA_FULL_VERSION_LIST,
        ),
        "Sec-CH-UA-Mobile": os.environ.get("DOTABUFF_SEC_CH_UA_MOBILE", "?0"),
        "Sec-CH-UA-Model": os.environ.get("DOTABUFF_SEC_CH_UA_MODEL", '""'),
        "Sec-CH-UA-Platform": os.environ.get("DOTABUFF_SEC_CH_UA_PLATFORM", '"macOS"'),
        "Sec-CH-UA-Platform-Version": os.environ.get("DOTABUFF_SEC_CH_UA_PLATFORM_VERSION", '"26.3.1"'),
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": sec_fetch_site,
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


def decode_response_body(raw: bytes, content_encoding: str | None) -> bytes:
    encoding = (content_encoding or "").lower().strip()
    if encoding == "gzip":
        return gzip_decompress(raw)
    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def read_text_response(response: Any) -> str:
    raw = response.read()
    body = decode_response_body(raw, response.headers.get("Content-Encoding"))
    charset = response.headers.get_content_charset() or "utf-8"
    return body.decode(charset, errors="replace")


def dotabuff_http_error_message(exc: HTTPError, url: str) -> str:
    raw_body = exc.read()
    try:
        body = decode_response_body(raw_body, exc.headers.get("Content-Encoding")).decode("utf-8", errors="replace")
    except Exception:
        body = raw_body.decode("utf-8", errors="replace")
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
