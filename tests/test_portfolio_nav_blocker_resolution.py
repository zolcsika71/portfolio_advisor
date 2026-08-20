from __future__ import annotations

import pytest

from portfolio_advisor.history.portfolio_nav_blocker_resolution import (
    ALLOCATION_SEMANTICS_UNKNOWN,
    DUPLICATE_SEMANTICS_UNRESOLVED,
    DuplicateSourceRow,
    PortfolioNavBlockerResolutionError,
    classify_allocation_semantics,
    classify_duplicate_source_rows,
)


def test_hanyad_header_does_not_promote_allocation_to_portfolio_semantics() -> None:
    assert classify_allocation_semantics("Hányad (%)") == ALLOCATION_SEMANTICS_UNKNOWN


def test_duplicate_rows_remain_unresolved_without_explicit_source_sleeve() -> None:
    rows = (
        DuplicateSourceRow(4, "7", "Fund", "USD", "Bond"),
        DuplicateSourceRow(5, "6", "Fund", "USD", "Bond"),
    )

    assert classify_duplicate_source_rows(rows) == DUPLICATE_SEMANTICS_UNRESOLVED


def test_single_row_cannot_be_misclassified_as_a_duplicate_resolution() -> None:
    row = DuplicateSourceRow(4, "7", "Fund", "USD", "Bond")

    with pytest.raises(PortfolioNavBlockerResolutionError, match="at least two rows"):
        classify_duplicate_source_rows((row,))
