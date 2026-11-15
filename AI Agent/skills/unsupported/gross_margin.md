---
name: gross_margin
domain: unsupported
title: Gross margin and profit
route: not_computable
description: Gross margin, profit or cost-of-goods metrics; not available because the dataset has no cost, COGS or supplier price data.
triggers:
  - "What is our gross margin?"
  - "How profitable is each category?"
  - "What is the profit per order?"
  - "Which products have the best margin?"
disambiguation: "Revenue and freight are available (see monthly_gmv, revenue_by_product_category), but cost of goods, fees and operating costs are not."
tables: []
---

## Definition
Gross margin needs the cost of each item sold. Olist data contains only what customers paid (item price, freight, payment value) and has no cost of goods, seller commissions or operating costs, so margin and profit cannot be computed. The closest available proxy is revenue (order_items.price) or GMV (payment value), which are revenue measures and must not be presented as profit.
