"""
Streamlit page for the executive overview dashboard.

Data comes from the API (GET /dashboards/<name>) once per session; the date-range slider
filters it in the browser session, so moving the slider is instant and needs no new query.
Deep links can pre-set the range: ?page=executive_overview&date_from=2017-10&date_to=2017-12
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import api_client

PALETTE = ["#4F6EF7", "#F59E0B", "#10B981", "#8B5CF6", "#EC4899", "#06B6D4", "#94A3B8"]
BAR = "#4F6EF7"
LINE = "#F97316"
GOOD = "#10B981"
BAD = "#EF4444"
GRID = "rgba(128,128,128,0.18)"


@st.cache_data(ttl=600, show_spinner=False)
def _load(name: str) -> dict:
    return api_client.dashboard(name, {})


def _layout(fig, title: str, height: int = 380, stacked: bool = False):
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=16)),
        height=height, margin=dict(l=8, r=8, t=56, b=8), hovermode="x unified",
        legend=dict(orientation="h", y=-0.18, x=0), bargap=0.25,
        barmode="stack" if stacked else "group",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False, type="category")
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


def bar_and_line(df: pd.DataFrame, bar_col: str, line_col: str, bar_name: str, line_name: str, title: str):
    """Bars on the left axis with a trend line on the right axis."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=df["month"], y=df[bar_col], name=bar_name, marker_color=BAR, opacity=0.85)
    fig.add_scatter(x=df["month"], y=df[line_col], name=line_name, mode="lines+markers",
                    line=dict(color=LINE, width=3), marker=dict(size=7), secondary_y=True, connectgaps=False)
    fig.update_yaxes(title_text=bar_name, secondary_y=False)
    fig.update_yaxes(title_text=line_name, secondary_y=True, showgrid=False)
    return _layout(fig, title)


def stacked(seg: pd.DataFrame, title: str, colors: dict[str, str] | None = None, y_title: str = ""):
    """Stacked bars per month, one colour per segment (largest segment at the bottom)."""
    import plotly.graph_objects as go

    order = seg.groupby("segment")["value"].sum().sort_values(ascending=False).index.tolist()
    if "other" in order:
        order = [o for o in order if o != "other"] + ["other"]
    fig = go.Figure()
    for i, name in enumerate(order):
        part = seg[seg["segment"] == name]
        color = (colors or {}).get(name, PALETTE[i % len(PALETTE)])
        fig.add_bar(x=part["month"], y=part["value"], name=name.replace("_", " "), marker_color=color)
    fig.update_yaxes(title_text=y_title)
    return _layout(fig, title, stacked=True)


def delivery_chart(seg: pd.DataFrame, title: str):
    """On-time vs late orders stacked, with the late-share trend on the right axis."""
    from plotly.subplots import make_subplots

    pivot = seg.pivot_table(index="month", columns="segment", values="value", aggfunc="sum", fill_value=0).reset_index()
    for c in ("on_time", "late"):
        if c not in pivot:
            pivot[c] = 0.0
    pivot["late_pct"] = 100 * pivot["late"] / (pivot["late"] + pivot["on_time"]).where(lambda s: s > 0)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=pivot["month"], y=pivot["on_time"], name="on time", marker_color=GOOD)
    fig.add_bar(x=pivot["month"], y=pivot["late"], name="late", marker_color=BAD)
    fig.add_scatter(x=pivot["month"], y=pivot["late_pct"], name="late share (%)", mode="lines+markers",
                    line=dict(color="#111827", width=2.5), marker=dict(size=6), secondary_y=True)
    fig.update_yaxes(title_text="orders", secondary_y=False)
    fig.update_yaxes(title_text="late share (%)", secondary_y=True, showgrid=False, rangemode="tozero")
    return _layout(fig, title, stacked=True)


def _initial_range(months: list[str]) -> tuple[str, str]:
    lo, hi = months[0], months[-1]
    q_lo = (st.query_params.get("date_from") or "")[:7]
    q_hi = (st.query_params.get("date_to") or "")[:7]
    if q_lo in months:
        lo = q_lo
    if q_hi in months:
        hi = q_hi
    return (lo, hi) if lo <= hi else (months[0], months[-1])


def render_dashboard(name: str) -> None:
    try:
        data = _load(name)
    except api_client.ApiError as exc:
        st.error(f"Could not load the dashboard: {exc}")
        return

    base = pd.DataFrame(data.get("months", []))
    if base.empty:
        st.info("No data available.")
        return
    months = base["month"].tolist()

    st.title("📊 " + data.get("title", name))
    st.caption("Olist e-commerce, Sep 2016 – Oct 2018. Same metric definitions as the assistant, so the numbers match its answers.")

    lo, hi = st.select_slider("Date range", options=months, value=_initial_range(months), key="dash_range")
    sel = base[(base["month"] >= lo) & (base["month"] <= hi)].reset_index(drop=True)
    seg = {k: pd.DataFrame(v) for k, v in data.get("segments", {}).items()}

    def pick(df: pd.DataFrame) -> pd.DataFrame:
        return df[(df["month"] >= lo) & (df["month"] <= hi)] if not df.empty else df

    # KPI row for the selected range
    gmv, orders = sel["gmv"].sum(), sel["orders"].sum()
    deliv = pick(seg.get("delivery", pd.DataFrame()))
    late_pct = (100 * deliv.loc[deliv["segment"] == "late", "value"].sum() / deliv["value"].sum()) if not deliv.empty and deliv["value"].sum() else None
    scored = sel.dropna(subset=["avg_review_score"])
    review = (scored["avg_review_score"] * scored["orders"]).sum() / scored["orders"].sum() if not scored.empty else None
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("GMV", f"{gmv:,.0f}")
    k2.metric("Orders", f"{orders:,.0f}")
    k3.metric("Avg order value", f"{gmv / orders:,.2f}" if orders else "n/a")
    k4.metric("Avg review score", f"{review:.2f}" if review else "n/a")
    k5.metric("Late deliveries", f"{late_pct:.1f}%" if late_pct is not None else "n/a")
    st.caption(f"{lo} to {hi} ({len(sel)} months).")

    st.plotly_chart(bar_and_line(sel, "gmv", "orders", "GMV (BRL)", "Orders", "GMV (bars) and order volume (line)"),
                    use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        if not seg["category"].empty:
            st.plotly_chart(stacked(pick(seg["category"]), "Revenue by product category", y_title="BRL (item price)"),
                            use_container_width=True)
    with c2:
        if not seg["payment"].empty:
            st.plotly_chart(stacked(pick(seg["payment"]), "GMV by payment type", y_title="BRL"),
                            use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        if not deliv.empty:
            st.plotly_chart(delivery_chart(deliv, "Delivery performance: on time vs late"), use_container_width=True)
    with c4:
        st.plotly_chart(bar_and_line(sel, "aov", "avg_review_score", "Avg order value (BRL)", "Avg review score",
                                     "Order value (bars) and review score (line)"), use_container_width=True)
    st.caption("Review score is shown by review month; months without reviews appear as gaps.")
