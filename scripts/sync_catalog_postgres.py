#!/usr/bin/env python3
"""Upsert publish/inventory.json into Postgres (Render catalog).

Requires DATABASE_URL. Safe to run locally against a Render Postgres URL
when you are ready; does nothing useful without a database.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ENV = BASE / ".env"


def load_dotenv() -> None:
    if not ENV.is_file():
        return
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))


def main() -> int:
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL not set; skipping Postgres catalog sync.", file=sys.stderr)
        return 0

    try:
        import psycopg
    except ImportError:
        print("psycopg not installed. pip install -r requirements.txt", file=sys.stderr)
        return 1

    inv_path = BASE / "publish" / "inventory.json"
    man_path = BASE / "publish" / "manifest.json"
    if not inv_path.is_file():
        print("publish/inventory.json missing � run build_publish_bundle.sh first", file=sys.stderr)
        return 1

    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.is_file() else {}

    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(schema)
            cur.execute(
                """
                INSERT INTO publish_runs (built_at, packet_count, content_hash)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (
                    manifest.get("builtAt"),
                    manifest.get("packetCount", len(inv.get("pdfs", []))),
                    manifest.get("contentHash"),
                ),
            )
            run_id = cur.fetchone()[0]
            cur.execute("DELETE FROM packets")
            for row in inv.get("pdfs", []):
                cur.execute(
                    """
                    INSERT INTO packets (
                      path, name, section, pages, need_content, level,
                      official_at, content_hash, publish_run_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        row["file"],
                        row["name"],
                        row["section"],
                        row.get("pages") or 0,
                        row.get("needContent"),
                        row.get("level"),
                        row.get("officialAt"),
                        manifest.get("contentHash"),
                        run_id,
                    ),
                )
        conn.commit()
    print(f"Catalog upserted (publish_run_id={run_id}, packets={len(inv.get('pdfs', []))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
