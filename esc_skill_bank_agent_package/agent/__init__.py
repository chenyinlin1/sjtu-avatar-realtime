"""Minimal skill-bank emotional support agent package."""

from .agent import EmotionalSupportAgent
from .hidden_need import infer_hidden_need
from .skill_bank import SkillBank, load_skill_bank

__all__ = ["EmotionalSupportAgent", "SkillBank", "load_skill_bank", "infer_hidden_need"]
