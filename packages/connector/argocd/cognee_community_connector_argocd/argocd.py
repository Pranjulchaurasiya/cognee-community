"""DLT source for Argo CD GitOps applications, projects, and cluster states.

Fetches Argo CD application manifests and projects, renders their deployment topology
and health status to structured markdown documents, and yields them as a dlt resource
for Cognee's ingestion and graph extraction pipeline.

Uses ``write_disposition="replace"`` and ``DOCUMENT_SOURCE_ATTR = "argocd"`` so deleted or
decommissioned Kubernetes applications automatically drop out of Cognee's memory via orphan cleanup.
"""

from typing import Any

try:
    from cognee.shared.logging_utils import get_logger

    logger = get_logger("argocd_connector")
except ImportError:
    import logging

    logger = logging.getLogger("argocd_connector")

try:
    from cognee.tasks.ingestion.dlt_utils import DOCUMENT_SOURCE_ATTR
except ImportError:
    DOCUMENT_SOURCE_ATTR = "cognee_document_source"

from cognee_community_connector_argocd.client import ArgoCDClient

ARGOCD_APPLICATIONS_TABLE_NAME = "argocd_applications"
ARGOCD_PROJECTS_TABLE_NAME = "argocd_projects"
ARGOCD_SOURCE_NAME = "argocd"

_EXTRA_HINT = (
    'The Argo CD connector requires the "argocd" dependencies: '
    'pip install "cognee-community-connector-argocd" (provides dlt and httpx).'
)


def argocd_source(
    base_url: str | None = None,
    token: str | None = None,
    app_names: list[str] | None = None,
    projects: list[str] | None = None,
    verify_ssl: bool = True,
    client: Any = None,
):
    """Create a dlt source yielding Argo CD applications and projects as documents.

    Args:
        base_url: Argo CD server URL (e.g. ``https://argocd.example.com``).
            Falls back to ``ARGOCD_SERVER_URL`` environment variable.
        token: Argo CD API bearer token.
            Falls back to ``ARGOCD_AUTH_TOKEN`` environment variable.
        app_names: Optional filter to restrict ingestion to specific application names.
        projects: Optional filter to restrict ingestion to specific project names.
        verify_ssl: Whether to verify SSL certificates (default: True).
        client: Pre-instantiated ``ArgoCDClient`` (primarily for test dependency injection).

    Returns:
        A dlt source suitable for ``cognee.add(...)``.
    """
    try:
        import dlt
    except ImportError as exc:
        raise ImportError(_EXTRA_HINT) from exc

    if client is None:
        client = ArgoCDClient(
            base_url=base_url,
            token=token,
            verify_ssl=verify_ssl,
        )

    server_base = client.base_url

    @dlt.resource(
        name=ARGOCD_APPLICATIONS_TABLE_NAME,
        primary_key="id",
        write_disposition="replace",
    )
    def argocd_applications():
        apps = client.get_applications(projects=projects, app_names=app_names)
        count = 0
        for app in apps:
            count += 1
            yield _application_to_document(app, server_base)
        logger.info("Argo CD: synced %d application(s).", count)

    @dlt.resource(
        name=ARGOCD_PROJECTS_TABLE_NAME,
        primary_key="id",
        write_disposition="replace",
    )
    def argocd_projects():
        projs = client.get_projects()
        if projects:
            target_projects = set(projects)
            projs = [p for p in projs if p.get("metadata", {}).get("name") in target_projects]
        count = 0
        for proj in projs:
            count += 1
            yield _project_to_document(proj, server_base)
        logger.info("Argo CD: synced %d project(s).", count)

    @dlt.source(name=ARGOCD_SOURCE_NAME)
    def _argocd():
        return [argocd_applications, argocd_projects]

    source = _argocd()
    setattr(source, DOCUMENT_SOURCE_ATTR, ARGOCD_SOURCE_NAME)
    return source


def _application_to_document(app: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Convert an Argo CD application resource into a structured markdown document."""
    metadata = app.get("metadata") or {}
    spec = app.get("spec") or {}
    status = app.get("status") or {}

    name = metadata.get("name", "unknown")
    project = spec.get("project") or metadata.get("project") or "default"
    app_namespace = metadata.get("namespace", "argocd")

    source = spec.get("source") or {}
    repo_url = source.get("repoURL", "N/A")
    target_revision = source.get("targetRevision", "HEAD")
    path = source.get("path", ".")

    destination = spec.get("destination") or {}
    dest_server = destination.get("server", "https://kubernetes.default.svc")
    dest_namespace = destination.get("namespace", "default")

    sync = status.get("sync") or {}
    sync_status = sync.get("status", "Unknown")
    sync_revision = sync.get("revision", target_revision)

    health = status.get("health") or {}
    health_status = health.get("status", "Unknown")
    health_message = health.get("message", "")

    summary = status.get("summary") or {}
    images = summary.get("images") or []

    lines = [
        f"# Argo CD Application: {name}",
        "",
        "## Deployment Metadata",
        f"- **Application Name**: {name}",
        f"- **AppProject**: {project}",
        f"- **Argo CD Namespace**: {app_namespace}",
        f"- **Health Status**: {health_status}",
        f"- **Sync Status**: {sync_status}",
        "",
        "## GitOps Source & Target",
        f"- **Git Repository**: {repo_url}",
        f"- **Target Revision**: {target_revision}",
        f"- **Synced Commit**: {sync_revision}",
        f"- **Manifest Path**: {path}",
        "",
        "## Cluster Destination",
        f"- **Target Cluster Server**: {dest_server}",
        f"- **Target Namespace**: {dest_namespace}",
    ]

    if images:
        lines.extend(
            [
                "",
                "## Deployed Container Images",
            ]
        )
        for img in images:
            lines.append(f"- `{img}`")

    if health_message:
        lines.extend(
            [
                "",
                "## Health Diagnostics",
                health_message,
            ]
        )

    conditions = status.get("conditions") or []
    if conditions:
        lines.extend(
            [
                "",
                "## Active Conditions",
            ]
        )
        for cond in conditions:
            c_type = cond.get("type", "Notice")
            c_msg = cond.get("message", "")
            lines.append(f"- **{c_type}**: {c_msg}")

    content = "\n".join(lines)
    app_id = f"argocd_app_{metadata.get('uid') or name}"
    app_url = f"{base_url}/applications/{name}"

    return {
        "id": app_id,
        "url": app_url,
        "title": f"Argo CD Application: {name}",
        "content": content,
    }


def _project_to_document(proj: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Convert an Argo CD AppProject resource into a structured markdown document."""
    metadata = proj.get("metadata") or {}
    spec = proj.get("spec") or {}

    name = metadata.get("name", "default")
    description = spec.get("description", "No description provided.")
    source_repos = spec.get("sourceRepos") or ["*"]
    destinations = spec.get("destinations") or []

    lines = [
        f"# Argo CD AppProject: {name}",
        "",
        f"**Description**: {description}",
        "",
        "## Permitted Source Repositories",
    ]
    for repo in source_repos:
        lines.append(f"- `{repo}`")

    lines.extend(
        [
            "",
            "## Allowed Destination Clusters",
        ]
    )
    for dest in destinations:
        server = dest.get("server", "*")
        namespace = dest.get("namespace", "*")
        lines.append(f"- Server: `{server}`, Namespace: `{namespace}`")

    content = "\n".join(lines)
    proj_id = f"argocd_project_{metadata.get('uid') or name}"
    proj_url = f"{base_url}/settings/projects/{name}"

    return {
        "id": proj_id,
        "url": proj_url,
        "title": f"Argo CD Project: {name}",
        "content": content,
    }
