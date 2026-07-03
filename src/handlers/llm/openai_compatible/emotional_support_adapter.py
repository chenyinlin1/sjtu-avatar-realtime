from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from loguru import logger


class EmotionalSupportSkillAdapter:
    """Prompt-context adapter for the ESC skill bank.

    This adapter does not call an LLM and does not produce the final reply. It
    only selects one emotional-support skill and returns a low-priority user
    context message for the main LLM turn.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        skill_bank_dir: str,
        max_chars: int = 1800,
        history_turns: int = 4,
    ) -> None:
        self.enabled = bool(enabled)
        self.max_chars = max(300, int(max_chars or 1800))
        self.history_messages = max(2, int(history_turns or 4) * 2)
        self.skill_bank = None
        self.infer_hidden_need = None
        if not self.enabled:
            return
        try:
            from esc_skill_bank_agent_package.agent import infer_hidden_need, load_skill_bank

            path = Path(skill_bank_dir or "esc_skill_bank_agent_package/skill_bank")
            if not path.is_absolute():
                path = Path.cwd() / path
            self.skill_bank = load_skill_bank(path)
            self.infer_hidden_need = infer_hidden_need
            logger.info(
                "Emotional support skill adapter enabled: "
                f"bank={path} version={getattr(self.skill_bank, 'version', 'unknown')}"
            )
        except Exception as exc:
            self.enabled = False
            self.skill_bank = None
            self.infer_hidden_need = None
            logger.warning(f"Emotional support skill adapter disabled: {exc}")

    def build_context_message(
        self,
        *,
        history: Iterable[Any],
        current_user_text: str,
    ) -> Optional[Dict[str, str]]:
        if not self.enabled or self.skill_bank is None or self.infer_hidden_need is None:
            return None
        visible_history = self._visible_history(history, current_user_text)
        if not visible_history:
            return None
        try:
            inference = self.infer_hidden_need(visible_history)
            entry = self.skill_bank.get_entry(inference.need)
            skill_text = self.skill_bank.load_skill_text(inference.need)
        except Exception as exc:
            logger.warning(f"Failed to build emotional support skill context: {exc}")
            return None
        logger.info(
            "Emotional support skill selected: "
            f"need={inference.need} confidence={inference.confidence} skill={entry.skill_id}"
        )
        return {
            "role": "user",
            "content": self._format_context(inference, entry, skill_text),
        }

    def _visible_history(self, history: Iterable[Any], current_user_text: str) -> List[Dict[str, str]]:
        turns: List[Dict[str, str]] = []
        for message in list(history or [])[-self.history_messages:]:
            role = getattr(message, "role", "")
            content = str(getattr(message, "content", "") or "").strip()
            if not content:
                continue
            turns.append({
                "role": "assistant" if role == "avatar" else "user",
                "content": content,
            })
        current_user_text = str(current_user_text or "").strip()
        if current_user_text:
            turns.append({"role": "user", "content": current_user_text})
        return turns

    def _format_context(self, inference: Any, entry: Any, skill_text: str) -> str:
        clipped_skill = (skill_text or "")[: self.max_chars]
        return "\n".join([
            "以下是本轮回复的情感支持参考。它只用于帮助选择共情和回应策略。",
            "必须服从最高优先级的小伴人设、口播长度和安全要求；不要提及 skill、hidden_need、SAGE、内部判断或本段参考。",
            f"本轮推断的支持需求：{getattr(inference, 'need', 'unknown')}，置信度：{getattr(inference, 'confidence', 'unknown')}。",
            f"选中策略：{getattr(entry, 'skill_id', 'unknown')}。请吸收其原则，先承接用户最近的具体内容，再温柔推进一小步。",
            "",
            "选中策略文本：",
            "```markdown",
            clipped_skill,
            "```",
        ])
