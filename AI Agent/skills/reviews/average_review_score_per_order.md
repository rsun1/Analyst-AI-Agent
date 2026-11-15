---
name: average_review_score_per_order
domain: reviews
title: Review score per order
route: analyze
description: "Review score for each order (1 to 5), which can be aggregated by time, category or state."
triggers:
  - "What is the average review score?"
  - "Review scores by order"
  - "How satisfied are customers on average?"
disambiguation: "For a monthly trend use monthly_average_review_score. This skill returns order-level rows to aggregate yourself."
tables: [orders, order_reviews]
grain: order
dimensions: [order_purchase_timestamp]
eval_questions:
  - "What is the average review score across all orders?"
  - "How many orders have a review score of 1?"
---

## Definition
Average review score per order (one review per order in this dataset). Can be aggregated further by time or category.

## SQL
### main
```sql
SELECT
  o.order_id,
  o.order_purchase_timestamp,
  r.review_score
FROM orders o
LEFT JOIN order_reviews r ON r.order_id = o.order_id
ORDER BY o.order_purchase_timestamp DESC;
```

## Pitfalls
- A few orders have several reviews; use AVG over reviews, not a join that repeats rows.
- LEFT JOIN keeps orders without a review (score NULL); filter them out for averages.

## Interpretation
Scores are bimodal (many 5s and 1s); the mean hides this, so mention the distribution.
