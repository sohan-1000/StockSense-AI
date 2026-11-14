"""Create a LOCAL DEV copy of the StockSense retail tables and load the sample CSVs.

Uses the team's real retail schema and views (retail_schema.sql, retail_views.sql).
Only for developing and testing this app: point RETAIL_DATABASE_URL at the real
database and none of this is needed.

    python dev_retail_db.py            # creates database `stocksense_dev`
"""
import os
import sys

import psycopg
from dotenv import load_dotenv

CSV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "StockSense_AI_Retail_Dataset")
DEV_DB = "stocksense_dev"

HERE = os.path.dirname(os.path.abspath(__file__))


def _read(name):
    with open(os.path.join(HERE, name), encoding="utf8") as f:
        return f.read()


# the team's real retail tables and the three chatbot views
DDL = _read("retail_schema.sql") + "\n" + _read("retail_views.sql")

# load order respects foreign keys
LOAD_ORDER = ["suppliers", "products", "calendar", "inventory", "batches", "sales"]


def load_csvs(conn):
    for table in LOAD_ORDER:
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            print(f"  {table}: already has data, skipped")
            continue
        with open(os.path.join(CSV_DIR, f"{table}.csv"), "rb") as f:
            with conn.cursor().copy(f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
                while chunk := f.read(65536):
                    copy.write(chunk)
        n = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()[0]
        print(f"  {table}: {n} rows")


def main():
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set in .env")
    base, _ = url.rsplit("/", 1)

    with psycopg.connect(base + "/postgres", autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DEV_DB,)).fetchone():
            admin.execute(f'CREATE DATABASE "{DEV_DB}"')
            print(f"created database {DEV_DB}")

    with psycopg.connect(f"{base}/{DEV_DB}") as conn:
        conn.execute(DDL)
        load_csvs(conn)
    print("\nAdd this line to .env:")
    print(f"RETAIL_DATABASE_URL={base}/{DEV_DB}")


if __name__ == "__main__":
    main()
