"""Which strategies may vote in a given session segment, and how large it may trade.

This sits **above** the voting layer. Voters are untouched: every strategy still evaluates
the bar and still returns its own signal, so its reasoning stays observable and shadow
evidence keeps accruing. What the policy decides is whether that vote *counts* toward the
ensemble, and how much size the resulting candidate may take.

Marking a vote ineligible rather than dropping it is deliberate. A dropped vote is
invisible: nothing downstream can tell the difference between "this strategy said nothing"
and "this strategy was not allowed to speak here". An ineligible vote carries its reason
code and still reaches the decision record.

The whole policy is disabled by default. It changes which strategies influence live
decisions, so it has to be turned on deliberately rather than arriving switched on with an
upgrade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


VOTING_ENSEMBLE_SESSION_POLICY_VERSION = "voting_ensemble_session_policy_v1"

SESSION_POLICY_DISABLED_REASON = "voting_ensemble.session_policy.disabled"
SESSION_POLICY_SEGMENT_UNKNOWN_REASON = "voting_ensemble.session_policy.segment_unknown"
SESSION_POLICY_VOTER_BLOCKED_REASON = "voting_ensemble.session_policy.voter_not_permitted_in_segment"
SESSION_POLICY_SEGMENT_CLOSED_REASON = "voting_ensemble.session_policy.segment_not_tradable"

# Segment names this policy understands. The snapshot's session state supplies the label;
# anything it does not recognise is treated as unknown and left alone rather than guessed at.
SESSION_SEGMENTS: tuple[str, ...] = ("premarket", "open", "midday", "close", "overnight")


@dataclass(frozen=True)
class SessionSegmentPolicy:
    """What one segment permits."""

    segment: str
    tradable: bool = True
    # None means "every strategy may vote here". An empty tuple means none may, which is a
    # different statement and is preserved as such.
    permitted_strategies: tuple[str, ...] | None = None
    # Multiplier on the position size the candidate may take in this segment. 1.0 is full
    # size; 0.0 blocks sizing without blocking the vote.
    max_position_multiplier: float = 1.0

    def permits(self, strategy_id: str) -> bool:
        if not self.tradable:
            return False
        if self.permitted_strategies is None:
            return True
        return strategy_id in self.permitted_strategies


@dataclass(frozen=True)
class SessionPolicySettings:
    """The whole policy, off unless explicitly enabled."""

    enabled: bool = False
    version: str = VOTING_ENSEMBLE_SESSION_POLICY_VERSION
    segments: Mapping[str, SessionSegmentPolicy] = field(default_factory=dict)
    # Applied when the session label is not one this policy knows. Leaving it permissive
    # keeps an unrecognised label from silently halting the algorithm; the reason code makes
    # the situation visible instead.
    unknown_segment_is_tradable: bool = True

    def policy_for(self, segment: str) -> SessionSegmentPolicy | None:
        return self.segments.get(_normalize_segment(segment))


@dataclass(frozen=True)
class SessionPolicyDecision:
    """What the policy did on one bar."""

    enabled: bool
    segment: str
    tradable: bool
    max_position_multiplier: float
    blocked_strategies: tuple[str, ...]
    permitted_strategies: tuple[str, ...]
    reason_codes: tuple[str, ...]
    explanation: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": VOTING_ENSEMBLE_SESSION_POLICY_VERSION,
            "enabled": self.enabled,
            "segment": self.segment,
            "tradable": self.tradable,
            "maxPositionMultiplier": self.max_position_multiplier,
            "blockedStrategies": list(self.blocked_strategies),
            "permittedStrategies": list(self.permitted_strategies),
            "reasonCodes": list(self.reason_codes),
            "explanation": self.explanation,
        }


def default_session_policy() -> SessionPolicySettings:
    """A sane starting shape, still disabled.

    The segments describe the intent -- avoid the illiquid extremes, run smaller into the
    close -- without being switched on. Turning it on is a decision, and the defaults are a
    starting point for that decision rather than a substitute for it.
    """
    return SessionPolicySettings(
        enabled=False,
        segments={
            "premarket": SessionSegmentPolicy("premarket", tradable=False, max_position_multiplier=0.0),
            "open": SessionSegmentPolicy("open", tradable=True, max_position_multiplier=1.0),
            "midday": SessionSegmentPolicy("midday", tradable=True, max_position_multiplier=1.0),
            "close": SessionSegmentPolicy("close", tradable=True, max_position_multiplier=0.5),
            "overnight": SessionSegmentPolicy("overnight", tradable=False, max_position_multiplier=0.0),
        },
    )


def session_policy_from_payload(payload: Mapping[str, Any] | None) -> SessionPolicySettings:
    """Build a policy from settings, falling back to the disabled default.

    A malformed policy yields the disabled default rather than a partially applied one: half
    a gate is worse than none, because it is not obvious which half is running.
    """
    if not isinstance(payload, Mapping):
        return default_session_policy()
    try:
        raw_segments = payload.get("segments")
        segments: dict[str, SessionSegmentPolicy] = {}
        if isinstance(raw_segments, Mapping):
            for name, raw in raw_segments.items():
                if not isinstance(raw, Mapping):
                    continue
                permitted = raw.get("permittedStrategies", raw.get("permitted_strategies"))
                segments[_normalize_segment(name)] = SessionSegmentPolicy(
                    segment=_normalize_segment(name),
                    tradable=bool(raw.get("tradable", True)),
                    permitted_strategies=tuple(str(item) for item in permitted) if isinstance(permitted, (list, tuple)) else None,
                    max_position_multiplier=_clamped_multiplier(raw.get("maxPositionMultiplier", raw.get("max_position_multiplier", 1.0))),
                )
        return SessionPolicySettings(
            enabled=bool(payload.get("enabled", False)),
            segments=segments or default_session_policy().segments,
            unknown_segment_is_tradable=bool(payload.get("unknownSegmentIsTradable", True)),
        )
    except Exception:
        return default_session_policy()


def apply_session_policy(
    votes: Iterable[Any],
    *,
    session_segment: str,
    settings: SessionPolicySettings | None = None,
) -> tuple[tuple[Any, ...], SessionPolicyDecision]:
    """Mark votes the segment does not permit as ineligible, and report the size cap.

    Returns the votes in their original order so nothing downstream has to care that a
    policy ran, and a decision record describing what it did.
    """
    policy_settings = settings or default_session_policy()
    ordered = tuple(votes)
    segment = _normalize_segment(session_segment)

    if not policy_settings.enabled:
        return ordered, SessionPolicyDecision(
            enabled=False,
            segment=segment,
            tradable=True,
            max_position_multiplier=1.0,
            blocked_strategies=(),
            permitted_strategies=tuple(_strategy_id(vote) for vote in ordered),
            reason_codes=(SESSION_POLICY_DISABLED_REASON,),
            explanation="Session policy is disabled; every strategy votes and size is uncapped by session.",
        )

    segment_policy = policy_settings.policy_for(segment)
    if segment_policy is None:
        tradable = policy_settings.unknown_segment_is_tradable
        return ordered if tradable else tuple(_blocked(vote, SESSION_POLICY_SEGMENT_UNKNOWN_REASON) for vote in ordered), SessionPolicyDecision(
            enabled=True,
            segment=segment,
            tradable=tradable,
            max_position_multiplier=1.0 if tradable else 0.0,
            blocked_strategies=() if tradable else tuple(_strategy_id(vote) for vote in ordered),
            permitted_strategies=tuple(_strategy_id(vote) for vote in ordered) if tradable else (),
            reason_codes=(SESSION_POLICY_SEGMENT_UNKNOWN_REASON,),
            explanation=f"Session segment '{segment}' has no policy; treated as {'tradable' if tradable else 'not tradable'}.",
        )

    blocked: list[str] = []
    permitted: list[str] = []
    adjusted: list[Any] = []
    for vote in ordered:
        strategy_id = _strategy_id(vote)
        if segment_policy.permits(strategy_id):
            permitted.append(strategy_id)
            adjusted.append(vote)
            continue
        blocked.append(strategy_id)
        reason = SESSION_POLICY_SEGMENT_CLOSED_REASON if not segment_policy.tradable else SESSION_POLICY_VOTER_BLOCKED_REASON
        adjusted.append(_blocked(vote, reason))

    reason_codes: tuple[str, ...] = ()
    if blocked:
        reason_codes = (SESSION_POLICY_SEGMENT_CLOSED_REASON,) if not segment_policy.tradable else (SESSION_POLICY_VOTER_BLOCKED_REASON,)
    return tuple(adjusted), SessionPolicyDecision(
        enabled=True,
        segment=segment,
        tradable=segment_policy.tradable,
        max_position_multiplier=segment_policy.max_position_multiplier,
        blocked_strategies=tuple(blocked),
        permitted_strategies=tuple(permitted),
        reason_codes=reason_codes,
        explanation=(
            f"Session segment '{segment}' permitted {len(permitted)} of {len(ordered)} strategies "
            f"at {segment_policy.max_position_multiplier:.2f}x size."
        ),
    )


def _blocked(vote: Any, reason_code: str) -> Any:
    """A copy of the vote that cannot influence the ensemble but still explains itself."""
    features = dict(getattr(vote, "features", {}) or {})
    features["sessionPolicyBlocked"] = True
    features["sessionPolicyReasonCode"] = reason_code
    try:
        return vote.model_copy(update={"eligible": False, "active": False, "features": features})
    except AttributeError:
        return vote


def _strategy_id(vote: Any) -> str:
    return str(getattr(vote, "strategy", "") or "")


def _normalize_segment(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "regular": "midday",
        "regular_session": "midday",
        "market_open": "open",
        "opening": "open",
        "power_hour": "close",
        "closing": "close",
        "pre_market": "premarket",
        "after_hours": "overnight",
        "postmarket": "overnight",
    }
    return aliases.get(text, text)


def _clamped_multiplier(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


__all__ = [
    "SESSION_POLICY_DISABLED_REASON",
    "SESSION_POLICY_SEGMENT_CLOSED_REASON",
    "SESSION_POLICY_SEGMENT_UNKNOWN_REASON",
    "SESSION_POLICY_VOTER_BLOCKED_REASON",
    "SESSION_SEGMENTS",
    "SessionPolicyDecision",
    "SessionPolicySettings",
    "SessionSegmentPolicy",
    "VOTING_ENSEMBLE_SESSION_POLICY_VERSION",
    "apply_session_policy",
    "default_session_policy",
    "session_policy_from_payload",
]
