"""Dataset profiles: business rules and context for the databases the agent can query.

OLIST_PROFILE describes the demo database built by `python -m data.build_db`. Its rules match
the definitions used by the reports and evaluation (see docs/DECISIONS.md).
"""

from __future__ import annotations

from shared.models import DatasetProfile

_OLIST_EXAMPLES = """\
Examples (for a question -> reply format; your schema is the one above):

Question: How many orders were placed in 2017?
{"action": "sql", "sql": "SELECT COUNT(*) AS orders FROM orders WHERE strftime('%Y', purchase_ts) = '2017'", "assumptions": "Counts orders of every status."}

Question: Monthly revenue in the first half of 2018
{"action": "sql", "sql": "SELECT strftime('%Y-%m', o.purchase_ts) AS month, ROUND(SUM(oi.price), 2) AS revenue FROM orders o JOIN order_items oi ON oi.order_id = o.order_id WHERE o.status NOT IN ('canceled', 'unavailable') AND o.purchase_ts >= '2018-01-01' AND o.purchase_ts < '2018-07-01' GROUP BY month ORDER BY month", "assumptions": "Revenue excludes freight and canceled/unavailable orders."}

Question: How are we doing?
{"action": "clarify", "question": "Which measure and period do you mean, e.g. revenue, orders or review scores in 2018?"}

Question: Remove all reviews with a score of 1
{"action": "refuse", "reason": "I can only read data, not change or delete it."}

The examples only show the reply format. Never copy an example reply; answer the actual question.
"""  # noqa: E501

OLIST_PROFILE = DatasetProfile(
    name="Olist e-commerce",
    description="an e-commerce marketplace (Olist, Brazil)",
    rules=[
        "Only when the question asks for revenue (sales value, average order value): Revenue = "
        "SUM(order_items.price) for orders whose status is not 'canceled' or 'unavailable' "
        "(freight excluded), unless the question asks for something else.",
        "Do not add that canceled/unavailable filter to questions that are not about revenue "
        "(e.g. counts of orders, sellers or payments). If the question names a status, such as "
        "delivered or shipped orders, filter on that status (status = 'delivered').",
        "Count people with COUNT(DISTINCT customers.customer_unique_id), not customer_id.",
        "After joining order_items (one row per item), count orders with "
        "COUNT(DISTINCT o.order_id).",
    ],
    currency="Brazilian reais (BRL)",
    currency_format="R$ 1,234.56",
    date_range=("2017-01-01", "2018-08-21"),
    examples=_OLIST_EXAMPLES,
    filter_example='"excluding canceled and unavailable orders", not "completed orders"',
    include_samples=True,
)
