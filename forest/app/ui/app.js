/* Forest Studio UI. Talks to Python through window.pywebview.api (desktop window).
   For UI development only, forest/app/devserver.py exposes the same API over HTTP. */
"use strict";

const devApi = new Proxy({}, {
  get: (_, method) => (...args) =>
    fetch("/api/" + method, { method: "POST", body: JSON.stringify(args) }).then(r => r.json()),
});
const api = () => (window.pywebview && window.pywebview.api) ? window.pywebview.api : devApi;
const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k.startsWith("on")) e[k] = v; else e.setAttribute(k, v);
  }
  for (const k of kids) e.append(k);
  return e;
};
const fmt = (n) => n == null ? "–" : Number(n).toLocaleString("en-US", { maximumFractionDigits: 1 });

let S = null;               // static state from get_state
let status = {};            // tile -> status
let selected = new Set();
let lastClicked = null;
let profile = null, profileSaved = null;
let rules = null;
const rects = {}, labels = {}, overlays = {};
let map, baseLayers, logSeq = 0, polling = null;

/* ---------------- map ---------------- */
function initMap() {
  map = L.map("map", { zoomSnap: 0.5, worldCopyJump: false });
  fitRegion();
  // Forest overlays above tile fills, never catching clicks.
  map.createPane("forest");
  map.getPane("forest").style.zIndex = 450;
  map.getPane("forest").style.pointerEvents = "none";
  baseLayers = {
    sat: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 18, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" }),
    osm: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      { maxZoom: 18, attribution: "© OpenStreetMap contributors" }),
  };
  baseLayers.sat.addTo(map);
  document.querySelectorAll("#basemap button").forEach(b => b.onclick = () => {
    document.querySelectorAll("#basemap button").forEach(x => x.classList.toggle("on", x === b));
    Object.values(baseLayers).forEach(l => map.removeLayer(l));
    baseLayers[b.dataset.b].addTo(map).bringToBack();
  });
  map.on("zoomend", () => { updateLabels(); Object.keys(rects).forEach(n => rects[n].setStyle(tileStyle(n))); });
  $("#showOverlay").onchange = () => { refreshOverlays(); renderForestLegend(); };
  $("#overlayOpacity").oninput = () => Object.values(overlays).forEach(o => o.setOpacity(+$("#overlayOpacity").value));
}

/* The map shows every region's tiles; each tile is built with the forest types of its own region
   (status[tile].region). The Forest types tab edits one region's types at a time: region(). */
const region = () => $("#region").value || S.default_region;
const regionInfo = () => S.region_info[region()] || {};
const regionLabel = (n) => (S.region_info[n] || {}).label || n;
const mainAssets = () => (regionInfo().evergreen_only === false ? S.forests : S.evergreen);
const allAssets = () => (regionInfo().evergreen_only === false ? S.forests_all : S.evergreen_all);

function fitRegion(name = S.default_region) {
  const ri = S.region_info[name] || {};
  if (ri.tiles && ri.tiles.length) {            // regions added from Geofabrik: their own tiles
    const ll = ri.tiles.map(n => [parseInt(n.slice(0, 3), 10), parseInt(n.slice(3), 10)]);
    const s = Math.min(...ll.map(x => x[0])), n = Math.max(...ll.map(x => x[0])) + 1;
    const w = Math.min(...ll.map(x => x[1])), e = Math.max(...ll.map(x => x[1])) + 1;
    map.fitBounds([[s, w], [n, e]]);
    return;
  }
  const [w, s, e, n] = ri.bounds || [-82, -5, -66, 13];
  map.fitBounds([[s, w], [n, e]]);
}

/* The list of regions changed (added / removed): tiles that no region offers any more go away. */
function dropTiles(keep) {
  for (const n of Object.keys(rects)) if (!keep[n]) {
    map.removeLayer(rects[n]); delete rects[n];
    map.removeLayer(labels[n]); delete labels[n];
    reloadOverlay(n); selected.delete(n);
  }
}

async function fillRegionSelects() {
  await fillSelect("#region", S.regions, S.regions.includes(region()) ? region() : S.default_region, regionLabel);
  const go = $("#goRegion"), keep = go.value;
  go.innerHTML = "";
  go.append(el("option", { value: "" }, "Go to region…"));
  for (const n of S.regions) go.append(el("option", { value: n }, regionLabel(n)));
  go.value = S.regions.includes(keep) ? keep : "";
}

function tileStyle(name) {
  const s = status[name] || {};
  const sel = selected.has(name);
  let color = "#ffffff", weight = 0.6, opacity = 0.18, dash = null;
  if (s.land) { opacity = 0.35; }
  if (s.cached) { color = "#8a7fd6"; opacity = 0.95; dash = null; weight = 1.4; }
  if (s.built) { color = "#3fa0e0"; opacity = 1; weight = 1.8; dash = null; }
  if (s.installed) { color = "#4cc38a"; opacity = 1; weight = 2.2; dash = null; }
  if (s.built && s.outdated && s.outdated.length) { color = "#f08c3a"; weight = 2.2; }   // settings/data changed
  return {
    color, weight, opacity, dashArray: dash,
    fillColor: sel ? "#f5c542" : "#000",
    fillOpacity: sel ? (map && map.getZoom() >= 8 ? 0.08 : 0.3) : (s.land ? 0.04 : 0.0),
    ...(sel ? { color: "#f5c542", weight: 2.5, opacity: 1, dashArray: null } : {}),
  };
}

function drawTiles() {
  for (const name of Object.keys(status)) {
    const lat = parseInt(name.slice(0, 3), 10), lon = parseInt(name.slice(3), 10);
    if (!rects[name]) {
      rects[name] = L.rectangle([[lat, lon], [lat + 1, lon + 1]], tileStyle(name)).addTo(map);
      rects[name].on("click", (e) => onTileClick(name, e.originalEvent.shiftKey));
      labels[name] = L.marker([lat + 0.5, lon + 0.5], {
        interactive: false, icon: L.divIcon({ className: "tilelabel", html: name, iconSize: [60, 14], iconAnchor: [30, 7] }),
      });
    } else rects[name].setStyle(tileStyle(name));
  }
  updateLabels();
}

function updateLabels() {
  const show = map.getZoom() >= 7;
  for (const [n, m] of Object.entries(labels)) {
    if (show && (status[n].land || selected.has(n))) m.addTo(map); else map.removeLayer(m);
  }
}

function onTileClick(name, shift) {
  if (shift && lastClicked) {
    const a = [parseInt(lastClicked.slice(0, 3)), parseInt(lastClicked.slice(3))];
    const b = [parseInt(name.slice(0, 3)), parseInt(name.slice(3))];
    for (let la = Math.min(a[0], b[0]); la <= Math.max(a[0], b[0]); la++)
      for (let lo = Math.min(a[1], b[1]); lo <= Math.max(a[1], b[1]); lo++) {
        const n = tname(la, lo); if (status[n]) selected.add(n);
      }
  } else if (selected.has(name)) selected.delete(name); else selected.add(name);
  lastClicked = name;
  renderSelection(); drawTiles(); showTileInfo(name);
}

const tname = (lat, lon) => (lat >= 0 ? "+" : "-") + String(Math.abs(lat)).padStart(2, "0")
  + (lon >= 0 ? "+" : "-") + String(Math.abs(lon)).padStart(3, "0");

async function refreshOverlays() {
  const show = $("#showOverlay").checked;
  for (const [n, s] of Object.entries(status)) {
    if (show && s.built && !overlays[n]) {
      const o = await api().get_overlay(n);
      if (o) overlays[n] = L.imageOverlay(o.url, o.bounds,
        { opacity: +$("#overlayOpacity").value, interactive: false, pane: "forest" }).addTo(map);
    }
    if (overlays[n]) { if (show) overlays[n].addTo(map); else map.removeLayer(overlays[n]); }
  }
}

function renderForestLegend() {
  const box = $("#forestLegend");
  box.classList.toggle("hidden", !$("#showOverlay").checked || !rules);
  if (!rules) return;
  const { shade, cut } = S.overlay;
  const rgb = (c, k) => `rgb(${c.map(v => Math.round(v * k)).join(",")})`;
  box.innerHTML = "";
  box.append(el("h5", {}, "Forest overlay"));
  for (const b of rules.biome || []) {
    const c = b.color || [60, 160, 70];
    const ramp = el("span", { class: "ramp" });
    ramp.style.background = `linear-gradient(90deg, ${rgb(c, shade[0])}, ${rgb(c, shade[1])})`;
    box.append(el("div", { class: "r", title: b.label || "" }, ramp, el("b", {}, b.name),
      (b.label || "").split("(")[0].trim()));
  }
  for (const pr of (rules.plantation || []).filter(x => x.asset)) {
    const sw = el("span", { class: "ramp" }); sw.style.background = `rgb(${(pr.color || [230, 120, 40]).join(",")})`;
    box.append(el("div", { class: "r" }, sw, el("b", {}, pr.name), (pr.label || "") + " (plantation)"));
  }
  const cutSw = el("span", { class: "ramp" }); cutSw.style.background = `rgb(${cut.join(",")})`;
  box.append(el("div", { class: "r" }, cutSw, el("b", {}, "cut out"), "roads, buildings, water, airports"));
  const scSw = el("span", { class: "ramp" });
  scSw.style.background = `linear-gradient(90deg, ${rgb(S.overlay.scrub, shade[0])}, ${rgb(S.overlay.scrub, shade[1])})`;
  box.append(el("div", { class: "r" }, scSw, el("b", {}, "scrub"), "bushes on open bushland"));
  const osmSw = el("span", { class: "ramp" }); osmSw.style.background = `rgb(${S.overlay.osm.join(",")})`;
  box.append(el("div", { class: "r" }, osmSw, el("b", {}, "OSM trees"), "single trees and tree rows from OpenStreetMap"));
  const twSw = el("span", { class: "ramp" }); twSw.style.background = `rgb(${S.overlay.town.join(",")})`;
  box.append(el("div", { class: "r" }, twSw, el("b", {}, "town trees"), "scattered trees in towns"));
  box.append(el("div", { class: "note" },
    "Colour = biome. Dark → bright = thin → thick forest. Transparent = no trees. "
    + "Shows the forest types as they were at the tile's last build."));
}

async function reloadOverlay(name) {
  if (overlays[name]) { map.removeLayer(overlays[name]); delete overlays[name]; }
}

async function refreshStatus() {
  status = await api().tile_status($("#profile").value);
  dropTiles(status);
  await refreshPack();
  drawTiles();
  await refreshOverlays();
}

/* ---------------- tiles panel ---------------- */
function renderSelection() {
  const box = $("#selList"); box.innerHTML = "";
  const list = [...selected].sort();
  if (!list.length) box.append(el("span", { class: "hint" }, "No tiles selected."));
  for (const n of list) box.append(el("span", { class: "chip", title: "Remove", onclick: () => onTileClick(n, false) }, n));
  $("#build").textContent = list.length ? `Build ${list.length} tile${list.length > 1 ? "s" : ""}` : "Build selected";
  const deletable = list.filter(n => status[n] && (status[n].built || status[n].installed || status[n].cached));
  $("#delete").disabled = !deletable.length;
  $("#delete").textContent = deletable.length ? `Delete ${deletable.length} tile${deletable.length > 1 ? "s" : ""}…` : "Delete selected tiles…";
  updateEstimate();
}

async function openDelete() {
  const tiles = [...selected].filter(n => status[n] && (status[n].built || status[n].installed || status[n].cached)).sort();
  if (!tiles.length) return;
  const pv = await api().delete_preview(tiles);
  if (!pv.ok) { alert(pv.error); return; }
  $("#delTitle").textContent = `Delete ${tiles.length} tile${tiles.length > 1 ? "s" : ""}`;
  $("#delTiles").textContent = tiles.join(", ");
  const opt = (id, kind, text) => {
    const v = pv[kind], box = $(id);
    box.disabled = !v.tiles; if (!v.tiles) box.checked = false;
    $(id + "Txt").textContent = `${text} — ${v.tiles} tile(s), ${fmtMB(v.mb)}`;
  };
  opt("#delOutput", "output", pv.pack_linked ? "Delete built tiles (removes them from X-Plane too)"
    : "Delete built tiles (.dsf, overlay, stats)");
  opt("#delCache", "cache", "Delete downloaded data (satellite maps, OSM extract)");
  msg("#delMsg", "");
  $("#delModal").classList.remove("hidden");
  $("#delCancel").onclick = () => $("#delModal").classList.add("hidden");
  $("#delOk").onclick = async () => {
    const kinds = [["#delOutput", "output"], ["#delCache", "cache"]]
      .filter(([id]) => $(id).checked).map(([, k]) => k);
    if (!kinds.length) { msg("#delMsg", "Choose at least one thing to delete.", true); return; }
    const r = await api().delete_tiles(tiles, kinds);
    if (!r.ok) { msg("#delMsg", r.error, true); return; }
    $("#delModal").classList.add("hidden");
    for (const t of tiles) await reloadOverlay(t);
    await refreshStatus();
    renderSelection();
    if (lastClicked) showTileInfo(lastClicked);
  };
}

let packLinked = false;
async function refreshPack() {
  const p = await api().pack_status();
  packLinked = p.installed;
  const box = $("#packBox"); box.innerHTML = "";
  const state = p.installed ? "Installed — linked into X-Plane"
    : p.real_folder ? "An old copied pack folder is in X-Plane (Install converts it to a link)"
    : p.linked_elsewhere ? "Linked to a different folder (Install fixes it)" : "Not installed";
  box.append(el("div", {}, el("b", {}, p.pack), ` · ${state}`),
    el("div", { class: "hint" }, `${p.tiles.length} built tile(s) of all your regions in one pack. X-Plane folder: ${p.link}`));
  if (p.installed && p.pack !== "forest_studio_xp")
    box.append(el("div", { class: "hint" }, `It keeps its older name ${p.pack} so it stays at the same place in `
      + "your scenery order; it now holds every region's tiles."));
  if (p.installed && !p.in_ini)
    box.append(el("div", { class: "warn" }, p.ini_disabled
      ? "The pack is disabled in scenery_packs.ini — enable it there."
      : "X-Plane doesn't list this pack yet: it will add it at the top of scenery_packs.ini on next start; "
        + "move it below your airport/city sceneries and above X-World extras."));
  $("#packInstall").disabled = p.installed;
  $("#packUninstall").disabled = !p.installed;
}

async function refreshWorkers() {
  const info = await api().parallel_info();
  const sel = $("#workers"), keep = sel.value;
  sel.innerHTML = "";
  sel.append(el("option", { value: "0" }, `Auto (${info.workers})`));
  for (let n = 1; n <= info.max; n++) sel.append(el("option", { value: String(n) }, String(n)));
  sel.value = [...sel.options].some(o => o.value === keep) ? keep : "0";
  $("#workersInfo").textContent =
    `${info.cores_physical} CPU cores · ${info.ram_available_gb} of ${info.ram_total_gb} GB RAM free · `
    + `one build needs up to ~${info.peak_gb} GB → Auto = ${info.workers} (limited by ${info.limited_by}). `
    + "Close X-Plane while building to allow more.";
}

let lastEstimate = null, estTimer = null;
const fmtDur = (s) => s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min`
  : `${(s / 3600).toFixed(1)} h`;
const fmtMB = (mb) => mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${Math.round(mb)} MB`;
function updateEstimate() {
  clearTimeout(estTimer);
  estTimer = setTimeout(async () => {
    const box = $("#estimate");
    const tiles = [...selected];
    if (!tiles.length) { box.classList.add("hidden"); lastEstimate = null; return; }
    const e = await api().estimate(tiles, +$("#workers").value || 0);
    if (!e.ok) { box.classList.add("hidden"); return; }
    lastEstimate = e;
    const disk = e.cache_mb + e.out_mb;
    const lowP = e.free_gb_project != null && (e.cache_mb + e.out_mb) / 1000 > e.free_gb_project * 0.9;
    const lowX = false;  // nothing is copied into X-Plane: the pack is a link
    box.innerHTML = "";
    box.append(
      el("div", {}, el("span", { class: "big" }, `≈ ${fmtDur(e.seconds)}`),
        `  ·  ${e.workers} at a time  ·  ≈ ${fmtMB(disk)} new disk use`),
      el("div", {}, `${e.download_tiles} tile(s) to download (≈ ${fmtMB(e.cache_mb)} cache)`
        + (e.osm_pass ? " · one OpenStreetMap pass" : " · OSM already up to date")),
      el("div", {}, `Build output ≈ ${fmtMB(e.out_mb)}` + (packLinked
        ? " · in X-Plane through the linked pack (no extra space)" : " · pack not installed in X-Plane yet")),
      el("div", { class: lowP || lowX ? "warn" : "" },
        `Free space: project drive ${e.free_gb_project ?? "?"} GB · X-Plane drive ${e.free_gb_xplane ?? "?"} GB`
        + (lowP || lowX ? " — not enough!" : "")),
      el("div", { class: "hint" }, e.measured ? "Based on your previous builds." : "Rough defaults until a few builds are measured."));
    box.classList.remove("hidden");
  }, 250);
}

async function showTileInfo(name) {
  const s = status[name] || {};
  const flags = [["Forest types", s.region ? regionLabel(s.region) : false],
    ["Land tile (base mesh)", s.land], ["Data cached", s.cached],
    ["Built", s.built],
    ["Up to date", !s.built ? false : (s.outdated && s.outdated.length ? "no — " + s.outdated.join("; ") : "yes")],
    ["In X-Plane", s.installed ? "yes (pack linked)" : (s.built ? "no — pack not installed" : false)]];
  const info = $("#tileInfo"); info.innerHTML = "";
  info.append(el("div", {}, el("b", {}, name)));
  const t = el("table");
  for (const [k, v] of flags) t.append(el("tr", {}, el("td", {}, k), el("td", {}, typeof v === "string" ? v : v ? "yes" : "–")));
  info.append(t);
  if (s.region) {
    const edit = el("a", { class: "link", href: "#" }, `Edit ${regionLabel(s.region)}'s forest types →`);
    edit.onclick = async (ev) => { ev.preventDefault(); await useRules(s.region); showTab("types"); };
    info.append(el("div", {}, edit));
  }
  const st = await api().get_stats(name);
  if (st) {
    const t2 = el("table");
    const rows = [["Profile", st.profile], ["Forest area (km²)", fmt(st.forest_km2)], ["Polygons", fmt(st.polygons)],
      ["Estimated trees", fmt(st.estimated_trees)], ["Budget scale", st.budget_scale], ["Build time (s)", st.seconds]];
    if (st.scrub_km2 !== undefined) rows.splice(2, 0, ["Scrub area (km²)", fmt(st.scrub_km2)]);
    if (st.town_trees !== undefined) rows.splice(st.scrub_km2 !== undefined ? 5 : 4, 0,
      ["Town trees", `${fmt(st.town_trees)} in ${fmt(st.town_km2)} km² of town`],
      ["OSM single trees / rows", `${fmt(st.osm_single_trees)} / ${fmt(st.osm_tree_row_trees)}`]);
    for (const [k, v] of rows) t2.append(el("tr", {}, el("td", {}, k), el("td", {}, String(v))));
    info.append(el("h3", {}, "Last build"), t2);
    if (st.biome_km2) {
      const bt = el("table");
      const col = Object.fromEntries((rules?.biome || []).map(b => [b.name, b.color]));
      for (const [n, km] of Object.entries(st.biome_km2).sort((x, y) => y[1] - x[1])) {
        const sw = el("span", { class: "ramp" }); sw.style.cssText = `display:inline-block;width:10px;height:10px;margin-right:6px;background:rgb(${(col[n] || [120, 120, 120]).join(",")})`;
        bt.append(el("tr", {}, el("td", {}, sw, n), el("td", {}, `${fmt(km)} km²`)));
      }
      info.append(el("h3", {}, "Forest by biome"), bt);
    }
    if (st.plantation_km2 && Object.keys(st.plantation_km2).length) {
      const pt = el("table");
      for (const [n, km] of Object.entries(st.plantation_km2).sort((x, y) => y[1] - x[1]))
        pt.append(el("tr", {}, el("td", {}, n), el("td", {}, `${fmt(km)} km²`)));
      info.append(el("h3", {}, "Plantations (OSM)"), pt);
    }
    const pa = el("table");
    for (const [a, v] of Object.entries(st.per_asset || {}).sort((x, y) => y[1].km2 - x[1].km2))
      pa.append(el("tr", {}, el("td", { title: a }, a.split("/").pop()), el("td", {}, `${fmt(v.km2)} km²`)));
    info.append(el("h3", {}, "Forest types"), pa);
  }
}

/* ---------------- settings ---------------- */
async function loadProfile() {
  const name = $("#profile").value;
  profile = await api().get_profile(name);
  profileSaved = JSON.parse(JSON.stringify(profile));
  $("#profTitle").textContent = `Profile: ${name}`;
  renderProfile(); msg("#profMsg", "");
}

function renderProfile() {
  const form = $("#profForm"); form.innerHTML = "";
  const groups = {};
  for (const [key, sc] of Object.entries(S.schema)) (groups[sc.group] ||= []).push([key, sc]);
  for (const [g, items] of Object.entries(groups)) {
    const box = el("div", { class: "group" }, el("h4", {}, g));
    for (const [key, sc] of items) {
      const num = el("input", { type: "number", min: sc.min, max: sc.max, step: sc.step });
      const rng = el("input", { type: "range", min: sc.min, max: sc.max, step: sc.step });
      const f = el("div", { class: "field" },
        el("div", { class: "lbl" }, el("span", {}, sc.label, fpsBadge(key)), num), rng, el("div", { class: "help" }, sc.help));
      const set = (v, from) => {
        v = sc.type === "int" ? Math.round(+v) : +v;
        profile[key] = v;
        if (from !== num) num.value = v;
        if (from !== rng) rng.value = v;
        f.classList.toggle("dirty", profileSaved[key] !== v);
      };
      num.oninput = () => set(num.value, num); rng.oninput = () => set(rng.value, rng);
      set(profile[key]);
      box.append(f);
    }
    form.append(box);
  }
  const lv = el("input", { type: "text", value: (profile.density_levels || []).join(", ") });
  const f = el("div", { class: "field" }, el("div", { class: "lbl" }, el("span", {}, "Thickness steps", fpsBadge("density_levels"))), lv,
    el("div", { class: "help" }, S.density_levels_help));
  lv.oninput = () => {
    profile.density_levels = lv.value.split(",").map(x => parseFloat(x)).filter(x => !isNaN(x));
    f.classList.toggle("dirty", JSON.stringify(profile.density_levels) !== JSON.stringify(profileSaved.density_levels));
  };
  form.append(el("div", { class: "group" }, el("h4", {}, "Thickness steps"), f));
}

const FPS_LABEL = { high: "High FPS impact", medium: "Medium FPS impact", low: "Low FPS impact" };
const FPS_TIP = {
  high: "Directly sets how many trees X-Plane draws. The first setting to change for frame rate.",
  medium: "Changes how much land gets trees, so the tree count changes (up to the tile's max trees).",
  low: "Small effect on the number of trees.",
};
function fpsBadge(key) {
  const lvl = S.fps_impact[key];
  if (!lvl || lvl === "none") return "";
  return el("span", { class: `fps fps-${lvl}`, title: FPS_TIP[lvl] }, lvl === "high" ? "⚡⚡ FPS" : lvl === "medium" ? "⚡ FPS" : "FPS");
}

const profileDirty = () => profile && JSON.stringify(profile) !== JSON.stringify(profileSaved);

async function saveProfile(name) {
  const r = await api().save_profile(name, profile);
  if (!r.ok) return msg("#profMsg", r.error, true), false;
  msg("#profMsg", `Saved ${name}.toml`);
  await fillSelect("#profile", (await api().get_state()).profiles, name);
  await loadProfile();
  refreshStatus();   // outdated tiles depend on the profile
  return true;
}

/* ---------------- forest types ---------------- */
let rulesRegion = null, rulesSaved = null;
const rulesDirty = () => rules && rulesSaved !== null && JSON.stringify(rules) !== rulesSaved;

/* Show (and edit) another region's forest types. */
async function useRules(name) {
  $("#region").value = name;
  S.ecoregions = await api().ecoregion_names(name);
  await loadRules();
}

async function loadRules() {
  rulesRegion = region();
  rules = await api().get_rules(rulesRegion);
  rulesSaved = JSON.stringify(rules);
  $("#rulesTitle").textContent = `Forest types: ${regionLabel(region())}`;
  renderRules(); renderForestLegend(); msg("#rulesMsg", "");
}

const COST_MARK = { high: "⚡⚡", medium: "⚡", low: "·" };
const COST_WORD = { high: "heavy", medium: "medium", low: "light" };
function assetSelect(value, onchange, list = mainAssets(), allowNone = false) {
  const s = el("select");
  if (allowNone) s.append(el("option", { value: "" }, typeof allowNone === "string" ? allowNone : "—  No trees (crop without trees)"));
  // Lightest first, so cheaper choices are at the top of the list.
  const order = [...list].sort((a, b) => (S.asset_info[a]?.mtris_km2 ?? 0) - (S.asset_info[b]?.mtris_km2 ?? 0));
  for (const a of order) {
    const i = S.asset_info[a] || {};
    const name = a.replace("lib/vegetation/forests/", "veg/").replace("lib/", "");
    s.append(el("option", { value: a }, `${COST_MARK[i.cost] || "·"}  ${name}   (${i.mtris_km2 ?? "?"} M tris/km²)`));
  }
  s.value = value; s.onchange = () => onchange(s.value);
  return s;
}
const assetNote = (a) => {
  const i = S.asset_info[a]; if (!i) return "";
  return `FPS cost: ${COST_WORD[i.cost] || "?"} — ${fmt(i.trees_km2)} trees/km² × ${fmt(i.tris_tree)} triangles/tree `
    + `= ${i.mtris_km2} M triangles/km² at full thickness · ${i.species.filter(x => !x.startsWith("stones")).slice(0, 8).join(", ")}`;
};
function costBadge(a) {
  const i = S.asset_info[a] || {};
  const lvl = i.cost || "low";
  return el("span", { class: `fps fps-${lvl}`, title: "How heavy this forest type is for the GPU: trees per km² × triangles per tree." },
    `${lvl === "low" ? "" : COST_MARK[lvl] + " "}${COST_WORD[lvl]}`);
}

let curBiome = 0;
const hex = (c) => "#" + c.map(v => v.toString(16).padStart(2, "0")).join("");
const unhex = (h) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));

function renderBiomePicker() {
  const box = $("#biomePicker"); box.innerHTML = "";
  rules.biome.forEach((bm, i) => {
    const sw = el("span", { class: "bsw" }); sw.style.background = `rgb(${bm.color.join(",")})`;
    box.append(el("button", { class: "bchip" + (i === curBiome ? " on" : ""), title: bm.label,
      onclick: () => { curBiome = i; renderRules(); } }, sw, bm.name));
  });
}

function renderBiomeHead(bm) {
  const box = $("#biomeHead"); box.innerHTML = "";
  const label = el("input", { type: "text", value: bm.label || "" });
  label.oninput = () => { bm.label = label.value; };
  const color = el("input", { type: "color", value: hex(bm.color), title: "Colour on the map overlay" });
  color.oninput = () => { bm.color = unhex(color.value); renderBiomePicker(); renderForestLegend(); };
  const ecoBox = el("div", { class: "chips" });
  const assigned = new Set(rules.biome.flatMap(b => b.ecoregions || []));
  for (const e of bm.ecoregions || []) {
    ecoBox.append(el("span", { class: "chip eco", title: "Remove (it will use the default biome)",
      onclick: () => { bm.ecoregions = bm.ecoregions.filter(x => x !== e); renderRules(); } }, e));
  }
  const add = el("select");
  add.append(el("option", { value: "" }, bm.name === "default" ? "Unlisted ecoregions use this biome"
    : "+ add ecoregion…"));
  for (const e of S.ecoregions) if (!assigned.has(e)) add.append(el("option", { value: e }, e));
  add.disabled = bm.name === "default";
  add.onchange = () => { if (add.value) { (bm.ecoregions ||= []).push(add.value); renderRules(); } };
  const resolve = (bm.resolve_biomes || []).length
    ? el("div", { class: "hint" }, "Also every ecoregion of these world biomes (RESOLVE) not listed by name: "
      + bm.resolve_biomes.join(" · "))
    : "";
  box.append(el("div", { class: "top" }, color, label),
    el("div", { class: "lab" }, bm.name === "default"
      ? "Used for every ecoregion no other biome covers."
      : `Ecoregions in this biome (${(bm.ecoregions || []).length}) — click one to remove it`),
    ecoBox, add, resolve);
}

function renderPlantations() {
  const box = $("#plantBox"); box.innerHTML = "";
  rules.plantation ||= [];
  rules.plantation.forEach((pr, i) => {
    const color = el("input", { type: "color", value: hex(pr.color || [230, 120, 40]), title: "Colour on the map overlay" });
    color.oninput = () => { pr.color = unhex(color.value); renderForestLegend(); };
    const label = el("input", { type: "text", value: pr.label || pr.name });
    label.oninput = () => { pr.label = label.value; };
    const up = el("button", { class: "ghost rm", title: "Move up (checked earlier)", onclick: () => {
      if (i > 0) { [rules.plantation[i - 1], rules.plantation[i]] = [pr, rules.plantation[i - 1]]; renderRules(); } } }, "↑");
    const rm = el("button", { class: "ghost rm", title: "Remove", onclick: () => { rules.plantation.splice(i, 1); renderRules(); } }, "✕");
    const match = el("textarea", { rows: 2, spellcheck: "false" });
    match.value = (pr.match || []).join(", ");
    match.oninput = () => { pr.match = match.value.split(/[,\n]/).map(x => x.trim()).filter(Boolean); };
    const note = el("div", { class: "sp" }, pr.asset ? assetNote(pr.asset) : "No trees are planted here.");
    const lab = el("div", { class: "lab" }, "Trees", pr.asset ? costBadge(pr.asset) : "");
    const sel = assetSelect(pr.asset || "", v => { pr.asset = v; note.textContent = v ? assetNote(v) : "No trees are planted here.";
      lab.replaceChildren("Trees", v ? costBadge(v) : ""); }, allAssets(), true);
    const dens = el("input", { type: "range", min: 0, max: 1, step: 0.05, value: pr.density ?? 0.7 });
    const dOut = el("output", {}, String(pr.density ?? 0.7));
    dens.oninput = () => { pr.density = +dens.value; dOut.textContent = dens.value; };
    box.append(el("div", { class: "band" },
      el("div", { class: "top" }, el("span", { class: "hint" }, `${i + 1}.`), color, label, up, rm),
      el("div", { class: "lab" }, "OSM tags that identify it (key=value, comma-separated; & = all of)"), match,
      lab, sel, note,
      el("div", { class: "lbl" }, el("span", { class: "hint" }, "Thickness"), dOut), dens));
  });
}

function renderRules() {
  if (!rules.biome) rules.biome = [{ name: "default", label: "All", color: [60, 160, 70], ecoregions: [], band: rules.band }];
  curBiome = Math.min(curBiome, rules.biome.length - 1);
  const bm = rules.biome[curBiome];
  renderBiomePicker(); renderBiomeHead(bm);
  $("#seasonNote").textContent = regionInfo().evergreen_only === false
    ? "This region allows seasonal X-Plane forests (bare in winter, as real temperate forests are)."
    : "Only evergreen X-Plane forests are offered (no winter look in tropical highlands).";
  $("#sparseBelow").value = rules.sparse_below; $("#sbOut").textContent = rules.sparse_below;
  $("#sparseBelow").oninput = () => { rules.sparse_below = +$("#sparseBelow").value; $("#sbOut").textContent = rules.sparse_below; };
  const sb = $("#singleBox"); sb.innerHTML = "";
  if (!rules.single_tree) rules.single_tree = bm.band[0].dense;
  const stNote = el("div", { class: "sp" }, assetNote(rules.single_tree));
  const stLab = el("div", { class: "lab" }, "OSM single trees & tree rows", costBadge(rules.single_tree));
  sb.append(el("div", { class: "top" }, el("b", {}, "OpenStreetMap trees")),
    el("div", { class: "hint" }, "Forest type used for trees mapped one by one in OSM (street trees, gardens, golf) "
      + "and for tree rows. X-Plane picks a random tree from this type for each point."),
    stLab, assetSelect(rules.single_tree, v => { rules.single_tree = v; stNote.textContent = assetNote(v);
      stLab.replaceChildren("OSM single trees & tree rows", costBadge(v)); }), stNote);
  if (!rules.town_tree) rules.town_tree = rules.single_tree;
  const ttNote = el("div", { class: "sp" }, assetNote(rules.town_tree));
  const ttLab = el("div", { class: "lab" }, "Town trees (where the biome sets none)", costBadge(rules.town_tree));
  sb.append(el("div", { class: "hint" }, "Trees scattered through towns and cities (how many: profile → "
      + "'Town trees per km²'). Each biome can pick its own type below, e.g. dry types with palms on the coast."),
    ttLab, assetSelect(rules.town_tree, v => { rules.town_tree = v; ttNote.textContent = assetNote(v);
      ttLab.replaceChildren("Town trees (where the biome sets none)", costBadge(v)); }), ttNote);
  renderPlantations();
  const box = $("#bands"); box.innerHTML = "";
  {
    const scNote = el("div", { class: "sp" }, bm.scrub ? assetNote(bm.scrub) : "No scrub in this biome.");
    const scLab = el("div", { class: "lab" }, "Scrub (bushes on open bushland)", bm.scrub ? costBadge(bm.scrub) : "");
    const scSel = assetSelect(bm.scrub || "", v => { bm.scrub = v; scNote.textContent = v ? assetNote(v) : "No scrub in this biome.";
      scLab.replaceChildren("Scrub (bushes on open bushland)", v ? costBadge(v) : ""); }, allAssets(), "—  No scrub");
    const bare = el("input", { type: "checkbox" }); bare.checked = !!bm.scrub_on_bare;
    bare.onchange = () => { bm.scrub_on_bare = bare.checked; };
    const twNote = el("div", { class: "sp" }, bm.town_tree ? assetNote(bm.town_tree) : "Uses the general town tree type above.");
    const twLab = el("div", { class: "lab" }, "Town trees", bm.town_tree ? costBadge(bm.town_tree) : "");
    const twSel = assetSelect(bm.town_tree || "", v => { bm.town_tree = v;
      twNote.textContent = v ? assetNote(v) : "Uses the general town tree type above.";
      twLab.replaceChildren("Town trees", v ? costBadge(v) : ""); }, allAssets(), "—  Same as the general town tree type");
    box.append(el("h3", {}, `Scrub & town trees — ${bm.name}`), el("div", { class: "band" },
      scLab, scSel, scNote,
      el("label", { class: "chk", title: "For deserts and dry coasts: the satellite map calls much of La Guajira bare or sparse ground." },
        bare, " Also put sparse bushes on bare / sparsely vegetated ground"),
      twLab, twSel, twNote));
  }
  {
    const cl = el("input", { type: "checkbox" }); cl.checked = !!bm.climate;
    cl.onchange = () => { bm.climate = cl.checked; renderRules(); };
    box.append(el("h3", {}, `Tree types — ${bm.name}`), el("div", { class: "band" },
      el("label", { class: "chk" }, cl, " Pick trees by leaf type and climate (recommended for new regions)"),
      el("div", { class: "hint" }, bm.climate
        ? "Each spot gets X-Plane's own forest for what the satellite sees there (conifers, broadleaves or mixed) "
          + "and the local climate (very cold … very hot, dry … rainforest), e.g. pine flatwoods → warm conifers. "
          + "It also places swamp forest in OSM swamps, wetland bushes in marshes, bogs and reed beds, cloud "
          + "forest in cool, wet tropical mountains, scattered savanna trees in hot, dry places, orchards for the "
          + "climate, and the climate's broadleaf trees in towns and for OSM single trees. OSM woods tagged "
          + "with a leaf type (conifer / broadleaf / mixed) win over the satellite. "
          + "Bushes follow the climate too (the scrub type above only says whether there are any). "
          + "The elevation bands below are only used if the climate map can't be downloaded. FPS cost: about the "
          + "same as the band types (none extra)."
        : "Off: the elevation bands below decide the trees. Turn on to let the satellite leaf type and the "
          + "climate map choose (one 131 MB download, once; ~1 MB per tile).")));
  }
  box.append(el("h3", {}, bm.climate ? `Elevation bands — ${bm.name} (fallback)` : `Elevation bands — ${bm.name}`));
  bm.band.forEach((b, i) => {
    const prevMax = i ? bm.band[i - 1].elev_max : 0;
    const name = el("input", { type: "text", value: b.name });
    name.oninput = () => b.name = name.value;
    const em = el("input", { type: "number", value: b.elev_max, step: 50, title: "Upper elevation (m)" });
    em.oninput = () => { b.elev_max = +em.value; };
    em.onchange = renderRules;
    const rm = el("button", { class: "ghost rm", title: "Remove band", onclick: () => { bm.band.splice(i, 1); renderRules(); } }, "✕");
    const dn = el("div", { class: "sp" }, assetNote(b.dense)), sn = el("div", { class: "sp" }, assetNote(b.sparse));
    const dl = el("div", { class: "lab" }, "Dense forest", costBadge(b.dense));
    const sl = el("div", { class: "lab" }, "Sparse forest", costBadge(b.sparse));
    const pick = (key, note, lab, title) => (v) => {
      b[key] = v; note.textContent = assetNote(v);
      lab.replaceChildren(title, costBadge(v));
    };
    box.append(el("div", { class: "band" },
      el("div", { class: "top" }, name, el("span", { class: "hint" }, `${fmt(prevMax)} –`), em, el("span", { class: "hint" }, "m"), rm),
      dl, assetSelect(b.dense, pick("dense", dn, dl, "Dense forest")), dn,
      sl, assetSelect(b.sparse, pick("sparse", sn, sl, "Sparse forest")), sn));
  });
}

/* ---------------- build ---------------- */
async function startBuild() {
  if (!selected.size) return;
  if (profileDirty()) {
    if (!confirm("The profile has unsaved changes. Save them before building?")) return;
    if (!await saveProfile($("#profile").value)) return;
  }
  const tiles = [...selected].sort();
  const e = lastEstimate;
  if (e && (tiles.length >= 10 || e.seconds > 1800)) {
    const disk = e.cache_mb + e.out_mb;
    if (!confirm(`Build ${tiles.length} tiles?\n\nEstimated time: ${fmtDur(e.seconds)}\nNew disk use: ${fmtMB(disk)}\n\n`
      + "You can cancel at any time; finished tiles are kept.")) return;
  }
  const r = await api().start_build(tiles, $("#profile").value, +$("#workers").value || 0);
  if (!r.ok) { alert(r.error); return; }
  window._buildTotal = tiles.length;
  if (r.job && typeof selectedJob !== "undefined") selectedJob = r.job;   // Build queue opens this run
  setRunning(true);
  poll();
}

function setRunning(on) {
  $("#build").disabled = on; $("#cancel").disabled = !on;
  if (on) $("#delete").disabled = true; else renderSelection();
  $("#progress").classList.toggle("hidden", !on);
  if (on) $("#openQueue").classList.remove("hidden");
}

async function poll() {
  clearTimeout(polling);
  let j;
  // One failed call must not stop the polling (the progress bar would stay "running" forever).
  try { j = await api().job_status(logSeq); }
  catch (e) { polling = setTimeout(poll, 2000); return; }
  // The full log lives in the Build queue; here only remember how far we've read.
  for (const ln of j.lines) logSeq = Math.max(logSeq, +ln.split("\t")[0]);
  const total = window._buildTotal || 1, n = j.done.length + j.failed.length;
  $("#progress div").style.width = `${100 * n / total}%`;
  // While data is fetched, say what exactly (satellite maps, the OSM country file with its %, the OSM pass).
  const doing = j.phase ? j.phase
    : /^(download|updat)/.test(j.current || "") ? j.current[0].toUpperCase() + j.current.slice(1)
    : `Building ${j.current || "…"}`;
  $("#progressText").textContent = j.running
    ? `${doing} · ${n}/${total} done${j.failed.length ? ` · ${j.failed.length} failed` : ""}`
    : (n ? `Finished: ${j.done.length} ok${j.failed.length ? `, ${j.failed.length} failed` : ""}` : "");
  if (j.running && j.workers > 1) $("#progressText").textContent += ` · ${j.workers} at a time`;
  const sbJob = $("#sbJob");
  if (sbJob) {
    sbJob.textContent = j.running ? `● ${$("#progressText").textContent}` : (n ? $("#progressText").textContent : "");
    sbJob.onclick = () => { if (typeof showView === "function") showView("works"); };
  }
  if (j.running) polling = setTimeout(poll, 800);
  else {
    setRunning(false);
    for (const t of [...j.done, ...j.failed]) await reloadOverlay(t);
    await refreshStatus();
    updateEstimate();
    if (lastClicked) showTileInfo(lastClicked);
  }
}

/* ---------------- misc ---------------- */
function msg(sel, text, err = false) { const m = $(sel); m.textContent = text; m.className = "msg " + (err ? "err" : "ok"); }
async function fillSelect(sel, items, value, label = (i) => i) {
  const s = $(sel); s.innerHTML = "";
  for (const i of items) s.append(el("option", { value: i }, label(i)));
  if (value && items.includes(value)) s.value = value;
}
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("on", t.id === "tab-" + name));
}
function ask(title, value) {
  return new Promise(res => {
    $("#modalTitle").textContent = title; $("#modalInput").value = value || ""; $("#modal").classList.remove("hidden");
    $("#modalInput").focus();
    const done = (v) => { $("#modal").classList.add("hidden"); res(v); };
    $("#modalOk").onclick = () => done($("#modalInput").value.trim());
    $("#modalCancel").onclick = () => done(null);
    $("#modalInput").onkeydown = (e) => { if (e.key === "Enter") $("#modalOk").click(); if (e.key === "Escape") done(null); };
  });
}
function badges() {
  // Status bar at the bottom (views.js keeps it up to date).
  if (typeof refreshStatusbar === "function") refreshStatusbar();
}

async function init() {
  S = await api().get_state();
  badges();
  await fillSelect("#profile", S.profiles, "medium");
  await fillRegionSelects();
  initMap();
  await Promise.all([loadProfile(), loadRules()]);
  await refreshStatus();
  renderSelection();

  document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => showTab(b.dataset.tab));
  $("#profile").onchange = async () => {
    if (profileDirty() && !confirm("Discard unsaved profile changes?")) return;
    await loadProfile();
    await refreshStatus();
  };
  $("#region").onchange = async () => {
    const want = region();
    if (rulesDirty() && !confirm("Discard unsaved forest-type changes?")) { $("#region").value = rulesRegion; return; }
    await useRules(want);
  };
  $("#goRegion").onchange = () => { if ($("#goRegion").value) fitRegion($("#goRegion").value); $("#goRegion").value = ""; };
  $("#packInstall").onclick = async () => {
    const r = await api().install_pack();
    msg("#packMsg", r.message, !r.ok); await refreshStatus(); updateEstimate();
  };
  $("#packUninstall").onclick = async () => {
    if (!confirm("Remove the forest pack from X-Plane? Your built tiles are kept; "
      + "X-Plane shows X-World / default forests again after a restart.")) return;
    const r = await api().uninstall_pack();
    msg("#packMsg", r.message, !r.ok); await refreshStatus(); updateEstimate();
  };
  $("#workers").onchange = updateEstimate;
  await refreshWorkers();
  // Free RAM changes over time: refresh the Auto value while no build is running.
  setInterval(() => { if ($("#cancel").disabled) refreshWorkers().catch(() => {}); }, 30000);
  $("#profRevert").onclick = loadProfile;
  $("#profSave").onclick = () => saveProfile($("#profile").value);
  $("#profSaveAs").onclick = async () => { const n = await ask("Save profile as", $("#profile").value + "-copy"); if (n) saveProfile(n); };
  $("#rulesRevert").onclick = loadRules;
  $("#profDefaults").onclick = async () => {
    const d = await api().default_profile($("#profile").value);
    profile = d.values; renderProfile();
    msg("#profMsg", `Loaded the default values of the '${d.base}' profile. Press Save to keep them, or Revert to undo.`);
  };
  $("#rulesDefaults").onclick = async () => {
    const d = await api().default_rules(region());
    if (!d.ok) return msg("#rulesMsg", d.error, true);
    rules = d.rules; curBiome = 0; renderRules(); renderForestLegend();
    msg("#rulesMsg", "Loaded the default forest types. Press Save to keep them, or Revert to undo.");
  };
  $("#rulesSave").onclick = async () => {
    const r = await api().save_rules(region(), rules);
    if (r.ok) { msg("#rulesMsg", "Saved."); loadRules(); refreshStatus(); } else msg("#rulesMsg", r.error, true);
  };
  $("#addPlant").onclick = () => {
    rules.plantation ||= [];
    rules.plantation.splice(Math.max(0, rules.plantation.length - 1), 0,
      { name: `plantation_${rules.plantation.length + 1}`, label: "New plantation", color: [230, 120, 40],
        match: ["trees=example_trees"], asset: allAssets()[0], density: 0.7 });
    renderRules();
  };
  $("#addBand").onclick = () => {
    const bands = rules.biome[curBiome].band;
    const last = bands[bands.length - 1];
    bands.push({ name: "new", elev_max: (last ? last.elev_max : 0) + 500, dense: last.dense, sparse: last.sparse });
    renderRules();
  };
  $("#selClear").onclick = () => { selected.clear(); renderSelection(); drawTiles(); };
  $("#selBuilt").onclick = () => { for (const [n, s] of Object.entries(status)) if (s.built) selected.add(n); renderSelection(); drawTiles(); };
  $("#selOutdated").onclick = () => {
    const out = Object.entries(status).filter(([, s]) => s.outdated && s.outdated.length);
    out.forEach(([n]) => selected.add(n)); renderSelection(); drawTiles();
    msg("#selMsg", out.length ? `${out.length} built tile(s) don't match the current profile, forest types or data.`
      : "All built tiles are up to date.");
  };
  $("#build").onclick = startBuild;
  $("#cancel").onclick = () => api().cancel_build();
  $("#delete").onclick = openDelete;
  $("#openQueue").onclick = (e) => { e.preventDefault(); showView("works"); };
  if (typeof initViews === "function") await initViews();

  const j = await api().job_status(0);
  if (j.running) { setRunning(true); poll(); }
}

if (window.pywebview) init();
else window.addEventListener("pywebviewready", init);
// Dev bridge (no pywebview): start after load.
window.addEventListener("load", () => setTimeout(() => { if (!window.pywebview && !S) init(); }, 300));
