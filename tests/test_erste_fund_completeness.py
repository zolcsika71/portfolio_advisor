from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts import check_erste_fund_data_completeness as audit


def test_numeric_zero_is_present_not_missing() -> None:
    result = audit.classify_percentage("0%", source="test")

    assert result["status"] == "PRESENT"
    assert result["normalized_value"] == "0"


def test_decimal_comma_percentage_is_parsed_losslessly() -> None:
    result = audit.classify_percentage("12,340%", source="test")

    assert result["status"] == "PRESENT"
    assert result["normalized_value"] == "12.340"


@pytest.mark.parametrize("placeholder", ["-", "N/A", "—", ""])
def test_placeholders_and_empty_strings_remain_distinct(placeholder: str) -> None:
    result = audit.classify_percentage(placeholder, source="test")

    expected = "EMPTY" if placeholder == "" else "PLACEHOLDER"
    assert result["status"] == expected
    assert result["raw_value"] == placeholder
    assert result["normalized_value"] is None


def test_selector_header_failure_is_fail_closed() -> None:
    with pytest.raises(audit.SelectorFailure, match="headers changed"):
        audit.require_search_headers(["Alap", "Deviza"])


def test_incomplete_pagination_is_partial() -> None:
    assert (
        audit.assess_paginated_coverage(
            expected_count=364,
            extracted_count=349,
            expected_pages=25,
            visited_pages=24,
            errors=(),
            stopping_reason="rendered pagination has no control for page 25",
        )
        == "PARTIAL"
    )


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        (audit.SEARCH_URL, True),
        (f"{audit.SEARCH_URL}?page=2", True),
        (f"{audit.SEARCH_URL}?page=2&extra=1", False),
        (f"{audit.SEARCH_URL}#results", False),
        (f"{audit.OVERVIEW_URL}?page=2", False),
        ("https://www.erstemarket.hu/befektetesi_alapok/alap/example", False),
        ("https://example.invalid/befektetesi_alapok", False),
        ("http://www.erstemarket.hu/befektetesi_alapok", False),
    ],
)
def test_main_navigation_allowlist_is_exact(url: str, allowed: bool) -> None:
    assert audit._allowed_main_navigation(url) is allowed


def test_route_decision_blocks_only_out_of_scope_main_navigation() -> None:
    outside = "https://example.invalid/redirect"

    assert not audit._route_request_allowed(
        url=outside, is_navigation_request=True, is_main_frame=True
    )
    assert audit._route_request_allowed(
        url=outside, is_navigation_request=False, is_main_frame=True
    )
    assert audit._route_request_allowed(
        url=outside, is_navigation_request=True, is_main_frame=False
    )


class _Locator:
    def __init__(self, *, text: str = "", texts: list[str] | None = None) -> None:
        self._text = text
        self._texts = texts or []

    def inner_text(self) -> str:
        return self._text

    def all_inner_texts(self) -> list[str]:
        return self._texts


class _PaginationLink:
    def __init__(self, page: _SearchPage) -> None:
        self.page = page
        self.clicked = 0

    def evaluate(self, _script: str) -> None:
        self.clicked += 1
        self.page.url = f"{audit.SEARCH_URL}?page=2"

    def get_attribute(self, name: str) -> str | None:
        assert name == "href"
        return "/befektetesi_alapok/kereses?page=2&_=volatile"


class _SearchPage:
    def __init__(self) -> None:
        self.url = audit.SEARCH_URL
        self.wait_arguments: list[dict[str, object]] = []

    def locator(self, selector: str) -> _Locator:
        if selector == audit.SEARCH_SELECTORS.headers:
            return _Locator(texts=[*audit.SEARCH_HEADER_FIELD_MAP, ""])
        if selector == audit.SEARCH_SELECTORS.result_count:
            return _Locator(text="2")
        raise AssertionError(f"unexpected selector: {selector}")

    def wait_for_function(
        self, _script: str, *, arg: dict[str, object], timeout: int
    ) -> None:
        self.wait_arguments.append({"arg": arg, "timeout": timeout})


def test_rendered_pagination_control_is_clicked_and_enumerated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _SearchPage()
    link = _PaginationLink(page)
    response = type("Response", (), {"url": audit.SEARCH_URL})()

    monkeypatch.setattr(audit, "_navigate", lambda *_args: response)
    monkeypatch.setattr(audit, "_wait_for_search_structure", lambda *_args: None)
    monkeypatch.setattr(audit, "_redirect_chain", lambda _response: [])
    monkeypatch.setattr(audit, "_last_page_number", lambda *_args: 2)
    monkeypatch.setattr(audit, "_pagination_link", lambda *_args: link)

    def extract_page(
        _page: Any,
        *,
        page_number: int,
        extracted_at: str,
        control_href: str | None,
    ) -> tuple[list[dict[str, object]], tuple[tuple[str, tuple[str, ...]], ...]]:
        del extracted_at, control_href
        return ([{"page": page_number}], ((f"row-{page_number}", ("value",)),))

    monkeypatch.setattr(audit, "_extract_search_page", extract_page)
    records, summary = audit.extract_search(
        page,
        timeout_ms=100,
        max_pages=3,
        timeout_error=TimeoutError,
    )

    assert [record["page"] for record in records] == [1, 2]
    assert link.clicked == 1
    assert summary["coverage"] == "COMPLETE"
    assert summary["pagination_interactions"] == [
        {
            "from_page": 1,
            "to_page": 2,
            "control_href": "/befektetesi_alapok/kereses?page=2",
        }
    ]


class _CurrencyTab:
    def __init__(self) -> None:
        self.clicked = 0

    def evaluate(self, _script: str) -> None:
        self.clicked += 1

    def get_attribute(self, name: str) -> str | None:
        assert name == "id"
        return "currency-eur"


class _CurrencyPage:
    def __init__(self) -> None:
        self.pane = object()
        self.wait_argument: dict[str, object] | None = None

    def locator(self, selector: str) -> object:
        assert selector == "#pane-eur"
        return self.pane

    def wait_for_function(
        self, _script: str, *, arg: dict[str, object], timeout: int
    ) -> None:
        self.wait_argument = {"arg": arg, "timeout": timeout}


def test_rendered_currency_tab_is_clicked_and_waited_for() -> None:
    page = _CurrencyPage()
    tab = _CurrencyTab()

    pane = audit._activate_currency_tab(
        page,
        tab,
        pane_id="pane-eur",
        timeout_ms=250,
        timeout_error=TimeoutError,
    )

    assert pane is page.pane
    assert tab.clicked == 1
    assert page.wait_argument == {
        "arg": {"tabId": "currency-eur", "paneId": "pane-eur"},
        "timeout": 250,
    }


def test_output_directory_must_be_new_and_beneath_audit_root(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    audit_root = repository / "data" / "audit"
    audit_root.mkdir(parents=True)

    expected = audit_root / "run"
    assert audit._resolve_new_output_dir(
        Path("data/audit/run"), repository_root=repository
    ) == expected

    with pytest.raises(ValueError, match="parent traversal"):
        audit._resolve_new_output_dir(
            Path("data/audit/../outside"), repository_root=repository
        )
    with pytest.raises(ValueError, match="beneath"):
        audit._resolve_new_output_dir(Path("reports/run"), repository_root=repository)

    expected.mkdir()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        audit._resolve_new_output_dir(expected, repository_root=repository)


def test_output_directory_rejects_symlink_escape(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    audit_root = repository / "data" / "audit"
    audit_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (audit_root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="resolves outside"):
        audit._resolve_new_output_dir(
            Path("data/audit/escape/run"), repository_root=repository
        )
