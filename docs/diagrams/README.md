# Technical diagrams

This directory contains project-owned PlantUML source files. Architecture
documents remain in `docs/architecture/`, and decision records remain in
`docs/decisions/`.

The governing decision is
[ADR-002](../decisions/ADR-002-adopt-plantuml-for-detailed-technical-diagrams.md).

## Choosing a format

Use Markdown prose, tables, or Mermaid for straightforward explanations and
small diagrams. Prefer PlantUML when the diagram needs detailed sequences,
complex state machines, component interfaces, error or retry paths,
conditional processing, or security and trust boundaries. Do not convert a
clear existing Mermaid diagram merely for consistency, and do not create a
diagram without a concrete documentation need.

During relevant development or documentation work, assess that need as part of
the task rather than waiting for a separate diagram request. Check the index
and existing sources first; update a diagram that already covers the behavior
instead of creating a duplicate. This assessment does not authorize work
outside the task's scope or turn diagram creation into a background process.

## Location and naming

- Store every new `.puml` file under `docs/diagrams/`.
- Use lowercase, hyphen-separated filenames that identify the subject and
  diagram kind, such as `supplementary-nav-admission-sequence.puml`.
- Use a subject subdirectory when several related diagrams or shared includes
  benefit from grouping. Preserve relative include paths within that subtree.
- Avoid spaces, ambiguous abbreviations, and generic names such as
  `diagram.puml`.
- Add each maintained PlantUML source to the index below.
- Link a diagram from the relevant maintained Markdown when it clarifies that
  document's implementation or contract.

## Accuracy and lifecycle

Diagrams must reflect verified code, schemas, or governed contracts. Record the
verification basis in leading PlantUML comments, using repository-relative
paths and contract or schema versions where applicable. Give the rendered
diagram a visible status in its title or a prominent note:

- `Implemented` means the depicted behavior was verified against the current
  implementation or installed contract.
- `Proposed` means the diagram describes a design that is not yet implemented;
  link the governing proposed ADR or design document.

Do not infer missing behavior or present a proposal as current behavior. Update
or retire a diagram when its verified basis changes.

Suggested source header:

```plantuml
@startuml
' Status: Implemented
' Verified against: src/portfolio_advisor/example.py and contract v1
title Example flow [Implemented]

' Diagram content
@enduml
```

## PyCharm preview and validation

Open a `.puml` file in PyCharm and use the installed PlantUML integration's
preview pane or preview action. Check that the full diagram renders, labels are
readable, includes resolve relative to the source, and the visible status
matches the documented basis. When a local PlantUML command-line validator is
available, validate changed sources as an additional check; do not install or
change tooling merely to validate documentation.

## Diagram index

### PlantUML sources

- [Broker snapshot import](broker-snapshot-import.puml) — confirmed George PDF
  discovery, validation, batch persistence, conflicts, and retries.
- [LTIA cash correction](ltia-cash-correction.puml) — screenshot retention,
  explicit CASH supersession, exact replay, and current selection.
- [Supplementary NAV validation and retries](supplementary-nav-validation-and-retries.puml)
  — provenance planning, isolated admission, strict legacy inspection, and v2
  retry authorization.
- [Shortlist zero-to-NULL correction](shortlist-zero-null-correction.puml) —
  immutable evidence bindings, transactional admission, exact replay, effective
  selection, and copy-on-write re-import validation.
- [Shortlist classification correction](shortlist-classification-correction.puml)
  — immutable source labels, ordered sub-asset and asset/sub-asset pair
  mappings, historical-stage resolution, replay, and copy-on-write re-import
  validation.
- [Model-portfolio consolidation and cutover](model-portfolio-consolidation-cutover.puml)
  — implemented non-operational Phase 1 contracts and Phase 3A read-only
  shadow comparison, the implemented parser-only BIFF envelope and synthetic
  writer, plus proposed single-writer admission, cutover, and post-cutover
  recovery boundaries.

### Existing diagrams retained in place

- [Schema-v3 central analytical ERD](../architecture/milestone_4_schema_v3_design.md#schema-v3-central-analytical-erd)
  — a straightforward inline Mermaid diagram that remains in its architecture
  document.
