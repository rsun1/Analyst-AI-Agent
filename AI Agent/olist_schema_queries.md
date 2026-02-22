# Olist SQLite: Schema, Definitions & Sample Queries

This document defines metrics and logic for the **olist.sqlite** dataset (Olist Brazilian e‑commerce) and provides sample SQL queries. All queries are written for **SQLite** (e.g. use `strftime` for dates, not `date_trunc`).

---

## 1. Sales

### definition: seller_tpv_per_month

Total sales (TPV = total payment value) for each seller per month, based on `order_purchase_timestamp` in the `orders` table. One row per seller per month.

### SQL query

```sql
SELECT
  oi.seller_id,
  strftime('%Y-%m', o.order_purchase_timestamp) AS order_month,
  SUM(op.payment_value) AS tpv
FROM orders o
LEFT JOIN order_payments op ON op.order_id = o.order_id
LEFT JOIN order_items oi ON oi.order_id = o.order_id
GROUP BY oi.seller_id, order_month
ORDER BY order_month, tpv DESC;
```

---

### definition: monthly_gmv

Gross merchandise value per month: total `payment_value` from `order_payments` per calendar month (by `order_purchase_timestamp`).

### SQL query

```sql
SELECT
  strftime('%Y-%m', o.order_purchase_timestamp) AS order_month,
  SUM(op.payment_value) AS gmv
FROM orders o
LEFT JOIN order_payments op ON op.order_id = o.order_id
GROUP BY order_month
ORDER BY order_month;
```

---

### definition: revenue_by_product_category

Total revenue (payment_value) attributed to each product category. Uses the product’s category on the order item; multiple items per order share the order’s payment, so this is an approximation (payment split by item count or by item price share would refine it).

### SQL query

```sql
SELECT
  p.product_category_name,
  SUM(op.payment_value) AS revenue
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
JOIN order_payments op ON op.order_id = o.order_id
JOIN products p ON p.product_id = oi.product_id
GROUP BY p.product_category_name
ORDER BY revenue DESC;
```

---

## 2. Customers & orders

### definition: customer_order_count

Number of orders per customer. Useful for repeat purchase and segmentation.

### SQL query

```sql
SELECT
  o.customer_id,
  COUNT(DISTINCT o.order_id) AS order_count
FROM orders o
GROUP BY o.customer_id
ORDER BY order_count DESC;
```

---

### definition: orders_per_month

Count of orders per calendar month (by `order_purchase_timestamp`).

### SQL query

```sql
SELECT
  strftime('%Y-%m', order_purchase_timestamp) AS order_month,
  COUNT(*) AS order_count
FROM orders
GROUP BY order_month
ORDER BY order_month;
```

---

### definition: sales_by_customer_state

Total payment value by customer state, using `customers.customer_state`.

### SQL query

```sql
SELECT
  c.customer_state,
  SUM(op.payment_value) AS total_sales
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN order_payments op ON op.order_id = o.order_id
GROUP BY c.customer_state
ORDER BY total_sales DESC;
```

---

## 3. Payments

### definition: payment_type_breakdown

Total amount and share of sales by `payment_type` (e.g. credit_card, boleto).

### SQL query

```sql
SELECT
  op.payment_type,
  SUM(op.payment_value) AS total_value,
  ROUND(100.0 * SUM(op.payment_value) / (SELECT SUM(payment_value) FROM order_payments), 2) AS pct
FROM order_payments op
GROUP BY op.payment_type
ORDER BY total_value DESC;
```

---

### definition: avg_installments_by_payment_type

Average number of payment installments per payment type.

### SQL query

```sql
SELECT
  payment_type,
  AVG(payment_installments) AS avg_installments,
  COUNT(*) AS payment_count
FROM order_payments
GROUP BY payment_type
ORDER BY avg_installments DESC;
```

---

## 4. Reviews

### definition: average_review_score_per_order

Average review score per order (one review per order in this dataset). Can be aggregated further by time or category.

### SQL query

```sql
SELECT
  o.order_id,
  o.order_purchase_timestamp,
  r.review_score
FROM orders o
LEFT JOIN order_reviews r ON r.order_id = o.order_id
ORDER BY o.order_purchase_timestamp DESC;
```

---

### definition: monthly_average_review_score

Average review score per calendar month (by review creation date).

### SQL query

```sql
SELECT
  strftime('%Y-%m', review_creation_date) AS review_month,
  AVG(review_score) AS avg_score,
  COUNT(*) AS review_count
FROM order_reviews
WHERE review_creation_date IS NOT NULL
GROUP BY review_month
ORDER BY review_month;
```

---

### definition: reviews_with_comments

Count of reviews that have a non-empty comment (title or message). Useful for engagement.

### SQL query

```sql
SELECT
  COUNT(*) AS reviews_with_comment
FROM order_reviews
WHERE (review_comment_title IS NOT NULL AND trim(review_comment_title) != '')
   OR (review_comment_message IS NOT NULL AND trim(review_comment_message) != '');
```

---

## 5. Delivery & items

### definition: delivery_delay_days

Days between estimated delivery date and actual delivery date (positive = late). One row per order.

### SQL query

```sql
SELECT
  order_id,
  order_estimated_delivery_date,
  order_delivered_customer_date,
  julianday(order_delivered_customer_date) - julianday(order_estimated_delivery_date) AS delay_days
FROM orders
WHERE order_delivered_customer_date IS NOT NULL
  AND order_estimated_delivery_date IS NOT NULL
ORDER BY delay_days DESC;
```

---

### definition: freight_to_price_ratio_by_category

Average ratio of freight value to item price by product category (from order_items and products).

### SQL query

```sql
SELECT
  p.product_category_name,
  AVG(CASE WHEN oi.price > 0 THEN oi.freight_value / oi.price ELSE NULL END) AS avg_freight_to_price,
  COUNT(*) AS item_count
FROM order_items oi
JOIN products p ON p.product_id = oi.product_id
GROUP BY p.product_category_name
HAVING item_count >= 10
ORDER BY avg_freight_to_price DESC;
```

---

## 6. Leads

### definition: lead_conversion_count

Number of qualified leads (leads_qualified) that became closed/won (leads_closed). One row per converted lead.

### SQL query

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

---

### definition: conversion_rate_by_origin

Conversion rate (qualified → closed) by lead origin.

### SQL query

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

---

## 7. Top N / rankings

### definition: top_10_sellers_by_revenue

Sellers ranked by total payment value (TPV) linked via order_items.

### SQL query

```sql
SELECT
  oi.seller_id,
  SUM(op.payment_value) AS total_revenue
FROM order_items oi
JOIN order_payments op ON op.order_id = oi.order_id
GROUP BY oi.seller_id
ORDER BY total_revenue DESC
LIMIT 10;
```

---

## 8. Cross-domain: Orders ↔ Leads (via sellers)

**Key concept:** Sellers are the bridge entity between the orders domain and the leads domain. There is NO direct join between `orders` (or `customers`) and `leads_qualified` / `leads_closed`. The correct multi-hop path is:

`orders → order_items (order_id) → leads_closed (seller_id) → leads_qualified (mql_id)`

Use `customers` on the orders side only for filtering by customer attributes (e.g. state). Never join `customers` or `leads_qualified` directly to each other.

### definition: leads_generated_from_orders_by_period_and_state

Count of distinct leads (MQLs) whose sellers had orders placed by customers from specific states within a given time window. Also returns total leads ever contacted (all-time count from leads_qualified, no filter). This is the canonical cross-domain query pattern for "how many leads came from orders in period X from states Y?".

### SQL query

```sql
-- Part 1: leads generated from orders in the period, from customers in given states
-- Path: orders -> customers (state filter) + orders -> order_items -> leads_closed -> leads_qualified
SELECT
  COUNT(DISTINCT lq.mql_id) AS leads_generated
FROM orders o
JOIN customers c    ON c.customer_id  = o.customer_id
JOIN order_items oi ON oi.order_id    = o.order_id
JOIN leads_closed lc ON lc.seller_id  = oi.seller_id
JOIN leads_qualified lq ON lq.mql_id  = lc.mql_id
WHERE o.order_purchase_timestamp BETWEEN '2017-07-01' AND '2017-08-31'
  AND c.customer_state IN ('BA', 'MA', 'SP');

-- Part 2: total leads ever contacted (all-time, no date or state filter)
SELECT COUNT(DISTINCT mql_id) AS total_leads_contacted
FROM leads_qualified;
```

---

### definition: leads_generated_and_contacted_combined

Single query that returns both the filtered lead count (orders in a period, customers from given states) and the all-time total leads contacted side by side. Uses a subquery for the all-time total so both numbers appear in one result row.

### SQL query

```sql
SELECT
  COUNT(DISTINCT lq.mql_id) AS leads_generated,
  (SELECT COUNT(DISTINCT mql_id) FROM leads_qualified) AS total_leads_contacted_ever
FROM orders o
JOIN customers c     ON c.customer_id  = o.customer_id
JOIN order_items oi  ON oi.order_id    = o.order_id
JOIN leads_closed lc ON lc.seller_id   = oi.seller_id
JOIN leads_qualified lq ON lq.mql_id   = lc.mql_id
WHERE o.order_purchase_timestamp BETWEEN '2017-07-01' AND '2017-08-31'
  AND c.customer_state IN ('BA', 'MA', 'SP');
```

---

### definition: seller_order_volume_with_lead_status

For each seller who had orders in a given period, show their order count, total revenue, and whether they appear in the leads pipeline (converted or not). Useful for understanding overlap between active sellers and the MQL funnel.

### SQL query

```sql
SELECT
  oi.seller_id,
  COUNT(DISTINCT o.order_id)   AS order_count,
  SUM(op.payment_value)        AS total_revenue,
  CASE WHEN lc.seller_id IS NOT NULL THEN 'in_leads' ELSE 'not_in_leads' END AS lead_status
FROM orders o
JOIN order_items oi   ON oi.order_id  = o.order_id
JOIN order_payments op ON op.order_id = o.order_id
LEFT JOIN leads_closed lc ON lc.seller_id = oi.seller_id
WHERE o.order_purchase_timestamp BETWEEN '2017-01-01' AND '2017-12-31'
GROUP BY oi.seller_id, lead_status
ORDER BY total_revenue DESC;
```

---

### definition: lead_conversion_rate_by_customer_state

Conversion rate (leads_qualified → leads_closed) broken down by the customer state of the orders those sellers fulfilled. Shows which geographic markets have the highest seller conversion rates.

### SQL query

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

---

## Reference: main relationships

| From table    | To table     | Join key(s)                          |
|---------------|-------------|--------------------------------------|
| orders        | customers   | `orders.customer_id` = `customers.customer_id` |
| orders        | order_items | `orders.order_id` = `order_items.order_id`     |
| orders        | order_payments | `orders.order_id` = `order_payments.order_id` |
| orders        | order_reviews  | `orders.order_id` = `order_reviews.order_id`  |
| order_items   | products    | `order_items.product_id` = `products.product_id` |
| order_items   | sellers     | `order_items.seller_id` = `sellers.seller_id`  |
| products      | product_category_name_translation | `products.product_category_name` = `...product_category_name` |
| leads_qualified | leads_closed | `leads_closed.mql_id` = `leads_qualified.mql_id` |
| leads_closed    | sellers      | `leads_closed.seller_id` = `sellers.seller_id` — **this is the bridge between orders and leads** |

All date/datetime fields are stored as text; use `strftime` and `julianday` in SQLite for filtering and grouping by time.

**Cross-domain bridge (critical):** There is NO direct join between `orders`/`customers` and `leads_qualified`/`leads_closed`. To connect them, always traverse: `orders → order_items (order_id) → leads_closed (seller_id) → leads_qualified (mql_id)`. The seller is the entity that exists in both domains.
