"""Connection pool behaviour (needs the local Postgres; skipped if it is down)."""
import psycopg
import pytest

import db


def _up():
    try:
        with db.get_connection() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _up(), reason="local Postgres not running")


def test_connections_are_reused():
    pids = set()
    for _ in range(20):
        with db.get_connection() as c:
            pids.add(c.execute("SELECT pg_backend_pid()").fetchone()[0])
    # 20 sequential borrows must not open 20 backend connections.
    assert len(pids) <= db._get_pool('reader').get_stats()["pool_size"] < 20


def test_pooled_connection_is_still_read_only():
    with pytest.raises(psycopg.Error):
        with db.get_connection() as c:
            c.execute("CREATE TABLE should_not_exist (x int)")


def test_failed_query_does_not_poison_the_pool():
    with pytest.raises(psycopg.Error):
        with db.get_connection() as c:
            c.execute("SELECT * FROM no_such_table")
    with db.get_connection() as c:
        assert c.execute("SELECT 1").fetchone() == (1,)


def test_timeout_setting_applies_to_pooled_connections():
    with db.get_connection() as c:
        assert c.execute("SHOW statement_timeout").fetchone()[0] == "15s"
