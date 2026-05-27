# Weekly Subscription Renewal Notifier

A small autonomous agent that runs every Monday in GitHub Actions, queries a
Notion database for subscription renewals due this week (Mon–Sun), and pushes a
summary notification to your phone via [ntfy](https://ntfy.sh).

## Setup

### 1. Create a Notion integration

1. Go to <https://www.notion.so/profile/integrations>.
2. Click **New integration**, give it a name (e.g. `subscription-notifier`), and
   pick the workspace that holds your subscriptions database.
3. Under **Capabilities**, only **Read content** is required.
4. Copy the **Internal Integration Secret** — this is your `NOTION_TOKEN`. It
   starts with `secret_` or `ntn_`.

### 2. Connect the integration to your database

1. Open your subscriptions database in Notion.
2. Click the `•••` menu in the top-right → **Connections** → **Connect to** →
   pick the integration you just created.

Without this step, the integration cannot see the database even with a valid token.

### 3. Find the database ID

Open the database as a full page. The URL looks like:

```
https://www.notion.so/<workspace>/<DATABASE_ID>?v=<view_id>
```

The `DATABASE_ID` is a 32-character hex string (often shown with dashes). Use
that value as `NOTION_DATABASE_ID`.

### 4. Find the cost column type

Open the database, click the **Cost Per Unit** column header:

- If you see a **number format** option (currency, percent, etc.), the column
  type is `number` — leave `COST_TYPE: "number"` in the workflow file.
- Otherwise (plain text), treat it as `rich_text` and change `COST_TYPE` in
  `.github/workflows/weekly-subs.yml` accordingly.

If the column names in your database differ from the defaults (`Name`,
`Next Billing Date`, `Cost Per Unit`), edit the `env:` block at the top of the
workflow file to match — names are case-sensitive.

### 5. Set GitHub secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add three:

| Name                 | Value                                          |
| -------------------- | ---------------------------------------------- |
| `NOTION_TOKEN`       | The integration secret from step 1.            |
| `NOTION_DATABASE_ID` | The database ID from step 3.                   |
| `NTFY_TOPIC`         | A hard-to-guess topic name (see step 6).       |

### 6. Subscribe to the ntfy topic on your phone

1. Install the **ntfy** app ([iOS](https://apps.apple.com/us/app/ntfy/id1625396347)
   / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
2. Tap **Subscribe to topic**, enter the same topic name you put in
   `NTFY_TOPIC`, and leave the server as `ntfy.sh`.

> ntfy topics are public — anyone who guesses the name can read messages and
> publish to it. Use something long and random.

### 7. Trigger a manual test run

1. In your repo, open the **Actions** tab.
2. Pick **Weekly subscription renewals** in the left sidebar.
3. Click **Run workflow** → **Run workflow**.
4. Wait ~30 seconds and check your phone.

After this, it will fire automatically every Monday at 14:00 UTC.

## Configuration reference

All configuration is via env vars (set in the workflow file or as secrets):

| Variable             | Required | Default              | Notes                              |
| -------------------- | -------- | -------------------- | ---------------------------------- |
| `NOTION_TOKEN`       | yes      | —                    | Integration secret.                |
| `NOTION_DATABASE_ID` | yes      | —                    | 32-char database ID.               |
| `NTFY_TOPIC`         | yes      | —                    | Your ntfy topic.                   |
| `NTFY_SERVER`        | no       | `https://ntfy.sh`    | For self-hosted ntfy.              |
| `DATE_PROPERTY`      | no       | `Next Billing Date`  | Notion date column name.           |
| `NAME_PROPERTY`      | no       | `Name`               | Notion title column name.          |
| `COST_PROPERTY`      | no       | `Cost Per Unit`      | Notion cost column name.           |
| `COST_TYPE`          | no       | `number`             | `number` or `rich_text`.           |
| `CURRENCY`           | no       | `$`                  | Symbol prepended to costs.         |

## Local test

```bash
pip install -r requirements.txt
export NOTION_TOKEN=secret_...
export NOTION_DATABASE_ID=...
export NTFY_TOPIC=...
python subscription_notifier.py
```
