"""KB snapshot restore (scripts/restore_kb.sh).

Tar overlays merge directories. Restore must clear live raw/ before extract
so post-snapshot evidence cannot masquerade as restored state.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RESTORE_SH = REPO_ROOT / "scripts" / "restore_kb.sh"


def _bash() -> str | None:
    # Prefer Git bash on Windows. WSL's system32\bash.exe strips backslashes
    # from Windows paths and cannot run a repo-local script.
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    for candidate in (
        program_files / "Git" / "bin" / "bash.exe",
        program_files / "Git" / "usr" / "bin" / "bash.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("bash")
    if found and "system32" not in found.lower().replace("\\", "/"):
        return found
    return None


@pytest.mark.skipif(_bash() is None, reason="bash required to run restore_kb.sh")
def test_restore_clears_stale_raw_files(tmp_path):
    """A file created after the snapshot must not survive restore."""
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    data = repo / "backend" / "data"
    stale = data / "raw" / "stale-run"
    stale.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (stale / "leftover.json").write_text("post-snapshot", encoding="utf-8")
    (data / "crs01.db").write_text("live-db", encoding="utf-8")

    snap_root = tmp_path / "snap"
    snap_raw = snap_root / "raw" / "snap-run"
    snap_raw.mkdir(parents=True)
    (snap_raw / "ok.json").write_text("from-snapshot", encoding="utf-8")
    (snap_root / "crs01.db").write_text("snap-db", encoding="utf-8")
    # Relative archive path: GNU tar treats `C:/...` as host:path, and the
    # script cds to the repo root before opening ARCHIVE (documented usage).
    archive = repo / "snap.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(snap_root / "raw", arcname="raw")
        tar.add(snap_root / "crs01.db", arcname="crs01.db")

    shutil.copy(RESTORE_SH, scripts / "restore_kb.sh")
    script = scripts / "restore_kb.sh"
    result = subprocess.run(
        [_bash(), script.as_posix(), "snap.tar.gz"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    leftover = data / "raw" / "stale-run" / "leftover.json"
    restored = data / "raw" / "snap-run" / "ok.json"
    assert not leftover.exists(), "stale raw/ file survived restore"
    assert restored.read_text(encoding="utf-8") == "from-snapshot"
    assert (data / "crs01.db").read_text(encoding="utf-8") == "snap-db"
