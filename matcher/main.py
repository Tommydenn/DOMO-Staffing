"""Matcher CLI entry point.

Modes:
  python -m matcher.main dryrun     # builds the crosswalk locally, prints stats, no upload
  python -m matcher.main run        # builds and uploads to DATASET_CROSSWALK
  python -m matcher.main report     # writes the review queue to a CSV under matcher/output/
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from . import domo_io, llm_tiebreak, matcher

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def _load_workers_and_employees(client: domo_io.DomoClient):
    adp_rows = domo_io.fetch_adp_workers(
        client, os.environ["DATASET_ADP_PUNCHES"], days=90
    )
    em_rows = domo_io.fetch_em_employees(client, os.environ["DATASET_EM_EMPLOYEES"])
    adp_workers = [matcher.AdpWorker(**r) for r in adp_rows if r.get("adp_associate_id")]
    em_employees = [matcher.EmEmployee(**r) for r in em_rows if r.get("em_employee_id")]
    return adp_workers, em_employees


def _resolve_pending_with_llm(results: list[matcher.MatchResult]) -> None:
    """Mutates results in place: anything tagged llm-pending or ambiguous-exact gets a tiebreak."""
    pending = [r for r in results if r.match_source in ("llm-pending", "ambiguous-exact")]
    if not pending:
        return
    print(f"  [tier 3] sending {len(pending)} ambiguous candidates to Claude for tiebreak...", file=sys.stderr)
    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    for r in pending:
        cand_dicts = [
            {"em_employee_id": cid, "em_name": cname, "score": cscore}
            for cid, cname, cscore in r.candidates
        ]
        try:
            decision = llm_tiebreak.decide(
                adp_name=r.adp_name,
                adp_title=None,
                adp_community=None,
                candidates=cand_dicts,
                client=client,
            )
        except Exception as exc:
            r.notes = f"llm-error: {exc}"[:240]
            continue
        if decision.em_employee_id and decision.confidence >= 0.75:
            chosen = next((c for c in r.candidates if c[0] == decision.em_employee_id), None)
            if chosen:
                r.em_employee_id = chosen[0]
                r.em_name = chosen[1]
                r.confidence = decision.confidence
                r.match_source = "llm"
                r.notes = decision.rationale
        else:
            r.match_source = "manual-review"
            r.notes = decision.rationale or "llm declined to pick"


def _build_crosswalk_rows(results: list[matcher.MatchResult]) -> tuple[list[dict], list[str]]:
    today = date.today().isoformat()
    columns = [
        "unified_employee_id",
        "adp_associate_id",
        "adp_name",
        "em_employee_id",
        "em_name",
        "confidence",
        "match_source",
        "verified_by",
        "last_verified",
        "notes",
    ]
    rows: list[dict] = []
    for r in results:
        unified = r.em_employee_id if r.em_employee_id else f"ADP:{r.adp_associate_id}"
        rows.append({
            "unified_employee_id": unified,
            "adp_associate_id": r.adp_associate_id,
            "adp_name": r.adp_name,
            "em_employee_id": r.em_employee_id or "",
            "em_name": r.em_name or "",
            "confidence": f"{r.confidence:.3f}",
            "match_source": r.match_source,
            "verified_by": "",
            "last_verified": today,
            "notes": r.notes,
        })
    return rows, columns


def _print_stats(results: list[matcher.MatchResult]) -> None:
    by_source = Counter(r.match_source for r in results)
    total = len(results)
    print(f"\n=== Match results ({total} ADP workers) ===")
    for source, n in sorted(by_source.items(), key=lambda t: -t[1]):
        print(f"  {source:20s} {n:5d}  ({100 * n / total:5.1f}%)")
    matched = sum(1 for r in results if r.em_employee_id)
    print(f"\n  matched           {matched:5d}  ({100 * matched / total:5.1f}%)")
    print(f"  unmatched/manual  {total - matched:5d}  ({100 * (total - matched) / total:5.1f}%)")


def cmd_dryrun(args: argparse.Namespace) -> int:
    client = domo_io.DomoClient.from_env()
    print("Loading ADP workers + EM employees...", file=sys.stderr)
    adp_workers, em_employees = _load_workers_and_employees(client)
    print(f"  ADP: {len(adp_workers)}   EM: {len(em_employees)}", file=sys.stderr)

    print("Running tiers 1-2 (exact + fuzzy)...", file=sys.stderr)
    results = matcher.run_tiered_match(
        adp_workers, em_employees,
        fuzzy_threshold=int(os.environ.get("TIER2_FUZZY_THRESHOLD", "88")),
        llm_lower_bound=int(os.environ.get("TIER3_LLM_LOWER_BOUND", "75")),
    )
    if args.with_llm:
        _resolve_pending_with_llm(results)
    _print_stats(results)

    rows, columns = _build_crosswalk_rows(results)
    out_path = OUTPUT_DIR / f"crosswalk_dryrun_{date.today().isoformat()}.csv"
    pd.DataFrame(rows, columns=columns).to_csv(out_path, index=False)
    print(f"\nDry-run crosswalk written to {out_path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    client = domo_io.DomoClient.from_env()
    cw_id = os.environ.get("DATASET_CROSSWALK", "").strip()
    if not cw_id:
        print(
            "DATASET_CROSSWALK is empty. Upload the seed CSV first, then paste the new "
            "dataset ID into matcher/.env (and as a GitHub Actions secret).",
            file=sys.stderr,
        )
        return 2
    adp_workers, em_employees = _load_workers_and_employees(client)
    results = matcher.run_tiered_match(
        adp_workers, em_employees,
        fuzzy_threshold=int(os.environ.get("TIER2_FUZZY_THRESHOLD", "88")),
        llm_lower_bound=int(os.environ.get("TIER3_LLM_LOWER_BOUND", "75")),
    )
    _resolve_pending_with_llm(results)
    _print_stats(results)
    rows, columns = _build_crosswalk_rows(results)
    print(f"Uploading {len(rows)} rows to dataset {cw_id}...", file=sys.stderr)
    client.replace_dataset_csv(cw_id, rows, columns)
    print("Done.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    client = domo_io.DomoClient.from_env()
    adp_workers, em_employees = _load_workers_and_employees(client)
    results = matcher.run_tiered_match(adp_workers, em_employees)
    review = [r for r in results if r.match_source in ("llm-pending", "ambiguous-exact", "unmatched", "manual-review")]
    rows = []
    for r in review:
        rows.append({
            "adp_associate_id": r.adp_associate_id,
            "adp_name": r.adp_name,
            "top_candidates": "; ".join(f"{c[1]} ({c[2]:.2f})" for c in r.candidates[:3]),
            "match_source": r.match_source,
            "notes": r.notes,
        })
    out_path = OUTPUT_DIR / f"review_queue_{date.today().isoformat()}.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"Wrote {len(rows)} review-queue rows to {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(__file__).parent / ".env")
    parser = argparse.ArgumentParser(prog="matcher")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dry = sub.add_parser("dryrun", help="match without uploading")
    p_dry.add_argument("--with-llm", action="store_true", help="also run tier 3 LLM tiebreak")
    p_dry.set_defaults(func=cmd_dryrun)

    p_run = sub.add_parser("run", help="match and upload to DATASET_CROSSWALK")
    p_run.set_defaults(func=cmd_run)

    p_rep = sub.add_parser("report", help="write the review queue CSV")
    p_rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
