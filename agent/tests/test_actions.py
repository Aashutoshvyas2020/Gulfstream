"""Tier rules, approvals and a two-message scan, with a fake model. No SuperLink needed.

    cd agent && python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("FLWR_RUNTIME_BASE_URL", "http://fake")
os.environ.setdefault("FLWR_RUNTIME_API_KEY", "fake")

from agent import actions, agent_app, building_domain, governance  # noqa: E402
from agent.building_data import SNAPSHOT  # noqa: E402
from agent.specialists import SPECIALISTS  # noqa: E402


class FakeRecord:
    """Stands in for AgentRecord: runs every action and logs what it was recorded as."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def call(self, tool_name, fn, operation=None):
        self.calls.append((tool_name, operation))
        return fn()

    def close(self) -> None:
        pass


def readings(code: str, **changes) -> dict:
    return {**SNAPSHOT[code], **changes}


class CatalogTest(unittest.TestCase):
    def test_every_specialist_has_readings_and_actions(self):
        self.assertEqual(set(SPECIALISTS), set(SNAPSHOT))
        self.assertEqual(set(SPECIALISTS), set(actions.CATALOG))

    def test_action_ids_follow_the_naming_contract(self):
        for code, catalog in actions.CATALOG.items():
            for action_id, action in catalog.items():
                self.assertRegex(action_id, rf"^{code.lower()}\.[a-z]+(_[a-z0-9]+)+$")
                if action.tier == actions.SELF_CORRECT:
                    self.assertIsNotNone(action.check, action_id)

    def test_every_action_has_a_building_domain_row(self):
        # A missing row silently makes an action a Tier 3 WRITE
        for catalog in actions.CATALOG.values():
            for action in catalog.values():
                self.assertIn(action.verb_object, building_domain.ACTIONS, action.id)
        self.assertEqual(building_domain.tier(actions.ESCALATE_ID), actions.ESCALATE)
        self.assertEqual(building_domain.tier(actions.AUTOMATION_TOOL), actions.APPROVAL)

    def test_every_tier3_action_is_flagged_by_its_governor_profile(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        profiles = {
            name: yaml.safe_load((REPO / "governance" / "profiles" / f"{name}.yaml").read_text())
            for name in ("agent", "cyber")
        }
        for code, catalog in actions.CATALOG.items():
            profile = profiles["cyber" if code == "CYBER" else "agent"]
            patterns = profile["high_consequence"]["tools"]
            for action in catalog.values():
                if action.tier == actions.APPROVAL:
                    key = f"{action.id}:{action.area}"
                    self.assertTrue(any(re.search(p, key) for p in patterns), f"{key} is not flagged")

    def test_cyber_can_change_nothing_alone(self):
        for action in actions.CATALOG["CYBER"].values():
            if action.tier < actions.APPROVAL:
                self.assertIsNone(action.effect, action.id)


class TierRulesTest(unittest.TestCase):
    def test_tier1_fix_that_works_stops_after_one_attempt(self):
        r = readings("HVAC")
        result = actions.act(FakeRecord(), "HVAC", {"tool": "hvac.reset_damper"}, r)
        self.assertTrue(result.fixed)
        self.assertEqual([o["status"] for o in result.outcomes], ["fixed"])
        self.assertEqual(r["damper_position_percent"], 60)

    def test_tier1_fix_that_fails_retries_twice_then_escalates(self):
        r = readings("H2O", night_flow_lpm_building_empty=14)
        record = FakeRecord()
        result = actions.act(record, "H2O", {"tool": "h2o.enable_night_flow_isolation_mode"}, r)
        self.assertFalse(result.fixed)
        self.assertEqual(
            [o["status"] for o in result.outcomes], ["recheck_failed", "recheck_failed", "escalated"]
        )
        self.assertEqual(len(record.calls), actions.MAX_FIX_ATTEMPTS)

    def test_branch_leak_is_contained_by_isolation(self):
        result = actions.act(FakeRecord(), "H2O", {"tool": "h2o.enable_night_flow_isolation_mode"}, readings("H2O"))
        self.assertTrue(result.fixed)

    def test_tier0_reread_that_still_shows_the_problem_escalates(self):
        result = actions.act(FakeRecord(), "ELEC", {"tool": "elec.request_thermal_rescan"}, readings("ELEC"))
        self.assertEqual([o["status"] for o in result.outcomes], ["recheck_failed", "escalated"])
        cool = readings("ELEC", breaker_temp_c=45)
        result = actions.act(FakeRecord(), "ELEC", {"tool": "elec.request_thermal_rescan"}, cool)
        self.assertEqual([o["status"] for o in result.outcomes], ["done"])

    def test_setpoint_changes_are_clamped(self):
        result = actions.act(
            FakeRecord(), "AIR", {"tool": "air.adjust_ventilation", "args": {"percent": 40}}, readings("AIR")
        )
        self.assertIn("40% clamped to 15%", result.outcomes[0]["detail"])

    def test_tier3_is_held_not_run(self):
        r = readings("H2O", night_flow_lpm_building_empty=14)
        record = FakeRecord()
        result = actions.act(record, "H2O", {"tool": "h2o.shut_valve", "reason": "riser leak"}, r)
        self.assertEqual(result.held[0]["action"], "h2o.shut_valve")
        self.assertEqual(r["night_flow_lpm_building_empty"], 14)  # nothing changed
        self.assertEqual(record.calls, [("h2o.shut_valve", None)])  # the attempt is on record

    def test_tier3_that_is_not_urgent_is_advice(self):
        record = FakeRecord()
        result = actions.act(record, "ELEC", {"tool": "elec.dispatch_contractor"}, readings("ELEC"), urgent=False)
        self.assertEqual([o["status"] for o in result.outcomes], ["advised"])
        self.assertEqual(result.held, [])
        self.assertEqual(record.calls, [])  # nothing attempted, nothing for Governor to flag

    def test_urgency(self):
        self.assertTrue(actions.is_urgent(0.85, None))
        self.assertTrue(actions.is_urgent(0.3, 0.5))
        self.assertFalse(actions.is_urgent(0.7, 3))
        self.assertFalse(actions.is_urgent("n/a", None))

    def test_another_areas_tool_is_refused_and_escalated(self):
        record = FakeRecord()
        result = actions.act(record, "HVAC", {"tool": "h2o.shut_valve"}, readings("HVAC"))
        self.assertEqual([o["status"] for o in result.outcomes], ["refused", "escalated"])
        self.assertEqual(record.calls, [("h2o.shut_valve", None)])  # Governor sees it out of scope

    def test_no_proposal_does_nothing(self):
        for proposal in (None, {}, "hvac.reset_damper", {"tool": 3}):
            self.assertEqual(actions.act(FakeRecord(), "HVAC", proposal, readings("HVAC")).outcomes, [])


class DecisionsTest(unittest.TestCase):
    def test_text_and_json_forms(self):
        cases = {
            "approve A1": ({"A1"}, set()),
            "Approve A1 and A3, reject A2": ({"A1", "A3"}, {"A2"}),
            "approve all": ({"ALL"}, set()),
            "Check the building.": (set(), set()),
            "I approve of this report": (set(), set()),
        }
        for prompt, (approve, reject) in cases.items():
            got = actions.parse_decisions(prompt, None)
            self.assertEqual((got[0], got[1]), (approve, reject), prompt)
        got = actions.parse_decisions('{"approve": ["a4"], "by": "Dana"}', {"approve": ["a4"], "by": "Dana"})
        self.assertEqual(got, ({"A4"}, set(), "Dana"))

    def test_decide(self):
        pending = [{"id": "A1"}, {"id": "A2"}, {"id": "A3"}]
        approved, rejected, remaining = actions.decide(pending, {"A1"}, {"A2"}, "")
        self.assertEqual([p["id"] for p in approved], ["A1"])
        self.assertEqual(approved[0]["approved_by"], "facility manager")
        self.assertEqual([p["id"] for p in rejected], ["A2"])
        self.assertEqual([p["id"] for p in remaining], ["A3"])


# ---- A full scan with a fake model -------------------------------------------------

class FakeModel:
    """Specialists answer from `risk` and `propose`; the coordinator may ask for automation."""

    def __init__(self, risk=None, propose=None, fail=(), automation=False):
        self.risk = risk or {}
        self.propose = propose or {}
        self.fail = set(fail)
        self.automation = automation
        self.inputs: list[dict] = []

    def create(self, **kw):
        if kw.get("stream"):
            text = SimpleNamespace(type="response.output_text.delta", delta="alert", to_dict=dict)
            done = SimpleNamespace(type="response.completed", to_dict=lambda: {"type": "response.completed"})
            self.inputs.append(kw)
            return iter([text, done])
        if kw.get("tools") is not None:
            asked = any("held_for_approval" in str(i.get("output", "")) for i in kw["input"])
            if self.automation and not asked:
                call = {"type": "function_call", "call_id": "c1", "name": "start_automation",
                        "arguments": json.dumps({"start_at": "2026-09-29T02:12:00-07:00", "fixed_interval": 600, "max_runs": 6})}
                return SimpleNamespace(output=[SimpleNamespace(to_dict=lambda: dict(call))])
            return SimpleNamespace(output=[])
        code = kw["instructions"].split("You are the ", 1)[1].split(" agent", 1)[0]
        if code in self.fail:
            raise RuntimeError("model down")
        r = self.risk.get(code, 0.05)
        reply = {
            "risk_score": r, "confidence": 0.8, "finding": "No issue" if r < 0.1 else f"{code} problem",
            "evidence": "", "time_to_failure_days": None if r < 0.1 else 3, "recommended_action": "",
            "proposed_action": self.propose.get(code),
        }
        return SimpleNamespace(output_text=json.dumps(reply))


class Connectors:
    def __init__(self):
        self.called: list[dict] = []

    def tools(self, refs):
        return [{"type": "function", "name": refs[0]}]

    def call(self, call):
        self.called.append(call)
        return {"type": "function_call_output", "call_id": call["call_id"], "output": "{}"}


def scan(prompt, state, model):
    agent_app.OpenAI = lambda **kw: SimpleNamespace(responses=model)
    events: list[dict] = []
    connectors = Connectors()
    agent = SimpleNamespace(prompt=prompt, events=SimpleNamespace(emit=events.append), connectors=connectors)
    agent_app.main(agent, SimpleNamespace(run_id=7, state=state))
    return events, connectors


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


class ScanTest(unittest.TestCase):
    """Full scans with the investigation and the model-written alert switched on."""

    def setUp(self):
        self._enabled = governance.ENABLED
        governance.ENABLED = False  # Governor has its own test below
        self._flags = agent_app.INVESTIGATE, agent_app.MODEL_ALERT
        agent_app.INVESTIGATE = agent_app.MODEL_ALERT = True

    def tearDown(self):
        governance.ENABLED = self._enabled
        agent_app.INVESTIGATE, agent_app.MODEL_ALERT = self._flags

    def test_leak_is_held_then_approved_in_the_next_message(self):
        state: dict = {}
        leak = '{"H2O": {"night_flow_lpm_building_empty": 14}}'
        model = FakeModel(risk={"H2O": 0.9}, propose={
            "H2O": {"tool": "h2o.shut_valve", "reason": "riser leak"},
            "HVAC": {"tool": "hvac.reset_damper"},
        })
        events, _ = scan("Leak spreads. " + leak, state, model)
        statuses = {(e["agent"], e["status"]) for e in of_type(events, "antibody.action")}
        self.assertIn(("H2O", "held"), statuses)
        self.assertIn(("HVAC", "fixed"), statuses)
        funnel = of_type(events, "antibody.scan.funnel")[0]
        self.assertEqual(funnel["human_decisions"], 1)
        approval_id = funnel["pending"][0]["id"]

        events, _ = scan(f"approve {approval_id}", state, FakeModel())
        executed = [e for e in of_type(events, "antibody.action") if e["status"] == "executed"]
        self.assertEqual([(e["agent"], e["action"]) for e in executed], [("H2O", "h2o.shut_valve")])
        self.assertEqual(of_type(events, "antibody.scan.funnel")[0]["pending"], [])
        overrides = json.loads(state[agent_app.ACTIONS_KEY]["overrides"])
        self.assertEqual(overrides["H2O"]["night_flow_lpm_building_empty"], 0.0)
        self.assertEqual(overrides["HVAC"]["damper_position_percent"], 60)

    def test_start_automation_needs_approval(self):
        state: dict = {}
        events, connectors = scan("Watch it 24/7", state, FakeModel(automation=True))
        self.assertEqual(connectors.called, [])  # not scheduled
        pending = of_type(events, "antibody.scan.funnel")[0]["pending"]
        self.assertEqual(pending[0]["action"], "start_automation")

        events, connectors = scan(f"approve {pending[0]['id']}", state, FakeModel())
        self.assertEqual([c["name"] for c in connectors.called], ["start_automation"])
        self.assertNotEqual(json.loads(connectors.called[0]["arguments"])["start_at"], "2026-09-29T02:12:00-07:00")

    def test_rejected_action_is_dropped(self):
        state: dict = {}
        model = FakeModel(risk={"CYBER": 0.9}, propose={"CYBER": {"tool": "cyber.close_port", "args": {"port": 47808}}})
        events, _ = scan("Check the building.", state, model)
        approval_id = of_type(events, "antibody.scan.funnel")[0]["pending"][0]["id"]
        events, _ = scan(f"reject {approval_id}", state, model)
        statuses = [e["status"] for e in of_type(events, "antibody.action") if e["agent"] == "CYBER"]
        self.assertEqual(statuses, ["rejected", "skipped"])
        self.assertEqual(of_type(events, "antibody.scan.funnel")[0]["pending"], [])

    def test_at_most_three_approvals_per_scan_highest_priority_first(self):
        risky = {c: 0.9 for c in ("PWR", "ELEC", "H2O", "STR", "CYBER")}
        propose = {c: {"tool": f"{c.lower()}.dispatch_contractor"} for c in ("PWR", "ELEC", "H2O", "STR")}
        propose["CYBER"] = {"tool": "cyber.close_port", "args": {"port": 47808}}
        events, _ = scan("Check the building.", {}, FakeModel(risk=risky, propose=propose))
        pending = of_type(events, "antibody.scan.funnel")[0]["pending"]
        self.assertEqual(len(pending), actions.MAX_HELD_PER_SCAN)
        ranked = [r["subsystem"] for r in of_type(events, "antibody.scan.ranked")[0]["ranked"]]
        self.assertEqual([p["agent"] for p in pending], [c for c in ranked if c in propose][:3])
        advised = [e for e in of_type(events, "antibody.action") if e["status"] == "advised"]
        self.assertEqual(len(advised), 2)

    def test_coordinator_gets_only_the_tools_the_message_asks_for(self):
        self.assertEqual(agent_app.connector_refs("Check the building."), ("web_search", "web_fetch"))
        self.assertIn("start_automation", agent_app.connector_refs("Watch it 24/7"))
        self.assertIn("start_automation", agent_app.connector_refs("then keep watching it"))
        self.assertIn("slack", agent_app.connector_refs("Any tenant complaints?"))
        self.assertNotIn("start_automation", agent_app.connector_refs("approve A1"))

    def test_failed_agent_lowers_health(self):
        healthy, _ = scan("Check the building.", {}, FakeModel())
        failed, _ = scan("Check the building.", {}, FakeModel(fail={"PWR"}))
        h = lambda ev: of_type(ev, "antibody.scan.ranked")[0]["health"]
        self.assertLess(h(failed), h(healthy))

    def test_slow_investigation_does_not_stop_the_alert(self):
        class SlowCoordinator(FakeModel):
            def create(self, **kw):
                if kw.get("tools") is not None:
                    self.timeouts = kw.get("timeout")
                    raise TimeoutError("model service slow")
                return super().create(**kw)

        model = SlowCoordinator()
        events, _ = scan("Check the building.", {}, model)
        self.assertEqual(model.timeouts, agent_app.INVESTIGATE_TIMEOUT)
        self.assertTrue(of_type(events, "response.output_text.delta"))  # the alert still streamed
        self.assertIn("investigation stopped early", json.dumps(model.inputs[-1]["input"]))

    def test_alert_falls_back_to_plain_code_when_the_model_times_out(self):
        class SlowAlert(FakeModel):
            def create(self, **kw):
                if kw.get("stream"):
                    return iter([SimpleNamespace(type="response.output_text.delta", delta="partial "),
                                 SimpleNamespace(type="error", to_dict=lambda: {"type": "error"},
                                                 code="model_response_timeout")])
                return super().create(**kw)

        state: dict = {}
        model = SlowAlert(risk={"H2O": 0.9}, propose={"H2O": {"tool": "h2o.shut_valve", "reason": "riser leak"}})
        events, _ = scan("Check the building.", state, model)
        text = "".join(e["delta"] for e in of_type(events, "response.output_text.delta"))
        self.assertTrue(text.startswith("**Building health:"))  # code writes the head at once
        self.assertIn("partial ", text)
        self.assertIn("**Building health:", text)
        self.assertEqual(text.count("| H2O Pipe leak |"), 1)
        self.assertEqual(sum(1 for line in text.splitlines() if line.startswith("| ") and line[2].isdigit()), 14)
        self.assertIn("**Needs your approval**", text)
        self.assertIn("h2o.shut_valve", text)
        self.assertIn("approve", state[agent_app.STATE_KEY]["last_alert"])  # remembered for follow-ups

    def test_every_scan_ends_with_exactly_one_completed_event_and_no_error(self):
        class SlowAlert(FakeModel):
            def create(self, **kw):
                if kw.get("stream"):
                    return iter([SimpleNamespace(type="response.output_text.delta", delta="partial "),
                                 SimpleNamespace(type="error", to_dict=lambda: {"type": "error"})])
                return super().create(**kw)

        for model in (FakeModel(), SlowAlert()):
            events, _ = scan("Check the building.", {}, model)
            kinds = [e["type"] for e in events]
            self.assertEqual(kinds.count("response.completed"), 1)
            self.assertEqual(kinds[-1], "response.completed")  # after the approvals text
            self.assertNotIn("error", kinds)

    def test_alert_falls_back_when_the_call_itself_fails(self):
        class DownAlert(FakeModel):
            def create(self, **kw):
                if kw.get("stream"):
                    raise ConnectionError("model service unreachable")
                return super().create(**kw)

        events, _ = scan("Check the building.", {}, DownAlert())
        text = "".join(e["delta"] for e in of_type(events, "response.output_text.delta"))
        self.assertIn("ConnectionError", text)
        self.assertIn("**Building health:", text)

    def test_alert_call_has_its_own_time_limit(self):
        model = FakeModel()
        scan("Check the building.", {}, model)
        self.assertEqual(model.inputs[-1]["timeout"], agent_app.ALERT_TIMEOUT)

    def test_keep_alive_sends_progress_while_a_scan_is_quiet(self):
        events: list[dict] = []
        agent = SimpleNamespace(events=SimpleNamespace(emit=events.append))
        saved, agent_app.KEEPALIVE_SECONDS = agent_app.KEEPALIVE_SECONDS, 0.05
        try:
            with agent_app.KeepAlive(agent):
                import time
                time.sleep(0.3)
        finally:
            agent_app.KEEPALIVE_SECONDS = saved
        self.assertGreaterEqual(len(of_type(events, "antibody.progress")), 3)

    def test_investigation_can_be_switched_off(self):
        model = FakeModel(automation=True)
        agent_app.INVESTIGATE = False
        try:
            events, connectors = scan("Watch it 24/7", {}, model)
        finally:
            agent_app.INVESTIGATE = True
        self.assertEqual(of_type(events, "antibody.tool"), [])
        self.assertTrue(of_type(events, "response.output_text.delta"))

    def test_low_health_escalates(self):
        events, _ = scan("Check the building.", {}, FakeModel(risk={c: 0.6 for c in SPECIALISTS}))
        escalations = [e for e in of_type(events, "antibody.action") if e["action"] == actions.ESCALATE_ID]
        self.assertTrue(any("below 70" in e["detail"] for e in escalations))


class SpeedTest(unittest.TestCase):
    def setUp(self):
        self._enabled = governance.ENABLED
        governance.ENABLED = False
        self._saved = {name: getattr(agent_app, name) for name, _ in agent_app.SETTINGS.values()}
        agent_app.INVESTIGATE = agent_app.MODEL_ALERT = True

    def tearDown(self):
        governance.ENABLED = self._enabled
        for name, value in self._saved.items():
            setattr(agent_app, name, value)
        agent_app._UNSUPPORTED.clear()

    def test_settings_come_from_run_config_and_env_overrides_them(self):
        context = SimpleNamespace(run_config={"parallel-agents": 9, "top-alerts": 1, "investigate": False})
        env = {"ANTIBODY_TOP_ALERTS": "3"}
        with unittest.mock.patch.dict(os.environ, env):
            os.environ.pop("ANTIBODY_MODEL", None)
            applied = agent_app.configure(context)
        self.assertEqual(applied["parallel-agents"], 9)
        self.assertEqual(applied["top-alerts"], 3)  # env wins
        self.assertIs(applied["investigate"], False)

    def test_pyproject_run_config_matches_the_settings(self):
        import tomllib
        config = tomllib.loads((REPO / "agent" / "pyproject.toml").read_text())["tool"]["flwr"]["app"]["config"]
        self.assertEqual(set(config), set(agent_app.SETTINGS))
        self.assertEqual(config["parallel-agents"], 14)
        self.assertIs(config["investigate"], False)
        self.assertIs(config["model-alert"], False)

    def test_default_scan_makes_only_specialist_model_calls(self):
        agent_app.INVESTIGATE = agent_app.MODEL_ALERT = False
        model = FakeModel(risk={"H2O": 0.9, "PWR": 0.8})
        seen = []
        original = model.create
        model.create = lambda **kw: (seen.append(kw), original(**kw))[1]
        events, _ = scan("Check the building.", {}, model)
        self.assertEqual(len(seen), 14)  # one per specialist, nothing else
        text = "".join(e["delta"] for e in of_type(events, "response.output_text.delta"))
        self.assertIn("**Top 2 alerts**", text)
        self.assertNotIn("did not finish", text)
        self.assertEqual([e["type"] for e in events].count("response.completed"), 1)

    def test_rejected_speed_option_is_dropped_and_retried(self):
        from openai import BadRequestError
        import httpx
        calls = []

        def create(**kw):
            calls.append(kw)
            if "reasoning" in kw:
                raise BadRequestError("Unsupported parameter: reasoning", response=httpx.Response(400, request=httpx.Request("POST", "http://x")), body=None)
            return "ok"

        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        self.assertEqual(agent_app.create_response(client, 500, model="m"), "ok")
        self.assertIn("reasoning", calls[0])
        self.assertNotIn("reasoning", calls[1])
        self.assertEqual(calls[1]["max_output_tokens"], 500)

    def test_specialists_ask_for_short_answers(self):
        model = FakeModel()
        seen = []
        original = model.create
        model.create = lambda **kw: (seen.append(kw), original(**kw))[1]
        scan("Check the building.", {}, model)
        specialist = [kw for kw in seen if "tools" not in kw and not kw.get("stream")]
        self.assertTrue(specialist)
        self.assertEqual(specialist[0]["max_output_tokens"], agent_app.SPECIALIST_MAX_OUTPUT_TOKENS)
        self.assertEqual(specialist[0]["reasoning"], {"effort": "low"})

    def test_cut_off_or_failed_specialist_is_retried_once(self):
        from openai import APIStatusError
        import httpx
        replies = {"GEN": ["cut off {", "ok"], "PWR": ["502", "ok"]}

        class Flaky(FakeModel):
            def create(self, **kw):
                code = kw.get("instructions", "").split("You are the ", 1)[-1].split(" agent", 1)[0]
                if code in replies and replies[code]:
                    what = replies[code].pop(0)
                    if what == "502":
                        raise APIStatusError("Flower Endeavor providers failed",
                                             response=httpx.Response(502, request=httpx.Request("POST", "http://x")), body=None)
                    if what.startswith("cut"):
                        return SimpleNamespace(output_text=what)
                return super().create(**kw)

        with unittest.mock.patch.object(agent_app.time, "sleep"):
            events, _ = scan("Check the building.", {}, Flaky())
        reports = {e["report"]["subsystem"]: e["report"] for e in of_type(events, "antibody.agent.report")}
        self.assertFalse(reports["GEN"]["failed"])
        self.assertFalse(reports["PWR"]["failed"])

    def test_a_fix_updates_the_report_without_another_model_call(self):
        model = FakeModel(risk={"HVAC": 0.5}, propose={"HVAC": {"tool": "hvac.reset_damper"}})
        seen = []
        original = model.create
        model.create = lambda **kw: (seen.append(kw), original(**kw))[1]
        events, _ = scan("Check the building.", {}, model)
        hvac_calls = [kw for kw in seen if "You are the HVAC agent" in kw.get("instructions", "")]
        self.assertEqual(len(hvac_calls), 1)
        report = next(e["report"] for e in of_type(events, "antibody.agent.report") if e["report"]["subsystem"] == "HVAC")
        self.assertLessEqual(report["risk_score"], 0.1)
        self.assertIn("Fixed by hvac.reset_damper", report["finding"])

    def test_alert_model_gets_only_the_top_reports(self):
        model = FakeModel(risk={"H2O": 0.9, "PWR": 0.8, "ELEC": 0.7})
        events, _ = scan("Check the building.", {}, model)
        payload = model.inputs[-1]["input"][-1]["content"]
        data = json.loads(payload.split("\n", 1)[1])
        ranked = [r["subsystem"] for r in of_type(events, "antibody.scan.ranked")[0]["ranked"]]
        self.assertEqual([r["subsystem"] for r in data["top_reports"]], ranked[:agent_app.TOP_ALERTS])
        text = "".join(e["delta"] for e in of_type(events, "response.output_text.delta"))
        self.assertEqual(sum(1 for line in text.splitlines() if line.startswith("| ") and line[2].isdigit()), 14)


GOVERNOR_SCRIPT = """
import json, sys
from agent import actions
from agent.building_data import SNAPSHOT
from agent.governance import AgentRecord, TRACE_DIR
record = AgentRecord("antibody-h2o", objective="t", scope=["h2o"], run_tag="t")
record.call("h2o.assess", lambda: None)
actions.act(record, "H2O", {"tool": "h2o.shut_valve"}, dict(SNAPSHOT["H2O"]))
actions.act(record, "H2O", {"tool": "elec.trip_breaker"}, dict(SNAPSHOT["H2O"]))
record.close()
(path,) = TRACE_DIR.glob("*antibody-h2o.jsonl")
print(json.dumps([json.loads(line) for line in path.read_text().splitlines()]))
"""


APPROVAL_SCRIPT = """
import json
from agent import actions
from agent.building_data import SNAPSHOT
from agent.governance import AgentRecord, TRACE_DIR, approved_record
record = AgentRecord("antibody-h2o", objective="t", scope=["h2o"], run_tag="t")
held = actions.act(record, "H2O", {"tool": "h2o.shut_valve"}, dict(SNAPSHOT["H2O"])).held
record.close()
item = {**held[0], "id": "A1", "approved_by": "Dana"}
approval = approved_record("antibody-h2o", ["h2o"], item, "t")
actions.execute_approved(approval, "H2O", item, dict(SNAPSHOT["H2O"]))
approval.close()
print(json.dumps({p.name: [json.loads(l) for l in p.read_text().splitlines()] for p in TRACE_DIR.glob("*.jsonl")}))
"""


class GovernorTest(unittest.TestCase):
    """Real Governor sessions with the repo's profiles, in a temporary home.

    Governor reads ~/.sentience when it is imported, so this runs in its own process.
    """

    def test_tier3_attempt_is_recorded_with_its_domain_type_and_flagged(self):
        if not governance.ENABLED:
            self.skipTest("Sentience Governor not installed")
        with tempfile.TemporaryDirectory() as home:
            shutil.copytree(REPO / "governance" / "profiles", Path(home, ".sentience", "profiles"))
            shutil.copy(REPO / "governance" / "resolution.yaml", Path(home, ".sentience"))
            env = {**os.environ, "HOME": home, "ANTIBODY_TRACE_DIR": str(Path(home, "traces"))}
            out = subprocess.run(
                [sys.executable, "-c", GOVERNOR_SCRIPT], env=env, cwd=REPO / "agent",
                capture_output=True, text=True, check=True,
            ).stdout
        events = json.loads(out.strip().splitlines()[-1])
        assess, shut, trip = [e for e in events if e["event_type"] == "SCOPE_ASSERTED"]
        self.assertEqual(assess["payload"]["operation_type"], "READ")
        self.assertEqual(shut["payload"]["operation_type"], "EXECUTE")  # from building_domain
        self.assertIn("HIGH_CONSEQUENCE_DETECTED", shut["advisory_flags"])
        self.assertIn("TASK_BOUNDARY_CROSSED", shut["advisory_flags"])  # read-only scan turned into a fix
        self.assertIn("SCOPE_INTENT_MISMATCH", trip["advisory_flags"])

    def test_approved_execution_carries_its_approval_and_the_proposal_does_not(self):
        if not governance.ENABLED:
            self.skipTest("Sentience Governor not installed")
        with tempfile.TemporaryDirectory() as home:
            shutil.copytree(REPO / "governance" / "profiles", Path(home, ".sentience", "profiles"))
            shutil.copy(REPO / "governance" / "resolution.yaml", Path(home, ".sentience"))
            env = {**os.environ, "HOME": home, "ANTIBODY_TRACE_DIR": str(Path(home, "traces"))}
            out = subprocess.run(
                [sys.executable, "-c", APPROVAL_SCRIPT], env=env, cwd=REPO / "agent",
                capture_output=True, text=True, check=True,
            ).stdout
        files = json.loads(out.strip().splitlines()[-1])

        def claim(name):
            (intent,) = [e for e in files[name] if e["event_type"] == "INTENT_DECLARED"]
            return intent["payload"]["authorization_claim"]

        def tools(name):
            return [e["payload"]["tool_id"] for e in files[name] if e["event_type"] == "SCOPE_ASSERTED"]

        self.assertIsNone(claim("t-antibody-h2o.jsonl"))  # held proposal: no approval
        self.assertEqual(claim("t-antibody-h2o-approved-A1.jsonl"), "approved by Dana (A1)")
        self.assertEqual(tools("t-antibody-h2o-approved-A1.jsonl"), ["h2o.shut_valve"])

    def test_closed_sessions_summarise_their_record_for_the_governor_pane(self):
        if not governance.ENABLED:
            self.skipTest("Sentience Governor not installed")
        script = APPROVAL_SCRIPT.replace(
            "print(json.dumps(",
            "from agent.governance import summaries, pane_line\n"
            "print(json.dumps([dict(s, line=pane_line(s)) for s in summaries()]))\n#",
        )
        with tempfile.TemporaryDirectory() as home:
            shutil.copytree(REPO / "governance" / "profiles", Path(home, ".sentience", "profiles"))
            shutil.copy(REPO / "governance" / "resolution.yaml", Path(home, ".sentience"))
            env = {**os.environ, "HOME": home, "ANTIBODY_TRACE_DIR": str(Path(home, "traces"))}
            out = subprocess.run(
                [sys.executable, "-c", script], env=env, cwd=REPO / "agent",
                capture_output=True, text=True, check=True,
            ).stdout
        held, approved = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(held["agent"], "antibody-h2o")
        self.assertIsNone(held["approval"])
        self.assertEqual(held["actions"][0]["tool"], "h2o.shut_valve")
        self.assertIn("HIGH_CONSEQUENCE_DETECTED", held["actions"][0]["flags"])
        self.assertNotIn("POL-003", held["line"])  # noise omitted
        self.assertEqual(approved["approval"], "approved by Dana (A1)")
        self.assertIn("approved by Dana (A1)", approved["line"])

    def test_bundled_profiles_govern_without_a_home_config_as_on_supergrid(self):
        if not governance.ENABLED:
            self.skipTest("Sentience Governor not installed")
        # The bundle is a copy of the repo's governance/: keep them identical
        bundle = REPO / "agent" / "agent" / "governance_profiles"
        for rel in ["resolution.yaml", *[f"profiles/{p.name}" for p in (REPO / "governance" / "profiles").glob("*.yaml")]]:
            src = REPO / "governance" / rel
            self.assertEqual((bundle / rel).read_text(), src.read_text(), f"governance_profiles/{rel} is out of date")
        with tempfile.TemporaryDirectory() as home:  # no ~/.sentience at all
            env = {**os.environ, "HOME": home, "ANTIBODY_TRACE_DIR": str(Path(home, "traces"))}
            out = subprocess.run(
                [sys.executable, "-c", GOVERNOR_SCRIPT], env=env, cwd=REPO / "agent",
                capture_output=True, text=True, check=True,
            ).stdout
        events = json.loads(out.strip().splitlines()[-1])
        (registered,) = [e for e in events if e["event_type"] == "AGENT_REGISTERED"]
        self.assertEqual(registered["payload"].get("profile_binding"), "antibody-*")
        shut = [e for e in events if e["event_type"] == "SCOPE_ASSERTED"][1]
        self.assertIn("HIGH_CONSEQUENCE_DETECTED", shut["advisory_flags"])


if __name__ == "__main__":
    unittest.main()
