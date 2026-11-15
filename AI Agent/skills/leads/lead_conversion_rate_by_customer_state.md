---
name: lead_conversion_rate_by_customer_state
domain: leads
title: Lead conversion rate by customer state
route: analyze
description: "Conversion rate from qualified to closed leads by the customer state of the orders those sellers fulfilled."
triggers:
  - "Which states have the highest seller conversion rates?"
  - "Lead conversion by customer state"
  - "Where do converted sellers sell most?"
disambiguation: "Geographic view through orders; for channel view use conversion_rate_by_origin."
tables: [orders, customers, order_items, leads_closed, leads_qualified]
grain: state
dimensions: [customer_state]
eval_questions:
  - "Which customer state has the highest lead conversion rate?"
  - "How many qualified leads are linked to orders in SP?"
---

## Definition
Conversion rate (leads_qualified → leads_closed) broken down by the customer state of the orders those sellers fulfilled. Shows which geographic markets have the highest seller conversion rates.

## SQL
### main
```sql
SELECT
  c.customer_state,
  COUNT(DISTINCT lq.mql_id)  AS total_leads_qualified,
  COUNT(DISTINCT lc.mql_id)  AS leads_closed_count,
  ROUND(
    100.0 * COUNT(DISTINCT lc.mql_id) / NULLIF(COUNT(DISTINCT lq.mql_id), 0),
    2
  ) AS conversion_pct
FROM orders o
JOIN customers c     ON c.customer_id  = o.customer_id
JOIN order_items oi  ON oi.order_id    = o.order_id
JOIN leads_closed lc ON lc.seller_id   = oi.seller_id
JOIN leads_qualified lq ON lq.mql_id   = lc.mql_id
GROUP BY c.customer_state
ORDER BY conversion_pct DESC;
```

## Pitfalls
- Joining leads_closed on seller_id only reaches closed leads, so the denominator is limited to leads that already converted; read the percentage with care.
- Never join customers directly to leads_qualified.
- Use NULLIF in the denominator.

## Interpretation
Treat as a relative ranking, not a true conversion rate.
