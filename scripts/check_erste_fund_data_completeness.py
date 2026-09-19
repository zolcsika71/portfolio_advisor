"""Read-only completeness audit of the two authorized Erste fund pages.

The browser opens only the configured public page URLs. Search pagination and
overview currency tabs are activated through their rendered controls; the
script never calls the site's fragment endpoints directly and never opens a
fund detail link.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
SEARCH_URL = "https://www.erstemarket.hu/befektetesi_alapok/kereses"
OVERVIEW_URL = "https://www.erstemarket.hu/befektetesi_alapok"
AUTHORIZED_URLS = (SEARCH_URL, OVERVIEW_URL)

STATUSES = (
    "PRESENT",
    "EMPTY",
    "PLACEHOLDER",
    "NOT_APPLICABLE",
    "INVALID_FORMAT",
    "NOT_EXPOSED",
    "EXTRACTION_ERROR",
)
ISSUE_STATUSES = frozenset({"EMPTY", "PLACEHOLDER", "INVALID_FORMAT", "EXTRACTION_ERROR"})
PLACEHOLDERS = frozenset({"-", "N/A", "N.A.", "—", "–"})
COMMON_FIELDS = (
    "fund_name",
    "isin",
    "currency",
    "price_nav",
    "price_date",
    "manager",
    "category",
    "performance_current_year",
    "performance_3_months",
    "performance_1_year",
    "performance_5_years",
    "performance_since_inception",
    "investment_horizon",
    "risk",
    "erste_best_of",
)


@dataclass(frozen=True, slots=True)
class SearchSelectors:
    result_count: str = ".found_count .count"
    table: str = "table.table-search-result"
    headers: str = "table.table-search-result thead th"
    rows: str = "table.table-search-result tbody tr.fundClick"
    empty_message: str = "table.table-search-result tbody td[colspan]"
    pagination_links: str = "a.page-link"
    fund_link: str = 'td a[href*="/befektetesi_alapok/alap/"]'


@dataclass(frozen=True, slots=True)
class OverviewSelectors:
    section: str = "#nepszeru-alapjaink"
    section_heading: str = "#nepszeru-alapjaink h2"
    tabs: str = "#nav-alapkezelo-tab [role=tab]"
    card: str = ".col-lg-4.mb-4"
    card_name: str = "h3"
    card_pairs: str = ".term-rows > .col-6"
    fund_link: str = 'a[href*="/befektetesi_alapok/alap/"]'


SEARCH_SELECTORS = SearchSelectors()
OVERVIEW_SELECTORS = OverviewSelectors()
SEARCH_HEADER_FIELD_MAP = {
    "Alap": "fund_name",
    "ERSTE Best of": "erste_best_of",
    "Deviza": "currency",
    "Kategória": "category",
    "Akt. év": "performance_current_year",
    "3 hó": "performance_3_months",
    "1 év": "performance_1_year",
    "5 év": "performance_5_years",
    "Ind.": "performance_since_inception",
}
OVERVIEW_LABEL_FIELD_MAP = {
    "Kategória": "category",
    "Időtáv": "investment_horizon",
    "Kockázat": "risk",
}


class CompletenessError(RuntimeError):
    """Base error for a fail-closed page audit."""


class SelectorFailure(CompletenessError):
    """A required rendered selector or label is absent."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def normalize_space(value: str) -> str:
    return " ".join(value.split())


def field_result(
    raw_value: str | None,
    normalized_value: str | None,
    status: str,
    reason: str,
    source: str,
) -> dict[str, str | None]:
    if status not in STATUSES:
        raise ValueError(f"unsupported field status: {status}")
    return {
        "raw_value": raw_value,
        "normalized_value": normalized_value,
        "status": status,
        "reason": reason,
        "source": source,
    }


def classify_text(raw_value: str | None, *, source: str) -> dict[str, str | None]:
    if raw_value is None:
        return field_result(None, None, "EXTRACTION_ERROR", "cell could not be extracted", source)
    normalized = normalize_space(raw_value)
    if normalized == "":
        return field_result(raw_value, None, "EMPTY", "rendered cell is empty", source)
    if normalized.upper() in PLACEHOLDERS:
        return field_result(raw_value, None, "PLACEHOLDER", "literal placeholder is displayed", source)
    return field_result(raw_value, normalized, "PRESENT", "non-empty rendered text", source)


def classify_currency(raw_value: str | None, *, source: str) -> dict[str, str | None]:
    result = classify_text(raw_value, source=source)
    if result["status"] != "PRESENT":
        return result
    normalized = str(result["normalized_value"]).upper()
    if not re.fullmatch(r"[A-Z]{3}", normalized):
        return field_result(
            raw_value,
            normalized,
            "INVALID_FORMAT",
            "currency is not a three-letter uppercase code",
            source,
        )
    return field_result(raw_value, normalized, "PRESENT", "three-letter currency code", source)


def classify_percentage(raw_value: str | None, *, source: str) -> dict[str, str | None]:
    basic = classify_text(raw_value, source=source)
    if basic["status"] != "PRESENT":
        return basic
    displayed = str(basic["normalized_value"])
    match = re.fullmatch(r"([+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+))\s*%", displayed)
    if match is None:
        return field_result(
            raw_value,
            None,
            "INVALID_FORMAT",
            "performance value is not a percentage",
            source,
        )
    decimal_text = match.group(1).replace(",", ".")
    try:
        value = Decimal(decimal_text)
    except InvalidOperation:
        return field_result(
            raw_value,
            None,
            "INVALID_FORMAT",
            "performance value could not be parsed as Decimal",
            source,
        )
    return field_result(
        raw_value,
        format(value, "f"),
        "PRESENT",
        "percentage parsed losslessly as Decimal text",
        source,
    )


def _valid_isin_checksum(value: str) -> bool:
    expanded = "".join(str(int(char, 36)) if char.isalpha() else char for char in value)
    total = 0
    for index, char in enumerate(reversed(expanded)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
        total += digit // 10 + digit % 10
    return total % 10 == 0


def classify_isin(raw_value: str | None, *, source: str) -> dict[str, str | None]:
    basic = classify_text(raw_value, source=source)
    if basic["status"] != "PRESENT":
        return basic
    normalized = str(basic["normalized_value"]).upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", normalized) or not _valid_isin_checksum(
        normalized
    ):
        return field_result(
            raw_value,
            normalized,
            "INVALID_FORMAT",
            "fund-link path segment is not a checksum-valid ISIN",
            source,
        )
    return field_result(
        raw_value,
        normalized,
        "PRESENT",
        "checksum-valid ISIN exposed in the rendered fund link",
        source,
    )


def not_exposed(reason: str) -> dict[str, str | None]:
    return field_result(None, None, "NOT_EXPOSED", reason, "page_schema")


def extraction_error(reason: str, source: str) -> dict[str, str | None]:
    return field_result(None, None, "EXTRACTION_ERROR", reason, source)


def require_search_headers(headers: list[str]) -> None:
    normalized = [normalize_space(value) for value in headers]
    expected = [*SEARCH_HEADER_FIELD_MAP, ""]
    if normalized != expected:
        raise SelectorFailure(
            f"search table headers changed: expected {expected!r}, rendered {normalized!r}"
        )


def assess_paginated_coverage(
    *,
    expected_count: int | None,
    extracted_count: int,
    expected_pages: int | None,
    visited_pages: int,
    errors: Iterable[str],
    stopping_reason: str | None,
) -> str:
    if tuple(errors):
        return "FAILED" if extracted_count == 0 else "PARTIAL"
    if stopping_reason is not None:
        return "PARTIAL" if extracted_count else "FAILED"
    if expected_count is None or expected_pages is None:
        return "PARTIAL"
    if extracted_count != expected_count or visited_pages != expected_pages:
        return "PARTIAL"
    return "COMPLETE"


def _page_schema() -> dict[str, dict[str, object]]:
    search_exposed = {
        "fund_name": "Alap header",
        "isin": "rendered fund-link path segment",
        "currency": "Deviza header",
        "category": "Kategória header",
        "performance_current_year": "Akt. év header",
        "performance_3_months": "3 hó header",
        "performance_1_year": "1 év header",
        "performance_5_years": "5 év header",
        "performance_since_inception": "Ind. header",
        "erste_best_of": "ERSTE Best of header and star marker",
    }
    overview_exposed = {
        "fund_name": "card heading",
        "isin": "rendered product-link path segment",
        "currency": "activated currency-tab label",
        "manager": "Népszerű alapjaink – Erste Alapkezelő section heading",
        "category": "Kategória label",
        "investment_horizon": "Időtáv label",
        "risk": "Kockázat label",
    }
    return {
        SEARCH_URL: {
            "record_kind": "search_result_row",
            "exposed_fields": search_exposed,
            "not_exposed_fields": [field for field in COMMON_FIELDS if field not in search_exposed],
            "observed_headers": list(SEARCH_HEADER_FIELD_MAP),
            "empty_state_message": "Nincs találat.",
        },
        OVERVIEW_URL: {
            "record_kind": "popular_fund_card",
            "exposed_fields": overview_exposed,
            "not_exposed_fields": [field for field in COMMON_FIELDS if field not in overview_exposed],
            "observed_labels": ["Kategória", "Időtáv", "Kockázat"],
            "section_heading": "Népszerű alapjaink – Erste Alapkezelő",
        },
    }


def _allowed_main_navigation(url: str) -> bool:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.erstemarket.hu"
        or parsed.fragment != ""
    ):
        return False
    if parsed.path == "/befektetesi_alapok/kereses":
        return parsed.query == "" or re.fullmatch(r"page=\d+", parsed.query) is not None
    if parsed.path == "/befektetesi_alapok":
        return parsed.query == ""
    return False


def _route_request_allowed(
    *, url: str, is_navigation_request: bool, is_main_frame: bool
) -> bool:
    return not (is_navigation_request and is_main_frame) or _allowed_main_navigation(
        url
    )


def _retry_once(action: Callable[[], Any], retryable: tuple[type[BaseException], ...]) -> Any:
    try:
        return action()
    except retryable:
        return action()


def _wait_for_search_structure(page: Any, timeout_ms: int) -> None:
    page.wait_for_function(
        """selectors => {
            const count = document.querySelector(selectors.result_count);
            const table = document.querySelector(selectors.table);
            return Boolean(count && count.textContent.trim() !== '' && table);
        }""",
        arg={"result_count": SEARCH_SELECTORS.result_count, "table": SEARCH_SELECTORS.table},
        timeout=timeout_ms,
    )


def _navigate(page: Any, url: str, timeout_ms: int, timeout_error: type[BaseException]) -> Any:
    def attempt() -> Any:
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if response is None:
            raise CompletenessError(f"navigation to {url} returned no response")
        if response.status != 200:
            raise CompletenessError(f"navigation to {url} returned HTTP {response.status}")
        if not _allowed_main_navigation(page.url):
            raise CompletenessError(f"navigation left authorized scope: {page.url}")
        return response

    return _retry_once(attempt, (timeout_error,))


def _redirect_chain(response: Any) -> list[dict[str, object]]:
    requests: list[Any] = []
    request = response.request
    while request is not None:
        requests.append(request)
        request = request.redirected_from
    requests.reverse()
    result: list[dict[str, object]] = []
    for item in requests:
        item_response = item.response()
        result.append(
            {
                "url": item.url,
                "http_status": item_response.status if item_response is not None else None,
            }
        )
    return result


def _detail_identity(href: str | None) -> tuple[str | None, str | None]:
    if href is None:
        return None, None
    parsed = urlparse(href)
    match = re.fullmatch(r"/befektetesi_alapok/alap/([^/]+)", parsed.path)
    return (match.group(1) if match else None), href


def _stable_control_href(href: str | None) -> str | None:
    """Remove only the site's volatile AJAX cache-buster from recorded control provenance."""
    if href is None:
        return None
    parsed = urlsplit(href)
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query) if key != "_"])
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _complete_fields(fields: dict[str, dict[str, str | None]], page_kind: str) -> None:
    reasons = {
        "price_nav": "the page exposes no price/NAV field",
        "price_date": "the page exposes no price/NAV date field",
        "manager": "the search-results page exposes no manager field",
        "performance_current_year": "the overview cards expose no current-year performance",
        "performance_3_months": "the overview cards expose no three-month performance",
        "performance_1_year": "the overview cards expose no one-year performance",
        "performance_5_years": "the overview cards expose no five-year performance",
        "performance_since_inception": "the overview cards expose no since-inception performance",
        "investment_horizon": "the search-results page exposes no investment-horizon field",
        "risk": "the search-results page exposes no risk field",
        "erste_best_of": "the overview cards expose no ERSTE Best of field",
    }
    for name in COMMON_FIELDS:
        if name not in fields:
            fields[name] = not_exposed(reasons.get(name, f"{page_kind} does not expose this field"))


def _search_record(
    *,
    cells: list[str],
    href: str | None,
    has_best_of_star: bool,
    dom_row_id: str,
    page_number: int,
    row_number: int,
    source_url: str,
    extracted_at: str,
    control_href: str | None,
) -> dict[str, object]:
    def cell(index: int) -> str | None:
        return cells[index] if index < len(cells) else None

    isin, rendered_href = _detail_identity(href)
    fields = {
        "fund_name": classify_text(cell(0), source="Alap cell"),
        "isin": classify_isin(isin, source="rendered fund-link href"),
        "currency": classify_currency(cell(2), source="Deviza cell"),
        "category": classify_text(cell(3), source="Kategória cell"),
        "performance_current_year": classify_percentage(cell(4), source="Akt. év cell"),
        "performance_3_months": classify_percentage(cell(5), source="3 hó cell"),
        "performance_1_year": classify_percentage(cell(6), source="1 év cell"),
        "performance_5_years": classify_percentage(cell(7), source="5 év cell"),
        "performance_since_inception": classify_percentage(cell(8), source="Ind. cell"),
        "erste_best_of": field_result(
            cell(1),
            "true" if has_best_of_star else "false",
            "PRESENT",
            "yellow star marker present" if has_best_of_star else "no star marker in boolean column",
            "ERSTE Best of cell marker",
        ),
    }
    _complete_fields(fields, "search-results page")
    return {
        "occurrence_id": f"search:p{page_number}:r{row_number}:dom-{dom_row_id or 'none'}",
        "page_url": SEARCH_URL,
        "source_url": source_url,
        "source_control_href": control_href,
        "rendered_detail_href": rendered_href,
        "location": {
            "page_number": page_number,
            "row_number": row_number,
            "dom_row_id": dom_row_id,
        },
        "extracted_at": extracted_at,
        "displayed_text": cells,
        "fields": fields,
    }


def _extract_search_page(
    page: Any,
    *,
    page_number: int,
    extracted_at: str,
    control_href: str | None,
) -> tuple[list[dict[str, object]], tuple[tuple[str, tuple[str, ...]], ...]]:
    row_locators = page.locator(SEARCH_SELECTORS.rows).all()
    records: list[dict[str, object]] = []
    fingerprint: list[tuple[str, tuple[str, ...]]] = []
    for row_number, row in enumerate(row_locators, start=1):
        cells = row.locator(":scope > td").all_inner_texts()
        dom_row_id = row.get_attribute("id") or ""
        link = row.locator(SEARCH_SELECTORS.fund_link).first
        href = link.get_attribute("href") if link.count() else None
        has_star = row.locator("td:nth-child(2) .yellow-star").count() > 0
        fingerprint.append((dom_row_id, tuple(cells)))
        records.append(
            _search_record(
                cells=cells,
                href=href,
                has_best_of_star=has_star,
                dom_row_id=dom_row_id,
                page_number=page_number,
                row_number=row_number,
                source_url=page.url,
                extracted_at=extracted_at,
                control_href=control_href,
            )
        )
    return records, tuple(fingerprint)


def _last_page_number(page: Any, expected_count: int, page_size: int) -> int | None:
    last = page.locator(f'{SEARCH_SELECTORS.pagination_links}[rel="last"]')
    if last.count():
        href = last.first.get_attribute("href") or ""
        match = re.search(r"/page:(\d+)", href)
        if match:
            return int(match.group(1))
    if expected_count == 0:
        return 1
    if page_size > 0:
        return math.ceil(expected_count / page_size)
    return None


def _pagination_link(page: Any, page_number: int) -> Any | None:
    pattern = re.compile(rf"^\s*{page_number}\s*$")
    matches = page.locator(SEARCH_SELECTORS.pagination_links).filter(has_text=pattern)
    return matches.first if matches.count() else None


def extract_search(
    page: Any,
    *,
    timeout_ms: int,
    max_pages: int,
    timeout_error: type[BaseException],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    errors: list[str] = []
    stopping_reason: str | None = None
    response = _navigate(page, SEARCH_URL, timeout_ms, timeout_error)
    _retry_once(lambda: _wait_for_search_structure(page, timeout_ms), (timeout_error,))
    headers = page.locator(SEARCH_SELECTORS.headers).all_inner_texts()
    require_search_headers(headers)
    count_text = normalize_space(page.locator(SEARCH_SELECTORS.result_count).inner_text())
    if not count_text.isdecimal():
        raise SelectorFailure(f"displayed result count is not an integer: {count_text!r}")
    expected_count = int(count_text)
    records: list[dict[str, object]] = []
    seen_states: set[tuple[tuple[str, tuple[str, ...]], ...]] = set()
    page_number = 1
    control_href: str | None = None
    page_sizes: list[int] = []
    expected_pages: int | None = None
    pagination_interactions: list[dict[str, object]] = []
    while True:
        if page_number > max_pages:
            stopping_reason = f"safety cap reached before page {page_number}: max_pages={max_pages}"
            break
        page_records, fingerprint = _extract_search_page(
            page,
            page_number=page_number,
            extracted_at=iso_timestamp(utc_now()),
            control_href=control_href,
        )
        if fingerprint in seen_states:
            stopping_reason = f"repeated pagination state detected at page {page_number}"
            break
        seen_states.add(fingerprint)
        page_sizes.append(len(page_records))
        records.extend(page_records)
        if page_number == 1:
            expected_pages = _last_page_number(page, expected_count, len(page_records))
            if expected_count > 0 and not page_records:
                empty_text = normalize_space(
                    page.locator(SEARCH_SELECTORS.empty_message).inner_text()
                    if page.locator(SEARCH_SELECTORS.empty_message).count()
                    else ""
                )
                raise SelectorFailure(
                    f"positive result count rendered no fund rows; empty message={empty_text!r}"
                )
        if expected_pages is not None and page_number >= expected_pages:
            break
        if len(records) >= expected_count:
            break
        next_number = page_number + 1
        link = _pagination_link(page, next_number)
        if link is None:
            stopping_reason = f"rendered pagination has no control for page {next_number}"
            break
        control_href = _stable_control_href(link.get_attribute("href"))
        before = fingerprint

        def click_and_wait(
            link: Any = link,
            before: object = before,
            next_number: int = next_number,
        ) -> None:
            link.evaluate("element => element.click()")
            page.wait_for_function(
                """args => {
                    const rows = Array.from(document.querySelectorAll(args.rowSelector));
                    const state = JSON.stringify(rows.map(row => [
                        row.id,
                        Array.from(row.querySelectorAll(':scope > td')).map(cell => cell.innerText)
                    ]));
                    const requestedPage = new URLSearchParams(location.search).get('page');
                    return requestedPage === String(args.pageNumber) && state !== args.before;
                }""",
                arg={
                    "rowSelector": SEARCH_SELECTORS.rows,
                    "before": json.dumps(before, ensure_ascii=False, separators=(",", ":")),
                    "pageNumber": next_number,
                },
                timeout=timeout_ms,
            )

        try:
            _retry_once(click_and_wait, (timeout_error,))
        except timeout_error:
            stopping_reason = f"page {next_number} did not finish rendering after one retry"
            break
        if not _allowed_main_navigation(page.url):
            errors.append(f"pagination left authorized scope: {page.url}")
            stopping_reason = "out-of-scope pagination navigation stopped enumeration"
            break
        pagination_interactions.append(
            {"from_page": page_number, "to_page": next_number, "control_href": control_href}
        )
        page_number = next_number
    coverage = assess_paginated_coverage(
        expected_count=expected_count,
        extracted_count=len(records),
        expected_pages=expected_pages,
        visited_pages=len(seen_states),
        errors=errors,
        stopping_reason=stopping_reason,
    )
    return records, {
        "requested_url": SEARCH_URL,
        "effective_url": response.url,
        "redirect_chain": _redirect_chain(response),
        "rendered_headers": [normalize_space(value) for value in headers],
        "displayed_result_count": expected_count,
        "expected_row_count": expected_count,
        "extracted_row_count": len(records),
        "expected_pages": expected_pages,
        "visited_pages": len(seen_states),
        "page_sizes": page_sizes,
        "pagination_interactions": pagination_interactions,
        "coverage": coverage,
        "errors": errors,
        "stopping_reason": stopping_reason,
    }


def _overview_record(
    *,
    raw_name: str | None,
    raw_currency: str,
    raw_manager: str,
    label_values: dict[str, str | None],
    href: str | None,
    tab_number: int,
    row_number: int,
    source_url: str,
    extracted_at: str,
) -> dict[str, object]:
    isin, rendered_href = _detail_identity(href)
    fields = {
        "fund_name": classify_text(raw_name, source="card heading"),
        "isin": classify_isin(isin, source="rendered product-link href"),
        "currency": classify_currency(raw_currency, source="activated currency-tab label"),
        "manager": classify_text(raw_manager, source="section heading"),
        "category": classify_text(label_values.get("Kategória"), source="Kategória card value"),
        "investment_horizon": classify_text(
            label_values.get("Időtáv"), source="Időtáv card value"
        ),
        "risk": classify_text(label_values.get("Kockázat"), source="Kockázat card value"),
    }
    _complete_fields(fields, "overview page")
    displayed = [raw_name or ""]
    for label in OVERVIEW_LABEL_FIELD_MAP:
        displayed.extend([f"{label}:", label_values.get(label) or ""])
    return {
        "occurrence_id": f"overview:t{tab_number}:{raw_currency}:r{row_number}:isin-{isin or 'none'}",
        "page_url": OVERVIEW_URL,
        "source_url": source_url,
        "source_control": f"currency tab {raw_currency}",
        "rendered_detail_href": rendered_href,
        "location": {
            "tab_number": tab_number,
            "tab_label": raw_currency,
            "card_number": row_number,
        },
        "extracted_at": extracted_at,
        "displayed_text": displayed,
        "fields": fields,
    }


def _activate_currency_tab(
    page: Any,
    tab: Any,
    *,
    pane_id: str,
    timeout_ms: int,
    timeout_error: type[BaseException],
) -> Any:
    pane = page.locator(f"#{pane_id}")
    tab_id = tab.get_attribute("id")

    def click_tab() -> None:
        tab.evaluate("element => element.click()")

    def wait_for_tab() -> None:
        page.wait_for_function(
            """args => {
                const tab = document.getElementById(args.tabId);
                const pane = document.getElementById(args.paneId);
                return tab?.getAttribute('aria-selected') === 'true'
                    && pane?.classList.contains('active')
                    && pane?.classList.contains('show');
            }""",
            arg={"tabId": tab_id, "paneId": pane_id},
            timeout=timeout_ms,
        )

    _retry_once(click_tab, (timeout_error,))
    _retry_once(wait_for_tab, (timeout_error,))
    return pane


def extract_overview(
    page: Any,
    *,
    timeout_ms: int,
    max_tabs: int,
    timeout_error: type[BaseException],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    response = _navigate(page, OVERVIEW_URL, timeout_ms, timeout_error)
    section = page.locator(OVERVIEW_SELECTORS.section)
    _retry_once(lambda: section.wait_for(state="visible", timeout=timeout_ms), (timeout_error,))
    heading = normalize_space(page.locator(OVERVIEW_SELECTORS.section_heading).inner_text())
    manager_match = re.fullmatch(r"Népszerű alapjaink\s*[–-]\s*(.+)", heading)
    if manager_match is None:
        raise SelectorFailure(f"overview manager section heading changed: {heading!r}")
    raw_manager = manager_match.group(1)
    tabs = page.locator(OVERVIEW_SELECTORS.tabs).all()
    if not tabs:
        raise SelectorFailure("overview currency tabs were not rendered")
    errors: list[str] = []
    stopping_reason: str | None = None
    records: list[dict[str, object]] = []
    tab_summaries: list[dict[str, object]] = []
    expected_row_count = 0
    if len(tabs) > max_tabs:
        stopping_reason = f"currency-tab safety cap reached: rendered={len(tabs)}, max_tabs={max_tabs}"
        tabs = tabs[:max_tabs]
    for tab_number, tab in enumerate(tabs, start=1):
        raw_currency = normalize_space(tab.inner_text())
        pane_id = tab.get_attribute("aria-controls")
        if not pane_id:
            errors.append(f"currency tab {raw_currency!r} has no aria-controls target")
            continue
        try:
            pane = _activate_currency_tab(
                page,
                tab,
                pane_id=pane_id,
                timeout_ms=timeout_ms,
                timeout_error=timeout_error,
            )
        except timeout_error:
            errors.append(f"currency tab {raw_currency!r} did not render after one retry")
            continue
        cards = pane.locator(OVERVIEW_SELECTORS.card).all()
        if not cards:
            errors.append(f"currency tab {raw_currency!r} rendered no fund cards")
            continue
        extracted_at = iso_timestamp(utc_now())
        expected_row_count += len(cards)
        tab_summaries.append(
            {
                "tab_number": tab_number,
                "tab_label": raw_currency,
                "card_count": len(cards),
                "interaction": "rendered tab control clicked",
            }
        )
        for row_number, card in enumerate(cards, start=1):
            name_locator = card.locator(OVERVIEW_SELECTORS.card_name).first
            raw_name = name_locator.inner_text() if name_locator.count() else None
            pairs = card.locator(OVERVIEW_SELECTORS.card_pairs).all_inner_texts()
            label_values: dict[str, str | None] = {}
            for index in range(0, len(pairs), 2):
                label = normalize_space(pairs[index]).removesuffix(":")
                value = pairs[index + 1] if index + 1 < len(pairs) else None
                label_values[label] = value
            missing_labels = set(OVERVIEW_LABEL_FIELD_MAP).difference(label_values)
            for missing_label in sorted(missing_labels):
                label_values[missing_label] = None
            link = card.locator(OVERVIEW_SELECTORS.fund_link).first
            href = link.get_attribute("href") if link.count() else None
            records.append(
                _overview_record(
                    raw_name=raw_name,
                    raw_currency=raw_currency,
                    raw_manager=raw_manager,
                    label_values=label_values,
                    href=href,
                    tab_number=tab_number,
                    row_number=row_number,
                    source_url=page.url,
                    extracted_at=extracted_at,
                )
            )
    if errors:
        coverage = "FAILED" if not records else "PARTIAL"
    elif stopping_reason is not None:
        coverage = "PARTIAL" if records else "FAILED"
    elif len(tab_summaries) == len(tabs):
        coverage = "COMPLETE"
    else:
        coverage = "PARTIAL"
    return records, {
        "requested_url": OVERVIEW_URL,
        "effective_url": response.url,
        "redirect_chain": _redirect_chain(response),
        "section_heading": heading,
        "rendered_labels": list(OVERVIEW_LABEL_FIELD_MAP),
        "displayed_result_count": None,
        "expected_row_count": expected_row_count,
        "expected_count_basis": "sum of cards exposed after activating every rendered currency tab",
        "extracted_row_count": len(records),
        "rendered_tab_count": len(tabs),
        "visited_tabs": len(tab_summaries),
        "tabs": tab_summaries,
        "coverage": coverage,
        "errors": errors,
        "stopping_reason": stopping_reason,
    }


def _status_totals(records: Iterable[dict[str, object]]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for record in records:
        fields = record["fields"]
        assert isinstance(fields, dict)
        for value in fields.values():
            assert isinstance(value, dict)
            totals[str(value["status"])] += 1
    return {status: totals.get(status, 0) for status in STATUSES}


def _issues(records: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for record in records:
        fields = record["fields"]
        location = record["location"]
        assert isinstance(fields, dict) and isinstance(location, dict)
        for field_name, field in fields.items():
            assert isinstance(field, dict)
            if field["status"] not in ISSUE_STATUSES:
                continue
            result.append(
                {
                    "page_url": record["page_url"],
                    "source_url": record["source_url"],
                    "occurrence_id": record["occurrence_id"],
                    "location": json.dumps(location, ensure_ascii=False, sort_keys=True),
                    "field": field_name,
                    "raw_value": field["raw_value"],
                    "normalized_value": field["normalized_value"],
                    "status": field["status"],
                    "reason": field["reason"],
                    "source": field["source"],
                    "extracted_at": record["extracted_at"],
                }
            )
    return result


def _atomic_write_text(path: Path, value: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _resolve_new_output_dir(
    output_dir: Path, *, repository_root: Path = ROOT
) -> Path:
    repository = repository_root.resolve()
    audit_root_path = repository / "data" / "audit"
    audit_root = audit_root_path.resolve()
    if not audit_root.is_relative_to(repository):
        raise ValueError("repository data/audit directory resolves outside the repository")
    if ".." in output_dir.parts:
        raise ValueError("--output-dir must not contain parent traversal")
    candidate_path = output_dir if output_dir.is_absolute() else repository / output_dir
    if not candidate_path.is_relative_to(audit_root_path) or candidate_path == audit_root_path:
        raise ValueError("--output-dir must be beneath the repository data/audit directory")
    candidate = candidate_path.resolve()
    if not candidate.is_relative_to(audit_root) or candidate == audit_root:
        raise ValueError("--output-dir resolves outside the repository data/audit directory")
    if candidate.exists() or candidate.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite existing report directory: {candidate}"
        )
    return candidate


def _write_reports(
    output_dir: Path,
    *,
    records: list[dict[str, object]],
    issues: list[dict[str, object]],
    summary: dict[str, object],
) -> dict[str, str]:
    output_dir = _resolve_new_output_dir(output_dir)
    output_dir.mkdir(parents=True)
    records_path = output_dir / "fund_records.json"
    issues_path = output_dir / "completeness_issues.csv"
    summary_path = output_dir / "run_summary.json"
    _atomic_write_text(
        records_path,
        json.dumps(
            {"schema_version": 1, "records": records},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    columns = [
        "page_url",
        "source_url",
        "occurrence_id",
        "location",
        "field",
        "raw_value",
        "normalized_value",
        "status",
        "reason",
        "source",
        "extracted_at",
    ]
    csv_buffer: list[str] = []
    from io import StringIO

    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(issues)
    csv_buffer.append(stream.getvalue())
    _atomic_write_text(issues_path, "".join(csv_buffer))
    _atomic_write_text(
        summary_path,
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return {
        "fund_records": str(records_path),
        "completeness_issues": str(issues_path),
        "run_summary": str(summary_path),
    }


def run_audit(
    *,
    output_dir: Path,
    browser_channel: str,
    headed: bool,
    timeout_seconds: float,
    max_pages: int,
    max_tabs: int,
) -> tuple[int, dict[str, object]]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise CompletenessError(
            "Playwright is required; install the locked project dependencies with Poetry"
        ) from error
    started = utc_now()
    timeout_ms = int(timeout_seconds * 1000)
    all_records: list[dict[str, object]] = []
    page_summaries: dict[str, dict[str, object]] = {}
    navigation_events: list[dict[str, object]] = []
    browser_metadata: dict[str, object] = {"channel": browser_channel, "headed": headed}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=browser_channel, headless=not headed)
        browser_metadata["version"] = browser.version
        context = browser.new_context()
        context.set_default_timeout(timeout_ms)
        page = context.new_page()

        def route_handler(route: Any, request: Any) -> None:
            is_main_navigation = request.is_navigation_request() and request.frame == page.main_frame
            allowed = _route_request_allowed(
                url=request.url,
                is_navigation_request=request.is_navigation_request(),
                is_main_frame=request.frame == page.main_frame,
            )
            if is_main_navigation:
                navigation_events.append(
                    {
                        "url": request.url,
                        "redirected_from": (
                            request.redirected_from.url if request.redirected_from is not None else None
                        ),
                        "allowed": allowed,
                    }
                )
            if allowed:
                route.continue_()
            else:
                route.abort("blockedbyclient")

        context.route("**/*", route_handler)
        for url in AUTHORIZED_URLS:
            try:
                if url == SEARCH_URL:
                    records, page_summary = extract_search(
                        page,
                        timeout_ms=timeout_ms,
                        max_pages=max_pages,
                        timeout_error=PlaywrightTimeoutError,
                    )
                else:
                    records, page_summary = extract_overview(
                        page,
                        timeout_ms=timeout_ms,
                        max_tabs=max_tabs,
                        timeout_error=PlaywrightTimeoutError,
                    )
            except (
                CompletenessError,
                OSError,
                PlaywrightError,
                TypeError,
                ValueError,
            ) as error:  # report structural/browser failures instead of false success
                records = []
                page_summary = {
                    "requested_url": url,
                    "effective_url": page.url,
                    "expected_row_count": None,
                    "extracted_row_count": 0,
                    "coverage": "FAILED",
                    "errors": [f"{type(error).__name__}: {error}"],
                    "stopping_reason": "page extraction failed closed",
                }
            all_records.extend(records)
            page_summaries[url] = page_summary
        context.close()
        browser.close()
    issues = _issues(all_records)
    for url, page_summary in page_summaries.items():
        page_records = [record for record in all_records if record["page_url"] == url]
        page_issues = [issue for issue in issues if issue["page_url"] == url]
        page_summary["field_status_totals"] = _status_totals(page_records)
        page_summary["issue_count"] = len(page_issues)
        page_summary["issue_totals"] = dict(
            sorted(Counter(str(issue["status"]) for issue in page_issues).items())
        )
    coverages = [str(item["coverage"]) for item in page_summaries.values()]
    overall_coverage = (
        "COMPLETE"
        if coverages and all(value == "COMPLETE" for value in coverages)
        else "FAILED"
        if coverages and all(value == "FAILED" for value in coverages)
        else "PARTIAL"
    )
    finished = utc_now()
    summary: dict[str, object] = {
        "schema_version": 1,
        "run_started_at": iso_timestamp(started),
        "run_finished_at": iso_timestamp(finished),
        "authorized_page_urls": list(AUTHORIZED_URLS),
        "scope_statement": (
            "Completeness is assessed only for fields and record occurrences rendered by the two "
            "authorized public pages and their in-page controls."
        ),
        "schemas": _page_schema(),
        "pages": page_summaries,
        "overall_coverage": overall_coverage,
        "record_count": len(all_records),
        "field_status_totals": _status_totals(all_records),
        "issue_count": len(issues),
        "issue_totals": dict(sorted(Counter(str(issue["status"]) for issue in issues).items())),
        "navigation_events": navigation_events,
        "blocked_navigation_attempts": [
            event for event in navigation_events if not bool(event["allowed"])
        ],
        "browser": browser_metadata,
        "limits": {
            "timeout_seconds": timeout_seconds,
            "retry_count_after_initial_attempt": 1,
            "max_pages": max_pages,
            "max_tabs": max_tabs,
        },
    }
    paths = _write_reports(output_dir, records=all_records, issues=issues, summary=summary)
    result = {"summary": summary, "report_paths": paths}
    return (0 if overall_coverage == "COMPLETE" else 2), result


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new directory beneath data/audit; existing directories are never overwritten",
    )
    parser.add_argument("--browser-channel", default="chrome")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--max-tabs", type=int, default=10)
    arguments = parser.parse_args(argv)
    try:
        output = _resolve_new_output_dir(arguments.output_dir)
    except (FileExistsError, ValueError) as error:
        parser.error(str(error))
    if arguments.timeout_seconds <= 0 or arguments.timeout_seconds > 60:
        parser.error("--timeout-seconds must be greater than zero and at most 60")
    if arguments.max_pages <= 0 or arguments.max_tabs <= 0:
        parser.error("safety caps must be positive")
    arguments.output_dir = output
    return arguments


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        exit_code, result = run_audit(
            output_dir=arguments.output_dir,
            browser_channel=arguments.browser_channel,
            headed=arguments.headed,
            timeout_seconds=arguments.timeout_seconds,
            max_pages=arguments.max_pages,
            max_tabs=arguments.max_tabs,
        )
    except (CompletenessError, FileExistsError, OSError, ValueError) as error:
        print(f"Erste fund completeness check failed: {error}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
