// GET /api/pending — pending crosswalk rows that need human review.

import axios from "axios";

const PENDING_SOURCES = new Set(["llm-pending", "ambiguous-exact", "unmatched", "manual-review"]);
const CROSSWALK_NAME = "MAP | Employee Crosswalk";

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

export default async function handler(req, res) {
  if (req.method !== "GET") return res.status(405).json({ error: "method not allowed" });
  if (!process.env.DOMO_CLIENT_ID || !process.env.DOMO_CLIENT_SECRET) {
    return res.status(500).json({ error: "DOMO_CLIENT_ID/SECRET not configured" });
  }
  try {
    const datasetId = await findDatasetIdByName(CROSSWALK_NAME);
    if (!datasetId) return res.status(200).json({ items: [], total: 0 });
    const rows = await queryDataset(datasetId, "SELECT * FROM table");
    const items = rows
      .filter((row) => PENDING_SOURCES.has(row.match_source))
      .map((row) => ({
        adp_associate_id: row.adp_associate_id,
        adp_name: row.adp_name,
        adp_title: row.adp_title || "",
        adp_department: row.adp_department || "",
        adp_community: row.adp_community || "",
        match_source: row.match_source,
        candidates: [],
      }));
    res.status(200).json({ items, total: rows.length });
  } catch (err) {
    res.status(500).json({ error: err.message || "domo query failed" });
  }
}
