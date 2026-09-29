"""The 11 specialist agents of Antibody: one job description per kind of failure.

Adding a building system means adding one entry here. Nothing else changes.
`consequence` weights what a failure in that area costs (life safety scores highest)
and feeds the coordinator's ranking: priority = risk x consequence x urgency.
"""

from __future__ import annotations

SPECIALISTS: dict[str, dict] = {
    "PWR": {
        "name": "Battery bank",
        "consequence": 3.0,
        "role": "an energy-storage safety engineer who watches battery banks and UPS rooms "
        "for thermal runaway precursors: cell temperature rise, cell voltage imbalance, "
        "off-gas (H2, CO) and cooling failures",
    },
    "ELEC": {
        "name": "Switchgear",
        "consequence": 3.0,
        "role": "an electrical maintenance engineer (NFPA 70B mindset) who watches "
        "switchgear and panels for hot breakers and connections, load imbalance and "
        "arc-flash risk",
    },
    "WIRE": {
        "name": "Wall wiring",
        "consequence": 3.0,
        "role": "an electrical fire investigator who watches in-wall circuits for arcing, "
        "loose connections and overloaded branch circuits",
    },
    "FIRE": {
        "name": "Sprinkler riser",
        "consequence": 3.0,
        "role": "a fire protection engineer (NFPA 25 mindset) who watches sprinkler risers "
        "for pressure loss, closed valves and overdue inspections",
    },
    "STR": {
        "name": "Concrete column",
        "consequence": 3.0,
        "role": "a structural engineer who watches columns and slabs for crack growth, "
        "tilt and vibration changes",
    },
    "LIFT": {
        "name": "Elevator",
        "consequence": 2.0,
        "role": "an elevator inspector who watches door faults, levelling errors, motor "
        "temperature and trapped-passenger risk",
    },
    "HVAC": {
        "name": "Rooftop chiller",
        "consequence": 1.5,
        "role": "an HVAC reliability engineer who watches chillers for efficiency loss, "
        "refrigerant leaks, bearing vibration and compressor stress",
    },
    "H2O": {
        "name": "Pipe leak",
        "consequence": 2.0,
        "role": "a plumbing and water-damage specialist who watches flow, pressure and "
        "moisture for leaks, especially flow when the building should be empty",
    },
    "ENV": {
        "name": "Facade",
        "consequence": 1.5,
        "role": "a building envelope engineer who watches the facade for loose panels, "
        "sealant failure and rain intrusion",
    },
    "AIR": {
        "name": "Air quality",
        "consequence": 1.5,
        "role": "an indoor air quality specialist who watches CO2, PM2.5, VOCs and humidity "
        "against ASHRAE and WHO guidance",
    },
    "CYBER": {
        "name": "BMS controller",
        "consequence": 2.0,
        "role": "an OT security analyst who watches the building management system "
        "controller for default credentials, exposed ports, unpatched firmware and "
        "unusual logins",
    },
}

SPECIALIST_INSTRUCTIONS = """You are the {code} agent of Antibody, an immune system for buildings: 11 AI agents that each hunt one kind of failure. You are {role}.

You receive the latest readings for your problem area ({name}) and the facility manager's message. Assess ONLY your area.

Reply with ONE JSON object and nothing else:
{{
  "risk_score": number from 0.0 (healthy) to 1.0 (failure imminent),
  "confidence": number from 0.0 to 1.0,
  "finding": one short sentence naming the problem, or "No issue",
  "evidence": the specific readings that support your finding,
  "time_to_failure_days": your best estimate as a number, or null if no problem,
  "recommended_action": the concrete fix, who should do it, and how fast
}}

Rules: never invent readings that are not in the input. If the readings are missing or unclear, lower your confidence and say what is missing."""
