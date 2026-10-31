# Taiga Project

A local Python workspace for studying how two coding models handle the same software engineering task. It helps a reviewer choose a suitable GitHub repository, frame an initial task, compare the two resulting transcripts on **behavior**, and draft a grounded follow-up.

The main application is **Behavioral Review Desk**. It evaluates how the models communicated, followed instructions, managed scope and risk, and supported their claims. It does not run the candidate models or certify the quality of their code.

## The workflow at a glance

| Step | What you provide | What you get |
| --- | --- | --- |
| [Repository Kickoff](repository_kickoff/README.md) | An API key and the repository-selection criteria built into the tool | A checked Python repository recommendation, a pinned source reference, a first engineering request, and a content-risk report |
| Run the task in two coding models | The same repository and request for Model A and Model B | Two exported run transcripts, produced outside this workspace |
| [Behavioral Grader](behavioral_grader/README.md) | Both transcripts, the task request, the public repository URL, and a turn number | A downloadable Markdown comparison, an overall preference, and a proposed next turn |

For later turns, the grader saves the selected run's context in a local sequence ledger. The next evaluation can build on that winner without sending every earlier raw transcript to the prompt-generation stage.

## See the grader in action

The web interface runs on your computer. You need your own two model-run files (`.txt`, `.md`, `.json`, or `.log`), a public GitHub repository URL, and an OpenAI API key. No sample transcripts or private run history are published in this repository.

From the project root in **PowerShell**:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\behavioral_grader\requirements.txt
Copy-Item .\behavioral_grader\.env.example .\behavioral_grader\.env
```

Open `behavioral_grader/.env` locally and set `OPENAI_API_KEY`. Then start the server:

```powershell
.\.venv\Scripts\python.exe -m behavioral_grader.web
```

Open **http://127.0.0.1:8766/**. Upload the two run files, enter the repository URL, and choose Turn 1 for a fresh sequence. Paste the task request if the files do not both contain the same latest user request. Select **Grade & download .md**. Later turns use Turn 2–30 and the preceding saved winner.

The downloaded report has four review sections:

1. What a senior engineer would have been expected to do for the task.
2. Cited A/B comparisons for Honesty, Agentic Safety, Scoping, Deference, Interaction, Confidence, and Clarity.
3. The overall preference, its deciding axes, and searchable evidence snippets.
4. A proposed next engineering prompt, with notes on its scope and difficulty.

The report is an **AI-generated review aid**. Check its citations and judgment against the original transcripts before using it as a human evaluation.

## What happens after you click Grade

```text
Browser uploads
    → local input and repository preflight
    → current-turn extraction from both transcripts
    → behavioral comparison model + local citation validation
    → selected-run checkpoint
    → next-prompt model + local validation
    → Markdown download and local sequence ledger
```

These are two sequential model calls with different jobs, orchestrated by Python; the application does not deploy autonomous agents. The comparison stage receives the current effective A/B turns. The next-prompt stage receives a compact selected-run checkpoint and bounded repository context. Input checks run before paid requests, while output checks verify the model's structured response and cited evidence afterward. Identical validated stages can be reused locally.

For the full explanation of inputs, APIs, validation, persistence, and known limitations, read the [architecture guide](behavioral_grader/docs/architecture.md). Designed [HTML](behavioral_grader/docs/behavioral_grader_guide.html) and [PDF](behavioral_grader/docs/behavioral_grader_guide.pdf) versions are also included.

## Try Repository Kickoff

This companion command searches for a suitable Python repository, verifies a selected GitHub snapshot, scans the downloaded source for content risks, and writes a first read-only engineering investigation. Its detailed [README](repository_kickoff/README.md) explains the selection criteria and review boundary.

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\repository_kickoff\requirements.txt
Copy-Item .\repository_kickoff\.env.example .\repository_kickoff\.env
.\.venv\Scripts\python.exe .\repository_kickoff\repository_kickoff.py --validate-only
.\.venv\Scripts\python.exe .\repository_kickoff\repository_kickoff.py
```

Set `OPENAI_API_KEY` in `repository_kickoff/.env` before the full run. `--validate-only` checks local configuration without making model requests. The full run uses OpenAI requests and GitHub access; its handoff and run records are saved locally.

## Where to look in the code

| Path | Role |
| --- | --- |
| [`behavioral_grader/web.py`](behavioral_grader/web.py) | Localhost form, request handler, download, and sequence persistence |
| [`behavioral_grader/grader.py`](behavioral_grader/grader.py) | Preflight, both model stages, evidence validation, and report rendering |
| [`behavioral_grader/prompts/`](behavioral_grader/prompts/) | Versioned behavioral rubric and next-turn instructions |
| [`repository_kickoff/repository_kickoff.py`](repository_kickoff/repository_kickoff.py) | Repository discovery, verification, and first-task orchestration |
| [`repository_kickoff/prompts/`](repository_kickoff/prompts/) | Repository scout and first-task writer instructions |

Run the local test suites with:

```powershell
.\.venv\Scripts\python.exe -m unittest behavioral_grader.tests.test_grader
.\.venv\Scripts\python.exe -m unittest repository_kickoff.test_archive_preparation repository_kickoff.test_content_safety repository_kickoff.test_repository_kickoff repository_kickoff.test_sanitization
```

Both applications write detailed inputs and results to ignored local `outputs/` directories. Those records can contain complete transcripts. `.env` files, run history, downloaded repositories, and the archived legacy pipeline are excluded from Git; keep local run records private. Model calls consume API tokens, and the repository snapshot used by the grader is bounded rather than a substitute for inspecting the full source tree.
