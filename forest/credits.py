"""Data and software credits: shown in the app (Settings → About) and written as CREDITS.txt
into every built pack, because the licenses of the source data require attribution in
anything made from them (OpenStreetMap's ODbL in particular).
"""

from __future__ import annotations

from pathlib import Path

CREDITS_FILE = "CREDITS.txt"

DATA = [
    {"name": "OpenStreetMap", "use": "roads, buildings, water, airports, parks, single trees, plantations",
     "license": "Open Database License (ODbL) 1.0",
     "attribution": "© OpenStreetMap contributors",
     "url": "https://www.openstreetmap.org/copyright",
     "via": "country extract from download.geofabrik.de"},
    {"name": "ESA WorldCover 2021 v200", "use": "land cover at 10 m (where trees and mangroves are)",
     "license": "Creative Commons Attribution 4.0 (CC BY 4.0)",
     "attribution": "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) "
                    "processed by ESA WorldCover consortium",
     "url": "https://esa-worldcover.org"},
    {"name": "Hansen Global Forest Change v1.12 (2000–2024)", "use": "tree cover share and recent forest loss",
     "license": "Creative Commons Attribution 4.0 (CC BY 4.0)",
     "attribution": "Hansen, M. C. et al. 2013. High-Resolution Global Maps of 21st-Century Forest Cover "
                    "Change. Science 342: 850–53. University of Maryland / Google / USGS / NASA",
     "url": "https://glad.earthengine.app/view/global-forest-change"},
    {"name": "RESOLVE Ecoregions 2017", "use": "biomes (which forest type grows where)",
     "license": "Creative Commons Attribution 4.0 (CC BY 4.0)",
     "attribution": "Dinerstein, E. et al. 2017. An Ecoregion-Based Approach to Protecting Half the "
                    "Terrestrial Realm. BioScience 67(6): 534–545",
     "url": "https://ecoregions.appspot.com"},
    {"name": "X-Plane 12 base mesh", "use": "elevation (read from your own X-Plane install, not redistributed)",
     "license": "Laminar Research", "attribution": "Laminar Research", "url": "https://www.x-plane.com"},
]

SOFTWARE = [
    {"name": "DSFTool (X-Plane Scenery Tools 24-5)", "license": "MIT/X11", "attribution": "Laminar Research",
     "url": "https://github.com/X-Plane/xptools"},
    {"name": "Leaflet", "license": "BSD-2-Clause", "attribution": "Volodymyr Agafonkin and contributors",
     "url": "https://leafletjs.com"},
    {"name": "Map tiles in the app", "license": "see provider",
     "attribution": "Esri World Imagery (© Esri, Maxar, Earthstar Geographics and the GIS user community); "
                    "OpenStreetMap tiles (© OpenStreetMap contributors)",
     "url": "https://www.esri.com / https://www.openstreetmap.org/copyright"},
]


def as_dict() -> dict:
    return {"data": DATA, "software": SOFTWARE}


def credits_text(region: str) -> str:
    lines = [
        f"forest_{region} - forest overlay for X-Plane 12, made with ForestStudio XP",
        "https://github.com/santigarcor/foreststudio-xp (GPL-3.0)",
        "",
        "Built from these data sources (their licenses require this notice):",
        "",
    ]
    for d in DATA:
        lines += [f"* {d['name']} - {d['use']}", f"  {d['attribution']}", f"  License: {d['license']}",
                  f"  {d['url']}" + (f" ({d['via']})" if d.get("via") else ""), ""]
    lines += [
        "This scenery contains information from OpenStreetMap, which is made available under the",
        "Open Database License (ODbL): https://opendatacommons.org/licenses/odbl/",
        "",
        "Tree models and textures are X-Plane 12's own default forests (referenced, not included).",
    ]
    return "\n".join(lines) + "\n"


def write_pack_credits(pack_root: Path, region: str) -> Path:
    pack_root.mkdir(parents=True, exist_ok=True)
    p = pack_root / CREDITS_FILE
    text = credits_text(region)
    if not p.exists() or p.read_text(encoding="utf-8-sig") != text:
        p.write_text(text, encoding="utf-8-sig")   # BOM: Notepad & co. show the © right
    return p
