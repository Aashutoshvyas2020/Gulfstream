"""Simulated sensor snapshot for the demo building (100,000 sq ft office, 7 floors).

These readings are demo data, not a real building. Four areas carry a planted problem
(PWR, ELEC, H2O, STR) and one carries a security weakness (CYBER). Three carry small
faults a bot or an inspector can clear: a stuck chiller damper (HVAC), a tripped
generator battery charger (GEN) and failed exit-sign self-tests (EGRESS). The rest are
healthy. Actions in actions.py change these readings as simulated effects.
Override any area at chat time by pasting JSON, e.g. {"H2O": {"night_flow_lpm_building_empty": 12}}.
"""

from __future__ import annotations

BUILDING = {
    "name": "Harbor Point Tower (demo)",
    "type": "office",
    "floors": 7,
    "area_sqft": 100000,
    "snapshot_time": "2026-09-29T02:10:00-07:00",
}

SNAPSHOT: dict[str, dict] = {
    "PWR": {
        "location": "B1 UPS battery room, string 2",
        "cell_temp_c_now": 41.5,
        "cell_temp_c_24h_ago": 33.0,
        "room_temp_c": 24.0,
        "max_cell_voltage_imbalance_mv": 85,
        "hydrogen_ppm": 180,
        "room_exhaust_fan": "running at 40% (fault code F12)",
    },
    "ELEC": {
        "location": "Main switchgear MSB-1, feeder breaker 4",
        "breaker_temp_c": 78,
        "adjacent_breakers_temp_c": [38, 41, 39],
        "load_percent_of_rating": 72,
        "last_infrared_scan": "2025-06-12",
    },
    "WIRE": {
        "location": "Floors 1-7 branch circuits",
        "arc_fault_events_7d": 0,
        "max_circuit_load_percent": 61,
        "outlet_temp_anomalies": 0,
    },
    "FIRE": {
        "location": "Sprinkler riser R-1",
        "static_pressure_psi": 68,
        "baseline_pressure_psi": 70,
        "control_valves_open": True,
        "last_nfpa25_inspection": "2026-03-04",
    },
    "STR": {
        "location": "Parking level P1, column C-7",
        "crack_width_mm_now": 0.42,
        "crack_width_mm_90d_ago": 0.18,
        "column_tilt_deg": 0.02,
        "vibration_change_percent": 3,
    },
    "LIFT": {
        "location": "Elevators 1 and 2",
        "door_faults_7d": 2,
        "levelling_error_mm": 4,
        "motor_temp_c": 52,
    },
    "HVAC": {
        "location": "Rooftop chiller CH-1",
        "kw_per_ton": 0.68,
        "design_kw_per_ton": 0.62,
        "refrigerant_pressure_trend": "stable",
        "bearing_vibration_mm_s": 2.1,
        "damper_position_percent": 20,
        "damper_commanded_percent": 60,
    },
    "H2O": {
        "location": "Floor 4 east riser",
        "night_flow_lpm_building_empty": 6.5,
        "normal_night_flow_lpm": 0.3,
        "ceiling_moisture_sensor_4E": "wet since 01:20",
        "pressure_drop_psi_last_6h": 4,
    },
    "ENV": {
        "location": "West facade",
        "loose_panel_alerts": 0,
        "rain_intrusion_events_30d": 1,
        "sealant_age_years": 9,
    },
    "AIR": {
        "location": "Floors 1-7 average",
        "co2_ppm": 720,
        "pm25_ug_m3": 8,
        "tvoc_ppb": 210,
        "relative_humidity_percent": 46,
    },
    "CYBER": {
        "location": "BMS supervisory controller",
        "default_admin_password": True,
        "open_ports_to_internet": [47808],
        "firmware_age_months": 26,
        "failed_logins_24h": 37,
    },
    "GEN": {
        "location": "B1 generator room, 500 kW diesel standby generator",
        "starting_battery_voltage_v": 11.6,
        "battery_charger": "tripped (overcurrent)",
        "fuel_level_percent": 78,
        "last_monthly_load_test": "2026-08-02",
        "transfer_switch_faults_30d": 0,
    },
    "GAS": {
        "location": "B1 boiler room and parking level P1",
        "boiler_room_ch4_percent_lel": 1,
        "garage_co_ppm": 14,
        "garage_exhaust_fans": "running, auto mode",
    },
    "EGRESS": {
        "location": "Stairs A and B, floors 1-7",
        "exit_signs_failed_self_test": 2,
        "emergency_lights_battery_fail": 1,
        "fire_doors_propped_open": ["Stair B, floor 3"],
        "last_annual_90_min_test": "2026-02-11",
    },
}
