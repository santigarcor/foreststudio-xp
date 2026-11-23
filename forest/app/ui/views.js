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
  if (name === "data") refreshData();
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
const KIND_TXT = { data: "Download data", osm_update: "Update OSM data" };   // build jobs show the profile
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
      el("div", { class: "row between" }, el("b", { class: "mono" }, tiles || "country-wide file"),
        el("span", { class: `jbadge ${j.status}` }, STATUS_TXT[j.status] || j.status)),
      el("div", { class: "hint" }, `${KIND_TXT[j.kind] || j.profile} · ${j.region} · ${fmtTime(j.created)} · ${j.tiles.length} tile(s)`),
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
    el("h2", {}, `${KIND_TXT[j.kind] ? KIND_TXT[j.kind] + " · " : ""}Job ${j.id}`), el("span", { class: `jbadge ${j.status}` }, STATUS_TXT[j.status] || j.status),
    el("span", { class: "hint" }, KIND_TXT[j.kind] ? j.region : `${j.profile} · ${j.region} · ${j.workers} at a time`));
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
    el("span", {}, `${KIND_TXT[j.kind] ? "Done" : "Built"} ${j.built} · Failed ${j.failed} · ${j.tiles.length} tile(s)`)));
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

/* ---------------- Data (downloaded source data) ---------------- */
const fmtDay = (t) => t ? new Date(t * 1000).toLocaleDateString([], { dateStyle: "medium" }) : "–";
const fmtWhen = (t) => t ? new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "–";
let dataChecked = new Set();

async function startDataJob(kind, tiles, force) {
  const r = await api().start_data_job(kind, tiles || [], $("#region").value || S.default_region, !!force);
  if (!r.ok) { msg("#dataMsg2", r.error, true); return r; }
  window._buildTotal = Math.max(1, r.tiles);
  if (typeof setRunning === "function") setRunning(true);
  if (typeof poll === "function") poll();
  msg("#dataMsg2", "Started. Progress and log are in the Build queue.");
  refreshData();
  waitForDataJob();
  return r;
}

async function waitForDataJob() {   // refresh this page when the download finishes
  let j;
  try { j = await api().job_status(1e12); }
  catch (e) { setTimeout(waitForDataJob, 3000); return; }   // keep waiting after a failed call
  if (j.running) { setTimeout(waitForDataJob, 2000); return; }
  if (currentView === "data") refreshData();
  if (typeof refreshStatus === "function") refreshStatus();
}

async function refreshData() {
  const d = await api().data_status($("#region").value || S.default_region);
  const busyNow = d.busy;
  // OpenStreetMap card
  const o = d.osm, oc = $("#dcOsm"); oc.innerHTML = "";
  oc.append(el("h3", {}, "OpenStreetMap (Geofabrik country file)"));
  oc.append(el("p", { class: "hint" }, "Roads, buildings, water, airports, parks, single trees and plantations. "
    + "Read once for all tiles of a build."));
  if (o.exists) {
    oc.append(el("div", {}, `${fmtMB(o.mb)} · ${o.remote_date ? `map data from ${fmtWhen(o.remote_date)} · ` : ""}downloaded ${fmtWhen(o.downloaded)}`));
    const chk = el("button", { class: "ghost" }, "Check for update");
    const upd = el("button", { class: "primary hidden" }, "Update OSM data");
    const res = el("div", { class: "hint" });
    chk.disabled = upd.disabled = busyNow;
    chk.onclick = () => busy(chk, "Checking…", async () => {
      const r = await api().check_osm_update($("#region").value || S.default_region);
      if (!r.ok) { res.className = "hint err"; res.textContent = r.error; return; }
      res.className = "hint";
      if (r.newer) {
        res.textContent = `A newer file is available: map data from ${fmtWhen(r.remote_date)}${r.remote_size ? ` (${fmtMB(r.remote_size / 1e6)})` : ""}.`;
        upd.classList.remove("hidden");
      } else res.textContent = "You have the newest file.";
    });
    upd.onclick = () => {
      const n = d.tiles.filter(t => t.osm).length;
      if (!confirm(`Download the newer OpenStreetMap file and re-read it for ${n} tile(s)?\n\n`
        + "This takes a few minutes. Built tiles then show as outdated until you rebuild them "
        + "(Build → Select outdated).")) return;
      busy(upd, "Starting…", () => startDataJob("osm"));
    };
    oc.append(el("div", { class: "row" }, chk, upd), res);
  } else {
    oc.append(el("div", { class: "hint err" }, "Not downloaded yet (about 335 MB). It's downloaded automatically on the first build."));
    const dl = el("button", { class: "primary" }, "Download now");
    dl.disabled = busyNow;
    dl.onclick = () => busy(dl, "Starting…", () => startDataJob("osm"));
    oc.append(el("div", { class: "row" }, dl));
  }
  // Ecoregions card
  const e = d.ecoregions, ec = $("#dcEco"); ec.innerHTML = "";
  ec.append(el("h3", {}, "Ecoregions (biomes)"));
  ec.append(el("p", { class: "hint" }, "RESOLVE Ecoregions 2017: decides which forest type (rainforest, montane, dry forest…) grows where."));
  if (e.exists || e.ready) {
    ec.append(el("div", {}, e.exists ? `${fmtMB(e.mb)} · downloaded ${fmtDay(e.downloaded)}${e.ready ? "" : ` · the ${S.region_info[$("#region").value || S.default_region].label} cut-out is made on the next build`}`
      : `${S.region_info[$("#region").value || S.default_region].label} cut-out ready`));
  } else {
    ec.append(el("div", { class: "hint err" }, "Not downloaded yet (about 150 MB). It's downloaded automatically on the first build."));
    const dl = el("button", { class: "primary" }, "Download now");
    dl.disabled = busyNow;
    dl.onclick = () => busy(dl, "Starting…", () => startDataJob("ecoregions"));
    ec.append(el("div", { class: "row" }, dl));
  }
  // Tiles table
  const names = new Set(d.tiles.map(t => t.tile));
  dataChecked = new Set([...dataChecked].filter(n => names.has(n)));
  $("#dataCount").textContent = `(${d.tiles.length} · ${fmtMB(d.tiles.reduce((a, t) => a + t.mb, 0))})`;
  const nSel = (typeof selected !== "undefined") ? selected.size : 0;
  $("#dataFetchSel").textContent = `Download for map selection (${nSel})`;
  $("#dataFetchSel").disabled = busyNow || !nSel;
  const box = $("#dataTiles"); box.innerHTML = "";
  if (!d.tiles.length) {
    box.append(el("p", { class: "hint" }, "No tile data yet. Select tiles on the Build map and build them, or use Download for map selection."));
    updateDataButtons(); return;
  }
  const all = el("input", { type: "checkbox", title: "Check all" });
  all.checked = dataChecked.size === d.tiles.length;
  all.onchange = () => { dataChecked = all.checked ? new Set(names) : new Set(); refreshData(); };
  const tbl = el("table", { class: "datatable" },
    el("tr", {}, el("th", {}, all), ...["Tile", "Satellite", "OpenStreetMap", "Elevation", "Size", "Built"].map(h => el("th", {}, h))));
  const yes = (v) => el("span", { class: v ? "ok" : "bad" }, v ? "✓" : "✗");
  for (const t of d.tiles) {
    const cb = el("input", { type: "checkbox" });
    cb.checked = dataChecked.has(t.tile);
    cb.onchange = () => { cb.checked ? dataChecked.add(t.tile) : dataChecked.delete(t.tile); updateDataButtons(); };
    const osmCell = t.osm ? el("span", { class: t.osm_stale ? "warn" : "" }, fmtDay(t.osm_date),
      t.osm_stale ? " · older than the country file" : "") : yes(false);
    tbl.append(el("tr", {}, el("td", {}, cb), el("td", { class: "mono" }, t.tile), el("td", {}, yes(t.satellite)),
      el("td", {}, osmCell), el("td", {}, yes(t.elevation)), el("td", {}, fmtMB(t.mb)), el("td", {}, t.built ? "yes" : "–")));
  }
  box.append(tbl);
  updateDataButtons();
}

function updateDataButtons() {
  $("#dataSelMap").disabled = !dataChecked.size;
  $("#dataDelete").disabled = !dataChecked.size;
  $("#dataDelete").textContent = dataChecked.size ? `Delete data of ${dataChecked.size} tile(s)…` : "Delete checked data…";
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

/* ---------------- data folder ---------------- */
let dataInfo = null, dataTimer = null, dataPlan = null;
const SOURCE_TXT = { default: "default", custom: "chosen by you", env: "set by FORESTSTUDIO_HOME" };

async function loadDataFolder() {
  dataInfo = await api().data_folder_info();
  $("#dataDefault").textContent = dataInfo.default;
  $("#dataFolder").value = dataInfo.current;
  $("#dataSource").textContent = SOURCE_TXT[dataInfo.source] || "";
  for (const id of ["#dataFolder", "#dataPick", "#dataDefaultBtn"]) $(id).disabled = dataInfo.locked;
  $("#dataDefaultBtn").disabled = dataInfo.locked || dataInfo.is_default;
  $("#dataCheck").textContent = dataInfo.locked ? "Remove the FORESTSTUDIO_HOME environment variable to change it here." : "";
  $("#dataApply").disabled = true; dataPlan = null;
}

function checkDataFolder() {
  clearTimeout(dataTimer);
  $("#dataApply").disabled = true; dataPlan = null;
  dataTimer = setTimeout(async () => {
    const r = await api().check_data_folder($("#dataFolder").value);
    const box = $("#dataCheck"); box.className = "hint " + (r.ok ? "" : "err");
    if (!r.ok) { box.textContent = "✗ " + r.error; return; }
    if (r.same) { box.textContent = r.message; return; }
    box.textContent = (r.note ? r.note + " " : "") + r.message;
    $("#dataApply").textContent = r.has_data ? "Switch to this folder" : "Move data here";
    $("#dataApply").disabled = false; dataPlan = r;
  }, 300);
}

async function applyDataFolder() {
  if (!dataPlan) return;
  const what = dataPlan.has_data ? `Switch ForestStudio to the data in\n${dataPlan.target}?`
    : `Move your data (${dataPlan.size_gb} GB) to\n${dataPlan.target}?`;
  if (!confirm(`${what}\n\nThe X-Plane pack link is updated too. ForestStudio restarts afterwards.`)) return;
  const r = await busy($("#dataApply"), dataPlan.has_data ? "Switching…" : "Moving…",
    () => api().change_data_folder($("#dataFolder").value));
  if (!r.ok) { msg("#dataMsg", r.error, true); return; }
  msg("#dataMsg", `Done: ${r.notes.join(" · ")}. Restart ForestStudio to use the new folder.`);
  $("#dataApply").disabled = true;
  for (const id of ["#dataFolder", "#dataPick", "#dataDefaultBtn"]) $(id).disabled = true;
  $("#dataRestart").classList.remove("hidden");
}

async function loadCredits() {
  const box = $("#aboutCredits");
  if (box.children.length) return;
  const c = await api().credits();
  const table = (rows, withUse) => {
    const t = el("table", { class: "datatable credits" });
    for (const r of rows) t.append(el("tr", {},
      el("td", {}, el("b", {}, r.name), withUse && r.use ? el("div", { class: "hint" }, r.use) : ""),
      el("td", {}, r.attribution, el("div", { class: "hint" }, `${r.license} · ${r.url}`))));
    return t;
  };
  box.append(el("h4", {}, "Data"), table(c.data, true), el("h4", {}, "Software"), table(c.software, false));
}

async function loadAppSettings() {
  loadDataFolder();
  loadCredits();
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
  $("#dataRefresh").onclick = () => busy($("#dataRefresh"), "Refreshing…", refreshData);
  $("#dataFetchSel").onclick = () => {
    const tiles = [...selected].sort();
    if (!confirm(`Download data for ${tiles.length} tile(s) without building them?`)) return;
    busy($("#dataFetchSel"), "Starting…", () => startDataJob("tiles", tiles));
  };
  $("#dataSelMap").onclick = () => {
    for (const n of dataChecked) selected.add(n);
    renderSelection(); drawTiles(); showView("build");
  };
  $("#dataDelete").onclick = async () => {
    const tiles = [...dataChecked].sort();
    if (!confirm(`Delete the downloaded data of ${tiles.length} tile(s)?\n\nBuilt tiles stay in X-Plane. `
      + "The data is downloaded again automatically if you rebuild them.")) return;
    await busy($("#dataDelete"), "Deleting…", async () => {
      const r = await api().delete_tiles(tiles, ["cache"], $("#region").value || S.default_region, "");
      msg("#dataMsg2", r.ok === false ? r.error : `Deleted data of ${tiles.length} tile(s).`, r.ok === false);
      dataChecked.clear(); await refreshData(); refreshStatus(); refreshStatusbarDisk();
    });
  };
  $("#setXp").oninput = liveCheck;
  $("#dataFolder").oninput = checkDataFolder;
  $("#dataPick").onclick = async () => { const p = await api().pick_path("folder");
    if (p) { $("#dataFolder").value = p; checkDataFolder(); } else msg("#dataMsg", "Browse works in the desktop app; type or paste the path here.", true); };
  $("#dataDefaultBtn").onclick = () => { $("#dataFolder").value = dataInfo.default; checkDataFolder(); };
  $("#dataApply").onclick = applyDataFolder;
  $("#dataRestart").onclick = () => busy($("#dataRestart"), "Restarting…", async () => {
    const r = await api().restart_app();
    if (!r.ok) msg("#dataMsg", r.error, true);
  });
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
