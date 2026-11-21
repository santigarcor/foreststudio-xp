# DSFTool (bundled)

ForestStudio XP ships **DSFTool** from Laminar Research's X-Plane Scenery Tools
(XPTools 24-5, DSFTool 2.4.0-b1) so you don't have to download anything else.
DSFTool converts DSF scenery files between X-Plane's binary format and text; ForestStudio
uses it to compile each forest tile and to read elevation from X-Plane's base mesh.

- Source and release: https://github.com/X-Plane/xptools and
  https://developer.x-plane.com/tools/xptools/
- License: code original to Laminar Research is available under the **MIT/X11 license**;
  the libraries it uses have licenses compatible with MIT/X11 or GPL (see `README_FIRST`,
  section "Installation, system requirements and licensing").
- Usage reference: `README.dsf2text`.

| Platform | File | Build |
|---|---|---|
| Windows | `windows/DSFTool.exe` | x86-64 |
| macOS | `macos/DSFTool` | universal (Intel x86-64 + Apple Silicon arm64) |
| Linux | `linux/DSFTool` | x86-64 (glibc) |

ForestStudio picks the file for the system it runs on (and marks it executable on macOS/Linux
if needed). A different DSFTool can be set by hand in `~/.foreststudio/config/local.toml`
(`[tools] dsftool = '...'`); it's used only if that file exists.
