#!/bin/sh
# ForestStudio XP.app/Contents/MacOS/ForestStudioXP: start the app with the bundled Python.
# -I: isolated (ignores PYTHONPATH and the user's site-packages); the app is on the path via a .pth.
HERE="$(cd "$(dirname "$0")/.." && pwd)"
exec "$HERE/Resources/python/bin/python3" -I -m forest.app.main "$@"
