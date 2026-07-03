"""Example wiring for an OpenAI-compatible chat completion client.

Install and configure the client separately, then adapt base_url/model/env vars
for your deployment. This file is intentionally not required by the core agent.
"""

import os
from pathlib import Path
import sys

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import EmotionalSupportAgent

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ.get("OPENAI_BASE_URL"),
)
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")


def call_llm(prompt: str) -> str:
    completion = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return completion.choices[0].message.content or ""


agent = EmotionalSupportAgent(skill_bank_dir=ROOT / "skill_bank", llm=call_llm)
decision = agent.respond([
    {
        "role": "user",
        "content": "\u6211\u771f\u7684\u6709\u70b9\u6491\u4e0d\u4f4f\u4e86\uff0c\u611f\u89c9\u6ca1\u4eba\u61c2\u6211\uff0c\u660e\u660e\u6211\u5df2\u7ecf\u5f88\u52aa\u529b\u4e86\u3002",
    }
])

print(decision.response)
