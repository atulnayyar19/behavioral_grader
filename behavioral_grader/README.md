# AI Model Behavioral Grader

This package contains the complete two-stage behavioral evaluation workflow:

1. `grader.py` compares the current effective turn from two model transcripts,
   validates cited behavioral reasoning, records the winning checkpoint, and
   renders the Markdown report.
2. The next-prompt stage in `grader.py` uses the selected checkpoint and bounded
   repository context to create the next realistic engineering request.
3. `web.py` provides the localhost upload and download interface.

## Layout

```text
behavioral_grader/
  grader.py              comparison, validation, rendering, next-prompt stage
  web.py                 localhost UI and sequence-ledger persistence
  prompt_loader.py       package-local Markdown prompt loading
  prompts/               comparison and next-prompt instructions
  tests/                 grader and UI regression tests
```

Runtime history is under `behavioral_grader/outputs/grader_attempts/` and
`behavioral_grader/outputs/grader_sequences/`. Existing attempts and sequence
ledgers were moved intact. The server reads `behavioral_grader/.env` (or the
process environment) for its API key.

For the canonical beginner-to-technical walkthrough—including inputs, outputs,
the two AI roles, every processing stage, and an inspection of known limits—read
[`docs/architecture.md`](docs/architecture.md).
For a designed, offline-readable version of the same guide, open
[`docs/behavioral_grader_guide.html`](docs/behavioral_grader_guide.html) or
[`docs/behavioral_grader_guide.pdf`](docs/behavioral_grader_guide.pdf).
After editing the canonical Markdown, rebuild both exports with
`node behavioral_grader/docs/build_guide_exports.js` (requires the `marked`
package and Chrome or Edge).

## Run

From the repository root, install the grader dependency if needed, then run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\behavioral_grader\requirements.txt
.\.venv\Scripts\python.exe -m behavioral_grader.web
```

If configuring the key for the first time, copy
`behavioral_grader/.env.example` to `behavioral_grader/.env` and fill in the key.

For the JSON CLI:

```powershell
.\.venv\Scripts\python.exe -m behavioral_grader.grader --input .\comparison.json
```

Run the package tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest behavioral_grader.tests.test_grader
```
