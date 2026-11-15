---
name: revenue_growth_mom
domain: sales
title: Month-over-month GMV growth
route: analyze
description: "Month-over-month growth of GMV: monthly GMV and the percentage change from the previous month."
triggers:
  - "How fast is revenue growing month over month?"
  - "MoM growth of GMV"
  - "Which month had the biggest sales jump?"
  - "Monthly revenue growth rate"
disambiguation: "Month over month within the data; for the same month a year earlier use revenue_yoy."
tables: [orders, order_payments]
grain: month
dimensions: [month]
eval_questions:
  - "Which month had the highest month-over-month GMV growth?"
  - "What was the GMV growth from October to November 2017?"
---

## Definition
GMV for each month and its percentage change versus the previous month (by order purchase month).

## SQL
### main
```sql
WITH m AS (
  SELECT TO_CHAR(o.order_purchase_timestamp, 'YYYY-MM') AS order_month, SUM(op.payment_value) AS gmv
  FROM orders o
  JOIN order_payments op ON op.order_id = o.order_id
  GROUP BY order_month
)
SELECT order_month, gmv,
  ROUND((100.0 * (gmv - LAG(gmv) OVER (ORDER BY order_month))
         / NULLIF(LAG(gmv) OVER (ORDER BY order_month), 0))::numeric, 2) AS mom_growth_pct
FROM m
ORDER BY order_month;
```

## Pitfalls
- The first month has no previous month, so its growth is NULL.
- Early 2017 growth rates are huge because the 2016-2017 base is tiny; say so.
- Use LAG over months ordered by the YYYY-MM text, which sorts correctly.

## Interpretation
November 2017 (Black Friday) is the peak jump; read growth next to the absolute GMV.
