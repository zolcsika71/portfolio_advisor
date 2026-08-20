from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from portfolio_advisor.history.oekb import (
    OekbAcquisitionError,
    OekbHttpResponse,
    bounded_date_chunks,
    fetch_bounded_oekb_history,
)

ISIN = "AT0000605324"


def row(
    current: date,
    value: str = "10",
    *,
    isin: str = ISIN,
    currency: str = "EUR",
) -> dict[str, object]:
    return {
        "numWkn": isin,
        "datKurs": current.isoformat(),
        "numKursErrechneterWert": value,
        "waehrung": currency,
        "numWfsKu": "source-row",
    }


def paged_transport(
    payloads: dict[tuple[str, str], dict[str, object]], calls: list[dict[str, str]]
):
    def get(url: str, timeout: int) -> OekbHttpResponse:
        assert timeout == 30
        query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
        calls.append(query)
        payload = payloads[(query["von"], query["bis"])]
        rows = payload.get("list")
        if rows is None:
            return OekbHttpResponse(200, json.dumps(payload).encode())
        assert isinstance(rows, list)
        offset = int(query["offset"])
        limit = int(query["limit"])
        return OekbHttpResponse(
            200,
            json.dumps({"anz": payload["anz"], "list": rows[offset : offset + limit]}).encode(),
        )

    return get


def test_bounded_chunks_have_no_gaps_overlaps_and_final_partial_chunk() -> None:
    start = date(2025, 1, 1)
    chunks = bounded_date_chunks(start, date(2025, 7, 5))

    assert chunks == (
        (date(2025, 1, 1), date(2025, 3, 31)),
        (date(2025, 4, 1), date(2025, 6, 29)),
        (date(2025, 6, 30), date(2025, 7, 5)),
    )
    assert all((end - begin).days + 1 <= 90 for begin, end in chunks)
    assert all(chunks[index][1] + timedelta(days=1) == chunks[index + 1][0] for index in range(2))


def test_empty_chunk_without_list_does_not_stop_later_chunks() -> None:
    calls: list[dict[str, str]] = []
    second_date = date(2025, 4, 1)
    history = fetch_bounded_oekb_history(
        isin=ISIN,
        date_from=date(2025, 1, 1),
        date_to=date(2025, 4, 1),
        limit=100,
        timeout=30,
        http_get=paged_transport(
            {
                ("20250101", "20250331"): {"anz": 0},
                ("20250401", "20250401"): {"anz": 1, "list": [row(second_date)]},
            },
            calls,
        ),
    )

    assert len(history.chunks) == 2
    assert [chunk.result_status for chunk in history.chunks] == ["EMPTY", "OK"]
    assert [item.calendar_date for item in history.observations] == [second_date]
    assert [call["von"] for call in calls] == ["20250101", "20250401"]


def test_zero_result_with_explicit_empty_list_is_valid() -> None:
    calls: list[dict[str, str]] = []
    history = fetch_bounded_oekb_history(
        isin=ISIN,
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 1),
        limit=100,
        timeout=30,
        http_get=paged_transport(
            {("20250101", "20250101"): {"anz": 0, "list": []}}, calls
        ),
    )

    assert history.observations == ()
    assert history.chunks[0].reported_anz == 0


def test_pagination_uses_offset_limit_until_each_chunk_total_is_retrieved() -> None:
    start = date(2025, 1, 1)
    rows = [row(start + timedelta(days=index), str(index + 1)) for index in range(3)]
    calls: list[dict[str, str]] = []
    history = fetch_bounded_oekb_history(
        isin=ISIN,
        date_from=start,
        date_to=date(2025, 1, 3),
        limit=2,
        timeout=30,
        http_get=paged_transport(
            {("20250101", "20250103"): {"anz": 3, "list": rows}}, calls
        ),
    )

    assert [call["offset"] for call in calls] == ["0", "2"]
    assert history.chunks[0].page_count == 2
    assert history.chunks[0].retrieved_observation_count == 3


def test_pagination_that_cannot_make_progress_fails_closed() -> None:
    start = date(2025, 1, 1)
    responses = iter(
        [
            {"anz": 2, "list": [row(start)]},
            {"anz": 2, "list": []},
        ]
    )

    def get(_url: str, _timeout: int) -> OekbHttpResponse:
        return OekbHttpResponse(200, json.dumps(next(responses)).encode())

    with pytest.raises(OekbAcquisitionError, match="ended before"):
        fetch_bounded_oekb_history(
            isin=ISIN,
            date_from=start,
            date_to=start + timedelta(days=1),
            limit=1,
            timeout=30,
            http_get=get,
        )


def test_exact_isin_and_chronological_normalization() -> None:
    start = date(2025, 1, 1)
    calls: list[dict[str, str]] = []
    history = fetch_bounded_oekb_history(
        isin=ISIN,
        date_from=start,
        date_to=date(2025, 1, 3),
        limit=100,
        timeout=30,
        http_get=paged_transport(
            {
                ("20250101", "20250103"): {
                    "anz": 2,
                    "list": [row(start + timedelta(days=2)), row(start)],
                }
            },
            calls,
        ),
    )

    assert history.returned_isin == ISIN
    assert [item.calendar_date for item in history.observations] == [
        start,
        start + timedelta(days=2),
    ]


@pytest.mark.parametrize(
    ("bad_row", "message"),
    [
        (row(date(2025, 1, 1), isin="AT0000000000"), "ISIN mismatch"),
        (row(date(2025, 1, 1), value="0"), "finite and positive"),
    ],
)
def test_identity_and_nav_defects_fail_closed(
    bad_row: dict[str, object], message: str
) -> None:
    with pytest.raises(OekbAcquisitionError, match=message):
        fetch_bounded_oekb_history(
            isin=ISIN,
            date_from=date(2025, 1, 1),
            date_to=date(2025, 1, 1),
            limit=100,
            timeout=30,
            http_get=paged_transport(
                {("20250101", "20250101"): {"anz": 1, "list": [bad_row]}}, []
            ),
        )


def test_currency_mismatch_fails_closed() -> None:
    start = date(2025, 1, 1)
    with pytest.raises(OekbAcquisitionError, match="inconsistent currencies"):
        fetch_bounded_oekb_history(
            isin=ISIN,
            date_from=start,
            date_to=start + timedelta(days=1),
            limit=100,
            timeout=30,
            http_get=paged_transport(
                {
                    ("20250101", "20250102"): {
                        "anz": 2,
                        "list": [row(start, currency="EUR"), row(start + timedelta(days=1), currency="USD")],
                    }
                },
                [],
            ),
        )


def test_identical_duplicate_dates_are_retained_as_raw_evidence_and_deduplicated() -> None:
    current = date(2025, 1, 1)
    duplicate = row(current)
    history = fetch_bounded_oekb_history(
        isin=ISIN,
        date_from=current,
        date_to=current,
        limit=100,
        timeout=30,
        http_get=paged_transport(
            {("20250101", "20250101"): {"anz": 2, "list": [duplicate, duplicate]}}, []
        ),
    )

    assert len(history.raw_observations) == 2
    assert len(history.observations) == 1
    assert history.duplicate_count == 1


def test_conflicting_duplicate_dates_fail_closed() -> None:
    current = date(2025, 1, 1)
    with pytest.raises(OekbAcquisitionError, match="conflicting observations"):
        fetch_bounded_oekb_history(
            isin=ISIN,
            date_from=current,
            date_to=current,
            limit=100,
            timeout=30,
            http_get=paged_transport(
                {
                    ("20250101", "20250101"): {
                        "anz": 2,
                        "list": [row(current, "10"), row(current, "20")],
                    }
                },
                [],
            ),
        )
