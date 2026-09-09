#!/usr/bin/env python3
"""Emit supercronic crontab lines from config/deploy.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_CONFIG = ROOT / "config" / "deploy.yaml"
INGEST_SCRIPT = ROOT / "scripts" / "run-daily-ingest.sh"


def main() -> None:
    data = yaml.safe_load(DEPLOY_CONFIG.read_text(encoding="utf-8"))
    ingest = data.get("ingest", {})
    nightly = ingest.get("cron", "0 7 * * *")
    pharmacy = ingest.get("pharmacy_cron", "0 8 * * 0")
    print(f"{nightly} {INGEST_SCRIPT}")
    print(f"{pharmacy} {INGEST_SCRIPT} --with-pharmacy-network --force")


if __name__ == "__main__":
    main()
