#!/usr/bin/env python3
"""Apply the additive plan/fact schema to the amoCRM dashboard database."""

from pathlib import Path

import pymysql
from db_security import connect as secure_connect, tls_context

from amocrm_client import load_env


def main() -> None:
    env = load_env(".env")
    connection = secure_connect(
        host=env["AMO_DB_HOST"],
        port=int(env.get("AMO_DB_PORT", "3306")),
        user=env["AMO_DB_USER"],
        password=env["AMO_DB_PASSWORD"],
        database=env["AMO_DB_NAME"],
        charset="utf8mb4",
        autocommit=True,
        ssl=tls_context(env),
        cursorclass=pymysql.cursors.DictCursor,
    )
    sql = Path(__file__).with_name("plan_fact_schema.sql").read_text(encoding="utf-8")
    try:
        with connection.cursor() as cursor:
            for statement in (part.strip() for part in sql.split(";")):
                if statement:
                    cursor.execute(statement)
    finally:
        connection.close()
    print("Plan/fact schema is ready.")


if __name__ == "__main__":
    main()
