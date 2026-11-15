"""
One-off migration: turn the existing metric definitions in olist_schema_queries.md
into skills/<domain>/<name>.md files.

SQL is taken verbatim from the markdown, except where an override below fixes a
known problem (found by skillgen/check.py): Postgres rejects a SELECT alias in
HAVING, and joining order_payments to order_items multiplies payment_value by the
number of items in the order (fan-out).

Usage (from "AI Agent/"):  python -m skillgen.migrate_existing
"""
from __future__ import annotations

import re
from pathlib import Path

from config import BASE_DIR, SCHEMA_DOC_PATH

OUT = BASE_DIR / "skills"

# name -> fixed SQL (replaces the markdown block). Reason is shown in the skill's Pitfalls.
SQL_OVERRIDES: dict[str, dict[str, str]] = {
    "seller_tpv_per_month": {"main": """SELECT
  oi.seller_id,
  TO_CHAR(o.order_purchase_timestamp, 'YYYY-MM') AS order_month,
  SUM(oi.price + oi.freight_value) AS seller_gmv
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
GROUP BY oi.seller_id, order_month
ORDER BY order_month, seller_gmv DESC"""},
    "top_10_sellers_by_revenue": {"main": """SELECT
  oi.seller_id,
  SUM(oi.price + oi.freight_value) AS total_revenue
FROM order_items oi
GROUP BY oi.seller_id
ORDER BY total_revenue DESC
LIMIT 10"""},
    "freight_to_price_ratio_by_category": {"main": """SELECT
  pct.product_category_name_english,
  AVG(CASE WHEN oi.price > 0 THEN oi.freight_value / oi.price END) AS avg_freight_to_price,
  COUNT(*) AS item_count
FROM order_items oi
JOIN products p ON p.product_id = oi.product_id
JOIN product_category_name_translation pct ON pct.product_category_name = p.product_category_name
GROUP BY pct.product_category_name_english
HAVING COUNT(*) >= 10
ORDER BY avg_freight_to_price DESC"""},
    "leads_generated_from_orders_by_period_and_state": {
        "leads_in_period": """-- parameters: period 2017-07-01..2017-09-01 (half-open), states BA, MA, SP
SELECT COUNT(DISTINCT lq.mql_id) AS leads_generated
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN order_items oi ON oi.order_id = o.order_id
JOIN leads_closed lc ON lc.seller_id = oi.seller_id
JOIN leads_qualified lq ON lq.mql_id = lc.mql_id
WHERE o.order_purchase_timestamp >= '2017-07-01' AND o.order_purchase_timestamp < '2017-09-01'
  AND c.customer_state IN ('BA', 'MA', 'SP')""",
        "total_leads_contacted": """SELECT COUNT(DISTINCT mql_id) AS total_leads_contacted
FROM leads_qualified""",
        "combined": """SELECT
  COUNT(DISTINCT lq.mql_id) AS leads_generated,
  (SELECT COUNT(DISTINCT mql_id) FROM leads_qualified) AS total_leads_contacted_ever
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN order_items oi ON oi.order_id = o.order_id
JOIN leads_closed lc ON lc.seller_id = oi.seller_id
JOIN leads_qualified lq ON lq.mql_id = lc.mql_id
WHERE o.order_purchase_timestamp >= '2017-07-01' AND o.order_purchase_timestamp < '2017-09-01'
  AND c.customer_state IN ('BA', 'MA', 'SP')""",
    },
    "seller_order_volume_with_lead_status": {"main": """SELECT
  oi.seller_id,
  COUNT(DISTINCT o.order_id) AS order_count,
  SUM(oi.price + oi.freight_value) AS total_revenue,
  CASE WHEN lc.seller_id IS NOT NULL THEN 'in_leads' ELSE 'not_in_leads' END AS lead_status
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
LEFT JOIN leads_closed lc ON lc.seller_id = oi.seller_id
WHERE o.order_purchase_timestamp >= '2017-01-01' AND o.order_purchase_timestamp < '2018-01-01'
GROUP BY oi.seller_id, lead_status
ORDER BY total_revenue DESC"""},
}

# Skill metadata (authored by hand). Keys are skill names; "src" = source definition names.
SKILLS: dict[str, dict] = {
    "monthly_gmv": dict(
        domain="sales", title="Monthly GMV", src=["monthly_gmv"],
        description="Gross merchandise value per calendar month: total customer payment value of orders by purchase month.",
        triggers=["What was our GMV per month?", "Show monthly gross merchandise value", "How much did customers pay each month in 2017?"],
        disambiguation="GMV here is payment_value (includes freight and vouchers). For product/seller revenue without freight use revenue_by_product_category.",
        tables=["orders", "order_payments"], grain="month", dimensions=["month"],
        eval=["What was the GMV in each month of 2017?", "Which month had the highest GMV?"],
        pitfalls=["order_payments has several rows per order (installments, vouchers): never join it to order_items before summing, or payment_value is multiplied by the item count.",
                  "Group by the purchase month (order_purchase_timestamp), not delivery month.",
                  "2016 and late 2018 months are sparse; say so when the user asks about them."],
        interp="Compare adjacent months for trend; the first and last months of the dataset are incomplete."),
    "seller_tpv_per_month": dict(
        domain="sellers", title="Seller GMV per month", src=["seller_tpv_per_month"],
        description="Sales value (item price plus freight) for each seller per calendar month, by order purchase month.",
        triggers=["What was each seller's monthly sales?", "Seller TPV per month", "How much did seller X sell in each month?"],
        disambiguation="payment_value cannot be attributed to one seller when an order has several sellers, so seller sales use order_items price + freight_value. For the top sellers overall use top_10_sellers_by_revenue.",
        tables=["orders", "order_items"], grain="seller-month", dimensions=["seller_id", "month"],
        eval=["Which seller had the highest sales in November 2017?", "How many sellers had sales in each month of 2017?"],
        pitfalls=["Do NOT sum order_payments.payment_value over order_items rows: it is repeated once per item in the order and inflates totals.",
                  "Original definition used payment_value; it was replaced because it double counted multi-item orders."],
        interp="Large values concentrate in a few sellers; use top_10_sellers_by_revenue for rankings."),
    "revenue_by_product_category": dict(
        domain="sales", title="Revenue by product category", src=["revenue_by_product_category"],
        description="Total item-level revenue (sum of order_items.price) per product category, with English category names.",
        triggers=["Which product categories make the most revenue?", "Top 5 categories by revenue", "Revenue by category"],
        disambiguation="Uses item price, excludes freight and payment adjustments. For total customer payments use monthly_gmv.",
        tables=["order_items", "products", "product_category_name_translation"], grain="category", dimensions=["product_category_name_english"],
        eval=["What are the top 5 product categories by total revenue?", "How much revenue did the health_beauty category generate?"],
        pitfalls=["Use order_items.price, NOT order_payments.payment_value: payments are per order and double count orders with several categories.",
                  "product_category_name is Portuguese; always JOIN product_category_name_translation and use product_category_name_english.",
                  "Inner join drops products without a category; that is a small share."],
        interp="Revenue is concentrated in a handful of categories; report the share of the total when helpful."),
    "customer_order_count": dict(
        domain="customers", title="Orders per customer", src=["customer_order_count"],
        description="Number of orders per customer, for repeat-purchase and segmentation questions.",
        triggers=["How many orders does each customer have?", "Which customers order most often?", "How many customers placed more than one order?"],
        disambiguation="customer_id is per order in Olist. For a real person use customer_unique_id.",
        tables=["orders", "customers"], grain="customer", dimensions=["customer_unique_id"],
        eval=["How many distinct customers placed more than one order?", "What is the maximum number of orders by one customer?"],
        pitfalls=["customers.customer_id changes with every order; group by customers.customer_unique_id to count repeat buyers, otherwise every customer has exactly one order.",
                  "Join orders to customers on customer_id, then group by customer_unique_id."],
        interp="Most Olist customers order once; repeat rates are low (a few percent)."),
    "orders_per_month": dict(
        domain="orders", title="Orders per month", src=["orders_per_month"],
        description="Number of orders placed per calendar month, by order purchase timestamp.",
        triggers=["How many orders per month?", "Monthly order volume", "Which month had the most orders in 2017?"],
        disambiguation="Orders over time, all statuses included. Not for how many orders have a given status (delivered, canceled, shipped...): use orders_by_status.",
        tables=["orders"], grain="month", dimensions=["month", "order_status"],
        eval=["Which month in 2017 had the highest order volume?", "How many orders were placed in January 2018?"],
        pitfalls=["Use half-open timestamp ranges (>= start AND < next period start); BETWEEN misses the last day's afternoon.",
                  "Cancelled and unavailable orders are included unless filtered."],
        interp="Look for the Black Friday spike (November 2017); the final months of the dataset are incomplete."),
    "sales_by_customer_state": dict(
        domain="sales", title="Sales by customer state", src=["sales_by_customer_state"],
        description="Total customer payment value by the customer's Brazilian state.",
        triggers=["Which states buy the most?", "Sales by state", "Total sales in SP versus RJ"],
        disambiguation="This is where customers live, not where sellers are. For seller geography use sellers.seller_state.",
        tables=["orders", "customers", "order_payments"], grain="state", dimensions=["customer_state"],
        eval=["Which customer state has the highest total sales?", "What is the total payment value from customers in RJ?"],
        pitfalls=["Join customers on customer_id, not customer_unique_id.",
                  "States are two-letter codes; SP is by far the largest."],
        interp="São Paulo dominates; consider each state's share of the total."),
    "payment_type_breakdown": dict(
        domain="payments", title="Payment type breakdown", src=["payment_type_breakdown"],
        description="Total amount and share of sales by payment type (credit card, boleto, voucher, debit card).",
        triggers=["What share of sales is paid by credit card?", "Payment method breakdown", "How popular is boleto?"],
        disambiguation="Share is by payment value, not by number of orders.",
        tables=["order_payments"], grain="payment_type", dimensions=["payment_type"],
        eval=["What percentage of total payment value comes from credit cards?", "Which payment type has the lowest total value?"],
        pitfalls=["An order can use several payment types, so counting orders per type double counts; this metric sums payment_value.",
                  "Valid types: credit_card, boleto, voucher, debit_card (a few not_defined rows exist)."],
        interp="Credit card is roughly three quarters of value; boleto is the main alternative."),
    "avg_installments_by_payment_type": dict(
        domain="payments", title="Average installments by payment type", src=["avg_installments_by_payment_type"],
        description="Average number of payment installments for each payment type.",
        triggers=["How many installments do customers choose on average?", "Average installments for credit cards", "Do boleto payments use installments?"],
        disambiguation="Per payment row, not per order.",
        tables=["order_payments"], grain="payment_type", dimensions=["payment_type"],
        eval=["What is the average number of installments for credit card payments?", "How many payments are there for each payment type?"],
        pitfalls=["Only credit cards use installments meaningfully; other types are almost always 1.",
                  "Averages are over payment rows, so orders with several payments weigh more."],
        interp="Compare credit-card installments to 1 to gauge how much customers rely on credit."),
    "average_review_score_per_order": dict(
        domain="reviews", title="Review score per order", src=["average_review_score_per_order"],
        description="Review score for each order (1 to 5), which can be aggregated by time, category or state.",
        triggers=["What is the average review score?", "Review scores by order", "How satisfied are customers on average?"],
        disambiguation="For a monthly trend use monthly_average_review_score. This skill returns order-level rows to aggregate yourself.",
        tables=["orders", "order_reviews"], grain="order", dimensions=["order_purchase_timestamp"],
        eval=["What is the average review score across all orders?", "How many orders have a review score of 1?"],
        pitfalls=["A few orders have several reviews; use AVG over reviews, not a join that repeats rows.",
                  "LEFT JOIN keeps orders without a review (score NULL); filter them out for averages."],
        interp="Scores are bimodal (many 5s and 1s); the mean hides this, so mention the distribution."),
    "monthly_average_review_score": dict(
        domain="reviews", title="Monthly average review score", src=["monthly_average_review_score"],
        description="Average review score and review count per calendar month, by review creation date.",
        triggers=["How did the average review score change by month?", "Monthly review score trend", "Which month had the lowest ratings?"],
        disambiguation="Grouped by review creation month, not purchase month.",
        tables=["order_reviews"], grain="month", dimensions=["month"],
        eval=["Which month had the lowest average review score?", "How many reviews were created in March 2018?"],
        pitfalls=["Group by review_creation_date, not order_purchase_timestamp.", "Exclude NULL review dates."],
        interp="A dip often follows delivery problems; check delivery_delay_vs_estimate for the same months."),
    "reviews_with_comments": dict(
        domain="reviews", title="Reviews with comments", src=["reviews_with_comments"],
        description="Count of reviews that include a non-empty comment title or message, a measure of review engagement.",
        triggers=["How many reviews have comments?", "What share of customers leave written feedback?", "Count of reviews with a message"],
        disambiguation="For the share, divide by the total review count (COUNT(*) over order_reviews).",
        tables=["order_reviews"], grain="all", dimensions=[],
        eval=["How many reviews have a written comment?", "What percentage of reviews include a comment?"],
        pitfalls=["Empty strings count as no comment: trim before testing.", "Check both title and message columns."],
        interp="Roughly half of reviews have text; low scores are more likely to carry a comment."),
    "delivery_delay_vs_estimate": dict(
        domain="fulfillment", title="Delivery delay versus estimate", src=["delivery_delay_days", "delivery_delay_vs_estimate"],
        description="Days between the estimated and the actual delivery date per order (positive means late, negative means early).",
        triggers=["How late are deliveries versus the estimate?", "Average delivery delay", "How many orders were delivered late?"],
        disambiguation="This is NOT total delivery time. Total time from purchase to delivery is delivered_customer_date minus order_purchase_timestamp.",
        tables=["orders"], grain="order", dimensions=["month", "customer_state"],
        eval=["What is the average delivery delay versus the estimate, in days?", "How many delivered orders arrived after the estimated date?"],
        pitfalls=["Compute with EXTRACT(EPOCH FROM (actual - estimated)) / 86400.0 on timestamps; never subtract strings.",
                  "Filter to rows where both dates are NOT NULL (undelivered orders have no delivery date).",
                  "Average delay is negative because most orders arrive early; report late share too."],
        interp="Negative averages mean early on average; the late tail drives review scores."),
    "freight_to_price_ratio_by_category": dict(
        domain="products", title="Freight-to-price ratio by category", src=["freight_to_price_ratio_by_category"],
        description="Average ratio of freight value to item price by product category (English names), for categories with enough items.",
        triggers=["Which categories have the highest shipping cost relative to price?", "Freight to price ratio by category", "Where is freight most expensive compared to the product?"],
        disambiguation="Ratio of freight to price per item, averaged; not total freight spend.",
        tables=["order_items", "products", "product_category_name_translation"], grain="category", dimensions=["product_category_name_english"],
        eval=["Which product category has the highest average freight-to-price ratio?", "What is the freight-to-price ratio for the computers category?"],
        pitfalls=["Postgres does not allow a SELECT alias in HAVING: use HAVING COUNT(*) >= 10.",
                  "Guard against price = 0 to avoid division by zero.",
                  "Translate category names with product_category_name_translation."],
        interp="Cheap, heavy items have the highest ratios."),
    "lead_conversion_count": dict(
        domain="leads", title="Converted leads", src=["lead_conversion_count"],
        description="Qualified leads (MQLs) that became closed deals, one row per converted lead with origin and business segment.",
        triggers=["How many qualified leads converted to closed deals?", "List converted leads", "Which leads were won?"],
        disambiguation="Rate by origin: conversion_rate_by_origin. Overall rate: closed count divided by qualified count.",
        tables=["leads_qualified", "leads_closed"], grain="lead", dimensions=["origin", "business_segment"],
        eval=["How many qualified leads became closed deals?", "How many leads were closed in 2018?"],
        pitfalls=["Join leads_qualified to leads_closed on mql_id only.",
                  "Filter by first_contact_date for when leads started, by won_date for when deals closed.",
                  "There is no direct join between leads and orders; the bridge is seller_id."],
        interp="Only a small share of qualified leads close (about 10 percent)."),
    "conversion_rate_by_origin": dict(
        domain="leads", title="Lead conversion rate by origin", src=["conversion_rate_by_origin"],
        description="Conversion rate from qualified lead to closed deal by lead origin channel.",
        triggers=["Which lead origin converts best?", "Conversion rate by channel", "Compare organic search and paid search conversion"],
        disambiguation="By channel (origin). For rates by customer state use lead_conversion_rate_by_customer_state.",
        tables=["leads_qualified", "leads_closed"], grain="origin", dimensions=["origin"],
        eval=["Which lead origin has the highest conversion rate?", "What is the conversion rate for organic_search leads?"],
        pitfalls=["Use COUNT(DISTINCT mql_id) on both sides after a LEFT JOIN so unconverted leads stay in the denominator.",
                  "Some origins have very few leads; show the qualified count next to the rate."],
        interp="Small origins give noisy percentages; weight by qualified_count."),
    "top_10_sellers_by_revenue": dict(
        domain="sellers", title="Top 10 sellers by revenue", src=["top_10_sellers_by_revenue"],
        description="The ten sellers with the highest total sales value (item price plus freight) across all orders.",
        triggers=["Who are our top 10 sellers?", "Top sellers by revenue", "Which seller sells the most?"],
        disambiguation="Seller revenue uses order_items price + freight_value. Per-month breakdown: seller_tpv_per_month.",
        tables=["order_items"], grain="seller", dimensions=["seller_id"],
        eval=["Which seller has the highest total revenue?", "What share of total revenue do the top 10 sellers account for?"],
        pitfalls=["Do NOT join order_payments to order_items: payment_value repeats for every item and inflates seller totals.",
                  "The original definition used payment_value and double counted multi-item orders."],
        interp="Revenue is concentrated: the top sellers account for a large share."),
    "leads_generated_from_orders_by_period_and_state": dict(
        domain="leads", title="Leads generated from orders by period and state", src=["leads_generated_from_orders_by_period_and_state", "leads_generated_and_contacted_combined"],
        description="Distinct leads whose sellers had orders from given customer states in a time window, plus the all-time total of leads contacted.",
        triggers=["How many leads came from orders in July-August 2017 from SP?", "Leads generated from orders by state and period", "How many leads were contacted overall versus generated from those orders?"],
        disambiguation="Cross-domain: orders and leads connect only through seller_id (orders -> order_items -> leads_closed -> leads_qualified). Use the 'combined' block for both numbers in one row.",
        tables=["orders", "customers", "order_items", "leads_closed", "leads_qualified"], grain="period", dimensions=["customer_state", "period"],
        eval=["How many leads were generated from orders placed between July and August 2017 by customers in SP, BA and MA?", "How many leads were contacted in total?"],
        pitfalls=["There is NO direct join between orders/customers and leads_qualified/leads_closed.",
                  "Join path: orders -> order_items (order_id) -> leads_closed (seller_id) -> leads_qualified (mql_id).",
                  "Use COUNT(DISTINCT mql_id); joins multiply rows.",
                  "Use half-open timestamp ranges on order_purchase_timestamp; the all-time total ignores filters."],
        interp="Lead counts here mean sellers who started as MQLs and later sold; they are not marketing-attributed leads."),
    "seller_order_volume_with_lead_status": dict(
        domain="leads", title="Seller order volume with lead status", src=["seller_order_volume_with_lead_status"],
        description="For each seller with orders in a period: order count, revenue and whether the seller appears in the closed-leads pipeline.",
        triggers=["Which active sellers came from the leads pipeline?", "Seller orders and lead status in 2017", "Overlap between active sellers and closed leads"],
        disambiguation="Sellers are the bridge between orders and leads.",
        tables=["orders", "order_items", "leads_closed"], grain="seller", dimensions=["seller_id", "lead_status"],
        eval=["How many sellers with orders in 2017 are in the closed leads pipeline?", "What is the total revenue of sellers that came from leads in 2017?"],
        pitfalls=["Use COUNT(DISTINCT order_id): items repeat orders.",
                  "Revenue uses order_items price + freight; joining order_payments here double counts.",
                  "Group by the CASE alias is fine in Postgres, but HAVING cannot use aliases."],
        interp="Compare average revenue of in-leads sellers to the rest."),
    "lead_conversion_rate_by_customer_state": dict(
        domain="leads", title="Lead conversion rate by customer state", src=["lead_conversion_rate_by_customer_state"],
        description="Conversion rate from qualified to closed leads by the customer state of the orders those sellers fulfilled.",
        triggers=["Which states have the highest seller conversion rates?", "Lead conversion by customer state", "Where do converted sellers sell most?"],
        disambiguation="Geographic view through orders; for channel view use conversion_rate_by_origin.",
        tables=["orders", "customers", "order_items", "leads_closed", "leads_qualified"], grain="state", dimensions=["customer_state"],
        eval=["Which customer state has the highest lead conversion rate?", "How many qualified leads are linked to orders in SP?"],
        pitfalls=["Joining leads_closed on seller_id only reaches closed leads, so the denominator is limited to leads that already converted; read the percentage with care.",
                  "Never join customers directly to leads_qualified.",
                  "Use NULLIF in the denominator."],
        interp="Treat as a relative ranking, not a true conversion rate."),
}

DASH = """\

"""


def parse_definitions(md: str) -> dict[str, tuple[str, list[str]]]:
    """name -> (description, [sql blocks]) from the legacy markdown."""
    out: dict[str, tuple[str, list[str]]] = {}
    parts = re.split(r"^### definition: ", md, flags=re.M)[1:]
    for part in parts:
        name, _, rest = part.partition("\n")
        name = name.strip()
        desc = rest.split("### SQL query")[0].strip() if "### SQL query" in rest else rest.strip()
        blocks = [b.strip().rstrip(";") for b in re.findall(r"```sql\s*\n(.*?)```", rest, re.S)]
        out[name] = (desc, blocks)
    return out


def render(name: str, m: dict, sql: dict[str, str], definition: str) -> str:
    fm = [
        "---",
        f"name: {name}",
        f"domain: {m['domain']}",
        f"title: {m['title']}",
        "route: analyze",
        f"description: {_q(m['description'])}",
        "triggers:",
        *[f"  - {_q(t)}" for t in m["triggers"]],
        f"disambiguation: {_q(m['disambiguation'])}",
        f"tables: [{', '.join(m['tables'])}]",
        f"grain: {m['grain']}",
        f"dimensions: [{', '.join(m['dimensions'])}]",
        "eval_questions:",
        *[f"  - {_q(e)}" for e in m["eval"]],
        "---",
        "",
        "## Definition",
        definition,
        "",
        "## SQL",
    ]
    for label, q in sql.items():
        fm += [f"### {label}", "```sql", q.strip().rstrip(";") + ";", "```", ""]
    fm += ["## Pitfalls", *[f"- {p}" for p in m["pitfalls"]], "", "## Interpretation", m["interp"], ""]
    return "\n".join(fm)


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> None:
    defs = parse_definitions(Path(SCHEMA_DOC_PATH).read_text(encoding="utf-8"))
    # the trailing 'delivery_delay_vs_estimate' block sits outside the '### SQL query' layout
    for name, m in SKILLS.items():
        src = m["src"]
        definition = defs[src[-1]][0]
        legacy = next(defs[n][1][0] for n in src if defs[n][1])
        sql = SQL_OVERRIDES.get(name) or {"main": legacy}
        d = OUT / m["domain"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.md").write_text(render(name, m, sql, definition), encoding="utf-8", newline="\n")
        print("wrote", d.name + "/" + name + ".md")
    print(f"{len(SKILLS)} skills written to {OUT}")


if __name__ == "__main__":
    main()
