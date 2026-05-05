"""Tiered matcher: ADP associates -> EM employees.

Tier 1: exact normalized name match (community-blocked).
Tier 2: fuzzy token-set match + nickname expansion.
Tier 3: LLM tiebreaker for ambiguous candidates.
Tier 4: human review queue (anything left).
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from rapidfuzz import fuzz, process

from . import nicknames

_PUNCT = re.compile(r"[^\w\s,]")
_MULTI_WS = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """'Bray-Richmond, Demya' -> 'bray richmond demya' for matching."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = _PUNCT.sub(" ", s)
    s = s.replace(",", " ")
    s = _MULTI_WS.sub(" ", s).strip()
    return s


def split_last_first(name: str) -> tuple[str, str]:
    """'Bray-Richmond, Demya' -> ('bray richmond', 'demya')."""
    n = normalize_name(name)
    if "," in (name or ""):
        last, _, rest = (name or "").partition(",")
        return normalize_name(last), normalize_name(rest)
    parts = n.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return n, ""


@dataclass
class AdpWorker:
    adp_associate_id: str
    adp_name: str
    adp_title: str | None
    adp_department: str | None
    adp_community: str | None
    ytd_hours: float = 0.0
    norm: str = ""
    last: str = ""
    first: str = ""

    def __post_init__(self) -> None:
        self.norm = normalize_name(self.adp_name)
        self.last, self.first = split_last_first(self.adp_name)


@dataclass
class EmEmployee:
    em_employee_id: str
    em_name: str
    em_first_name: str | None
    em_last_name: str | None
    em_title: str | None
    em_community_id: str | None
    em_inactive: str | None
    ytd_services: int = 0
    norm: str = ""
    last: str = ""
    first: str = ""

    def __post_init__(self) -> None:
        self.norm = normalize_name(self.em_name)
        if self.em_last_name and self.em_first_name:
            self.last = normalize_name(self.em_last_name)
            self.first = normalize_name(self.em_first_name)
        else:
            self.last, self.first = split_last_first(self.em_name)


@dataclass
class MatchResult:
    adp_associate_id: str
    adp_name: str
    em_employee_id: str | None
    em_name: str | None
    confidence: float  # 0..1
    match_source: str  # exact | fuzzy | llm | manual | unmatched
    # Each candidate dict: {em_employee_id, em_name, score, ytd_services}
    candidates: list[dict] = field(default_factory=list)
    notes: str = ""


def _first_name_compatible(adp: AdpWorker, em: EmEmployee) -> bool:
    if not adp.first or not em.first:
        return True
    if adp.first == em.first:
        return True
    return nicknames.names_could_match(adp.first.split()[0], em.first.split()[0])


def match_one(
    adp: AdpWorker,
    em_pool: list[EmEmployee],
    *,
    fuzzy_threshold: int,
    llm_lower_bound: int,
) -> MatchResult:
    if not em_pool:
        return MatchResult(adp.adp_associate_id, adp.adp_name, None, None, 0.0, "unmatched")

    # Tier 1: exact normalized match on full string
    exacts = [e for e in em_pool if e.norm == adp.norm]
    if len(exacts) == 1:
        e = exacts[0]
        return MatchResult(
            adp.adp_associate_id, adp.adp_name, e.em_employee_id, e.em_name, 1.0, "exact"
        )
    if len(exacts) > 1:
        # Exact-name collision: prefer the candidate with YTD service activity.
        # If exactly one is active YTD, auto-confirm. Otherwise push to LLM.
        active = [e for e in exacts if e.ytd_services > 0]
        if len(active) == 1:
            e = active[0]
            return MatchResult(
                adp.adp_associate_id, adp.adp_name, e.em_employee_id, e.em_name, 1.0, "exact",
                notes=f"exact-name collision broken by YTD activity ({e.ytd_services} svc)",
            )
        cands = [
            {"em_employee_id": e.em_employee_id, "em_name": e.em_name, "score": 1.0, "ytd_services": e.ytd_services}
            for e in exacts
        ]
        return MatchResult(
            adp.adp_associate_id,
            adp.adp_name,
            None,
            None,
            0.0,
            "ambiguous-exact",
            candidates=cands,
            notes="multiple exact matches in community block",
        )

    # Tier 2: fuzzy token-set ratio + nickname check on first name
    scored: list[tuple[float, EmEmployee]] = []
    for e in em_pool:
        if e.last and adp.last and e.last.split()[0] != adp.last.split()[0]:
            # Last names diverge in first token — still scored but with last-name penalty
            ls = fuzz.ratio(adp.last, e.last)
            if ls < 80:
                continue
        score = fuzz.token_set_ratio(adp.norm, e.norm)
        if _first_name_compatible(adp, e):
            score = max(score, fuzz.token_set_ratio(adp.last, e.last))
        else:
            score -= 8
        scored.append((float(score), e))

    # Sort: name score first, then YTD activity (active candidate wins ties)
    scored.sort(key=lambda t: (t[0], t[1].ytd_services), reverse=True)
    top = scored[:5]

    if top and top[0][0] >= fuzzy_threshold and (len(top) == 1 or top[0][0] - top[1][0] >= 6):
        s, e = top[0]
        return MatchResult(
            adp.adp_associate_id,
            adp.adp_name,
            e.em_employee_id,
            e.em_name,
            round(s / 100.0, 3),
            "fuzzy",
        )

    if top and top[0][0] >= llm_lower_bound:
        cands = [
            {"em_employee_id": e.em_employee_id, "em_name": e.em_name,
             "score": round(s / 100.0, 3), "ytd_services": e.ytd_services}
            for s, e in top
        ]
        return MatchResult(
            adp.adp_associate_id,
            adp.adp_name,
            None,
            None,
            round(top[0][0] / 100.0, 3),
            "llm-pending",
            candidates=cands,
        )

    return MatchResult(adp.adp_associate_id, adp.adp_name, None, None, 0.0, "unmatched")


def block_by_community(
    adp_workers: Iterable[AdpWorker],
    em_employees: Iterable[EmEmployee],
    community_alias: dict[str, set[str]] | None = None,
) -> tuple[dict[str | None, list[EmEmployee]], list[EmEmployee]]:
    """Group EM employees by community_id; also keep a fallback global pool."""
    by_community: dict[str | None, list[EmEmployee]] = defaultdict(list)
    for e in em_employees:
        by_community[e.em_community_id].append(e)
    global_pool = list(em_employees)
    return by_community, global_pool


def candidates_for_adp(
    adp: AdpWorker,
    by_community: dict[str | None, list[EmEmployee]],
    global_pool: list[EmEmployee],
    community_map: dict[str, str] | None = None,
) -> list[EmEmployee]:
    """Return EM candidates likely to match this ADP worker.

    Strategy: try ADP community -> EM community_id mapping if available; else fall back
    to the global pool.
    """
    if community_map and adp.adp_community:
        cid = community_map.get(adp.adp_community)
        if cid and by_community.get(cid):
            return list(by_community[cid])
    return list(global_pool)


def run_tiered_match(
    adp_workers: list[AdpWorker],
    em_employees: list[EmEmployee],
    *,
    community_map: dict[str, str] | None = None,
    fuzzy_threshold: int = 88,
    llm_lower_bound: int = 75,
) -> list[MatchResult]:
    by_community, global_pool = block_by_community(adp_workers, em_employees)
    results: list[MatchResult] = []
    for adp in adp_workers:
        pool = candidates_for_adp(adp, by_community, global_pool, community_map)
        # Always also try the full pool as a backup if the community pool is empty
        if not pool:
            pool = global_pool
        results.append(
            match_one(
                adp,
                pool,
                fuzzy_threshold=fuzzy_threshold,
                llm_lower_bound=llm_lower_bound,
            )
        )
    return results
