"""Self-update helper for Lemonaid.

Remote `version.json` format:
{
  "version": "2.3.0",
  "notes": "Kurzbeschreibung",
  "url": "https://example.com/lemonaid-2.3.0.zip"
}

The zip should contain the game files at its root (lemonaid.py, assets/, …).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Change this to your hosted version.json URL
UPDATE_CHECK_URL = "https://example.com/lemonaid/version.json"

TIMEOUT_S = 8


def parse_version(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.strip().lstrip("vV").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def fetch_remote_info(url: str = UPDATE_CHECK_URL) -> dict | None:
    if not url or "example.com" in url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Lemonaid-Updater"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict) or "version" not in data or "url" not in data:
            return None
        return data
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None


def check_for_update(local_version: str, url: str = UPDATE_CHECK_URL) -> dict | None:
    info = fetch_remote_info(url)
    if not info:
        return None
    if is_newer(str(info["version"]), local_version):
        return info
    return None


def check_async(local_version: str, callback, url: str = UPDATE_CHECK_URL) -> threading.Thread:
    """Run update check in a daemon thread; callback(info|None) on the worker thread."""

    def worker():
        try:
            info = check_for_update(local_version, url)
        except Exception:
            info = None
        callback(info)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Lemonaid-Updater"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if not str(target).startswith(str(dest)):
            raise RuntimeError(f"Unsafe path in zip: {member.filename}")
    zf.extractall(dest)


def download_and_stage(info: dict) -> Path:
    """Download zip and extract into ROOT / '_update_staging'. Returns staging path."""
    staging = ROOT / "_update_staging"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "update.zip"
        _download(str(info["url"]), zip_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            _safe_extract(zf, staging)

        # If zip wrapped everything in a single top folder, unwrap it
        children = [p for p in staging.iterdir()]
        if len(children) == 1 and children[0].is_dir():
            inner = children[0]
            for item in inner.iterdir():
                shutil.move(str(item), str(staging / item.name))
            inner.rmdir()

    return staging


def write_apply_script(staging: Path) -> Path:
    """Write a small helper that copies staged files after this process exits."""
    script = ROOT / "_apply_update.py"
    # Paths as raw strings for Windows
    root_s = str(ROOT)
    staging_s = str(staging)
    py_s = sys.executable
    main_s = str(ROOT / "lemonaid.py")
    script.write_text(
        f'''# Auto-generated — applies a Lemonaid update then relaunches.
import os, shutil, sys, time
from pathlib import Path

ROOT = Path(r"{root_s}")
STAGING = Path(r"{staging_s}")
SKIP = {{"_update_staging", "_apply_update.py", "__pycache__"}}

time.sleep(0.8)

if not STAGING.is_dir():
    sys.exit(1)

for src in STAGING.rglob("*"):
    if not src.is_file():
        continue
    rel = src.relative_to(STAGING)
    if rel.parts and rel.parts[0] in SKIP:
        continue
    dest = ROOT / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(20):
        try:
            shutil.copy2(src, dest)
            break
        except PermissionError:
            time.sleep(0.25)

shutil.rmtree(STAGING, ignore_errors=True)
try:
    os.remove(__file__)
except OSError:
    pass

os.execv(r"{py_s}", [r"{py_s}", r"{main_s}"])
''',
        encoding="utf-8",
    )
    return script


def apply_and_restart(info: dict) -> None:
    """Download update, spawn apply helper, then exit current process."""
    staging = download_and_stage(info)
    script = write_apply_script(staging)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        # Prefer no extra console flash if possible
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", creationflags)
    subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        creationflags=creationflags,
        close_fds=True,
    )
    # Caller should quit pygame / exit
