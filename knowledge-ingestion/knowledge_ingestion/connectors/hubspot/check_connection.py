"""Read-only smoke test: confirms the HubSpot token works and counts contacts and companies.

Run from the project root:
    python -m knowledge_ingestion.connectors.hubspot.check_connection

Never prints the token. Makes GET requests only.
"""

from __future__ import annotations

import sys

from knowledge_ingestion.config import config
from knowledge_ingestion.connectors.hubspot.client import HubSpotClient, HubSpotError
from knowledge_ingestion.connectors.hubspot.properties import (
    COMPANY_PROPERTIES,
    CONTACT_PROPERTIES,
)

PREVIEW_COUNT = 5


def main() -> int:
    print(f"Checking HubSpot read access for portal {config.hubspot.portal_id}...\n")

    try:
        with HubSpotClient() as client:
            contacts = client.list_all("contacts", CONTACT_PROPERTIES)
            print(f"contacts: {len(contacts)} total")
            for record in contacts[:PREVIEW_COUNT]:
                print(f"  id={record.id} {record.properties}")
            if len(contacts) > PREVIEW_COUNT:
                print(f"  ... {len(contacts) - PREVIEW_COUNT} more not shown")
            print("")

            companies = client.list_all("companies", COMPANY_PROPERTIES)
            print(f"companies: {len(companies)} total\n")
    except HubSpotError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1

    print("OK: token has read access to contacts and companies.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
