# Staffing App — Claude Context

> Session-memory file for this project. Read at the start of every session. Update at the end of meaningful work.

## What this app does
HGSA Staffing Efficiency Dashboard — visualizes staffing efficiency metrics for Great Lakes Management / HGSA communities.

## Stack
- Single-file static HTML app (`index.html`) — loads React 18 + ReactDOM + Babel Standalone + Tailwind (all via CDN)
- UI library: `lucide-react` (CDN UMD)
- Source component: `staffing-efficiency-dashboard.jsx` — the React component that's (likely) inlined/compiled into `index.html`
- No build step; Babel runs in-browser

## Deployment
- GitHub: https://github.com/Tommydenn/DOMO-Staffing
- Vercel project: **TODO: confirm name**

## Files
- `index.html` — the deployed single-file dashboard (includes a loading splash, loads data in a plain `<script>` block, then hands off to Babel-compiled React)
- `staffing-efficiency-dashboard.jsx` — source React component; **TODO: confirm workflow — is this the "edit here, then paste into index.html" source of truth?**
- `matcher/` — Python project that builds the ADP↔Eldermark employee crosswalk via tiered matching (exact → fuzzy → Claude Haiku tiebreak). Runs daily via GitHub Actions.
- `review-ui/` — Vercel-deployed page where Tommy approves/overrides matches the matcher couldn't auto-resolve.
- `.github/workflows/matcher.yml` — daily cron at 03:00 CT.

## Data
- Primary dataflow: DF205 `DEV | RA Staffing Efficiency` (Magic ETL). Output dataset `995db646-e97e-41e8-b8fd-44f517904859`. Currently RA-only — being pivoted to all-staff.
- Crosswalk dataset: `MAP | Employee Crosswalk` (`6adece01-4d8a-4a2c-be23-b3b33e05f897`) — built daily by `matcher/`.
- Crosswalk decisions log: `MAP | Employee Crosswalk Decisions` (`6e9df3f9-ffa3-401d-bdcb-dc4d3e97de3e`) — appended by the Vercel review UI.
- Always load the `domo-great-lakes` skill before any Domo API call or ETL edit.

## Domo integration
If this reads from Domo, load the `domo-great-lakes` skill before making any API calls or building queries — it has the dataflow/dataset map, beastmode patterns, and API quirks already captured.

## Conventions
- Tailwind for styling (CDN mode — utility classes only)
- React components are functional + hooks

## Where we left off
_Update this section at the end of each session._

- **2026-04-13**: CLAUDE.md created. No code changes yet.
- **2026-05-05**: Started ADP↔Eldermark crosswalk project. Scaffolded `matcher/` (Python, tiered matching) + `review-ui/` (Vercel) + GitHub Actions cron. Crosswalk + decisions datasets uploaded to Domo. DF205 pivot to all-staff is the next step, gated on a successful matcher dry-run.

## Open questions / TODOs for Tommy
- Clarify `.jsx` → `index.html` workflow
- Confirm Vercel project name for the dashboard
- Rotate the Anthropic API key after first successful matcher run (it was pasted in chat)
