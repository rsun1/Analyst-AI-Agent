"""
Analytics AI Agent: CLI flow.
Ask question → RAG → propose SQL + explanation → approve/edit loop → execute → insights.
"""
import sqlite3
import sys

from config import MAX_EDIT_ATTEMPTS, MAX_SQL_RETRIES, OLIST_DB_PATH
from db import execute_query
from insights import explain_results
from rag import retrieve
from sql_correction import get_corrected_sql
from sql_errors import classify_sqlite_error
from sql_generator import UNCLEAR_INSTRUCTION, generate_sql_and_explanation, revise_sql


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

    # Phase 2: Retrieve context and generate SQL + explanation
    print("\nRetrieving relevant schema context...")
    try:
        rag_context = retrieve(q)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Run ingestion first: python -m ingest")
        return
    except ValueError as e:
        print(f"Error: {e}")
        return

    print("Generating SQL and explanation...")
    try:
        sql, explanation = generate_sql_and_explanation(q, rag_context)
    except Exception as e:
        print(f"Error generating SQL: {e}")
        return

    if not sql:
        print("No SQL was generated. Please rephrase your question.")
        return

    # Phase 2 – approve / edit loop (FR-2.5, FR-2.6)
    _show_proposed(sql, explanation)
    approved = False
    for edit_attempt in range(MAX_EDIT_ATTEMPTS):
        confirm = input("Proceed? (y/n): ").strip().lower()

        if confirm in ("y", "yes"):
            approved = True
            break

        # User declined — ask what to change
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
                revised_sql, revised_explanation = revise_sql(
                    sql, explanation, instruction, rag_context
                )
            except Exception as e:
                print(f"Error revising SQL: {e}")
                break

            if revised_sql == UNCLEAR_INSTRUCTION:
                # FR-2.6: ambiguous instruction — do not count as an attempt
                print("Unclear instruction. Can you please rephrase.")
                continue

            # Got a valid revision — update, show, and break inner loop to re-ask Proceed?
            sql, explanation = revised_sql, revised_explanation
            _show_proposed(sql, explanation)
            break
        else:
            # inner while broke via error path — fall through to next edit_attempt
            pass

    if not approved:
        print(f"Too many attempts ({MAX_EDIT_ATTEMPTS}). Exiting without executing.")
        return

    # Phase 3: Execute (with self-correction), then explain
    current_sql = sql
    last_error: str | None = None
    for attempt in range(MAX_SQL_RETRIES + 1):
        try:
            columns, rows = execute_query(current_sql)
            break
        except sqlite3.OperationalError as e:
            last_error = str(e)
            classified = classify_sqlite_error(e)
            if attempt < MAX_SQL_RETRIES:
                print(f"\nQuery failed ({classified.kind}): {last_error}")
                print("Requesting corrected SQL from model...")
                try:
                    current_sql = get_corrected_sql(
                        current_sql, last_error, rag_context, classified=classified
                    )
                    print("Retrying with corrected query...")
                except Exception as fix_err:
                    print(f"Correction failed: {fix_err}")
                    break
            else:
                print(f"\nExecution failed after {MAX_SQL_RETRIES + 1} attempt(s): {last_error}")
                return
        except FileNotFoundError as e:
            print(f"Database not found: {e}")
            return
        except Exception as e:
            print(f"Error: {e}")
            return
    else:
        print(f"Execution failed: {last_error}")
        return

    print("\n--- Results ---")
    print_table(columns, rows)

    print("\n--- Summary & insights ---")
    try:
        summary = explain_results(q, current_sql, columns, rows)
        print(summary)
    except Exception as e:
        print(f"Could not generate insights: {e}")


def main() -> None:
    question = None
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    run_agent_loop(question)


if __name__ == "__main__":
    main()
