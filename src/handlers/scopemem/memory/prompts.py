SESSION_EXTRACT_PROMPT = """You extract long-term memories from a two-speaker conversation session.
Return JSON with exactly one top-level key, "memories". Each memory must include:
speaker, text, kind, topic, time_anchor, confidence.
Use only the supplied conversation lines. Keep memories atomic, precise, and searchable."""

REFLECTION_PROMPT = """You consolidate existing memories for future retrieval.
Return JSON with exactly one top-level key, "memories". Each memory must include:
speaker, text, kind, topic, time_anchor, confidence.
Use only the supplied source memories. Preserve exact names, dates, counts, titles, places, and negations."""

ANSWER_PROMPT = """Answer the question using only the supplied memories.
Return JSON: {"answer": "...", "evidence": [1, 2]}.

Speaker 1: {{ speaker_1 }}
Speaker 1 memories:
{{ speaker_1_memories }}

Speaker 2: {{ speaker_2 }}
Speaker 2 memories:
{{ speaker_2_memories }}

Question: {{ question }}"""

REVIEW_PROMPT = """Review whether the draft answer is supported by the supplied memories.
If unsupported, return a corrected concise answer or N/A.
Return JSON: {"answer": "...", "reason": "..."}.

Question: {{ question }}
Draft answer: {{ draft_answer }}
Memories:
{{ memories }}"""

