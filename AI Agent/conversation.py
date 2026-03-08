"""
Multi-turn conversation history management.
Stores user questions and assistant summaries (not raw result sets) so that
follow-up questions carry context through to SQL generation.
"""

# Keep at most this many turns in the history passed to the LLM.
# 6 entries = 3 user questions + 3 assistant responses ≈ ~2 000 tokens max.
MAX_HISTORY_TURNS = 6


def append_user_turn(history: list[dict], question: str) -> None:
    """Append a user question to the history list in place."""
    history.append({"role": "user", "content": question})


def append_assistant_turn(history: list[dict], sql: str, summary: str) -> None:
    """
    Append an assistant turn. Stores the SQL and the insight summary,
    NOT the raw result set (to keep token count bounded).
    """
    history.append({"role": "assistant", "content": summary, "sql": sql})


def trim_history(history: list[dict]) -> list[dict]:
    """Return a new list containing only the last MAX_HISTORY_TURNS entries."""
    return history[-MAX_HISTORY_TURNS:]


def build_history_block(history: list[dict]) -> str:
    """
    Convert trimmed history entries into a text block for LLM injection.
    Returns "" if history is empty.

    Format:
        === CONVERSATION HISTORY (most recent last) ===
        [Turn N - User]: ...
        [Turn N - Assistant summary]: ...
        [Turn N - Assistant SQL]: ...
        === END HISTORY ===
    """
    if not history:
        return ""

    lines = ["=== CONVERSATION HISTORY (most recent last) ===", ""]
    turn = 1
    i = 0
    while i < len(history):
        entry = history[i]
        if entry["role"] == "user":
            lines.append(f"[Turn {turn} - User]: {entry['content']}")
            # Look ahead for the matching assistant turn
            if i + 1 < len(history) and history[i + 1]["role"] == "assistant":
                asst = history[i + 1]
                lines.append(f"[Turn {turn} - Assistant summary]: {asst['content']}")
                if asst.get("sql"):
                    lines.append(f"[Turn {turn} - Assistant SQL]: {asst['sql']}")
                i += 2
            else:
                i += 1
            lines.append("")
            turn += 1
        else:
            # Orphaned assistant turn (shouldn't normally happen)
            i += 1

    lines.append("=== END HISTORY ===")
    return "\n".join(lines)
