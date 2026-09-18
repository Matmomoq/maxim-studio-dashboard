"""Apply the non-destructive monthly marketing budget schema migration."""

from pathlib import Path

import pymysql
from db_security import connect as secure_connect, tls_context

from amocrm_client import load_env


def main() -> None:
    env = load_env(".env")
    sql = Path("marketing_budgets_schema.sql").read_text(encoding="utf-8")
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    connection = secure_connect(
        host=env["AMO_DB_HOST"],
        port=int(env.get("AMO_DB_PORT", "3306")),
        user=env["AMO_DB_USER"],
        password=env["AMO_DB_PASSWORD"],
        database=env["AMO_DB_NAME"],
        charset="utf8mb4",
        autocommit=False,
        ssl=tls_context(env),
        connect_timeout=int(env.get("AMO_DB_CONNECT_TIMEOUT", "10")),
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print("Marketing budget schema is ready")


if __name__ == "__main__":
    main()
