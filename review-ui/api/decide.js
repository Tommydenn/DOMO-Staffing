// POST /api/decide — append a human decision to MAP | Employee Crosswalk Decisions.
// Uses OAuth + the Domo public API. Append is implemented as read-all + add + replace,
// with the readAll step tolerant of brand-new (empty) datasets that 400 on SELECT.

import axios from "axios";

const DECISIONS_NAME = "MAP | Employee Crosswalk Decisions";
const DECISIONS_DESC = "Append-only log of human review decisions from the Vercel review UI.";
const DECISIONS_COLUMNS = [
  { type: "STRING", name: "adp_associate_id" },
  { type: "STRING", name: "em_employee_id" },
  { type: "STRING", name: "decision" },
  { type: "DATETIME", name: "decided_at" },
];

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

async function ensureDecisionsDataset() {
  const tok = await bearer();
  const list = await axios.get("https://api.domo.com/v1/datasets", {
    headers: { Authorization: `Bearer ${tok}`, Accept: "application/json" },
    params: { nameLike: DECISIONS_NAME, limit: 50 },
    timeout: 30000,
  });
  for (const ds of list.data || []) if (ds.name === DECISIONS_NAME) return ds.id;
  const created = await axios.post(
    "https://api.domo.com/v1/datasets",
    { name: DECISIONS_NAME, description: DECISIONS_DESC, schema: { columns: DECISIONS_COLUMNS } },
    { headers: { Authorization: `Bearer ${tok}`, Accept: "application/json" }, timeout: 30000 }
  );
  return created.data.id;
}

// Read existing rows. Brand-new api-type datasets return 400 on SELECT before
// any data has been uploaded — treat that as empty rather than failing the request.
async function readAll(datasetId) {
  const tok = await bearer();
  try {
    const r = await axios.post(
      `https://api.domo.com/v1/datasets/query/execute/${datasetId}`,
      { sql: "SELECT * FROM table" },
      { headers: { Authorization: `Bearer ${tok}`, Accept: "application/json" }, timeout: 60000 }
    );
    const cols = r.data.columns || [];
    return (r.data.rows || []).map((row) => Object.fromEntries(cols.map((c, i) => [c, row[i]])));
  } catch (err) {
    const status = err?.response?.status;
    if (status === 400 || status === 404) return [];
    throw err;
  }
}

function rowsToCsv(rows, cols) {
  const escape = (v) => {
    const s = v === undefined || v === null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
  };
  const out = [cols.join(",")];
  for (const r of rows) out.push(cols.map((c) => escape(r[c])).join(","));
  return out.join("\n") + "\n";
}

async function replaceData(datasetId, rows, cols) {
  const tok = await bearer();
  await axios.put(
    `https://api.domo.com/v1/datasets/${datasetId}/data`,
    rowsToCsv(rows, cols),
    {
      headers: {
        Authorization: `Bearer ${tok}`,
        "Content-Type": "text/csv",
        Accept: "application/json",
      },
      timeout: 60000,
    }
  );
}

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "method not allowed" });
  const { adp_associate_id, em_employee_id, decision } = req.body || {};
  // Accept both directions:
  //   ADP-anchored: adp_associate_id required; em_employee_id optional (No match → "")
  //   EM-anchored: em_employee_id required; adp_associate_id optional (No match → "")
  if (!decision || (!adp_associate_id && !em_employee_id)) {
    return res.status(400).json({ error: "decision plus at least one of adp_associate_id or em_employee_id required" });
  }
  if (!process.env.DOMO_CLIENT_ID || !process.env.DOMO_CLIENT_SECRET) {
    return res.status(500).json({ error: "DOMO_CLIENT_ID/SECRET not configured" });
  }
  try {
    const datasetId = await ensureDecisionsDataset();
    const existing = await readAll(datasetId);
    existing.push({
      adp_associate_id: adp_associate_id || "",
      em_employee_id: em_employee_id || "",
      decision,
      decided_at: new Date().toISOString(),
    });
    const colNames = DECISIONS_COLUMNS.map((c) => c.name);
    await replaceData(datasetId, existing, colNames);
    res.status(200).json({ ok: true });
  } catch (err) {
    const detail = err?.response?.data || err?.message || "domo upload failed";
    res.status(500).json({ error: typeof detail === "string" ? detail : JSON.stringify(detail) });
  }
}
