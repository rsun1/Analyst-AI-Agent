"""
Auto chart detection and Plotly figure construction.
Pure function — no OpenAI calls, no side effects.
Returns a plotly Figure or None based on the result set shape.
"""

try:
    import plotly.express as px
    import plotly.graph_objects as go
    _PLOTLY_AVAILABLE = True
except ImportError:
    _PLOTLY_AVAILABLE = False


# ── Heuristics ─────────────────────────────────────────────────────────────────

_DATE_KEYWORDS = {"date", "month", "year", "week", "day", "time", "period", "quarter"}
_CHART_LAYOUT = dict(template="plotly_white", height=400)


def _looks_like_date(col_name: str, sample_values: list) -> bool:
    """
    True if the column name contains a date keyword OR most non-null values
    look like ISO date strings (YYYY-MM...).
    Excludes columns whose values are plain small integers (day-of-month, etc.).
    """
    name_lower = col_name.lower()
    if any(kw in name_lower for kw in _DATE_KEYWORDS):
        non_null = [v for v in sample_values if v is not None]
        if not non_null:
            return True
        # Exclude plain integers under 32 (e.g. day numbers, month numbers)
        try:
            as_ints = [int(v) for v in non_null]
            if all(1 <= i <= 9999 for i in as_ints) and all(i <= 31 for i in as_ints):
                return False  # looks like day/month integers, not date strings
        except (ValueError, TypeError):
            pass
        return True
    # Check values for ISO date pattern (YYYY-MM or YYYY-MM-DD)
    non_null = [str(v) for v in sample_values if v is not None]
    if not non_null:
        return False
    hits = sum(1 for v in non_null if len(v) >= 7 and v[:4].isdigit() and v[4] == "-")
    return hits / len(non_null) > 0.5


def _looks_like_numeric(col_name: str, sample_values: list) -> bool:
    """True if > 80% of non-null values can be cast to float."""
    non_null = [v for v in sample_values if v is not None]
    if not non_null:
        return False
    hits = 0
    for v in non_null:
        try:
            float(v)
            hits += 1
        except (ValueError, TypeError):
            pass
    return hits / len(non_null) > 0.8


def _looks_like_categorical(col_name: str, sample_values: list, n_rows: int) -> bool:
    """
    True if the column contains string-like values, is not a date, and has
    reasonable cardinality (not a unique ID column).
    """
    if _looks_like_date(col_name, sample_values):
        return False
    if _looks_like_numeric(col_name, sample_values):
        return False
    non_null = [v for v in sample_values if v is not None]
    if not non_null:
        return False
    # Reject if all values are unique and count matches rows (likely an ID column)
    if len(set(str(v) for v in non_null)) == n_rows and n_rows > 10:
        return False
    return True


def _classify_columns(
    columns: list[str], rows: list[tuple]
) -> tuple[str, dict[str, list[str]]]:
    """
    Classify the result shape. Returns (chart_type, column_roles) where
    chart_type is one of: 'time_series', 'multi_series', 'categorical',
    'distribution', 'none'.
    column_roles maps role names to column name lists.
    """
    n_rows = len(rows)

    # Hard fallback conditions
    if n_rows == 0 or n_rows == 1 or len(columns) > 6:
        return "none", {}

    # Build a sample of up to 20 values per column
    samples: dict[str, list] = {}
    for i, col in enumerate(columns):
        samples[col] = [row[i] for row in rows[:20]]

    date_cols = [c for c in columns if _looks_like_date(c, samples[c])]
    numeric_cols = [c for c in columns if c not in date_cols and _looks_like_numeric(c, samples[c])]
    cat_cols = [c for c in columns if c not in date_cols and c not in numeric_cols and _looks_like_categorical(c, samples[c], n_rows)]

    # Decision tree (priority order)
    if len(date_cols) > 1:
        return "none", {}
    if len(date_cols) == 1 and len(numeric_cols) >= 2:
        return "multi_series", {"date": date_cols, "values": numeric_cols}
    if len(date_cols) == 1 and len(numeric_cols) == 1:
        return "time_series", {"date": date_cols, "value": numeric_cols}
    if len(cat_cols) == 1 and len(numeric_cols) == 1:
        return "categorical", {"category": cat_cols, "value": numeric_cols}
    if len(columns) == 1 and len(numeric_cols) == 1:
        return "distribution", {"value": numeric_cols}

    return "none", {}


# ── Chart builders ─────────────────────────────────────────────────────────────

def _rows_to_dicts(columns: list[str], rows: list[tuple]) -> list[dict]:
    return [dict(zip(columns, row)) for row in rows]


def _make_line_chart(date_col: str, value_col: str, data: list[dict]):
    fig = px.line(
        data, x=date_col, y=value_col,
        title=f"{value_col} over time",
        labels={date_col: date_col, value_col: value_col},
        **_CHART_LAYOUT,
    )
    fig.update_layout(xaxis_title=date_col, yaxis_title=value_col)
    return fig


def _make_multi_line_chart(date_col: str, value_cols: list[str], data: list[dict]):
    fig = go.Figure()
    for col in value_cols:
        fig.add_trace(go.Scatter(
            x=[row[date_col] for row in data],
            y=[row[col] for row in data],
            mode="lines+markers",
            name=col,
        ))
    fig.update_layout(
        title=f"{', '.join(value_cols)} over time",
        xaxis_title=date_col,
        yaxis_title="value",
        **_CHART_LAYOUT,
    )
    return fig


def _make_bar_chart(cat_col: str, value_col: str, data: list[dict]):
    sorted_data = sorted(data, key=lambda r: (r[value_col] or 0), reverse=True)
    fig = px.bar(
        sorted_data, x=cat_col, y=value_col,
        title=f"{value_col} by {cat_col}",
        labels={cat_col: cat_col, value_col: value_col},
        **_CHART_LAYOUT,
    )
    fig.update_layout(xaxis_tickangle=-30)
    return fig


def _make_histogram(value_col: str, data: list[dict]):
    fig = px.histogram(
        data, x=value_col,
        title=f"Distribution of {value_col}",
        labels={value_col: value_col},
        **_CHART_LAYOUT,
    )
    return fig


# ── Public API ─────────────────────────────────────────────────────────────────

def detect_chart(columns: list[str], rows: list[tuple]):
    """
    Inspect result shape and return an appropriate Plotly Figure, or None.
    Never raises — returns None on any error or when no chart is appropriate.
    """
    if not _PLOTLY_AVAILABLE:
        return None

    try:
        if not columns or not rows or len(rows) > 10_000:
            return None

        chart_type, roles = _classify_columns(columns, rows)
        data = _rows_to_dicts(columns, rows)

        if chart_type == "time_series":
            return _make_line_chart(roles["date"][0], roles["value"][0], data)

        if chart_type == "multi_series":
            return _make_multi_line_chart(roles["date"][0], roles["values"], data)

        if chart_type == "categorical":
            return _make_bar_chart(roles["category"][0], roles["value"][0], data)

        if chart_type == "distribution":
            return _make_histogram(roles["value"][0], data)

        return None

    except Exception:
        return None
