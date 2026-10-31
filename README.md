# Taiga Project

This workspace contains two active Python applications:

| Application | Purpose | Start here |
|---|---|---|
| [Repository Kickoff](repository_kickoff/README.md) | Find a suitable Python repository and write its first engineering task. | `repository_kickoff/repository_kickoff.py` |
| [Behavioral Grader](behavioral_grader/README.md) | Compare two model transcripts and generate the next-turn request. | `behavioral_grader/web.py` |

The grader's [learning and architecture guide](behavioral_grader/docs/architecture.md)
is the main explanation to share with a new colleague.

The shared `.venv/` and `.vscode/` folders support local development and are
not included in this repository. Each application has its own prompts,
requirements, private `.env`, tests, and output history. Credentials, run
history, downloaded repositories, and candidate transcripts remain local and
are excluded from version control.

From this directory, use the shared virtual environment:

```powershell
.\.venv\Scripts\python.exe -m behavioral_grader.web
.\.venv\Scripts\python.exe .\repository_kickoff\repository_kickoff.py --validate-only
```

The legacy long-horizon pipeline and its historical runs are archived locally,
outside this repository. They are not part of either active application's
execution path.
