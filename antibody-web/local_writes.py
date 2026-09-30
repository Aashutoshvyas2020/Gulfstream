"""Approved Notion and Slack writes, carried out on the operator's laptop.

Flower's Notion and Slack connectors are read-only in Flower 1.39, and a SuperGrid run
cannot hold secrets. So the agent only proposes the writes (Tier 3) and, once a human
approves one, emits an antibody.action with status "approved" and the write's details.
server.py sees that event and calls perform() here, which uses the operator's own
tokens from antibody-web/.env.local (git-ignored):

    ANTIBODY_NOTION_TOKEN        Notion internal integration secret (ntn_...), with the
                                 work-order database shared to that integration
    ANTIBODY_NOTION_DATABASE_ID  optional; found by title when unset
    ANTIBODY_NOTION_DATABASE     title to look for (default: Harbor Point work orders)
    ANTIBODY_SLACK_BOT_TOKEN     Slack bot token (xoxb-...) with chat:write, the bot
                                 invited to the channel
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

ENV_FILE = Path(__file__).resolve().parent / ".env.local"
NOTION_VERSION = "2022-06-28"
TIMEOUT = 20.0

WORK_ORDER_ACTION = "coordinator.create_work_order"
SLACK_REPLY_ACTION = "coordinator.notify_tenants"
ACTIONS = {WORK_ORDER_ACTION, SLACK_REPLY_ACTION}

_database: dict[str, str] = {}  # cached id and title property of the work-order database


def load_env(path: Path = ENV_FILE) -> None:
    """KEY=value lines from .env.local into the environment (existing values win)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _notion(method: str, path: str, token: str, body: dict | None = None) -> dict[str, Any]:
    res = httpx.request(
        method,
        f"https://api.notion.com/v1{path}",
        headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION},
        json=body,
        timeout=TIMEOUT,
    )
    data = res.json()
    if res.status_code >= 400:
        raise RuntimeError(f"Notion {res.status_code}: {data.get('message', data)}")
    return data


def _work_order_database(token: str) -> tuple[str, str]:
    """(database id, name of its title property)."""
    if _database:
        return _database["id"], _database["title"]
    db_id = os.environ.get("ANTIBODY_NOTION_DATABASE_ID", "")
    if not db_id:
        wanted = os.environ.get("ANTIBODY_NOTION_DATABASE", "Harbor Point work orders").lower()
        found = _notion("POST", "/search", token, {"filter": {"property": "object", "value": "database"}})
        databases = found.get("results", [])
        titled = {"".join(t.get("plain_text", "") for t in db.get("title", [])).lower(): db["id"] for db in databases}
        # The one whose title matches; else the only database shared with the integration.
        # An inline table created under a page may have an empty title of its own.
        db_id = next((i for t, i in titled.items() if wanted in t), "")
        if not db_id and len(databases) == 1:
            db_id = databases[0]["id"]
        if not db_id:
            raise RuntimeError("no Notion database shared with the integration; share "
                               "'Harbor Point work orders' via ••• > Connections")
    db = _notion("GET", f"/databases/{db_id}", token)
    title_prop = next((name for name, p in db.get("properties", {}).items() if p.get("type") == "title"), "Name")
    _database.update(id=db_id, title=title_prop)
    return db_id, title_prop


def create_work_order(args: dict[str, Any]) -> str:
    token = os.environ.get("ANTIBODY_NOTION_TOKEN")
    if not token:
        raise RuntimeError("ANTIBODY_NOTION_TOKEN is not set in antibody-web/.env.local")
    db_id, title_prop = _work_order_database(token)
    details = str(args.get("details", ""))[:1900]
    page = _notion("POST", "/pages", token, {
        "parent": {"database_id": db_id},
        "properties": {title_prop: {"title": [{"text": {"content": str(args.get("title", "Antibody work order"))[:190]}}]}},
        "children": [{"object": "block", "type": "paragraph",
                      "paragraph": {"rich_text": [{"type": "text", "text": {"content": details}}]}}],
    })
    return page.get("url", "created")


def reply_in_slack(args: dict[str, Any]) -> str:
    token = os.environ.get("ANTIBODY_SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("ANTIBODY_SLACK_BOT_TOKEN is not set in antibody-web/.env.local")
    res = httpx.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": args.get("channel"), "thread_ts": args.get("thread_ts"), "text": args.get("text", "")},
        timeout=TIMEOUT,
    )
    data = res.json()
    if not data.get("ok"):
        hint = " (invite the bot: /invite @Antibody in that channel)" if data.get("error") == "not_in_channel" else ""
        raise RuntimeError(f"Slack: {data.get('error', res.status_code)}{hint}")
    link = httpx.get("https://slack.com/api/chat.getPermalink",
                     headers={"Authorization": f"Bearer {token}"},
                     params={"channel": data.get("channel"), "message_ts": data.get("ts")}, timeout=TIMEOUT).json()
    return link.get("permalink") or "posted"


def perform(event: dict[str, Any]) -> dict[str, Any]:
    """Carry out one approved write; return the antibody.action result for the console."""
    action = event.get("action")
    result = {"kind": "antibody.action", "type": "antibody.action", "agent": "COORDINATOR",
              "action": action, "tier": 3, "approval_id": event.get("approval_id", "")}
    try:
        detail = create_work_order(event.get("args", {})) if action == WORK_ORDER_ACTION \
            else reply_in_slack(event.get("args", {}))
        return {**result, "status": "executed", "detail": detail}
    except Exception as exc:  # the scan goes on; the console shows why
        return {**result, "status": "failed", "detail": str(exc)[:300]}
