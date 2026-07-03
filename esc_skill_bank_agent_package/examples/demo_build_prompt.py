"""Demo: build the prompt that should be sent to your LLM API.

Run from this directory:
    python3 examples/demo_build_prompt.py
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import EmotionalSupportAgent

history = [
    {
        "role": "user",
        "content": "\u6211\u771f\u7684\u6709\u70b9\u6491\u4e0d\u4f4f\u4e86\uff0c\u611f\u89c9\u6ca1\u4eba\u61c2\u6211\uff0c\u660e\u660e\u6211\u5df2\u7ecf\u5f88\u52aa\u529b\u4e86\u3002",
    }
]

agent = EmotionalSupportAgent(skill_bank_dir=ROOT / "skill_bank")
prompt, inference, selected_skill = agent.build_prompt(history)

print("inferred_hidden_need:", inference.to_dict())
print("selected_skill:", selected_skill)
print("\n--- prompt preview ---\n")
print(prompt[:3000])
