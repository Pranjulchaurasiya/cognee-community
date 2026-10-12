"""Example usage of the Cognee Read.ai meeting connector."""

import asyncio
import os

import cognee

from cognee_community_connector_read_ai import read_ai_source


async def main():
    api_key = os.getenv("READAI_API_KEY", "mock_readai_key_for_demo")
    print("Connecting to Read.ai API...")

    source = read_ai_source(
        api_key=api_key,
        include_summary=True,
        include_transcript=True,
    )

    print("Adding Read.ai meeting reports and metrics to Cognee...")
    await cognee.add(source)

    print("Running Cognify on meeting knowledge graph...")
    await cognee.cognify()

    print("Searching for deliverables and customer action items...")
    results = await cognee.search(
        "What action items and deliverables were assigned to Alice in the product sync?"
    )
    for res in results:
        print("-", res)


if __name__ == "__main__":
    asyncio.run(main())
