---
name: items_per_order
domain: sales
title: Items per order
route: analyze
description: "Average number of items per order, overall and by month."
triggers:
  - "How many items are in an average order?"
  - "Items per order"
  - "Do customers buy multiple items per order?"
  - "Average basket size in items"
disambiguation: "Counts items, not distinct products and not money. For value per order use aov."
tables: [order_items, orders]
grain: order
dimensions: [month]
eval_questions:
  - "What is the average number of items per order?"
  - "What share of orders contain more than one item?"
---

## Definition
Average number of items (order_items rows) per order, overall and by purchase month.

## SQL
### main
```sql
SELECT ROUND(AVG(n)::numeric, 3) AS avg_items_per_order,
  COUNT(*) AS orders,
  ROUND((100.0 * COUNT(*) FILTER (WHERE n > 1) / COUNT(*))::numeric, 2) AS multi_item_pct
FROM (SELECT order_id, COUNT(*) AS n FROM order_items GROUP BY order_id) t;
```

### by_month
```sql
SELECT TO_CHAR(o.order_purchase_timestamp, 'YYYY-MM') AS order_month, ROUND(AVG(t.n)::numeric, 3) AS avg_items_per_order
FROM (SELECT order_id, COUNT(*) AS n FROM order_items GROUP BY order_id) t
JOIN orders o ON o.order_id = t.order_id
GROUP BY order_month
ORDER BY order_month;
```

## Pitfalls
- Count rows of order_items per order, not DISTINCT product_id: the same product can appear in several rows.
- Orders without items (e.g. unavailable, canceled before items were recorded) are excluded.

## Interpretation
Most orders have a single item (about 90 percent).
