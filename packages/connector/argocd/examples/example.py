"""Example usage of the Cognee Argo CD connector."""

import asyncio
import os
import cognee
from cognee_community_connector_argocd import argocd_source


async def main():
    server_url = os.environ.get("ARGOCD_SERVER_URL", "https://argocd.example.com")
    token = os.environ.get("ARGOCD_AUTH_TOKEN", "mock-token")

    # Ingest Argo CD applications and projects
    source = argocd_source(
        base_url=server_url,
        token=token,
        projects=["core-infrastructure", "payment-services"],
    )

    print("Adding Argo CD applications to Cognee...")
    await cognee.add(source)

    print("Running Cognify on deployment topology...")
    await cognee.cognify()

    print("Searching for deployment state...")
    results = await cognee.search(
        "What git commit revision is the payment-service running on production?"
    )
    for res in results:
        print("-", res)


if __name__ == "__main__":
    asyncio.run(main())
