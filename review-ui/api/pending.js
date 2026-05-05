// GET /api/pending — returns the current crosswalk rows that need human review.
// Reads from MAP | Employee Crosswalk in Domo.

import axios from "axios";

const PENDING_SOURCES = new Set(["llm-pending", "ambiguous-exact", "unmatched", "manual-review"]);

export default async function handler(req, res) {
  if (req.method !== "GET") {
    return res.status(405).json({ error: "method not allowed" });
  }

  const host = process.env.DOMO_API_HOST;
  const token = process.env.DOMO_DEVELOPER_TOKEN;
  const datasetId = process.env.DATASET_CROSSWALK;
  if (!host || !token || !datasetId) {
    return res.status(500).json({ error: "server not configured" });
  }

  try {
    const r = await axios.post(
      `https://${host}/api/query/v1/execute/${datasetId}`,
      { sql: "SELECT * FROM table", fillEmptyCells: true },
      { headers: { "X-DOMO-Developer-Token": token, "Accept": "application/json" }, timeout: 60000 }
    );
    const cols = r.data.columns || [];
    const rows = (r.data.rows || []).map(row => Object.fromEntries(cols.map((c, i) => [c, row[i]])));

    const items = rows
      .filter(row => PENDING_SOURCES.has(row.match_source))
      .map(row => {
        let candidates = [];
        try {
          if (row.candidates_json) candidates = JSON.parse(row.candidates_json);
        } catch (_) {}
        return {
          adp_associate_id: row.adp_associate_id,
          adp_name: row.adp_name,
          adp_title: row.adp_title || "",
          adp_department: row.adp_department || "",
          adp_community: row.adp_community || "",
          match_source: row.match_source,
          candidates,
        };
      });

    res.status(200).json({ items, total: rows.length });
  } catch (err) {
    res.status(500).json({ error: err.message || "domo query failed" });
  }
}
