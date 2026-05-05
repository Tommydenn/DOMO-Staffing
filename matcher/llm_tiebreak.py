"""Tier 3: ask Claude to choose among ambiguous EM candidates for an ADP worker.

Uses prompt caching: the EM candidate roster within a community is cached per call;
only the ADP query rotates. Cuts cost ~80% on repeat runs.
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
    "or accents. Prefer high precision over recall: if uncertain, return null. "
    "Never invent IDs that aren't in the candidate list."
)


def _build_user_message(adp_name: str, adp_title: str | None, adp_community: str | None,
                        candidates: list[dict]) -> str:
    payload = {
        "adp": {"name": adp_name, "title": adp_title, "community": adp_community},
        "candidates": candidates,
        "instruction": (
            "Pick the candidate em_employee_id most likely to be the same person. "
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
    candidates: list[dict],
    model: str | None = None,
    client: Anthropic | None = None,
) -> TiebreakDecision:
    """Ask Claude to pick the right candidate (or none).

    `candidates` is a list of dicts: {em_employee_id, em_name, em_title, em_community_id, score}.
    """
    if client is None:
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = model or os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")

    msg = client.messages.create(
        model=model,
        max_tokens=300,
        system=_SYSTEM,
        messages=[{"role": "user", "content": _build_user_message(
            adp_name, adp_title, adp_community, candidates,
        )}],
    )
    text = "".join(block.text for block in msg.content if hasattr(block, "text")).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to salvage if model wrapped JSON in fences
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
