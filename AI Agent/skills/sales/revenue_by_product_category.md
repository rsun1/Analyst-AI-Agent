---
name: revenue_by_product_category
domain: sales
title: Revenue by product category
route: analyze
description: "Total item-level revenue (sum of order_items.price) per product category, with English category names."
triggers:
  - "Which product categories make the most revenue?"
  - "Top 5 categories by revenue"
  - "Revenue by category"
disambiguation: "Uses item price, excludes freight and payment adjustments. For total customer payments use monthly_gmv."
tables: [order_items, products, product_category_name_translation]
grain: category
dimensions: [product_category_name_english]
eval_questions:
  - "What are the top 5 product categories by total revenue?"
  - "How much revenue did the health_beauty category generate?"
---

## Definition
Total item-level revenue (sum of order_items.price) per product category. Use order_items.price — NOT order_payments.payment_value — to avoid double-counting when an order has items in multiple categories. Join product_category_name_translation to return English category names.

## SQL
### main
```sql
SELECT
  pct.product_category_name_english,
  SUM(oi.price) AS revenue
FROM order_items oi
JOIN products p ON p.product_id = oi.product_id
JOIN product_category_name_translation pct
  ON pct.product_category_name = p.product_category_name
GROUP BY pct.product_category_name_english
ORDER BY revenue DESC;
```

## Pitfalls
- Use order_items.price, NOT order_payments.payment_value: payments are per order and double count orders with several categories.
- product_category_name is Portuguese; always JOIN product_category_name_translation and use product_category_name_english.
- Inner join drops products without a category; that is a small share.

## Interpretation
Revenue is concentrated in a handful of categories; report the share of the total when helpful.
