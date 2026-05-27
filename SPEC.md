# Weekly Subscription Renewal Notifier

## Goal
An autonomous agent that runs weekly in GitHub Actions, queries my Notion
subscriptions database for renewals due in the current week (Mon–Sun),
and pushes a summary notification to my phone via ntfy.

## My configuration (fill these in before running)
- Notion database ID: `32ef9576bfcf8003b601c4e1b5e79329`
- Notion column for billing date (exact name, case-sensitive): `Next Billing Date`
- Notion column for subscription name (title property): `Name`
- Notion column for cost (exact name, case-sensitive): `Cost Per Unit`
- Notion column type for cost (`number` or `rich_text`): `number`
- Currency symbol to display (e.g. `$`, `€`, `£`): `$`
- ntfy topic name: `sub-notice18493adjwlxnsm`

## Architecture
1. Python script (`subscription_notifier.py`) does the work.
2. GitHub Actions workflow (`.github/workflows/weekly-subs.yml`) runs it
   on a cron schedule: Mondays at 14:00 UTC, plus manual `workflow_dispatch`.
3. Secrets stored in GitHub repo settings: `NOTION_TOKEN`,
   `NOTION_DATABASE_ID`, `NTFY_TOPIC`.

## Behavior
- Compute Monday and Sunday of the current week (using `date.today()` and
  `weekday()`).
- Query the Notion database via `POST /v1/databases/{id}/query` with a
  compound filter: date `on_or_after` Monday AND `on_or_before` Sunday.
  Sort ascending by date. Handle pagination via `has_more` / `next_cursor`.
- Use Notion API version `2022-06-28`.
- For each result page, extract:
  - `name` from the title property (concatenate `plain_text` of all title chunks)
  - `due` from the date property's `start` field
  - `cost` from the cost property:
    - If the column type is `number`: read `properties[<col>].number` (float or None).
    - If the column type is `rich_text`: concatenate `plain_text` of all chunks
      and attempt to parse as float (strip currency symbols and commas);
      fall back to the raw string if parsing fails.
  - Treat a missing/empty cost as `None` and render it as `—` in output.
- Send one ntfy notification:
  - If there are renewals: title `"N renewal(s) this week — <CURRENCY><total>"`
    where `<total>` is the sum of all numeric costs (omit the total if no
    costs could be parsed). Body is a bullet list:
    `• <name> — <Mon Jan 15> — <CURRENCY><cost>`.
    Use two decimal places for the cost (e.g. `$9.99`).
  - If there are none: a low-priority "nothing this week" message.
- ntfy POST goes to `https://ntfy.sh/{topic}` with `Title`, `Priority`,
  and `Tags` headers (suggested tags: `moneybag,calendar`).

## Implementation notes
- Use only `requests` from PyPI; no Notion SDK dependency needed.
- All config via env vars: `NOTION_TOKEN`, `NOTION_DATABASE_ID`,
  `NTFY_TOPIC`, optional `NTFY_SERVER` (default `https://ntfy.sh`),
  optional `DATE_PROPERTY`, optional `NAME_PROPERTY`,
  optional `COST_PROPERTY`, optional `COST_TYPE` (`number` or `rich_text`,
  default `number`), optional `CURRENCY` (default `$`).
- Raise on HTTP errors so failed runs surface in the Actions log.
- Python 3.12. Single file, no package structure needed.

## Workflow file requirements
- Triggers: `schedule` (cron `"0 14 * * 1"`) and `workflow_dispatch`.
- Steps: checkout, setup-python@v5 with pip cache, install requirements,
  run script with secrets injected as env vars. Also inject the optional
  column-name / cost-type / currency env vars from a single place near
  the top of the workflow so they're easy to edit.
- `timeout-minutes: 5`.

## Deliverables
1. `subscription_notifier.py`
2. `requirements.txt` (just `requests>=2.31`)
3. `.github/workflows/weekly-subs.yml`
4. `README.md` with setup steps: creating the Notion integration,
   connecting it to the database, finding the database ID, setting the
   three GitHub secrets, subscribing to the ntfy topic on phone, and
   triggering a manual test run from the Actions tab. Include a note on
   how to find the cost column type (open the database, click the column
   header — if it shows a number-format option it's `number`, otherwise
   treat it as `rich_text`).

## Out of scope (for now)
- Multiple notification channels.
- Persisting state between runs.
- Multi-currency handling (assumes one currency across all rows).