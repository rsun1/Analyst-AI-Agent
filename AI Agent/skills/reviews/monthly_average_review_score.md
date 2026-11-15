---
name: monthly_average_review_score
domain: reviews
title: Monthly average review score
route: analyze
description: "Average review score and review count per calendar month, by review creation date."
triggers:
  - "How did the average review score change by month?"
  - "Monthly review score trend"
  - "Which month had the lowest ratings?"
disambiguation: "Grouped by review creation month, not purchase month."
tables: [order_reviews]
grain: month
dimensions: [month]
eval_questions:
  - "Which month had the lowest average review score?"
  - "How many reviews were created in March 2018?"
---

## Definition
Average review score per calendar month (by review creation date).

## SQL
### main
```sql
SELECT
  TO_CHAR(review_creation_date, 'YYYY-MM') AS review_month,
  AVG(review_score) AS avg_score,
  COUNT(*) AS review_count
FROM order_reviews
WHERE review_creation_date IS NOT NULL
GROUP BY review_month
ORDER BY review_month;
```

## Pitfalls
- Group by review_creation_date, not order_purchase_timestamp.
- Exclude NULL review dates.

## Interpretation
A dip often follows delivery problems; check delivery_delay_vs_estimate for the same months.
