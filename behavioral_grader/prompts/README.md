# Behavioral grader prompts

These Markdown files are the runtime instructions for transcript comparison and
repository-aware next-turn generation.

- `behavioral_axes.md` defines the seven behavioral dimensions.
- `behavioral_rubric.md` defines the forced-choice rating policy.
- `behavioral_comparison.md` drives the Sol-high comparison stage and the
  structured senior-engineer baseline.
- `next_turn_generation.md` drives the Sol-xhigh next-prompt stage and its
  evidence-first 90/10 depth policy.
- `next_turn_workload_profiles.md` defines classification-specific standards
  for connected long-horizon work without artificial padding.
- `next_turn_request_types.md` defines the shared request-type taxonomy.

`behavioral_grader.prompt_loader` expands standalone
`{{include:filename.md}}` directives within this directory. The long-horizon
archived pipeline owns separate trajectory and heuristic prompts in
`repository_kickoff/legacy_long_horizon/prompts/`.
