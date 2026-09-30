"""Tenant and facilities messages from Slack, matched to the problem areas.

Slack search needs a query, so the coordinator searches the main words of each at-risk
area ("leak", "water" for H2O, "battery" for PWR, ...) through Flower's Slack connector
(`slack_search_messages`, read-only in Flower 1.39) and matches the messages it finds
with the same keywords as the Notion reports. Done in code, like the Notion lookup.
"""

from __future__ import annotations

import json
from typing import Any

from .notion_reports import match

SEARCH_TOOL = "slack_search_messages"
MAX_AREAS = 4  # at-risk areas searched per scan, highest priority first
RESULTS_PER_QUERY = 10

# Area -> the words searched for it (a subset of notion_reports.KEYWORDS)
QUERIES: dict[str, tuple[str, ...]] = {
    "H2O": ("leak", "water"),
    "PWR": ("battery",),
    "ELEC": ("breaker",),
    "WIRE": ("outlet",),
    "FIRE": ("sprinkler",),
    "STR": ("crack",),
    "LIFT": ("elevator",),
    "HVAC": ("hot", "cold"),
    "ENV": ("facade", "window"),
    "AIR": ("stuffy",),
    "CYBER": ("bms",),
    "GEN": ("generator",),
    "GAS": ("gas",),
    "EGRESS": ("exit", "stair"),
}


def queries(ranked: list[dict[str, Any]]) -> list[str]:
    """The words to search this scan: the at-risk areas' words, no repeats."""
    words: list[str] = []
    at_risk = [r for r in ranked if not r["failed"] and r["risk_score"] >= 0.3][:MAX_AREAS]
    for report in at_risk:
        for word in QUERIES.get(report["subsystem"], ()):
            if word not in words:
                words.append(word)
    return words


def search_call(query: str, index: int) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": f"slack-reports-{index}",
        "name": SEARCH_TOOL,
        "arguments": json.dumps({"query": query, "count": RESULTS_PER_QUERY, "sort": "timestamp"}),
    }


def messages(output: Any) -> list[dict[str, str]]:
    """(title, url) per message in a slack_search_messages result; title is the message text."""
    try:
        data = json.loads(output) if isinstance(output, str) else output
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict) or "error" in data or data.get("ok") is False:
        return []
    matches = (data.get("messages") or {}).get("matches") or []
    found = []
    for item in matches if isinstance(matches, list) else []:
        if isinstance(item, dict) and item.get("text"):
            channel = (item.get("channel") or {}).get("name", "")
            text = " ".join(str(item["text"]).split())[:160]
            found.append({"title": f"#{channel}: {text}" if channel else text,
                          "url": str(item.get("permalink", "")),
                          # where a reply goes: the message's channel and timestamp (thread)
                          "channel_id": str((item.get("channel") or {}).get("id", "")),
                          "ts": str(item.get("ts", ""))})
    return found


def match_messages(found: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    """Area -> matching messages, each message once per area."""
    unique = list({m["url"] or m["title"]: m for m in found}.values())
    return match(unique)
