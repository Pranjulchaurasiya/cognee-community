"""Cognee Argo CD GitOps connector."""

from cognee_community_connector_argocd.argocd import (
    ARGOCD_APPLICATIONS_TABLE_NAME,
    ARGOCD_PROJECTS_TABLE_NAME,
    ARGOCD_SOURCE_NAME,
    argocd_source,
)
from cognee_community_connector_argocd.client import (
    ArgoCDAuthError,
    ArgoCDClient,
    ArgoCDError,
    ArgoCDNotFoundError,
)

__all__ = [
    "ARGOCD_APPLICATIONS_TABLE_NAME",
    "ARGOCD_PROJECTS_TABLE_NAME",
    "ARGOCD_SOURCE_NAME",
    "ArgoCDAuthError",
    "ArgoCDClient",
    "ArgoCDError",
    "ArgoCDNotFoundError",
    "argocd_source",
]
