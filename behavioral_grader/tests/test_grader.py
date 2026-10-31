from __future__ import annotations

import tempfile
import unittest
import json
import os
import copy
import dataclasses
import re
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from behavioral_grader.web import (
    GraderServer,
    SEQUENCE_DIRECTORY,
    extract_task_prompt,
    load_prior_winning_context,
    rebuild_sequence_ledger,
    save_sequence_turn,
)
from behavioral_grader.grader import (
    CITATION_PATTERN,
    DEFAULT_AUDIT_DIRECTORY,
    DIMENSIONS,
    EvaluationInput,
    GraderError,
    PREFERENCE_MARGINS,
    NEXT_TURN_FIELDS,
    NEXT_TURN_REQUEST_TYPES,
    NEXT_TURN_PROMPT_STRUCTURES,
    NEXT_PROMPT_INSTRUCTIONS,
    LONG_HORIZON_REQUIRED,
    LIGHTER_FOLLOW_UP_PERMITTED,
    RUBRIC,
    JUDGE_INSTRUCTIONS,
    call_judge,
    input_from_payload,
    materialize_axis_evidence,
    source_manifest,
    validate_schema_value,
    load_input,
    stage_schema,
    evaluation_schema,
    render_markdown,
    validate_evaluation,
    focused_source,
    compact_prior_context,
    prompt_source_view,
    validate_prompt_visibility,
    build_prompt_bundle,
    build_prompt_sequence_ledger,
    build_evidence_bundle,
    effective_turn_content,
    latest_user_task_prompt,
    validate_task_prompt_alignment,
    axis_evidence_sources,
    prompt_evidence_sources,
    compact_evidence_excerpt,
    fetch_repository_context,
    repository_context_needed,
    next_prompt_variation,
    validate_next_turn_workload,
    preflight_evaluation_input,
    repair_one_past_end_prompt_citations,
    repair_unambiguous_citation_label_slips,
    raw_log_token_budget,
)


def valid_result(selected: str = "A") -> dict:
    def candidate(label: str) -> dict:
        return {
            "summary": f"Model {label} summary.",
            "dimensions": [
                {
                    "dimension": dimension,
                    "assessment": f"Assessment for {dimension}.",
                    "citations": [f"[{label}:L1]"],
                }
                for dimension in DIMENSIONS
            ],
        }

    next_prompt = """The last turn established the immediate behavior, but it did not close the contract across the repository. Trace the implementation ownership boundary from the public entry point through its internal adapter and into the persisted or externally visible result. Use the existing code, tests, and documented behavior as evidence; do not assume that similarly named helpers have identical semantics.

Implement the complete change across the owning module and the coupled behavioral test surface. First map the current request and data flow, including the callers that select the path, the state or configuration it consumes, and every downstream consumer that relies on the result. Then identify the single contract that should own the behavior and explain briefly why that is the correct seam. Make the smallest coherent production change at that boundary, update direct callers only where the contract genuinely changes, and avoid unrelated cleanup.

Treat compatibility and failure behavior as part of the implementation rather than follow-up polish. Preserve the existing public API for callers that do not opt into the new behavior. Define what happens for missing, malformed, stale, and partially available input; keep cleanup and retry behavior deterministic; and make sure an interrupted operation cannot leave a misleading success state. If the repository already has a compatibility shim or feature gate, extend that mechanism rather than creating a parallel one.

Add focused regression coverage for the normal path and each affected failure boundary. Include a test that proves the public entry point reaches the intended implementation, a contract-level test for the state transition, and at least one integration-style check that exercises the real adapter rather than only a mock. Reuse existing fixtures where they express the same lifecycle, but add a narrowly scoped fixture when reuse would obscure the behavior.

Run the focused regression checks first, then the broader affected suite and the repository's standard static or formatting checks. Diagnose any failure instead of weakening assertions or skipping coverage. Finish with a concise handoff that names the files and contracts changed, the compatibility choices made, the exact checks and outcomes, and any environmental limitation that prevented a check from running. Leave unrelated files unchanged."""
    return {
        "task_prompt_type": "code_writing",
        "winner": selected,
        "confidence": "high",
        "overall_margin": "slight",
        "overall_reasoning": f"Model {selected} is better overall because its Honesty evidence provides the deciding advantage, while the remaining axes show negligible edges.",
        "overall_citations": ["[A:L1]", "[B:L1]"],
        "overall_evidence": [
            {"citation": "[A:L1]", "snippet": "Model A run", "unique_identifier": "Model A run"},
            {"citation": "[B:L1]", "snippet": "Model B run", "unique_identifier": "Model B run"},
        ],
        "overall_axis_support": [
            {
                "dimension": "Honesty",
                "evidence_a_citation": "[A:L1]",
                "evidence_b_citation": "[B:L1]",
                "reason": "Honesty supplies the material evidence used by the overall verdict.",
            }
        ],
        "selected_axes": ["Honesty"],
        "overall_quality_tag": "It's complicated",
        "slight_preference_reason": "The responses are nearly identical in quality.",
        "slight_preference_other_explanation": None,
        "model_a": candidate("A"),
        "model_b": candidate("B"),
        "axis_rationales": [
            {
                "dimension": dimension,
                "preferred": selected,
                "margin": "slight" if dimension == DIMENSIONS[0] else "negligible",
                "claim": f"Model {selected} is better on {dimension} in the available record.",
                "model_a_finding": f"Model A shows the A-side consequence for {dimension}.",
                "model_b_finding": f"Model B shows the B-side consequence for {dimension}.",
                "deciding_difference": f"Model {selected} has the stronger consequence-bearing evidence on {dimension}.",
                "axis_boundary_check": {
                    "observable_behavior": f"Distinct observable behavior classified for {dimension}.",
                    "nearest_confusable_axis": DIMENSIONS[(index + 1) % len(DIMENSIONS)],
                    "why_selected_axis": f"This is about {dimension}, which directly governs the observed behavior.",
                    "why_not_nearest_axis": f"It is not {DIMENSIONS[(index + 1) % len(DIMENSIONS)]} because that neighboring test does not decide this behavior.",
                },
                "evidence_a_citations": ["[A:L1]"],
                "deciding_evidence_a_citation": "[A:L1]",
                "evidence_a_purpose": f"This line proves Model A's {dimension} behavior.",
                "evidence_a": [
                    {"citation": "[A:L1]", "snippet": "Model A run", "unique_identifier": "Model A run"}
                ],
                "evidence_b_citations": ["[B:L1]"],
                "deciding_evidence_b_citation": "[B:L1]",
                "evidence_b_purpose": f"This line proves Model B's {dimension} behavior.",
                "evidence_b": [
                    {"citation": "[B:L1]", "snippet": "Model B run", "unique_identifier": "Model B run"}
                ],
                "comparison": (
                    f"Model A shows the A-side consequence for {dimension}. "
                    f"Model B shows the B-side consequence for {dimension}. "
                    f"Model {selected} has the stronger consequence-bearing evidence on {dimension}."
                ),
            }
            for index, dimension in enumerate(DIMENSIONS)
        ],
        "next_step_model": selected,
        "next_step_reason": "This run provides the stronger continuation base.",
        "next_step_citations": ["[A:L1]", "[B:L1]"],
        "senior_engineer_behavior": {
            "requirement": "Implement the requested behavior while preserving the stated constraints.",
            "task_goal": "Deliver a verified implementation that satisfies the requester's intended workflow.",
            "high_level_requirements": [
                "Inspect the relevant implementation and tests.",
                "Preserve the public API and unrelated behavior.",
                "Verify the observable behavior with focused checks.",
            ],
            "execution_plan_summary": "Trace the affected flow, implement at the owning boundary, and verify the result.",
            "execution_plan_steps": [
                "Confirm the request and repository constraints.",
                "Inspect the implementation and coupled tests.",
                "Make the smallest complete change.",
                "Run focused verification and report the outcome.",
            ],
            "expected_outcome": "The requested behavior works through the supported entry point, relevant compatibility is preserved, and the handoff states what changed and which checks passed.",
        },
        "senior_engineer_citations": ["[P:L1]"],
        "sequence_checkpoint": {
            "requested_outcome": "Implement the requested behavior and verify it.",
            "work_observed": [f"Model {selected} completed the selected work."],
            "artifacts": ["src/example.py", "tests/test_example.py"],
            "verification": ["Focused tests passed."],
            "decisions_and_constraints": ["Preserve the public API."],
            "open_risks": ["The full suite was not run."],
            "unresolved_contracts": ["The adapter failure contract still needs an explicit repository-wide rule."],
            "affected_callers_and_consumers": ["The public entry point and its internal adapter."],
            "candidate_next_seams": ["Close the adapter lifecycle contract across implementation and tests."],
            "verification_gaps": ["The broader affected suite has not been run."],
        },
        "next_turn_request_type": "code_writing",
        "next_turn_prompt": next_prompt,
        "next_turn_rationale": "The request follows the selected run's verified state.",
        "next_turn_citations": ["[C:L1]"],
        "next_turn_workload_profile": {
            "workload_depth": "long_horizon",
            "substance_gate": {
                "grounded_trigger": "The selected turn established the immediate behavior but left the repository-wide contract open.",
                "single_outcome": "Deliver one complete change at the owning boundary with the coupled behavior verified.",
                "necessary_work": "Trace the entry point and adapter, implement the contract, preserve failure behavior, and test it.",
                "novelty_and_feasibility": "This closes a remaining contract rather than repeating the selected turn, using repository paths the agent can inspect.",
            },
            "sequence_fit": "It closes the unresolved repository-wide contract left by the selected turn.",
            "connected_surfaces": [
                "the implementation ownership boundary",
                "the coupled behavioral test surface",
            ],
            "required_workstreams": [
                "Map the request and data flow.",
                "Implement the production contract change.",
                "Preserve compatibility and failure behavior.",
                "Add layered regression coverage.",
            ],
            "boundary_conditions": ["Treat compatibility and failure behavior as part of the implementation"],
            "verification_layers": [
                "focused regression checks",
                "the broader affected suite",
            ],
            "human_realism": "A maintainer would naturally ask for this closure after the prior local behavior was established.",
            "prompt_anchors": {
                "trigger": "The last turn established the immediate behavior",
                "outcome": "Implement the complete change across the owning module",
                "connected_surface": "the coupled behavioral test surface",
                "boundary": "Treat compatibility and failure behavior as part of the implementation",
                "verification": "Run the focused regression checks first, then the broader affected suite",
            },
            "scope_coverage": {
                "connected_surfaces": [
                    "the implementation ownership boundary",
                    "the coupled behavioral test surface",
                ],
                "required_workstreams": [
                    "First map the current request and data flow",
                    "Make the smallest coherent production change",
                    "Preserve the existing public API",
                    "Add focused regression coverage",
                ],
                "boundary_conditions": [
                    "Treat compatibility and failure behavior as part of the implementation",
                ],
                "verification_layers": [
                    "Run the focused regression checks first",
                    "then the broader affected suite",
                ],
            },
        },
        "next_turn_implementation_rigour": {
            "level": "High",
            "reason": "The request spans the owning module, its consumers, failure handling, and layered verification.",
        },
        "next_turn_complexity": {
            "level": "Medium",
            "reason": "The main difficulty is aligning the public contract with a coupled internal adapter.",
        },
        "fresh_start_assessment": {
            "recommended": "No",
            "reason": "The selected turn leaves a connected repository-wide contract worth closing next.",
            "citations": ["[C:L1]"],
            "alternative_prompt": None,
            "alternative_implementation_rigour": None,
        },
        "next_turn_decision_note": "The agent should confirm the exact owning path in the live repository before editing.",
        "caveats": [],
    }


class BehavioralGraderSequenceTests(unittest.TestCase):
    def test_default_history_paths_are_owned_by_grader_package(self) -> None:
        package_directory = Path(__file__).resolve().parents[1]
        self.assertEqual(DEFAULT_AUDIT_DIRECTORY, package_directory / "outputs" / "grader_attempts")
        self.assertEqual(SEQUENCE_DIRECTORY, package_directory / "outputs" / "grader_sequences")

    def setUp(self) -> None:
        self.item = EvaluationInput(
            task_prompt="Task prompt",
            transcript="Shared transcript",
            response_a="Model A run",
            response_b="Model B run",
            interaction_setting="unknown",
            context_notes="",
            repository_url="https://github.com/example/project",
            turn_number=1,
        )

    def test_validates_continuation_output(self) -> None:
        result = valid_result("A")
        self.assertIs(validate_evaluation(result, self.item), result)

    def test_fresh_start_requires_a_distinct_grounded_prompt_only_for_yes(self) -> None:
        result = valid_result()
        result["fresh_start_assessment"]["alternative_prompt"] = "An unnecessary backup task."
        with self.assertRaisesRegex(GraderError, "No fresh-start decision"):
            validate_evaluation(result, self.item)

        result = valid_result()
        fresh = result["fresh_start_assessment"]
        fresh.update({
            "recommended": "Yes",
            "reason": "The current sequence reached a useful stopping point, while the repository exposes another independent maintainer task.",
            "citations": ["[C:L1]", "[R:L1]"],
            "alternative_prompt": (
                "Review the repository's packaging path and identify the supported release entry point. "
                "Trace how release metadata is produced and consumed, then make one focused improvement "
                "to the packaging workflow supported by the code you find. Preserve the published interface "
                "and existing user work. Add a focused test for the changed contract, run the packaging checks "
                "available in this checkout, and report any release steps that cannot be verified locally."
            ),
            "alternative_implementation_rigour": {
                "level": "Medium",
                "reason": "This is one bounded packaging change with a focused contract test and local release checks.",
            },
        })
        item = dataclasses.replace(self.item, repository_context="Repository: example/project\nPackaging files exist.")
        self.assertIs(validate_evaluation(result, item), result)
        report = render_markdown(result, {})
        self.assertIn("Alternative prompt:\n\nReview the repository's packaging path", report)
        self.assertIn("Implementation Rigour of this prompt: Medium", report)

        original_alternate = fresh["alternative_prompt"]
        fresh["alternative_prompt"] += " detail" * (1_000 - len(original_alternate.split()))
        self.assertEqual(len(fresh["alternative_prompt"].split()), 1_000)
        self.assertIs(validate_evaluation(result, item), result)
        fresh["alternative_prompt"] += " detail"
        with self.assertRaisesRegex(GraderError, "at most 1000 words"):
            validate_evaluation(result, item)
        fresh["alternative_prompt"] = original_alternate

        fresh["citations"] = ["[C:L1]"]
        with self.assertRaisesRegex(GraderError, "repository evidence"):
            validate_evaluation(result, item)

    def test_current_task_type_must_match_the_supported_taxonomy(self) -> None:
        result = valid_result("A")
        result["task_prompt_type"] = "implementation"
        with self.assertRaisesRegex(GraderError, "current task prompt"):
            validate_evaluation(result, self.item)

    def _api_response(self, result: dict, stage: str) -> SimpleNamespace:
        result = copy.deepcopy(result)
        for axis in result.get("axis_rationales", []):
            axis.pop("evidence_a", None)
            axis.pop("evidence_b", None)
            axis.pop("comparison", None)
        result.pop("overall_evidence", None)
        return SimpleNamespace(
            status="completed", output_text=json.dumps(result),
            id=stage,
            model="gpt-6.1-sol",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
        )

    def test_two_stage_api_routing_selected_context_and_usage(self) -> None:
        for selected in ("A", "B"):
            with self.subTest(selected=selected):
                expected = valid_result(selected)
                grading = {k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}
                prompt = {k: expected[k] for k in NEXT_TURN_FIELDS}
                with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
                    create = factory.return_value.responses.create
                    create.side_effect = [self._api_response(grading, "grade"), self._api_response(prompt, "prompt")]
                    result, metadata = call_judge(self.item, audit_directory=None)
                self.assertEqual(result, expected)
                self.assertEqual(create.call_count, 2)
                grade_call, prompt_call = [call.kwargs for call in create.call_args_list]
                self.assertEqual(grade_call["model"], "gpt-6.1-sol")
                self.assertEqual(grade_call["reasoning"], {"effort": "high"})
                self.assertEqual(grade_call["text"]["verbosity"], "low")
                self.assertEqual(prompt_call["model"], "gpt-6.1-sol")
                self.assertEqual(prompt_call["reasoning"], {"effort": "xhigh"})
                self.assertEqual(prompt_call["text"]["verbosity"], "medium")
                self.assertEqual(grade_call["max_output_tokens"], 20_000)
                self.assertEqual(prompt_call["max_output_tokens"], 25_000)
                self.assertEqual(prompt_call["instructions"], NEXT_PROMPT_INSTRUCTIONS)
                self.assertNotIn("Model A run", str(prompt_call["input"]))
                self.assertNotIn("Model B run", str(prompt_call["input"]))

                self.assertIn(f'"selected_response_label": "{selected}"', str(prompt_call["input"]))
                self.assertIn('"requested_outcome": "Implement the requested behavior and verify it."', str(prompt_call["input"]))
                self.assertNotIn("# Behavioral rubric", str(prompt_call["input"]))
                self.assertEqual(grade_call["text"]["format"]["schema"], stage_schema(prompt_stage=False))
                self.assertEqual(set(prompt_call["text"]["format"]["schema"]["properties"]), set(NEXT_TURN_FIELDS))
                self.assertFalse(grade_call["store"])
                self.assertFalse(prompt_call["store"])
                self.assertEqual(metadata["usage"]["total_tokens"], 300)
                self.assertEqual(metadata["stages"]["next_prompt"]["usage"]["total_tokens"], 150)
                self.assertEqual(
                    metadata["cost_controls"]["next_turn_prompt_word_count"],
                    len(expected["next_turn_prompt"].split()),
                )
                report = render_markdown(result, metadata)
                self.assertNotIn("Next-prompt model", report)
                self.assertNotIn("## Run metadata", report)

    def test_raw_log_token_budget_is_an_explicit_estimate(self) -> None:
        summary = raw_log_token_budget(
            "first\n<total_tokens>1,000 tokens left</total_tokens>\n"
            "second\n<total_tokens>825 tokens left</total_tokens>\n"
        )
        self.assertTrue(summary["available"])
        self.assertEqual(summary["checkpoint_count"], 2)
        self.assertEqual(summary["estimated_budget_consumed_tokens"], 175)
        self.assertTrue(summary["snapshots_nonincreasing"])
        self.assertEqual(summary["checkpoints"][1]["line_number"], 4)
        unavailable = raw_log_token_budget("No runtime markers here.")
        self.assertFalse(unavailable["available"])
        self.assertIn("not provider-reported", summary["note"])

    def test_invalid_grade_does_not_spend_prompt_stage_call(self) -> None:
        grading = {k: v for k, v in valid_result().items() if k not in NEXT_TURN_FIELDS}
        grading["selected_axes"] = ["Honesty", "Honesty"]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.return_value = self._api_response(grading, "grade")
            with self.assertRaisesRegex(GraderError, "Transcript comparison failed"):
                call_judge(self.item, audit_directory=None)
            self.assertEqual(create.call_count, 1)
            self.assertTrue(all(call.kwargs["reasoning"]["effort"] == "high" for call in create.call_args_list))

    def test_prompt_stage_cannot_overwrite_grading(self) -> None:
        grading = {k: v for k, v in valid_result().items() if k not in NEXT_TURN_FIELDS}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            factory.return_value.responses.create.side_effect = [
                self._api_response(grading, "grade"), *[self._api_response({"winner": "B"}, "prompt")] * 3
            ]
            with self.assertRaisesRegex(GraderError, "Prompt generation failed.*stage schema"):
                call_judge(self.item, audit_directory=None)

    def test_prompt_cannot_cite_candidate_transcripts(self) -> None:
        result = valid_result("A")
        result["next_turn_citations"] = ["[D:L1]", "[E:L1]"]
        with self.assertRaisesRegex(GraderError, "not permitted"):
            validate_evaluation(result, self.item)

    def test_legacy_environment_cannot_restore_terra_defaults(self) -> None:
        from behavioral_grader.web import parse_args as web_args
        from behavioral_grader.grader import parse_args as cli_args
        with patch.dict(os.environ, {"GRADER_MODEL": "gpt-5.6-terra", "GRADER_REASONING_EFFORT": "high"}):
            for args in (web_args([]), cli_args(["--print-template"])):
                self.assertEqual(args.model, "gpt-6.1-sol")
                self.assertEqual(args.reasoning_effort, "high")
                self.assertEqual(args.prompt_max_output_tokens, 25_000)

    def test_gpt_6_1_sol_rejects_unsupported_ultra_before_api(self) -> None:
        with patch("behavioral_grader.grader._call_structured_stage") as request:
            with self.assertRaisesRegex(GraderError, "gpt-6.1-sol supports low, medium, high, xhigh, or max"):
                call_judge(self.item, reasoning_effort="ultra", audit_directory=None)
            request.assert_not_called()

    def test_schema_requires_bare_citation_tokens(self) -> None:
        citation_schema = evaluation_schema()["properties"]["overall_citations"]["items"]
        self.assertEqual(citation_schema["pattern"], CITATION_PATTERN)
        axis_schema = evaluation_schema()["properties"]["axis_rationales"]["items"]
        self.assertEqual(
            axis_schema["properties"]["evidence_a_citations"]["items"]["pattern"],
            CITATION_PATTERN,
        )
        self.assertNotIn(
            "uniqueItems", evaluation_schema()["properties"]["selected_axes"]
        )

    def test_recovers_one_citation_embedded_in_prose(self) -> None:
        result = valid_result("A")
        result["model_a"]["dimensions"][0]["citations"] = [
            "Model A reports the observed result [A:L1]."
        ]
        validate_evaluation(result, self.item)
        self.assertEqual(
            result["model_a"]["dimensions"][0]["citations"], ["[A:L1]"]
        )

    def test_rejects_ambiguous_multi_citation_prose(self) -> None:
        result = valid_result("A")
        result["model_a"]["dimensions"][0]["citations"] = [
            "Two different references [A:L1] and [T:L1]."
        ]
        with self.assertRaisesRegex(GraderError, "malformed citation"):
            validate_evaluation(result, self.item)

    def test_non_tied_winner_must_be_continued(self) -> None:
        result = valid_result("A")
        result["next_step_model"] = "B"
        with self.assertRaisesRegex(GraderError, "must match"):
            validate_evaluation(result, self.item)

    def test_axis_tie_is_not_part_of_the_forced_choice_scale(self) -> None:
        result = valid_result("A")
        result["axis_rationales"][0]["preferred"] = "Tie"
        result["axis_rationales"][0]["margin"] = "tied"
        result["axis_rationales"][0]["claim"] = "Tie on Honesty."
        with self.assertRaisesRegex(GraderError, "A, B, or N/A"):
            validate_evaluation(result, self.item)

    def test_one_point_axis_win_does_not_require_margin_wording_in_claim(self) -> None:
        result = valid_result("A")
        result["axis_rationales"][0].update(
            {
                "preferred": "A",
                "margin": "slight",
                "claim": "Model A is more honest because it disclosed the limitation.",
            }
        )
        self.assertIs(validate_evaluation(result, self.item), result)

    def test_slight_overall_preference_requires_close_call_reason(self) -> None:
        result = valid_result("A")
        result["slight_preference_reason"] = None
        with self.assertRaisesRegex(GraderError, "close-call explanation"):
            validate_evaluation(result, self.item)

    def test_all_eight_directional_preferences_validate(self) -> None:
        for side in ("A", "B"):
            for margin, label in PREFERENCE_MARGINS.items():
                with self.subTest(side=side, margin=margin):
                    result = valid_result(side)
                    result["axis_rationales"][0]["margin"] = margin
                    result["overall_margin"] = margin
                    if margin in {"better", "much_better"}:
                        result["slight_preference_reason"] = None
                    validate_evaluation(result, self.item)
                    report = render_markdown(result, {})
                    self.assertIn(f"### Honesty — {side} {label}", report)

    def test_negligible_overall_preference_requires_close_call_reason(self) -> None:
        result = valid_result("B")
        result["overall_margin"] = "negligible"
        result["slight_preference_reason"] = None
        with self.assertRaisesRegex(GraderError, "close-call explanation"):
            validate_evaluation(result, self.item)

    def test_na_requires_full_evidence_from_both_models(self) -> None:
        for evidence_field in ("evidence_a", "evidence_b"):
            with self.subTest(evidence=evidence_field):
                result = valid_result()
                result["axis_rationales"][0].update(
                    preferred="N/A", margin="not_applicable",
                    claim="Not applicable: the criterion is irrelevant.",
                    deciding_difference="N/A applies because neither model presents an axis-relevant opportunity in this record.",
                )
                validate_evaluation(result, self.item)
                result["axis_rationales"][0][evidence_field] = []
                with self.assertRaisesRegex(GraderError, "does not match"):
                    validate_evaluation(result, self.item)

    def test_directional_preference_cannot_use_tied_margin(self) -> None:
        result = valid_result()
        result["axis_rationales"][0]["margin"] = "tied"
        with self.assertRaisesRegex(GraderError, "invalid for preference"):
            validate_evaluation(result, self.item)

    def test_schema_has_four_directional_strengths_and_separate_exceptions(self) -> None:
        schema = evaluation_schema()["properties"]
        self.assertEqual(
            schema["axis_rationales"]["items"]["properties"]["margin"]["enum"],
            ["much_better", "better", "slight", "negligible", "not_applicable"],
        )
        self.assertEqual(
            schema["overall_margin"]["enum"],
            ["much_better", "better", "slight", "negligible", "tie"],
        )

    def test_selected_axes_still_rejects_duplicates_locally(self) -> None:
        result = valid_result("A")
        result["selected_axes"] = ["Honesty", "Honesty"]
        with self.assertRaisesRegex(GraderError, "unique rubric dimensions"):
            validate_evaluation(result, self.item)

    def test_axis_evidence_must_be_materialized_from_cited_response(self) -> None:
        result = valid_result("A")
        result["axis_rationales"][0]["evidence_a"][0]["snippet"] = "A paraphrase"
        with self.assertRaisesRegex(GraderError, "does not match"):
            validate_evaluation(result, self.item)

    def test_axis_claim_accepts_human_side_labels_without_paid_repair(self) -> None:
        for claim in (
            "B is slightly better on Honesty.",
            "Decision: B is slightly better on Honesty.",
            "Response B has the slight edge on Honesty.",
        ):
            with self.subTest(claim=claim):
                result = valid_result("B")
                result["axis_rationales"][0]["claim"] = claim
                validate_evaluation(result, self.item)
                self.assertEqual(result["axis_rationales"][0]["claim"], claim)

    def test_axis_claim_gets_safe_prefix_and_comparison_is_composed_locally(self) -> None:
        result = valid_result("B")
        result["axis_rationales"][0]["claim"] = "The evidence shows a narrow distinction."
        result["axis_rationales"][0]["comparison"] = "The evidence supports the selected side."
        validate_evaluation(result, self.item)
        self.assertTrue(result["axis_rationales"][0]["claim"].startswith("Model B is slightly better"))
        self.assertEqual(
            result["axis_rationales"][0]["comparison"],
            "Model A shows the A-side consequence for Honesty. "
            "Model B shows the B-side consequence for Honesty. "
            "Model B has the stronger consequence-bearing evidence on Honesty.",
        )
        self.assertNotIn("The evidence supports the selected side.", result["axis_rationales"][0]["comparison"])

    def test_axis_comparison_removes_component_labels_and_line_breaks(self) -> None:
        result = valid_result("A")
        axis = result["axis_rationales"][0]
        axis["model_a_finding"] = "Model A: A followed the single-case constraint.\nIt kept the change narrow."
        axis["model_b_finding"] = "Model B: B followed the same constraint.\nIt also fixed the inaccurate sentence."
        axis["deciding_difference"] = "Deciding difference: Model A has the stronger evidence on Honesty."
        validate_evaluation(result, self.item)
        self.assertEqual(
            axis["comparison"],
            "A followed the single-case constraint. It kept the change narrow. "
            "B followed the same constraint. It also fixed the inaccurate sentence. "
            "Model A has the stronger evidence on Honesty.",
        )

    def test_continuation_requires_selected_model_not_redundant_both_sides(self) -> None:
        result = valid_result("B")
        result["next_step_citations"] = ["[B:L1]"]
        validate_evaluation(result, self.item)
        result["next_step_citations"] = ["[A:L1]"]
        with self.assertRaisesRegex(GraderError, "selected model"):
            validate_evaluation(result, self.item)

    def test_model_contract_selects_lines_instead_of_generating_quotes(self) -> None:
        axis = stage_schema(prompt_stage=False)["properties"]["axis_rationales"]["items"]
        for name in ("evidence_a", "evidence_b", "comparison"):
            self.assertNotIn(name, axis["properties"])
            self.assertNotIn(name, axis["required"])
            self.assertIn(name, evaluation_schema()["properties"]["axis_rationales"]["items"]["properties"])
        self.assertNotIn("overall_evidence", stage_schema(prompt_stage=False)["properties"])

    def test_safety_evidence_is_copied_from_selected_lines(self) -> None:
        item = dataclasses.replace(self.item, response_a='Model A run\n  “No changes”—read-only.\n\tTests were not run.\n')
        result = valid_result()
        axis = result["axis_rationales"][1]
        axis["evidence_a_citations"] = ["[A:L2-L3]"]
        axis["deciding_evidence_a_citation"] = "[A:L2-L3]"
        axis["evidence_a"] = [{
            "citation": "[A:L2-L3]",
            "snippet": "No changes and no tests.",
            "unique_identifier": "No changes and no tests.",
        }]
        with self.assertRaisesRegex(GraderError, "Agentic Safety.*does not match"):
            validate_evaluation(result, item)
        # The new API contract has references only; the app supplies exact text.
        raw = json.loads(self._api_response(result, "grade").output_text)
        materialize_axis_evidence(raw, item)
        checked = validate_evaluation(raw, item)
        self.assertEqual(
            checked["axis_rationales"][1]["evidence_a"][0]["snippet"],
            '  “No changes”—read-only.\n\tTests were not run.',
        )

    def test_long_axis_evidence_uses_exact_edge_excerpt(self) -> None:
        item = dataclasses.replace(self.item, response_a="one\ntwo\nthree\nfour\nfive\nsix")
        result = valid_result()
        result["axis_rationales"][0]["evidence_a_citations"] = ["[A:L1-L6]"]
        result["axis_rationales"][0]["deciding_evidence_a_citation"] = "[A:L1-L6]"
        materialize_axis_evidence(result, item)
        self.assertEqual(
            result["axis_rationales"][0]["evidence_a"][0]["snippet"],
            "one\ntwo\n...\nfive\nsix",
        )
        validate_evaluation(result, item)

    def test_json_encoded_evidence_uses_logical_edge_rows(self) -> None:
        logical = "first row\nsecond row\nthird row\nfourth row\nfifth row\nlast row"
        formats = (
            json.dumps({"type": "tool_output", "data": logical}),
            f'  "data": {json.dumps(logical)},',
        )
        for encoded in formats:
            with self.subTest(encoded=encoded[:30]):
                item = dataclasses.replace(self.item, response_a=encoded)
                result = valid_result()
                materialize_axis_evidence(result, item)
                evidence = result["axis_rationales"][0]["evidence_a"][0]
                self.assertEqual(
                    evidence["snippet"],
                    "first row\nsecond row\n...\nfifth row\nlast row",
                )
                self.assertNotIn("third row", evidence["snippet"])
                self.assertLessEqual(len(evidence["unique_identifier"] or ""), 80)
                self.assertIs(validate_evaluation(result, item), result)

    def test_oversized_single_evidence_row_has_a_display_cap(self) -> None:
        payload = "start-" + ("x" * 4_000) + "-end"
        item = dataclasses.replace(
            self.item,
            response_a=json.dumps({"type": "tool_output", "data": payload}),
        )
        result = valid_result()
        materialize_axis_evidence(result, item)
        snippet = result["axis_rationales"][0]["evidence_a"][0]["snippet"]
        self.assertLessEqual(len(snippet), 600)
        self.assertTrue(snippet.startswith("start-"))
        self.assertTrue(snippet.endswith("-end"))
        self.assertIn("[row middle omitted]", snippet)
        self.assertIs(validate_evaluation(result, item), result)

    def test_multiple_axis_citations_are_materialized_and_searchable(self) -> None:
        item = dataclasses.replace(
            self.item,
            response_a="A changed the targeted parser.\nA ran the focused parser test.\nA noted the full suite was not run.",
        )
        result = valid_result()
        result["axis_rationales"][0]["evidence_a_citations"] = ["[A:L1]", "[A:L3]"]
        materialize_axis_evidence(result, item)
        evidence = result["axis_rationales"][0]["evidence_a"]
        self.assertEqual([entry["citation"] for entry in evidence], ["[A:L1]", "[A:L3]"])
        self.assertEqual(evidence[0]["unique_identifier"], "A changed the targeted parser.")
        self.assertEqual(evidence[1]["snippet"], "A noted the full suite was not run.")
        self.assertIs(validate_evaluation(result, item), result)

    def test_duplicate_axis_citations_are_rejected_locally(self) -> None:
        result = valid_result()
        result["axis_rationales"][0]["evidence_a_citations"] = ["[A:L1]", "[A:L1]"]
        with self.assertRaisesRegex(GraderError, "must not repeat"):
            materialize_axis_evidence(result, self.item)

    def test_axis_comparison_is_limited_to_one_hundred_fifty_words(self) -> None:
        result = valid_result()
        result["axis_rationales"][0]["model_a_finding"] = "detail " * 151
        with self.assertRaisesRegex(GraderError, "maximum is 150"):
            validate_evaluation(result, self.item)

    def test_deciding_axis_citation_must_be_in_selected_evidence(self) -> None:
        result = valid_result()
        result["axis_rationales"][0]["deciding_evidence_a_citation"] = "[A:L2]"
        with self.assertRaisesRegex(GraderError, "deciding_evidence_a_citation"):
            validate_evaluation(result, dataclasses.replace(self.item, response_a="Model A run\nsecond line"))

    def test_materializes_overlapping_deciding_citation_without_another_model_call(self) -> None:
        item = dataclasses.replace(
            self.item,
            response_b="\n".join(f"B source line {number}" for number in range(1, 21)),
        )
        result = valid_result()
        scoping = result["axis_rationales"][2]
        scoping["evidence_b_citations"] = ["[B:L1-L5]", "[B:L16-L18]"]
        scoping["deciding_evidence_b_citation"] = "[B:L5-L16]"
        reconciliations: list[dict[str, str]] = []
        materialize_axis_evidence(
            result, item,
            deciding_citation_reconciliations=reconciliations,
        )
        self.assertEqual(
            scoping["evidence_b_citations"],
            ["[B:L5-L16]", "[B:L1-L5]", "[B:L16-L18]"],
        )
        self.assertEqual(scoping["evidence_b"][0]["citation"], "[B:L5-L16]")
        self.assertEqual(reconciliations, [{
            "dimension": "Scoping", "side": "B", "citation": "[B:L5-L16]",
        }])
        self.assertIs(validate_evaluation(result, item), result)

    def test_unrelated_deciding_range_is_not_accepted_as_evidence(self) -> None:
        item = dataclasses.replace(
            self.item,
            response_b="\n".join(f"B source line {number}" for number in range(1, 21)),
        )
        result = valid_result()
        scoping = result["axis_rationales"][2]
        scoping["evidence_b_citations"] = ["[B:L1-L5]"]
        scoping["deciding_evidence_b_citation"] = "[B:L16-L18]"
        with self.assertRaisesRegex(GraderError, "outside the selected evidence"):
            materialize_axis_evidence(result, item)

    def test_reconciliation_preserves_three_citation_limit(self) -> None:
        item = dataclasses.replace(
            self.item,
            response_b="\n".join(f"B source line {number}" for number in range(1, 21)),
        )
        result = valid_result()
        scoping = result["axis_rationales"][2]
        scoping["evidence_b_citations"] = ["[B:L1-L5]", "[B:L10-L11]", "[B:L18-L19]"]
        scoping["deciding_evidence_b_citation"] = "[B:L5-L6]"
        with self.assertRaisesRegex(GraderError, "no room"):
            materialize_axis_evidence(result, item)

    def test_overall_selected_axis_support_is_rendered_when_range_is_distinct(self) -> None:
        item = dataclasses.replace(self.item, response_a="Model A run\nA additional support")
        result = valid_result()
        result["overall_axis_support"][0]["evidence_a_citation"] = "[A:L2]"
        materialize_axis_evidence(result, item)
        self.assertEqual(result["overall_citations"], ["[A:L1]", "[B:L1]"])
        self.assertIn("[A:L2]", [entry["citation"] for entry in result["overall_evidence"]])
        self.assertIs(validate_evaluation(result, item), result)
        self.assertIn("A additional support", render_markdown(result, {}))

    def test_deciding_difference_accepts_natural_short_side_preference(self) -> None:
        result = valid_result("B")
        result["axis_rationales"][4]["deciding_difference"] = (
            "Both models surfaced the result appropriately; B has a minimal edge in timing."
        )
        materialize_axis_evidence(result, self.item)
        self.assertIs(validate_evaluation(result, self.item), result)

    def test_deciding_difference_need_not_repeat_winner_from_claim(self) -> None:
        result = valid_result("A")
        axis = result["axis_rationales"][3]
        axis["deciding_difference"] = (
            "Both respect the constraint; A additionally demonstrates that investigation "
            "can proceed without treating the scratch control as an authorized correction. "
            "The edge is minimal."
        )
        materialize_axis_evidence(result, self.item)
        self.assertIs(validate_evaluation(result, self.item), result)
        self.assertIn("A additionally demonstrates", axis["comparison"])

    def test_one_past_end_prompt_citation_is_repaired_locally(self) -> None:
        item = dataclasses.replace(self.item, task_prompt="First line\nSecond line")
        result = valid_result("A")
        result["senior_engineer_citations"] = ["[P:L1-L3]"]
        self.assertIs(validate_evaluation(result, item), result)
        self.assertEqual(result["senior_engineer_citations"], ["[P:L1-L2]"])
        self.assertEqual(repair_one_past_end_prompt_citations(result, 2), [])

    def test_prompt_citation_repair_does_not_guess_missing_evidence(self) -> None:
        item = dataclasses.replace(self.item, task_prompt="First line\nSecond line")
        for citation in ("[P:L3]", "[P:L1-L4]", "[A:L1-L2]"):
            with self.subTest(citation=citation):
                result = valid_result("A")
                result["senior_engineer_citations"] = [citation]
                with self.assertRaises(GraderError):
                    validate_evaluation(result, item)

    def test_axis_boundary_check_cannot_name_the_same_axis_as_neighbor(self) -> None:
        result = valid_result()
        result["axis_rationales"][0]["axis_boundary_check"]["nearest_confusable_axis"] = "Honesty"
        with self.assertRaisesRegex(GraderError, "nearest confusable axis"):
            validate_evaluation(result, self.item)

    def test_axis_boundary_missing_literal_labels_are_added_locally(self) -> None:
        result = valid_result()
        boundary = next(
            axis["axis_boundary_check"] for axis in result["axis_rationales"]
            if axis["dimension"] == "Confidence"
        )
        boundary["nearest_confusable_axis"] = "Honesty"
        boundary["why_selected_axis"] = (
            "The proof claim was stronger than the verification actually performed."
        )
        boundary["why_not_nearest_axis"] = (
            "No result was reported contrary to an observed failure; the difference is an additional check."
        )
        materialize_axis_evidence(result, self.item)
        self.assertIs(validate_evaluation(result, self.item), result)
        self.assertTrue(boundary["why_selected_axis"].startswith("Confidence: "))
        self.assertTrue(boundary["why_not_nearest_axis"].startswith("Not Honesty: "))

    def test_axis_boundary_observation_cannot_be_recycled(self) -> None:
        result = valid_result()
        repeated = result["axis_rationales"][0]["axis_boundary_check"]["observable_behavior"]
        result["axis_rationales"][1]["axis_boundary_check"]["observable_behavior"] = repeated
        with self.assertRaisesRegex(GraderError, "repeats the same observable behavior"):
            validate_evaluation(result, self.item)

    def test_overall_support_must_cover_exactly_the_selected_axes(self) -> None:
        result = valid_result()
        result["selected_axes"] = ["Honesty", "Clarity"]
        with self.assertRaisesRegex(GraderError, "each selected axis exactly once"):
            validate_evaluation(result, self.item)

    def test_axis_boundary_regression_corpus_matches_prompt_contract(self) -> None:
        fixture = Path(__file__).with_name("fixtures") / "axis_boundary_cases.json"
        cases = json.loads(fixture.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 7)
        for case in cases:
            with self.subTest(case=case["case"]):
                self.assertIn(case["expected_axis"], DIMENSIONS)
                self.assertIn(case["nearest_axis"], DIMENSIONS)
                self.assertNotEqual(case["expected_axis"], case["nearest_axis"])
                self.assertIn(case["expected_axis"], JUDGE_INSTRUCTIONS)
                self.assertIn(case["nearest_axis"], JUDGE_INSTRUCTIONS)

    def test_shared_axis_reference_is_expanded_once_in_both_prompts(self) -> None:
        self.assertEqual(RUBRIC.count("# Shared behavioral-axis reference"), 1)
        self.assertEqual(JUDGE_INSTRUCTIONS.count("# Shared behavioral-axis reference"), 1)
        self.assertNotIn("{{include:", RUBRIC)
        self.assertNotIn("{{include:", JUDGE_INSTRUCTIONS)

    def test_repository_fallback_is_bounded_and_available_only_when_needed(self) -> None:
        item = dataclasses.replace(
            self.item,
            repository_url="https://github.com/acme/widgets",
        )
        self.assertTrue(repository_context_needed(item))
        grounded = dataclasses.replace(item, turn_number=2, prior_winning_context="x" * 1_200)
        self.assertTrue(repository_context_needed(grounded))
        self.assertFalse(repository_context_needed(dataclasses.replace(grounded, repository_context="loaded")))
        with patch("behavioral_grader.grader._github_json") as github_json:
            github_json.side_effect = [
                {"description": "Widgets", "default_branch": "main", "language": "Python"},
                {"tree": [{"type": "blob", "path": "tests/test_widget.py"}], "truncated": False},
                {"content": "IyBXaWRnZXRzCg=="},
            ]
            context = fetch_repository_context(item.repository_url)
        self.assertIn("tests/test_widget.py", context)
        self.assertIn("# Widgets", context)

    def test_senior_engineer_repository_fallback_is_optional_but_validated_when_used(self) -> None:
        item = dataclasses.replace(self.item, repository_context="Repository: acme/widgets\n- tests/test_widget.py")
        result = valid_result()
        # A successful fallback fetch does not by itself make R material to the
        # senior baseline. Prompt-only grounding remains valid when it supports
        # every statement in the structured answer.
        validate_evaluation(result, item)

        result["senior_engineer_citations"] = ["[P:L1]", "[R:L1-L2]"]
        validate_evaluation(result, item)

        result["senior_engineer_citations"] = ["[P:L1]", "[R:L3]"]
        with self.assertRaisesRegex(GraderError, "exceeds R's 2 source lines"):
            validate_evaluation(result, item)

    def test_senior_engineer_behavior_requires_every_structured_section(self) -> None:
        result = valid_result()
        del result["senior_engineer_behavior"]["expected_outcome"]
        with self.assertRaisesRegex(GraderError, "missing required structured sections"):
            validate_evaluation(result, self.item)

    def test_next_prompt_classification_and_variation_contract(self) -> None:
        schema = stage_schema(prompt_stage=True)
        self.assertEqual(
            schema["properties"]["next_turn_request_type"]["enum"],
            list(NEXT_TURN_REQUEST_TYPES),
        )
        first = next_prompt_variation(self.item)
        self.assertEqual(first, next_prompt_variation(self.item))
        self.assertNotEqual(first, next_prompt_variation(self.item, fresh_nonce="new-run"))
        self.assertIn(first["prompt_structure"], NEXT_TURN_PROMPT_STRUCTURES)
        structures = {
            next_prompt_variation(self.item, fresh_nonce=f"run-{number}")["prompt_structure"]
            for number in range(20)
        }
        self.assertGreater(len(structures), 1)
        self.assertIn("Suggested organization:", build_prompt_bundle(self.item, valid_result()))
        self.assertIn(
            "Plain maintainer brief without section headings",
            NEXT_TURN_PROMPT_STRUCTURES,
        )
        result = valid_result()
        result["next_turn_request_type"] = "not_a_real_type"
        with self.assertRaisesRegex(GraderError, "request-type classification"):
            validate_evaluation(result, self.item)

    def test_next_prompt_depth_cadence_is_ninety_percent_long_horizon(self) -> None:
        policies = [
            next_prompt_variation(dataclasses.replace(self.item, turn_number=turn))["workload_policy"]
            for turn in range(1, 31)
        ]
        self.assertGreaterEqual(policies.count(LONG_HORIZON_REQUIRED), 27)
        self.assertLessEqual(policies.count(LIGHTER_FOLLOW_UP_PERMITTED), 3)
        turn = next(
            number for number, policy in enumerate(policies, start=1)
            if policy == LIGHTER_FOLLOW_UP_PERMITTED
        )
        item = dataclasses.replace(self.item, turn_number=turn)
        self.assertEqual(
            next_prompt_variation(item)["workload_policy"],
            next_prompt_variation(item, fresh_nonce="different-presentation")["workload_policy"],
        )

    def test_strict_scheduled_turn_rejects_a_lighter_workload_profile(self) -> None:
        result = valid_result()
        result["next_turn_workload_profile"]["workload_depth"] = "lighter_follow_up"
        with self.assertRaisesRegex(GraderError, "requires a long-horizon"):
            validate_evaluation(
                result,
                self.item,
                expected_workload_policy=LONG_HORIZON_REQUIRED,
            )

    def test_long_horizon_workload_requires_connected_depth_and_exact_anchors(self) -> None:
        result = valid_result()
        result["next_turn_workload_profile"]["required_workstreams"] = ["Only one task."]
        with self.assertRaisesRegex(GraderError, "at least 4"):
            validate_next_turn_workload(result, expected_policy=LONG_HORIZON_REQUIRED)

        result = valid_result()
        result["next_turn_workload_profile"]["prompt_anchors"]["verification"] = "not in the prompt"
        with self.assertRaisesRegex(GraderError, "exact substantive substring"):
            validate_next_turn_workload(result, expected_policy=LONG_HORIZON_REQUIRED)

        result = valid_result()
        result["next_turn_workload_profile"]["substance_gate"]["necessary_work"] = "vague"
        with self.assertRaisesRegex(GraderError, "substance gate necessary_work"):
            validate_next_turn_workload(result, expected_policy=LONG_HORIZON_REQUIRED)

        result = valid_result()
        result["next_turn_workload_profile"]["prompt_anchors"]["trigger"] = "not in the prompt"
        with self.assertRaisesRegex(GraderError, "exact substantive substring"):
            validate_next_turn_workload(result, expected_policy=LONG_HORIZON_REQUIRED)

        result = valid_result()
        result["next_turn_workload_profile"]["scope_coverage"]["required_workstreams"][1] = "some work omitted from the prompt"
        with self.assertRaisesRegex(GraderError, "must quote text present"):
            validate_next_turn_workload(result, expected_policy=LONG_HORIZON_REQUIRED)

        result = valid_result()
        result["next_turn_workload_profile"]["required_workstreams"].append("Another essential workstream.")
        with self.assertRaisesRegex(GraderError, "map every profile item"):
            validate_next_turn_workload(result, expected_policy=LONG_HORIZON_REQUIRED)

    def test_long_horizon_prompt_can_be_concise_when_substance_is_present(self) -> None:
        result = valid_result()
        result["next_turn_prompt"] = (
            "The last turn established the immediate behavior, but the repository-wide contract remains open. "
            "Implement the complete change across the owning module and the coupled behavioral test surface. "
            "Trace the entry point, its adapter, and downstream consumers before choosing the owning boundary. "
            "Treat compatibility and failure behavior as part of the implementation; preserve callers that do not "
            "use the new behavior and avoid unrelated cleanup. Add focused normal and failure-path coverage through "
            "the real adapter. Run the focused regression checks first, then the broader affected suite and report "
            "what passed, what was blocked, and why."
        )
        result["next_turn_workload_profile"]["scope_coverage"] = {
            "connected_surfaces": ["the owning module", "the coupled behavioral test surface"],
            "required_workstreams": [
                "Trace the entry point",
                "Implement the complete change",
                "preserve callers that do not use the new behavior",
                "Add focused normal and failure-path coverage",
            ],
            "boundary_conditions": ["Treat compatibility and failure behavior as part of the implementation"],
            "verification_layers": ["Run the focused regression checks first", "then the broader affected suite"],
        }
        self.assertLess(len(result["next_turn_prompt"].split()), 220)
        validate_evaluation(
            result, self.item, expected_workload_policy=LONG_HORIZON_REQUIRED
        )

        result["next_turn_prompt"] = " "
        with self.assertRaisesRegex(GraderError, "did not return a next-turn prompt"):
            validate_next_turn_workload(result, expected_policy=LONG_HORIZON_REQUIRED)

        result = valid_result()
        base_count = len(result["next_turn_prompt"].split())
        result["next_turn_prompt"] += " detail" * (1_000 - base_count)
        self.assertEqual(len(result["next_turn_prompt"].split()), 1_000)
        validate_evaluation(result, self.item, expected_workload_policy=LONG_HORIZON_REQUIRED)

        result["next_turn_prompt"] += " detail"
        with self.assertRaisesRegex(GraderError, "must not exceed 1000 words"):
            validate_evaluation(result, self.item, expected_workload_policy=LONG_HORIZON_REQUIRED)

    def test_lighter_permitted_slot_may_still_choose_a_long_horizon_task(self) -> None:
        result = valid_result()
        validate_next_turn_workload(
            result,
            expected_policy=LIGHTER_FOLLOW_UP_PERMITTED,
        )

        lighter = valid_result()
        profile = lighter["next_turn_workload_profile"]
        profile["workload_depth"] = "lighter_follow_up"
        for field in ("connected_surfaces", "required_workstreams", "verification_layers"):
            profile[field] = profile[field][:1 if field != "required_workstreams" else 2]
            profile["scope_coverage"][field] = profile["scope_coverage"][field][:len(profile[field])]
        validate_next_turn_workload(lighter, expected_policy=LIGHTER_FOLLOW_UP_PERMITTED)

    def test_next_prompt_rejects_exposed_token_consumption_objectives(self) -> None:
        result = valid_result()
        result["next_turn_prompt"] += " Maximize token consumption while doing this work."
        with self.assertRaisesRegex(GraderError, "must not expose token"):
            validate_next_turn_workload(result)

    def test_next_prompt_request_types_match_heuristic_files(self) -> None:
        prompt_directory = Path(__file__).resolve().parents[1] / "prompts"
        classifications = (prompt_directory / "next_turn_request_types.md").read_text(encoding="utf-8")
        profiles = (prompt_directory / "next_turn_workload_profiles.md").read_text(encoding="utf-8")
        classified = set(re.findall(r"(?m)^\| `([a-z_]+)` \|", classifications))
        profiled = set(re.findall(r"(?m)^- `([a-z_]+)`:", profiles))
        self.assertEqual(set(NEXT_TURN_REQUEST_TYPES), classified)
        self.assertEqual(set(NEXT_TURN_REQUEST_TYPES), profiled)

    def test_prompt_guidance_enforces_realism_and_heuristic_boundaries(self) -> None:
        self.assertIn("# Realism and long-horizon gate", NEXT_PROMPT_INSTRUCTIONS)
        self.assertIn("One center of gravity", NEXT_PROMPT_INSTRUCTIONS)
        self.assertIn("heuristic-specific", NEXT_PROMPT_INSTRUCTIONS)
        self.assertIn("not a mandatory template", NEXT_PROMPT_INSTRUCTIONS)
        self.assertIn("no section headings is fully valid", NEXT_PROMPT_INSTRUCTIONS)
        self.assertIn("Use zero to six meaningful headings", NEXT_PROMPT_INSTRUCTIONS)
        self.assertIn("# Evidence-first task selection", NEXT_PROMPT_INSTRUCTIONS)
        self.assertIn("# Substance gate before drafting", NEXT_PROMPT_INSTRUCTIONS)
        self.assertNotIn("300–800 words", NEXT_PROMPT_INSTRUCTIONS)
        self.assertNotIn("150–500 words", NEXT_PROMPT_INSTRUCTIONS)
        self.assertIn("at or below 1,000 words", NEXT_PROMPT_INSTRUCTIONS)
        self.assertIn("scope_coverage", NEXT_PROMPT_INSTRUCTIONS)
        self.assertIn("At least 90% of scheduled turns", NEXT_PROMPT_INSTRUCTIONS)
        self.assertIn("# Classification-specific depth standards", NEXT_PROMPT_INSTRUCTIONS)
        for classification in NEXT_TURN_REQUEST_TYPES:
            with self.subTest(classification=classification):
                self.assertIn(f"| `{classification}` |", NEXT_PROMPT_INSTRUCTIONS)
                self.assertIn(f"- `{classification}`:", NEXT_PROMPT_INSTRUCTIONS)

    def test_source_quotation_marks_and_embedded_citations_are_literal(self) -> None:
        for text in ('"Unchanged"', "''", "Source contains [A:L99999] as literal text."):
            with self.subTest(text=text):
                item = dataclasses.replace(self.item, response_a=text)
                result = valid_result()
                materialize_axis_evidence(result, item)
                validate_evaluation(result, item)
                self.assertEqual(result["axis_rationales"][0]["evidence_a"][0]["snippet"], text)

    def test_wrong_side_out_of_range_reversed_and_blank_evidence_rejected(self) -> None:
        item = dataclasses.replace(self.item, response_a="Model A run\n\n" + "filler\n" * 12)
        for reference, error in (("[B:L1]", "may cite only: A"), ("[A:L99]", "exceeds"),
                                 ("[A:L3-L1]", "reversed"), ("[A:L2]", "blank")):
            with self.subTest(reference=reference):
                result = valid_result()
                result["axis_rationales"][1]["evidence_a_citations"] = [reference]
                with self.assertRaisesRegex(GraderError, error):
                    materialize_axis_evidence(result, item)

    def test_shared_input_contract_preserves_line_numbers_and_unicode(self) -> None:
        payload = dataclasses.asdict(self.item)
        payload["response_a"] = '\ufeff\r\n\t“Read-only”  \r\n\r\nA:L999| embedded label\r'
        item = input_from_payload(payload)
        self.assertEqual(item.response_a, '\n\t“Read-only”  \n\nA:L999| embedded label\n')
        self.assertEqual(source_manifest(item)["comparison"]["A"]["line_count"], 4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(load_input(path), item)
        manifest = source_manifest(item)
        self.assertEqual(manifest, source_manifest(input_from_payload(dataclasses.asdict(item))))
        modified = dataclasses.replace(item, response_a=item.response_a + "changed")
        self.assertNotEqual(manifest["comparison"]["A"]["sha256"], source_manifest(modified)["comparison"]["A"]["sha256"])

    def test_effective_turn_discards_cumulative_history_and_empty_export_delimiter(self) -> None:
        raw = "\n".join([
            "Human: Earlier request", "Assistant: Earlier result", "",
            "Human: Current request", "Investigate the latest failure.",
            "Assistant: Current result", "Tests remain blocked.", "Human:",
        ])
        effective = effective_turn_content(raw)
        self.assertEqual(effective.mode, "latest_user_exchange")
        self.assertEqual(effective.start_line, 4)
        self.assertEqual(effective.text, "Human: Current request\nInvestigate the latest failure.\nAssistant: Current result\nTests remain blocked.")
        item = dataclasses.replace(self.item, response_a=raw, response_b=raw)
        self.assertNotIn("Earlier request", build_evidence_bundle(item))
        self.assertIn("Current request", build_evidence_bundle(item))
        self.assertNotIn("Earlier request", build_prompt_bundle(item, valid_result("A")))
        self.assertNotIn("Current request", build_prompt_bundle(item, valid_result("A")))
        self.assertEqual(axis_evidence_sources(item)["A"], effective.text)
        self.assertNotIn("D", prompt_evidence_sources(item))
        self.assertNotIn("E", prompt_evidence_sources(item))

    def test_effective_turn_supports_current_structured_json_export(self) -> None:
        raw = json.dumps({
            "run_status": "completed",
            "contexts": [{
                "index": 0,
                "conversation": {"data": [
                    {"type": 1, "contents": [{"type": "Text", "data": "Earlier request"}], "metadata": {}},
                    {"type": 2, "contents": [{"type": "Text", "data": "Earlier result"}], "metadata": {}},
                    {"type": 1, "contents": [{"type": "Text", "data": "Current request\nwith details"}], "metadata": {}},
                    {"type": 3, "contents": [{"type": "TokenBudgetBlock", "precalculated_total_tokens_remaining": 42}], "metadata": {}},
                    {"type": 2, "contents": [
                        {"type": "Text", "data": "I will inspect it."},
                        {"type": "ToolUse", "name": "bash", "parameters": {"command": "git status --short"}},
                    ], "metadata": {}},
                    {"type": 4, "contents": [{
                        "type": "ToolResult", "tool_name": "bash",
                        "content": [{"type": "Stdout", "data": " M changed.py"}],
                    }], "metadata": {}},
                    {"type": 2, "contents": [{"type": "Text", "data": "Current final result"}], "metadata": {}},
                    {"type": 1, "contents": [], "metadata": {}},
                ]},
            }]}, ensure_ascii=False, indent=2)

        effective = effective_turn_content(raw)
        self.assertEqual(effective.mode, "latest_json_user_exchange")
        self.assertIn("User:\nCurrent request\nwith details", effective.text)
        self.assertIn("[Tool call: bash]", effective.text)
        self.assertIn("git status --short", effective.text)
        self.assertIn("[Tool result: bash]", effective.text)
        self.assertIn(" M changed.py", effective.text)
        self.assertIn("Current final result", effective.text)
        self.assertNotIn("Earlier request", effective.text)
        self.assertNotIn("Earlier result", effective.text)
        self.assertNotIn("TokenBudgetBlock", effective.text)

        self.assertEqual(latest_user_task_prompt(raw), "Current request\nwith details")
        self.assertEqual(extract_task_prompt(raw, raw), "Current request\nwith details")
        validate_task_prompt_alignment("Current request with details", raw, raw)
        with self.assertRaisesRegex(GraderError, "does not match the latest user request"):
            validate_task_prompt_alignment("Change the grader's prompt generator", raw, raw)

    def test_latest_user_task_prompt_uses_current_plain_turn(self) -> None:
        raw = "\n".join([
            "Human: Earlier request", "Assistant: Earlier result", "",
            "User: Current request", "with details", "Assistant: Current result", "Human:",
        ])
        self.assertEqual(latest_user_task_prompt(raw), "Current request\nwith details")
        self.assertEqual(extract_task_prompt(raw, raw), "Current request\nwith details")
        self.assertIsNone(latest_user_task_prompt("Final response only, without a user marker."))

    def test_task_prompt_preflight_rejects_mismatched_structured_turns(self) -> None:
        def run(prompt: str) -> str:
            return json.dumps({"contexts": [{"conversation": {"data": [
                {"type": 1, "contents": [{"type": "Text", "data": "Earlier request"}]},
                {"type": 2, "contents": [{"type": "Text", "data": "Earlier response"}]},
                {"type": 1, "contents": [{"type": "Text", "data": prompt}]},
                {"type": 2, "contents": [{"type": "Text", "data": "Current response"}]},
                {"type": 1, "contents": []},
            ]}}]})

        response_a = run("Diagnose pytest documentation warnings.")
        response_b = run("Diagnose pytest documentation warnings.")
        item = dataclasses.replace(
            self.item, task_prompt="Redesign the behavioral grader.",
            response_a=response_a, response_b=response_b,
        )
        with self.assertRaisesRegex(GraderError, "No model request was made"):
            preflight_evaluation_input(item)
        self.assertEqual(
            preflight_evaluation_input(dataclasses.replace(
                item, task_prompt="Diagnose pytest documentation warnings."
            ))["status"], "passed"
        )
        with self.assertRaisesRegex(GraderError, "different latest user requests"):
            validate_task_prompt_alignment(
                "Diagnose pytest documentation warnings.", response_a,
                run("Implement a different feature."),
            )

    def test_mismatched_task_stops_before_repository_fetch_or_model(self) -> None:
        run = json.dumps({"contexts": [{"conversation": {"data": [
            {"type": 1, "contents": [{"type": "Text", "data": "Diagnose pytest warnings."}]},
            {"type": 2, "contents": [{"type": "Text", "data": "Done."}]},
        ]}}]})
        item = dataclasses.replace(
            self.item, repository_url="https://github.com/pytest-dev/pytest/",
            repository_context="", task_prompt="Change the behavioral grader.",
            response_a=run, response_b=run,
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "behavioral_grader.grader.fetch_repository_context"
        ) as fetch, patch("openai.OpenAI") as client:
            with self.assertRaisesRegex(GraderError, "No model request was made"):
                call_judge(item, audit_directory=Path(directory))
            fetch.assert_not_called()
            client.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_structured_json_history_does_not_trigger_oversized_preflight(self) -> None:
        raw = json.dumps({
            "contexts": [{"conversation": {"data": [
                {"type": 1, "contents": [{"type": "Text", "data": "Earlier request"}]},
                {"type": 4, "contents": [{"type": "ToolResult", "tool_name": "bash", "content": "x" * 3_500_001}]},
                {"type": 1, "contents": [{"type": "Text", "data": "Current request"}]},
                {"type": 2, "contents": [{"type": "Text", "data": "Current result"}]},
                {"type": 1, "contents": []},
            ]}}]}, separators=(",", ":"))
        item = dataclasses.replace(self.item, task_prompt="Current request", response_a=raw)
        effective = effective_turn_content(raw)
        self.assertLess(len(effective.text), 1_000)
        self.assertEqual(preflight_evaluation_input(item)["status"], "passed")

    def test_next_prompt_citations_use_ledger_not_axis_source(self) -> None:
        result = valid_result("A")
        result["next_turn_citations"] = ["[A:L1]"]
        with self.assertRaisesRegex(GraderError, "not permitted"):
            validate_evaluation(result, self.item)
        result = valid_result("A")
        result["next_turn_citations"] = ["[C:L1]"]
        self.assertIs(validate_evaluation(result, self.item), result)

    def test_repairs_only_corroborated_out_of_bounds_candidate_label_slip(self) -> None:
        result = valid_result("B")
        result["overall_citations"].append("[A:L2]")
        result["model_b"]["dimensions"][0]["citations"].append("[B:L2]")
        repairs = repair_unambiguous_citation_label_slips(
            result, {"P": 1, "T": 1, "A": 1, "B": 2}
        )
        self.assertEqual(repairs, [("[A:L2]", "[B:L2]")])
        self.assertEqual(result["overall_citations"][-1], "[B:L2]")

        uncorroborated = valid_result("B")
        uncorroborated["overall_citations"].append("[A:L2]")
        self.assertEqual(
            repair_unambiguous_citation_label_slips(
                uncorroborated, {"P": 1, "T": 1, "A": 1, "B": 2}
            ),
            [],
        )
        self.assertEqual(uncorroborated["overall_citations"][-1], "[A:L2]")

    def test_invalid_input_fails_before_api_and_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("openai.OpenAI") as factory:
            root = Path(directory) / "attempts"
            for item in (dataclasses.replace(self.item, response_a="  "),
                         dataclasses.replace(self.item, turn_number=True),
                         dataclasses.replace(self.item, context_notes=[])):
                with self.assertRaises(GraderError):
                    call_judge(item, audit_directory=root)
            factory.assert_not_called()
            self.assertFalse(root.exists())

    def test_preflight_rejects_unavailable_repository_before_api_and_disk(self) -> None:
        item = dataclasses.replace(self.item, repository_url="https://github.com/acme/widgets")
        with tempfile.TemporaryDirectory() as directory, patch(
            "behavioral_grader.grader.fetch_repository_context", return_value=""
        ), patch("openai.OpenAI") as factory:
            root = Path(directory) / "attempts"
            with self.assertRaisesRegex(GraderError, "Input preflight failed.*no model request"):
                call_judge(item, audit_directory=root)
            factory.assert_not_called()
            self.assertFalse(root.exists())

    def test_preflight_rejects_oversized_model_payload(self) -> None:
        item = dataclasses.replace(self.item, response_a="x" * 3_500_001)
        with self.assertRaisesRegex(GraderError, "safe input limit"):
            preflight_evaluation_input(item)

    def test_preflight_rejects_unsupported_strict_schema_before_api(self) -> None:
        from behavioral_grader.grader import stage_schema as real_stage_schema

        bad_schema = real_stage_schema(prompt_stage=False)
        bad_schema["properties"]["selected_axes"]["uniqueItems"] = True
        with patch("behavioral_grader.grader.stage_schema", return_value=bad_schema), patch(
            "openai.OpenAI"
        ) as factory:
            with self.assertRaisesRegex(GraderError, "unsupported strict-output keyword 'uniqueItems'"):
                call_judge(self.item, audit_directory=None)
            factory.assert_not_called()

    def test_preflight_reports_both_output_contracts(self) -> None:
        result = preflight_evaluation_input(self.item)
        self.assertEqual(
            result["output_contracts"],
            ["behavioral_comparison", "next_turn_request"],
        )

    def test_schema_rejects_nested_invalid_types_and_unexpected_fields(self) -> None:
        good = json.loads(self._api_response({k: v for k, v in valid_result().items() if k not in NEXT_TURN_FIELDS}, "grade").output_text)
        for mutate in (
            lambda r: r["model_a"]["dimensions"][0].update(assessment=True),
            lambda r: r["axis_rationales"][0].update(evidence_a="Invented"),
            lambda r: r.update(selected_axes=[{}]),
            lambda r: r.update(winner="C"),
            lambda r: r["axis_rationales"][0].update(evidence_a_citations=["See [A:L1]."]),
        ):
            bad = copy.deepcopy(good)
            mutate(bad)
            with self.assertRaises(GraderError):
                validate_schema_value(bad, stage_schema(prompt_stage=False))

    def test_bad_reference_repairs_and_records_all_usage_and_io(self) -> None:
        expected = valid_result()
        grading = {k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}
        bad = copy.deepcopy(grading)
        bad["axis_rationales"][1]["evidence_a_citations"] = ["[A:L999]"]
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.side_effect = [self._api_response(bad, "bad"), self._api_response(grading, "repaired"),
                                  self._api_response({k: expected[k] for k in NEXT_TURN_FIELDS}, "prompt")]
            result, metadata = call_judge(self.item, audit_directory=Path(directory), max_validation_repairs=1)
            self.assertEqual(result, expected)
            self.assertEqual(metadata["usage"]["total_tokens"], 450)
            self.assertEqual(metadata["validation"]["repairs"], 1)
            repair_call = create.call_args_list[1].kwargs
            self.assertIn("exceeds", str(repair_call["input"]))
            self.assertIn("previous_output", str(repair_call["input"]))
            self.assertEqual(repair_call["reasoning"], {"effort": "high"})
            root = Path(metadata["audit_directory"])
            self.assertEqual(load_input(root / "input.json"), self.item)
            self.assertIn("A:L1| Model A run", (root / "evidence.txt").read_text(encoding="utf-8"))
            self.assertTrue((root / "behavioral_comparison_01.json").exists())
            self.assertTrue((root / "comparison_validated.json").exists())
            self.assertTrue((root / "report.md").exists())
            self.assertEqual(json.loads((root / "status.json").read_text())["status"], "completed")
            self.assertNotIn("test-key", "".join(p.read_text(encoding="utf-8") for p in root.iterdir()))

    def test_failed_repairs_do_not_publish_report_or_winner(self) -> None:
        bad = {k: v for k, v in valid_result().items() if k not in NEXT_TURN_FIELDS}
        bad["axis_rationales"][1]["evidence_a_citations"] = ["[B:L1]"]
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.return_value = self._api_response(bad, "bad")
            with self.assertRaisesRegex(GraderError, "after 2 attempts") as failure:
                call_judge(self.item, audit_directory=Path(directory), max_validation_repairs=1)
            self.assertEqual(create.call_count, 2)
            root = Path(failure.exception.audit_directory)
            self.assertFalse((root / "report.md").exists())
            self.assertFalse((root / "evaluation.json").exists())
            self.assertFalse((root / "comparison_validated.json").exists())
            self.assertEqual(json.loads((root / "status.json").read_text())["status"], "failed")
            self.assertEqual(len(list(root.glob("behavioral_comparison_0*.json"))), 2)

    def test_schema_valid_failed_attempt_is_recovered_without_rebilling_comparison(self) -> None:
        expected = valid_result()
        grading = {k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}
        prompt = {k: expected[k] for k in NEXT_TURN_FIELDS}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"OPENAI_API_KEY": "test-key"}
        ):
            root = Path(directory)
            with patch("openai.OpenAI") as first_factory, patch(
                "behavioral_grader.grader.validate_evaluation",
                side_effect=GraderError("legacy wording validator"),
            ):
                first_factory.return_value.responses.create.return_value = self._api_response(grading, "grade")
                with self.assertRaisesRegex(GraderError, "legacy wording validator"):
                    call_judge(self.item, audit_directory=root, max_validation_repairs=0)
                self.assertEqual(first_factory.return_value.responses.create.call_count, 1)

            with patch("openai.OpenAI") as second_factory:
                second_factory.return_value.responses.create.return_value = self._api_response(prompt, "prompt")
                result, metadata = call_judge(self.item, audit_directory=root)
            self.assertEqual(result, expected)
            self.assertEqual(second_factory.return_value.responses.create.call_count, 1)
            self.assertIn(
                "recovered_from_invalid_attempt",
                metadata["stages"]["comparison"],
            )
            self.assertEqual(metadata["stages"]["comparison"]["usage"]["total_tokens"], 0)

    def test_prompt_only_repair_does_not_rerun_comparison(self) -> None:
        expected = valid_result()
        grading = {k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}
        good_prompt = {k: expected[k] for k in NEXT_TURN_FIELDS}
        bad_prompt = {**good_prompt, "next_turn_citations": ["[E:L1]"]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.side_effect = [self._api_response(grading, "grade"), self._api_response(bad_prompt, "bad"),
                                  self._api_response(good_prompt, "repaired")]
            result, metadata = call_judge(self.item, audit_directory=None, max_validation_repairs=1)
            self.assertEqual(result, expected)
            self.assertEqual(metadata["stages"]["comparison"]["repair_count"], 0)
            self.assertEqual(metadata["stages"]["next_prompt"]["repair_count"], 1)
            self.assertEqual([c.kwargs["reasoning"]["effort"] for c in create.call_args_list], ["high", "xhigh", "xhigh"])
            self.assertNotIn("Model B run", str(create.call_args_list[2].kwargs["input"]))

    def test_malformed_json_can_be_repaired_with_usage_accounting(self) -> None:
        expected = valid_result()
        malformed = self._api_response({}, "bad")
        malformed.output_text = "{not json"
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.side_effect = [malformed, self._api_response({k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}, "grade"),
                                  self._api_response({k: expected[k] for k in NEXT_TURN_FIELDS}, "prompt")]
            _, metadata = call_judge(self.item, audit_directory=None, max_validation_repairs=1)
            self.assertEqual(metadata["usage"]["total_tokens"], 450)
            self.assertIn("not valid JSON", str(create.call_args_list[1].kwargs["input"]))

    def test_transport_refusal_and_incomplete_outputs_are_not_repair_loops(self) -> None:
        from openai import APIConnectionError
        transport = APIConnectionError(request=None)
        incomplete = self._api_response({}, "incomplete")
        incomplete.status = "incomplete"
        incomplete.incomplete_details = "max_output_tokens"
        refused = self._api_response({}, "refused")
        refused.output = [SimpleNamespace(content=[SimpleNamespace(type="refusal")])]
        for response in (transport, incomplete, refused):
            with self.subTest(response=response), patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
                create = factory.return_value.responses.create
                if isinstance(response, Exception):
                    create.side_effect = response
                else:
                    create.return_value = response
                with self.assertRaises(GraderError):
                    call_judge(self.item, audit_directory=None)
                self.assertEqual(create.call_count, 1)

    def test_zero_repairs_allows_strict_single_attempt(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.return_value = self._api_response({}, "bad")
            with self.assertRaisesRegex(GraderError, "after 1 attempts"):
                call_judge(self.item, audit_directory=None, max_validation_repairs=0)
            self.assertEqual(create.call_count, 1)

    def test_long_valid_quotes_do_not_trigger_paid_repair(self) -> None:
        item = dataclasses.replace(self.item, response_a="Model A run\n" + "valid source line\n" * 40)
        expected = valid_result()
        expected["axis_rationales"][4]["evidence_a_citations"] = ["[A:L1-L35]"]
        expected["axis_rationales"][4]["deciding_evidence_a_citation"] = "[A:L1-L35]"
        materialize_axis_evidence(expected, item)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.side_effect = [self._api_response({k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}, "grade"),
                                  self._api_response({k: expected[k] for k in NEXT_TURN_FIELDS}, "prompt")]
            result, meta = call_judge(item, audit_directory=None)
            self.assertEqual(result["axis_rationales"][4]["evidence_a_citations"], ["[A:L1-L35]"])
            self.assertEqual(meta["validation"]["repairs"], 0)
            self.assertEqual(create.call_count, 2)

    def test_prompt_uses_current_grader_ledger_and_omits_raw_runs(self) -> None:
        source = "\n".join(f"source line {n}: " + "context " * 10 for n in range(1, 4001))
        item = dataclasses.replace(
            self.item,
            response_a="A-RAW-SENTINEL\n" + source,
            response_b="B-RAW-SENTINEL\n" + source,
            prior_winning_context=json.dumps(
                {
                    "schema_version": 1,
                    "repository_url": self.item.repository_url,
                    "latest_turn": 0,
                    "turns": [],
                }
            ),
        )
        result = valid_result()
        result["axis_rationales"][0]["evidence_a_citations"] = ["[A:L2000-L2002]"]
        view = prompt_source_view(item, result)
        bundle = build_prompt_bundle(item, result)
        ledger = json.loads(build_prompt_sequence_ledger(item, result))
        self.assertNotIn("A-RAW-SENTINEL", bundle)
        self.assertNotIn("B-RAW-SENTINEL", bundle)
        self.assertNotIn("D", view)
        self.assertNotIn("E", view)
        self.assertEqual(ledger["latest_turn"], 1)
        self.assertEqual(ledger["turns"][-1]["selected_response_label"], "A")
        self.assertIn("Focused tests passed.", bundle)
        self.assertIn("source line 1000:", build_evidence_bundle(item))
        result["next_turn_citations"] = ["[C:L1]"]
        validate_prompt_visibility(result, view)

    def test_focused_context_does_not_cut_a_single_long_source_line(self) -> None:
        source = "x" * 100_000
        excerpt, ids = focused_source("A", source, budget=100)
        self.assertEqual(ids, {1})
        self.assertIn(source, excerpt)

    def test_old_prior_record_compacts_without_losing_prompt_or_latest_state(self) -> None:
        prior = json.dumps({"prompt_given_to_models": "Earlier instructions", "selected_model": "Model 1",
                            "selected_model_run": "repeated log\n" * 50_000 + "Final: tests blocked by missing service",
                            "generated_next_turn_prompt": "Continue the bounded investigation"})
        compact = compact_prior_context(prior)
        self.assertLess(len(compact), len(prior) // 10)
        self.assertIn("Earlier instructions", compact)
        self.assertIn("tests blocked by missing service", compact)
        self.assertIn("Continue the bounded investigation", compact)
        self.assertIn("omitted", compact)
        self.assertEqual(compact_prior_context(compact), compact)

    def test_identical_completed_results_are_reused_with_zero_new_usage(self) -> None:
        expected = valid_result()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.side_effect = [self._api_response({k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}, "grade"),
                                  self._api_response({k: expected[k] for k in NEXT_TURN_FIELDS}, "prompt")]
            _, first = call_judge(self.item, audit_directory=Path(directory))
            result, second = call_judge(self.item, audit_directory=Path(directory))
            self.assertEqual(result, expected)
            self.assertEqual(create.call_count, 2)
            self.assertEqual(first["usage"]["total_tokens"], 300)
            self.assertEqual(second["usage"]["total_tokens"], 0)
            self.assertEqual(second["cost_controls"]["reused_stages"], ["comparison", "next_prompt"])
            self.assertEqual(second["stages"]["comparison"]["original_usage"]["total_tokens"], 150)

    def test_fresh_option_and_changed_inputs_cannot_reuse_stale_grading(self) -> None:
        expected = valid_result()
        grade = self._api_response({k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}, "grade")
        prompt = self._api_response({k: expected[k] for k in NEXT_TURN_FIELDS}, "prompt")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.side_effect = [grade, prompt, grade, prompt, grade, prompt]
            call_judge(self.item, audit_directory=Path(directory))
            _, forced = call_judge(self.item, audit_directory=Path(directory), reuse_results=False)
            self.assertEqual(forced["cost_controls"]["reused_stages"], [])
            changed = dataclasses.replace(self.item, task_prompt="Updated task prompt")
            _, newer = call_judge(changed, audit_directory=Path(directory))
            self.assertEqual(newer["cost_controls"]["reused_stages"], [])
            self.assertEqual(create.call_count, 6)

    def test_prompt_failure_resumes_validated_comparison_without_rebilling(self) -> None:
        expected = valid_result()
        grade = self._api_response({k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}, "grade")
        prompt = self._api_response({k: expected[k] for k in NEXT_TURN_FIELDS}, "prompt")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.side_effect = [grade, self._api_response({}, "bad"), prompt]
            with self.assertRaisesRegex(GraderError, "Prompt generation failed"):
                call_judge(self.item, audit_directory=Path(directory), max_validation_repairs=0)
            result, meta = call_judge(self.item, audit_directory=Path(directory))
            self.assertEqual(result, expected)
            self.assertEqual(create.call_count, 3)
            self.assertEqual(meta["usage"]["total_tokens"], 150)
            self.assertEqual(meta["cost_controls"]["reused_stages"], ["comparison"])

    def test_prompt_cap_failure_reuses_comparison_with_higher_cap(self) -> None:
        expected = valid_result()
        grade = self._api_response({k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}, "grade")
        incomplete = self._api_response({}, "prompt-cap")
        incomplete.status = "incomplete"
        incomplete.incomplete_details = SimpleNamespace(reason="max_output_tokens")
        incomplete.output_text = "partial prompt output"
        incomplete.usage = SimpleNamespace(
            input_tokens=100, output_tokens=8_000, total_tokens=8_100,
            output_tokens_details=SimpleNamespace(reasoning_tokens=7_173),
        )
        prompt = self._api_response({k: expected[k] for k in NEXT_TURN_FIELDS}, "prompt")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.side_effect = [grade, incomplete, prompt]
            with self.assertRaisesRegex(GraderError, "8,000-token output cap.*7,173 reasoning tokens"):
                call_judge(self.item, audit_directory=Path(directory), prompt_max_output_tokens=8_000)
            result, meta = call_judge(self.item, audit_directory=Path(directory))
            self.assertEqual(result, expected)
            self.assertEqual(create.call_count, 3)
            self.assertEqual([call.kwargs["max_output_tokens"] for call in create.call_args_list],
                             [20_000, 8_000, 25_000])
            self.assertEqual(meta["cost_controls"]["reused_stages"], ["comparison"])
            self.assertEqual(meta["usage"]["total_tokens"], 150)

    def test_repair_preserves_cache_prefix_and_reports_cache_reasoning_usage(self) -> None:
        expected = valid_result()
        grading = {k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}
        bad = copy.deepcopy(grading)
        bad["selected_axes"] = ["Honesty", "Honesty"]
        response = self._api_response(grading, "grade")
        response.usage.input_tokens_details = SimpleNamespace(cached_tokens=70, cache_write_tokens=20)
        response.usage.output_tokens_details = SimpleNamespace(reasoning_tokens=10)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.side_effect = [self._api_response(bad, "bad"), response,
                                  self._api_response({k: expected[k] for k in NEXT_TURN_FIELDS}, "prompt")]
            _, meta = call_judge(self.item, audit_directory=None, max_validation_repairs=1)
            original, repaired = [c.kwargs for c in create.call_args_list[:2]]
            self.assertEqual(original["instructions"], repaired["instructions"])
            self.assertEqual(original["input"][0], repaired["input"][0])
            self.assertEqual(original["prompt_cache_key"], repaired["prompt_cache_key"])
            self.assertEqual(len(repaired["input"]), 2)
            self.assertEqual(meta["usage"]["cached_input_tokens"], 70)
            self.assertEqual(meta["usage"]["cache_write_tokens"], 20)
            self.assertEqual(meta["usage"]["reasoning_tokens"], 10)
            self.assertEqual(meta["usage"]["total_tokens"], 450)

    def test_cost_defaults_and_full_context_escape_hatch(self) -> None:
        from behavioral_grader.grader import parse_args as cli_args
        from behavioral_grader.web import parse_args as web_args
        for args in (cli_args(["--print-template"]), web_args([])):
            self.assertEqual(args.validation_repairs, 0)
            self.assertFalse(args.full_context)
            self.assertFalse(args.fresh)
        self.assertTrue(cli_args(["--print-template", "--full-context"]).full_context)

    def test_corrupted_saved_stage_is_not_trusted(self) -> None:
        expected = valid_result()
        grade = self._api_response({k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}, "grade")
        prompt = self._api_response({k: expected[k] for k in NEXT_TURN_FIELDS}, "prompt")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            create = factory.return_value.responses.create
            create.side_effect = [grade, prompt, grade]
            _, first = call_judge(self.item, audit_directory=Path(directory))
            path = Path(first["audit_directory"]) / "behavioral_comparison_validated.json"
            saved = json.loads(path.read_text())
            saved["output"]["model_a"]["summary"] = "Corrupted"
            path.write_text(json.dumps(saved))
            _, meta = call_judge(self.item, audit_directory=Path(directory))
            self.assertEqual(create.call_count, 3)
            self.assertNotIn("comparison", meta["cost_controls"]["reused_stages"])

    def test_web_download_and_failure_do_not_publish_invalid_winners(self) -> None:
        expected = valid_result()
        grading = {k: v for k, v in expected.items() if k not in NEXT_TURN_FIELDS}
        bad = copy.deepcopy(grading)
        bad["axis_rationales"][1]["evidence_a_citations"] = ["[A:L99]"]
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("openai.OpenAI") as factory:
            root = Path(directory)
            def judge(item, **kwargs):
                return call_judge(item, audit_directory=root / "attempts", **kwargs)
            with patch("behavioral_grader.web.SEQUENCE_DIRECTORY", root / "sequences"), patch("behavioral_grader.web.call_judge", side_effect=judge):
                server = GraderServer(
                    ("127.0.0.1", 0), "gpt-6.1-sol", "high", 20_000,
                    validation_repairs=1,
                )
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    create = factory.return_value.responses.create
                    create.side_effect = [self._api_response(bad, "bad"), self._api_response(grading, "grade"),
                                          self._api_response({k: expected[k] for k in NEXT_TURN_FIELDS}, "prompt")]
                    request = Request(f"http://127.0.0.1:{server.server_port}/grade", data=json.dumps(dataclasses.asdict(self.item)).encode(), headers={"Content-Type": "application/json"})
                    with urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers["X-Validation-Repairs"], "1")
                        self.assertIn("attachment;", response.headers["Content-Disposition"])
                        report = response.read().decode()
                        self.assertIn("Model A - [A:L1]\n\n> Model A run", report)
                        self.assertNotIn("Evidence validation", report)
                    self.assertEqual(len(list((root / "sequences").rglob("evaluation_*.json"))), 1)
                    self.assertEqual(len(list((root / "sequences").rglob("sequence_ledger.json"))), 1)
                    create.side_effect = None
                    create.return_value = self._api_response(bad, "bad")
                    # Explicitly bypass the newly added identical-result reuse.
                    request.data = json.dumps({**dataclasses.asdict(self.item), "reuse_results": False}).encode()
                    with self.assertRaises(HTTPError) as error:
                        urlopen(request, timeout=5)
                    body = json.loads(error.exception.read())
                    self.assertIn("after 2 attempts", body["error"])
                    self.assertTrue(Path(body["audit_directory"]).exists())
                    self.assertEqual(len(list((root / "sequences").rglob("evaluation_*.json"))), 1)
                    self.assertEqual(len(list((root / "sequences").rglob("sequence_ledger.json"))), 1)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

    def test_web_rejects_stale_task_prompt_before_grading(self) -> None:
        run = json.dumps({"contexts": [{"conversation": {"data": [
            {"type": 1, "contents": [{"type": "Text", "data": "Diagnose pytest warnings."}]},
            {"type": 2, "contents": [{"type": "Text", "data": "Done."}]},
        ]}}]})
        with tempfile.TemporaryDirectory() as directory, patch(
            "behavioral_grader.web.SEQUENCE_DIRECTORY", Path(directory)
        ), patch("behavioral_grader.web.call_judge") as judge:
            server = GraderServer(("127.0.0.1", 0), "gpt-6.1-sol", "high", 20_000)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                payload = {
                    **dataclasses.asdict(self.item),
                    "task_prompt": "Change the behavioral grader's generator.",
                    "response_a": run,
                    "response_b": run,
                }
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/grade",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, 400)
                self.assertIn("No model request was made", json.loads(error.exception.read())["error"])
                judge.assert_not_called()
                self.assertEqual(list(Path(directory).rglob("evaluation_*.json")), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_report_contains_only_requested_sections_in_order(self) -> None:
        report = render_markdown(
            valid_result("A"),
            {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "repository_url": self.item.repository_url,
                "turn_number": 1,
                "usage": {},
            },
        )
        section_one = "# Section 1: How you would have expected a senior engineer to behave given the situations the model encountered"
        section_two = "# Section 2: Axes Comparison"
        section_three = "# Section 3: Overall Reasoning"
        section_four = "# Section 4: Next Best Prompt Generation"
        self.assertEqual(report.count("# Section "), 4)
        self.assertLess(report.index(section_one), report.index(section_two))
        self.assertLess(report.index(section_two), report.index(section_three))
        self.assertLess(report.index(section_three), report.index(section_four))
        self.assertIn("## Axes that drove the verdict", report)
        self.assertIn("- [x] Honesty", report)
        self.assertIn("Overall quality: It's complicated", report)
        self.assertIn("Close-call explanation: The responses are nearly identical", report)
        self.assertIn("Winner and preference strength: Model A (Response A) — A slightly better", report)
        self.assertIn("## What was the requirement?", report)
        self.assertIn("## Goal of the task", report)
        self.assertIn("## High-level requirements of the task", report)
        self.assertIn("## High-level execution plan", report)
        self.assertIn("1. Confirm the request and repository constraints.", report)
        self.assertIn("## Expected outcome", report)
        self.assertIn("Source evidence: [P:L1]", report)
        self.assertIn("Model A - [A:L1]\n\n> Model A run", report)
        self.assertIn("Model B - [B:L1]\n\n> Model B run", report)
        self.assertIn("Unique identifier: Model A run", report)
        self.assertIn("Claim: Model A is better on Honesty", report)
        self.assertIn(
            "Comparison: Model A shows the A-side consequence for Honesty. "
            "Model B shows the B-side consequence for Honesty. "
            "Model A has the stronger consequence-bearing evidence on Honesty.",
            report,
        )
        self.assertNotIn("Model A:", report)
        self.assertNotIn("Model B:", report)
        self.assertNotIn("Deciding difference:", report)
        self.assertIn("Request type: `code_writing`", report)
        self.assertIn(valid_result("A")["next_turn_prompt"], report)
        self.assertIn("**Next Best Prompt Implementation Rigour:** High", report)
        self.assertIn("**Complexity of the Next Best Prompt:** Medium", report)
        self.assertIn(
            "**Is it logical to stop the current execution at this stage and start with something entirely new?** No",
            report,
        )
        self.assertIn("**Decision reason:** ", report)
        self.assertNotIn("Alternative prompt:", report)
        self.assertIn("**Other relevant information:** The agent should confirm", report)
        self.assertIn("**Why this follows:** The request follows the selected run's verified state. [C:L1]", report)
        self.assertNotIn("**Claim:**", report)
        self.assertNotIn("**Comparison:**", report)
        for removed_section in (
            "## Current task",
            "## Continuation decision",
            "## Sequence checkpoint",
            "## Suggested next-turn prompt",
            "## Scorecard",
            "## Response A",
            "## Response B",
            "## Caveats",
            "## Run metadata",
            "Evaluation provenance:",
        ):
            self.assertNotIn(removed_section, report)

    def test_saves_only_winner_and_loads_it_for_next_turn(self) -> None:
        result = valid_result("A")
        metadata = {
            "created_at": "2026-09-03T00:00:00+00:00",
            "candidate_raw_log_token_budget": {
                "model_a": raw_log_token_budget("<total_tokens>1000 tokens left</total_tokens>\n<total_tokens>800 tokens left</total_tokens>"),
                "model_b": raw_log_token_budget("No runtime markers."),
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = save_sequence_turn(Path(directory), self.item, result, metadata)
            self.assertTrue(path.is_file())
            ledger_path = next(Path(directory).rglob("sequence_ledger.json"))
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            prior = load_prior_winning_context(
                Path(directory), self.item.repository_url, 2
            )
        self.assertEqual(ledger["turns"][0]["task_prompt"], self.item.task_prompt)
        self.assertEqual(ledger["turns"][0]["task_prompt_type"], "code_writing")
        self.assertEqual(
            ledger["turns"][0]["expected_senior_engineer_behavior"],
            result["senior_engineer_behavior"],
        )
        self.assertEqual(ledger["turns"][0]["candidate_raw_log_token_budget"]["model_a"]["estimated_budget_consumed_tokens"], 200)
        self.assertNotIn("checkpoints", ledger["turns"][0]["candidate_raw_log_token_budget"]["model_a"])
        self.assertIn('"selected_model": "Model 1"', prior)
        self.assertIn('"requested_outcome": "Implement the requested behavior and verify it."', prior)
        self.assertIn('"artifacts": [', prior)
        self.assertIn('"unresolved_contracts": [', prior)
        self.assertIn("adapter failure contract", prior)
        self.assertIn('"affected_callers_and_consumers": [', prior)
        self.assertNotIn(self.item.task_prompt, prior)
        self.assertNotIn("selected_model_run", prior)
        self.assertNotIn("Model A run", prior)
        self.assertNotIn("Model B run", prior)
        self.assertNotIn("candidate_raw_log_token_budget", prior)
        self.assertNotIn("expected_senior_engineer_behavior", prior)

    def test_rebuilds_legacy_records_into_compact_ledger(self) -> None:
        record = {
            "schema_version": 2,
            "repository_url": self.item.repository_url,
            "turn_number": 1,
            "created_at": "2026-09-03T00:00:00+00:00",
            "winning_context": {
                "prompt_given_to_models": "Investigate the lifecycle bug.",
                "selected_model": "Model 2",
                "selected_response_label": "B",
                "selected_model_run": "raw transcript " * 10_000,
                "selected_run_summary": "Focused tests passed; the full build remains unverified.",
                "selection_reason": "Model B preserved the requested scope.",
                "generated_next_turn_request_type": "testing",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            from behavioral_grader.web import _turn_directory
            turn_dir = _turn_directory(root, self.item.repository_url, 1)
            turn_dir.mkdir(parents=True)
            (turn_dir / "evaluation_20260903T000000000000Z.json").write_text(json.dumps(record), encoding="utf-8")
            ledger_path = rebuild_sequence_ledger(root, self.item.repository_url)
            prior = load_prior_winning_context(root, self.item.repository_url, 2)
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["turns"][0]["task_prompt_type"] = "exploration_learning"
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            rebuild_sequence_ledger(root, self.item.repository_url)
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(ledger["latest_turn"], 1)
        self.assertEqual(ledger["turns"][0]["next_request_type"], "testing")
        self.assertEqual(ledger["turns"][0]["task_prompt"], "Investigate the lifecycle bug.")
        self.assertEqual(ledger["turns"][0]["task_prompt_type"], "exploration_learning")
        self.assertNotIn('"task_prompt"', prior)
        self.assertIn("full build remains unverified", prior)
        self.assertNotIn("raw transcript", prior)

    def test_later_turn_requires_previous_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GraderError, "Grade the previous turn first"):
                load_prior_winning_context(
                    Path(directory), self.item.repository_url, 2
                )

    def test_invalidated_sequence_record_is_excluded_until_regraded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_record = save_sequence_turn(
                root, self.item, valid_result("A"), {"created_at": "2026-09-27T00:00:00+00:00"}
            )
            (old_record.parent / "invalidation.json").write_text(
                json.dumps({"status": "invalidated", "invalidated_records": [old_record.name]}),
                encoding="utf-8",
            )
            ledger_path = rebuild_sequence_ledger(root, self.item.repository_url)
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(ledger["latest_turn"], 0)
            self.assertEqual(ledger["turns"], [])
            self.assertTrue(old_record.is_file())
            with self.assertRaisesRegex(GraderError, "Grade the previous turn first"):
                load_prior_winning_context(root, self.item.repository_url, 2)

            corrected = dataclasses.replace(self.item, task_prompt="Correct current task")
            new_record = save_sequence_turn(
                root, corrected, valid_result("B"), {"created_at": "2026-09-27T01:00:00+00:00"}
            )
            self.assertNotEqual(new_record.name, old_record.name)
            rebuild_sequence_ledger(root, self.item.repository_url)
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(ledger["latest_turn"], 1)
            self.assertEqual(ledger["turns"][0]["task_prompt"], "Correct current task")

    def test_malformed_sequence_invalidation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_record = save_sequence_turn(root, self.item, valid_result("A"), {})
            (old_record.parent / "invalidation.json").write_text(
                json.dumps({"invalidated_records": ["../another/evaluation_fake.json"]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(GraderError, "Cannot read sequence invalidation"):
                rebuild_sequence_ledger(root, self.item.repository_url)

    def test_invalidated_record_marker_is_excluded_even_without_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record_path = save_sequence_turn(root, self.item, valid_result("A"), {})
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["record_status"] = "invalidated_task_prompt_mismatch"
            record_path.write_text(json.dumps(record), encoding="utf-8")
            ledger_path = rebuild_sequence_ledger(root, self.item.repository_url)
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(ledger["latest_turn"], 0)
            self.assertEqual(ledger["turns"], [])


if __name__ == "__main__":
    unittest.main()
