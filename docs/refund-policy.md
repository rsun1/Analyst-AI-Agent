# Refund / dispute policy (v1)

Source of the numbers: queries against the Olist Postgres (delivered orders only, order value = `SUM(payment_value)` per order). The thresholds are a proposal; change them here and in `workflow/rules.py` together.

## Data facts that shape the rules

| Fact | Value | Consequence |
|---|---|---|
| Order value (delivered): P50 / P75 / P90 / P95 / P99 | 105 / 176 / 306 / 446 / 1,052 BRL | thresholds below |
| Delivered late vs. estimate | 7,826 of 96,470 (8.1%) | "late" is a meaningful signal, not the norm |
| Review scores 1 / 2 / 3 / 4 / 5 | 11.4k / 3.2k / 8.2k / 19.1k / 57.3k | 1-2 stars = 15% of reviews |
| Order date range | 2016-09 to 2018-10 | policy clock must be injectable (`as_of`) |
| Orders with 0 payment rows | 1 (`bfbd0f9b...`, delivered) | nothing to refund, treat as invalid |
| Delivered with no delivered date | 8 | window cannot be computed, route to a human |
| Orders with more than one review | 547 | use the lowest score (`MIN`) |
| Customers appearing under >1 `customer_id` | 2,997 `customer_unique_id`s | repeat-refund history must be keyed on `customer_unique_id`, not `customer_id` |

## Thresholds

- **Small amount:** `<= 100 BRL` (about the median order).
- **High value:** `>= 450 BRL` (about P95).
- **Refund window:** 30 days after the delivered date.
- **Repeat refunder:** `>= 2` refunded disputes in the last 90 days.

## Principle

Deterministic rules decide. When in doubt the outcome is a human review, never an automatic approval. See `PHASE_A_PLAN.md` section 4 for the ordered rule table.

## Test fixtures (real Olist orders)

`as_of` is chosen per case (delivered date + a few days) so the order is inside the window, except T6.

| Case | order_id | Status | Paid | Review | Delivered / estimated | Expected |
|---|---|---|---|---|---|---|
| T1 late + low score + small | `001d8f0e34a38c37f7dba2a37d4eba8b` | delivered | 53.54 | 1 | 2017-05-26 / 2017-05-24 | auto_approve (R09) |
| T1b late + low score + small | `0030d783f979fbc5981e75613b057344` | delivered | 78.27 | 1 | 2018-01-06 / 2017-12-15 | auto_approve (R09) |
| T2 high value | `0009c9a17f916a706d71784483a5d643` | delivered | 650.34 | 4 | 2018-04-30 / 2018-05-09 | needs_review, policy (R07) |
| T2b high value | `000576fe39319847cbb9d288c5617fa6` | delivered | 880.75 | 5 | 2018-07-09 / 2018-07-25 | needs_review, policy (R07) |
| T5 canceled | `00310b0c75bb13015ec4d82d341865a4` | canceled | 55.28 | 1 | none / 2018-08-29 | reject (R03) |
| T6 outside window | `000e562887b1f2006d75e0be9558292e` with `as_of` = 2018-06-01 | delivered | 41.11 | 3 | 2018-03-12 / 2018-03-19 | reject (R05) |
| T7 contradiction ("never arrived") | `00010242fe8c5a6d1ba2dd792cb16214` | delivered | 72.19 | 5 | 2017-09-20 / 2017-09-29 | needs_review, agent_uncertain (R06) |
| T-mid default | `00018f77f2f0320c557190d7a144bdd3` | delivered | 259.83 | 4 | 2017-05-12 / 2017-05-15 | needs_review, policy (R99) |
| T-small on time | `000e63d38ae8c00bbcb5a30573b99628` | delivered | 56.78 | 3 | 2018-03-27 / 2018-04-05 | auto_approve (R10) |
| T8a no payment rows | `bfbd0f9bdef84302105ad712db648a6c` | delivered | none | n/a | n/a | reject (invalid) |
| T8b unknown order | `does-not-exist` | n/a | n/a | n/a | n/a | 400 / R01 |

Repeat-refunder (R08), duplicate open dispute (R02), amount over paid (R04), T9 (payment failure) and T10 (concurrency) are built from the cases above plus extra disputes created in the test.
