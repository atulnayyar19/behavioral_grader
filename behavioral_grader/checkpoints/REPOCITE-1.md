# REPOCITE-1 rollback checkpoint

Created: 2026-09-18

Rollback code word: `ROLLBACK REPOCITE-1`

## Scope

This checkpoint covers the Turn 12 behavioral-comparison fix for optional
repository-fallback citations in the structured senior-engineer baseline.

## Root cause addressed

The prompt instructed the grader to cite source `R` only when the bounded
repository snapshot materially informed the senior-engineer baseline. The local
validator instead required an `R` citation whenever the GitHub fetch succeeded.
That mismatch rejected otherwise complete, schema-valid comparisons and could
trigger a redundant paid request.

## Changes in this checkpoint

- Removed the unconditional `R`-citation requirement from local validation.
- Preserved strict range and source-label validation whenever `R` is cited.
- Added regression coverage for both prompt-only grounding and invalid `R`
  ranges.
- Documented the material-use rule in the project README.

## Cost and recovery behavior

The model prompt, structured-output schema, and contract version are unchanged.
Therefore the existing exact-fingerprint recovery path can revalidate the saved
Turn 12 schema-valid attempt instead of rebilling the comparison stage.

## Baseline to restore

When the rollback code word is requested:

1. Restore the unconditional validator error when repository context is present
   but `senior_engineer_citations` has no `R` citation.
2. Restore the former regression test that requires `R` whenever repository
   context is supplied.
3. Remove the README explanation that `R` is conditional on material use.
4. Run Python compilation and
   `python -m unittest behavioral_grader.tests.test_grader`.

## Files covered

- `behavioral_grader/grader.py`
- `behavioral_grader/tests/test_grader.py`
- `README.md`
