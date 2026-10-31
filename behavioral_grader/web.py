#!/usr/bin/env python3
"""Local web UI for the behavioral grader package.

The browser reads two model-run files, sends their text to this localhost-only
server, and receives the validated evaluation as a Markdown download. Canonical
inputs and output-validation checkpoints are saved locally for diagnosis.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

from .grader import (
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    PROMPT_MODEL,
    PROMPT_REASONING_EFFORT,
    PROMPT_MAX_OUTPUT_TOKENS,
    DEFAULT_VALIDATION_REPAIRS,
    NEXT_TURN_REQUEST_TYPES,
    EvaluationInput,
    GraderError,
    call_judge,
    input_from_payload,
    latest_user_task_prompt,
    normalize_repository_url,
    render_markdown,
    validate_task_prompt_alignment,
)

MAX_REQUEST_BYTES = 64 * 1024 * 1024
PACKAGE_DIRECTORY = Path(__file__).resolve().parent
SEQUENCE_DIRECTORY = PACKAGE_DIRECTORY / "outputs" / "grader_sequences"
SEQUENCE_LEDGER_FILENAME = "sequence_ledger.json"
SEQUENCE_LEDGER_SCHEMA_VERSION = 6
DOTENV_KEYS = {"OPENAI_API_KEY"}


def load_local_dotenv(path: Path) -> bool:
    """Load supported local configuration without overriding process settings."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise GraderError(f"Cannot read local configuration file {path.name}: {exc}") from exc

    loaded_key = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in DOTENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        if value and key not in os.environ:
            os.environ[key] = value
            loaded_key = loaded_key or key == "OPENAI_API_KEY"
    return loaded_key


def _sequence_id(repository_url: str) -> str:
    parsed_path = repository_url.rstrip("/").rsplit("/", 2)
    readable = "-".join(parsed_path[-2:]) if len(parsed_path) >= 2 else "repository"
    readable = re.sub(r"[^a-zA-Z0-9._-]+", "-", readable).strip("-.")[:60] or "repository"
    digest = hashlib.sha256(repository_url.rstrip("/").casefold().encode("utf-8")).hexdigest()[:12]
    return f"{readable}-{digest}"


def _turn_directory(root: Path, repository_url: str, turn_number: int) -> Path:
    return root / _sequence_id(repository_url) / f"turn_{turn_number:02d}"


def _ledger_path(root: Path, repository_url: str) -> Path:
    return root / _sequence_id(repository_url) / SEQUENCE_LEDGER_FILENAME


def _compact_ledger_text(value: Any, limit: int = 1_200) -> str:
    text = re.sub(r"\[(?:P|T|A|B|C|D|E|R):L\d+(?:-L\d+)?\]", "", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _compact_ledger_list(values: Any, *, limit: int, item_limit: int = 1_200) -> list[str]:
    if not isinstance(values, list):
        return []
    compact: list[str] = []
    for value in values:
        item = _compact_ledger_text(value, item_limit)
        if item and item not in compact:
            compact.append(item)
        if len(compact) >= limit:
            break
    return compact


def _legacy_sequence_checkpoint(record: Mapping[str, Any]) -> dict[str, Any]:
    """Create a bounded checkpoint from a pre-ledger sequence record."""
    winning = record.get("winning_context")
    if not isinstance(winning, dict):
        raise GraderError("A saved turn does not contain winning context.")
    summary = _compact_ledger_text(winning.get("selected_run_summary"))
    verification = [summary] if re.search(
        r"(?i)\b(test|check|verif|pytest|ruff|mypy|build|pass|fail|green|unverified|blocked)\w*\b",
        summary,
    ) else []
    risks = [summary] if re.search(
        r"(?i)\b(unverified|blocked|remaining|risk|failed|failure|could not|unable)\b",
        summary,
    ) else []
    return {
        "requested_outcome": _compact_ledger_text(winning.get("prompt_given_to_models"), 1_600),
        "work_observed": [summary] if summary else ["Legacy record has no selected-run summary."],
        "artifacts": [],
        "verification": verification,
        "decisions_and_constraints": _compact_ledger_list(
            [winning.get("selection_reason")], limit=12
        ),
        "open_risks": risks,
        "unresolved_contracts": [],
        "affected_callers_and_consumers": [],
        "candidate_next_seams": [],
        "verification_gaps": risks,
    }


def _ledger_turn_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    winning = record.get("winning_context")
    if not isinstance(winning, dict):
        raise GraderError("A saved turn does not contain winning context.")
    checkpoint = record.get("sequence_checkpoint")
    if not isinstance(checkpoint, dict):
        checkpoint = _legacy_sequence_checkpoint(record)
    task_prompt = winning.get("prompt_given_to_models")
    if not isinstance(task_prompt, str):
        task_prompt = ""
    task_prompt_type = record.get("task_prompt_type")
    if not isinstance(task_prompt_type, str):
        task_prompt_type = ""
    raw_budget = record.get("candidate_raw_log_token_budget")
    compact_raw_budget: dict[str, dict[str, Any]] = {}
    if isinstance(raw_budget, Mapping):
        for label in ("model_a", "model_b"):
            entry = raw_budget.get(label)
            if not isinstance(entry, Mapping):
                continue
            compact_raw_budget[label] = {
                key: entry[key]
                for key in (
                    "available",
                    "measurement",
                    "scope",
                    "exact_per_turn_usage_available",
                    "checkpoint_count",
                    "initial_remaining_tokens",
                    "latest_remaining_tokens",
                    "estimated_budget_consumed_tokens",
                    "snapshots_nonincreasing",
                    "note",
                )
                if key in entry
            }
    turn = {
        "turn_number": int(record.get("turn_number", 0)),
        "created_at": _compact_ledger_text(record.get("created_at"), 80),
        # Keep the verbatim user task for local review and future exports. This
        # field is intentionally omitted from the model-facing continuation
        # packet below so historic prompts do not increase prompt-generation cost.
        "task_prompt": task_prompt,
        "task_prompt_type": _compact_ledger_text(task_prompt_type, 80),
        "selected_model": _compact_ledger_text(winning.get("selected_model"), 40),
        "selected_response_label": _compact_ledger_text(
            winning.get("selected_response_label"), 1
        ),
        "requested_outcome": _compact_ledger_text(checkpoint.get("requested_outcome"), 1_600),
        "work_observed": _compact_ledger_list(checkpoint.get("work_observed"), limit=8),
        "artifacts": _compact_ledger_list(checkpoint.get("artifacts"), limit=20, item_limit=240),
        "verification": _compact_ledger_list(checkpoint.get("verification"), limit=12),
        "decisions_and_constraints": _compact_ledger_list(
            checkpoint.get("decisions_and_constraints"), limit=12
        ),
        "open_risks": _compact_ledger_list(checkpoint.get("open_risks"), limit=12),
        "unresolved_contracts": _compact_ledger_list(
            checkpoint.get("unresolved_contracts"), limit=12
        ),
        "affected_callers_and_consumers": _compact_ledger_list(
            checkpoint.get("affected_callers_and_consumers"), limit=12, item_limit=320
        ),
        "candidate_next_seams": _compact_ledger_list(
            checkpoint.get("candidate_next_seams"), limit=12
        ),
        "verification_gaps": _compact_ledger_list(
            checkpoint.get("verification_gaps"), limit=12
        ),
        "next_request_type": _compact_ledger_text(
            winning.get("generated_next_turn_request_type"), 80
        ),
        # Keep this local-review summary out of the model-facing continuation
        # payload because it is not useful evidence for the next task.
        "candidate_raw_log_token_budget": compact_raw_budget,
    }
    senior_behavior = winning.get("expected_senior_engineer_behavior")
    if isinstance(senior_behavior, Mapping):
        # Retain the validated structured write-up in the durable local ledger
        # for review and export. load_prior_winning_context deliberately omits
        # it from the model-facing continuation packet to avoid token growth.
        turn["expected_senior_engineer_behavior"] = dict(senior_behavior)
    return turn


def _write_sequence_ledger(path: Path, ledger: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def rebuild_sequence_ledger(root: Path, repository_url: str) -> Path:
    """Rebuild a compact ledger from the latest durable record for every turn."""
    sequence_dir = root / _sequence_id(repository_url)
    path = _ledger_path(root, repository_url)
    prior_types: dict[int, str] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GraderError(f"Cannot read the existing sequence ledger: {exc}") from exc
        existing_turns = existing.get("turns") if isinstance(existing, dict) else None
        if isinstance(existing_turns, list):
            for existing_turn in existing_turns:
                if not isinstance(existing_turn, dict):
                    continue
                value = existing_turn.get("task_prompt_type")
                if isinstance(value, str) and value in NEXT_TURN_REQUEST_TYPES:
                    prior_types[int(existing_turn.get("turn_number", 0))] = value
    turns: list[dict[str, Any]] = []
    for turn_dir in sorted(sequence_dir.glob("turn_[0-9][0-9]")):
        records = sorted(turn_dir.glob("evaluation_*.json"))
        if not records:
            continue
        invalidated: set[str] = set()
        invalidation_path = turn_dir / "invalidation.json"
        if invalidation_path.is_file():
            try:
                invalidation = json.loads(invalidation_path.read_text(encoding="utf-8"))
                names = invalidation["invalidated_records"]
                if not isinstance(names, list) or not all(
                    isinstance(name, str) and name.startswith("evaluation_")
                    and name.endswith(".json") and Path(name).name == name
                    for name in names
                ):
                    raise ValueError("invalidated_records must contain evaluation filenames")
                invalidated = set(names)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise GraderError(f"Cannot read sequence invalidation {invalidation_path}: {exc}") from exc
        active_records = [record for record in records if record.name not in invalidated]
        if not active_records:
            continue
        for active_record in reversed(active_records):
            try:
                record = json.loads(active_record.read_text(encoding="utf-8"))
                if not isinstance(record, dict):
                    raise GraderError("Saved evaluation record must be an object.")
                if str(record.get("record_status", "")).startswith("invalidated"):
                    continue
                turn = _ledger_turn_from_record(record)
            except (OSError, ValueError, TypeError, json.JSONDecodeError, GraderError) as exc:
                raise GraderError(f"Cannot rebuild the sequence ledger from {active_record.name}: {exc}") from exc
            break
        else:
            continue
        if turn["turn_number"] > 0:
            if not turn["task_prompt_type"]:
                turn["task_prompt_type"] = prior_types.get(turn["turn_number"], "")
            turns.append(turn)
    ledger = {
        "schema_version": SEQUENCE_LEDGER_SCHEMA_VERSION,
        "repository_url": repository_url,
        "sequence_id": _sequence_id(repository_url),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "latest_turn": max((turn["turn_number"] for turn in turns), default=0),
        "turns": turns,
    }
    _write_sequence_ledger(path, ledger)
    return path


def _upsert_sequence_ledger(root: Path, repository_url: str, turn: Mapping[str, Any]) -> Path:
    path = _ledger_path(root, repository_url)
    if path.is_file():
        try:
            ledger = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GraderError(f"Cannot read the sequence ledger: {exc}") from exc
    else:
        ledger = {
            "schema_version": SEQUENCE_LEDGER_SCHEMA_VERSION,
            "repository_url": repository_url,
            "sequence_id": _sequence_id(repository_url),
            "turns": [],
        }
    turns = ledger.get("turns")
    if not isinstance(turns, list):
        raise GraderError("The sequence ledger has an invalid turns collection.")
    turn_number = int(turn["turn_number"])
    turns = [existing for existing in turns if existing.get("turn_number") != turn_number]
    turns.append(dict(turn))
    turns.sort(key=lambda entry: int(entry.get("turn_number", 0)))
    ledger.update(
        {
            "schema_version": SEQUENCE_LEDGER_SCHEMA_VERSION,
            "repository_url": repository_url,
            "sequence_id": _sequence_id(repository_url),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "latest_turn": max((int(entry.get("turn_number", 0)) for entry in turns), default=0),
            "turns": turns,
        }
    )
    _write_sequence_ledger(path, ledger)
    return path


def load_prior_winning_context(root: Path, repository_url: str, turn_number: int) -> str:
    """Load bounded prior state without sending persisted full prompts to the model."""
    if turn_number == 1:
        return ""
    path = _ledger_path(root, repository_url)
    if not path.is_file():
        rebuild_sequence_ledger(root, repository_url)
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GraderError(f"Cannot load the prior sequence ledger: {exc}") from exc
    turns = ledger.get("turns")
    if not isinstance(turns, list):
        raise GraderError("The prior sequence ledger has an invalid turns collection.")
    prior_turn = turn_number - 1
    if not any(entry.get("turn_number") == prior_turn for entry in turns):
        raise GraderError(
            f"Turn {turn_number} requires a saved Turn {prior_turn} result for this repository. Grade the previous turn first."
        )
    continuation_fields = (
        "turn_number",
        "created_at",
        "selected_model",
        "selected_response_label",
        "task_prompt_type",
        "requested_outcome",
        "work_observed",
        "artifacts",
        "verification",
        "decisions_and_constraints",
        "open_risks",
        "unresolved_contracts",
        "affected_callers_and_consumers",
        "candidate_next_seams",
        "verification_gaps",
        "next_request_type",
    )
    visible_turns = [
        {field: entry[field] for field in continuation_fields if field in entry}
        for entry in turns
        if int(entry.get("turn_number", 0)) <= prior_turn
    ]
    prior = {
        "schema_version": ledger.get("schema_version"),
        "repository_url": ledger.get("repository_url"),
        "sequence_id": ledger.get("sequence_id"),
        "latest_turn": prior_turn,
        "turns": visible_turns,
    }
    return json.dumps(prior, indent=2, ensure_ascii=False)


def save_sequence_turn(
    root: Path,
    item: EvaluationInput,
    result: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> Path:
    """Persist the selected-run audit record and update its compact ledger."""
    selected = str(result["next_step_model"])
    selected_response = item.response_a if selected == "A" else item.response_b
    checkpoint = result.get("sequence_checkpoint")
    if not isinstance(checkpoint, dict):
        raise GraderError("Cannot save a turn without a validated sequence checkpoint.")
    record = {
        "schema_version": SEQUENCE_LEDGER_SCHEMA_VERSION,
        "repository_url": item.repository_url,
        "sequence_id": _sequence_id(item.repository_url),
        "turn_number": item.turn_number,
        "created_at": metadata.get("created_at"),
        "task_prompt_type": result["task_prompt_type"],
        "candidate_raw_log_token_budget": metadata.get("candidate_raw_log_token_budget", {}),
        "sequence_checkpoint": checkpoint,
        "winning_context": {
            "prompt_given_to_models": item.task_prompt,
            "shared_transcript": item.transcript,
            "selected_model": "Model 1" if selected == "A" else "Model 2",
            "selected_response_label": selected,
            "selected_model_run": selected_response,
            "selected_run_summary": result["model_a" if selected == "A" else "model_b"]["summary"],
            "selection_reason": result["next_step_reason"],
            "expected_senior_engineer_behavior": result["senior_engineer_behavior"],
            "generated_next_turn_request_type": result["next_turn_request_type"],
            "generated_next_turn_prompt": result["next_turn_prompt"],
        },
        "judge_metadata": dict(metadata),
    }
    turn_dir = _turn_directory(root, item.repository_url, item.turn_number)
    turn_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = turn_dir / f"evaluation_{stamp}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    _upsert_sequence_ledger(root, item.repository_url, _ledger_turn_from_record(record))
    return path


def _markdown_section(document: str, heading: str, *, level: int = 2) -> str:
    marker = "#" * level
    match = re.search(
        rf"(?ms)^\s*{re.escape(marker)}\s+{re.escape(heading)}\s*$\n(.*?)(?=^\s*{re.escape(marker)}\s+|\Z)",
        document,
    )
    return match.group(1).strip() if match else ""


def _first_paragraph(value: str) -> str:
    for part in re.split(r"\n\s*\n", value.strip()):
        cleaned = re.sub(r"^\*\*[^*]+\*\*\s*", "", part.strip())
        if cleaned and not cleaned.startswith("#"):
            return _compact_ledger_text(cleaned)
    return ""


def _report_artifacts(value: str) -> list[str]:
    artifacts: list[str] = []
    filename = re.compile(
        r"^(?:[.\w-]+[/\\])*[.\w-]+(?:\.py|\.md|\.toml|\.lock|\.yaml|\.yml|\.json|\.ini|\.cfg|\.txt)$",
        re.IGNORECASE,
    )
    for candidate in re.findall(r"`([^`\r\n]{1,240})`", value):
        item = candidate.strip()
        if filename.fullmatch(item) or item in {"Dockerfile", ".tool-versions", "pyproject.toml", "uv.lock"}:
            normalized = item.replace("\\", "/")
            if normalized not in artifacts:
                artifacts.append(normalized)
        if len(artifacts) >= 20:
            break
    return artifacts


def seed_sequence_ledger_from_report(root: Path, report_path: Path) -> Path:
    """Enrich one legacy ledger entry from a human-readable evaluation report."""
    try:
        report = report_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise GraderError(f"Cannot read evaluation report {report_path}: {exc}") from exc
    repository_match = re.search(r"(?m)^- Repository URL:\s*`([^`]+)`", report)
    turn_match = re.search(r"(?m)^- Sequence turn:\s*`(\d+)`", report)
    selected_match = re.search(r"\*\*Carry Model ([12]) \(Response ([AB])\) into the next turn\.\*\*", report)
    if not (repository_match and turn_match and selected_match):
        raise GraderError(f"Evaluation report {report_path.name} lacks repository, turn, or continuation metadata.")
    repository_url = repository_match.group(1)
    turn_number = int(turn_match.group(1))
    selected_label = selected_match.group(2)
    path = _ledger_path(root, repository_url)
    if not path.is_file():
        rebuild_sequence_ledger(root, repository_url)
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GraderError(f"Cannot load the sequence ledger for report seeding: {exc}") from exc
    turns = ledger.get("turns")
    if not isinstance(turns, list):
        raise GraderError("The sequence ledger has an invalid turns collection.")
    target = next((entry for entry in turns if entry.get("turn_number") == turn_number), None)
    if not isinstance(target, dict):
        raise GraderError(f"No saved Turn {turn_number} record exists for {repository_url}.")

    response_section = _markdown_section(report, f"Response {selected_label}")
    summary = _first_paragraph(response_section)
    confidence = _first_paragraph(_markdown_section(response_section, "Confidence", level=3))
    continuation = _markdown_section(report, "Continuation decision")
    continuation_reason = _first_paragraph(re.sub(r"\*\*Carry Model.*?\*\*", "", continuation, count=1))
    senior_section = _markdown_section(report, "How the senior SWE would have acted")
    if not senior_section:
        senior_section = _markdown_section(report, "How a senior engineer should have behaved")
    senior = _compact_ledger_text(senior_section, 3_000)
    suggested = _markdown_section(report, "Suggested next-turn prompt")
    why_match = re.search(r"(?ms)^\*\*Why this follows:\*\*\s*(.+)$", suggested)
    why_follows = _compact_ledger_text(why_match.group(1)) if why_match else ""
    caveats = [
        _compact_ledger_text(line[2:])
        for line in _markdown_section(report, "Caveats").splitlines()
        if line.startswith("- ")
    ]

    if summary:
        target["work_observed"] = [summary]
    target["artifacts"] = _report_artifacts("\n".join((summary, senior, suggested)))
    if confidence:
        target["verification"] = [confidence]
    target["decisions_and_constraints"] = _compact_ledger_list(
        [continuation_reason, senior], limit=12
    )
    target["open_risks"] = _compact_ledger_list([*caveats, why_follows], limit=12)
    target["reference_report"] = str(report_path)
    return _upsert_sequence_ledger(root, repository_url, target)

PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Compare two coding-agent runs on behavior and communication.">
  <title>Behavioral Review Desk</title>
  <style>
    :root{color-scheme:dark;--bg:#071019;--panel:#0d1a25;--ink:#edf7f5;--muted:#8fa5ad;--line:#223746;--accent:#6cf0c2;--blue:#6ccff0;--danger:#ff8e94;--shadow:0 28px 70px rgba(0,0,0,.34)}
    *{box-sizing:border-box} body{margin:0;min-height:100vh;background:linear-gradient(rgba(108,240,194,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(108,240,194,.035) 1px,transparent 1px),radial-gradient(circle at 84% -10%,rgba(108,207,240,.16),transparent 34%),var(--bg);background-size:36px 36px,36px 36px,auto,auto;color:var(--ink);font:16px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
    button,input,textarea,select{font:inherit}.shell{width:min(1180px,calc(100% - 32px));margin:auto;padding:30px 0 56px}header{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin-bottom:24px}.eyebrow{color:var(--accent);font:700 .75rem/1.2 ui-monospace,Consolas,monospace;letter-spacing:.15em;text-transform:uppercase}h1{margin:8px 0 4px;font-size:clamp(1.85rem,4vw,3rem);line-height:1.05;letter-spacing:-.045em}.intro{color:var(--muted);margin:8px 0 0;max-width:650px}.api-state{display:flex;align-items:center;gap:9px;white-space:nowrap;color:var(--muted);font-size:.875rem;padding-top:8px}.dot{width:9px;height:9px;border-radius:50%;background:#81909a;box-shadow:0 0 0 4px rgba(129,144,154,.1)}.dot.ready{background:var(--accent);box-shadow:0 0 0 4px rgba(108,240,194,.12)}
    .panel{background:rgba(13,26,37,.94);border:1px solid var(--line);box-shadow:var(--shadow)}.context-panel{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(250px,.65fr)}.section{padding:22px}.section+.section{border-left:1px solid var(--line)}.section-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px}h2{margin:0;font-size:1rem;letter-spacing:-.01em}.optional,.hint,.meta{color:var(--muted);font-size:.82rem}label{display:block;color:#c9dbdd;font-size:.875rem;font-weight:650;margin-bottom:7px}textarea,select,input[type=url]{width:100%;border:1px solid var(--line);color:var(--ink);background:#08141e;border-radius:8px;outline:none;transition:.16s}textarea{min-height:128px;padding:13px 14px;resize:vertical}select,input[type=url]{padding:11px 12px}select{padding-right:38px}textarea:focus,select:focus,input[type=url]:focus,.upload:focus-within{border-color:var(--blue);box-shadow:0 0 0 3px rgba(108,207,240,.1)}.settings{display:grid;gap:18px}.model-tag{border:1px solid var(--line);background:#08141e;border-radius:8px;padding:12px}.model-tag strong{display:block;font:650 .9rem/1.4 ui-monospace,Consolas,monospace;color:var(--accent)}
    .uploads{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}.upload-card{padding:20px;min-width:0}.candidate-label{display:flex;align-items:center;gap:10px;margin-bottom:12px}.badge{display:grid;place-items:center;width:30px;height:30px;background:var(--accent);color:#062018;font-weight:850;border-radius:6px}.badge.b{background:var(--blue);color:#061b24}.upload{display:grid;place-items:center;min-height:154px;padding:18px;text-align:center;border:1px dashed #375365;background:#08141e;border-radius:9px;cursor:pointer;transition:.16s}.upload:hover,.upload.drag{background:#0b1a25;border-color:var(--accent);transform:translateY(-1px)}.upload input{position:absolute;width:1px;height:1px;opacity:0}.upload-title{font-weight:720}.file-name{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--accent);margin-top:7px}.shared{margin-top:16px;padding:18px 20px;display:grid;grid-template-columns:1fr auto;align-items:center;gap:20px}.shared input{max-width:280px;color:var(--muted)}
    .action-bar{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-top:16px;padding:17px 20px}.axes{display:flex;flex-wrap:wrap;gap:6px}.axis{border:1px solid var(--line);border-radius:999px;color:#a9bdc2;font-size:.75rem;padding:4px 9px}button{border:0;border-radius:8px;padding:12px 18px;color:#052018;background:var(--accent);font-weight:800;cursor:pointer;min-width:210px;transition:.16s}button:hover{filter:brightness(1.06);transform:translateY(-1px)}button:disabled{cursor:wait;filter:grayscale(.45);transform:none;opacity:.72}.status{min-height:24px;margin:14px 2px 0;color:var(--muted)}.status.error{color:var(--danger)}.status.success,.download{color:var(--accent)}.download{font-weight:700}.spinner{display:inline-block;width:13px;height:13px;margin-right:8px;border:2px solid #45606b;border-top-color:var(--accent);border-radius:50%;animation:spin .75s linear infinite;vertical-align:-2px}@keyframes spin{to{transform:rotate(360deg)}}
    @media(max-width:760px){.shell{width:min(100% - 20px,680px);padding-top:20px}header,.action-bar{align-items:stretch;flex-direction:column}.api-state{padding-top:0}.context-panel,.uploads{grid-template-columns:1fr}.section+.section{border-left:0;border-top:1px solid var(--line)}.shared{grid-template-columns:1fr}button{width:100%}}
  </style>
</head>
<body>
<main class="shell">
  <header>
    <div><div class="eyebrow">Authorized AI benchmark workspace</div><h1>Behavioral Review Desk</h1><p class="intro">Compare how two coding agents reasoned, communicated, and managed the work. The downloaded report is marked as AI-generated benchmark output and includes a self-contained verdict with exact, searchable evidence.</p></div>
    <div class="api-state" aria-live="polite"><span class="dot" id="apiDot"></span><span id="apiText">Checking server…</span></div>
  </header>
  <form id="gradeForm">
    <section class="panel context-panel" aria-labelledby="contextHeading">
      <div class="section"><div class="section-head"><h2 id="contextHeading">Task prompt</h2><span class="meta">Optional when embedded in both files</span></div><label for="taskPrompt">The request given to both models</label><textarea id="taskPrompt" placeholder="Leave blank to detect the latest user request in both TXT or JSON transcripts…"></textarea><div class="hint">If supplied, this must match the latest user request in the uploaded run files. A mismatch stops grading before any model request.</div></div>
      <div class="section settings">
        <div><label for="interactionSetting">User availability</label><select id="interactionSetting"><option value="unknown">Unknown</option><option value="synchronous">Synchronous / actively watching</option><option value="asynchronous">Asynchronous / checking later</option><option value="mixed">Mixed during the run</option></select></div>
        <div><label for="repositoryUrl">Repository link</label><input id="repositoryUrl" type="url" inputmode="url" placeholder="https://github.com/owner/repository" required><div class="hint">Identifies the saved sequence. The server fetches a bounded public GitHub tree and README snapshot for repository-aware senior guidance and next-prompt design; it never uses that snapshot to rank A against B.</div></div>
        <div><label for="turnNumber">Sequence turn</label><select id="turnNumber" aria-describedby="turnHint"></select><div class="hint" id="turnHint">Turn 1 starts fresh. Turn 2–30 load the preceding saved winner.</div></div>
        <div class="model-tag"><span class="meta">Transcript comparison</span><strong id="modelName">gpt-6.1-sol · high</strong><span class="meta">Next prompt · substantial adaptive workload</span><strong id="promptModelName">gpt-6.1-sol · xhigh</strong></div>
        <div><label for="repairBudget">Additional paid repair calls per stage</label><select id="repairBudget"><option value="0" selected>0 — single attempt (recommended)</option><option value="1">1 — one automatic repair</option><option value="2">2 — higher retry budget</option></select></div>
        <div><label><input id="fullContext" type="checkbox"> Full shared context (higher cost)</label><div class="hint">Axis grading automatically uses only the latest substantive user exchange from plain Human/User transcripts and supported structured JSON exports. Next-prompt design receives the compact grader sequence ledger through the selected current turn and the bounded repository snapshot; raw candidate transcripts are never sent to that stage.</div><label><input id="freshRun" type="checkbox"> Force new evaluation and prompt variation (bypass saved results)</label></div>
      </div>
    </section>
    <section class="uploads" aria-label="Candidate run files">
      <article class="panel upload-card"><div class="candidate-label"><span class="badge">A</span><div><h2>Model run A</h2><span class="hint">Full transcript or response</span></div></div><label class="upload" id="dropA" for="fileA"><input id="fileA" type="file" accept=".txt,.md,.json,.log,text/plain,application/json" required><span><span class="upload-title">Choose or drop the first file</span><span class="file-name" id="nameA">TXT, Markdown, JSON, or log</span></span></label></article>
      <article class="panel upload-card"><div class="candidate-label"><span class="badge b">B</span><div><h2>Model run B</h2><span class="hint">Full transcript or response</span></div></div><label class="upload" id="dropB" for="fileB"><input id="fileB" type="file" accept=".txt,.md,.json,.log,text/plain,application/json" required><span><span class="upload-title">Choose or drop the second file</span><span class="file-name" id="nameB">TXT, Markdown, JSON, or log</span></span></label></article>
    </section>
    <section class="panel shared"><div><h2>Shared transcript <span class="optional">— optional</span></h2><div class="hint">Add context common to both runs if it is not already inside each file.</div></div><input id="sharedFile" type="file" accept=".txt,.md,.json,.log,text/plain,application/json" aria-label="Optional shared transcript file"></section>
    <section class="panel action-bar"><div class="axes" aria-label="Scoring dimensions"><span class="axis">Honesty</span><span class="axis">Safety</span><span class="axis">Scoping</span><span class="axis">Deference</span><span class="axis">Interaction</span><span class="axis">Confidence</span><span class="axis">Clarity</span></div><button id="submitButton" type="submit">Grade &amp; download .md</button></section>
    <p class="hint">Cost-conscious, evidence-locked review: one to three cited ranges per model are copied from source lines and receive a locally derived unique identifier when possible. Valid long excerpts never trigger a paid rerun. Identical validated stages are reused locally. Input/output records contain both transcripts under behavioral_grader/outputs/grader_attempts; only the winner enters the next turn.</p>
    <div class="status" id="status" role="status" aria-live="polite"></div>
  </form>
</main>
<script>
  const maxFileBytes=24*1024*1024,form=document.getElementById('gradeForm'),button=document.getElementById('submitButton'),statusBox=document.getElementById('status');let lastUrl=null;
  const humanSize=b=>b<1024?`${b} B`:b<1048576?`${(b/1024).toFixed(1)} KB`:`${(b/1048576).toFixed(1)} MB`;
  function bindUpload(inputId,dropId,nameId){const input=document.getElementById(inputId),drop=document.getElementById(dropId),name=document.getElementById(nameId);const show=()=>{const f=input.files[0];name.textContent=f?`${f.name} · ${humanSize(f.size)}`:'TXT, Markdown, JSON, or log'};input.addEventListener('change',show);['dragenter','dragover'].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.add('drag')}));['dragleave','drop'].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.remove('drag')}));drop.addEventListener('drop',e=>{if(e.dataTransfer.files.length){const dt=new DataTransfer();dt.items.add(e.dataTransfer.files[0]);input.files=dt.files;show()}})}
  bindUpload('fileA','dropA','nameA');bindUpload('fileB','dropB','nameB');
  const turnSelect=document.getElementById('turnNumber');for(let turn=1;turn<=30;turn++){const option=document.createElement('option');option.value=String(turn);option.textContent=`Turn ${turn}`;turnSelect.appendChild(option)}
  fetch('/health').then(r=>r.json()).then(data=>{document.getElementById('modelName').textContent=`${data.model} · ${data.reasoning_effort}`;document.getElementById('promptModelName').textContent=`${data.prompt_model} · ${data.prompt_reasoning_effort}`;document.getElementById('apiText').textContent=data.api_key_configured?'API ready':'API key not configured';document.getElementById('apiDot').classList.toggle('ready',data.api_key_configured);document.getElementById('repairBudget').value=String(data.validation_repairs??0);document.getElementById('fullContext').checked=Boolean(data.full_context);document.getElementById('freshRun').checked=data.reuse_results===false}).catch(()=>document.getElementById('apiText').textContent='Server unavailable');
  async function readFile(input,required){const file=input.files[0];if(!file){if(required)throw new Error('Choose both model run files.');return ''}if(file.size>maxFileBytes)throw new Error(`${file.name} is larger than 24 MB.`);const text=await file.text();if(required&&!text.trim())throw new Error(`${file.name} is empty.`);return text}
  form.addEventListener('submit',async e=>{
    e.preventDefault();button.disabled=true;statusBox.className='status';
    statusBox.innerHTML='<span class="spinner"></span>Running local input preflight before any model request. If it passes, grading and evidence validation will begin.';
    try{
      const[a,b,t]=await Promise.all([readFile(document.getElementById('fileA'),true),readFile(document.getElementById('fileB'),true),readFile(document.getElementById('sharedFile'),false)]);
      const payload={task_prompt:document.getElementById('taskPrompt').value,transcript:t.trim()?t:'No separate shared transcript was supplied; each candidate file contains its own observable run record.',response_a:a,response_b:b,interaction_setting:document.getElementById('interactionSetting').value,context_notes:'Candidate uploads may contain a full run transcript, a final response, or both.',repository_url:document.getElementById('repositoryUrl').value.trim(),turn_number:Number(turnSelect.value),full_context:document.getElementById('fullContext').checked,reuse_results:!document.getElementById('freshRun').checked,validation_repairs:Number(document.getElementById('repairBudget').value)};
      const response=await fetch('/grade',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(!response.ok){let msg=`Grading failed (${response.status}).`;try{msg=(await response.json()).error||msg}catch{}throw new Error(msg)}
      const selected=response.headers.get('X-Selected-Model')||'A model',turn=response.headers.get('X-Sequence-Turn')||turnSelect.value,repairs=response.headers.get('X-Validation-Repairs')||'0',blob=await response.blob();
      if(lastUrl)URL.revokeObjectURL(lastUrl);lastUrl=URL.createObjectURL(blob);
      const disposition=response.headers.get('Content-Disposition')||'',match=disposition.match(/filename="([^"]+)"/),filename=match?match[1]:'behavioral-evaluation.md',link=document.createElement('a');
      link.href=lastUrl;link.download=filename;link.className='download';link.textContent=`Download ${filename} again`;link.click();
      statusBox.className='status success';
      const reused=response.headers.get('X-Reused-Stages')||'none',tokens=response.headers.get('X-Run-Tokens')||'not reported';
      statusBox.textContent=`Turn ${turn} saved with ${selected} selected. Evidence validated; ${repairs} repairs. Reused stages: ${reused}. New API tokens: ${tokens}. `;
      statusBox.appendChild(link);
    }catch(error){statusBox.className='status error';statusBox.textContent=error.message||'The evaluation could not be completed.'}
    finally{button.disabled=false}
  });
</script>
</body>
</html>"""


def _text(payload: dict[str, Any], key: str, *, required: bool = True) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise GraderError(f"{key} must be text.")
    if required and not value.strip():
        raise GraderError(f"{key} is required.")
    return value


def _turn_number(payload: dict[str, Any]) -> int:
    value = payload.get("turn_number", 1)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 30:
        raise GraderError("turn_number must be an integer from 1 through 30.")
    return value


def extract_task_prompt(model_a: str, model_b: str) -> str:
    """Recover the shared latest user prompt from two exported run files."""
    detected = [latest_user_task_prompt(transcript) for transcript in (model_a, model_b)]
    if not all(detected):
        raise GraderError(
            "Task prompt was left blank and could not be detected in both files. Paste the request given to both models."
        )
    normalized = [re.sub(r"\s+", " ", prompt).strip() for prompt in detected if prompt]
    if normalized[0] != normalized[1]:
        raise GraderError(
            "The detected latest user requests differ between the two files. Select matching turn files."
        )
    return detected[0] or ""


class GraderHandler(BaseHTTPRequestHandler):
    server_version = "BehavioralReviewDesk/1.0"

    @property
    def grader_server(self) -> "GraderServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str, **headers: str) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send(HTTPStatus.OK, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/health":
            self._json(HTTPStatus.OK, {"ok": True, "api_key_configured": bool(os.getenv("OPENAI_API_KEY")), "model": self.grader_server.model, "reasoning_effort": self.grader_server.reasoning_effort, "prompt_model": PROMPT_MODEL, "prompt_reasoning_effort": PROMPT_REASONING_EFFORT, "validation_repairs": self.grader_server.validation_repairs, "full_context": self.grader_server.full_context, "reuse_results": not self.grader_server.fresh})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/grade":
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Expected a JSON request."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid request length."})
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "The combined upload must be 64 MB or smaller."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise GraderError("Request body must be an object.")
            setting = _text(payload, "interaction_setting")
            if setting not in {"synchronous", "asynchronous", "mixed", "unknown"}:
                raise GraderError("Invalid interaction setting.")
            response_a = _text(payload, "response_a")
            response_b = _text(payload, "response_b")
            task_prompt = _text(payload, "task_prompt", required=False)
            if not task_prompt.strip():
                task_prompt = extract_task_prompt(response_a, response_b)
            validate_task_prompt_alignment(task_prompt, response_a, response_b)
            repository_url = normalize_repository_url(payload.get("repository_url", ""))
            if not repository_url:
                raise GraderError("repository_url is required so the turn can be saved in the correct sequence.")
            turn_number = _turn_number(payload)
            prior_context = load_prior_winning_context(SEQUENCE_DIRECTORY, repository_url, turn_number)
            item = input_from_payload(dict(task_prompt=task_prompt, transcript=_text(payload, "transcript"), response_a=response_a, response_b=response_b, interaction_setting=setting, context_notes=_text(payload, "context_notes", required=False), repository_url=repository_url, turn_number=turn_number, prior_winning_context=prior_context))
            full_context = payload.get("full_context", self.grader_server.full_context)
            reuse_results = payload.get("reuse_results", not self.grader_server.fresh)
            if type(full_context) is not bool or type(reuse_results) is not bool:
                raise GraderError("Context and reuse options must be true or false.")
            result, metadata = call_judge(item, model=self.grader_server.model, reasoning_effort=self.grader_server.reasoning_effort, max_output_tokens=self.grader_server.max_output_tokens, prompt_max_output_tokens=self.grader_server.prompt_max_output_tokens, max_validation_repairs=payload.get("validation_repairs", self.grader_server.validation_repairs), full_context=full_context, reuse_results=reuse_results)
            saved_path = save_sequence_turn(SEQUENCE_DIRECTORY, item, result, metadata)
            metadata["sequence_record"] = str(saved_path)
            metadata["sequence_ledger"] = str(_ledger_path(SEQUENCE_DIRECTORY, repository_url))
            markdown = render_markdown(result, metadata).encode("utf-8")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "The browser sent invalid JSON text."})
            return
        except GraderError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc), "audit_directory": getattr(exc, "audit_directory", None)})
            return
        except Exception as exc:
            sys.stderr.write(f"Unexpected grading error: {exc}\n")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Unexpected server error. Check the terminal for details."})
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        selected_name = "Model 1" if result["next_step_model"] == "A" else "Model 2"
        self._send(HTTPStatus.OK, markdown, "text/markdown; charset=utf-8", Content_Disposition=f'attachment; filename="behavioral-evaluation-turn-{item.turn_number:02d}-{stamp}.md"', X_Selected_Model=selected_name, X_Sequence_Turn=str(item.turn_number), X_Validation_Repairs=str(metadata.get("validation", {}).get("repairs", 0)), X_Reused_Stages=", ".join(metadata.get("cost_controls", {}).get("reused_stages", [])) or "none", X_Run_Tokens=str(metadata["usage"]["total_tokens"]))


class GraderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], model: str, reasoning_effort: str, max_output_tokens: int, prompt_max_output_tokens: int = PROMPT_MAX_OUTPUT_TOKENS, validation_repairs: int = DEFAULT_VALIDATION_REPAIRS, full_context: bool = False, fresh: bool = False) -> None:
        super().__init__(address, GraderHandler)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self.prompt_max_output_tokens = prompt_max_output_tokens
        self.validation_repairs = validation_repairs
        self.full_context = full_context
        self.fresh = fresh


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the behavioral grader web UI locally.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: localhost only).")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Transcript comparison model.")
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"), default=DEFAULT_REASONING_EFFORT)
    parser.add_argument("--max-output-tokens", type=int, default=20_000)
    parser.add_argument("--prompt-max-output-tokens", type=int, default=PROMPT_MAX_OUTPUT_TOKENS)
    parser.add_argument("--validation-repairs", type=int, choices=(0, 1, 2), default=DEFAULT_VALIDATION_REPAIRS)
    parser.add_argument("--full-context", action="store_true")
    parser.add_argument("--fresh", action="store_true", help="Bypass locally saved validated results.")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65_535:
        parser.error("--port must be between 1 and 65535")
    if args.max_output_tokens < 1_000:
        parser.error("--max-output-tokens must be at least 1000")
    if args.prompt_max_output_tokens < 1_000:
        parser.error("--prompt-max-output-tokens must be at least 1000")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    dotenv_loaded = load_local_dotenv(PACKAGE_DIRECTORY / ".env")
    args = parse_args(argv or sys.argv[1:])
    server = GraderServer((args.host, args.port), args.model, args.reasoning_effort, args.max_output_tokens, args.prompt_max_output_tokens, args.validation_repairs, args.full_context, args.fresh)
    host, port = server.server_address
    print(f"Behavioral Review Desk: http://{host}:{port}")
    print(f"Comparison: {args.model} / {args.reasoning_effort}; next prompt: {PROMPT_MODEL} / {PROMPT_REASONING_EFFORT}")
    if dotenv_loaded:
        print("Loaded API-key configuration from .env.")
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not configured; the page will load, but grading is disabled.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
