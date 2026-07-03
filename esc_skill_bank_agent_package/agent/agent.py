"""Minimal emotional-support agent powered by a SAGE skill bank."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .hidden_need import HiddenNeedInference, infer_hidden_need, sanitize_history
from .prompting import build_supporter_prompt
from .skill_bank import SkillBank, load_skill_bank

LLMCallable = Callable[[str], str]


@dataclass(frozen=True)
class AgentDecision:
    response: str
    inferred_hidden_need: HiddenNeedInference
    selected_skill: Dict[str, Any]
    prompt: str


class EmotionalSupportAgent:
    """Select one skill from the bank, place it in the prompt, and call an LLM."""

    def __init__(self, *, skill_bank_dir: Path | str | None = None, llm: Optional[LLMCallable] = None) -> None:
        self.skill_bank: SkillBank = load_skill_bank(skill_bank_dir)
        self.llm = llm

    def build_prompt(self, history: Iterable[Dict[str, Any]]) -> tuple[str, HiddenNeedInference, Dict[str, Any]]:
        visible_history: List[Dict[str, str]] = sanitize_history(history)
        inference = infer_hidden_need(visible_history)
        entry = self.skill_bank.get_entry(inference.need)
        skill_text = self.skill_bank.load_skill_text(inference.need)
        selected_skill = entry.to_dict()
        prompt = build_supporter_prompt(
            inferred_hidden_need=inference.to_dict(),
            selected_skill=selected_skill,
            skill_text=skill_text,
            visible_history=visible_history,
        )
        return prompt, inference, selected_skill

    def respond(self, history: Iterable[Dict[str, Any]]) -> AgentDecision:
        prompt, inference, selected_skill = self.build_prompt(history)
        if self.llm is None:
            raise RuntimeError("No LLM callable was provided. Pass llm=lambda prompt: ... when constructing the agent.")
        response = self.llm(prompt).strip()
        return AgentDecision(
            response=response,
            inferred_hidden_need=inference,
            selected_skill=selected_skill,
            prompt=prompt,
        )
