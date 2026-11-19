"""Command line: python -m chargeplan [download|run|all]"""

from __future__ import annotations

import argparse
import json

from . import config, download, pipeline


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="chargeplan")
    ap.add_argument("command", choices=["download", "run", "all"])
    ap.add_argument("--force", action="store_true", help="re-download data")
    ap.add_argument("--refresh-osm", action="store_true", help="query OpenStreetMap live instead of the snapshot")
    a = ap.parse_args(argv)
    if a.command in ("download", "all"):
        download.download_all(force=a.force, refresh_osm=a.refresh_osm)
    if a.command in ("run", "all"):
        s = pipeline.run()
        keys = ("points", "sites", "operators", "coverage", "backtest")
        print(json.dumps({k: s[k] for k in keys}, indent=1, default=str))
        print(f"outputs in {config.OUT}")


if __name__ == "__main__":
    main()
