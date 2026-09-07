"""The operator-editable one-minute trading parameters, and where each one binds.

Every field listed here is a parameter the Voting Ensemble actually trades on. The
registry is the single source of truth for three things that used to disagree: what the
resolver accepts as an override, what the API offers for editing, and what the dashboard
renders. Before this existed the resolver took ten keys, the dashboard offered a
different fifteen (several of them removed from the backend), and the live path read
neither.

`bindsAt` on each field names the read site that makes the value take effect. That is not
decoration: `BASELINES.md` records a set of controls that are resolved into the settings
and consumed by nothing, and shipping those as editable would promise an operator a
change the algorithm never makes. Inert parameters are listed separately in
`INERT_ONE_MINUTE_PARAMETERS` and are reported read-only.

`binding` says which engine honours the field:

- ``live``     the dedicated one-minute pipeline that trades the paper account
- ``backtest`` the legacy ``main.py`` replay engine only
- ``both``     read by each of them
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


EDITABLE_SETTINGS_CATALOG_VERSION = "voting_ensemble_editable_trading_settings_v1"

FieldGroup = Literal["tradingSettings", "targetOrder", "defaultSettings"]
FieldKind = Literal["number", "integer", "boolean", "time", "choice"]
FieldBinding = Literal["live", "backtest", "both"]


GROUP_LABELS: dict[str, str] = {
    "tradingSettings": "Trading Settings",
    "targetOrder": "Target Order",
    "defaultSettings": "Default Settings",
}

GROUP_DESCRIPTIONS: dict[str, str] = {
    "tradingSettings": "Capital, exposure and the session window the algorithm may open trades in.",
    "targetOrder": "The geometry of the order itself: stop, target, trailing management and execution.",
    "defaultSettings": "The decision thresholds the vote has to clear before an order is proposed.",
}


@dataclass(frozen=True)
class EditableTradingSettingField:
    """One operator-editable parameter.

    `key` is the override payload key. A dotted key addresses a nested baseline value
    (only `familyWeights.*` today); the resolver accepts both the dotted form and the
    nested object, so a client may send either.
    """

    key: str
    label: str
    group: FieldGroup
    kind: FieldKind
    binding: FieldBinding
    bindsAt: str
    help: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()
    unit: str = ""

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(self.key.split("."))

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "kind": self.kind,
            "binding": self.binding,
            "bindsAt": self.bindsAt,
            "help": self.help,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "choices": list(self.choices),
            "unit": self.unit,
        }


@dataclass(frozen=True)
class InertTradingSettingParameter:
    """A resolved parameter no live read site consumes. Reported, never editable."""

    key: str
    label: str
    why: str


EDITABLE_ONE_MINUTE_FIELDS: tuple[EditableTradingSettingField, ...] = (
    # ------------------------------------------------------------------ capital
    EditableTradingSettingField(
        key="startingCapital",
        label="Total balance",
        group="tradingSettings",
        kind="number",
        binding="both",
        bindsAt="risk_budget.py:107 (equity base); local_paper_account.py (opening cash)",
        help="The one source of starting equity. Every allocation and risk cap below is a percentage of it.",
        minimum=1000.0,
        maximum=10_000_000.0,
        step=100.0,
        unit="$",
    ),
    EditableTradingSettingField(
        key="riskPerTradePercent",
        label="Risk per trade %",
        group="tradingSettings",
        kind="number",
        binding="both",
        bindsAt="risk_budget.py:107",
        help="Equity fraction put at risk on one trade. Risk dollars / stop distance is the first sizing cap.",
        minimum=0.01,
        maximum=100.0,
        step=0.01,
        unit="%",
    ),
    EditableTradingSettingField(
        key="orderAllocationPercent",
        label="Order limit %",
        group="tradingSettings",
        kind="number",
        binding="both",
        bindsAt="risk_budget.py:116",
        help="Largest notional a single order may take, as a percentage of equity.",
        minimum=0.1,
        maximum=100.0,
        step=0.1,
        unit="%",
    ),
    EditableTradingSettingField(
        key="dailyAllocationPercent",
        label="Daily max %",
        group="tradingSettings",
        kind="number",
        binding="both",
        bindsAt="risk_budget.py:117",
        help="Notional the day may deploy in total. Must be at least the order limit.",
        minimum=0.1,
        maximum=100.0,
        step=0.1,
        unit="%",
    ),
    EditableTradingSettingField(
        key="maximumPositionPercent",
        label="Max position %",
        group="tradingSettings",
        kind="number",
        binding="live",
        bindsAt="risk_budget.py:195; paper_execution.py:3133",
        help="Ceiling on open position notional as a percentage of equity.",
        minimum=0.1,
        maximum=100.0,
        step=0.1,
        unit="%",
    ),
    EditableTradingSettingField(
        key="maxShareQuantity",
        label="Max shares",
        group="tradingSettings",
        kind="integer",
        binding="live",
        bindsAt="risk_budget.py:206; service.py:1591",
        help="Hard share ceiling applied after every percentage cap. 0 removes the ceiling.",
        minimum=0,
        maximum=1_000_000,
        step=1,
        unit="shares",
    ),
    EditableTradingSettingField(
        key="maxDailyLossPercent",
        label="Max daily loss %",
        group="tradingSettings",
        kind="number",
        binding="both",
        bindsAt="service.py:1549 (HardRiskLimits.maxDailyLossPercent)",
        help="The day stops when realised loss reaches this fraction of equity. With no trade-count cap this is the main bound on a day.",
        minimum=0.1,
        maximum=100.0,
        step=0.1,
        unit="%",
    ),
    EditableTradingSettingField(
        key="maxTradesPerDay",
        label="Max trades/day",
        group="tradingSettings",
        kind="integer",
        binding="both",
        bindsAt="service.py:_local_gate_engine (maximum_trades_per_day)",
        help="0 means no fixed cap: the day is bounded by the loss and exposure limits instead. A positive value is clamped to what the daily allocation can fund.",
        minimum=0,
        maximum=50,
        step=1,
    ),
    EditableTradingSettingField(
        key="sessionStart",
        label="Session start",
        group="tradingSettings",
        kind="time",
        binding="both",
        bindsAt="finalized_bar_producer.py:1188 (entry window)",
        help="No entry is proposed before this time.",
    ),
    EditableTradingSettingField(
        key="newTradesUntil",
        label="New trades until",
        group="tradingSettings",
        kind="time",
        binding="both",
        bindsAt="finalized_bar_producer.py:1189 (entry window)",
        help="Last time a new entry may be opened. Exits still run after it.",
    ),
    EditableTradingSettingField(
        key="forceClose",
        label="Force close",
        group="tradingSettings",
        kind="time",
        binding="backtest",
        bindsAt="main.py:7907 (replay end-of-day flatten)",
        help="End-of-day flatten time for replay. The live account flattens off the market clock, not this value.",
    ),
    # ------------------------------------------------------------- order geometry
    EditableTradingSettingField(
        key="stopAtrMultiplier",
        label="ATR stop multiplier",
        group="targetOrder",
        kind="number",
        binding="live",
        bindsAt="service.py:_exit_geometry (stopPolicy.atrMultiplier)",
        help="Stop distance is this many session ATRs. 0 disables ATR scaling and falls back to the fixed dollar distance.",
        minimum=0.0,
        maximum=10.0,
        step=0.1,
        unit="x ATR",
    ),
    EditableTradingSettingField(
        key="fixedStopDistanceDollars",
        label="Stop $/share",
        group="targetOrder",
        kind="number",
        binding="both",
        bindsAt="service.py:_exit_geometry (fallback when no ATR exists)",
        help="Stop distance used when the session has no ATR yet.",
        minimum=0.0,
        maximum=100.0,
        step=0.01,
        unit="$",
    ),
    EditableTradingSettingField(
        key="minimumStopDistanceDollars",
        label="Min stop distance",
        group="targetOrder",
        kind="number",
        binding="live",
        bindsAt="service.py:_exit_geometry (stop distance floor)",
        help="Floor under the resolved stop distance, whichever source produced it.",
        minimum=0.0,
        maximum=100.0,
        step=0.01,
        unit="$",
    ),
    EditableTradingSettingField(
        key="takeProfitR",
        label="Target R",
        group="targetOrder",
        kind="number",
        binding="both",
        bindsAt="service.py:_exit_geometry (targetPolicy.takeProfitR)",
        help="Profit target as a multiple of the initial stop distance.",
        minimum=0.1,
        maximum=20.0,
        step=0.1,
        unit="R",
    ),
    EditableTradingSettingField(
        key="minimumTakeProfitR",
        label="Minimum target R",
        group="targetOrder",
        kind="number",
        binding="live",
        bindsAt="service.py:_exit_geometry (structural target floor)",
        help="A structural level closer than this is not allowed to replace the R target. The trade must still offer this much.",
        minimum=0.1,
        maximum=20.0,
        step=0.1,
        unit="R",
    ),
    EditableTradingSettingField(
        key="structuralTargets",
        label="Structural targets",
        group="targetOrder",
        kind="boolean",
        binding="live",
        bindsAt="service.py:_exit_geometry (structural level substitution)",
        help="Let a VWAP, opening-range, prior-day or premarket level inside the R target replace it, because price tends to stall there.",
    ),
    EditableTradingSettingField(
        key="breakevenTriggerR",
        label="Breakeven trigger R",
        group="targetOrder",
        kind="number",
        binding="live",
        bindsAt="service.py:_exit_geometry (features.breakevenTriggerR)",
        help="Move the stop to entry once the trade is this many initial-stop distances in profit. 0 disables it.",
        minimum=0.0,
        maximum=10.0,
        step=0.1,
        unit="R",
    ),
    EditableTradingSettingField(
        key="trailingStopR",
        label="Trailing stop R",
        group="targetOrder",
        kind="number",
        binding="live",
        bindsAt="service.py:_exit_geometry (features.trailingStopDistance)",
        help="Trail distance after breakeven, in initial-stop distances.",
        minimum=0.0,
        maximum=10.0,
        step=0.1,
        unit="R",
    ),
    EditableTradingSettingField(
        key="trailingStopEnabled",
        label="Trailing enabled",
        group="targetOrder",
        kind="boolean",
        binding="live",
        bindsAt="service.py:_exit_geometry (stopPolicy.trailingEnabled)",
        help="Off leaves the initial stop in place for the life of the trade.",
    ),
    EditableTradingSettingField(
        key="maximumHoldingMinutes",
        label="Max holding minutes",
        group="targetOrder",
        kind="integer",
        binding="live",
        bindsAt="local_paper_account.py:67 (holding-time exit)",
        help="A position still open after this many minutes is closed at the market.",
        minimum=1,
        maximum=390,
        step=1,
        unit="min",
    ),
    EditableTradingSettingField(
        key="limitOrderOffsetBps",
        label="Limit offset",
        group="targetOrder",
        kind="number",
        binding="live",
        bindsAt="service.py:1512 (order plan limitOffsetBps)",
        help="How far through the entry price the limit is placed. 0 prices at the entry.",
        minimum=0.0,
        maximum=100.0,
        step=0.1,
        unit="bps",
    ),
    EditableTradingSettingField(
        key="slippagePerShare",
        label="Slippage/share",
        group="targetOrder",
        kind="number",
        binding="both",
        bindsAt="execution_economics.py (expected cost per share)",
        help="Adverse slippage priced into the entry and exit when the net edge is checked.",
        minimum=0.0,
        maximum=10.0,
        step=0.01,
        unit="$",
    ),
    EditableTradingSettingField(
        key="maxSlippagePerShare",
        label="Max slippage/share",
        group="targetOrder",
        kind="number",
        binding="live",
        bindsAt="execution_economics.py:103",
        help="Ceiling on the slippage reserve. A candidate costing more than this is refused.",
        minimum=0.0,
        maximum=10.0,
        step=0.01,
        unit="$",
    ),
    EditableTradingSettingField(
        key="stopLossPercent",
        label="Stop %",
        group="targetOrder",
        kind="number",
        binding="backtest",
        bindsAt="main.py replay stop; recorded on the live policy at service.py:1532",
        help="Percentage stop used by the replay engine. The live pipeline sizes its stop from ATR, so this does not move a live stop.",
        minimum=0.01,
        maximum=20.0,
        step=0.01,
        unit="%",
    ),
    # ------------------------------------------------------- decision thresholds
    EditableTradingSettingField(
        key="minVoteEdge",
        label="Minimum vote edge",
        group="defaultSettings",
        kind="number",
        binding="live",
        bindsAt="service.py:_family_engine_for_settings (FamilyAwareEnsembleConfig.minimumFinalScore)",
        help="Weighted score the winning side must reach before a candidate is built.",
        minimum=0.0,
        maximum=1.0,
        step=0.01,
    ),
    EditableTradingSettingField(
        key="minEligibleDirectionalVotes",
        label="Min eligible strategies",
        group="defaultSettings",
        kind="integer",
        binding="live",
        bindsAt="service.py:_family_engine_for_settings (minimumEligibleDirectionalStrategies)",
        help="How many directional strategies must be data-ready and eligible before the vote counts at all.",
        minimum=1,
        maximum=12,
        step=1,
    ),
    EditableTradingSettingField(
        key="minimumFamiliesForTrade",
        label="Min independent families",
        group="defaultSettings",
        kind="integer",
        binding="live",
        bindsAt="service.py:_local_gate_engine and _family_engine_for_settings",
        help="Independent strategy families that must agree. At 2, one family alone can never trade: both reversal strategies agreeing is still one family.",
        minimum=1,
        maximum=5,
        step=1,
    ),
    EditableTradingSettingField(
        key="familyWeights.trend",
        label="Trend family weight",
        group="defaultSettings",
        kind="number",
        binding="live",
        bindsAt="service.py:_family_weights",
        help="Weight this family carries in the aggregate. 0 mutes it.",
        minimum=0.0,
        maximum=5.0,
        step=0.05,
    ),
    EditableTradingSettingField(
        key="familyWeights.breakout",
        label="Breakout family weight",
        group="defaultSettings",
        kind="number",
        binding="live",
        bindsAt="service.py:_family_weights",
        help="Weight this family carries in the aggregate. 0 mutes it.",
        minimum=0.0,
        maximum=5.0,
        step=0.05,
    ),
    EditableTradingSettingField(
        key="familyWeights.reversal",
        label="Reversal family weight",
        group="defaultSettings",
        kind="number",
        binding="live",
        bindsAt="service.py:_family_weights",
        help="Weight this family carries in the aggregate. 0 mutes it.",
        minimum=0.0,
        maximum=5.0,
        step=0.05,
    ),
    EditableTradingSettingField(
        key="familyWeights.mean_reversion",
        label="Mean reversion family weight",
        group="defaultSettings",
        kind="number",
        binding="live",
        bindsAt="service.py:_family_weights",
        help="Weight this family carries in the aggregate. 0 mutes it.",
        minimum=0.0,
        maximum=5.0,
        step=0.05,
    ),
    EditableTradingSettingField(
        key="familyWeights.gap_session",
        label="Gap session family weight",
        group="defaultSettings",
        kind="number",
        binding="live",
        bindsAt="service.py:_family_weights",
        help="Weight this family carries in the aggregate. 0 mutes it.",
        minimum=0.0,
        maximum=5.0,
        step=0.05,
    ),
    EditableTradingSettingField(
        key="reliabilityWeightingMode",
        label="Reliability weighting",
        group="defaultSettings",
        kind="choice",
        binding="live",
        bindsAt="service.py:_reliability_mode",
        help="Whether recorded per-strategy accuracy re-weights the vote (active), is only observed (shadow), or falls back to neutral.",
        choices=("shadow", "active", "fallback"),
    ),
    EditableTradingSettingField(
        key="reliabilitySampleWindow",
        label="Reliability window",
        group="defaultSettings",
        kind="choice",
        binding="live",
        bindsAt="service.py:_reliability_sample_window",
        help="How many recent trades a reliability estimate is drawn from.",
        choices=("rolling_20_trades", "rolling_60_trades", "rolling_120_trades"),
    ),
    EditableTradingSettingField(
        key="minimumNetEdgeR",
        label="Minimum net edge R",
        group="defaultSettings",
        kind="number",
        binding="live",
        bindsAt="execution_economics.py:102",
        help="Edge the trade must still show after fees and slippage, in R. 0 requires only that it is positive.",
        minimum=0.0,
        maximum=10.0,
        step=0.01,
        unit="R",
    ),
    EditableTradingSettingField(
        key="minimumEdgeToCostRatio",
        label="Min edge/cost ratio",
        group="defaultSettings",
        kind="number",
        binding="live",
        bindsAt="execution_economics.py:146; gates.py:291",
        help="Expected edge divided by expected cost. Below this the candidate is refused as not worth its expenses.",
        minimum=0.0,
        maximum=10.0,
        step=0.05,
    ),
    EditableTradingSettingField(
        key="maximumSpreadBps",
        label="Max spread",
        group="defaultSettings",
        kind="number",
        binding="live",
        bindsAt="execution_economics.py:147; gates.py:251",
        help="Widest quoted spread, in basis points, the algorithm will enter across.",
        minimum=0.0,
        maximum=500.0,
        step=0.5,
        unit="bps",
    ),
    EditableTradingSettingField(
        key="maximumSpreadDollars",
        label="Max spread $",
        group="defaultSettings",
        kind="number",
        binding="live",
        bindsAt="gates.py:257",
        help="Same limit expressed in dollars. Whichever binds first wins.",
        minimum=0.0,
        maximum=10.0,
        step=0.01,
        unit="$",
    ),
    EditableTradingSettingField(
        key="commandDeadlineSeconds",
        label="Decision deadline",
        group="defaultSettings",
        kind="integer",
        binding="live",
        bindsAt="service.py:_decision_deadline_expired",
        help="A decision older than this is dropped rather than acted on, so a backed-up queue cannot trade a stale bar.",
        minimum=1,
        maximum=600,
        step=1,
        unit="s",
    ),
    EditableTradingSettingField(
        key="minimumRiskMultiplier",
        label="Min risk multiplier",
        group="defaultSettings",
        kind="number",
        binding="live",
        bindsAt="service.py:1557 (DynamicPolicyBounds.minimumRiskMultiplier)",
        help="Floor under the dynamic risk multiplier, so an overlay cannot size a trade below this fraction of baseline risk.",
        minimum=0.0,
        maximum=1.0,
        step=0.05,
    ),
)


INERT_ONE_MINUTE_PARAMETERS: tuple[InertTradingSettingParameter, ...] = (
    InertTradingSettingParameter(
        key="holdBand",
        label="Hold band",
        why="No aggregation read site consumes it; the winning side is decided by minVoteEdge alone.",
    ),
    InertTradingSettingParameter(
        key="minWinningVotes",
        label="Minimum winning votes",
        why="No read site anywhere in the backend. The family minimum is what constrains agreement.",
    ),
    InertTradingSettingParameter(
        key="maxContextBoost",
        label="Max context boost",
        why="Context bounds are resolved but never read; the context engine applies its own per-signal cap.",
    ),
    InertTradingSettingParameter(
        key="maxContextPenalty",
        label="Max context penalty",
        why="Context bounds are resolved but never read; the context engine applies its own per-signal cap.",
    ),
    InertTradingSettingParameter(
        key="maxConcurrentPositions",
        label="Max concurrent positions",
        why="No gate reads it. The exposure caps bound a second position, and an opposite candidate is the reversal exit the paper account nets.",
    ),
    InertTradingSettingParameter(
        key="warmupBars",
        label="Warm-up bars",
        why="Snapshot readiness has its own history requirement; this value reaches no check.",
    ),
    InertTradingSettingParameter(
        key="entryConfirmationBars",
        label="Entry confirmation bars",
        why="Each strategy confirms its own entry; this value reaches no check.",
    ),
    InertTradingSettingParameter(
        key="maxPrimaryFeedAgeSeconds",
        label="Max primary feed age",
        why="The producer's freshness checks use their own constants (5 s quote, 10 s trade).",
    ),
    InertTradingSettingParameter(
        key="maxAuxiliaryFeedAgeSeconds",
        label="Max auxiliary feed age",
        why="The producer's freshness checks use their own constant (90 s auxiliary).",
    ),
    InertTradingSettingParameter(
        key="maxDecisionLatencyMs",
        label="Max decision latency",
        why="Nothing measures in-process decision duration against it. Only commandDeadlineSeconds reaches a gate.",
    ),
    InertTradingSettingParameter(
        key="maxQueueLatencyMs",
        label="Max queue latency",
        why="The worker does not measure its queue delay against it.",
    ),
    InertTradingSettingParameter(
        key="cancelUnfilledAfterSeconds",
        label="Cancel unfilled after",
        why="Resolved onto the profile as cancelReplaceTimeoutSeconds; no execution path reads it.",
    ),
    InertTradingSettingParameter(
        key="maxReplacementAttempts",
        label="Max replacement attempts",
        why="No execution path replaces an unfilled order, so the attempt budget is never consulted.",
    ),
    InertTradingSettingParameter(
        key="cooldownSeconds",
        label="Cooldown seconds",
        why="Resolved onto the profile; no gate enforces a wait between entries.",
    ),
    InertTradingSettingParameter(
        key="allowedEntryHours",
        label="Allowed entry hours",
        why="Entries are bounded by sessionStart and newTradesUntil. Kept as the full session so it cannot silently narrow entries.",
    ),
)


_FIELDS_BY_KEY: dict[str, EditableTradingSettingField] = {item.key: item for item in EDITABLE_ONE_MINUTE_FIELDS}

EDITABLE_FIELD_KEYS: frozenset[str] = frozenset(_FIELDS_BY_KEY)

# Keys an older dashboard may still send. Sizing never read them and the baseline no
# longer carries them, so they are dropped rather than rejected: refusing them would
# make every save from a stale client fail.
LEGACY_IGNORED_OVERRIDE_KEYS: frozenset[str] = frozenset(
    {
        "riskBudgetPercentOfOrder",
        "positionSizingMode",
        "useDefaultSizingSettings",
        "pyramidingEnabled",
        "allowPyramiding",
        "execution",
        "positionSizing",
        "minimumBuyScore",
        "minimumSignalEdge",
    }
)


def editable_field(key: str) -> EditableTradingSettingField | None:
    return _FIELDS_BY_KEY.get(key)


def editable_fields_for_group(group: str) -> tuple[EditableTradingSettingField, ...]:
    return tuple(item for item in EDITABLE_ONE_MINUTE_FIELDS if item.group == group)


def editable_field_groups() -> tuple[str, ...]:
    return ("tradingSettings", "targetOrder", "defaultSettings")
