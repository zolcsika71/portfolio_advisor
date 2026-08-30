# Milestone 9 — Shortlist relational import

The shortlist is imported as a dated investment universe only, never as a
portfolio, candidate, ranking input or recommendation. Valid explicit ISINs
reuse canonical instruments or add a source-supported instrument and
`SHORTLIST_XLS` alias. Provider metrics remain provenance-tagged observations.

The retained corpus's 2026-04-01 shortlist sheet reports `LU0251131958` twice
with conflicting names and classifications. Both immutable source occurrences
are retained and linked to one unique membership marked
`SOURCE_METADATA_CONFLICT`; no name or classification is selected or merged.
All sheets are imported copy-on-write with explicit apply:

```bash
poetry run python scripts/integrate_schema_v3_shortlist.py
poetry run python scripts/integrate_schema_v3_shortlist.py --apply
poetry run python scripts/validate_schema_v3_shortlist.py
```

Cutover remains **NOT_AUTHORIZED**.
