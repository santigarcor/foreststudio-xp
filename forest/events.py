"""Progress events from the work code (downloads, data phases, build steps).

The work code calls `emit("download", what="OSM", mb=57, pct=45)` instead of printing a line for
the app to parse. Where the events go depends on who runs the code:

- in an app worker (forest.worker) every event is sent to the app as a dict, so progress bars and
  build steps never depend on the wording of a log line;
- on the command line (no sink set) each event is printed as a plain line (`text()`).

Events (data fields):
  log            text
  download       what ("OSM" / "ecoregions" / "climate"), mb, pct (None if the size is unknown)
  downloading    what, name (plain words: "the OpenStreetMap file colombia-latest.osm.pbf"), url
  ready          what (a country-wide file is downloaded and prepared)
  sat_start      n (tiles whose satellite maps are being fetched)
  sat_tile       tile, ok, mb | error
  osm_reading    file, mb, n, tiles (the tiles this file is read for)
  osm_done       n, seconds, tiles (n = 0: already up to date; tiles = whose OSM step ended)
  step           tile, step ("data" / "classify" / "vectorise" / "compile"), status, note
"""

from __future__ import annotations

from typing import Callable

Sink = Callable[[str, dict], None]
_sink: Sink | None = None


def set_sink(sink: Sink | None) -> None:
    global _sink
    _sink = sink


def emit(event: str, **data) -> None:
    if _sink is not None:
        _sink(event, data)
        return
    line = text(event, data)
    if line:
        print(line, flush=True)


def log(line: str) -> None:
    emit("log", text=line)


def text(event: str, d: dict) -> str:
    """The event as one log line ("" = nothing worth a line)."""
    if event == "log":
        return str(d.get("text", ""))
    if event == "download":
        pct = f" {d['pct']}%" if d.get("pct") is not None else ""
        return f"download {d['what']}: {d['mb']} MB{pct}"
    if event == "downloading":
        return f"{d['what']}: downloading {d.get('url') or d.get('name', '')}"
    if event == "ready":
        return f"{d['what']}: ready"
    if event == "sat_start":
        return f"satellite maps: downloading for {d['n']} tile(s)"
    if event == "sat_tile":
        return (f"{d['tile']}: rasters ready ({d.get('mb', 0):.0f} MB)" if d.get("ok")
                else f"{d['tile']}: data download FAILED: {d.get('error', '')}")
    if event == "osm_reading":
        return f"reading OpenStreetMap: {d['file']} ({d['mb']} MB) for {d['n']} tile(s)"
    if event == "osm_done":
        return (f"OSM: one pass for {d['n']} tile(s) in {d['seconds']:.0f} s" if d.get("n")
                else "OSM: already up to date")
    if event == "step":
        return ""   # the build's own timed log lines say the same
    return f"{event}: {d}"
