"""Source Gateway: uniform, permission-checked access to heterogeneous data sources."""

from backend.gateway.base import DataSourceAdapter, SQLSourceAdapter
from backend.gateway.registry import AccessContext, AccessDenied, SourceGateway

__all__ = ["DataSourceAdapter", "SQLSourceAdapter", "SourceGateway", "AccessContext", "AccessDenied"]
