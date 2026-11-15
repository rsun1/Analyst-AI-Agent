---
name: avg_installments_by_payment_type
domain: payments
title: Average installments by payment type
route: analyze
description: "Average number of payment installments for each payment type."
triggers:
  - "How many installments do customers choose on average?"
  - "Average installments for credit cards"
  - "Do boleto payments use installments?"
disambiguation: "Per payment row, not per order."
tables: [order_payments]
grain: payment_type
dimensions: [payment_type]
eval_questions:
  - "What is the average number of installments for credit card payments?"
  - "How many payments are there for each payment type?"
---

## Definition
Average number of payment installments per payment type.

## SQL
### main
```sql
SELECT
  payment_type,
  AVG(payment_installments) AS avg_installments,
  COUNT(*) AS payment_count
FROM order_payments
GROUP BY payment_type
ORDER BY avg_installments DESC;
```

## Pitfalls
- Only credit cards use installments meaningfully; other types are almost always 1.
- Averages are over payment rows, so orders with several payments weigh more.

## Interpretation
Compare credit-card installments to 1 to gauge how much customers rely on credit.
