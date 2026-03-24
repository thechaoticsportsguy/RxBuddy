"""
Pipeline Step 8 — Response Cleaner.

Cleans and formats the final response before returning to the user.

Rules:
  - Max 2 sentence explanation
  - Remove generic filler warnings
  - Remove unrelated drugs
  - Limit bullets to 2-3
  - No markdown (no **, no ##, no -)
  - No fluff phrases
"""
from __future__ import annotations

import re

from pipeline.claude_explainer import Explanation


# ── Fluff phrases to remove ──────────────────────────────────────────────────
_FLUFF = (
    "it is important to note that",
    "please note that",
    "it should be noted that",
    "it's worth mentioning that",
    "as always,",
    "as with any medication,",
    "generally speaking,",
    "in general,",
    "however, it is important",
    "that being said,",
    "with that said,",
    "it goes without saying",
    "needless to say,",
    "of course,",
    "naturally,",
)

# Generic warnings that add no value (vague filler)
_GENERIC_WARNINGS = (
    "always consult your doctor before taking any medication",
    "consult your healthcare provider",
    "talk to your doctor before starting",
    "this is not medical advice",
    "results may vary",
    "individual responses may differ",
    "everyone is different",
    "your mileage may vary",
)


def clean_response(explanation: Explanation) -> Explanation:
    """
    Clean the final explanation for UI display.

    Operations:
      1. Strip markdown formatting (**, ##, bullet markers)
      2. Remove fluff phrases
      3. Remove generic filler warnings
      4. Limit answer to 2 sentences
      5. Limit details to 3 items
      6. Limit action to 3 items
      7. Ensure sentences end with periods
    """
    # Clean the main answer
    explanation.answer = _clean_text(explanation.answer, max_sentences=2)
    explanation.article = _clean_text(explanation.article, max_sentences=3)
    explanation.warning = _clean_text(explanation.warning, max_sentences=1)

    # Clean and limit bullet lists
    explanation.details = [_clean_text(d, max_sentences=1) for d in explanation.details[:3] if d.strip()]
    explanation.action = [_clean_text(a, max_sentences=1) for a in explanation.action[:3] if a.strip()]

    # Remove empty items
    explanation.details = [d for d in explanation.details if d]
    explanation.action = [a for a in explanation.action if a]

    return explanation


def _clean_text(text: str, max_sentences: int = 2) -> str:
    """Clean a text string: strip markdown, fluff, and limit length."""
    if not text:
        return ""

    t = text.strip()

    # 1. Strip markdown
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)   # **bold** → bold
    t = re.sub(r"\*(.+?)\*", r"\1", t)         # *italic* → italic
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)  # ## headers
    t = re.sub(r"^[\-\*\•]\s+", "", t, flags=re.MULTILINE)  # bullet markers
    t = re.sub(r"^\d+\.\s+", "", t, flags=re.MULTILINE)     # numbered lists

    # 2. Remove fluff phrases
    for fluff in _FLUFF:
        t = re.sub(re.escape(fluff), "", t, flags=re.IGNORECASE)

    # 3. Remove generic warnings
    for gw in _GENERIC_WARNINGS:
        t = re.sub(re.escape(gw) + r"\.?", "", t, flags=re.IGNORECASE)

    # 4. Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()

    # 5. Limit to max_sentences
    sentences = re.split(r"(?<=[.!?])\s+", t)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) > max_sentences:
        sentences = sentences[:max_sentences]
    t = " ".join(sentences)

    # 6. Ensure ends with period
    if t and not re.search(r"[.!?]$", t):
        t += "."

    return t
