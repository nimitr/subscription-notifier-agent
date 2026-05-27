#!/usr/bin/env python3
"""Weekly subscription renewal notifier: Notion -> ntfy.

High-level flow:
    1. Compute the date range for the current week (Mon–Sun, local to UTC).
    2. Query a Notion database for pages whose date property falls in that
       range, paginating through all results.
    3. Extract name / due date / cost from each page's properties, tolerating
       both `number` and `rich_text` cost columns.
    4. Build a single ntfy notification — a summary title plus a bullet-list
       body — and POST it to the configured topic.

All configuration is read from environment variables; see README.md for the
full list. The script is intended to be run from a GitHub Actions cron job,
so any HTTP failure is allowed to bubble up as a non-zero exit so the run
shows red in the Actions UI.
"""

import os
import re
import sys
from datetime import date, timedelta

import requests

# Notion API constants. The version header is required on every request and
# pins the response schema we're parsing against.
NOTION_API_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"


def week_bounds(today: date) -> tuple[date, date]:
    """Return (Monday, Sunday) of the ISO week containing `today`.

    `weekday()` returns 0 for Monday through 6 for Sunday, so subtracting
    that many days always lands on Monday regardless of which day we run on.
    """
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def query_notion(token: str, database_id: str, date_prop: str,
                 start: date, end: date) -> list[dict]:
    """Query the Notion database for pages whose `date_prop` is within [start, end].

    Notion paginates query results (max 100 per page), so we loop on
    `has_more` / `next_cursor` until every matching page has been collected.
    Results are sorted ascending by the date property so the final bullet
    list comes out in chronological order without extra sorting on our side.
    """
    url = f"{NOTION_BASE}/databases/{database_id}/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }
    # Compound `and` filter: due date must fall on or between Monday and Sunday.
    base_payload = {
        "filter": {
            "and": [
                {"property": date_prop, "date": {"on_or_after": start.isoformat()}},
                {"property": date_prop, "date": {"on_or_before": end.isoformat()}},
            ]
        },
        "sorts": [{"property": date_prop, "direction": "ascending"}],
    }
    results: list[dict] = []
    cursor: str | None = None
    while True:
        # Copy the base payload each iteration so the cursor from the previous
        # page doesn't leak into the request when we're on the final page.
        payload = dict(base_payload)
        if cursor:
            payload["start_cursor"] = cursor
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results


def extract_title(prop: dict) -> str:
    """Flatten a Notion title property into a plain string.

    Notion stores titles as a list of "rich text" chunks (one per styling
    run), so we concatenate the `plain_text` of each chunk to recover the
    human-visible title.
    """
    return "".join(chunk.get("plain_text", "") for chunk in prop.get("title", []))


def extract_date(prop: dict) -> str | None:
    """Pull the ISO `start` string out of a Notion date property, if present.

    Handles both shapes:
      - Plain date column:    properties[X].date.start
      - Formula returning a date: properties[X].formula.date.start
    """
    if prop.get("type") == "formula":
        f = prop.get("formula") or {}
        if f.get("type") == "date":
            d = f.get("date") or {}
            return d.get("start")
        return None
    d = prop.get("date") or {}
    return d.get("start")


def parse_cost_number(s: str) -> float | None:
    """Best-effort parse of a free-form cost string into a float.

    Strips anything that isn't a digit, dot, or minus sign (so `"$9.99"`,
    `"USD 9.99"`, `"9,99"` after comma removal all work). Returns None if
    nothing numeric is left, so callers can fall back to showing the raw
    text instead of a misleading 0.
    """
    cleaned = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
    if not cleaned or cleaned in ("-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_cost(prop: dict, cost_type: str):
    """Extract a cost from a Notion property, handling both supported types.

    Returns a `(numeric_value, display_string)` tuple where exactly one side
    is meaningful:
      - `(float, None)` when we got a usable number (either from a `number`
        column, or from parsing a `rich_text` value).
      - `(None, str)`  when the column is `rich_text` and the value couldn't
        be parsed — we keep the raw string so the user still sees something.
      - `(None, None)` when the cell is empty.
    """
    if cost_type == "number":
        v = prop.get("number")
        if v is None:
            return None, None
        return float(v), None
    # rich_text path: concatenate chunks like we do for titles, then try to
    # parse out a number. If parsing fails, surface the original text so the
    # user at least sees what's in Notion rather than a silent "—".
    text = "".join(chunk.get("plain_text", "") for chunk in prop.get("rich_text", []))
    text = text.strip()
    if not text:
        return None, None
    parsed = parse_cost_number(text)
    if parsed is None:
        return None, text
    return parsed, None


def format_date(iso: str) -> str:
    """Render an ISO date (or datetime) as a short human string like 'Mon Jan 15'.

    Notion may return either a bare `YYYY-MM-DD` or a full ISO timestamp; we
    only need the date portion, so we slice off the first 10 characters.
    Falls back to the original string if parsing fails so we never crash on
    an unexpected format.
    """
    try:
        y, m, d = iso[:10].split("-")
        dt = date(int(y), int(m), int(d))
    except Exception:
        return iso
    return dt.strftime("%a %b %d")


def build_message(items: list[dict], currency: str) -> tuple[str, str, str]:
    """Compose the ntfy (title, body, priority) for this week's renewals.

    Behavior:
      - Empty week  -> a low-priority "nothing scheduled" notification so it
        doesn't buzz the phone.
      - Non-empty   -> title includes a count and (if any numeric costs
        exist) a summed total; body is a bullet list, one renewal per line.
    """
    if not items:
        return ("No renewals this week", "Nothing scheduled this week.", "low")

    # Only sum costs we actually parsed as numbers; mixing in `None` would
    # crash, and silently treating missing values as 0 would understate
    # spend.
    numeric_costs = [it["cost_num"] for it in items if it["cost_num"] is not None]
    n = len(items)
    if numeric_costs:
        total = sum(numeric_costs)
        # ASCII-only here: this string becomes an HTTP header (ntfy `Title`),
        # which must be latin-1 encodable. Em-dashes etc. crash requests.
        title = f"{n} renewal(s) this week - {currency}{total:.2f}"
    else:
        # Suppress the total when nothing parsed — showing "$0.00" would be
        # misleading if costs exist but couldn't be read.
        title = f"{n} renewal(s) this week"

    lines = []
    for it in items:
        # Cost rendering precedence: parsed number > raw display string > em-dash.
        if it["cost_num"] is not None:
            cost_str = f"{currency}{it['cost_num']:.2f}"
        elif it["cost_display"]:
            cost_str = it["cost_display"]
        else:
            cost_str = "—"
        date_str = format_date(it["due"]) if it["due"] else "—"
        lines.append(f"• {it['name']} — {date_str} — {cost_str}")
    return (title, "\n".join(lines), "default")


def send_ntfy(server: str, topic: str, title: str, body: str, priority: str) -> None:
    """POST the notification to ntfy.

    ntfy uses request *headers* (not JSON) for metadata like title and
    priority, with the message body as the raw POST body. Encoded as UTF-8
    so non-ASCII subscription names survive the trip.
    """
    url = f"{server.rstrip('/')}/{topic}"
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": "moneybag,calendar",
    }
    resp = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=30)
    resp.raise_for_status()


def require_env(name: str) -> str:
    """Fetch a required env var or exit with code 2 if it's missing/empty."""
    v = os.environ.get(name)
    if not v:
        print(f"Missing required env var: {name}", file=sys.stderr)
        sys.exit(2)
    return v


def main() -> None:
    """Entry point: read config, query Notion, send the ntfy notification."""
    # --- Required configuration (must be provided via secrets / env) -------
    token = require_env("NOTION_TOKEN")
    database_id = require_env("NOTION_DATABASE_ID")
    topic = require_env("NTFY_TOPIC")

    # --- Optional configuration (sensible defaults for my Notion DB) -------
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    date_prop = os.environ.get("DATE_PROPERTY", "Next Billing Date")
    name_prop = os.environ.get("NAME_PROPERTY", "Name")
    cost_prop = os.environ.get("COST_PROPERTY", "Cost Per Unit")
    cost_type = os.environ.get("COST_TYPE", "number").lower()
    currency = os.environ.get("CURRENCY", "$")

    # Fail fast on a typo'd COST_TYPE rather than silently going down the
    # wrong extraction branch and producing empty costs.
    if cost_type not in ("number", "rich_text"):
        print(f"COST_TYPE must be 'number' or 'rich_text', got: {cost_type}",
              file=sys.stderr)
        sys.exit(2)

    # --- 1. Compute the week window ---------------------------------------
    monday, sunday = week_bounds(date.today())
    print(f"Querying renewals from {monday} to {sunday}")

    # --- 2. Fetch matching pages from Notion ------------------------------
    pages = query_notion(token, database_id, date_prop, monday, sunday)
    print(f"Notion returned {len(pages)} page(s)")

    # --- 3. Reshape Notion's verbose property objects into flat dicts -----
    items = []
    for page in pages:
        props = page.get("properties", {})
        name = extract_title(props.get(name_prop, {})) or "(untitled)"
        due = extract_date(props.get(date_prop, {}))
        cost_num, cost_display = extract_cost(props.get(cost_prop, {}), cost_type)
        items.append({
            "name": name,
            "due": due,
            "cost_num": cost_num,
            "cost_display": cost_display,
        })

    # --- 4. Build the message and ship it ---------------------------------
    title, body, priority = build_message(items, currency)
    # Log the outgoing message so it's visible in the Actions run log — handy
    # for debugging without having to re-query Notion.
    print(f"Sending ntfy: {title}")
    print(body)
    send_ntfy(server, topic, title, body, priority)
    print("Done.")


if __name__ == "__main__":
    main()
