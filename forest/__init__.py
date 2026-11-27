"""Forest Studio: X-Plane 12 forest overlay builder."""

# The one place the version lives: pyproject.toml reads it (setuptools dynamic version), the app
# shows it (header, Settings → About), every pack's CREDITS.txt records it, and the release
# workflow refuses a tag that isn't "v" + this.
__version__ = "0.1.0"
