---
name: executive_overview
domain: dashboards
title: Executive overview dashboard
route: redirect
description: One-page business overview with GMV, orders, average order value, review score and on-time delivery trends; use for broad health-of-the-business or monthly review questions.
triggers:
  - "Show me the executive dashboard"
  - "How is the business doing overall?"
  - "Give me a monthly business review"
  - "Where can I see the key KPIs?"
disambiguation: "For a single specific number (e.g. GMV in March 2018) use the metric skills instead; this is for open-ended overview requests."
tables: [orders, order_items, order_payments, order_reviews]
dashboard:
  provider: streamlit
  page: executive_overview
  params:
    date_from: date
    date_to: date
  placeholder: false
---

## Definition
A single page that shows the main business KPIs over time: monthly GMV, order count, average order value, average review score and the share of orders delivered after the estimated date. It reads the same verified metric SQL as the agent's skills, so its numbers match what the agent answers.
