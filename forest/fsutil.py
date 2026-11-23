"""Small file helpers shared by the data-folder move, the old-data migration, disk usage and
build history."""

from __future__ import annotations

import shutil
from pathlib import Path


def size_bytes(p: Path) -> int:
    """Bytes in a file, or in every file under a folder (0 if missing). Linked files are not
    counted: they belong to someone else."""
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def size_mb(p: Path) -> float:
    return size_bytes(p) / 1e6


def move_merge(src: Path, dst: Path, notes: list[str], report_moves: bool = False) -> None:
    """Move src to dst without ever overwriting: folders are merged item by item; an item that
    already exists in dst wins and the old copy stays where it was (reported in `notes`)."""
    if dst.exists() and src.resolve() == dst.resolve():
        return
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        if report_moves:
            notes.append(f"moved {src} -> {dst}")
        return
    if src.is_dir() and dst.is_dir():
        for child in sorted(src.iterdir()):
            move_merge(child, dst / child.name, notes, report_moves)
        try:
            src.rmdir()
        except OSError:
            pass  # something was left behind (a conflict, reported)
        return
    notes.append(f"kept {src} ({dst} already exists)")
