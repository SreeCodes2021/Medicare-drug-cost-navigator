#!/usr/bin/env python3
"""Emit a supercronic crontab line from config/deploy.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_CONFIG = ROOT / "config" / "deploy.yaml"
INGEST_SCRIPT = ROOT / "scripts" / "run-daily-ingest.sh"


def main() -> None:
    data = yaml.safe_load(DEPLOY_CONFIG.read_text(encoding="utf-8"))
    schedule = data.get("ingest", {}).get("cron", "0 3 * * *")
    print(f"{schedule} {INGEST_SCRIPT}")


if __name__ == "__main__":
    main()
