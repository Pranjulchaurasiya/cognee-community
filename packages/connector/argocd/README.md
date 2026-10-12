# cognee-community-connector-argocd

Argo CD data-source connector for [cognee](https://github.com/topoteretes/cognee).

Syncs declarative Kubernetes applications, GitOps revisions, project boundaries, and deployment health states into Cognee memory and knowledge graphs.

## Features

- **Full Deployment Topology**: Extracts applications, source repositories, target revisions, destination clusters, and namespaces.
- **Health & Sync Observability**: Ingests live health status (`Healthy`, `Degraded`, `Progressing`), sync status (`Synced`, `OutOfSync`), and active error conditions.
- **Forget-On-Delete**: Uses `write_disposition="replace"` so decommissioned or deleted applications are automatically pruned from Cognee's graph and vector indices via orphan cleanup.
- **Project Scoping**: Filter ingestion to specific application names or AppProjects.

## Installation

```bash
pip install cognee-community-connector-argocd
```

## Quickstart

```python
import asyncio
import cognee
from cognee_community_connector_argocd import argocd_source


async def main():
    # Ingest all Argo CD applications and projects
    source = argocd_source(
        base_url="https://argocd.example.com",
        token="your-argocd-api-token",
    )

    await cognee.add(source)
    await cognee.cognify()

    # Query your deployment topology
    results = await cognee.search(
        "Which services are currently OutOfSync or Degraded in the production cluster?"
    )
    print(results)


if __name__ == "__main__":
    asyncio.run(main())
```

## Environment Variables

- `ARGOCD_SERVER_URL`: Argo CD base URL (e.g. `https://argocd.example.com`).
- `ARGOCD_AUTH_TOKEN`: Argo CD JWT bearer token or API token.
