# FLEXBRIEF-1 rollback checkpoint

Created: 2026-09-11

Rollback code word: `ROLLBACK FLEXBRIEF-1`

## Scope

This checkpoint covers only the optional-format change for generated next-turn
prompts. It does not include or reverse the earlier structured
"How the senior SWE would have acted" report section.

## Changes in this checkpoint

- Added `Plain maintainer brief without section headings` to
  `NEXT_TURN_PROMPT_STRUCTURES`.
- Renamed the prompt-bundle variation label from `Prompt structure` to
  `Suggested organization`.
- Changed `behavioral_grader/prompts/next_turn_generation.md` so headings are optional and clarity
  takes precedence over a fixed tagged layout.
- Updated the corresponding README description and regression assertions.

## Baseline to restore

When the rollback code word is requested, restore the following behavior:

1. Remove `Plain maintainer brief without section headings` from
   `NEXT_TURN_PROMPT_STRUCTURES`.
2. Change `Suggested organization:` back to `Prompt structure:` in the model
   input bundle.
3. Restore the next-turn instructions so the supplied prompt structure is used
   by default, another listed structure is used only when a heading would be
   empty or misleading, and generated prompts use three to six headings.
4. Restore the README text describing six varied Markdown layouts.
5. Restore the matching tests, then run Python compilation and
   `python -m unittest behavioral_grader.tests.test_grader`.

## Files covered

- `behavioral_grader/grader.py`
- `behavioral_grader/prompts/next_turn_generation.md`
- `behavioral_grader/tests/test_grader.py`
- `README.md`
