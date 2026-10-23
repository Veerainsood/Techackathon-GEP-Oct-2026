"""Source Gateway: uniform, permission-checked access to heterogeneous data sources."""

from udre.gateway.base import DataSourceAdapter, SQLSourceAdapter
from udre.gateway.registry import AccessContext, AccessDenied, SourceGateway

__all__ = ["DataSourceAdapter", "SQLSourceAdapter", "SourceGateway", "AccessContext", "AccessDenied"]
