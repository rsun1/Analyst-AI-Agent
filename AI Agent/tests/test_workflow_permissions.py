"""
Database permissions of the `olist_workflow` role, tested with a direct connection as that
role (not through the app code). These are the guarantees the README makes: the audit log is
append-only for the application, nothing can be deleted, and the Olist data is out of reach.
Skipped if the local Postgres or the workflow schema is not available.
"""
import psycopg
import pytest

from config import PG_ADMIN_DSN, PG_WORKFLOW_DSN

MARK = "__pytest_marker__"


def _ready():
    try:
        with psycopg.connect(PG_ADMIN_DSN, connect_timeout=3) as c:
            return c.execute("SELECT to_regclass('workflow.audit_log')").fetchone()[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="Postgres or workflow schema not available")


@pytest.fixture
def role():
    """Autocommit connection as the workflow role, with one dispute and one audit row to aim at."""
    conn = psycopg.connect(PG_WORKFLOW_DSN, autocommit=True)
    did = conn.execute(
        "INSERT INTO workflow.disputes (order_id, customer_id, reason_text, amount_requested, status) "
        "VALUES (%s, 'c', 't', 10, 'submitted') RETURNING id", (MARK,)).fetchone()[0]
    conn.execute(
        "INSERT INTO workflow.audit_log (dispute_id, action, actor_type, actor_id, to_status, reasoning) "
        "VALUES (%s, 'dispute_submitted', 'human', 'customer', 'submitted', 'test')", (did,))
    yield conn, did
    conn.close()
    with psycopg.connect(PG_ADMIN_DSN) as admin:
        ids = "SELECT id FROM workflow.disputes WHERE order_id = %s"
        admin.execute(f"DELETE FROM workflow.audit_log WHERE dispute_id IN ({ids})", (MARK,))
        admin.execute("DELETE FROM workflow.disputes WHERE order_id = %s", (MARK,))


@pytest.mark.parametrize("statement", [
    "UPDATE workflow.audit_log SET reasoning = 'rewritten'",
    "UPDATE workflow.audit_log SET dispute_id = dispute_id",
    "DELETE FROM workflow.audit_log",
    "TRUNCATE workflow.audit_log",
])
def test_the_audit_log_cannot_be_changed_by_the_application_role(role, statement):
    conn, did = role
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement)
    assert conn.execute("SELECT reasoning FROM workflow.audit_log WHERE dispute_id = %s", (did,)).fetchone() == ("test",)


@pytest.mark.parametrize("statement", [
    "DELETE FROM workflow.disputes",
    "DELETE FROM workflow.approvals",
    "DELETE FROM workflow.refund_attempts",
    "TRUNCATE workflow.disputes CASCADE",
])
def test_nothing_in_the_workflow_schema_can_be_deleted(role, statement):
    conn, _ = role
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement)


def test_the_role_cannot_change_the_schema(role):
    conn, _ = role
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("ALTER TABLE workflow.audit_log ADD COLUMN sneaky text")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DROP TABLE workflow.audit_log")


@pytest.mark.parametrize("table", ["orders", "order_payments", "customers", "order_reviews"])
def test_the_olist_data_is_out_of_reach(role, table):
    conn, _ = role
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(f"SELECT 1 FROM public.{table} LIMIT 1")


def test_it_can_still_do_its_job(role):
    """The restrictions above must not break the allowed operations."""
    conn, did = role
    conn.execute("UPDATE workflow.disputes SET status = 'rejected' WHERE id = %s", (did,))
    conn.execute(
        "INSERT INTO workflow.audit_log (dispute_id, action, actor_type, actor_id, from_status, to_status, reasoning) "
        "VALUES (%s, 'policy_rejected', 'agent', 'a', 'submitted', 'rejected', 'ok')", (did,))
    assert conn.execute("SELECT count(*) FROM workflow.audit_log WHERE dispute_id = %s", (did,)).fetchone()[0] == 2
