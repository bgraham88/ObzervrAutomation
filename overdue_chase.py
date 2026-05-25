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


def fetch_overdue_invoices(acces
