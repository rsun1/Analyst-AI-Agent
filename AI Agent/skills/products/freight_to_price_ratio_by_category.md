---
name: freight_to_price_ratio_by_category
domain: products
title: Freight-to-price ratio by category
route: analyze
description: "Average ratio of freight value to item price by product category (English names), for categories with enough items."
triggers:
  - "Which categories have the highest shipping cost relative to price?"
  - "Freight to price ratio by category"
  - "Where is freight most expensive compared to the product?"
disambiguation: "Ratio of freight to price per item, averaged; not total freight spend."
tables: [order_items, products, product_category_name_translation]
grain: category
dimensions: [product_category_name_english]
eval_questions:
  - "Which product category has the highest average freight-to-price ratio?"
  - "What is the freight-to-price ratio for the computers category?"
---

## Definition
Average ratio of freight value to item price by product category (from order_items and products).

## SQL
### main
```sql
SELECT
  pct.product_category_name_english,
  AVG(CASE WHEN oi.price > 0 THEN oi.freight_value / oi.price END) AS avg_freight_to_price,
  COUNT(*) AS item_count
FROM order_items oi
JOIN products p ON p.product_id = oi.product_id
JOIN product_category_name_translation pct ON pct.product_category_name = p.product_category_name
GROUP BY pct.product_category_name_english
HAVING COUNT(*) >= 10
ORDER BY avg_freight_to_price DESC;
```

## Pitfalls
- Postgres does not allow a SELECT alias in HAVING: use HAVING COUNT(*) >= 10.
- Guard against price = 0 to avoid division by zero.
- Translate category names with product_category_name_translation.

## Interpretation
Cheap, heavy items have the highest ratios.
