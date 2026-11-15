---
name: revenue_yoy
domain: sales
title: Year-over-year GMV growth
route: analyze
description: "Year-over-year GMV growth: each 2018 month versus the same month in 2017 (January to August)."
triggers:
  - "How does 2018 compare with 2017?"
  - "Year over year revenue growth"
  - "GMV growth versus last year"
  - "Is 2018 sales higher than 2017?"
disambiguation: "Same month a year earlier. Sequential change is revenue_growth_mom."
tables: [orders, order_payments]
grain: month
dimensions: [month]
eval_questions:
  - "What was the year-over-year GMV growth for March 2018?"
  - "Was GMV higher in every month of 2018 than in the same month of 2017?"
---

## Definition
GMV of each month of 2018 compared with the same month of 2017, as a percentage change. Only January to August are compared because later 2018 months are incomplete in the dataset.

## SQL
### main
```sql
WITH m AS (
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
ORDER BY month_number;
```

## Pitfalls
- The dataset covers roughly September 2016 to October 2018: only 2017 vs 2018 and only months 1-8 are comparable.
- 2016 vs 2017 is not a fair comparison (2016 has a few months of sparse data).
- Do not extrapolate 2018 beyond August.

## Interpretation
Growth decelerates through 2018 from very high early-2018 rates.
