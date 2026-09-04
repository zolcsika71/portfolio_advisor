# Portfolio workflow and current availability

This is the current user-facing workflow contract. It distinguishes the intended
governed workflow from the subset that is implemented today. It does not grant
authorization for portfolio construction, rebalancing, trading, or production
cutover.

## Governed inputs

The workflow keeps three independently governed inputs separate:

| Input | Current evidence boundary | Role in the target workflow |
| --- | --- | --- |
| Current investments | Local LTIA evidence: accounts, holdings, and cash. Legacy `tbsz` paths, databases, tables, packages, and scripts are compatibility identifiers. | Compare the user's current position with an explicitly selected target. |
| Model portfolios | Dated XLS worksheet `modell portfóliók` (with a supported English equivalent in the importer). | Rank eligible predefined model portfolios. |
| Shortlist instruments | Dated XLS worksheet `shortlist`, imported through the schema-v3 shortlist pipeline. | Construct and rank an eligible same-currency shortlist portfolio. |

`LTIA` means **Long-Term Investment Account**. It is the domain term; legacy
`TBSZ` identifiers do not change the meaning or provenance of retained local
evidence. Cash is a separately evidenced currency amount, never an instrument
or an implicit FX conversion.

## Target workflow

When the required policies, evidence, calculations, and persistence contracts
have been reviewed and implemented, the workflow is:

1. The user selects `CAPITAL_CONSERVATION` or `DIVIDEND_MAXIMIZATION`.
2. The user selects a governed investment horizon: 90, 180, or 365 days.
3. The system ranks and selects the best eligible model portfolio from
   `modell portfóliók`.
4. The system constructs and ranks a portfolio from eligible `shortlist`
   instruments.
5. The system compares the two finalists using compatible portfolio-level
   metrics.
6. The governed system recommendation remains separate from the user's
   explicit final choice.
7. Graphify knowledge-base material is retrieved only as cited explanatory
   evidence.
8. An OpenAI explanation layer, if separately implemented and approved, may
   explain the governed result and its retrieved citations. It must not
   calculate a metric, invent evidence, alter eligibility or ranking, or make
   the final selection.
9. The chosen target is compared with the current LTIA holdings and cash.
10. The system produces a proposed transition report.
11. The user explicitly approves or rejects that proposal before any external
   action.
12. The system does not submit orders or imply automated trading.

Steps 3 through 10 describe the target contract, not a claim that the complete
runtime exists now.

## Horizon and evidence semantics

An investment horizon is not a permission to use any available history. Each
decision must retain and validate the following independent values:

| Term | Meaning |
| --- | --- |
| Investment horizon | The user-selected forward objective: 90, 180, or 365 calendar days. |
| Metric lookback window | The historical return interval used for a metric. It is set by a reviewed methodology and can be longer than, equal to, or otherwise distinct from the investment horizon. |
| NAV cutoff | The latest admissible valuation date for a calculation; this is an evidence boundary, not an interpolation target. |
| Decision timestamp | The exact time at which the system evaluates only evidence then available. |
| Minimum history and observations | Reviewed minimum calendar span, common return intervals, and benchmark observations. These must be met independently for every constituent and the selected calculation window. |

Non-trading days, missing benchmark dates, stale NAV, insufficient common
observations, or unavailable evidence must cause an explicit unavailable or
blocked result under the reviewed methodology. They must not be repaired with
nearest dates, interpolation, proxy series, zero returns, or invented history.

## Objective availability

| User-facing objective | Current status | Boundary |
| --- | --- | --- |
| `CAPITAL_CONSERVATION` | **Partially implemented; not a complete portfolio workflow.** | `CAPITAL_PRESERVATION_RANKING_POLICY` v1.0.1 supports reviewed model/singleton-screening foundations. The reviewed `CAPITAL_DEFENSIVE` 80/20 construction policy and schema foundation exist, but aligned return-series methodology, portfolio metrics, real shortlist construction, finalist comparison, and transition reporting remain unavailable pending Phase F approval and implementation. |
| `DIVIDEND_MAXIMIZATION` | **Unavailable.** | The current code has the legacy compatibility objective identity `dividend_portfolio`/`DIVIDEND_PORTFOLIO`, but no validated active dividend policy, dividend evidence, eligibility, construction, ranking, or finalist-comparison contract. No dividend portfolio may be fabricated to populate an interface. |

`DIVIDEND_MAXIMIZATION` is the target user-facing label. It is not an alias
accepted by the current objective parser; no code or data migration is implied
by this documentation.

Phase E admitted immutable exact-share-class EUR/HUF NAV provenance but did
not approve the Phase F metric methodology or activate construction. In
particular, admitted evidence alone does not prove a policy-compliant common
return window, a cash-return convention, or a comparable portfolio metric.

## Output contract examples

These placeholders define the eventual report shape. They are deliberately not
financial outputs and do not represent a recommendation.

### `CAPITAL_CONSERVATION`

```text
objective: CAPITAL_CONSERVATION
investment_horizon_days: <90|180|365>
decision_timestamp: <ISO-8601 timestamp>
metric_lookback: <reviewed window or UNAVAILABLE>
nav_cutoff: <date or UNAVAILABLE>

model_portfolio_finalist: <portfolio identity or UNAVAILABLE>
constructed_shortlist_finalist: <portfolio identity or UNAVAILABLE>
compatible_portfolio_metrics: <versioned governed metric set or UNAVAILABLE>
system_recommendation: <model|shortlist|UNAVAILABLE>

graphify_explanation: <retrieved citations and constrained explanation or UNAVAILABLE>
openai_explanation: <explanation of governed result only or NOT_IMPLEMENTED>

current_ltia_holdings_comparison: <versioned comparison or UNAVAILABLE>
transition_proposal:
  retain: <assets or UNAVAILABLE>
  sell: <assets or UNAVAILABLE>
  buy: <assets or UNAVAILABLE>
  current_weights: <evidenced values or UNAVAILABLE>
  target_weights: <governed values or UNAVAILABLE>
  monetary_differences: <currency-specific values or UNAVAILABLE>
  available_cash: <currency-specific values or UNAVAILABLE>
  required_spending: <currency-specific values or UNAVAILABLE>
  residual_cash: <currency-specific values or UNAVAILABLE>
  currency_mismatches: <explicit blockers>
  unavailable_prices_or_identifiers: <explicit blockers>
  assumptions_and_blocking_conditions: <explicit list>

user_decision_state: <PENDING_EXPLICIT_APPROVAL|APPROVED|REJECTED>
external_action: NOT_AUTHORIZED
```

### `DIVIDEND_MAXIMIZATION`

```text
objective: DIVIDEND_MAXIMIZATION
investment_horizon_days: <90|180|365>
policy_status: UNAVAILABLE_NO_VALIDATED_ACTIVE_POLICY
model_portfolio_finalist: UNAVAILABLE
constructed_shortlist_finalist: UNAVAILABLE
system_recommendation: UNAVAILABLE
graphify_explanation: <retrieved general explanation only; no financial input>
openai_explanation: NOT_IMPLEMENTED
current_ltia_holdings_comparison: UNAVAILABLE
transition_proposal: UNAVAILABLE
assumptions_and_blocking_conditions: <missing policy, dividend evidence, eligibility, construction, ranking, and comparison contracts>
user_decision_state: NO_DECISIONABLE_RESULT
external_action: NOT_AUTHORIZED
```

## Present implementation boundary

The current application can produce a deterministic, point-in-time ranking
from reported model-portfolio indicators. Those indicators are not a
portfolio return series. It cannot currently produce the target workflow's
constructed-shortlist finalist, compatible portfolio-level metrics, Graphify
retrieval/citations in an explanation, OpenAI explanation, current-versus-
target calculation, buy/sell/cash proposal, rebalancing plan, order, or
production recommendation.

Phase F must first approve the financial methodology and then implement and
test deterministic return-series calculations before any disposable candidate
construction may be considered. The detailed evidence, provenance, and Phase E
boundaries are recorded in [the Phase E record](milestone_11c_phase_e_nav_provenance.md).

## Non-negotiable safety boundary

All recommendations and proposals remain advisory. A user decision is explicit
and independently recorded; it is not inferred from a ranking or explanation.
No current or planned component may automatically rebalance, submit a buy or
sell order, contact a broker, or convert currencies.
