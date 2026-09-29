"""Building domain vocabulary: what each Antibody action does, and which tier it is.

Governor cannot tell from a tool name alone whether "h2o.shut_valve" reads or changes
the building; left to itself it guesses from words like "write" or "run". This table
is the domain adapter: it states each action's operation type for the Governor record
and its autonomy tier for the app's own approval gate. One table, two consumers.

Operation types: READ, WRITE, DELETE, EXECUTE (Governor's vocabulary).
Tiers: 0 cleanup, 1 self-correction, 2 escalate, 3 human approval required.

Actions are named "<area>.<action>" (e.g. "h2o.shut_valve"); connector tools keep
Flower's names. Add a row when you add an action; an unknown action is treated as a
Tier 3 WRITE until it is added here, so a missing row errs on the side of caution.
"""

from __future__ import annotations

READ, WRITE, DELETE, EXECUTE = "READ", "WRITE", "DELETE", "EXECUTE"

# action (the part after "<area>.") -> (operation type, tier)
ACTIONS: dict[str, tuple[str, int]] = {
    # Sensing and review: no change to the building
    "assess": (READ, 0),
    "run_audit": (READ, 0),  # CYBER audit is read-only, despite the word "run"
    "request_thermal_rescan": (READ, 0),
    "request_arc_rescan": (READ, 0),
    "request_crack_remeasure": (READ, 0),
    "request_facade_rescan": (READ, 0),
    "recheck_riser_pressure": (READ, 0),
    "run_emergency_light_test": (EXECUTE, 0),  # EGRESS 30-second self-test, re-read only
    # Tier 0: cleanup
    "clear_alert": (WRITE, 0),
    "rebaseline_sensor": (WRITE, 0),
    "close_duplicate_work_order": (WRITE, 0),
    # Tier 1: self-correction within set limits
    "reset_damper": (EXECUTE, 1),
    "adjust_setpoint": (WRITE, 1),
    "adjust_ventilation": (WRITE, 1),
    "retry_levelling": (EXECUTE, 1),
    "enable_night_flow_isolation_mode": (EXECUTE, 1),
    "reset_battery_charger": (EXECUTE, 1),  # GEN starting-battery charger
    # Tier 2: the coordinator alerts a human
    "escalate": (WRITE, 2),
    # Tier 3: consequential, human approval required
    "trip_breaker": (EXECUTE, 3),
    "shut_valve": (EXECUTE, 3),
    "isolate_zone": (EXECUTE, 3),
    "take_out_of_service": (EXECUTE, 3),
    "dispatch_contractor": (EXECUTE, 3),
    "notify_tenants": (WRITE, 3),
    "share_lesson": (WRITE, 3),
    "execute_approved_action": (EXECUTE, 3),
    # Tier 3: every CYBER change to the building control system
    "close_port": (WRITE, 3),
    "reset_password": (WRITE, 3),
    "disable_account": (WRITE, 3),
    "change_firewall_rule": (WRITE, 3),
    "push_config": (WRITE, 3),
}

# Flower connector tools, matched by name prefix
CONNECTORS: dict[str, tuple[str, int]] = {
    "web_search": (READ, 0),
    "web_fetch": (READ, 0),
    "slack": (READ, 0),  # read-only in Flower 1.39
    "notion": (READ, 0),  # read-only in Flower 1.39
    "start_automation": (EXECUTE, 3),  # "Watch 24/7" is a standing change
}

UNKNOWN: tuple[str, int] = (WRITE, 3)


def classify(tool_name: str) -> tuple[str, int]:
    """(operation type, tier) for a tool name; unknown names are a Tier 3 WRITE."""
    action = tool_name.split(".", 1)[1] if "." in tool_name else tool_name
    if action in ACTIONS:
        return ACTIONS[action]
    for prefix, entry in CONNECTORS.items():
        if tool_name.startswith(prefix):
            return entry
    return UNKNOWN


def operation(tool_name: str) -> str:
    return classify(tool_name)[0]


def tier(tool_name: str) -> int:
    """For the approval gate: 3 means a human must approve before the action runs."""
    return classify(tool_name)[1]
