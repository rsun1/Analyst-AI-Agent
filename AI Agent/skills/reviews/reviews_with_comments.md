---
name: reviews_with_comments
domain: reviews
title: Reviews with comments
route: analyze
description: "Count of reviews that include a non-empty comment title or message, a measure of review engagement."
triggers:
  - "How many reviews have comments?"
  - "What share of customers leave written feedback?"
  - "Count of reviews with a message"
disambiguation: "For the share, divide by the total review count (COUNT(*) over order_reviews)."
tables: [order_reviews]
grain: all
dimensions: []
eval_questions:
  - "How many reviews have a written comment?"
  - "What percentage of reviews include a comment?"
---

## Definition
Count of reviews that have a non-empty comment (title or message). Useful for engagement.

## SQL
### main
```sql
SELECT
  COUNT(*) AS reviews_with_comment
FROM order_reviews
WHERE (review_comment_title IS NOT NULL AND trim(review_comment_title) != '')
   OR (review_comment_message IS NOT NULL AND trim(review_comment_message) != '');
```

## Pitfalls
- Empty strings count as no comment: trim before testing.
- Check both title and message columns.

## Interpretation
Roughly half of reviews have text; low scores are more likely to carry a comment.
