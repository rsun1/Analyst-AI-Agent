---
name: orders_by_weekday_hour
domain: orders
title: Orders by weekday and hour
route: analyze
description: "Order volume by day of the week and by hour of the day."
triggers:
  - "Which day of the week has the most orders?"
  - "What time of day do customers order?"
  - "Orders by hour"
  - "Busiest weekday for sales"
disambiguation: "Purchase timestamp as stored (no timezone conversion)."
tables: [orders]
grain: weekday/hour
dimensions: [weekday, hour]
eval_questions:
  - "Which weekday has the most orders?"
  - "At which hour of the day are the most orders placed?"
---

## Definition
Number of orders by day of week and by hour of day of the purchase timestamp (times as stored; no time zone conversion).

## SQL
### by_weekday
```sql
SELECT EXTRACT(DOW FROM order_purchase_timestamp)::int AS weekday_num,
  TO_CHAR(order_purchase_timestamp, 'Dy') AS weekday,
  COUNT(*) AS order_count
FROM orders
GROUP BY weekday_num, weekday
ORDER BY weekday_num;
```

### by_hour
```sql
SELECT EXTRACT(HOUR FROM order_purchase_timestamp)::int AS hour_of_day,
  COUNT(*) AS order_count
FROM orders
GROUP BY hour_of_day
ORDER BY hour_of_day;
```

## Pitfalls
- EXTRACT(DOW ...) returns 0 for Sunday through 6 for Saturday.
- Timestamps have no time zone; the hours are as recorded, not necessarily local Brazil time.
- Use TO_CHAR(..., 'Dy') only for labels; sort by the numeric weekday.

## Interpretation
Orders peak on weekdays around late morning and the afternoon, with fewer on Saturdays.
