// GET /api/pending — pending crosswalk rows that need human review,
// enriched with last-30-day punch + service activity to help Tommy decide.

import axios from "axios";

const PENDING_SOURCES = new Set(["llm-pending", "ambiguous-exact", "unmatched", "manual-review"]);
const CROSSWALK_NAME = "MAP | Employee Crosswalk";
const PUNCHES_DATASET = "8e3dce28-1e8b-4cf3-a7f4-b95e06e4eaf6";  // RAW | ADP Punches
const SERVICES_DATASET = "b8806ae0-6b04-4cb1-8896-02d0aa7ec3ef"; // RAW | EM | Service Received
const EM_EMPLOYEES_DATASET = "cc34bb65-3e4d-4942-8f5f-a4f3488aae92"; // RAW | EM | Employees

let _token = null;
let _tokenExpires = 0;

async function bearer() {
  if (_token && Date.now() < _tokenExpires - 60_000) return _token;
  const id = process.env.DOMO_CLIENT_ID;
  const sec = process.env.DOMO_CLIENT_SECRET;
  const auth = Buffer.from(`${id}:${sec}`).toString("base64");
  const r = await axios.post(
    "https://api.domo.com/oauth/token",
    null,
    {
      params: { grant_type: "client_credentials", scope: "data user" },
      headers: { Authorization: `Basic ${auth}` },
      timeout: 15000,
    }
  );
  _token = r.data.access_token;
  _tokenExpires = Date.now() + Number(r.data.expires_in) * 1000;
  return _token;
}

async function findDatasetIdByName(name) {
  const tok = await bearer();
  const r = await axios.get("https://api.domo.com/v1/datasets", {
    headers: { Authorization: `Bearer ${tok}`, Accept: "application/json" },
    params: { nameLike: name, limit: 50 },
    timeout: 30000,
  });
  for (const ds of r.data || []) if (ds.name === name) return ds.id;
  return null;
}

async function queryDataset(datasetId, sql) {
  const tok = await bearer();
  const r = await axios.post(
    `https://api.domo.com/v1/datasets/query/execute/${datasetId}`,
    { sql },
    { headers: { Authorization: `Bearer ${tok}`, Accept: "application/json" }, timeout: 60000 }
  );
  const cols = r.data.columns || [];
  return (r.data.rows || []).map((row) => Object.fromEntries(cols.map((c, i) => [c, row[i]])));
}

async function safeQuery(datasetId, sql) {
  try {
    return await queryDataset(datasetId, sql);
  } catch (err) {
    // Activity enrichment is optional — never break the page if a side query fails.
    console.error(`safeQuery ${datasetId} failed:`, err?.message);
    return [];
  }
}

function parseCandidates(raw) {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

// Last-30-day ADP punch activity, keyed by Associate ID. Includes Payroll Name
// so the EM-unmatched view can fuzzy-match candidates by name.
async function fetchAdpActivity() {
  const sql = `
    SELECT
      \`Associate ID\` AS adp_id,
      MAX(\`Payroll Name\`) AS adp_name,
      ROUND(SUM(CAST(\`Hours\` AS DOUBLE)), 1) AS hours_30d,
      MAX(\`Community Name\`) AS last_community,
      MAX(\`Job Title Description\`) AS last_title,
      MAX(\`Department Simplified\`) AS last_dept,
      MAX(CAST(\`Timecard Date\` AS DATE)) AS last_punch
    FROM table
    WHERE \`Time In\` IS NOT NULL
      AND CAST(\`Hours\` AS DOUBLE) > 0
      AND CAST(\`Timecard Date\` AS DATE) >= DATE_SUB(CURRENT_DATE, 30)
    GROUP BY 1
  `;
  const rows = await safeQuery(PUNCHES_DATASET, sql);
  const map = new Map();
  for (const r of rows) {
    if (!r.adp_id) continue;
    map.set(r.adp_id, {
      adp_name: r.adp_name || "",
      hours_30d: Number(r.hours_30d || 0),
      last_community: r.last_community || "",
      last_title: r.last_title || "",
      last_dept: r.last_dept || "",
      last_punch: r.last_punch || "",
    });
  }
  return map;
}

// Token-overlap score between two name strings. Strips punctuation, lowercases,
// splits on whitespace, returns intersection / max(set sizes). 1.0 = perfect.
function nameTokens(name) {
  return new Set(
    String(name || "")
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .split(/\s+/)
      .filter((t) => t.length >= 2)
  );
}
function overlapScore(a, b) {
  const ta = nameTokens(a);
  const tb = nameTokens(b);
  if (!ta.size || !tb.size) return 0;
  let inter = 0;
  for (const t of ta) if (tb.has(t)) inter++;
  return inter / Math.max(ta.size, tb.size);
}

// EM employees profile metadata, keyed by ID. Used to mark Inactive accounts
// (Eldermark sometimes has duplicate accounts for one person — the deactivated
// one should not be the match) and to surface Title + Hire_Date for context.
async function fetchEmEmployees() {
  const sql = `
    SELECT
      ID AS em_id,
      Inactive AS inactive,
      Title AS title,
      Hire_Date AS hire_date,
      Community_ID AS home_community_id
    FROM table
  `;
  const rows = await safeQuery(EM_EMPLOYEES_DATASET, sql);
  const map = new Map();
  for (const r of rows) {
    if (!r.em_id) continue;
    map.set(r.em_id, {
      inactive: String(r.inactive || "").toLowerCase() === "true",
      title: r.title || "",
      hire_date: r.hire_date || "",
      home_community_id: r.home_community_id || "",
    });
  }
  return map;
}

// Last-30-day EM service activity, keyed by Employee_ID. Med passes attributed
// to Given_or_Recorded_Person_ID are merged in so candidates with med-only
// activity still show up.
async function fetchEmActivity() {
  const svcSql = `
    SELECT Employee_ID AS em_id,
           SUM(Minutes_of_Service_Actual) / 60.0 AS svc_hrs_30d,
           COUNT(*) AS svc_count_30d,
           MAX(CAST(Service_Date AS DATE)) AS last_service
    FROM table
    WHERE Minutes_of_Service_Actual > 0
      AND (Canceled_Code IS NULL OR Canceled_Code = '')
      AND Employee_ID IS NOT NULL AND Employee_ID != ''
      AND CAST(Service_Date AS DATE) >= DATE_SUB(CURRENT_DATE, 30)
    GROUP BY 1
  `;
  const rows = await safeQuery(SERVICES_DATASET, svcSql);
  const map = new Map();
  for (const r of rows) {
    if (!r.em_id) continue;
    map.set(r.em_id, {
      svc_hrs_30d: Number(r.svc_hrs_30d || 0),
      svc_count_30d: Number(r.svc_count_30d || 0),
      last_service: r.last_service || "",
    });
  }
  return map;
}

export default async function handler(req, res) {
  if (req.method !== "GET") return res.status(405).json({ error: "method not allowed" });
  if (!process.env.DOMO_CLIENT_ID || !process.env.DOMO_CLIENT_SECRET) {
    return res.status(500).json({ error: "DOMO_CLIENT_ID/SECRET not configured" });
  }
  try {
    const datasetId = await findDatasetIdByName(CROSSWALK_NAME);
    if (!datasetId) return res.status(200).json({ items: [], total: 0, summary: {} });

    // Run the 4 queries in parallel — side queries are independent of crosswalk
    const [rows, adpActivity, emActivity, emEmployees] = await Promise.all([
      queryDataset(datasetId, "SELECT * FROM table"),
      fetchAdpActivity(),
      fetchEmActivity(),
      fetchEmEmployees(),
    ]);

    // Pre-compute communities lookup (community_id -> name) from EM employees
    // so we can label EM-anchored cards with their home community.
    const emEmpsArr = [...emEmployees.entries()].map(([em_id, p]) => ({ em_id, ...p }));
    // Build set of EM ids that are already paired in the crosswalk so we can
    // exclude them from the unmatched-EM view.
    const pairedEmIds = new Set();
    for (const row of rows) {
      const adp = (row.adp_associate_id || "").trim();
      const em = (row.em_employee_id || "").trim();
      if (adp && em) pairedEmIds.add(em);
    }

    const summary = { exact: 0, fuzzy: 0, llm: 0, "manual-review": 0, unmatched: 0, other: 0 };
    for (const row of rows) {
      const k = row.match_source || "other";
      if (k in summary) summary[k]++; else summary.other++;
    }

    const items = rows
      .filter((row) => PENDING_SOURCES.has(row.match_source))
      .map((row) => {
        const cands = parseCandidates(row.candidates_json).map((c) => {
          const act = emActivity.get(c.em_employee_id);
          const profile = emEmployees.get(c.em_employee_id);
          return {
            ...c,
            svc_hrs_30d: act?.svc_hrs_30d ?? 0,
            svc_count_30d: act?.svc_count_30d ?? 0,
            last_service: act?.last_service ?? "",
            inactive: profile?.inactive ?? false,
            em_title: profile?.title ?? "",
            em_hire_date: profile?.hire_date ?? "",
            em_home_community_id: profile?.home_community_id ?? "",
          };
        });
        const adpAct = adpActivity.get(row.adp_associate_id);
        return {
          adp_associate_id: row.adp_associate_id,
          adp_name: row.adp_name,
          adp_title: row.adp_title || "",
          adp_department: row.adp_department || "",
          adp_community: row.adp_community || "",
          adp_ytd_hours: Number(row.adp_ytd_hours || 0),
          adp_hours_30d: adpAct?.hours_30d ?? 0,
          adp_last_punch: adpAct?.last_punch ?? "",
          adp_last_community_30d: adpAct?.last_community ?? "",
          match_source: row.match_source,
          candidates: cands,
          notes: row.notes || "",
        };
      });

    // Manual-review (has candidates) before unmatched (no candidates) — actionable first.
    // Within the same group, sort by community to make the in-page community-grouped
    // view stable.
    items.sort((a, b) => {
      const rank = (s) => (s === "manual-review" || s === "ambiguous-exact" ? 0 : s === "llm-pending" ? 1 : 2);
      if (rank(a.match_source) !== rank(b.match_source)) return rank(a.match_source) - rank(b.match_source);
      const ca = (a.adp_community || "zzz").toLowerCase();
      const cb = (b.adp_community || "zzz").toLowerCase();
      if (ca !== cb) return ca.localeCompare(cb);
      return (a.adp_name || "").localeCompare(b.adp_name || "");
    });

    // ----- EM-anchored unmatched: EM employees performing services with no ADP match -----
    // Materialize ADP punchers as a flat array for fuzzy candidate lookup.
    const adpPunchers = [...adpActivity.entries()].map(([adp_id, a]) => ({ adp_id, ...a }));

    const em_items = emEmpsArr
      .filter((emp) => {
        if (emp.inactive) return false;
        if (pairedEmIds.has(emp.em_id)) return false;
        const act = emActivity.get(emp.em_id);
        if (!act || act.svc_hrs_30d <= 0) return false;
        return true;
      })
      .map((emp) => {
        const act = emActivity.get(emp.em_id) || {};
        // Try to look up Sort_Name from emEmployees — but it's not in the schema we pulled.
        // Use a derived display name: prefer em_id-keyed services last_service for context.
        // Actually emEmployees has Title; Sort_Name is in the raw dataset but we didn't
        // pull it. Fall back to em_id if no name; the matcher's already-stored EM Sort_Name
        // for this person lives in the crosswalk's em_name field for paired rows. For
        // unmatched it doesn't exist. We'll fix this in a follow-up by pulling Sort_Name too.
        const em_name = emp.em_name || "";  // populated below if we re-fetched
        // Score ADP punchers by name overlap. We don't have em_name here yet —
        // candidate scoring runs after we enrich names.
        return {
          em_employee_id: emp.em_id,
          em_name,
          em_title: emp.title || "",
          em_hire_date: emp.hire_date || "",
          em_home_community_id: emp.home_community_id || "",
          svc_hrs_30d: Number(act.svc_hrs_30d || 0),
          svc_count_30d: Number(act.svc_count_30d || 0),
          last_service: act.last_service || "",
          adp_candidates: [],
        };
      });

    // Pull Sort_Name + Community from EM employees in one go, since fetchEmEmployees
    // didn't include them (it only had Inactive/Title/Hire_Date/Community_ID). Re-query
    // for just the unmatched IDs to keep the payload small.
    if (em_items.length) {
      const idList = em_items.map((e) => `'${String(e.em_employee_id).replace(/'/g, "''")}'`).join(",");
      const namesSql = `SELECT ID AS em_id, Sort_Name AS sort_name FROM table WHERE ID IN (${idList})`;
      const nameRows = await safeQuery(EM_EMPLOYEES_DATASET, namesSql);
      const nameMap = new Map();
      for (const r of nameRows) if (r.em_id) nameMap.set(r.em_id, r.sort_name || "");
      for (const it of em_items) {
        it.em_name = nameMap.get(it.em_employee_id) || it.em_employee_id;
      }
    }

    // Now score ADP candidates by token overlap on names. Suggest top 5 with score >= 0.5.
    for (const it of em_items) {
      const scored = [];
      for (const p of adpPunchers) {
        if (!p.adp_name) continue;
        const score = overlapScore(it.em_name, p.adp_name);
        if (score >= 0.5) {
          scored.push({
            adp_associate_id: p.adp_id,
            adp_name: p.adp_name,
            adp_title: p.last_title,
            adp_community: p.last_community,
            adp_dept: p.last_dept,
            adp_hours_30d: Number(p.hours_30d || 0),
            score,
          });
        }
      }
      scored.sort((a, b) => b.score - a.score || b.adp_hours_30d - a.adp_hours_30d);
      it.adp_candidates = scored.slice(0, 5);
    }

    // Sort EM-anchored items: highest 30d service hours first, then by name
    em_items.sort((a, b) => {
      if (a.svc_hrs_30d !== b.svc_hrs_30d) return b.svc_hrs_30d - a.svc_hrs_30d;
      return (a.em_name || "").localeCompare(b.em_name || "");
    });

    res.status(200).json({
      items,
      em_items,
      total: rows.length,
      em_total: em_items.length,
      summary,
    });
  } catch (err) {
    res.status(500).json({ error: err.message || "domo query failed" });
  }
}
