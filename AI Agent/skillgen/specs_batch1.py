"""Batch 1: sales and orders skills (new, beyond the 19 migrated ones). Rendered by skillgen/build.py."""

MONTH = "TO_CHAR(o.order_purchase_timestamp, 'YYYY-MM')"

SPECS = [
    dict(
        name="aov", domain="sales", title="Average order value",
        definition="Average order value (AOV): total customer payment value divided by the number of orders that have a payment, by purchase month and overall. Includes freight and vouchers, like GMV.",
        description="Average order value: GMV divided by the number of paid orders, per month and overall.",
        triggers=["What is the average order value?", "How much does a typical order cost?", "AOV by month", "Average basket size"],
        disambiguation="AOV here is payment_value per order (includes freight). Items per order is items_per_order; average item price is not payment based.",
        tables=["orders", "order_payments"], grain="month", dimensions=["month"],
        eval=["What is the overall average order value?", "Which month had the highest average order value?"],
        sql={
            "by_month": f"""SELECT {MONTH} AS order_month,
  ROUND((SUM(op.payment_value) / COUNT(DISTINCT o.order_id))::numeric, 2) AS aov,
  COUNT(DISTINCT o.order_id) AS orders
FROM orders o
JOIN order_payments op ON op.order_id = o.order_id
GROUP BY order_month
ORDER BY order_month""",
            "overall": """SELECT ROUND((SUM(op.payment_value) / COUNT(DISTINCT op.order_id))::numeric, 2) AS aov,
  COUNT(DISTINCT op.order_id) AS orders
FROM order_payments op""",
        },
        pitfalls=["Divide by COUNT(DISTINCT order_id): order_payments has several rows per order, so COUNT(*) would understate AOV.",
                  "Orders without any payment row are excluded (a handful of orders).",
                  "Months at the edges of the dataset (2016, late 2018) have very few orders; AOV is noisy there."],
        interp="AOV near 160 BRL with long right tail; compare months, not single orders."),
    dict(
        name="revenue_growth_mom", domain="sales", title="Month-over-month GMV growth",
        definition="GMV for each month and its percentage change versus the previous month (by order purchase month).",
        description="Month-over-month growth of GMV: monthly GMV and the percentage change from the previous month.",
        triggers=["How fast is revenue growing month over month?", "MoM growth of GMV", "Which month had the biggest sales jump?", "Monthly revenue growth rate"],
        disambiguation="Month over month within the data; for the same month a year earlier use revenue_yoy.",
        tables=["orders", "order_payments"], grain="month", dimensions=["month"],
        eval=["Which month had the highest month-over-month GMV growth?", "What was the GMV growth from October to November 2017?"],
        sql={"main": f"""WITH m AS (
  SELECT {MONTH} AS order_month, SUM(op.payment_value) AS gmv
  FROM orders o
  JOIN order_payments op ON op.order_id = o.order_id
  GROUP BY order_month
)
SELECT order_month, gmv,
  ROUND((100.0 * (gmv - LAG(gmv) OVER (ORDER BY order_month))
         / NULLIF(LAG(gmv) OVER (ORDER BY order_month), 0))::numeric, 2) AS mom_growth_pct
FROM m
ORDER BY order_month"""},
        pitfalls=["The first month has no previous month, so its growth is NULL.",
                  "Early 2017 growth rates are huge because the 2016-2017 base is tiny; say so.",
                  "Use LAG over months ordered by the YYYY-MM text, which sorts correctly."],
        interp="November 2017 (Black Friday) is the peak jump; read growth next to the absolute GMV."),
    dict(
        name="revenue_yoy", domain="sales", title="Year-over-year GMV growth",
        definition="GMV of each month of 2018 compared with the same month of 2017, as a percentage change. Only January to August are compared because later 2018 months are incomplete in the dataset.",
        description="Year-over-year GMV growth: each 2018 month versus the same month in 2017 (January to August).",
        triggers=["How does 2018 compare with 2017?", "Year over year revenue growth", "GMV growth versus last year", "Is 2018 sales higher than 2017?"],
        disambiguation="Same month a year earlier. Sequential change is revenue_growth_mom.",
        tables=["orders", "order_payments"], grain="month", dimensions=["month"],
        eval=["What was the year-over-year GMV growth for March 2018?", "Was GMV higher in every month of 2018 than in the same month of 2017?"],
        sql={"main": """WITH m AS (
  SELECT EXTRACT(YEAR FROM o.order_purchase_timestamp)::int AS yr,
         EXTRACT(MONTH FROM o.order_purchase_timestamp)::int AS mon,
         SUM(op.payment_value) AS gmv
  FROM orders o
  JOIN order_payments op ON op.order_id = o.order_id
  GROUP BY yr, mon
)
SELECT cur.mon AS month_number, prev.gmv AS gmv_2017, cur.gmv AS gmv_2018,
  ROUND((100.0 * (cur.gmv - prev.gmv) / NULLIF(prev.gmv, 0))::numeric, 2) AS yoy_growth_pct
FROM m cur
JOIN m prev ON prev.mon = cur.mon AND prev.yr = cur.yr - 1
WHERE cur.yr = 2018 AND cur.mon <= 8
ORDER BY month_number"""},
        pitfalls=["The dataset covers roughly September 2016 to October 2018: only 2017 vs 2018 and only months 1-8 are comparable.",
                  "2016 vs 2017 is not a fair comparison (2016 has a few months of sparse data).",
                  "Do not extrapolate 2018 beyond August."],
        interp="Growth decelerates through 2018 from very high early-2018 rates."),
    dict(
        name="items_per_order", domain="sales", title="Items per order",
        definition="Average number of items (order_items rows) per order, overall and by purchase month.",
        description="Average number of items per order, overall and by month.",
        triggers=["How many items are in an average order?", "Items per order", "Do customers buy multiple items per order?", "Average basket size in items"],
        disambiguation="Counts items, not distinct products and not money. For value per order use aov.",
        tables=["order_items", "orders"], grain="order", dimensions=["month"],
        eval=["What is the average number of items per order?", "What share of orders contain more than one item?"],
        sql={
            "main": """SELECT ROUND(AVG(n)::numeric, 3) AS avg_items_per_order,
  COUNT(*) AS orders,
  ROUND((100.0 * COUNT(*) FILTER (WHERE n > 1) / COUNT(*))::numeric, 2) AS multi_item_pct
FROM (SELECT order_id, COUNT(*) AS n FROM order_items GROUP BY order_id) t""",
            "by_month": f"""SELECT {MONTH} AS order_month, ROUND(AVG(t.n)::numeric, 3) AS avg_items_per_order
FROM (SELECT order_id, COUNT(*) AS n FROM order_items GROUP BY order_id) t
JOIN orders o ON o.order_id = t.order_id
GROUP BY order_month
ORDER BY order_month""",
        },
        pitfalls=["Count rows of order_items per order, not DISTINCT product_id: the same product can appear in several rows.",
                  "Orders without items (e.g. unavailable, canceled before items were recorded) are excluded."],
        interp="Most orders have a single item (about 90 percent)."),
    dict(
        name="orders_by_status", domain="orders", title="Orders by status",
        definition="Number of orders and share of all orders in each order status (delivered, shipped, canceled, unavailable, invoiced, processing, created, approved).",
        description="Count and percentage of orders in each order status.",
        triggers=["How many orders are in each status?", "Order status breakdown", "How many orders are still processing?", "What share of orders were delivered?"],
        disambiguation="All statuses over all time. Cancellation share by month is cancellation_rate.",
        tables=["orders"], grain="order_status", dimensions=["order_status"],
        eval=["How many orders have status delivered?", "What percentage of orders are canceled?"],
        sql={"main": """SELECT order_status, COUNT(*) AS order_count,
  ROUND((100.0 * COUNT(*) / SUM(COUNT(*)) OVER ())::numeric, 2) AS pct
FROM orders
GROUP BY order_status
ORDER BY order_count DESC"""},
        pitfalls=["Valid statuses: delivered, shipped, canceled, unavailable, invoiced, processing, created, approved.",
                  "To count successfully delivered orders also require order_delivered_customer_date IS NOT NULL."],
        interp="About 97 percent of orders are delivered; canceled plus unavailable are about 1 percent each."),
    dict(
        name="cancellation_rate", domain="orders", title="Cancellation rate",
        definition="Share of orders with status canceled, by purchase month and overall. Orders with status unavailable are reported separately and not counted as canceled.",
        description="Cancellation rate: share of orders that were canceled, by month and overall.",
        triggers=["What is the order cancellation rate?", "How many orders were canceled?", "Cancellation rate by month", "Are cancellations increasing?"],
        disambiguation="Only status = 'canceled'. 'unavailable' orders (item out of stock) are a different status.",
        tables=["orders"], grain="month", dimensions=["month"],
        eval=["What is the overall order cancellation rate?", "Which month had the highest cancellation rate?"],
        sql={
            "by_month": """SELECT TO_CHAR(order_purchase_timestamp, 'YYYY-MM') AS order_month,
  COUNT(*) AS orders,
  COUNT(*) FILTER (WHERE order_status = 'canceled') AS canceled,
  ROUND((100.0 * COUNT(*) FILTER (WHERE order_status = 'canceled') / COUNT(*))::numeric, 2) AS cancel_pct
FROM orders
GROUP BY order_month
ORDER BY order_month""",
            "overall": """SELECT COUNT(*) AS orders,
  COUNT(*) FILTER (WHERE order_status = 'canceled') AS canceled,
  ROUND((100.0 * COUNT(*) FILTER (WHERE order_status = 'canceled') / COUNT(*))::numeric, 2) AS cancel_pct
FROM orders""",
        },
        pitfalls=["Denominator is all orders placed in the period, not only delivered ones.",
                  "Cancellation time is not recorded: months are by purchase date, not cancel date.",
                  "Sparse months (2016, late 2018) give unstable rates."],
        interp="Cancellation is under 1 percent overall; individual months can spike."),
    dict(
        name="order_approval_lag", domain="orders", title="Order approval lag",
        definition="Hours between order purchase and payment approval (order_approved_at minus order_purchase_timestamp), average and median by purchase month.",
        description="Time from order purchase to approval in hours: average and median by month.",
        triggers=["How long does it take to approve an order?", "Average order approval time", "Order approval lag by month", "How fast are payments approved?"],
        disambiguation="Purchase to approval only. Purchase to delivery is delivery_time and approval to carrier handoff is carrier_handoff_time.",
        tables=["orders"], grain="month", dimensions=["month"],
        eval=["What is the average time in hours from purchase to approval?", "Which month had the longest median approval time?"],
        sql={"main": """SELECT TO_CHAR(order_purchase_timestamp, 'YYYY-MM') AS order_month,
  ROUND(AVG(EXTRACT(EPOCH FROM (order_approved_at - order_purchase_timestamp)) / 3600.0)::numeric, 2) AS avg_approval_hours,
  ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (order_approved_at - order_purchase_timestamp)) / 3600.0))::numeric, 2) AS median_approval_hours,
  COUNT(*) AS orders
FROM orders
WHERE order_approved_at IS NOT NULL
GROUP BY order_month
ORDER BY order_month"""},
        pitfalls=["Skip orders with NULL order_approved_at (never approved).",
                  "The average is pulled up by a few very slow approvals; show the median too.",
                  "Compute durations with EXTRACT(EPOCH FROM (a - b)) / 3600.0 on timestamps."],
        interp="Median is well under an hour (boleto payments take longer than cards)."),
    dict(
        name="orders_by_weekday_hour", domain="orders", title="Orders by weekday and hour",
        definition="Number of orders by day of week and by hour of day of the purchase timestamp (times as stored; no time zone conversion).",
        description="Order volume by day of the week and by hour of the day.",
        triggers=["Which day of the week has the most orders?", "What time of day do customers order?", "Orders by hour", "Busiest weekday for sales"],
        disambiguation="Purchase timestamp as stored (no timezone conversion).",
        tables=["orders"], grain="weekday/hour", dimensions=["weekday", "hour"],
        eval=["Which weekday has the most orders?", "At which hour of the day are the most orders placed?"],
        sql={
            "by_weekday": """SELECT EXTRACT(DOW FROM order_purchase_timestamp)::int AS weekday_num,
  TO_CHAR(order_purchase_timestamp, 'Dy') AS weekday,
  COUNT(*) AS order_count
FROM orders
GROUP BY weekday_num, weekday
ORDER BY weekday_num""",
            "by_hour": """SELECT EXTRACT(HOUR FROM order_purchase_timestamp)::int AS hour_of_day,
  COUNT(*) AS order_count
FROM orders
GROUP BY hour_of_day
ORDER BY hour_of_day""",
        },
        pitfalls=["EXTRACT(DOW ...) returns 0 for Sunday through 6 for Saturday.",
                  "Timestamps have no time zone; the hours are as recorded, not necessarily local Brazil time.",
                  "Use TO_CHAR(..., 'Dy') only for labels; sort by the numeric weekday."],
        interp="Orders peak on weekdays around late morning and the afternoon, with fewer on Saturdays."),
]
