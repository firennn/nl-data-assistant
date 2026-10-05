# Case study: a natural-language data assistant for an e-commerce marketplace

## The business question

Managers and analysts at a marketplace ask the same kinds of questions every week: how much did
we sell, which categories grow, where are deliveries late, what will next month look like.
Answering them usually means waiting for someone who can write SQL. This project asks two
questions:

1. Can a business user get **correct, explainable answers** from the sales database by asking
   in plain language, without writing SQL and without any risk of changing the data?
2. Can the **weekly performance report** be produced automatically, with numbers that can be
   trusted?

## Data

The [Olist Brazilian E-Commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce):
about 99,000 orders from a Brazilian marketplace between 2016 and 2018, in 8 related tables
(orders, items, payments, reviews, customers, sellers, products, geolocation). The data is
complete from January 2017 to about 21 August 2018. It is rebuilt locally into a SQLite
database by `python -m data.build_db`; cleaning steps and limitations are in
[SCHEMA.md](SCHEMA.md).

One shared business definition is used everywhere (agent, report and evaluation):
**revenue = sum of item prices for orders that are not canceled or unavailable, without
freight.**

## Method

| Part | What it does |
|---|---|
| NL-to-SQL agent (`agent/`) | Turns a question into one read-only SQL query using the database schema and a dataset profile (business rules, currency, date range). Asks a clarifying question when readings differ a lot, retries failed SQL with the error message, refuses requests to change data, picks a chart by fixed rules and explains the result in 2-4 sentences. |
| Safety (`shared/`) | Every query passes three independent checks: a validator (one SELECT/WITH statement, no write keywords), a read-only connection, and an SQLite authorizer that only allows reads. |
| Weekly report (`reports/`, `modeling/`) | Six weekly metrics compared with the previous week, charts, daily anomalies (rolling z-score) and a 4-week revenue forecast (damped-trend exponential smoothing, kept only because it beats a naive baseline in a rolling backtest). The LLM writes the summary from the computed facts only, and every number in it is checked. See [REPORTS_AND_MODELING.md](REPORTS_AND_MODELING.md). |
| Evaluation (`evaluation/`) | 27 held-out questions with answers verified by two independent queries; predictions scored by the data they return; every failure classified. See [EVALUATION.md](EVALUATION.md). |
| Dashboard (`dashboard/`) | Streamlit app with chat, reports, evaluation results and usage statistics. |

## Findings about the business

Numbers below come from queries on the full database (revenue as defined above, in BRL).

- **Strong growth.** Revenue in January-August 2018 was 7.34 million BRL against 3.08 million
  in the same months of 2017 (2.4 times as much); the first quarter alone grew 3.8 times
  (0.73 million to 2.76 million).
- **Black Friday dominates the calendar.** 24 November 2017 was the busiest day in the data with
  1,176 orders, about 9.5 times the 2017 daily average of 124. The report's anomaly check flags
  it as by far the largest outlier.
- **Late deliveries hurt reviews.** 6.8% of delivered orders arrived after the estimated
  delivery date. Their average review score is 2.27, against 4.29 for orders that arrived on
  time. Average delivery time improved from 13.0 days (2017) to 12.1 days (2018).
- **Revenue is spread across categories.** The top three categories by revenue are
  health_beauty (9.3% of revenue), watches_gifts (8.9%) and bed_bath_table (7.7%);
  bed_bath_table sells the most items.
- **Sao Paulo is the core market.** SP has 41.9% of all customers and 38.3% of revenue.
- **Credit card is the main payment method** (78.3% of the payment value).
- **Almost every customer orders only once**: 3.1% of customers placed more than one order.
  This is also why "new customers" and "orders" are nearly equal in the weekly report.
- **The last week in the data** (13-19 August 2018) had 244,676 BRL revenue, 11.3% below the
  previous week, with no anomalies. The forecast for the next four weeks is about 255,000 BRL
  per week, with an 80% range of 202,000 to 307,000 BRL one week ahead that widens to
  186,000 to 327,000 BRL four weeks ahead.

## Findings about the assistant

Measured on 27 held-out questions with verified answers ([EVALUATION.md](EVALUATION.md)):

- **96% accuracy** with gemini-3.1-flash-lite and the Olist profile, the same in three repeated
  runs; 93% without the business rules and 85% with openai/gpt-oss-120b. After the revenue
  rule was reworded to apply only to revenue questions, one run scored 100% (27/27; see
  docs/EVALUATION.md).
- **Every request to change data was refused**, and the read-only layers would block a write
  even if a model produced one.
- **Business rules matter in both directions.** Without them the agent included canceled
  orders in an average and double-counted payments through a join; with them it sometimes
  applied the revenue rule's status filter to questions that are not about revenue.
- **Ambiguous questions were answered, not asked about**: each model picked a reasonable reading
  and stated its assumption.
- Failures concentrate in multi-table joins and in how "delivered" is defined; filters,
  rankings and refusals were correct in every run.

## Recommendations

**For the business**

1. **Fix late deliveries first.** Late orders score about 2 points lower on a 5-point scale;
   reducing the 6.8% late rate (for example with more realistic delivery estimates or carrier
   follow-up) is the clearest lever for customer satisfaction.
2. **Plan capacity and campaigns around Black Friday**, which brings about ten times a normal
   day's orders.
3. **Work on repeat purchases.** With only 3.1% returning customers, growth depends almost
   entirely on new customers; a retention offer after the first delivery is worth testing.
4. **Use the weekly report and its forecast range** for planning rather than single numbers:
   even the best simple forecast is off by about 20% per week on this data.

**For the assistant**

1. **Reword the revenue rule** in the dataset profile so it clearly applies only to revenue
   questions; in the evaluation it was sometimes applied to questions about orders.
2. **Grow the evaluation set** beyond 27 questions and repeat runs, so differences between
   models of a few percent become measurable.
3. **Keep the business rules**: without them the agent made the classic mistakes (double
   counting through joins, including canceled orders in revenue).

## Limitations

- The dataset ends in 2018 and covers one marketplace; findings describe that period only.
- The evaluation has 27 questions, so one question is about 3.7 percentage points; small
  differences between models are not significant.
- Only free-tier models were evaluated, and model answers vary between runs.
- The chat page sends the question, the schema (for the demo database with a few sample values)
  and the result rows needed for the explanation to the LLM provider API; the database itself
  stays local.
