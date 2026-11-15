---
name: customer_order_count
domain: customers
title: Orders per customer
route: analyze
description: "Number of orders per customer, for repeat-purchase and segmentation questions."
triggers:
  - "How many orders does each customer have?"
  - "Which customers order most often?"
  - "How many customers placed more than one order?"
disambiguation: "customer_id is per order in Olist. For a real person use customer_unique_id."
tables: [orders, customers]
grain: customer
dimensions: [customer_unique_id]
eval_questions:
  - "How many distinct customers placed more than one order?"
  - "What is the maximum number of orders by one customer?"
---

## Definition
Number of orders per customer. Useful for repeat purchase and segmentation.

## SQL
### main
```sql
SELECT
  o.customer_id,
  COUNT(DISTINCT o.order_id) AS order_count
FROM orders o
GROUP BY o.customer_id
ORDER BY order_count DESC;
```

## Pitfalls
- customers.customer_id changes with every order; group by customers.customer_unique_id to count repeat buyers, otherwise every customer has exactly one order.
- Join orders to customers on customer_id, then group by customer_unique_id.

## Interpretation
Most Olist customers order once; repeat rates are low (a few percent).
