# Architecture Decision Records

Architecture Decision Records (ADRs) capture important project decisions as
part of the work that makes those decisions. Architecture documentation stays
in `docs/architecture/`, and diagram artifacts stay in `docs/diagrams/`.

## When an ADR is required

Create or update an ADR for an important decision involving:

- system or component architecture and module boundaries;
- database or storage choices and schemas;
- data authority, provenance, or source-of-truth boundaries;
- public interfaces or contracts;
- dependencies with substantial operational or maintenance impact; or
- security or deployment approaches.

Routine fixes, formatting, documentation-only corrections, and simple
refactors do not require an ADR.

## Naming and numbering

- Use `ADR-NNN-<decision-description>.md`.
- Use sequential, zero-padded numbers and a short, hyphen-separated
  description.
- Inspect this index and all existing `ADR-*.md` files before selecting a
  number or creating a record.
- Use the next unused number. Never overwrite, reuse, or renumber an existing
  ADR.
- Keep every ADR directly under `docs/decisions/`.

## Status and lifecycle

Use exactly one of these status forms:

- `Proposed` for an unresolved choice.
- `Accepted` only when explicit approval or an established decision within the
  authorized task scope supports it.
- `Deprecated` when the decision no longer applies and has no replacement.
- `Superseded by ADR-NNN` when a later accepted ADR replaces it.

Before creating an ADR, check for an existing record covering the same
decision. A proposed ADR may be updated as the decision develops. If an
accepted decision changes, create a new ADR, link the new and old records, and
change the old status to `Superseded by ADR-NNN`; do not rewrite the old
decision as though it had always been different. Do not invent historical
approval or backfill unrelated decisions.

## Reusable template

```markdown
# ADR-NNN: <Decision title>

## Status

Proposed

## Context

Describe the problem, relevant constraints, and alternatives considered.

## Decision

State the chosen approach and why it was selected.

## Consequences

Describe benefits, trade-offs, limitations, and follow-up obligations.
```

## ADR index

| ADR | Status | Decision |
| --- | --- | --- |
| [ADR-001](ADR-001-adopt-architecture-decision-records.md) | Accepted | Adopt a persistent ADR workflow for important project decisions. |
