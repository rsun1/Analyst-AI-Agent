---
name: leads_generated_from_orders_by_period_and_state
domain: leads
title: Leads generated from orders by period and state
route: analyze
description: "Distinct leads whose sellers had orders from given customer states in a time window, plus the all-time total of leads contacted."
triggers:
  - "How many leads came from orders in July-August 2017 from SP?"
  - "Leads generated from orders by state and period"
  - "How many leads were contacted overall versus generated from those orders?"
disambiguation: "Cross-domain: orders and leads connect only through seller_id (orders -> order_items -> leads_closed -> leads_qualified). Use the 'combined' block for both numbers in one row."
tables: [orders, customers, order_items, leads_closed, leads_qualified]
grain: period
dimensions: [customer_state, period]
eval_questions:
  - "How many leads were generated from orders placed between July and August 2017 by customers in SP, BA and MA?"
  - "How many leads were contacted in total?"
---

## Definition
Single query that returns both the filtered lead count (orders in a period, customers from given states) and the all-time total leads contacted side by side. Uses a subquery for the all-time total so both numbers appear in one result row.

## SQL
### leads_in_period
```sql
-- parameters: period 2017-07-01..2017-09-01 (half-open), states BA, MA, SP
SELECT COUNT(DISTINCT lq.mql_id) AS leads_generated
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN order_items oi ON oi.order_id = o.order_id
JOIN leads_closed lc ON lc.seller_id = oi.seller_id
JOIN leads_qualified lq ON lq.mql_id = lc.mql_id
WHERE o.order_purchase_timestamp >= '2017-07-01' AND o.order_purchase_timestamp < '2017-09-01'
  AND c.customer_state IN ('BA', 'MA', 'SP');
```

### total_leads_contacted
```sql
SELECT COUNT(DISTINCT mql_id) AS total_leads_contacted
FROM leads_qualified;
```

### combined
```sql
SELECT
  COUNT(DISTINCT lq.mql_id) AS leads_generated,
  (SELECT COUNT(DISTINCT mql_id) FROM leads_qualified) AS total_leads_contacted_ever
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN order_items oi ON oi.order_id = o.order_id
JOIN leads_closed lc ON lc.seller_id = oi.seller_id
JOIN leads_qualified lq ON lq.mql_id = lc.mql_id
WHERE o.order_purchase_timestamp >= '2017-07-01' AND o.order_purchase_timestamp < '2017-09-01'
  AND c.customer_state IN ('BA', 'MA', 'SP');
```

## Pitfalls
- There is NO direct join between orders/customers and leads_qualified/leads_closed.
- Join path: orders -> order_items (order_id) -> leads_closed (seller_id) -> leads_qualified (mql_id).
- Use COUNT(DISTINCT mql_id); joins multiply rows.
- Use half-open timestamp ranges on order_purchase_timestamp; the all-time total ignores filters.

## Interpretation
Lead counts here mean sellers who started as MQLs and later sold; they are not marketing-attributed leads.
