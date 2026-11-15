---
name: conversion_rate_by_origin
domain: leads
title: Lead conversion rate by origin
route: analyze
description: "Conversion rate from qualified lead to closed deal by lead origin channel."
triggers:
  - "Which lead origin converts best?"
  - "Conversion rate by channel"
  - "Compare organic search and paid search conversion"
disambiguation: "By channel (origin). For rates by customer state use lead_conversion_rate_by_customer_state."
tables: [leads_qualified, leads_closed]
grain: origin
dimensions: [origin]
eval_questions:
  - "Which lead origin has the highest conversion rate?"
  - "What is the conversion rate for organic_search leads?"
---

## Definition
Conversion rate (qualified → closed) by lead origin.

## SQL
### main
```sql
SELECT
  lq.origin,
  COUNT(DISTINCT lq.mql_id) AS qualified_count,
  COUNT(DISTINCT lc.mql_id) AS closed_count,
  ROUND(100.0 * COUNT(DISTINCT lc.mql_id) / COUNT(DISTINCT lq.mql_id), 2) AS conversion_pct
FROM leads_qualified lq
LEFT JOIN leads_closed lc ON lc.mql_id = lq.mql_id
GROUP BY lq.origin
ORDER BY conversion_pct DESC;
```

## Pitfalls
- Use COUNT(DISTINCT mql_id) on both sides after a LEFT JOIN so unconverted leads stay in the denominator.
- Some origins have very few leads; show the qualified count next to the rate.

## Interpretation
Small origins give noisy percentages; weight by qualified_count.
