"""
Analytics AI Agent: CLI flow.
Ask question → RAG → propose SQL + explanation → approve/edit loop → execute → insights.

All business logic lives in agent_core.py and is shared with the Streamlit UI (app.py).
This module only handles CLI I/O.
"""
import sys

from agent_core import (
    UNCLEAR_INSTRUCTION,
    step_execute,
    step_explain,
    step_generate,
    step_rag,
    step_revise,
)
from config import MAX_EDIT_ATTEMPTS
from schema_loader import validate_catalog_against_db


def print_table(columns: list[str], rows: list[tuple], max_display: int = 50) -> None:
    """Print result set as a simple table to the console."""
    if not columns:
        print("(No columns)")
        return
    sample = rows[:max_display]
    widths = []
    for i in range(len(columns)):
        w = len(str(columns[i]))
        if sample:
            w = max(w, max(len(str(r[i])) for r in sample))
        widths.append(min(w, 24))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*columns))
    print("-" * (sum(widths) + 2 * (len(columns) - 1)))
    for row in rows[:max_display]:
        print(fmt.format(*(str(x)[:24] for x in row)))
    if len(rows) > max_display:
        print(f"... ({len(rows) - max_display} more rows)")


def _show_proposed(sql: str, explanation: str) -> None:
    """Print the proposed SQL and explanation."""
    print("\n--- Proposed query ---")
    print("Reason:", explanation)
    print("\nSQL:")
    print(sql)
    print("---")


def run_agent_loop(question: str | None = None) -> None:
    """
    One full cycle: question → propose SQL → approve/edit loop → execute → explain.
    If question is None, prompt from stdin.
    """
    q = question or input("Your question about the Olist data: ").strip()
    if not q:
        print("No question entered. Exiting.")
        return

    # Phase 2: Retrieve context and generate SQL + explanation.
    print("\nRetrieving relevant schema context...")
    try:
        rag_context = step_rag(q)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Run ingestion first: python -m ingest")
        return
    except ValueError as e:
        print(f"Error: {e}")
        return

    print("Generating SQL and explanation...")
    try:
        sql, explanation = step_generate(q, rag_context)
    except Exception as e:
        print(f"Error generating SQL: {e}")
        return

    if not sql:
        print("No SQL was generated. Please rephrase your question.")
        return

    # Phase 2 – approve / edit loop (FR-2.5, FR-2.6).
    _show_proposed(sql, explanation)
    approved = False
    for edit_attempt in range(MAX_EDIT_ATTEMPTS):
        confirm = input("Proceed? (y/n): ").strip().lower()

        if confirm in ("y", "yes"):
            approved = True
            break

        # User declined — ask what to change.
        while True:
            instruction = input("What needs to be changed? ").strip()
            if not instruction:
                print("Please describe what you'd like to change, or type 'n' to abort.")
                continue

            if instruction.lower() in ("abort", "cancel", "quit", "exit", "n", "no"):
                print("Aborted.")
                return

            print("Revising SQL...")
            try:
                revised_sql, revised_explanation = step_revise(
                    original_question=q,
                    rag_context=rag_context,
                    current_sql=sql,
                    current_explanation=explanation,
                    revision_instruction=instruction,
                    last_db_error=None,
                )
            except Exception as e:
                print(f"Error revising SQL: {e}")
                break

            if revised_sql == UNCLEAR_INSTRUCTION:
                # FR-2.6: ambiguous instruction — do not count as an attempt.
                print("Unclear instruction. Can you please rephrase.")
                continue

            # Got a valid revision — update, show, and break inner loop to re-ask Proceed?
            sql, explanation = revised_sql, revised_explanation
            _show_proposed(sql, explanation)
            break

    if not approved:
        print(f"Too many attempts ({MAX_EDIT_ATTEMPTS}). Exiting without executing.")
        return

    # Phase 3: Execute (with self-correction), then explain.
    try:
        print("\nExecuting query...")
        columns, rows = step_execute(sql, rag_context)
    except FileNotFoundError as e:
        print(f"Database not found: {e}")
        return
    except Exception as e:
        print(f"\nExecution failed: {e}")
        return

    print("\n--- Results ---")
    print_table(columns, rows)

    print("\n--- Summary & insights ---")
    try:
        summary = step_explain(q, sql, columns, rows)
        print(summary)
    except Exception as e:
        print(f"Could not generate insights: {e}")


def main() -> None:
    # Validate schema catalog against live DB at startup; warn but do not crash.
    drift = validate_catalog_against_db()
    if drift:
        print("⚠  Schema catalog drift detected:")
        for d in drift:
            print(f"   {d}")
        print()

    question = None
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    run_agent_loop(question)


if __name__ == "__main__":
    main()
