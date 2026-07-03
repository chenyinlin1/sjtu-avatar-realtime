# Skill Bank Emotional Support Agent Package

This folder is a minimal handoff package for building an emotional-support agent that uses the SAGE skill bank as prompt context.

It intentionally excludes online self-evolution code: no rollout runner, no trajectory memory update, no skill distillation, no gate, and no promoted-skill workflow.

## Contents

```text
skill_bank/
  manifest.json
  deep_empathy/SKILL.md
  analyze_other_motives/SKILL.md
  action_plan/SKILL.md
  validation/SKILL.md
  self_reflection/SKILL.md
  praise_specific_behavior/SKILL.md
  vent_listening/SKILL.md
  balanced_analysis/SKILL.md

agent/
  skill_bank.py      # load manifest and selected SKILL.md
  hidden_need.py     # lightweight local hidden-need inference from visible dialogue
  prompting.py       # build the final LLM prompt
  agent.py           # EmotionalSupportAgent wrapper

examples/
  demo_build_prompt.py
  demo_with_openai_style_client.py
```

## Runtime Mechanism

Each response turn follows this flow:

```text
visible dialogue history
  -> sanitize_history()
  -> infer_hidden_need()
  -> skill_bank.get_entry(inferred_hidden_need)
  -> read selected SKILL.md
  -> build prompt with selected skill + recent visible history
  -> call one LLM API to generate the NPC reply
```

The skill bank is not a tool-call system. The selected `SKILL.md` is added to the LLM prompt as behavioral guidance.

## Minimal Usage

```python
from agent import EmotionalSupportAgent

agent = EmotionalSupportAgent(skill_bank_dir="skill_bank", llm=my_llm_function)

decision = agent.respond([
    {"role": "user", "content": "?????????????????"}
])

print(decision.response)
```

`my_llm_function(prompt: str) -> str` can wrap any chat-completion API.

## Prompt-Only Demo

To inspect what will be sent to the LLM:

```bash
python examples/demo_build_prompt.py
```

## What To Modify

- Replace `agent/hidden_need.py` with a stronger classifier if needed.
- Replace or extend `examples/demo_with_openai_style_client.py` for the target LLM provider.
- Keep `skill_bank/manifest.json` and the `SKILL.md` paths together; the loader relies on the manifest.

## What Is Not Included

The following online self-evolution pieces are deliberately omitted:

- rollout/test runners
- runtime batch data
- trajectory memory read/write
- skill distillation
- gate evaluation
- skill promotion
