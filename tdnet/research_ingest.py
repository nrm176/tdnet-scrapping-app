"""Bounded TDnet capture, validation, normalization, and command-line replay."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from tdnet.constants import HEADERS
from tdnet.parsing import extract_structured_data_from_page, has_next_page
from tdnet.services import _build_page_url


_JST = ZoneInfo("Asia/Tokyo")
_EMPTY_MARKER = "検索条件に該当するデータが見つかりません。"
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_observed(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid observed_at")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid observed_at") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    result = result.astimezone(timezone.utc)
    if result > _utc_now().astimezone(timezone.utc) + timedelta(minutes=5):
        raise ValueError("observed_at is too far in the future")
    return result


def _parse_target_date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("invalid target_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid target_date") from exc
    if parsed.isoformat() != value:
        raise ValueError("invalid target_date")
    return parsed


def _safe_release_url(value: str, suffix: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.release.tdnet.info"
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path.startswith("/inbs/")
        and parsed.path.endswith(suffix)
    )


def _expected_page_url(target: date, number: int) -> str:
    return _build_page_url(target, number)


def _next_page_target(soup: BeautifulSoup) -> str | None:
    pager = soup.find(class_="pager-R")
    if pager is None:
        return None
    onclick = str(pager.get("onclick", ""))
    if not onclick.strip():
        return None
    if re.fullmatch(r"\s*pager\(\s*(['\"])\1\s*\)\s*;?\s*", onclick):
        return None
    targets = re.findall(r"['\"]([^'\"]+)['\"]", onclick)
    if len(targets) != 1:
        raise ValueError("invalid continuation target")
    return targets[0]


def parse_capture(capture: object) -> list[dict[str, object]]:
    """Validate a complete capture before returning normalized disclosure rows."""
    if not isinstance(capture, dict) or set(capture) != {
        "source", "target_date", "observed_at", "raw_payload"
    }:
        raise ValueError("invalid capture envelope")
    if capture["source"] != "tdnet":
        raise ValueError("invalid source")
    target = _parse_target_date(capture["target_date"])
    _parse_observed(capture["observed_at"])
    raw_payload = capture["raw_payload"]
    if not isinstance(raw_payload, dict) or set(raw_payload) != {"pages"}:
        raise ValueError("invalid raw_payload")
    pages = raw_payload["pages"]
    if not isinstance(pages, list) or not 1 <= len(pages) <= 20:
        raise ValueError("invalid page count")

    normalized: dict[str, dict[str, object]] = {}
    for number, page in enumerate(pages, 1):
        if not isinstance(page, dict) or set(page) != {"url", "html"}:
            raise ValueError("invalid page")
        page_url, page_html = page["url"], page["html"]
        if not isinstance(page_url, str) or page_url != _expected_page_url(target, number):
            raise ValueError("invalid page URL chain")
        if not isinstance(page_html, str):
            raise ValueError("invalid page HTML")

        soup = BeautifulSoup(page_html, "html.parser")
        expected_next = f"I_list_{number + 1:03d}_{target:%Y%m%d}.html"
        next_target = _next_page_target(soup)
        if next_target is not None and next_target != expected_next:
            raise ValueError("invalid continuation target")
        tables = soup.select("table#main-list-table")
        if _EMPTY_MARKER in soup.get_text("", strip=True):
            disclosure_rows = [
                row for row in soup.find_all("tr")
                if any(
                    str(class_name).startswith("kj")
                    for cell in row.find_all("td")
                    for class_name in cell.get("class", [])
                )
            ]
            if len(pages) != 1 or disclosure_rows or next_target is not None:
                raise ValueError("ambiguous empty result")
            return []
        if len(tables) != 1:
            raise ValueError("missing or ambiguous disclosure table")
        candidates = [row for row in tables[0].find_all("tr") if row.find("td")]
        if not candidates:
            raise ValueError("unmarked empty result")
        parsed_rows = extract_structured_data_from_page(
            soup, target, log_validation_errors=False,
        )
        if len(parsed_rows) != len(candidates):
            raise ValueError("a disclosure row could not be parsed")

        should_continue = number < len(pages)
        if should_continue != (next_target is not None):
            raise ValueError("incomplete or inconsistent page chain")

        for item in parsed_rows:
            dumped = item.model_dump(mode="json")
            pdf_url = str(item.pdf_url)
            if not _safe_release_url(pdf_url, ".pdf"):
                raise ValueError("invalid PDF URL")
            if item.xbrl_url is not None and not _safe_release_url(str(item.xbrl_url), ".zip"):
                raise ValueError("invalid XBRL URL")
            if not _TIME.fullmatch(item.time):
                raise ValueError("invalid publication time")
            published = datetime.combine(
                target,
                datetime.strptime(item.time, "%H:%M").time(),
                tzinfo=_JST,
            )
            row = {
                "source_id": pdf_url,
                "source_date": target,
                "title": item.title,
                "company_code": item.code,
                "published_at": published,
                "payload": dumped,
            }
            previous = normalized.get(pdf_url)
            if previous is not None and previous != row:
                raise ValueError("conflicting duplicate source_id")
            normalized[pdf_url] = row
    return list(normalized.values())


def fetch_capture(target: date) -> dict[str, object]:
    """Fetch one complete TDnet list capture with no retries or redirects."""
    pages: list[dict[str, str]] = []
    with requests.Session() as session:
        session.headers.update(HEADERS)
        for number in range(1, 21):
            url = _expected_page_url(target, number)
            try:
                response = session.get(url, timeout=30, allow_redirects=False)
            except requests.RequestException as exc:
                raise ValueError("TDnet request failed") from exc
            if response.status_code != 200:
                raise ValueError("TDnet returned a non-success status")
            response.encoding = "utf-8"
            try:
                page_html = response.content.decode("utf-8", errors="strict")
            except (AttributeError, UnicodeDecodeError) as exc:
                raise ValueError("TDnet returned invalid UTF-8") from exc
            pages.append({"url": url, "html": page_html})
            if not has_next_page(BeautifulSoup(page_html, "html.parser")):
                break
        else:
            if has_next_page(BeautifulSoup(pages[-1]["html"], "html.parser")):
                raise ValueError("TDnet page limit exceeded")
    capture = {
        "source": "tdnet",
        "target_date": target.isoformat(),
        "observed_at": _utc_now().astimezone(timezone.utc).isoformat(),
        "raw_payload": {"pages": pages},
    }
    parse_capture(capture)
    return capture


def snapshot_hash(capture: dict[str, object]) -> str:
    body = {"target_date": capture["target_date"], "raw_payload": capture["raw_payload"]}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def store_capture(capture: dict[str, object], database_url: str) -> dict[str, object]:
    from tdnet.research_store import store_capture as persist

    return await persist(capture, database_url)


def _parser() -> argparse.ArgumentParser:
    class SafeParser(argparse.ArgumentParser):
        def error(self, message: str) -> None:
            raise ValueError("invalid command-line arguments")

    parser = SafeParser(description="Capture or replay TDnet research data")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--date", type=date.fromisoformat)
    source.add_argument("--input", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.output and args.output.exists():
            raise ValueError("output already exists")
        if args.output and (not args.output.parent.is_dir() or not os.access(args.output.parent, os.W_OK)):
            raise ValueError("output destination is unavailable")
        database_url = None
        if not args.dry_run:
            database_url = os.environ.get("HFT_RESEARCH_DATABASE_URL")
            if not database_url:
                raise ValueError("research database is not configured")
            from tdnet.research_store import validate_database_url

            validate_database_url(database_url)
        if args.input:
            capture = json.loads(args.input.read_text(encoding="utf-8"))
        else:
            capture = fetch_capture(args.date)
        rows = parse_capture(capture)
        capture_hash = snapshot_hash(capture)
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(capture, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
        if args.dry_run:
            result = {
                "source": "tdnet",
                "date": capture["target_date"],
                "record_count": len(rows),
                "snapshot_hash": capture_hash,
                "persisted": False,
                "ready_for_analysis": False,
            }
        else:
            assert database_url is not None
            result = asyncio.run(store_capture(capture, database_url))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
