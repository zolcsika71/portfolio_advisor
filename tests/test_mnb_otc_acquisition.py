from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from portfolio_advisor.history import mnb_otc_acquisition as acquisition

SEARCH_HTML = """
<table><tbody><tr>
<td class="center">2024.12.02. 14:22:58</td><td></td>
<td>KELER Központi Értéktár Zártkörűen Működő Részvénytársaság</td>
<td class="break">Heti OTC file-ok 2024.11.25. - 2024.12.01.</td><td>rendszeres tájékoztatás</td><td>egyéb</td>
<td><a class="clickable" href="/kozzetetelek?viewid=K564686/2024">K564686/2024</a></td>
</tr></tbody></table>
"""
DETAIL_HTML = """
<table><tbody><tr><td>OTC_HETI_20241129.pdf</td><td>hu-HU</td>
<td><a class="clickable" href="../downloadkozzetetel?id=1&amp;did=K564686/2024">Megtekintés</a></td>
</tr></tbody></table>
"""


class Response:
    def __init__(
        self, status_code: int, content: bytes, content_type: str = "text/html"
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(content)),
        }


class Client:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def post(self, url: str, **kwargs: object) -> Response:
        self.calls.append(("POST", url))
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: object) -> Response:
        self.calls.append(("GET", url))
        return self.responses.pop(0)


def _listing() -> tuple[acquisition.OfficialReportListing, ...]:
    return acquisition.parse_search_listing(
        SEARCH_HTML, start=date(2024, 7, 2), end=date(2025, 6, 4)
    )


def test_official_listing_is_bounded_and_parses_exact_period() -> None:
    listing = _listing()

    assert len(listing) == 1
    assert listing[0].publication_id == "K564686/2024"
    assert listing[0].period_start == date(2024, 11, 25)
    assert listing[0].period_end == date(2024, 12, 1)


def test_listing_rejects_non_keler_or_malformed_otc_period() -> None:
    assert (
        acquisition.parse_search_listing(
            SEARCH_HTML.replace("KELER Központi Értéktár", "Other publisher"),
            start=date(2024, 7, 2),
            end=date(2025, 6, 4),
        )
        == ()
    )
    with pytest.raises(
        acquisition.MnbOtcAcquisitionError, match="deterministic period"
    ):
        acquisition.parse_search_listing(
            SEARCH_HTML.replace("2024.11.25. - 2024.12.01.", "period unknown"),
            start=date(2024, 7, 2),
            end=date(2025, 6, 4),
        )


def test_listing_ignores_otc_statistics_not_marked_as_report_files() -> None:
    assert (
        acquisition.parse_search_listing(
            SEARCH_HTML.replace(
                "Heti OTC file-ok 2024.11.25. - 2024.12.01.", "Heti OTC statisztika"
            ),
            start=date(2024, 7, 2),
            end=date(2025, 6, 4),
        )
        == ()
    )


def test_acquisition_validates_pdf_and_is_idempotent_by_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(acquisition.time, "sleep", lambda _: None)
    pdf = b"%PDF-1.7\nvalidated official report"
    first = Client(
        [Response(200, DETAIL_HTML.encode()), Response(200, pdf, "application/pdf")]
    )

    records = acquisition.acquire_official_reports(_listing(), tmp_path, client=first)

    assert records[0].status == "REPORT_ACQUIRED"
    assert (tmp_path / "OTC_HETI_20241129.pdf").read_bytes() == pdf
    second = Client(
        [Response(200, DETAIL_HTML.encode()), Response(200, pdf, "application/pdf")]
    )
    duplicate = acquisition.acquire_official_reports(
        _listing(), tmp_path, client=second
    )
    assert duplicate[0].status == "REPORT_ACQUIRED_DUPLICATE"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (Response(200, b"<html>error</html>", "text/html"), "application/pdf"),
        (Response(200, b"not a pdf", "application/pdf"), "magic bytes"),
        (Response(404, b"missing"), "HTTP 404"),
    ],
)
def test_acquisition_rejects_invalid_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: Response, message: str
) -> None:
    monkeypatch.setattr(acquisition.time, "sleep", lambda _: None)
    client = Client([Response(200, DETAIL_HTML.encode()), response])

    result = acquisition.acquire_official_reports(_listing(), tmp_path, client=client)

    assert result[0].status == "REPORT_ACQUISITION_FAILED"
    assert message in (result[0].error or "")
    assert not list(tmp_path.glob("*.pdf"))


def test_non_authoritative_url_and_unbounded_interval_fail_closed() -> None:
    with pytest.raises(acquisition.MnbOtcAcquisitionError, match="non-authoritative"):
        acquisition._assert_official_url("https://example.com/report.pdf")
    with pytest.raises(acquisition.MnbOtcAcquisitionError, match="bounded maximum"):
        acquisition.validate_interval(date(2024, 1, 1), date(2026, 1, 1))


def test_discovery_uses_only_the_fixed_public_search_endpoint() -> None:
    client = Client([Response(200, SEARCH_HTML.encode())])
    result = acquisition.discover_official_reports(
        date(2024, 7, 2), date(2025, 6, 4), client=client
    )

    assert len(result) == 1
    assert client.calls == [("POST", acquisition.SEARCH_URL)]
