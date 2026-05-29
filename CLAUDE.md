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
Current version at handoff: **v5042** (live, SUCCESS, dataVersion 47, 7,331,636 rows)

Outputs a single dataset with two row types:
- **`Row_Type='Hour'`** — community × date × hour aggregate (legacy RA-staffing card grain). Service totals correct here.
- **`Row_Type='Detail'`** — per (community_id, date, hour, associate, employee). All-staff with department, job title, TWDD-derived community_worked_in, plus `match_status`, `med_minutes_employee`.

### Tile chain (34 tiles)
- `sql-01..03` — RA-only chain (legacy compat)
- `sql-04 staff_punches` → `sql-05 staff_hourly` — all-staff punch hour-grain. v5041 added pay code / rate type bucket columns flowing through.
- `sql-07 comm_bridge` — `(community_id, community_name)` from occupancy. **The bridge** between ADP-side names (in punches) and EM-side IDs (in services).
- `sql-06 staff_detail` — Detail-row producer. Two UNION branches (A: staff with optional svc/med joined; B: matched-EM care work where matched-ADP wasn't punched at that community). v5040: Branch B derives dept_worked from EM Title. v5041: pay/rate buckets. v5042: provider_* columns from sql-12.
- `sql-08 provider_dim` — **NEW v5042**. EM Service Providers catalog from `c028ecfe-...`, MAX'd per `(community_id, provider_code)`. Outputs `provider_name`, `provider_category` (mostly empty in source), `provider_billing_rate`.
- `sql-10 svc_base` → `sql-11 svc_agg` (Hour-grain svc) + `sql-12 svc_by_employee` (community + em_employee + adp_associate grain). v5042: sql-10 carries `provider_code`, sql-12 LEFT JOINs `provider_dim` and MAX's the provider_* cols.
- `sql-13 svc_unmatched` — EM services where the EM employee isn't in the crosswalk. Emits Detail rows with synthetic `associate_id='EM:'+em_employee_id`. **Derives `dept_worked` from EM `Title`** (RA/CNA/HHA/Caregiver → Resident Assistants Staff; RN/LPN/DON/MA → Nursing Staff; Housekeep* → Housekeeping Staff; ED/BOM/etc → Management Office Staff). v5042: also brings in provider_*.
- `sql-20 med_base` → `sql-21 med_agg` + `sql-22 med_by_employee` + `sql-23 med_unmatched` — same shape as svc tiles, for med passes (estimated 2 min per pass). sql-23 also derives dept_worked from Title. **No provider info** — `med_delivery` source has no Provider column.
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
- **Provider join (v5042)**: `svc_received.Provider` ↔ `Service_Providers.Code`, **scoped by Community_ID** (same code can mean different things in different communities). `svc_received.Service_Type` is NOT the provider code — it's a separate Service Type ID.

## Output columns added in v5040–v5042 (all additive, no grain change)

**v5041 — pay code / rate type:**
- Categoricals (MAX per punch): `pay_code`, `timecard_pay_code`, `rate_type_category`, `regular_pay_rate_amount`
- Hour buckets (proportional split, sum to `staff_hours_worked`): `regular_hours`, `overtime_hours`, `pto_hours`, `unpaid_hours`, `training_hours`, `double_time_hours`, `other_hours`

**v5042 — Service Providers join (svc-side rows only; NULL on Hour rows + med_unmatched):**
- `provider_name` (e.g. "Nurse", "AM AL RA", "NOC MC Med Passer")
- `provider_category` (mostly empty in source data)
- `provider_billing_rate` (DOUBLE; Nurse=$120, RA roles=$50)

## Cards to know

- **#1735745257 "Last Month Efficiency by Community by Employee"** — primary all-staff card, reads DF205 Detail rows. Row dims: Community → Match Status → Employee. Columns: Hours Worked (ADP), Service Hours (EM = svc + med), Med Pass Hours, Efficiency.
- **#1046798988 "Nursing Productivity by Community"** — created 2026-05-07. Pivot by community → dept_worked, with `percentOfTotal: true` to show nursing share visually. Last-month filter. On page 826293561 ("NEW - Staffing Dashboard"). Uses `% Nursing Worked` (9547) + `% Nursing Received` (9549) directly.
- **#571988101 "Productivity by Community by Provider"** — pivot Community → provider_name. Last-30d. Created 2026-05-12; **legacy beast modes** broke initial render (referenced old `ra_hours_worked` / `service_minutes`) — fixed 2026-05-12 by replacing value columns with raw `staff_hours_worked` SUM + `Service Hours (EM)` (9557) + `Efficiency` (9556). Will render realistic per-provider efficiencies after v5045 (dominant-provider fallback).
- **#429214416 "Nursing Productivity by Community Simple"** — Tommy added 2026-05-13/14. New per-resident ratio beastmodes (9559–9562) attached. Uses `Monday of the Week` (9558) for week grain.
- **#321295623 "Efficiency by Employee"** — owner 1823318908 (teammate), created 2026-05-14. Tommy added.
- **#463035702 "Staffing Efficiency Comparison"** — legacy community-level RA-only card, reads Hour rows. Used as the verification benchmark.

## Dataset-level beast modes catalog (`995db646-…` = DEV | RA Staffing Efficiency)

**Use these — they reference the current schema:**

| ID | legacyId (formulaId) | Name | Expression / notes |
|---|---|---|---|
| 9554 | `calculation_a0905dd7…` | Community Name | `community_worked_in` (use as ROW dim) |
| 9555 | `calculation_d67ea1d8…` | Med Pass Hours | `SUM(med_minutes_employee)/60.0` |
| 9557 | `calculation_b7021ade…` | Service Hours (EM) | `(SUM(svc_minutes_employee)+SUM(med_minutes_employee))/60` |
| 9556 | `calculation_f29f1b5c…` | Efficiency | `Service Hours (EM) / SUM(staff_hours_worked)` |
| 9546 | `calculation_e6e76616…` | Nursing Hours Worked | nursing-dept hrs |
| 9547 | `calculation_737bf53d…` | % Nursing Worked | nursing/total worked |
| 9548 | `calculation_5dab3372…` | Nursing Hours Received | nursing svc+med hrs |
| 9549 | `calculation_2e22999b…` | % Nursing Received | nursing/total received |
| 9550 | `calculation_70d47244…` | Residents Receiving Services | `MAX(residents_served_monthly)` |
| 9558 | `calculation_66bbd181…` | Monday of the Week | week-grain key from `report_date` |
| 9559 | `calculation_0485acdc…` | Nursing Utilization | `9548/9546` (recv/worked) |
| 9560 | `calculation_9e58f5a8…` | Nursing Rec / Total Rec | `9548/9563` |
| 9561 | `calculation_a2913b0d…` | Nursing Hrs Worked / Residents | `9546/9550` |
| 9562 | `calculation_5794b188…` | Nursing Hrs Rec / Residents | `9548/9550` |
| 9563 | `calculation_b6243c44…` | Service Hours EM | clone of 9557, used by 9560 |

**Do not use these (broken — reference columns that no longer exist after all-staff pivot):**
- 9424 "Hours Worked (ADP)" → `SUM(ra_hours_worked)` ❌
- 9425 "Hours Serviced (EM)" → uses `service_minutes` + `med_minutes_est` ❌
- 9426 "Unscheduled Hours" / 9427 "% Unscheduled" / 9428 "Unscheduled Charge" → all use `uncovered_service_minutes` / `ra_labor_cost` ❌
- 9429 "Efficiency" → uses old columns ❌ AND name-collides with 9556 → causes the orange "Rename Beast Modes" warning in Analyzer

**For new ADP-hours columns** just drag raw `staff_hours_worked` as SUM (no beast mode needed).

## Conventions
- Tailwind for styling (CDN mode — utility classes only)
- React components are functional + hooks
- Always load the `domo-great-lakes` skill before any Domo API call or ETL edit
- **Edit ETL via source-of-truth**: edit `matcher/df205_pivot.py`, regenerate JSON, push via `mcp__Domo_ETL__update_dataflow(dataflow_id=205, actions=...)`. Pass actions inline.
- **Card update via direct API**: `update_card` tool returns 405; use `domo_api_request` with `PUT /api/content/v1/cards/{id}` and the FULL card body (no partial patches).
- **Inline-paste corruption is real**: when pasting 50KB+ JSON, the model sometimes hallucinates extra clauses. Always verify push response by querying back the pushed body for known-corruption patterns: `LOWER(COALESCE(Hours_*, 'false'), 'false')` (wrong sql-40), `FROM \`svc_unmatched\` u` at the END of sql-99 (should be `med_unmatched mu`).
- **Beast Modes API is at `/api/query/v1/functions/...`** (NOT `/api/content/v1/cards/{id}` for formula creation, and NOT `/api/data/v3/datasources/{id}/formulas`). Auth: same `X-DOMO-Developer-Token` the MCP already uses. Documented at https://www.domo.com/docs/api-reference/beast-modes/.
  - **Create**: `POST /api/query/v1/functions/template` with body `{"name":"...", "expression":"SUM(...)", "dataType":"DOUBLE"|"DECIMAL", "links":[{"resource":{"type":"DATA_SOURCE","id":"<ds-id>"}, "visible": true, "active": true}]}`. The link MUST have `visible: true` or you get "Non global functions must have one and only one visible link". Returns `id` (int) and `legacyId` ("calculation_<uuid>") — use `legacyId` as `formulaId` in card subscriptions.
  - **Update expression**: `PUT /api/query/v1/functions/template/{id}/update?strict=false` body `{"expression":"..."}`. Path uses int `id`, not legacyId.
  - **Get by id**: `GET /api/query/v1/functions/template/{id}`.
  - **Search**: `POST /api/query/v1/functions/search` with `{"name":"<substring>", "filters":[{"field":"<flag>"}], "sort":{"field":"name","ascending":true}, "limit":N, "offset":0}`. Valid filter fields: `active, archived, beastmode, card, column, created, dataset, datatype, function, global, inactive, legacyid, link, locked, modified, name, nested, owner, status, variable` (plus `not<...>` variants). Filters are flag predicates, not key-value queries.
  - **Lock**: `PUT /api/query/v1/functions/{id}/lock`.
  - The previously-suspected limitation ("can't create new formulas via card PUT") was real for `/api/content/v1/cards/{id}` — but the right path is `/api/query/v1/functions/template`.

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
- **2026-05-07 morning — EM Title → dept_worked (v5039)**: sql-13 + sql-23 derive dept_worked from EM Title. v5039 ran clean.
- **2026-05-07 afternoon — three more pushes, all live and verified, all on PR #12 (open, awaiting merge to main)**:
  - **v5040 — Branch B EM Title fix** (commit `2daa8d4`). sql-06 Branch B was still emitting `'unmapped'`; same Title CASE applied. Caretta Holmen Apr 2026 Matched/unmapped: 1,278 em hrs → 18 em hrs (recovered to RA + Nursing).
  - **v5041 — pay code / rate type bucket columns** (commit `a16acf1`). 11 additive columns (4 categoricals + 7 hour buckets). Bucket sums = staff_hours_worked within ~0.05%. Caretta Holmen unpaid_hours = 157 hrs / $0 paid.
  - **v5042 — EM Service Providers join** (commit `941be9b`). Joined `c028ecfe-b470-43cb-bd9a-bf0f8de0abbe` on (Community_ID, Provider Code). Adds `provider_name` / `provider_category` / `provider_billing_rate`. 14 distinct providers at Caretta Holmen, Nurse @ $120/hr, RA roles @ $50/hr.
  - **New card #1046798988 "Nursing Productivity by Community"** — pivot by community → dept_worked with percentOfTotal. On page 826293561.
  - **API limitation _resolved_**: beastmodes ARE creatable via `POST /api/query/v1/functions/template` (was hunting wrong path). Created 4 dataset-level beastmodes on `DEV | RA Staffing Efficiency`: `Nursing Hours Worked` (id 9546), `% Nursing Worked` (9547), `Nursing Hours Received` (9548), `% Nursing Received` (9549). Card #1046798988 now uses them directly — explicit % columns, sorted by % Nursing Received DESC. Glenn Minnetonka tops the list at 16.3% / 10.7%. See Conventions for full API.
- **2026-05-12 — Provider efficiency card + v5045 dominant-provider fallback** (commit `c56407c` on PR #12):
  - User reported card #571988101 "Productivity by Community by Provider" rendering broken: ADP hours showing as `(blank)` for most rows, efficiency > 100%.
  - **Root cause #1 (data)**: when grouping by provider, only ADP hours that had a concurrent EM service event got `provider_name` from sql-12. An employee's idle/non-service hours fell into the `(blank)` bucket — so denominator under-counted in per-provider math.
  - **Fix**: new tile `sql-51 emp_dominant_provider_monthly` picks the provider with the most service minutes per (community, employee, month). sql-06 Branch A LEFT JOINs it and uses `COALESCE(MAX(se.provider_name), MAX(h.dominant_provider_name))` so hour-grain wins when present, monthly dominant fills the rest. Grain unchanged (7,361,632 rows). Pushed v5045, byte-for-byte verify clean, triggered run id 7469839, SUCCESS in ~6.5min.
  - **Root cause #2 (card)**: card had 3 legacy beast modes (`Hours Worked (ADP)` 9424 = `SUM(ra_hours_worked)`, `Hours Serviced (EM)` 9425, `Efficiency` 9429) still attached from before the all-staff pivot. Those columns don't exist anymore → beast modes return null.
  - **Fix**: PUT card with new subscription columns: raw `staff_hours_worked` SUM (alias "Hours Worked (ADP)") + `Service Hours (EM)` 9557 (alias "Hours Received (EM)") + `Efficiency` 9556. Card now renders realistic numbers (Glenn Minnetonka RA AM AL/IL FLOAT: 481 ADP / 262 EM / 55% efficiency).
  - **Outstanding**: duplicate "Efficiency" + "Service Hours (EM)" beast mode names on the dataset still trigger the orange "Rename Beast Modes to avoid conflict" warning in Analyzer. Could fix by archiving 9424/9425/9426/9427/9428/9429 (all reference dead columns). Tommy hasn't asked yet.
- **2026-05-13 / 14 — Tommy added per-resident ratio beast modes and two new cards**:
  - Beast modes created on the dataset (visible in catalog table above): **9558 Monday of the Week** (week-grain key), **9559 Nursing Utilization** (recv/worked), **9560 Nursing Rec / Total Rec**, **9561 Nursing Hrs Worked / Residents**, **9562 Nursing Hrs Rec / Residents**, **9563 Service Hours EM** (clone of 9557), and (Tommy added later) **9577 Free Nursing Hours** = `(9546 - 9548) / 9550`. The 95xx-`DOMO_BEAST_MODE(NNNN)` syntax composes beast modes by id — handy.
  - New cards: **#429214416 "Nursing Productivity by Community Simple"** and **#321295623 "Efficiency by Employee"** (owned by teammate 1823318908). Both use the dataset-level beast modes directly (no card-level formulas needed).
- **2026-05-29 — DON salaried imputed hours (v5046 + v5047)** on PR #12:
  - Salaried Directors of Nursing don't punch but work ~40 hr/wk. Without them, dept_worked='Nursing Staff' under-counted and Nursing Hours Worked was missing imputed time at every community with a DON.
  - **New ADP HR loads**: `RAW | ADP | Employee Job History` (`98dbcdce-…`) and `RAW | ADP | Employee Pay History` (`7bc97874-…`). Both auto-refresh daily.
  - **sql-70 don_dim**: filters to currently-active salaried DONs only (`Position Status = 'Active' AND Position Effective End Date IS NULL`, `Basis of Pay = 'Salary'`, `Job Title Description = 'Director of Nursing'`). Inline alias map normalizes ADP `Location Description` to canonical `comm_bridge` community_name (handles "Hayden Grove BL" → "Hayden Grove Bloomington", "Caretta Senior Living Holmen" → "Caretta Holmen", "Talamore SP -do not use" → "Talamore Sun Prairie", etc.). Result: 19 currently-active DONs across 19 communities.
  - **sql-71 don_hourly**: synthetic punches at M-F 8a-4p (8 hr/day, 40 hr/wk) in `staff_hourly` shape. Anti-joins real punches on `(associate_id, bucket_date)` so PTO/sick days come from real ADP records, not duplicates.
  - **sql-06 changes**: new `staff_hourly_all` CTE unions `staff_hourly + don_hourly`; dept_worked CASE overrides `SALARIED + Director of Nursing → 'Nursing Staff'`. No other downstream changes.
  - **v5046 (commit `1ae40af`)** had a bug: filter `Position Status = 'Active'` matched stale historical "Active" records → ghost DONs at communities they'd moved away from (Caretta Holmen DON ending 2023-08-14 was still emitting hours through today). **v5047 (commit `844440d`)** fixed by also requiring `Effective End Date IS NULL/blank`.
  - **Card #429214416 "Nursing Productivity by Community Simple"** picks up DON hours automatically via existing beast mode 9546 — no card edit needed. Glenn Minnetonka last-30d Nursing Hours Worked: 1,593 (184 DON synthetic). 184 = 23 weekdays × 8 hrs (one full month of DON imputed).
  - Output row count: 7,493,762 (was 7,481,574 pre-DON). +12,188 net rows = 19 DONs × ~weekdays-since-effective-start × 8 hr-rows.

### sql-70 / sql-71 things to watch
- **Multi-community DONs**: source data has 1 DON per community max; if ADP ever assigns one DON to multiple `Location Description` values simultaneously, `MAX(adp_location)` will pick alphabetically — may need ROW_NUMBER() logic.
- **Unmapped locations**: any new `Location Description` string will fall through the CASE WHEN to `ELSE p.adp_location` and miss `comm_bridge` join → DON hours go to a community_id-NULL bucket. Periodic check: `SELECT DISTINCT community_worked_in FROM table WHERE pay_code = 'SALARIED' AND community_id IS NULL`.
- **Anti-join false positives**: if a DON does happen to punch at all (training day, etc.), the whole day's 8 synthetic hrs are dropped. Acceptable for now since DONs rarely punch.

## Pickup checklist for new chat

1. Read this file + memory index (auto-loaded).
2. **Check PR #12 status** at https://github.com/Tommydenn/DOMO-Staffing/pull/12. Latest commit `c56407c` (v5045 dominant-provider fallback). If merged, branch `claude/intelligent-kilby-1467f4` can be deleted.
3. **Output dataset columns to remember**: pay/rate buckets (v5041), provider_name/category/billing_rate (v5042), `residents_served_monthly` (v5044), dominant-provider fallback via sql-51 (v5045). Revenue-validation card idea: `SUM(svc_minutes_employee/60.0 * provider_billing_rate)` should reconcile against `svc_revenue_month`.
4. **Beast modes catalog above is canonical** — use it as the lookup table for which formulaId/templateId to reference.

## Open questions / TODOs
- Clarify `.jsx` → `index.html` workflow
- Confirm Vercel project name for the dashboard
- Tommy: clean up TWDD webform inconsistencies long-term (the regex normalization is a safety net, not a substitute)
- PR #12 needs review/merge.
- **Med pass provider attribution**: `med_delivery` source has no Provider column, so med_unmatched and med_by_employee paths can't get provider_*. Acceptable since med passes are a smaller slice; revisit only if needed.
- **Archive legacy beast modes** (9424, 9425, 9426, 9427, 9428, 9429) to silence the "Rename Beast Modes" Analyzer warning. Only do this when Tommy asks — they may be linked to cards we haven't audited.
