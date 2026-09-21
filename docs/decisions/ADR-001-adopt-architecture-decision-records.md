# ADR-001: Adopt Architecture Decision Records

## Status

Accepted

## Context

Important project decisions affect long-lived architecture, storage, data
authority, public contracts, dependencies, security, and deployment. These
decisions need a durable record that explains the constraints and alternatives
considered, rather than relying only on task conversation, milestone reports,
or architecture descriptions.

The alternatives considered were continuing to record decisions only in the
documents they affect, using ADRs optionally, or establishing a repository-wide
ADR workflow. Scattered or optional records would not reliably tell future
contributors where to find decision rationale. The repository must also avoid
inventing historical approvals, backfilling unrelated decisions, or treating a
formatting example as authorization for a substantive technical choice.

## Decision

Adopt ADRs under `docs/decisions/` for important project decisions. Codex must
consult the ADR index and existing records before making such a decision and
must create or update the relevant ADR as part of the same authorized work.

ADRs use sequential, zero-padded filenames of the form
`ADR-NNN-<decision-description>.md` and the required Status, Context, Decision,
and Consequences sections. Unresolved choices are `Proposed`. A record is
`Accepted` only with explicit approval or an established decision within the
authorized task scope. Changing an accepted decision requires a new ADR linked
to the superseded record; existing ADR numbers are never overwritten, reused,
or renumbered.

This ADR is accepted because the user explicitly approved adoption of this
repository convention. It does not approve any separate architecture,
database, or storage decision.

## Consequences

Important decisions will have a discoverable rationale and lifecycle, and
future Codex tasks will apply the same numbering and status rules. The workflow
adds a small documentation obligation to decision-making tasks and requires
checking for related ADRs before creating a new one. Routine fixes, formatting,
and simple refactors remain outside the ADR requirement. Architecture documents
and diagram artifacts remain in their dedicated directories, and historical
decisions are not backfilled without separate evidence and authorization.
