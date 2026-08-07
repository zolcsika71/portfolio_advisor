# Deterministic capital-preservation methodology

## Scope and audit trail

`CapitalPreservationAdvisor` is a deterministic, read-only workflow:

```text
SQLite observation -> allocation-weighted reported indicators -> eligibility
-> min-max scoring -> stable ranking -> structured result
```

It reads the most recent valid `Date` from the `model_portfolios` table and
opens the database using SQLite's read-only URI mode. It does not import,
update, or derive a new database observation. Each `AdvisorResult` includes:

| Result field | Meaning |
|---|---|
| `observation_date` | Latest source date used for all holdings in the result. |
| `rules_status` | Source policy's `reviewed`, `approved`, or `proposed` status; `unavailable` when no policy can be safely used. |
| `rule_set_version` | Required immutable `version` from the loaded YAML; `unavailable` when policy loading fails. |
| `proposed_rules_explicitly_enabled` | Whether the caller explicitly set `allow_proposed_rules` / `--allow-proposed-rules`. |

The checked-in database regression test pins the explicitly enabled proposed
policy outcome for observation date **2026-07-06**. It also hashes the database
before and after evaluation to make the read-only boundary observable.

## SQLite source fields and output mapping

The advisor requires the following fields. All numeric data is interpreted as
the decimal/percentage representation stored by the importer; allocation is in
percentage points, while the reported risk and return values are used without
unit conversion.

| SQLite field | Holding observation field | Portfolio output | Treatment |
|---|---|---|---|
| `Date` | observation-date selector | `observation_date` | Latest parseable date is selected. |
| `Portfolio Name` | `portfolio_name` | candidate name | Groups holdings into one candidate. |
| `Product`, `ISIN` | identifiers | none | Retained for traceability; not ranked. |
| `Allocation (%)` | `allocation` | `allocation_total`, all weighted indicators | Weight and coverage denominator. |
| `Currency` | `currency` | `currency_concentration` | Currency allocation Herfindahl index. |
| `Currency Risk` | `currency_risk` | `unhedged_allocation` | Allocation whose value is `Unhedged`, case-insensitively. |
| `1 Year` | `return_1y` | `return_1y` | Allocation-weighted reported indicator. |
| `1Y Sharpe Ratio` | `sharpe_ratio_1y` | `sharpe_ratio` | Allocation-weighted reported indicator. |
| `1Y Volatility` | `volatility_1y` | `annualized_volatility` | Allocation-weighted reported indicator. |
| `Downside Risk` | `downside_risk` | `downside_deviation` | Allocation-weighted reported indicator. |
| `Maximum Drawdown` | `maximum_drawdown` | `maximum_drawdown` | Allocation-weighted reported indicator. |

For a reported constituent metric \(x_i\), only allocated holdings with an
observed value participate:

\[
\bar{x} = \frac{\sum_{i \in O} a_i x_i}{\sum_{i \in O} a_i},
\qquad
coverage = \frac{\sum_{i \in O} a_i}{\sum_i a_i}.
\]

The result retains coverage and a warning whenever it is below 100%; it does
not treat missing values as zero. `allocation_total` is the raw sum of all
non-null allocations, so the policy can reject incomplete allocations.

## Reported indicators versus recomputed return-series metrics

The database contains latest-date *reported constituent indicators*, not a
periodic portfolio return history. Therefore the production result does **not**
reconstruct returns, covariance, volatility, drawdown, Sharpe, Sortino, VaR,
or CVaR. In particular, output `annualized_volatility` and
`maximum_drawdown` are allocation-weighted reported values, not a volatility
or drawdown recomputed from the holdings.

The following warnings deliberately remain in every candidate's unavailable
metrics (and are de-duplicated into result warnings):

- `historical_var` and `historical_cvar`: no periodic return history;
- `sortino_ratio`: no periodic return history or target return;
- `cost_indicators`: no cost/fee field;
- `liquidity_indicators`: no liquidity field; and
- `currency_mismatch`: no investor base currency (the descriptive currency
  indicators are shown instead).

`Downside Risk` is a reported source field. If it is absent for every
allocated holding, `downside_deviation` is separately marked unavailable.

## Return-series formulas and annualization assumptions

`metrics/calculations.py` supplies deterministic formulas for a future caller
that provides a complete periodic return series. These are documented and
tested but are not invoked by the SQLite aggregation described above. Returns
are decimal values: `0.01` means one percent. Missing observations make the
relevant calculation unavailable rather than being imputed.

| Function | Formula / convention |
|---|---|
| Compounded return | \(\prod_t(1+r_t)-1\). |
| Annualized volatility | Sample standard deviation \(s(r)\sqrt{P}\), requiring at least two periods. |
| Maximum drawdown | Minimum of \(W_t / \max_{u\leq t}W_u - 1\), with \(W_t=\prod_{u\leq t}(1+r_u)\). |
| Downside deviation | \(\sqrt{mean(min(r_t-T,0)^2)}\sqrt{P}\), using all periods in the denominator. |
| Sharpe ratio | \(mean(r_t-r_f)P / [s(r)\sqrt{P}]\). |
| Sortino ratio | \(mean(r_t-T)P / downside\ deviation\). |
| Historical VaR | Non-negative loss at the linearly interpolated empirical \(1-c\) return quantile. |
| Historical CVaR | Mean non-negative loss of returns at or below that VaR cutoff. |

`P` is the caller-supplied positive number of periods per year; it is not
inferred. `r_f` and `T` are per-period, not annual, rates. The source fields
whose names begin `1Y` are already reported one-year indicators and are never
annualized again. Zero volatility or zero downside deviation yields an
unavailable ratio rather than infinity. Historical VaR/CVaR require a
confidence level strictly between zero and one and at least two complete
observations.

## Eligibility, scoring, and deterministic order

The versioned YAML policy controls allocation target/tolerance, minimum metric
coverage, required metrics, metric weights, and directions. The current policy
uses only `maximum_drawdown`, `annualized_volatility`,
`unhedged_allocation`, `sharpe_ratio`, and `return_1y` for scoring. It first
rejects a candidate whose allocation or required coverage is outside the
configured eligibility constraints.

For each available scoring metric among eligible portfolios, values are min-max
normalized. Higher-is-better metrics use \((x-min)/(max-min)\); lower-is-better
metrics invert that value. Equal observed values receive a normalized score of
one. The total is the sum of `weight * normalized_score`. Ranking sorts by
descending total score, then ascending Unicode portfolio name, so equal scores
are deterministic. Rejected candidates remain in the output after ranked
candidates with `rank: null` and their rejection reasons.

## Validated and proposed policy behavior

Only a YAML policy with `status: reviewed` or `status: approved` is executable
by default. A `status: proposed` policy is intentionally blocked unless the
caller sets `allow_proposed_rules=True` (CLI:
`--allow-proposed-rules`). The shipped `1.0.0` policy is proposed, so its
regression outcome is not an approved investment recommendation.

All executable policies must provide a non-empty `version`. If the policy is
missing, unreadable, invalid YAML, unversioned, malformed, or proposed without
explicit opt-in, the advisor fails closed: it reports the problem in `warnings`
and returns no selected portfolio, no ranking, and
`rules_status: "unavailable"`. There is deliberately no numeric fallback.
