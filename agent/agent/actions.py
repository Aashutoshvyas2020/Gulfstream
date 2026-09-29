"""Tier 0-3 actions for Antibody's agents, and the fixed rules that govern them.

Every action an agent can take is listed here, one catalog per area, with what it does
to the demo readings and how its fix is re-checked. Action ids follow the Governor
naming contract `<area>.<verb>_<object>` (see governance.py), so a Tier 3 id such as
`h2o.shut_valve` matches the high-consequence regexes in governance/profiles/.

Each action's tier and operation type come from the building domain adapter
(building_domain.py), the one table shared with the Governor record. Every action
here needs a row there; a missing row makes it a Tier 3 WRITE.

Tiers (docs/antibody-notes.md, section 2):
  0 Cleanup           runs once, no human; a re-read that still shows the problem escalates
  1 Self-correction   runs, re-checks its own fix, at most MAX_FIX_ATTEMPTS times, then escalates
  2 Escalate          the coordinator alerts a human
  3 Human approval    held until a person approves it in a later message of the run series

The numbers below are fixed in code, never left to the model. Effects on the readings
are simulated: the building is demo data, so a fix changes the demo readings, and the
change is kept in run-series state so the next scan sees it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from . import building_domain

HEALTH_ESCALATE = 70  # building health below this escalates to a human (Tier 2)
MAX_FIX_ATTEMPTS = 2  # a Tier 1 loop stops and escalates after this many failed fixes
SETPOINT_LIMIT_PERCENT = 15  # autonomous ventilation / setpoint changes stay within +/- this
ISOLATION_MAX_LPM = 10.0  # night-flow isolation contains a branch leak; above this it is the riser
APPROVAL_START_DELAY = timedelta(minutes=2)  # an approved automation starts this long after approval
# A Tier 3 proposal is held for a human only when its problem is urgent; otherwise it is
# advice in the report. At most MAX_HELD_PER_SCAN are held per scan, highest priority first.
URGENT_RISK = 0.8
URGENT_DAYS = 1.0
MAX_HELD_PER_SCAN = 3

CLEANUP, SELF_CORRECT, ESCALATE, APPROVAL = 0, 1, 2, 3

Readings = dict[str, Any]
Effect = Callable[[Readings, dict[str, Any]], Readings]


@dataclass(frozen=True)
class Action:
    area: str
    verb_object: str
    description: str
    # Simulated effect: returns the readings it changes
    effect: Effect | None = None
    # Re-check on the readings after the action: True when the problem is gone
    check: Callable[[Readings], bool] | None = None
    # Argument name clamped to +/- SETPOINT_LIMIT_PERCENT, if the action takes one
    limited_arg: str | None = None

    @property
    def id(self) -> str:
        return f"{self.area}.{self.verb_object}"

    @property
    def tier(self) -> int:
        return building_domain.tier(self.id)


def _num(readings: Readings, key: str, default: float = 0.0) -> float:
    try:
        return float(readings.get(key, default))
    except (TypeError, ValueError):
        return default


# Shared Tier 0 cleanups
def _clear_alert(area: str) -> Action:
    return Action(area, "clear_alert", "Clear a stale or duplicate alert for this area")


def _rebaseline(area: str) -> Action:
    return Action(
        area, "rebaseline_sensor", "Re-baseline a sensor after it was recalibrated"
    )


def _dispatch(area: str) -> Action:
    return Action(
        area, "dispatch_contractor", "Dispatch a licensed contractor to this area"
    )


# Simulated effects of Tier 1 fixes and Tier 3 actions
def _pwr_ventilate(r: Readings, args: dict) -> Readings:
    # More exhaust dilutes hydrogen but cannot cool cells that are heating themselves
    pct = float(args.get("percent", SETPOINT_LIMIT_PERCENT))
    return {"hydrogen_ppm": round(_num(r, "hydrogen_ppm") * (1 - pct / 100), 1)}


def _hvac_reset_damper(r: Readings, args: dict) -> Readings:
    design = _num(r, "design_kw_per_ton", 0.62)
    return {
        "damper_position_percent": r.get("damper_commanded_percent", 60),
        "kw_per_ton": round(design + 0.01, 2),
    }


def _hvac_efficient(r: Readings) -> bool:
    commanded = r.get("damper_commanded_percent")
    damper_ok = commanded is None or r.get("damper_position_percent") == commanded
    return damper_ok and _num(r, "kw_per_ton") <= _num(r, "design_kw_per_ton", 0.62) * 1.05


def _h2o_isolate_night_flow(r: Readings, args: dict) -> Readings:
    # Night isolation closes the floor branch valves: it stops a branch leak, not a riser leak
    if _num(r, "night_flow_lpm_building_empty") <= ISOLATION_MAX_LPM:
        return {"night_flow_lpm_building_empty": r.get("normal_night_flow_lpm", 0.3)}
    return {}


def _h2o_contained(r: Readings) -> bool:
    return _num(r, "night_flow_lpm_building_empty") <= 2 * _num(r, "normal_night_flow_lpm", 0.3)


def _h2o_shut_valve(r: Readings, args: dict) -> Readings:
    return {"night_flow_lpm_building_empty": 0.0, "riser_valve": "closed (approved)"}


def _lift_relevel(r: Readings, args: dict) -> Readings:
    return {"levelling_error_mm": round(_num(r, "levelling_error_mm") / 2, 1)}


def _air_ventilate(r: Readings, args: dict) -> Readings:
    pct = float(args.get("percent", SETPOINT_LIMIT_PERCENT))
    return {
        "co2_ppm": round(_num(r, "co2_ppm") * (1 - pct / 100)),
        "tvoc_ppb": round(_num(r, "tvoc_ppb") * (1 - pct / 100)),
    }


def _gas_ventilate(r: Readings, args: dict) -> Readings:
    pct = float(args.get("percent", SETPOINT_LIMIT_PERCENT))
    return {"garage_co_ppm": round(_num(r, "garage_co_ppm") * (1 - pct / 100))}


def _gen_reset_charger(r: Readings, args: dict) -> Readings:
    # A tripped charger recovers on reset; a failed one does not
    if str(r.get("battery_charger", "")).startswith("tripped"):
        return {"battery_charger": "charging", "starting_battery_voltage_v": 12.8}
    return {}


def _cyber_close_port(r: Readings, args: dict) -> Readings:
    port = args.get("port")
    ports = [p for p in r.get("open_ports_to_internet", []) if port is not None and p != port]
    return {"open_ports_to_internet": ports}


def _set(**changes: Any) -> Effect:
    return lambda r, args: dict(changes)


CATALOG: dict[str, dict[str, Action]] = {}


def _register(*actions: Action) -> None:
    for action in actions:
        CATALOG.setdefault(action.area.upper(), {})[action.id] = action


_register(
    _clear_alert("pwr"),
    _rebaseline("pwr"),
    Action(
        "pwr", "adjust_ventilation",
        "Raise battery-room exhaust by up to 15% (args: percent)",
        effect=_pwr_ventilate,
        check=lambda r: _num(r, "hydrogen_ppm") < 100 and _num(r, "cell_temp_c_now") < 35,
        limited_arg="percent",
    ),
    Action("pwr", "isolate_zone", "Disconnect the affected battery string from the bus",
           effect=_set(string_status="isolated (approved)")),
    _dispatch("pwr"),
)
_register(
    _clear_alert("elec"),
    _rebaseline("elec"),
    Action(
        "elec", "request_thermal_rescan",
        "Re-read the breaker with the fixed infrared sensor",
        effect=lambda r, args: {"last_infrared_scan": datetime.now().date().isoformat()},
        check=lambda r: _num(r, "breaker_temp_c") < 60,
    ),
    Action("elec", "trip_breaker", "Open the overheating breaker (loses its load)",
           effect=_set(breaker_state="open (approved)", breaker_temp_c=35)),
    _dispatch("elec"),
)
_register(
    _clear_alert("wire"),
    _rebaseline("wire"),
    Action(
        "wire", "request_arc_rescan", "Re-run arc-fault detection on the branch circuits",
        effect=_set(),
        check=lambda r: _num(r, "arc_fault_events_7d") == 0 and _num(r, "outlet_temp_anomalies") == 0,
    ),
    Action("wire", "trip_breaker", "De-energise the affected branch circuit",
           effect=_set(circuit_state="de-energised (approved)")),
    _dispatch("wire"),
)
_register(
    _clear_alert("fire"),
    _rebaseline("fire"),
    Action(
        "fire", "recheck_riser_pressure", "Re-read riser static pressure",
        effect=_set(),
        check=lambda r: _num(r, "static_pressure_psi") >= _num(r, "baseline_pressure_psi") - 5
        and r.get("control_valves_open", True) is True,
    ),
    _dispatch("fire"),
)
_register(
    _clear_alert("str"),
    _rebaseline("str"),
    Action(
        "str", "request_crack_remeasure", "Re-measure the crack with the gauge",
        effect=_set(),
        check=lambda r: _num(r, "crack_width_mm_now") - _num(r, "crack_width_mm_90d_ago") < 0.1,
    ),
    Action("str", "isolate_zone", "Close the parking bays around the column",
           effect=_set(zone_status="closed to parking (approved)")),
    _dispatch("str"),
)
_register(
    _clear_alert("lift"),
    Action(
        "lift", "retry_levelling", "Run a levelling calibration cycle",
        effect=_lift_relevel,
        check=lambda r: _num(r, "levelling_error_mm") <= 3,
    ),
    Action("lift", "take_out_of_service", "Take the elevator out of service",
           effect=_set(service_status="out of service (approved)")),
    _dispatch("lift"),
)
_register(
    _clear_alert("hvac"),
    Action(
        "hvac", "reset_damper", "Reset a stuck damper to its commanded position",
        effect=_hvac_reset_damper,
        check=_hvac_efficient,
    ),
    Action(
        "hvac", "adjust_setpoint",
        "Move the chilled-water setpoint by up to 15% (args: percent)",
        effect=_set(),
        check=_hvac_efficient,
        limited_arg="percent",
    ),
    _dispatch("hvac"),
)
_register(
    _clear_alert("h2o"),
    _rebaseline("h2o"),
    Action(
        "h2o", "enable_night_flow_isolation_mode",
        "Close the floor branch valves while the building is empty",
        effect=_h2o_isolate_night_flow,
        check=_h2o_contained,
    ),
    Action("h2o", "shut_valve", "Shut the riser valve (cuts water above it)",
           effect=_h2o_shut_valve),
    _dispatch("h2o"),
)
_register(
    _clear_alert("env"),
    Action(
        "env", "request_facade_rescan", "Re-scan the facade panels",
        effect=_set(),
        check=lambda r: _num(r, "loose_panel_alerts") == 0,
    ),
    Action("env", "isolate_zone", "Cordon the sidewalk below the facade",
           effect=_set(sidewalk="cordoned (approved)")),
    _dispatch("env"),
)
_register(
    _clear_alert("air"),
    Action(
        "air", "adjust_ventilation", "Raise outdoor-air ventilation by up to 15% (args: percent)",
        effect=_air_ventilate,
        check=lambda r: _num(r, "co2_ppm") <= 800 and _num(r, "tvoc_ppb") <= 500,
        limited_arg="percent",
    ),
    Action("air", "notify_tenants", "Message tenants about air quality"),
)
_register(
    # CYBER is audit-only: nothing it may do alone changes the control system
    _clear_alert("cyber"),
    Action("cyber", "run_audit", "Audit the controller configuration (no changes)"),
    Action("cyber", "close_port", "Close an internet-facing port (args: port)",
           effect=_cyber_close_port),
    Action("cyber", "reset_password", "Reset the controller admin password",
           effect=_set(default_admin_password=False)),
    Action("cyber", "disable_account", "Disable a controller account (args: account)"),
    Action("cyber", "change_firewall_rule", "Change a BMS firewall rule"),
)
_register(
    _clear_alert("gen"),
    Action(
        "gen", "reset_battery_charger", "Reset the generator's starting-battery charger",
        effect=_gen_reset_charger,
        check=lambda r: _num(r, "starting_battery_voltage_v") >= 12.4,
    ),
    Action("gen", "take_out_of_service", "Take the generator out of service for repair",
           effect=_set(service_status="out of service (approved)")),
    _dispatch("gen"),
)
_register(
    _clear_alert("gas"),
    _rebaseline("gas"),
    Action(
        "gas", "adjust_ventilation", "Raise garage exhaust by up to 15% (args: percent)",
        effect=_gas_ventilate,
        check=lambda r: _num(r, "garage_co_ppm") <= 35 and _num(r, "boiler_room_ch4_percent_lel") < 10,
        limited_arg="percent",
    ),
    Action("gas", "shut_valve", "Shut the gas main to the boiler room",
           effect=_set(gas_main="closed (approved)")),
    Action("gas", "notify_tenants", "Tell tenants to avoid the affected area"),
)
_register(
    _clear_alert("egress"),
    Action(
        "egress", "run_emergency_light_test",
        "Run the 30-second self-test on exit signs and emergency lights",
        effect=_set(),
        check=lambda r: _num(r, "exit_signs_failed_self_test") == 0
        and _num(r, "emergency_lights_battery_fail") == 0,
    ),
    _dispatch("egress"),
    Action("egress", "notify_tenants", "Remind tenants not to prop fire doors open"),
)

# The coordinator's own actions (its connector calls keep Flower's names)
COORDINATOR_AREA = "coordinator"
ESCALATE_ID = f"{COORDINATOR_AREA}.escalate"
AUTOMATION_TOOL = "start_automation"


def catalog_for(code: str) -> dict[str, Action]:
    return CATALOG.get(code.upper(), {})


def tools_prompt(code: str) -> str:
    """The tool list shown to one specialist, one line per action."""
    lines = []
    for action in catalog_for(code).values():
        gate = " (held until a human approves)" if action.tier == APPROVAL else ""
        lines.append(f"- {action.id} [Tier {action.tier}]{gate}: {action.description}")
    return "\n".join(lines)


def outcome(code: str, action_id: str, tier: int, status: str, **extra: Any) -> dict[str, Any]:
    """One step of an action, as it appears in the report and the antibody.action event."""
    return {"agent": code, "action": action_id, "tier": tier, "status": status, **extra}


@dataclass
class ActResult:
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    changes: Readings = field(default_factory=dict)
    fixed: bool = False
    # Tier 3 proposals that need an approval id (assigned by the coordinator)
    held: list[dict[str, Any]] = field(default_factory=list)


def _clamp_args(action: Action, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not action.limited_arg or action.limited_arg not in args:
        return args, ""
    try:
        value = float(args[action.limited_arg])
    except (TypeError, ValueError):
        return {**args, action.limited_arg: SETPOINT_LIMIT_PERCENT}, "non-numeric value replaced"
    limited = max(-SETPOINT_LIMIT_PERCENT, min(SETPOINT_LIMIT_PERCENT, value))
    note = f"{value:g}% clamped to {limited:g}%" if limited != value else ""
    return {**args, action.limited_arg: limited}, note


def parse_proposal(value: Any) -> tuple[str, dict[str, Any], str] | None:
    """(action id, args, reason) from a specialist's proposed_action, or None."""
    if not isinstance(value, dict) or not isinstance(value.get("tool"), str):
        return None
    args = value.get("args") if isinstance(value.get("args"), dict) else {}
    return value["tool"].strip(), args, str(value.get("reason", ""))[:300]


def apply(record: Any, action: Action, readings: Readings, args: dict[str, Any]) -> Readings:
    """Run one action through the agent's Governor record; update and return the readings it changed."""

    def run() -> Readings:
        return action.effect(readings, args) if action.effect else {}

    changes = record.call(action.id, run) or {}
    readings.update(changes)
    return changes


def is_urgent(risk_score: Any, time_to_failure_days: Any) -> bool:
    """Urgent enough for a human decision now: high risk, or failure within about a day."""
    try:
        if float(risk_score) >= URGENT_RISK:
            return True
    except (TypeError, ValueError):
        pass
    try:
        return float(time_to_failure_days) <= URGENT_DAYS
    except (TypeError, ValueError):
        return False


def act(record: Any, code: str, proposal: Any, readings: Readings, urgent: bool = True) -> ActResult:
    """Carry out one specialist's proposed action under the tier rules.

    `readings` is this area's readings; simulated effects are applied to it in place.
    `urgent` (see is_urgent) decides whether a Tier 3 proposal is held for a human or
    kept as advice.
    """
    result = ActResult()
    parsed = parse_proposal(proposal)
    if parsed is None:
        return result
    action_id, args, reason = parsed
    catalog = catalog_for(code)
    action = catalog.get(action_id)

    if action is None:
        # Not this agent's tool. If it names another area's system, record the attempt
        # so Governor shows it outside the declared scope, then refuse and escalate.
        if re.fullmatch(r"[a-z0-9]+\.[a-z0-9_]+", action_id):
            record.call(action_id, lambda: None)
        result.outcomes.append(
            outcome(code, action_id, ESCALATE, "refused", detail="not one of this agent's tools", reason=reason)
        )
        result.outcomes.append(
            outcome(code, ESCALATE_ID, ESCALATE, "escalated", detail=f"{code} asked for {action_id}, outside its tools")
        )
        return result

    args, note = _clamp_args(action, args)

    if action.tier == CLEANUP:
        result.changes.update(apply(record, action, readings, args))
        if action.check is not None and not action.check(readings):
            # A re-read (rescan, remeasure) that still shows the problem goes to a human
            result.outcomes.append(outcome(code, action.id, CLEANUP, "recheck_failed", reason=reason, detail=note))
            result.outcomes.append(
                outcome(code, ESCALATE_ID, ESCALATE, "escalated", detail=f"{action.id} still shows the problem")
            )
            return result
        result.outcomes.append(outcome(code, action.id, CLEANUP, "done", reason=reason, detail=note))
        return result

    if action.tier == SELF_CORRECT:
        for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
            result.changes.update(apply(record, action, readings, args))
            if action.check is None or action.check(readings):
                result.fixed = True
                result.outcomes.append(
                    outcome(code, action.id, SELF_CORRECT, "fixed", attempt=attempt, reason=reason, detail=note)
                )
                return result
            result.outcomes.append(
                outcome(code, action.id, SELF_CORRECT, "recheck_failed", attempt=attempt, reason=reason, detail=note)
            )
        result.outcomes.append(
            outcome(code, ESCALATE_ID, ESCALATE, "escalated",
                    detail=f"{action.id} failed its re-check {MAX_FIX_ATTEMPTS} times")
        )
        return result

    if action.tier != APPROVAL:  # a Tier 2 row: the action is the escalation itself
        result.outcomes.append(outcome(code, ESCALATE_ID, ESCALATE, "escalated", detail=f"{code} asked to escalate"))
        return result

    if not urgent:
        # Not urgent: the recommendation stays in the report, and nobody is asked to decide
        result.outcomes.append(
            outcome(code, action.id, APPROVAL, "advised", reason=reason,
                    detail="not urgent enough for an approval; recommended in the report")
        )
        return result

    # Tier 3: record the attempt (Governor flags it as high-consequence), then hold it
    record.call(action.id, lambda: None)
    result.held.append({"agent": code, "action": action.id, "args": args, "reason": reason})
    return result


def execute_approved(record: Any, code: str, item: dict[str, Any], readings: Readings) -> ActResult:
    """Run a Tier 3 action a human approved in this message."""
    result = ActResult()
    action = catalog_for(code).get(item["action"])
    if action is None:
        result.outcomes.append(
            outcome(code, item["action"], APPROVAL, "failed", approval_id=item["id"], detail="unknown action")
        )
        return result
    result.changes.update(apply(record, action, readings, item.get("args", {})))
    result.outcomes.append(
        outcome(code, action.id, APPROVAL, "executed", approval_id=item["id"], approved_by=item.get("approved_by", ""))
    )
    return result


# Approvals: a Flower run is one chat message, so a held action is decided in the next one.
# Accepted forms: {"approve": ["A1"], "reject": ["A2"], "by": "Dana"}, or text such as
# "approve A1 and A3", "approve all", "reject A2".
_DECISION_WORDS = {"approve": "approve", "approved": "approve", "reject": "reject", "rejected": "reject", "deny": "reject"}


def parse_decisions(prompt: str, pasted: dict[str, Any] | None) -> tuple[set[str], set[str], str]:
    """(approved ids, rejected ids, approver) from the facility manager's message. "all" is a valid id."""
    approve: set[str] = set()
    reject: set[str] = set()
    by = ""
    if pasted:
        for key, target in (("approve", approve), ("reject", reject)):
            value = pasted.get(key)
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                target.update(str(v).strip().upper() for v in value)
        if isinstance(pasted.get("by"), str):
            by = pasted["by"][:80]
    # Plain-text decisions, ignoring any pasted JSON
    text = re.sub(r"\{.*\}", " ", prompt, flags=re.DOTALL)
    mode = None
    for token in re.findall(r"[A-Za-z]+\d*", text):
        word = token.lower()
        if word in _DECISION_WORDS:
            mode = _DECISION_WORDS[word]
        elif mode and (re.fullmatch(r"a\d+", word) or word == "all"):
            (approve if mode == "approve" else reject).add(token.upper())
        elif word not in {"and", "or", "action", "actions", "id", "ids"}:
            mode = None
    return approve, reject - approve, by


def decide(
    pending: list[dict[str, Any]], approve: set[str], reject: set[str], by: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split pending approvals into (approved, rejected, still pending)."""
    approved, rejected, remaining = [], [], []
    for item in pending:
        if item["id"] in approve or "ALL" in approve:
            approved.append({**item, "approved_by": by or "facility manager"})
        elif item["id"] in reject or "ALL" in reject:
            rejected.append(item)
        else:
            remaining.append(item)
    return approved, rejected, remaining


def rescheduled_automation(arguments: str, now: datetime) -> str:
    """An approved start_automation starts shortly after approval, not when it was first asked."""
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return arguments
    if isinstance(args, dict) and "start_at" in args:
        args["start_at"] = (now + APPROVAL_START_DELAY).isoformat(timespec="seconds")
    return json.dumps(args)


def funnel(outcomes: list[dict[str, Any]], pending: list[dict[str, Any]]) -> dict[str, int]:
    """Actions -> autonomous fixes -> escalations -> human decisions, for this scan."""
    attempts = [o for o in outcomes if o["status"] in {"done", "fixed", "recheck_failed", "refused", "held", "executed"}]
    # "advised" is a recommendation, not an action, so it is not counted
    return {
        "actions": len(attempts),
        "auto_fixes": sum(1 for o in outcomes if o["status"] in {"done", "fixed"}),
        "escalations": sum(1 for o in outcomes if o["status"] == "escalated"),
        "human_decisions": len(pending),
    }
