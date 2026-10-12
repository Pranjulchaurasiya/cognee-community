"""Example usage of the Cognee Grain AI meeting connector."""

import asyncio
import os

import cognee

from cognee_community_connector_grain import grain_source


async def main():
    token = os.getenv("GRAIN_API_TOKEN", "mock_grain_token_for_demo")
    print("Connecting to Grain API...")

    source = grain_source(
        token=token,
        include_highlights=True,
        include_transcript=True,
    )

    print("Adding Grain meeting recordings and highlights to Cognee...")
    await cognee.add(source)

    print("Running Cognify on meeting knowledge graph...")
    await cognee.cognify()

    print("Searching for customer feedback and key takeaways...")
    results = await cognee.search(
        "What pricing objections and feature requests were discussed in customer meetings?"
    )
    for res in results:
        print("-", res)


if __name__ == "__main__":
    asyncio.run(main())
