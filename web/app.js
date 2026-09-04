/* Shared helpers for both pages.
 *
 * The site never computes a benchmark value. It loads the JSON that
 * `gpuidx export-web` derived from the archive and renders it. Anything that
 * looks like arithmetic here is presentation -- percentages, bar widths,
 * chart geometry -- never a price.
 */

const DATA = "data";

export const fmtUsd = (v, digits = 3) =>
  v === null || v === undefined ? "--" : `$${v.toFixed(digits)}`;

export const fmtPct = (v, digits = 0) =>
  v === null || v === undefined ? "--" : `${(v * 100).toFixed(digits)}%`;

export const fmtSigned = (v) =>
  v === null || v === undefined ? "--" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;

export const fmtNum = (v, digits = 3) =>
  v === null || v === undefined ? "--" : v.toFixed(digits);

/** 2026-09-03 -> "3 Sep" */
export function shortDate(iso) {
  if (!iso) return "--";
  const [y, m, d] = iso.split("-").map(Number);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d} ${months[m - 1]}`;
}

export function longDate(iso) {
  if (!iso) return "--";
  const [y, m, d] = iso.split("-").map(Number);
  const months = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"];
  return `${d} ${months[m - 1]} ${y}`;
}

export const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** Load the exported bundle. Fails loudly: a silent empty page is worse. */
export async function loadBundle(names) {
  const results = await Promise.all(
    names.map(async (name) => {
      const res = await fetch(`${DATA}/${name}.json`, { cache: "no-cache" });
      if (!res.ok) throw new Error(`${name}.json: HTTP ${res.status}`);
      return [name, await res.json()];
    })
  );
  return Object.fromEntries(results);
}

export function showError(node, err) {
  node.innerHTML = `<div class="empty">
    Could not load the exported data (${escapeHtml(err.message)}).<br>
    Run <code>uv run gpuidx export-web</code> and serve this directory over HTTP.
  </div>`;
}

/* ---------- theme ---------- */

export function initTheme() {
  const stored = (() => {
    try { return localStorage.getItem("gpuidx-theme"); } catch { return null; }
  })();
  if (stored === "dark" || stored === "light") {
    document.documentElement.setAttribute("data-theme", stored);
  }
  const button = document.querySelector(".theme-toggle");
  if (!button) return;
  const label = () => {
    const explicit = document.documentElement.getAttribute("data-theme");
    const dark = explicit
      ? explicit === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    button.textContent = dark ? "Light" : "Dark";
    button.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  };
  label();
  button.addEventListener("click", () => {
    const explicit = document.documentElement.getAttribute("data-theme");
    const dark = explicit
      ? explicit === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("gpuidx-theme", next); } catch { /* private mode */ }
    label();
  });
}

/* ---------- the fixing board, shared by both pages ---------- */

export function statusChip(status) {
  return `<span class="status ${status === "published" ? "published" : "withheld"}">${status}</span>`;
}

/**
 * Render the fixing table for one day.
 *
 * A withheld row shows the value the estimator *would* have printed, struck
 * through, next to the gate that stopped it. Hiding it would make withholding
 * look like missing data rather than a decision.
 */
export function renderBoard(latest, { onSelect = null, selected = null } = {}) {
  const codes = Object.keys(latest.indices);
  const rows = codes.map((code) => {
    const idx = latest.indices[code];
    const est = idx.estimate;
    const published = idx.status === "published";
    const contributing = est.providers.filter((p) => !p.screened_out).length;
    const screened = est.providers.length - contributing;

    const priceCell = published
      ? `<span class="price">${fmtUsd(idx.published_value)}</span>`
      : est.value !== null
        ? `<span class="price struck" title="the value the gates stopped">${fmtUsd(est.value)}</span>`
        : `<span class="price none">--</span>`;

    const failed = est.gates.filter((g) => !g.passed).map((g) => g.name);
    const reason = published
      ? ""
      : `<span class="reason">${escapeHtml(failed.join(", ") || idx.withheld_reason || "gated")}</span>`;

    return `<tr class="${onSelect ? "clickable" : ""} ${selected === code ? "is-selected" : ""}"
                data-code="${code}" ${onSelect ? 'tabindex="0"' : ""}>
      <td><span class="code">${code}</span></td>
      <td class="r">${priceCell}</td>
      <td class="r num">${contributing}${screened ? `<span style="color:var(--screened)"> +${screened}</span>` : ""}</td>
      <td class="r num">${est.providers.reduce((n, p) => n + (p.screened_out ? 0 : p.quote_count), 0)}</td>
      <td class="r num">${fmtNum(est.dispersion)}</td>
      <td>${statusChip(idx.status)} ${reason}</td>
    </tr>`;
  });

  const html = `<div class="scroll"><table>
    <thead><tr>
      <th>index</th>
      <th class="r">fixing</th>
      <th class="r">providers</th>
      <th class="r">obs</th>
      <th class="r">dispersion</th>
      <th>status</th>
    </tr></thead>
    <tbody>${rows.join("")}</tbody>
  </table></div>`;

  return { html, codes };
}

export function wireBoard(container, onSelect) {
  container.querySelectorAll("tr[data-code]").forEach((tr) => {
    const fire = () => onSelect(tr.dataset.code);
    tr.addEventListener("click", fire);
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fire(); }
    });
  });
}
