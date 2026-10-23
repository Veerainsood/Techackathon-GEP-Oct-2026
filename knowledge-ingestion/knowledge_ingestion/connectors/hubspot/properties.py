"""HubSpot properties requested for each object type.

Only these properties are read from HubSpot. createdate and lastmodifieddate
show when each record was created or last changed in the CRM.
MODIFIED_PROPERTY is the property polling uses to find records changed since the last run.
"""

from __future__ import annotations

CONTACT_PROPERTIES = [
    "email",
    "firstname",
    "lastname",
    "company",
    "phone",
    "jobtitle",
    "lifecyclestage",
    "createdate",
    "lastmodifieddate",
]

COMPANY_PROPERTIES = ["name", "domain", "createdate", "hs_lastmodifieddate"]

OBJECT_PROPERTIES: dict[str, list[str]] = {
    "contacts": CONTACT_PROPERTIES,
    "companies": COMPANY_PROPERTIES,
}

MODIFIED_PROPERTY: dict[str, str] = {
    "contacts": "lastmodifieddate",
    "companies": "hs_lastmodifieddate",
}
