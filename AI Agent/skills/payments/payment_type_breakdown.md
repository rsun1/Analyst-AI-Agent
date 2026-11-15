---
name: payment_type_breakdown
domain: payments
title: Payment type breakdown
route: analyze
description: "Total amount and share of sales by payment type (credit card, boleto, voucher, debit card)."
triggers:
  - "What share of sales is paid by credit card?"
  - "Payment method breakdown"
  - "How popular is boleto?"
disambiguation: "Share is by payment value, not by number of orders."
tables: [order_payments]
grain: payment_type
dimensions: [payment_type]
eval_questions:
  - "What percentage of total payment value comes from credit cards?"
  - "Which payment type has the lowest total value?"
---

## Definition
Total amount and share of sales by `payment_type` (e.g. credit_card, boleto).

## SQL
### main
```sql
SELECT
  op.payment_type,
  SUM(op.payment_value) AS total_value,
  ROUND((100.0 * SUM(op.payment_value) / (SELECT SUM(payment_value) FROM order_payments))::numeric, 2) AS pct
FROM order_payments op
GROUP BY op.payment_type
ORDER BY total_value DESC;
```

## Pitfalls
- An order can use several payment types, so counting orders per type double counts; this metric sums payment_value.
- Valid types: credit_card, boleto, voucher, debit_card (a few not_defined rows exist).

## Interpretation
Credit card is roughly three quarters of value; boleto is the main alternative.
