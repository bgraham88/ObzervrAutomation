"""
Obzervr Overdue Invoice Chase
-----------------------------
Pulls overdue invoices from Xero, groups them by customer, looks up the
internal owner for each customer in owners.yaml, and DMs that owner via
Slack with their list.

Runs daily on weekday mornings via GitHub Actions.

Set DRY_RUN=true to print messages without actually sending.
"""

import os
import sys
import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

import requests
import yaml

# -------------------------------------------------------------------
# Config from environment
# -------------------------------------------------------------------
XERO_CLIENT_ID = os.environ["XERO_CLIENT_ID"]
XERO_CLIENT_SECRET = os.environ["XERO_CLIENT_SECRET"]
XERO_TENANT_ID = os.environ["XERO_TENANT_ID"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
FALLBACK_SLACK_USER = os.environ.get("FALLBACK_SLACK_USER", "")  # your own user ID or email
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

OWNERS_FILE = "owners.yaml"


# -------------------------------------------------------------------
# Xero
# -------------------------------------------------------------------
def get_xero_token() -> str:
    """Exchange client credentials for an access token."""
    resp = requests.post(
        "https://identity.xero.com/connect/token",
        auth=(XERO_CLIENT_ID, XERO_CLIENT_SECRET),
        data={"grant_type": "client_credentials", "scope": "accounting.invoices.read accounting.contacts.read"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_overdue_invoices(access_token: str) -> list[dict]:
    """Return all AUTHORISED invoices with DueDate before today."""
    today = date.today().strftime("%Y, %m, %d")
    where_clause = f'Status=="AUTHORISED" AND Type=="ACCREC" AND DueDate<DateTime({today})'

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": XERO_TENANT_ID,
        "Accept": "application/json",
    }

    invoices = []
    page = 1
    while True:
        resp = requests.get(
            "https://api.xero.com/api.xro/2.0/Invoices",
            headers=headers,
            params={"where": where_clause, "page": page, "pageSize": 100},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json().get("Invoices", [])
        if not batch:
            break
        invoices.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    # Defensive: filter again client-side and exclude anything with AmountDue == 0
    today_iso = date.today()
    overdue = []
    for inv in invoices:
        if inv.get("Status") != "AUTHORISED":
            continue
        amount_due = Decimal(str(inv.get("AmountDue", 0)))
        if amount_due <= 0:
            continue
        due_date = parse_xero_date(inv.get("DueDateString") or inv.get("DueDate"))
        if due_date is None or due_date >= today_iso:
            continue
        inv["_due_date"] = due_date
        inv["_amount_due"] = amount_due
        inv["_days_overdue"] = (today_iso - due_date).days
        overdue.append(inv)
    return overdue


def parse_xero_date(s: str | None):
    """Xero returns dates either as '2025-04-15T00:00:00' or '/Date(1681516800000+0000)/'."""
    if not s:
        return None
    if s.startswith("/Date("):
        ms = int(s[6:].split("+")[0].split("-")[0])
        return datetime.utcfromtimestamp(ms / 1000).date()
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except ValueError:
        return None


# -------------------------------------------------------------------
# Owner mapping
# -------------------------------------------------------------------
def load_owners() -> list[dict]:
    with open(OWNERS_FILE) as f:
        data = yaml.safe_load(f) or []
    return data


def find_owner(contact_name: str, contact_id: str, owners: list[dict]) -> dict | None:
    """Match by ContactID first (robust), then by name (case-insensitive)."""
    for o in owners:
        if o.get("contact_id") and contact_id and o["contact_id"] == contact_id:
            return o
    for o in owners:
        if o.get("name", "").strip().lower() == (contact_name or "").strip().lower():
            return o
    return None


# -------------------------------------------------------------------
# Slack
# -------------------------------------------------------------------
def slack_call(method: str, payload: dict) -> dict:
    resp = requests.post(
        f"https://slack.com/api/{method}",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps(payload),
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Slack {method} failed: {body}")
    return body


def resolve_slack_user(owner: dict) -> str | None:
    """Return a Slack user ID. Use slack_user_id if provided, else look up by email."""
    if owner.get("slack_user_id"):
        return owner["slack_user_id"]
    if owner.get("email"):
        try:
            res = slack_call("users.lookupByEmail", {"email": owner["email"]})
            return res["user"]["id"]
        except Exception as e:
            print(f"  WARN: could not resolve Slack user for {owner.get('email')}: {e}")
            return None
    return None


def send_dm(slack_user_id: str, text: str) -> None:
    if DRY_RUN:
        print(f"  [DRY RUN] Would DM {slack_user_id}:\n{text}\n")
        return
    # Open DM channel, then post
    im = slack_call("conversations.open", {"users": slack_user_id})
    channel = im["channel"]["id"]
    slack_call("chat.postMessage", {"channel": channel, "text": text, "unfurl_links": False})


# -------------------------------------------------------------------
# Message formatting
# -------------------------------------------------------------------
def format_amount(d: Decimal) -> str:
    return f"${d:,.2f}"


def format_owner_message(owner_label: str, customers: dict) -> str:
    total = sum(
        sum(inv["_amount_due"] for inv in invs) for invs in customers.values()
    )
    lines = [
        f"Good morning {owner_label} :wave:",
        "",
        f"You have *{format_amount(total)}* in overdue invoices across {len(customers)} customer(s). Please follow up today.",
        "",
    ]
    for customer_name, invs in sorted(customers.items()):
        customer_total = sum(inv["_amount_due"] for inv in invs)
        lines.append(f"*{customer_name}* — {format_amount(customer_total)} total")
        for inv in sorted(invs, key=lambda i: i["_days_overdue"], reverse=True):
            lines.append(
                f"  • {inv.get('InvoiceNumber', '(no number)')} — "
                f"{format_amount(inv['_amount_due'])} — "
                f"{inv['_days_overdue']} days overdue (due {inv['_due_date'].isoformat()})"
            )
        lines.append("")
    lines.append("_Sent automatically by the AR chase bot. Reply to Brett with any issues._")
    return "\n".join(lines)


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
def main():
    print(f"Run mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print("Fetching Xero token...")
    token = get_xero_token()

    print("Fetching overdue invoices...")
    invoices = fetch_overdue_invoices(token)
    print(f"Found {len(invoices)} overdue invoice(s).")

    if not invoices:
        print("Nothing to chase. Exiting.")
        return

    owners = load_owners()
    print(f"Loaded {len(owners)} owner mappings.")

    # Group invoices: owner -> customer -> [invoices]
    by_owner: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    unmapped: dict[str, list[dict]] = defaultdict(list)

    for inv in invoices:
        contact = inv.get("Contact", {})
        contact_name = contact.get("Name", "(unknown)")
        contact_id = contact.get("ContactID", "")
        owner = find_owner(contact_name, contact_id, owners)
        if owner:
            key = owner.get("email") or owner.get("name") or "unknown"
            by_owner[key][contact_name].append(inv)
            owner_label_cache[key] = owner
        else:
            unmapped[contact_name].append(inv)

    # Send a DM to each owner
    for owner_key, customers in by_owner.items():
        owner = owner_label_cache[owner_key]
        owner_label = owner.get("display_name") or owner.get("email") or "there"
        slack_user_id = resolve_slack_user(owner)
        if not slack_user_id:
            print(f"  Skipping {owner_key} — no Slack user resolved.")
            continue
        msg = format_owner_message(owner_label, customers)
        print(f"Sending DM to {owner_label} ({slack_user_id})...")
        send_dm(slack_user_id, msg)

    # Send unmapped to fallback user (you)
    if unmapped and FALLBACK_SLACK_USER:
        lines = [
            ":warning: *Overdue invoices with no owner mapped:*",
            "",
        ]
        for customer_name, invs in sorted(unmapped.items()):
            total = sum(inv["_amount_due"] for inv in invs)
            lines.append(f"• *{customer_name}* — {format_amount(total)} across {len(invs)} invoice(s)")
        lines.append("")
        lines.append("Add these customers to `owners.yaml` so they get chased.")
        fallback_id = resolve_slack_user({"slack_user_id": FALLBACK_SLACK_USER} if FALLBACK_SLACK_USER.startswith("U") else {"email": FALLBACK_SLACK_USER})
        if fallback_id:
            send_dm(fallback_id, "\n".join(lines))

    print("Done.")


owner_label_cache: dict[str, dict] = {}


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)
