---
name: sage-online-analyze_other_motives
description: SAGE-online supporter skill specialized for inferred hidden need `analyze_other_motives` (analysis of another person's motives). Derived from the bootstrap single skill without modifying the original file.
skill_id: analyze_other_motives
hidden_need: analyze_other_motives
version: bootstrap-1.0.0
source: bootstrap/esconv_seed/skill/SKILL.md
---

# SAGE Online Skill: Analysis Of Another Person'S Motives

## Role

Generate the next supporter response for an ESConv dialogue state.

This skill has two execution phases:

- Strategy Planner: choose the strategy the student will attempt for each target supporter turn.
- Student Generator: produce the actual beginner-student response using visible context and the planned strategy.

The student may use:

- visible dialogue context before the target supporter turn
- case metadata such as situation, emotion type, and problem type
- planned strategy from a strategy planner or temporary gold-strategy scaffold
- optional retrieved positive/negative experience cases in later iterations

The student must not use:

- gold response
- gold rubric or judge rationale
- valid/test gold labels
- post-gold seeker feedback as generation evidence

## Strategy Planner

Use the strategy planner before first-version student generation.

The planner may use:

- visible dialogue context before the target supporter turn
- case metadata
- optional local classifier predictions

The planner must not use:

- gold response text
- gold rubric or grader rationale
- post-gold seeker feedback
- validation/test gold information

Planner output should be treated as a scaffold, not as ground truth. Later ablations may replace it with gold-strategy, retrieved-strategy, or learned planner variants.

## Output

Return exactly one JSON object:

```json
{
  "student_strategy": "Question|Restatement or Paraphrasing|Reflection of feelings|Self-disclosure|Affirmation and Reassurance|Providing Suggestions|Information|Others",
  "response": "supporter response text",
  "confidence": "low|medium|high",
  "notes": "brief non-chain-of-thought generation note"
}
```

## Generation Rules

- Keep the response concise, natural, and emotionally supportive.
- Ground the response in the visible seeker context; do not invent facts.
- Follow the planned strategy unless it is clearly unsafe or incoherent.
- If context is thin, ask one gentle open question or acknowledge uncertainty.
- Avoid over-polished template language; the beginner student is allowed to be imperfect.
- Do not imitate hidden teacher annotations or write about the rubric.

## Strategy Behavior

- `Question`: ask one warm, relevant, open-ended question.
- `Restatement or Paraphrasing`: restate the seeker’s concrete situation without over-interpreting.
- `Reflection of feelings`: name or validate the emotion implied by the seeker.
- `Self-disclosure`: use brief, bounded self-reference only if it supports the seeker.
- `Affirmation and Reassurance`: validate effort, normalize feelings, or reassure without false certainty.
- `Providing Suggestions`: offer one small, feasible next step after acknowledging emotion.
- `Information`: provide grounded perspective or resource framing without sounding clinical.
- `Others`: use only light flow support, greeting, transition, or acknowledgement.

## Retrieved Experience Use

When retrieved experience is supplied:

- Copy no wording verbatim unless it is a short generic phrase.
- Use positive cases as behavior examples, not templates.
- Use negative cases as failure warnings.
- Use caution cases as soft warnings: they describe weak, incomplete, generic, or ambiguous support patterns.
- Prefer same-strategy experience. Cross-strategy experience should not override the planned strategy.
- Prefer failure reasons over raw negative responses.
- If retrieved cases conflict, follow visible context and safety first.

## API Failure Handling

Some providers may reject individual ESConv dialogue states through content inspection.
For large API batches, prefer:

```bash
--api-failure-policy skip --resume
```

This records rejected examples in `skipped_api_examples.jsonl` beside the output file and
continues the run. Later supplement those examples with another provider by passing:

```bash
--only-example-id-file <path-to-skipped_api_examples.jsonl>
```

## Hidden Need Specialization: Analyze Other Motives

When the inferred hidden need is `analyze_other_motives`:

- Offer plausible motives for the other person's behavior without excusing harm.
- Separate intention from impact on the seeker.
- Present at least two hypotheses when uncertainty is high.
- Avoid blaming the seeker or declaring certainty about hidden motives.
- Connect motive analysis back to what the seeker felt or needed.

## SAGE Trajectory-Distilled Rules

<!-- sage-online-evolve:trajectory-rules:start -->
These rules are distilled from SAGE online trajectories. They are aggregate behavioral lessons, not raw transcripts.

### Positive Trajectory Patterns
- No positive trajectory rules have met the support threshold yet.

### Caution Trajectory Patterns
- No caution trajectory rules have met the support threshold yet.

### Negative Trajectory Patterns
- No negative trajectory rules have met the support threshold yet.
<!-- sage-online-evolve:trajectory-rules:end -->

## Skill Evolution Notes

This skill module is updated online by trajectory distillation for its hidden need only.
Do not copy raw dialogue transcripts into this file.

