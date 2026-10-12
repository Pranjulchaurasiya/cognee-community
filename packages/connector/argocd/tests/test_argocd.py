"""Unit tests for Cognee Argo CD GitOps connector (100% offline)."""

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

try:
    from cognee.tasks.ingestion.dlt_utils import DOCUMENT_SOURCE_ATTR
except ImportError:
    DOCUMENT_SOURCE_ATTR = "cognee_document_source"
from cognee_community_connector_argocd import (
    ARGOCD_APPLICATIONS_TABLE_NAME,
    ARGOCD_PROJECTS_TABLE_NAME,
    ARGOCD_SOURCE_NAME,
    ArgoCDAuthError,
    ArgoCDClient,
    ArgoCDNotFoundError,
    argocd_source,
)
from cognee_community_connector_argocd.argocd import (
    _application_to_document,
    _project_to_document,
)


@pytest.fixture
def sample_application() -> dict[str, Any]:
    return {
        "metadata": {
            "name": "payment-service",
            "uid": "12345-abcde",
            "namespace": "argocd",
        },
        "spec": {
            "project": "ecommerce",
            "source": {
                "repoURL": "https://github.com/org/payment-repo.git",
                "targetRevision": "v2.1.0",
                "path": "k8s/overlays/prod",
            },
            "destination": {
                "server": "https://k8s-prod.internal.cloud",
                "namespace": "prod-payment",
            },
        },
        "status": {
            "sync": {
                "status": "Synced",
                "revision": "a1b2c3d4e5f6",
            },
            "health": {
                "status": "Healthy",
                "message": "All 3 pods running with 0 restarts",
            },
            "summary": {
                "images": ["docker.io/org/payment-service:v2.1.0"],
            },
            "conditions": [
                {"type": "SharedResourceWarning", "message": "ServiceAccount shared with worker"},
            ],
        },
    }


@pytest.fixture
def sample_project() -> dict[str, Any]:
    return {
        "metadata": {
            "name": "ecommerce",
            "uid": "proj-98765",
        },
        "spec": {
            "description": "Core ecommerce microservices",
            "sourceRepos": [
                "https://github.com/org/payment-repo.git",
                "https://github.com/org/order-repo.git",
            ],
            "destinations": [
                {"server": "https://k8s-prod.internal.cloud", "namespace": "prod-*"},
            ],
        },
    }


# ===========================================================================
# Client validation & security tests
# ===========================================================================


def test_client_missing_url(monkeypatch):
    monkeypatch.delenv("ARGOCD_SERVER_URL", raising=False)
    with pytest.raises(ValueError, match="Argo CD server URL required"):
        ArgoCDClient(base_url=None, token="token-123")


def test_client_invalid_url_scheme():
    with pytest.raises(ValueError, match="Invalid Argo CD server URL"):
        ArgoCDClient(base_url="ftp://invalid.host", token="token-123")

    with pytest.raises(ValueError, match="Invalid Argo CD server URL"):
        ArgoCDClient(base_url="not-a-url", token="token-123")


def test_client_missing_token(monkeypatch):
    monkeypatch.delenv("ARGOCD_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="Argo CD authentication token required"):
        ArgoCDClient(base_url="https://argocd.example.com", token=None)


def test_client_initialization_success():
    client = ArgoCDClient(
        base_url="https://argocd.example.com/",
        token="secret-token-xyz",
        verify_ssl=False,
    )
    assert client.base_url == "https://argocd.example.com"
    assert client.token == "secret-token-xyz"
    assert client.verify_ssl is False
    assert client._headers()["Authorization"] == "Bearer secret-token-xyz"


def test_client_token_redaction():
    client = ArgoCDClient(
        base_url="https://argocd.example.com",
        token="super-secret-jwt-token",
    )
    msg = "Error connecting with super-secret-jwt-token to server"
    redacted = client._redact(msg)
    assert "super-secret-jwt-token" not in redacted
    assert "[REDACTED]" in redacted


# ===========================================================================
# Client HTTP operations & error handling
# ===========================================================================


def test_client_get_applications_with_filter():
    client = ArgoCDClient(
        base_url="https://argocd.example.com",
        token="token-123",
    )
    mock_data = {
        "items": [
            {"metadata": {"name": "app-1"}},
            {"metadata": {"name": "app-2"}},
            {"metadata": {"name": "app-3"}},
        ]
    }
    with patch.object(client, "request", return_value=mock_data) as mock_req:
        result = client.get_applications(projects=["default"], app_names=["app-1", "app-3"])
        mock_req.assert_called_once_with(
            "GET", "/api/v1/applications", params={"projects": ["default"]}
        )
        assert len(result) == 2
        assert [r["metadata"]["name"] for r in result] == ["app-1", "app-3"]


def test_client_get_application_detail():
    client = ArgoCDClient(
        base_url="https://argocd.example.com",
        token="token-123",
    )
    with patch.object(client, "request", return_value={"metadata": {"name": "my-app"}}) as mock_req:
        res = client.get_application("my-app")
        mock_req.assert_called_once_with("GET", "/api/v1/applications/my-app")
        assert res["metadata"]["name"] == "my-app"


def test_client_auth_error_handling():
    client = ArgoCDClient(
        base_url="https://argocd.example.com",
        token="invalid-token",
    )
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 401
    with patch("httpx.Client.request", return_value=mock_resp), pytest.raises(ArgoCDAuthError):
        client.request("GET", "/api/v1/applications")


def test_client_not_found_error_handling():
    client = ArgoCDClient(
        base_url="https://argocd.example.com",
        token="valid-token",
    )
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    with patch("httpx.Client.request", return_value=mock_resp), pytest.raises(ArgoCDNotFoundError):
        client.request("GET", "/api/v1/applications/nonexistent")


# ===========================================================================
# Markdown document rendering
# ===========================================================================


def test_application_to_document_rendering(sample_application):
    doc = _application_to_document(sample_application, "https://argocd.example.com")
    assert doc["id"] == "argocd_app_12345-abcde"
    assert doc["url"] == "https://argocd.example.com/applications/payment-service"
    assert doc["title"] == "Argo CD Application: payment-service"
    content = doc["content"]
    assert "# Argo CD Application: payment-service" in content
    assert "**AppProject**: ecommerce" in content
    assert "**Health Status**: Healthy" in content
    assert "**Sync Status**: Synced" in content
    assert "**Git Repository**: https://github.com/org/payment-repo.git" in content
    assert "**Target Revision**: v2.1.0" in content
    assert "**Synced Commit**: a1b2c3d4e5f6" in content
    assert "**Target Cluster Server**: https://k8s-prod.internal.cloud" in content
    assert "`docker.io/org/payment-service:v2.1.0`" in content
    assert "All 3 pods running with 0 restarts" in content
    assert "**SharedResourceWarning**" in content


def test_project_to_document_rendering(sample_project):
    doc = _project_to_document(sample_project, "https://argocd.example.com")
    assert doc["id"] == "argocd_project_proj-98765"
    assert doc["url"] == "https://argocd.example.com/settings/projects/ecommerce"
    assert doc["title"] == "Argo CD Project: ecommerce"
    content = doc["content"]
    assert "# Argo CD AppProject: ecommerce" in content
    assert "Core ecommerce microservices" in content
    assert "`https://github.com/org/payment-repo.git`" in content
    assert "https://k8s-prod.internal.cloud" in content


# ===========================================================================
# dlt source and integration pipeline invariants
# ===========================================================================


def test_argocd_source_attributes():
    mock_client = MagicMock(spec=ArgoCDClient)
    mock_client.base_url = "https://argocd.example.com"
    source = argocd_source(client=mock_client)

    # Invariant 1: Document mode attribute tag
    assert getattr(source, DOCUMENT_SOURCE_ATTR) == ARGOCD_SOURCE_NAME

    # Invariant 2: Resource definitions & table names
    resources = {r.name: r for r in source.selected_resources.values()}
    assert ARGOCD_APPLICATIONS_TABLE_NAME in resources
    assert ARGOCD_PROJECTS_TABLE_NAME in resources

    # Invariant 3: Write disposition is replace for automatic orphan cleanup
    assert resources[ARGOCD_APPLICATIONS_TABLE_NAME].write_disposition == "replace"
    assert resources[ARGOCD_PROJECTS_TABLE_NAME].write_disposition == "replace"


def test_argocd_source_execution(sample_application, sample_project):
    mock_client = MagicMock(spec=ArgoCDClient)
    mock_client.base_url = "https://argocd.example.com"
    mock_client.get_applications.return_value = [sample_application]
    mock_client.get_projects.return_value = [sample_project]

    source = argocd_source(client=mock_client)
    app_resource = source.selected_resources[ARGOCD_APPLICATIONS_TABLE_NAME]
    proj_resource = source.selected_resources[ARGOCD_PROJECTS_TABLE_NAME]

    app_rows = list(app_resource)
    proj_rows = list(proj_resource)

    assert len(app_rows) == 1
    assert app_rows[0]["id"] == "argocd_app_12345-abcde"
    assert app_rows[0]["title"] == "Argo CD Application: payment-service"

    assert len(proj_rows) == 1
    assert proj_rows[0]["id"] == "argocd_project_proj-98765"
    assert proj_rows[0]["title"] == "Argo CD Project: ecommerce"
