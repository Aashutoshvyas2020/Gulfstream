"""Tenant reports and work orders from Notion, matched to the problem areas.

The coordinator calls Flower's Notion connector (`notion_search`, read-only in Flower
1.39) once per scan and matches the page titles it gets back against keywords for each
area. A title that names a problem ("Tenant 4E: water stain on ceiling near east riser")
is a human report; when it matches an area the sensors also flag, it confirms the
finding. Notion search matches titles only, so reports should say what is wrong in their
title.

Done in code rather than left to the model: on SuperGrid, Endeavor has not decided on
tool calls inside the investigation's time limit, and this lookup takes seconds.
"""

from __future__ import annotations

import json
import re
from typing import Any

SEARCH_TOOL = "notion_search"
MAX_PAGES = 50

# Area -> words in a report title that point at it (matched as whole words, any case)
KEYWORDS: dict[str, tuple[str, ...]] = {
    "H2O": ("leak", "leaking", "water", "stain", "drip", "dripping", "flood", "wet", "damp", "riser", "pipe"),
    "PWR": ("battery", "ups", "hydrogen", "exhaust"),
    "ELEC": ("breaker", "switchgear", "electrical", "burning", "panel"),
    "WIRE": ("outlet", "outlets", "flicker", "flickering", "spark", "sparking", "wiring"),
    "FIRE": ("sprinkler", "sprinklers", "fire alarm"),
    "STR": ("crack", "cracks", "column", "concrete", "parking"),
    "LIFT": ("elevator", "elevators", "lift"),
    "HVAC": ("chiller", "hvac", "too hot", "too cold", "temperature", "air conditioning", "damper"),
    "ENV": ("facade", "window", "windows", "rain"),
    "AIR": ("stuffy", "air quality", "odor", "odour", "co2"),
    "CYBER": ("bms", "login", "logins", "password", "network", "controller"),
    "GEN": ("generator",),
    "GAS": ("gas", "carbon monoxide", "fumes", "boiler"),
    "EGRESS": ("exit sign", "exit signs", "fire door", "emergency light", "emergency lights", "stair", "stairwell"),
}
_PATTERNS = {
    area: re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)
    for area, words in KEYWORDS.items()
}


def search_call(call_id: str = "notion-reports") -> dict[str, Any]:
    """A function_call for Flower's Notion search: every page shared with the connection."""
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": SEARCH_TOOL,
        "arguments": json.dumps({"filter": {"property": "object", "value": "page"}, "page_size": MAX_PAGES}),
    }


def _title(page: dict[str, Any]) -> str:
    for prop in (page.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(part.get("plain_text", "") for part in prop.get("title") or []).strip()
    return ""


def pages(output: Any) -> list[dict[str, str]]:
    """(title, url) of each page in a notion_search result; tolerant of shape changes."""
    try:
        data = json.loads(output) if isinstance(output, str) else output
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and "error" in data:
        return []
    results = data.get("results", []) if isinstance(data, dict) else []
    found = []
    for page in results if isinstance(results, list) else []:
        if isinstance(page, dict):
            title = _title(page)
            if title:
                found.append({"title": title, "url": str(page.get("url", ""))})
    return found


def match(found: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    """Area -> the reports whose title points at it. A title can match several areas."""
    matched: dict[str, list[dict[str, str]]] = {}
    for page in found:
        for area, pattern in _PATTERNS.items():
            if pattern.search(page["title"]):
                matched.setdefault(area, []).append(page)
    return matched


def section(
    matched: dict[str, list[dict[str, str]]],
    ranked: list[dict[str, Any]],
    heading: str = "Notion: tenant reports and work orders",
) -> str:
    """An alert section of human reports, in ranked order; says when one confirms a finding."""
    if not matched:
        return ""
    lines = [f"**{heading}**"]
    for report in ranked:
        for page in matched.get(report["subsystem"], []):
            at_risk = not report["failed"] and report["risk_score"] >= 0.3
            verdict = "confirms the sensor finding" if at_risk else "sensors show no problem here yet"
            link = f" ([open]({page['url']}))" if page["url"] else ""
            lines.append(f"- {report['subsystem']}: \"{page['title']}\"{link}: {verdict}")
    return "\n".join(lines) + "\n"
