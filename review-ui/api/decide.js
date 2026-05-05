// POST /api/decide — record a human decision into MAP | Employee Crosswalk Decisions.
// We append to a separate "decisions" dataset rather than mutating the matcher output;
// the matcher reads decisions and respects them on the next run (manual rows pinned).

import axios from "axios";

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "method not allowed" });
  }
  const { adp_associate_id, em_employee_id, decision } = req.body || {};
  if (!adp_associate_id || !decision) {
    return res.status(400).json({ error: "adp_associate_id and decision required" });
  }

  const host = process.env.DOMO_API_HOST;
  const token = process.env.DOMO_DEVELOPER_TOKEN;
  const decisionsDataset = process.env.DATASET_CROSSWALK_DECISIONS;
  if (!host || !token || !decisionsDataset) {
    return res.status(500).json({ error: "server not configured" });
  }

  const ts = new Date().toISOString();
  const csv =
    "adp_associate_id,em_employee_id,decision,decided_at\n" +
    [adp_associate_id, em_employee_id || "", decision, ts]
      .map(v => String(v).replaceAll('"', '""'))
      .map(v => /[",\n]/.test(v) ? `"${v}"` : v)
      .join(",") + "\n";

  try {
    await axios.put(
      `https://${host}/api/data/v3/datasources/${decisionsDataset}/uploads`,
      csv,
      {
        params: { appendData: "true" },
        headers: {
          "X-DOMO-Developer-Token": token,
          "Content-Type": "text/csv",
        },
        timeout: 60000,
      }
    );
    res.status(200).json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message || "domo upload failed" });
  }
}
