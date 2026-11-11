"""
Logging to Postgres (needs the local Postgres with `python -m setup_logging_db` run;
skipped otherwise). Test rows carry a marker question and are deleted afterwards.
"""
import logging

import psycopg
import pytest

import db
import logger
from config import PG_ADMIN_DSN, PG_LOGGER_DSN

MARK = "__pytest_marker__"


def _ready():
    try:
        with psycopg.connect(PG_ADMIN_DSN) as c:
            return c.execute("SELECT to_regclass('app_logs.query_events')").fetchone()[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="Postgres or app_logs schema not available")


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with psycopg.connect(PG_ADMIN_DSN) as c:
        c.execute("DELETE FROM app_logs.query_events WHERE question LIKE %s", (MARK + "%",))
        c.execute("DELETE FROM app_logs.feedback_events WHERE question LIKE %s", (MARK + "%",))


def _admin(query, params=()):
    with psycopg.connect(PG_ADMIN_DSN) as c:
        return c.execute(query, params).fetchall()


def test_query_event_is_stored():
    logger.log_query_event(MARK + "q", "SELECT 1", [{"type": "direct_edit", "sql_after": "SELECT 2"}],
                           "SELECT 2", ["oops"], 7)
    (row,) = _admin("SELECT proposed_sql, final_sql, was_corrected, revision_history, db_errors, row_count "
                    "FROM app_logs.query_events WHERE question = %s", (MARK + "q",))
    assert row[:3] == ("SELECT 1", "SELECT 2", True)
    assert row[3] == [{"type": "direct_edit", "sql_after": "SELECT 2"}] and row[4] == ["oops"] and row[5] == 7


def test_feedback_event_is_stored():
    logger.log_feedback_event(MARK + "f", "SELECT 1", "down", "wrong")
    assert _admin("SELECT rating, comment FROM app_logs.feedback_events WHERE question = %s",
                  (MARK + "f",)) == [("down", "wrong")]


def test_logger_role_is_insert_only():
    with psycopg.connect(PG_LOGGER_DSN) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM app_logs.query_events")
    with psycopg.connect(PG_LOGGER_DSN) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM app_logs.query_events")


def test_sql_agent_role_cannot_read_logs():
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.get_connection() as c:
            c.execute("SELECT * FROM app_logs.query_events")


def test_log_tables_are_invisible_to_schema_introspection():
    assert not any(t in db.get_live_schema() for t in ("query_events", "feedback_events"))


def test_failed_write_falls_back_to_process_log_and_never_raises(monkeypatch, caplog):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(logger, "get_log_connection", boom)
    logger._fallback.propagate = True  # let caplog see it
    try:
        with caplog.at_level(logging.WARNING, logger="analytics_agent.events"):
            logger.log_query_event("q", "SELECT 1", [], "SELECT 1", [], 1)  # must not raise
    finally:
        logger._fallback.propagate = False
    assert "log_write_failed" in caplog.text and "db down" in caplog.text
