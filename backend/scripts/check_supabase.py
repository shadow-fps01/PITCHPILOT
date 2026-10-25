"""Verify Supabase Postgres connectivity for the project in the root .env.

Tries, in order:
  1. Direct connection  — db.<ref>.supabase.co:5432 (user: postgres)
  2. Session-mode pooler — aws-<cluster>-<region>.pooler.supabase.com:5432 (user: postgres.<ref>)
  3. Transaction pooler  — same hosts on :6543

Prints the first working connection string plus the existing tables.

Run from `backend/`:
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\check_supabase.py
"""

import asyncio

import asyncpg

PROJECT_REF = "kkfhlpiksucvvtizeuzo"
PASSWORD = "Kamsy_2010.#"

CANDIDATE_REGIONS = [
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-north-1",
    "ap-southeast-1", "ap-southeast-2", "ap-northeast-1", "ap-northeast-2",
    "ap-south-1", "sa-east-1", "ca-central-1",
]
CLUSTERS = ["aws-0", "aws-1"]


async def try_connect(host: str, port: int, user: str) -> tuple[str, str]:
    """Return (label, 'OK <info>' | 'FAIL <error>') for one target."""
    label = f"{user}@{host}:{port}"
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(
                host=host,
                port=port,
                user=user,
                password=PASSWORD,
                database="postgres",
                ssl="require",
                timeout=8,
            ),
            timeout=12,
        )
    except Exception as exc:  # noqa: BLE001
        return label, f"FAIL {type(exc).__name__}: {exc}"
    try:
        version = await conn.fetchval("SELECT version()")
        tables = await conn.fetch(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE schemaname NOT IN ('pg_catalog','information_schema') "
            "ORDER BY 1, 2"
        )
        table_list = ", ".join(f"{r[0]}.{r[1]}" for r in tables)
        return (
            label,
            f"OK | {str(version).split(',')[0]} | tables: [{table_list}]",
        )
    finally:
        await conn.close()


async def main() -> None:
    print(f"Target project: {PROJECT_REF}\n")

    # 1) Direct connection.
    label, result = await try_connect(f"db.{PROJECT_REF}.supabase.co", 5432, "postgres")
    print(f"[direct]    {result}")
    if result.startswith("OK"):
        print("\nUSE THIS ->")
        print(
            f"postgresql+asyncpg://postgres:{PASSWORD.replace('#', '%23')}"
            f"@db.{PROJECT_REF}.supabase.co:5432/postgres"
        )
        return

    # 2/3) Poolers (session mode first, then transaction mode) — in parallel.
    pooler_targets: list[tuple[str, int, str]] = []
    for cluster in CLUSTERS:
        for region in CANDIDATE_REGIONS:
            pooler_targets.append((f"{cluster}-{region}.pooler.supabase.com", 5432, f"postgres.{PROJECT_REF}"))
    print(f"\nProbing {len(pooler_targets)} session-mode poolers in parallel (this can take ~10s)…")
    results = await asyncio.gather(*(try_connect(*t) for t in pooler_targets))

    working = [(lbl, res) for lbl, res in results if res.startswith("OK")]
    auth_failed = sum(1 for _, res in results if "authentication" in res or "password" in res.lower())

    if working:
        for lbl, res in working:
            print(f"\n[pooler OK] {lbl}")
            print(f"  {res}")
            host, _, _ = lbl.split("@")[1].rpartition(":")
            print("\nUSE THIS ->")
            print(
                f"postgresql+asyncpg://postgres.{PROJECT_REF}:{PASSWORD.replace('#', '%23')}"
                f"@{host}:5432/postgres"
            )
    else:
        print("\nNo pooler authenticated. Per-target failure summary (first 5):")
        for lbl, res in results[:5]:
            print(f"  {lbl} -> {res}")
        print(f"({auth_failed} targets failed at auth; total probed: {len(results)})")


if __name__ == "__main__":
    asyncio.run(main())
