/* ForestStudio XP: top-level views (Build / Build queue / Disk space / Settings) and the status bar.
   Uses helpers from app.js: api(), $, el, fmt, fmtMB, fmtDur, msg, map, S. */
"use strict";

let currentView = "build";
let selectedJob = null;
let worksTimer = null;

function showView(name) {
  currentView = name;
  document.querySelectorAll("#views button").forEach(b => b.classList.toggle("on", b.dataset.view === name));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("on", v.id === "view-" + name));
  document.querySelectorAll("header .hdr").forEach(l => l.style.display = name === "build" ? "" : "none");
  if (name === "build" && typeof map !== "undefined" && map) setTimeout(() => map.invalidateSize(), 50);
  if (name === "works") refreshWorks();
  if (name === "disk") refreshDisk();
  if (name === "appsettings") loadAppSettings();
}

/* ---------------- status bar ---------------- */
async function refreshStatusbar() {
  try {
    const st = await api().get_app_settings();
    const ck = await api().setup_checks();
    const xp = st.xplane_root || "not set";
    $("#sbXp").textContent = `X-Plane: ${xp.length > 42 ? "…" + xp.slice(-40) : xp}`;
    $("#sbXp").title = xp;
    const sb = $("#sbChecks");
    sb.innerHTML = "";
    sb.append("Checks: ", el("span", { class: "pill " + (ck.ok === ck.total ? "ok" : "bad") }, `${ck.ok}/${ck.total} ok`));
    sb.title = Object.entries(ck.items).map(([k, v]) => `${v ? "✓" : "✗"} ${k}`).join("\n");
    sb.onclick = () => showView("appsettings");
  } catch (e) { /* dev server stopped */ }
}

async function refreshStatusbarDisk() {
  try {
    const u = await api().disk_usage();
    $("#sbDisk").textContent = `Data kept: ${fmtMB(u.total_mb)} · free ${u.free_gb ?? "?"} GB`;
    $("#sbDisk").onclick = () => showView("disk");
  } catch (e) { /* ignore */ }
}

/* ---------------- Build queue ---------------- */
const STATUS_TXT = { running: "running", done: "done", partial: "partly failed", failed: "failed",
  cancelled: "cancelled", interrupted: "interrupted" };
const fmtTime = (t) => new Date(t * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" });

async function refreshWorks() {
  const jobs = await api().list_jobs();
  const q = ($("#jobSearch").value || "").trim().toLowerCase();
  const list = $("#jobList"); list.innerHTML = "";
  const shown = jobs.filter(j => !q || j.tiles.some(t => t.toLowerCase().includes(q)) || j.id.includes(q));
  if (!shown.length) list.append(el("p", { class: "hint" }, jobs.length ? "No job matches." : "No builds yet."));
  for (const j of shown) {
    const tiles = j.tiles.length > 3 ? `${j.tiles.slice(0, 3).join(", ")} +${j.tiles.length - 3}` : j.tiles.join(", ");
    const card = el("div", { class: "jobcard" + (j.id === selectedJob ? " on" : ""), onclick: () => openJob(j.id) },
      el("div", { class: "row between" }, el("b", { class: "mono" }, tiles),
        el("span", { class: `jbadge ${j.status}` }, STATUS_TXT[j.status] || j.status)),
      el("div", { class: "hint" }, `${j.profile} · ${j.region} · ${fmtTime(j.created)} · ${j.tiles.length} tile(s)`),
      el("div", { class: "mini" }, el("div", { style: `width:${Math.round(j.progress * 100)}%` })));
    list.append(card);
  }
  // The selected job may have been deleted (Delete job / clear all): forget it.
  if (selectedJob && !jobs.some(j => j.id === selectedJob)) selectedJob = null;
  if (!selectedJob && shown.length) openJob(shown[0].id);
  else if (selectedJob) renderJob(await api().get_job(selectedJob));
  else showNoJob(jobs.length ? "No job matches the search." : "");
  clearTimeout(worksTimer);
  if (currentView === "works" && jobs.some(j => j.status === "running")) worksTimer = setTimeout(refreshWorks, 1500);
}

function showNoJob(text) {
  const box = $("#jobDetail"); box.innerHTML = "";
  box.append(el("p", { class: "hint" }, text
    || "No builds in the queue. Build some tiles and every run shows up here with its steps, progress, log and report."));
}

function openJob(id) {
  selectedJob = id;
  refreshWorks();  // re-renders the list (highlight) and the details
}

function stepCell(s, label) {
  const cls = s.status;
  let note = s.status === "done" ? `done${s.seconds != null ? " · " + fmtDur(s.seconds) : ""}`
    : s.status === "running" ? "running…" : s.status === "failed" ? "failed" : s.status === "skipped" ? "skipped" : "waiting";
  if (s.note && s.status !== "done") note += ` — ${s.note}`;
  return el("div", { class: `step ${cls}`, title: s.note || "" },
    el("div", { class: "sl" }, el("i", { class: "dot" }), label),
    el("div", { class: "bar" }, el("div", {})),
    el("div", { class: "sn" }, note));
}

async function renderJob(j) {
  const box = $("#jobDetail");
  // The details re-render every 1.5 s while a job runs: keep the log open and where it was scrolled.
  const oldDet = box.querySelector("details.logbox"), oldPre = box.querySelector("pre.joblog");
  const keepLog = oldDet && oldDet.open && j && oldDet.dataset.job === j.id;
  const wasAtBottom = keepLog && oldPre.scrollTop + oldPre.clientHeight >= oldPre.scrollHeight - 30;
  const oldScroll = keepLog ? oldPre.scrollTop : 0;
  box.innerHTML = "";
  if (!j) { box.append(el("p", { class: "hint" }, "This job no longer exists.")); return; }
  const head = el("div", { class: "jobhead" },
    el("h2", {}, `Job ${j.id}`), el("span", { class: `jbadge ${j.status}` }, STATUS_TXT[j.status] || j.status),
    el("span", { class: "hint" }, `${j.profile} · ${j.region} · ${j.workers} at a time`));
  const del = el("button", { class: "ghost danger", title: "Delete this job's record and log" }, "Delete job");
  del.onclick = async () => {
    if (!confirm("Delete this job's record and log? Built tiles are not affected.")) return;
    await busy(del, "Deleting…", async () => {
      const r = await api().delete_job(j.id);
      if (!r.ok) { alert(r.error); return; }
      selectedJob = null;
      await refreshWorks();   // details switch to the next job, or an empty message
    });
  };
  box.append(el("div", { class: "row between" }, head, j.status === "running" ? "" : del));
  box.append(el("div", { class: "row" }, el("span", {}, el("u", {}, "Progress"), ` ${Math.round(j.progress * 100)}%`),
    el("span", {}, el("u", {}, "Elapsed"), ` ${fmtDur(j.elapsed)}`),
    el("span", {}, `Built ${j.built} · Failed ${j.failed} · ${j.tiles.length} tile(s)`)));
  box.append(el("div", { class: "jprogress" }, el("div", { style: `width:${Math.round(j.progress * 100)}%` })));

  const grid = el("div", { class: "tilegrid" });
  for (const t of j.tiles) {
    const row = el("div", { class: "tilerow" }, el("div", { class: "tname mono" }, t));
    for (const s of j.step_names) row.append(stepCell(j.steps[t][s], j.step_labels[s]));
    const r = j.results[t];
    row.append(el("div", { class: "tres" }, r ? (r.ok
      ? `${fmt(r.forest_km2)} km² · ${fmt(r.estimated_trees)} trees · ${fmt(r.dsf_mb)} MB`
      : "failed") : ""));
    grid.append(row);
  }
  box.append(grid);

  const det = el("details", { class: "logbox" }, el("summary", {}, `Log (${j.log_lines} line(s))`));
  det.dataset.job = j.id;
  const pre = el("pre", { class: "joblog" });
  det.append(pre);
  const loadLog = async (stickBottom, scroll) => {
    pre.textContent = (await api().get_job_log(j.id)).join("\n");
    pre.scrollTop = stickBottom ? pre.scrollHeight : scroll;
  };
  det.addEventListener("toggle", () => { if (det.open && !pre.textContent) loadLog(true, 0); });
  box.append(det);
  if (keepLog) {
    pre.textContent = oldPre.textContent;   // no flicker while the new lines load
    det.open = true;
    pre.scrollTop = wasAtBottom ? pre.scrollHeight : oldScroll;
    loadLog(wasAtBottom, oldScroll);
  }

  const rep = j.report;
  const left = el("table", { class: "kv" });
  for (const [k, v] of [["Tiles", rep.tiles], ["Built", rep.built], ["Failed", rep.failed],
    ["Elapsed", fmtDur(j.elapsed)], ["Forest area", `${fmt(rep.forest_km2)} km²`],
    ["Estimated trees", fmt(rep.trees)], ["Forest pieces", fmt(rep.polygons)], ["Written (.dsf)", `${fmt(rep.dsf_mb)} MB`]])
    left.append(el("tr", {}, el("td", {}, k), el("td", {}, String(v))));
  const steps = el("table", { class: "steps" },
    el("tr", {}, ...["Step", "Done", "Failed", "Skipped", "Total time", "Average"].map(h => el("th", {}, h))));
  for (const s of j.step_names) {
    const p = rep.per_step[s];
    steps.append(el("tr", {}, el("td", {}, j.step_labels[s]), el("td", {}, String(p.done)), el("td", {}, String(p.failed)),
      el("td", {}, String(p.skipped)), el("td", {}, fmtDur(p.seconds_total)), el("td", {}, p.seconds_avg != null ? fmtDur(p.seconds_avg) : "–")));
  }
  const errs = Object.entries(j.results).filter(([, r]) => !r.ok);
  const errBox = el("div", { class: "errs" });
  if (errs.length) {
    errBox.append(el("h3", {}, "Errors"));
    for (const [t, r] of errs) errBox.append(el("div", {}, el("b", { class: "mono" }, t), el("pre", {}, r.error || "failed")));
  }
  box.append(el("div", { class: "report" }, el("div", {}, el("h3", {}, "Final report"), left),
    el("div", {}, el("h3", {}, "Steps"), steps), errBox));
}

/* ---------------- busy buttons (spinner while an action runs) ---------------- */
async function busy(btn, label, fn) {
  if (!btn || btn.classList.contains("busy")) return;
  const old = btn.innerHTML, wasDisabled = btn.disabled;
  btn.classList.add("busy"); btn.disabled = true;
  btn.innerHTML = "";
  btn.append(el("span", { class: "spinner" }), label);
  try { return await fn(); }
  finally { btn.classList.remove("busy"); btn.innerHTML = old; btn.disabled = wasDisabled; }
}

/* ---------------- Disk space ---------------- */
async function refreshDisk() {
  const body = $("#diskBody");
  if (!body.children.length) body.append(el("div", { class: "loading" }, el("span", { class: "spinner" }), "Measuring disk usage…"));
  body.classList.add("dim");
  let u;
  try { u = await api().disk_usage(); } finally { body.classList.remove("dim"); }
  body.innerHTML = "";
  const max = Math.max(1, ...u.categories.map(c => c.mb));
  body.append(el("div", { class: "disktotal" },
    el("div", {}, el("span", { class: "big" }, fmtMB(u.total_mb)), " used by ForestStudio"),
    el("div", { class: "hint" }, `Free on this drive: ${u.free_gb ?? "?"} of ${u.disk_total_gb ?? "?"} GB · ${u.project}`)));
  const t = el("table", { class: "disktable" });
  for (const c of u.categories) {
    t.append(el("tr", { class: c.key === "leftovers" && c.mb > 0 ? "warnrow" : "" },
      el("td", {}, el("b", {}, c.label), el("div", { class: "hint" }, c.detail)),
      el("td", { class: "barcell" }, el("div", { class: "dbar" }, el("div", { style: `width:${(100 * c.mb / max).toFixed(1)}%` }))),
      el("td", { class: "num" }, fmtMB(c.mb))));
  }
  body.append(t);

  const act = el("div", { class: "cleanups" }, el("h3", {}, "Clean up"));
  const btn = (label, kind, note, enabled) => {
    const b = el("button", { class: "ghost" + (kind === "unbuilt_tile_data" ? " danger" : "") }, label);
    b.disabled = !enabled;
    b.onclick = async () => {
      if (!confirm(`${label}?\n\n${note}`)) return;
      const r = await busy(b, "Deleting…", () => api().disk_clean(kind));
      const text = r.ok ? `Done${r.freed_mb != null ? ` — freed ${fmtMB(r.freed_mb)}` : ""}${r.deleted != null ? ` (${r.deleted} item(s))` : ""}.` : r.error;
      await busy($("#diskRefresh"), "Refreshing…", refreshDisk);
      msg("#diskMsg", text, !r.ok);
      refreshStatusbarDisk();
    };
    return el("div", { class: "row" }, b, el("span", { class: "hint" }, note));
  };
  const cl = u.cleanable;
  act.append(
    btn(`Delete leftovers (${fmtMB(cl.leftovers.mb)})`, "leftovers",
      "Debug decodes, abandoned downloads, kept DSF text and unfinished compiles. Nothing you need.", cl.leftovers.items > 0),
    btn(`Delete data of tiles not built (${fmtMB(cl.unbuilt_tile_data.mb)})`, "unbuilt_tile_data",
      "Downloaded satellite/OSM data of tiles that have no built forest. It is downloaded again if you build them.",
      cl.unbuilt_tile_data.items > 0),
    btn("Clear job history", "jobs", "Deletes the records and logs in the Build queue. Built tiles are not affected.", true),
    el("div", { id: "diskMsg", class: "msg" }));
  body.append(act);

  if (u.largest_tiles.length) {
    const lt = el("table", { class: "kv" });
    for (const x of u.largest_tiles) lt.append(el("tr", {}, el("td", { class: "mono" }, x.tile), el("td", {}, fmtMB(x.mb))));
    body.append(el("h3", {}, "Largest downloaded tiles"), lt,
      el("p", { class: "hint" }, "To remove a tile's data, select it on the Build map and use Delete selected tiles."));
  }
}

/* ---------------- App settings ---------------- */
function renderChecks(box, check, okText) {
  box.innerHTML = "";
  if (check.ok) { box.append(el("div", { class: "ck ok" }, "✓ ", okText)); return; }
  for (const p of check.problems) box.append(el("div", { class: "ck bad" }, "✗ ", p));
}

let checkTimer = null;
async function liveCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(async () => {
    const r = await api().check_paths($("#setXp").value);
    renderChecks($("#setXpCheck"), r.xplane_check, "X-Plane 12 found (Custom Scenery, forest library and base mesh present)");
  }, 250);
}

async function loadAppSettings() {
  const s = await api().get_app_settings();
  $("#setXp").value = s.xplane_root;
  renderChecks($("#setXpCheck"), s.xplane_check, "X-Plane 12 found (Custom Scenery, forest library and base mesh present)");
  const ck = await api().setup_checks();
  const box = $("#setupChecks"); box.innerHTML = "";
  for (const [k, v] of Object.entries(ck.items)) box.append(el("div", { class: "ck " + (v ? "ok" : "bad") }, v ? "✓ " : "✗ ", k));
  if (!s.saved) msg("#setMsg", "Not saved yet: these paths were detected automatically. Press Save to keep them.");
}

async function initViews() {
  document.querySelectorAll("#views button").forEach(b => b.onclick = () => showView(b.dataset.view));
  $("#jobSearch").oninput = refreshWorks;
  $("#jobsClear").onclick = async () => {
    if (!confirm("Delete all finished jobs' records and logs? Built tiles are not affected.")) return;
    await busy($("#jobsClear"), "", async () => { await api().clear_jobs(); selectedJob = null; await refreshWorks(); });
  };
  $("#diskRefresh").onclick = () => busy($("#diskRefresh"), "Refreshing…", refreshDisk);
  $("#setXp").oninput = liveCheck;
  $("#setXpPick").onclick = async () => { const p = await api().pick_path("folder");
    if (p) { $("#setXp").value = p; liveCheck(); } else msg("#setMsg", "Browse works in the desktop app; type or paste the path here.", true); };
  $("#setXpDetect").onclick = () => busy($("#setXpDetect"), "Detecting…", async () => { const s = await api().get_app_settings();
    if (s.detected_xplane) { $("#setXp").value = s.detected_xplane; liveCheck(); } else msg("#setMsg", "No X-Plane 12 found automatically.", true); });
  $("#setSave").onclick = () => busy($("#setSave"), "Saving…", async () => {
    const r = await api().save_app_settings($("#setXp").value);
    if (!r.ok) { msg("#setMsg", r.error, true); return; }
    S = await api().get_state();
    await refreshStatus(); refreshStatusbar(); await loadAppSettings();
    msg("#setMsg", "Saved. ForestStudio now uses these locations.");
  });
  refreshStatusbar(); refreshStatusbarDisk();
  setInterval(() => { refreshStatusbarDisk(); }, 60000);
  // First run: no usable X-Plane folder → go straight to Settings.
  const ck = await api().setup_checks();
  if (!ck.items["X-Plane 12 folder"]) showView("appsettings");
}
