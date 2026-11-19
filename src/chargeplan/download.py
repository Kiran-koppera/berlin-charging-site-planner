"""Download the public input data into data/raw (about 75 MB)."""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.parse
import urllib.request

from . import config

UA = {"User-Agent": "berlin-charging-site-planner/1.0 (open-data analysis)"}


def _curl(url: str, dest, data: bytes | None) -> None:
    cmd = ["curl", "-sfL", "--max-time", "300", "-A", UA["User-Agent"], "-o", str(dest), url]
    if data is not None:
        cmd[1:1] = ["--data-binary", "@-"]
    subprocess.run(cmd, input=data, check=True)


def fetch(url: str, dest, data: bytes | None = None, tries: int = 4) -> None:
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read()
            if not body or body[:1] == b"<" and dest.suffix == ".json":
                raise ValueError("empty or HTML response")
            dest.write_bytes(body)
            return
        except Exception as e:  # network hiccups, Overpass rate limits, old SSL builds
            if shutil.which("curl"):
                try:
                    _curl(url, dest, data)
                    if dest.stat().st_size > 0:
                        return
                except subprocess.CalledProcessError:
                    pass
            if attempt == tries:
                raise
            wait = 20 * attempt
            print(f"  retry {attempt} for {dest.name} in {wait}s ({e})")
            time.sleep(wait)


def download_all(force: bool = False, refresh_osm: bool = False) -> None:
    config.RAW.mkdir(parents=True, exist_ok=True)
    for name, url in config.SOURCES.items():
        dest = config.RAW / name
        if dest.exists() and not force:
            continue
        print(f"downloading {name}")
        fetch(url, dest)
    dest = config.RAW / "osm_pois.json"
    if refresh_osm:
        print("downloading osm_pois.json (Overpass API)")
        fetch(config.OVERPASS_URL, dest, data=urllib.parse.urlencode({"data": config.OVERPASS_QUERY}).encode())
    elif force or not dest.exists():
        # OpenStreetMap changes daily; the committed snapshot keeps results reproducible
        shutil.copy(config.OSM_SNAPSHOT, dest)
