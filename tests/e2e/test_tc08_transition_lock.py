"""TC-08: Auto-mode v2 transition lock - rejection matrix (AM2-150).

Matrix-driven executable specification for the auto-mode v2 transition
lock. The static matrix (``fixtures/automode_v2/transition_lock_matrix.json``)
encodes the full rejection contract; this module asserts each row through
a pure reference decision function exercised via the shared
``automode_matrix_loader`` fixture (architecture doc 44bb351e).

Test cases (test design doc 15726212):
- TC08-C1: Fixture integrity and schema (vocabulary, ids, payload shape)
- TC08-C2: Legal-path acceptance (F1, tc08-001..005)
- TC08-C3: Illegal-transition rejection (F2, tc08-006..010)
- TC08-C4: Actor-gating rejection (F3, tc08-011..015; reason provided)
- TC08-C5: Reason-required rejection (F4, tc08-016..018; user actor)
- TC08-C6: Exempt recovery not over-blocked (F4R, tc08-019..020)
- TC08-C7: Rejection payload completeness (all reject rows)
- TC08-C8: Multi-exit payload detail (tc08-021)
- TC08-C9: Marker and collection integration

When the real auto-mode v2 implementation lands, the implementing story
swaps the reference decision function; the matrix and this design remain
the contract.
"""

from __future__ import annotations

import pytest

MATRIX_FILE = "transition_lock_matrix.json"
STATUS_VOCABULARY = frozenset({"idle", "standard", "supervised", "autonomous", "error"})
ERROR_CODE = "transition_locked"

# -- Reference transition-lock decision function (spec carrier) ---------------


def _transition_options() -> dict[str, dict]:
    """Declarative table of allowed exits per status.

    Maps ``from_status`` -> ``{to_status: rule}``. Each rule carries the
    actor gating (``user``-only transitions are agent-gated) and whether
    a reason is required, mirroring the rejection vocabulary of the AM2
    transition lock.
    """
    return {
        "idle": {
            "standard": {"actor": "user", "reason_required": True},
            "autonomous": {"actor": "user", "reason_required": True},
        },
        "standard": {
            "supervised": {"actor": "user", "reason_required": True},
            "autonomous": {"actor": "user", "reason_required": True},
            "idle": {"actor": "user", "reason_required": False},
        },
        "supervised": {
            "autonomous": {"actor": "user", "reason_required": True},
            "standard": {"actor": "user", "reason_required": False},
            "idle": {"actor": "user", "reason_required": False},
        },
        "autonomous": {
            "standard": {"actor": "user", "reason_required": False},
            "supervised": {"actor": "user", "reason_required": False},
            "idle": {"actor": "user", "reason_required": False},
        },
        "error": {
            "idle": {"actor": "user", "reason_required": False},
        },
    }


def _evaluate_transition_lock(
    from_status: str,
    to_status: str,
    actor: str,
    reason_provided: bool,
) -> dict:
    """Reference decision function for the transition lock.

    Returns the same result envelope the auto-mode v2 implementation is
    expected to produce: ``{"outcome": "accept"}`` or
    ``{"outcome": "reject", "error_code": ..., "rejection": {...}}`` with
    the complete recoverability payload.
    """
    options = _transition_options()[from_status]
    rule = options.get(to_status)
    if rule is None:
        return _reject_payload(from_status, options)
    if rule["actor"] == "user" and actor != "user":
        return _reject_payload(from_status, options)
    if rule["reason_required"] and not reason_provided:
        return _reject_payload(from_status, options)
    return {"outcome": "accept"}


def _reject_payload(status: str, options: dict[str, dict]) -> dict:
    allowed = [
        {
            "to_status": to_status,
            "label": _LABELS[(status, to_status)],
            "reason_required": rule["reason_required"],
            "hint": _HINTS[(status, to_status)],
        }
        for to_status, rule in options.items()
    ]
    return {
        "outcome": "reject",
        "error_code": ERROR_CODE,
        "rejection": {
            "current_status": status,
            "allowed_next_transitions": allowed,
        },
    }


_LABELS = {
    ("idle", "standard"): "Escalate to standard mode",
    ("idle", "autonomous"): "Escalate directly to autonomous mode",
    ("standard", "supervised"): "Escalate to supervised mode",
    ("standard", "autonomous"): "Escalate to autonomous mode",
    ("standard", "idle"): "Deactivate auto-mode",
    ("supervised", "autonomous"): "Escalate to autonomous mode",
    ("supervised", "standard"): "De-escalate to standard mode",
    ("supervised", "idle"): "Abort to idle",
    ("autonomous", "standard"): "De-escalate to standard mode",
    ("autonomous", "supervised"): "De-escalate to supervised mode",
    ("autonomous", "idle"): "Emergency stop to idle",
    ("error", "idle"): "Reset to idle",
}

_HINTS = {
    ("idle", "standard"): "Provide a reason to escalate from idle to standard mode.",
    ("idle", "autonomous"): (
        "Provide a reason to escalate from idle to autonomous mode."
    ),
    ("standard", "supervised"): (
        "Provide a reason to escalate from standard to supervised mode."
    ),
    ("standard", "autonomous"): (
        "Provide a reason to escalate from standard to autonomous mode."
    ),
    ("standard", "idle"): "No reason required to return to idle.",
    ("supervised", "autonomous"): (
        "Provide a reason to escalate from supervised to autonomous mode."
    ),
    ("supervised", "standard"): "Recovery de-escalation is reason-exempt.",
    ("supervised", "idle"): "No reason required to abort to idle.",
    ("autonomous", "standard"): "Recovery de-escalation is reason-exempt.",
    ("autonomous", "supervised"): "Recovery de-escalation is reason-exempt.",
    ("autonomous", "idle"): "No reason required for emergency stop.",
    ("error", "idle"): "Recovery from error is reason-exempt.",
}


def _load_matrix_rows(automode_matrix_loader) -> list[dict]:
    document = automode_matrix_loader(MATRIX_FILE)
    return document["matrix"]


def _assert_rejection_contract(row: dict, result: dict) -> None:
    """Assert the full rejection contract for one matrix row (C7 core)."""
    rejection = result["rejection"]
    assert rejection["current_status"] == row["from_status"]
    allowed = rejection["allowed_next_transitions"]
    assert allowed, "allowed_next_transitions must be non-empty"
    for entry in allowed:
        assert entry["to_status"] in STATUS_VOCABULARY
        assert isinstance(entry["label"], str) and entry["label"]
        assert isinstance(entry["reason_required"], bool)
        assert isinstance(entry["hint"], str) and entry["hint"]
    if row["family"] == "F2-illegal-transition":
        assert row["to_status"] not in {entry["to_status"] for entry in allowed}


def _assert_row(row: dict) -> None:
    """Evaluate one matrix row through the reference decision function."""
    result = _evaluate_transition_lock(
        row["from_status"],
        row["to_status"],
        row["actor"],
        row["reason_provided"],
    )
    if row["expected"] == "accept":
        assert result["outcome"] == "accept", (
            f"{row['case']}: expected accept but got {result}"
        )
    else:
        assert result["outcome"] == "reject", (
            f"{row['case']}: expected reject but got {result}"
        )
        assert result["error_code"] == row["expected_error_code"]
        _assert_rejection_contract(row, result)


# -- Fixtures -----------------------------------------------------------------


@pytest.fixture(scope="module")
def matrix_rows(automode_matrix_loader) -> list[dict]:
    """All matrix rows (loaded once via the shared conftest loader)."""
    return _load_matrix_rows(automode_matrix_loader)


@pytest.fixture(scope="module")
def rows_by_id(matrix_rows) -> dict[str, dict]:
    """Matrix rows keyed by case id."""
    return {row["case"]: row for row in matrix_rows}


@pytest.fixture(scope="module")
def reject_rows(matrix_rows) -> list[dict]:
    return [row for row in matrix_rows if row["expected"] == "reject"]


# -- C1: Fixture integrity and schema ------------------------------------------


class TestTC08TransitionLock:
    """TC-08: transition-lock rejection matrix (AM2-150)."""

    # -- C1: fixture integrity and schema ---------------------------------

    def test_fixture_integrity_and_schema(self, automode_matrix_loader) -> None:
        document = automode_matrix_loader(MATRIX_FILE)
        assert document["schema_version"] == 1
        assert document["suite"] == "auto-mode-v2-e2e"
        assert document["case_id"] == "TC-08"
        rows = document["matrix"]
        assert rows, "matrix must not be empty"
        case_ids = [row["case"] for row in rows]
        assert len(case_ids) == len(set(case_ids)), "case ids must be unique"
        for row in rows:
            assert row["from_status"] in STATUS_VOCABULARY, row["case"]
            assert row["to_status"] in STATUS_VOCABULARY, row["case"]
            assert row["actor"] in ("agent", "user"), row["case"]
            assert isinstance(row["reason_provided"], bool), row["case"]
            if row["expected"] == "reject":
                rejection = row["expected_rejection"]
                assert rejection["current_status"] == row["from_status"]
                assert rejection["allowed_next_transitions"]
            else:
                assert "expected_rejection" not in row

    # -- C2: legal-path acceptance ----------------------------------------

    @pytest.mark.parametrize("case_id", ["tc08-001", "tc08-002", "tc08-003",
                                         "tc08-004", "tc08-005"])
    def test_legal_path_acceptance(self, rows_by_id, case_id) -> None:
        row = rows_by_id[case_id]
        result = _evaluate_transition_lock(
            row["from_status"], row["to_status"], row["actor"],
            row["reason_provided"],
        )
        assert result["outcome"] == "accept"
        assert "error_code" not in result

    # -- C3: illegal-transition rejection ---------------------------------

    @pytest.mark.parametrize("case_id", ["tc08-006", "tc08-007", "tc08-008",
                                         "tc08-009", "tc08-010"])
    def test_illegal_transition_rejection(self, rows_by_id, case_id) -> None:
        row = rows_by_id[case_id]
        result = _evaluate_transition_lock(
            row["from_status"], row["to_status"], row["actor"],
            row["reason_provided"],
        )
        assert result["outcome"] == "reject"
        assert result["error_code"] == row["expected_error_code"]
        assert "rejection" in result

    # -- C4: actor-gating rejection ---------------------------------------

    @pytest.mark.parametrize("case_id", ["tc08-011", "tc08-012", "tc08-013",
                                         "tc08-014", "tc08-015"])
    def test_actor_gating_rejection(self, rows_by_id, case_id) -> None:
        row = rows_by_id[case_id]
        # Actor is the sole rejection variable: reason was provided.
        assert row["actor"] == "agent"
        assert row["reason_provided"] is True
        result = _evaluate_transition_lock(
            row["from_status"], row["to_status"], row["actor"],
            row["reason_provided"],
        )
        assert result["outcome"] == "reject"
        # Control: the same request by the user actor must NOT be
        # actor-rejected (actor is the isolation variable).
        control = _evaluate_transition_lock(
            row["from_status"], row["to_status"], "user", True,
        )
        assert control["outcome"] == "accept", (
            f"{case_id}: rejection must be attributable to actor, not the "
            "transition itself"
        )

    # -- C5: reason-required rejection ------------------------------------

    @pytest.mark.parametrize("case_id", ["tc08-016", "tc08-017", "tc08-018"])
    def test_reason_required_rejection(self, rows_by_id, case_id) -> None:
        row = rows_by_id[case_id]
        assert row["actor"] == "user"
        assert row["reason_provided"] is False
        result = _evaluate_transition_lock(
            row["from_status"], row["to_status"], row["actor"],
            row["reason_provided"],
        )
        assert result["outcome"] == "reject"
        assert result["error_code"] == row["expected_error_code"]

    # -- C6: exempt recovery not over-blocked -----------------------------

    @pytest.mark.parametrize("case_id", ["tc08-019", "tc08-020"])
    def test_exempt_recovery_not_over_blocked(self, rows_by_id, case_id) -> None:
        row = rows_by_id[case_id]
        assert row["actor"] == "user"
        assert row["reason_provided"] is False
        result = _evaluate_transition_lock(
            row["from_status"], row["to_status"], row["actor"],
            row["reason_provided"],
        )
        assert result["outcome"] == "accept"

    # -- C7: rejection payload completeness -------------------------------

    def test_rejection_payload_completeness(self, reject_rows) -> None:
        assert reject_rows, "matrix must contain reject rows"
        for row in reject_rows:
            result = _evaluate_transition_lock(
                row["from_status"], row["to_status"], row["actor"],
                row["reason_provided"],
            )
            assert result["outcome"] == "reject", row["case"]
            _assert_rejection_contract(row, result)

    # -- C8: multi-exit payload detail ------------------------------------

    def test_multi_exit_payload_detail(self, rows_by_id) -> None:
        row = rows_by_id["tc08-021"]
        assert row["from_status"] == "autonomous"
        result = _evaluate_transition_lock(
            row["from_status"], row["to_status"], row["actor"],
            row["reason_provided"],
        )
        assert result["outcome"] == "reject"
        allowed = result["rejection"]["allowed_next_transitions"]
        assert len(allowed) >= 2
        for entry in allowed:
            assert entry["to_status"] in STATUS_VOCABULARY
            assert entry["label"]
            assert isinstance(entry["reason_required"], bool)
            assert entry["hint"]

    # -- C9: marker and collection integration ----------------------------

    @pytest.mark.e2e_automode
    def test_marker_and_collection_integration(self, matrix_rows) -> None:
        """Module-level: runs under the e2e_automode marker, collected by
        default (no env-var gate), and every row is covered by a test."""
        assert matrix_rows, "matrix must contain rows"


def test_tc08_all_rows_evaluated(automode_matrix_loader) -> None:
    """Every matrix row passes the reference decision function (TC-08)."""
    for row in _load_matrix_rows(automode_matrix_loader):
        _assert_row(row)
