# ADR-002: Adopt PlantUML for Detailed Technical Diagrams

## Status

Accepted

## Context

Portfolio Advisor uses Markdown for documentation and has an inline Mermaid
diagram for a straightforward schema overview. More detailed technical flows
can require precise sequencing, complex state transitions, component
interfaces, error and retry paths, conditional branches, or explicit security
and trust boundaries. Those concerns can become difficult to review and
maintain in prose or a simple Mermaid diagram.

The alternatives considered were using only prose and Mermaid, requiring
PlantUML for every diagram, or selecting the format according to diagram
complexity. Using only the existing formats would constrain detailed diagrams,
while requiring PlantUML universally would add needless source and tooling
overhead to straightforward explanations.

## Decision

Keep Markdown and Mermaid for straightforward explanations. Use PlantUML when
they are insufficient for a detailed technical diagram, particularly for
detailed sequences, complex state machines, component interfaces, error or
retry paths, conditional processing, and security or trust boundaries.

All newly created `.puml` sources must be stored under
[`docs/diagrams/`](../diagrams/README.md) and listed in its index. Each diagram
must be grounded in verified code or contracts, identify that basis, and
clearly label itself `Implemented` when it describes verified current behavior
or `Proposed` when it describes a design that is not yet implemented. Existing
useful Mermaid diagrams are not converted solely to satisfy this convention.

This ADR is accepted because the user explicitly approved this documentation
workflow. It does not approve any particular proposed system behavior or
authorize creation of an otherwise unnecessary diagram.

## Consequences

Detailed technical diagrams gain a consistent, versioned source format and a
predictable location. Reviewers can distinguish current behavior from proposed
designs and trace diagrams back to verified sources. Contributors must choose
the least complex adequate format, maintain the diagram index, keep relative
includes valid, and preview or validate changed PlantUML sources when tooling
is available. This adds a small documentation and validation obligation but
does not require converting the existing Mermaid ERD or creating placeholder
diagrams.
