from __future__ import annotations

from datetime import UTC, datetime

from backend.app.risk.market_gates import evaluate_market_gates
from backend.app.risk.order_gates import evaluate_order_integrity_gates
from backend.app.risk.persistence import InMemoryGlobalRiskDecisionStore
from backend.app.risk.portfolio_gates import approved_quantity_cap, evaluate_portfolio_gates
from backend.app.risk.reservations import InMemoryRiskReservationStore
from backend.app.risk.settings import DEFAULT_GLOBAL_RISK_SETTINGS, GlobalRiskSettings
from backend.app.risk.types import AccountSnapshot, GateResult, GlobalGateDecision, GlobalOrderIntent, MarketSnapshot, PendingOrder, PortfolioSnapshot


GLOBAL_PORTFOLIO_RISK_MANAGER_VERSION = "global_portfolio_risk_manager_v1"


class GlobalPortfolioRiskManager:
    def __init__(
        self,
        *,
        settings: GlobalRiskSettings = DEFAULT_GLOBAL_RISK_SETTINGS,
        reservations: InMemoryRiskReservationStore | None = None,
        decisions: InMemoryGlobalRiskDecisionStore | None = None,
    ) -> None:
        self.settings = settings
        self.reservations = reservations or InMemoryRiskReservationStore()
        self.decisions = decisions or InMemoryGlobalRiskDecisionStore()

    def evaluate(
        self,
        *,
        intent: GlobalOrderIntent,
        account: AccountSnapshot,
        market: MarketSnapshot,
        portfolio: PortfolioSnapshot | None = None,
        evaluated_at: datetime | None = None,
        reserve: bool = False,
    ) -> GlobalGateDecision:
        evaluated_at = (evaluated_at or market.evaluatedAt or datetime.now(UTC)).astimezone(UTC)
        portfolio = portfolio or PortfolioSnapshot()
        account, portfolio = self._with_active_reservations(intent, account, portfolio)
        gates = [
            *self._system_and_broker_gates(intent, account, evaluated_at),
            *evaluate_market_gates(intent, market, self.settings, evaluated_at=evaluated_at),
            *evaluate_portfolio_gates(intent, account, portfolio, self.settings, evaluated_at=evaluated_at),
            *evaluate_order_integrity_gates(intent, portfolio, self.settings, evaluated_at=evaluated_at),
        ]
        failed = tuple(gate for gate in gates if gate.status == "fail" and (gate.blocksNewEntries or gate.blocksProtectiveExits))
        warnings = tuple(gate for gate in gates if gate.status == "warning")
        passed = tuple(gate for gate in gates if gate.status == "pass")

        if failed:
            decision = GlobalGateDecision(
                status="denied",
                approvedQuantity=0,
                approvedRiskDollars=0.0,
                passedGates=passed,
                failedGates=failed,
                warningGates=warnings,
                accountSnapshotId=account.accountSnapshotId,
                evaluatedAt=evaluated_at,
            )
            self.decisions.record(intent.decisionId, decision)
            return decision

        approved_quantity = min(intent.requestedQuantity, approved_quantity_cap(intent, account, portfolio, self.settings))
        approved_risk = _scaled_risk(intent, approved_quantity)
        status = "approved" if approved_quantity == intent.requestedQuantity else "resized" if approved_quantity > 0 else "denied"
        reservation_id = None
        if reserve and approved_quantity > 0:
            reservation = self.reservations.reserve(
                decision_id=intent.decisionId,
                algorithm_id=intent.algorithmId,
                symbol=intent.symbol,
                quantity=approved_quantity,
                buying_power=approved_quantity * intent.expectedEntryPrice,
                risk_dollars=approved_risk,
            )
            reservation_id = reservation.reservationId
        decision = GlobalGateDecision(
            status=status,
            approvedQuantity=approved_quantity,
            approvedRiskDollars=approved_risk,
            passedGates=passed,
            failedGates=() if status != "denied" else failed,
            warningGates=warnings,
            accountSnapshotId=account.accountSnapshotId,
            reservationId=reservation_id,
            evaluatedAt=evaluated_at,
        )
        self.decisions.record(intent.decisionId, decision)
        return decision

    def commit_reservation(self, reservation_id: str, *, broker_order_id: str | None = None) -> None:
        self.reservations.commit(reservation_id, broker_order_id=broker_order_id)

    def release_reservation(self, reservation_id: str) -> None:
        self.reservations.release(reservation_id)

    def _system_and_broker_gates(self, intent: GlobalOrderIntent, account: AccountSnapshot, evaluated_at: datetime) -> tuple[GateResult, ...]:
        new_entry = intent.is_new_entry

        def gate(gate_id: str, passed: bool, reason: str, *, warning: bool = False, blocks_exits: bool = False) -> GateResult:
            return GateResult(
                gateId=gate_id,
                gateName=gate_id.replace("_", " ").title(),
                status="pass" if passed else "warning" if warning else "fail",
                reason=reason,
                blocksNewEntries=not passed and new_entry and not warning,
                blocksProtectiveExits=not passed and blocks_exits,
                evaluatedAt=evaluated_at,
            )

        return (
            gate("manager_version", True, GLOBAL_PORTFOLIO_RISK_MANAGER_VERSION),
            gate("master_new_entry_switch", self.settings.masterNewEntryEnabled or not new_entry, "Master new-entry switch evaluated."),
            gate("normal_trading_enabled", self.settings.tradingEnabled, "Normal trading-off switch evaluated.", warning=not self.settings.tradingEnabled and not new_entry),
            gate("emergency_kill_switch", not self.settings.emergencyKillSwitch, "Emergency kill switch evaluated.", blocks_exits=True),
            gate("broker_api_connectivity", account.brokerConnected, "Broker API connectivity evaluated."),
            gate("broker_account_active_status", account.brokerAccountActive, "Broker account active status evaluated."),
            gate("trading_permission", account.tradingPermission, "Trading permission evaluated."),
            gate("clock_synchronization", account.clockSynchronized, "Clock synchronization evaluated."),
            gate("current_account_snapshot", account.accountSnapshotFresh, "Current account snapshot evaluated."),
            gate("local_broker_order_reconciliation", account.localBrokerOrdersReconciled, "Local/broker order reconciliation evaluated."),
            gate("local_broker_position_reconciliation", account.localBrokerPositionsReconciled, "Local/broker position reconciliation evaluated."),
            gate("unresolved_submission_failure", not account.unresolvedSubmissionFailure, "Unresolved submission failure evaluated."),
            gate("broker_rate_limit_protection", not account.brokerRateLimited, "Broker rate-limit protection evaluated."),
        )

    def _with_active_reservations(self, intent: GlobalOrderIntent, account: AccountSnapshot, portfolio: PortfolioSnapshot) -> tuple[AccountSnapshot, PortfolioSnapshot]:
        active = tuple(
            reservation
            for reservation in self.reservations.all()
            if reservation.status == "reserved" and reservation.decisionId != intent.decisionId
        )
        if not active:
            return account, portfolio

        reserved_buying_power = sum(reservation.reservedBuyingPower for reservation in active)
        adjusted_account = account.model_copy(update={"availableBuyingPower": max(0.0, account.availableBuyingPower - reserved_buying_power)})
        reservation_orders = tuple(
            PendingOrder(
                algorithmId=reservation.algorithmId,
                symbol=reservation.symbol,
                side=intent.side,
                quantity=reservation.quantity,
                notional=reservation.reservedBuyingPower,
                riskDollars=reservation.reservedRiskDollars,
                decisionId=reservation.decisionId,
                clientOrderId=None,
                intentKey=f"risk-reservation:{reservation.reservationId}",
                submittedAt=reservation.createdAt,
            )
            for reservation in active
        )
        adjusted_portfolio = portfolio.model_copy(update={"pendingOrders": (*portfolio.pendingOrders, *reservation_orders)})
        return adjusted_account, adjusted_portfolio


def _scaled_risk(intent: GlobalOrderIntent, approved_quantity: int) -> float:
    if intent.requestedQuantity <= 0:
        return 0.0
    return round(intent.requestedRiskDollars * approved_quantity / intent.requestedQuantity, 6)


__all__ = ["GLOBAL_PORTFOLIO_RISK_MANAGER_VERSION", "GlobalPortfolioRiskManager"]
