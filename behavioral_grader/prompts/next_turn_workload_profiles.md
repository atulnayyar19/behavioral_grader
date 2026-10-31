# Classification-specific depth standards

Apply the standard for the classification you select. These standards describe
engineering depth, not a mandatory writing template. Every activity must serve
one connected outcome.

- `exploration_learning`: trace one consequential workflow across its entry
  point, state or data owners, callers or consumers, failure paths, and test
  seams. Finish with a source-grounded decision about the next implementation
  slice rather than a generic repository tour.
- `planning_requirements`: resolve dependencies, boundaries, risks, sequencing,
  compatibility, and acceptance evidence for a change that is substantial
  enough to implement. Produce an implementation-ready decision set, not a
  short backlog.
- `design`: compare realistic repository-compatible options and settle
  ownership, contracts, failure behavior, migration, and testability across the
  affected modules. Leave a reviewable decision and rollout plan.
- `debugging`: start from an observed symptom, reproduce or falsify it, trace the
  responsible path, implement the complete correction when confirmed, and add
  regression proof across the affected boundary.
- `root_cause_analysis`: establish trigger, causal chain, enabling conditions,
  correction, and prevention for a confirmed failure. A report without the
  evidence-backed correction is incomplete when implementation is authorized.
- `code_writing`: deliver the complete behavior at its owning boundary, update
  directly coupled callers or consumers, cover failure and compatibility paths,
  and provide focused plus broader verification.
- `testing`: make executable proof the primary outcome. Cover the public entry
  point, interacting boundary cases, failure behavior, and any fixture or
  harness work required to exercise the real contract without mocking it away.
- `system_level_investigation`: distinguish application, dependency,
  configuration, packaging, platform, and environment causes using controlled
  reproductions. Correct confirmed repository-owned causes and preserve a safe
  validation path.
- `maintenance_ops`: improve diagnostics, observability, performance evidence,
  or maintainer workflow around a real subsystem. Connect signals to an
  actionable decision and verify both normal and failure behavior.
- `code_review`: review the complete accumulated change across producers,
  consumers, state transitions, error paths, compatibility, tests, and
  documentation. Return prioritized, evidence-linked findings and the smallest
  coherent remediation slice.
- `deployment_infrastructure`: trace build, package, CI, artifact, and delivery
  contracts end to end; implement bounded fail-safe mechanics and validate
  negative paths without requiring production credentials or destructive
  rollout.
- `communication`: update a substantive runbook, migration guide, or technical
  contract by verifying every important claim and command against the owning
  source. Reconcile conflicting guidance and test examples where possible; do
  not use prose as a substitute for missing implementation.

For a lighter-follow-up-permitted turn, the same classification semantics
apply, but a smaller correction or clarification is acceptable when it is the
most natural next request. Never enlarge a task with unrelated chores merely to
make it look substantial.
