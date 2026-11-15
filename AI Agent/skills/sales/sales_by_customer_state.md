---
name: sales_by_customer_state
domain: sales
title: Sales by customer state
route: analyze
description: "Total customer payment value by the customer's Brazilian state."
triggers:
  - "Which states buy the most?"
  - "Sales by state"
  - "Total sales in SP versus RJ"
disambiguation: "This is where customers live, not where sellers are. For seller geography use sellers.seller_state."
tables: [orders, customers, order_payments]
grain: state
dimensions: [customer_state]
eval_questions:
  - "Which customer state has the highest total sales?"
  - "What is the total payment value from customers in RJ?"
---

## Definition
Total payment value by customer state, using `customers.customer_state`.

## SQL
### main
```sql
SELECT
  c.customer_state,
  SUM(op.payment_value) AS total_sales
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN order_payments op ON op.order_id = o.order_id
GROUP BY c.customer_state
ORDER BY total_sales DESC;
```

## Pitfalls
- Join customers on customer_id, not customer_unique_id.
- States are two-letter codes; SP is by far the largest.

## Interpretation
São Paulo dominates; consider each state's share of the total.
