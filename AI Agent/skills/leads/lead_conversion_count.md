---
name: lead_conversion_count
domain: leads
title: Converted leads
route: analyze
description: "Qualified leads (MQLs) that became closed deals, one row per converted lead with origin and business segment."
triggers:
  - "How many qualified leads converted to closed deals?"
  - "List converted leads"
  - "Which leads were won?"
disambiguation: "Rate by origin: conversion_rate_by_origin. Overall rate: closed count divided by qualified count."
tables: [leads_qualified, leads_closed]
grain: lead
dimensions: [origin, business_segment]
eval_questions:
  - "How many qualified leads became closed deals?"
  - "How many leads were closed in 2018?"
---

## Definition
Number of qualified leads (leads_qualified) that became closed/won (leads_closed). One row per converted lead.

## SQL
### main
```sql
SELECT
  lq.mql_id,
  lq.first_contact_date,
  lq.origin,
  lc.won_date,
  lc.business_segment
FROM leads_qualified lq
INNER JOIN leads_closed lc ON lc.mql_id = lq.mql_id
ORDER BY lc.won_date DESC;
```

## Pitfalls
- Join leads_qualified to leads_closed on mql_id only.
- Filter by first_contact_date for when leads started, by won_date for when deals closed.
- There is no direct join between leads and orders; the bridge is seller_id.

## Interpretation
Only a small share of qualified leads close (about 10 percent).
