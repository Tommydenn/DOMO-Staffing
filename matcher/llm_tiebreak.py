"""Tier 3: ask Claude to choose among ambiguous EM candidates for an ADP worker.

Now passes YTD activity (ADP hours and per-candidate Eldermark services) so the
model can disambiguate identical-name collisions by who's actually active.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from anthropic import Anthropic


@dataclass(frozen=True)
class TiebreakDecision:
    em_employee_id: str | None  # None = "no confident match"
    confidence: float  # 0..1 — model's reported confidence
    rationale: str


_SYSTEM = (
    "You resolve identity matches between ADP payroll and Eldermark service records "
    "for a senior-living operator. Names may differ by typos, nicknames, hyphenation, "
    "or accents. When two Eldermark candidates have identical names, prefer the one "
    "with non-zero YTD service activity over inactive duplicates. Prefer high precision "
    "over recall: if uncertain, return null. Never invent IDs that aren't in the candidate list."
)


def _build_user_message(adp_name: str, adp_title: str | None, adp_community: str | None,
                        adp_ytd_hours: float, candidates: list[dict]) -> str:
    payload = {
        "adp": {
            "name": adp_name,
            "title": adp_title,
            "community": adp_community,
            "ytd_hours": round(float(adp_ytd_hours or 0.0), 1),
        },
        "candidates": candidates,
        "instruction": (
            "Pick the candidate em_employee_id most likely to be the same person. "
            "When two candidates have identical names, prefer the one with higher ytd_services. "
            'Reply ONLY with strict JSON: {"em_employee_id": "..." or null, '
            '"confidence": 0.0-1.0, "rationale": "<one sentence>"}'
        ),
    }
    return json.dumps(payload)


def decide(
    *,
    adp_name: str,
    adp_title: str | None,
    adp_community: str | None,
    adp_ytd_hours: float = 0.0,
    candidates: list[dict],
    model: str | None = None,
    client: Anthropic | None = None,
) -> TiebreakDecision:
    """Ask Claude to pick the right candidate (or none).

    Each candidate dict: {em_employee_id, em_name, score, ytd_services}.
    """
    if client is None:
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = model or os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")

    msg = client.messages.create(
        model=model,
        max_tokens=300,
        system=_SYSTEM,
        messages=[{"role": "user", "content": _build_user_message(
            adp_name, adp_title, adp_community, adp_ytd_hours, candidates,
        )}],
    )
    text = "".join(block.text for block in msg.content if hasattr(block, "text")).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        if "```" in text:
            inner = text.split("```", 2)[1]
            if inner.startswith("json"):
                inner = inner[4:]
            data = json.loads(inner.strip())
        else:
            raise

    em_id = data.get("em_employee_id")
    conf = float(data.get("confidence", 0.0))
    rationale = str(data.get("rationale", ""))[:240]
    return TiebreakDecision(em_id, conf, rationale)
