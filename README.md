# obzervr-ar-chase

Daily automation that DMs internal owners on Slack with their list of
overdue Obzervr invoices.

## How it works

1. GitHub Actions runs `overdue_chase.py` every weekday at 9am Brisbane time.
2. The script authenticates to Xero with a Custom Connection (client credentials).
3. It pulls all `AUTHORISED` ACCREC invoices with `DueDate < today`.
4. It groups invoices by customer, then looks up each customer's owner in `owners.yaml`.
5. It DMs each owner a formatted list of their overdue invoices via Slack.
6. Customers with no owner mapped are reported to the fallback Slack user.

## Files

- `overdue_chase.py` — the script
- `owners.yaml` — customer -> owner mapping (edit this to add new customers)
- `requirements.txt` — Python dependencies
- `.github/workflows/daily.yml` — schedule and runtime config

## Required GitHub secrets

Set under Settings -> Secrets and variables -> Actions:

- `XERO_CLIENT_ID`
- `XERO_CLIENT_SECRET`
- `XERO_TENANT_ID`
- `SLACK_BOT_TOKEN` (starts with `xoxb-`)
- `FALLBACK_SLACK_USER` — your Slack user ID or email. Receives the "unmapped" digest.

## Required GitHub variable

Set under Settings -> Secrets and variables -> Actions -> Variables tab:

- `DRY_RUN` — `true` to run without sending Slack DMs (default), `false` to go live.

## Running manually

GitHub -> Actions tab -> Daily AR Chase -> "Run workflow" button.

## Adding a new customer

Edit `owners.yaml` directly in the GitHub web UI (pencil icon), add the entry,
commit. The next run will pick it up — no deploy needed.
