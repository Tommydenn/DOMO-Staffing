# Staffing App — Claude Context

> Session-memory file for this project. Read at the start of every session. Update at the end of meaningful work.

## What this project does

Two-part system for Great Lakes Management / HGSA staffing efficiency:

1. **DF205** Magic ETL dataflow in Domo — produces an hourly + employee-detail efficiency dataset that powers the dashboard and per-employee cards.
2. **ADP ↔ Eldermark crosswalk matcher** — Python service that maps payroll associates to clinical-system employees. Without this, services/labor can't be tied together at the employee level.

## Stack
- **Dashboard**: single-file static HTML app (`index.html`) — React 18 + ReactDOM + Babel Standalone + Tailwind, all via CDN. UI lib: `lucide-react` (CDN UMD). No build step; Babel runs in-browser.
- **Source component**: `staffing-efficiency-dashboard.jsx` — React component (workflow for getting it into `index.html` is **TODO: confirm**).
- **Matcher**: Python in `matcher/`. Tiered: exact → RapidFuzz fuzzy + nicknames → Claude Haiku 4.5 LLM tiebreak with YTD activity context → unresolved go to review queue.
- **Review UI**: `review-ui/` — Vercel app where Tommy approves/overrides unresolved matches (https://domo-staffing.vercel.app).
- **Schedule**: `.github/workflows/matcher.yml` — daily cron at 03:00 CT, runs `python -m matcher.main run` from repo root.

## Deployment
- GitHub: https://github.com/Tommydenn/DOMO-Staffing
- Vercel project for review UI: `domo-staffing` (NOT the older `staffing` project)
- Vercel project for dashboard: **TODO: confirm**

## Files
- `index.html` — deployed single-file dashboard
- `staffing-efficiency-dashboard.jsx` — source React component
- `matcher/` — Python crosswalk builder (see modules below)
- `matcher/df205_pivot.py` — generates the full DF205 actions JSON. **Source of truth for the dataflow**. Run: `python matcher/df205_pivot.py > matcher/df205_actions.json`
- `matcher/df205_actions.json` — generated payload, gitignored
- `review-ui/` — Vercel review-queue app
- `.github/workflows/matcher.yml` — daily cron

### Matcher modules
- `matcher/main.py` — orchestrator (`cmd_run`, `_build_crosswalk_rows`, `_resolve_pending_with_llm`)
- `matcher/matcher.py` — tiered matching (`AdpWorker`, `EmEmployee`, `MatchResult`)
- `matcher/llm_tiebreak.py` — Claude Haiku tiebreak with activity context
- `matcher/domo_io.py` — OAuth-based Domo client (uses `api.domo.com` public API + Streams; the dev-token internal API blocks writes for `large-file-upload` connector datasets)

### Review UI (`review-ui/`)
- `index.html` — single-file React/Tailwind UI with **two review modes**:
  - **ADP →** tabs: Manual review / Unmatched / All (ADP punchers needing an EM match)
  - **EM →** Unmatched tab (Eldermark employees with last-30d services and no ADP match)
- `api/pending.js` — Vercel serverless GET. Returns `{items, em_items, total, em_total, summary}`. Runs 6 parallel queries: crosswalk + ADP 30d activity + EM 30d activity + EM employees (Inactive/Title/Hire) + EM 30d services-by-community + community-name lookup. Computes `em_items` on the fly (matcher seeds crosswalk from ADP-side only).
- `api/decide.js` — Vercel serverless POST. Accepts `{decision, adp_associate_id?, em_employee_id?}` (either side anchors the decision). Append pattern is read-all + add + replace via Streams API. **Catches 400/404 from a brand-new empty Decisions dataset**.

## Domo datasets

| Purpose | GUID | Notes |
|---|---|---|
| **DF205 output** | `995db646-e97e-41e8-b8fd-44f517904859` | All-staff hourly + detail. Both `Row_Type='Hour'` and `Row_Type='Detail'` rows. |
| **Crosswalk** | `143b7a4f-75b6-4e64-a60c-12d58cf13370` | OAuth-built daily. Old GUID `42e99d29-…` is deprecated. |
| **Decisions log** | Created on first decision via `decide.js` | Auto-created if missing; appended by review UI. |
| **Punches (ADP)** | `8e3dce28-1e8b-4cf3-a7f4-b95e06e4eaf6` | DF205 input |
| **Service Received (EM)** | `b8806ae0-6b04-4cb1-8896-02d0aa7ec3ef` | RAW EM service events |
| **Med Delivery (EM)** | `224fd07c-773f-4163-a891-a74cd70ab423` | Med passes; `Given_or_Recorded_Person_ID` is the EM employee_id (lets us attribute med passes per employee) |
| **Communities (EM)** | `7570562b-d421-448c-85f7-b42e0967ab83` | EM-side names ("Caretta Senior Living Holmen") |
| **Occupancy (EM)** | `e616d17f-be36-494d-8b31-f45a0850dbd8` | Has BOTH community_id AND ADP-style name ("Caretta Holmen") — used as `comm_bridge` |
| **EM Employees** | `cc34bb65-3e4d-4942-8f5f-a4f3488aae92` | EM employee directory. Has `Inactive`, `Title`, `Hire_Date`, `Sort_Name`, `Community_ID`. |
| **TWDD Mapping** (webform) | `6bfc6116-8876-4381-b63d-1c7fb8d48b97` | Tommy-maintained: `Timecard Worked Department Description` → `Department Worked` + `Community Worked In`. Ask Tommy to add unmapped TWDDs. |

## DF205 architecture (Magic ETL)

URL: https://greatlakesmc.domo.com/datacenter/dataflows/205/graph
Current version at handoff: **v20 / 5039** (auto-run on prior v5038 in flight at handoff time)

Outputs a single dataset with two row types:
- **`Row_Type='Hour'`** — community × date × hour aggregate (legacy RA-staffing card grain). Service totals correct here.
- **`Row_Type='Detail'`** — per (community_id, date, hour, associate, employee). All-staff with department, job title, TWDD-derived community_worked_in, plus `match_status`, `med_minutes_employee`.

### Tile chain (32 tiles)
- `sql-01..03` — RA-only chain (legacy compat)
- `sql-04 staff_punches` → `sql-05 staff_hourly` — all-staff punch hour-grain
- `sql-07 comm_bridge` — `(community_id, community_name)` from occupancy. **The bridge** between ADP-side names (in punches) and EM-side IDs (in services).
- `sql-06 staff_detail` — Detail-row producer. Two UNION branches (A: staff with optional svc/med joined; B: matched-EM care work where matched-ADP wasn't punched at that community).
- `sql-10 svc_base` → `sql-11 svc_agg` (Hour-grain svc) + `sql-12 svc_by_employee` (community + em_employee + adp_associate grain)
- `sql-13 svc_unmatched` — EM services where the EM employee isn't in the crosswalk. Emits Detail rows with synthetic `associate_id='EM:'+em_employee_id`. **Derives `dept_worked` from EM `Title`** (RA/CNA/HHA/Caregiver → Resident Assistants Staff; RN/LPN/DON/MA → Nursing Staff; Housekeep* → Housekeeping Staff; ED/BOM/etc → Management Office Staff).
- `sql-20 med_base` → `sql-21 med_agg` + `sql-22 med_by_employee` + `sql-23 med_unmatched` — same shape as svc tiles, for med passes (estimated 2 min per pass). sql-23 also derives dept_worked from Title.
- `sql-99 final_output` — UNION of Hour rows + sql-06 Detail rows + sql-13 unmatched-EM-svc rows + sql-23 unmatched-EM-med rows. Adds `match_status` column ('Matched' / 'ADP unlinked' / 'EM unlinked').

### The four Detail-row sources (covers ALL care work)
1. **sql-06 Branch A** — staff_hourly punch with optionally matched svc + med at same community → `match_status = 'Matched'` if EM linked else `'ADP unlinked'`
2. **sql-06 Branch B** — matched-EM care work with NO matching ADP punch at that community → `match_status = 'Matched'`
3. **sql-13** — unmatched-EM svc (EM person not in crosswalk) → `match_status = 'EM unlinked'`, dept_worked from Title
4. **sql-23** — unmatched-EM med passes → `match_status = 'EM unlinked'`, dept_worked from Title

Sum of `(svc_minutes_employee + med_minutes_employee)/60` across all four == raw EM total (services + estimated med pass time). **Always group by `community_id`, NOT `community_name`**.

### Critical join semantics
- staff_hourly has `community_name` from ADP punches ("Caretta Holmen")
- comm_dim has EM-side `community_name` ("Caretta Senior Living Holmen")
- Occupancy has BOTH — that's why it's the bridge
- `community_worked_in` in Branch A: TWDD output, normalized via `comm_bridge` lookup using `LOWER(REGEXP_REPLACE(name, '[^A-Za-z0-9]', ''))` — handles "AMIRA CHOICE - ARVADA" vs "AMIRA CHOICE ARVADA"
- `community_worked_in` in Branch B + sql-13 + sql-23: `comm_bridge.community_name` (canonical ADP-side)

## Cards to know

- **#1735745257 "Last Month Efficiency by Community by Employee"** — primary all-staff card, reads DF205 Detail rows. Row dims: Community → Match Status → Employee. Columns: Hours Worked (ADP), Service Hours (EM = svc + med), Med Pass Hours, Efficiency. Tommy may also have added `dept_worked` as a 4th row dim.
- **#463035702 "Staffing Efficiency Comparison"** — legacy community-level RA-only card, reads Hour rows. Used as the verification benchmark.

## Conventions
- Tailwind for styling (CDN mode — utility classes only)
- React components are functional + hooks
- Always load the `domo-great-lakes` skill before any Domo API call or ETL edit
- **Edit ETL via source-of-truth**: edit `matcher/df205_pivot.py`, regenerate JSON, push via `mcp__Domo_ETL__update_dataflow(dataflow_id=205, actions=...)`. Pass actions inline.
- **Card update via direct API**: `update_card` tool returns 405; use `domo_api_request` with `PUT /api/content/v1/cards/{id}` and the FULL card body (no partial patches).
- **Inline-paste corruption is real**: when pasting 50KB+ JSON, the model sometimes hallucinates extra clauses. Always verify push response by querying back the pushed body for known-corruption patterns: `LOWER(COALESCE(Hours_*, 'false'), 'false')` (wrong sql-40), `FROM \`svc_unmatched\` u` at the END of sql-99 (should be `med_unmatched mu`).

## Where we left off

- **2026-04-13**: CLAUDE.md created.
- **2026-05-05**: Scaffolded matcher + review UI + cron. Pivoted DF205 RA-only → all-staff. Caretta Holmen 1547 → 2877 svc_hrs fix landed (added comm_bridge, restructured sql-12 grain, sql-06 two-branch UNION, sql-13 unmatched-EM). Community name dedup (regex normalization).
- **2026-05-06 morning — med passes per employee + match_status**:
  - Discovered `med_delivery.Given_or_Recorded_Person_ID` is the EM employee_id, so med passes are attributable per person (not just at community-hour grain).
  - Added `sql-22 med_by_employee` + `sql-23 med_unmatched` (parallel to sql-12/sql-13). 4th Detail-row UNION branch.
  - Updated card #1735745257: `Service Hours (EM)` formula now = `(SUM(svc_minutes_employee) + SUM(med_minutes_employee)) / 60.0`. Added `Med Pass Hours` column. Caretta Holmen now shows ~3,739 service hrs (up from 2,877; matches legacy card).
  - Added `match_status` column ('Matched' / 'ADP unlinked' / 'EM unlinked'). Updated card to use as a Match Status row dim.
  - Caretta Holmen Apr 2026 split verified: Matched 4,089 staff hrs / 3,649 svc+med hrs · ADP unlinked 547 staff hrs / 0 svc · EM unlinked 0 staff hrs / 90 svc+med hrs. Totals reconcile: 4,636 / 3,739.
- **2026-05-06 review-UI work — 3 PRs (#9, #10, #11) all merged**:
  - PR #9: 30d activity badges, community grouping, fix-No-match-400 (`decide.js` was crashing on empty new Decisions dataset)
  - PR #10: Inactive flag on EM candidates + EM-unmatched tab (reverse-direction review)
  - PR #11: Service-community chips on EM cards (where the EM person's services are landing)
- **2026-05-07 — EM Title → dept_worked mapping (in flight, v20 / 5039)**:
  - Tommy filtered card by `dept_worked` (Housekeeping / Nursing / RA), but EM-unlinked rows had `dept_worked = NULL/'unmapped'` so they fell out of the filter.
  - Fix: sql-13 + sql-23 now derive `dept_worked` from EM `Title` (RA/CNA/HHA/Caregiver → Resident Assistants Staff; RN/LPN/DON/MA → Nursing Staff; Housekeep* → Housekeeping Staff; etc.)
  - sql-99 unmatched UNION branches now pass through `u.dept_worked` / `mu.dept_worked` instead of NULL.
  - Pushed v20/5039. Auto-run on prior v5038 was in progress at handoff (so v5039 hadn't run yet).
  - **Pending verify**: trigger DF205 (should run on v5039) and confirm Caretta Holmen EM-unlinked rows now show under "Resident Assistants Staff" / "Nursing Staff" instead of "unmapped".

## Pickup checklist for new chat

1. Read this file + memory index (auto-loaded)
2. Verify v5039 ran successfully and EM-unlinked rows have populated `dept_worked`:
   ```sql
   SELECT match_status, dept_worked, COUNT(*), ROUND(SUM(svc_minutes_employee + med_minutes_employee) / 60.0, 1) AS svc_hrs
   FROM table
   WHERE Row_Type='Detail' AND month_start='2026-04-01' AND community_id='119'
     AND match_status='EM unlinked'
   GROUP BY 1, 2
   ```
3. If still showing 'unmapped' for EM unlinked, the run hasn't picked up v5039 yet — trigger it.
4. Confirm with Tommy that filtering card #1735745257 by dept_worked = 'Resident Assistants Staff' now includes the EM-unlinked RA folks.

## Open questions / TODOs
- Clarify `.jsx` → `index.html` workflow
- Confirm Vercel project name for the dashboard
- Tommy: clean up TWDD webform inconsistencies long-term (the regex normalization is a safety net, not a substitute)
- Source-of-truth `matcher/df205_pivot.py` has uncommitted changes (med_passes, match_status, EM Title mapping). Commit when convenient — the dataflow is already live with the right SQL, but the script needs to match.
