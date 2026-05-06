// GET /api/pending — pending crosswalk rows that need human review,
// enriched with last-30-day punch + service activity to help Tommy decide.

import axios from "axios";

const PENDING_SOURCES = new Set(["llm-pending", "ambiguous-exact", "unmatched", "manual-review"]);
const CROSSWALK_NAME = "MAP | Employee Crosswalk";
const PUNCHES_DATASET = "8e3dce28-1e8b-4cf3-a7f4-b95e06e4eaf6";  // RAW | ADP Punches
const SERVICES_DATASET = "b8806ae0-6b04-4cb1-8896-02d0aa7ec3ef"; // RAW | EM | Service Received

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

// Last-30-day ADP punch activity, keyed by Associate ID
async function fetchAdpActivity() {
  const sql = `
    SELECT
      \`Associate ID\` AS adp_id,
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
      hours_30d: Number(r.hours_30d || 0),
      last_community: r.last_community || "",
      last_title: r.last_title || "",
      last_dept: r.last_dept || "",
      last_punch: r.last_punch || "",
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

    // Run the 3 queries in parallel — activity queries are independent of crosswalk
    const [rows, adpActivity, emActivity] = await Promise.all([
      queryDataset(datasetId, "SELECT * FROM table"),
      fetchAdpActivity(),
      fetchEmActivity(),
    ]);

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
          return {
            ...c,
            svc_hrs_30d: act?.svc_hrs_30d ?? 0,
            svc_count_30d: act?.svc_count_30d ?? 0,
            last_service: act?.last_service ?? "",
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

    res.status(200).json({ items, total: rows.length, summary });
  } catch (err) {
    res.status(500).json({ error: err.message || "domo query failed" });
  }
}
