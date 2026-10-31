# Next-turn request classifications

Choose the single classification that describes the prompt's primary work. The task's motivation, requested actions, main deliverable, and completion evidence must all point to that classification. Supporting investigation, tests, or documentation do not change the label. Reject a generated prompt whose title uses one category while its actual workload belongs to another.

| Classification | Required center of gravity and deliverable | Do not drift into |
|---|---|---|
| `exploration_learning` | Read-only mapping of one named workflow, its entry points, data/state/control flow, collaborators, failure paths, test seams, and unresolved questions. Deliver a source-backed map and a recommended next slice. | Editing files, prescribing a finished design, or claiming an unobserved defect. |
| `planning_requirements` | Define a bounded outcome, dependencies, explicit in/out scope, risks, open decisions, sequencing, and observable acceptance criteria. Deliver an implementation-ready plan, not implementation. | Writing code, choosing architecture without alternatives, or offering a generic backlog. |
| `design` | Evaluate realistic options and specify contracts, ownership, compatibility, failure behavior, migration, and testability. Deliver a decision or design record grounded in existing boundaries. | Implementing the design or giving an abstract architecture disconnected from the repository. |
| `debugging` | Start from a concrete reported symptom, reproduce or falsify it, localize the defect, make the smallest complete correction if confirmed, and verify the observed behavior. | Inventing a bug, broad redesign, or stopping at a cause without resolving a confirmed defect. |
| `root_cause_analysis` | Start from a confirmed failure, establish the causal chain and triggering conditions, correct the root cause, and add explicit regression prevention. Deliver cause, correction, and proof. | A symptom-only patch, speculative hypotheses presented as findings, or unrelated hardening. |
| `code_writing` | Add, change, restructure, roll back, or harden a defined behavior that is not primarily a confirmed defect. Deliver working repository changes, compatibility handling, focused verification, and a concise handoff. | Relabeling a confirmed bug fix, an analysis-only answer, or unrelated refactoring. |
| `testing` | Make the test strategy or executable verification surface the main work: expose gaps, add focused cases/fixtures/harness support, and run the relevant checks. Deliver stronger proof of behavior. | Smuggling in a feature implementation whose tests are merely secondary. |
| `system_level_investigation` | Diagnose behavior that may depend on environment, configuration, packaging, dependencies, platform, or version. Compare relevant paths, reproduce where feasible, and separate application defects from system causes. | Assuming a plain code bug before evidence or proposing production changes without a safe validation path. |
| `maintenance_ops` | Improve developer-facing observability, diagnostics, performance measurement, or maintenance workflow around a real subsystem without changing user-facing semantics. Deliver useful signals and a verification method. | Adding product features, collecting unbounded telemetry, or measuring without a decision the measurement supports. |
| `code_review` | Read-only review of existing or accumulated changes against the request and repository conventions. Deliver prioritized, evidence-linked findings with impact and concrete follow-up. | Editing files, producing a generic summary, or grading code style without consequence. |
| `deployment_infrastructure` | Address a repository-supported CI/CD, packaging, release, or runtime-delivery concern. Deliver bounded configuration/tooling changes and safe validation appropriate to the environment. | Requiring production access, secrets, or destructive rollout; generic application feature work. |
| `communication` | Create or update the specific human-facing documentation, runbook, migration guide, or design record required by observed behavior. Verify examples and commands against source where possible. | Replacing missing implementation with prose or restating internals without a reader task. |

Classification examples:

- An implementation task that also requires regression tests remains `code_writing` unless a confirmed defect and its cause are the center of the work.
- A confirmed failure requiring causal proof and prevention is `root_cause_analysis`, not ordinary `debugging`.
- A read-only flow map that recommends a later change remains `exploration_learning`.
- A packaging failure caused by dependency resolution may be `system_level_investigation`; changing the release pipeline itself is `deployment_infrastructure`.
