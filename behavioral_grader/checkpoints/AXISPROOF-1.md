# AXISPROOF-1 rollback checkpoint

Created: 2026-09-26

Rollback code word: `ROLLBACK AXISPROOF-1`

## Scope

This checkpoint covers the behavioral-grader contract changes that strengthen
axis classification, two-sided rationales, deciding evidence, and overall
grounding. It does not cover or modify transcript context limits, turn-count
completion, next-prompt generation, model selection, or sequence-ledger logic.

## Changes in this checkpoint

- Bumped the comparison contract from version 14 to version 15.
- Replaced the model-authored free-form `comparison` with required
  `model_a_finding`, `model_b_finding`, and `deciding_difference` components.
- Made the application compose the displayed Comparison from those three
  components and enforce a 150-word maximum.
- Added a required nearest-axis boundary check to every axis rationale.
- Added one deciding citation and an evidence-purpose statement for each model
  on every axis; deciding citations must belong to the selected evidence.
- Added `overall_axis_support` so every selected overall axis has direct A and B
  evidence already present in the overall citation set.
- Added an offline regression corpus for the recurring axis-confusion cases and
  expanded local validation tests and documentation.
- Reconciled valid, overlapping model-selected deciding citations into the
  displayed evidence without another comparison request. Selected overall-axis
  support ranges are materialized in the overall evidence display. This
  follow-up changed only local materialization and validation; it did not
  change the model-stage schema or contract version, so matching saved attempts
  remain eligible for offline revalidation.

## Baseline to restore

When the rollback code word is requested:

1. Restore contract version 14.
2. Restore the model-authored `comparison` field and its former 100-word cap.
3. Remove the three comparison components, the boundary-check object, deciding
   citation/purpose fields, and `overall_axis_support` from the schema, prompt,
   materialization, validation, and tests.
4. Restore the former comparison fallback that prepended a generic A/B label
   when either model was not named.
5. Remove `behavioral_grader/tests/fixtures/axis_boundary_cases.json` and the
   matching regression tests.
6. Restore the affected README and architecture wording, then run the grader
   test suite and Python compilation.

## Files covered

- `behavioral_grader/grader.py`
- `behavioral_grader/prompts/behavioral_axes.md`
- `behavioral_grader/prompts/behavioral_rubric.md`
- `behavioral_grader/prompts/behavioral_comparison.md`
- `behavioral_grader/tests/test_grader.py`
- `behavioral_grader/tests/fixtures/axis_boundary_cases.json`
- `behavioral_grader/docs/architecture.md`
- `README.md`
