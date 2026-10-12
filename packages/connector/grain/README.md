# Cognee Grain Data-Source Connector

A data-source connector for **Grain AI meeting recordings, highlights, and transcripts** in [Cognee](https://github.com/topoteretes/cognee).

Grain captures, transcribes, and highlights customer interviews, sales calls, and internal engineering syncs. This connector bridges Grain meeting intelligence into Cognee's entity graph and vector stores with **highlight-first prioritization**.

---

## Key Features

1. **Highlight-First Normalization:**
   Human-curated key takeaways, tags, and timestamped clips are prioritized at the top of the ingested Markdown document. This ensures Cognee's entity extraction and search weights give primary importance to distilled insights rather than rambling conversational filler.
2. **Speaker-Diarized Transcripts:**
   Speaker-attributed dialogue timeline (`[Speaker (mm:ss)]: ...`) provides complete conversational context without heavy audio/video media blobs.
3. **Pre-Ingestion Secret Redaction:**
   Automatically scrubs sensitive tokens (Bearer tokens, OpenAI keys, GitHub PATs, AWS access keys, passwords) before emitting records into Cognee.
4. **Forget-on-Delete & Snapshot Sync:**
   Uses `write_disposition="replace"` and `DOCUMENT_SOURCE_ATTR="grain"` so Cognee's automatic orphan cleanup purges archived or deleted remote recordings on subsequent runs.
5. **Incremental Sync Support:**
   Supports the `since=` parameter to ingest only meetings recorded or modified after a specified timestamp (`write_disposition="merge"`).
6. **100% Offline Testing:**
   Complete unit test suite with mock fixtures and zero external network calls.

---

## Installation

```bash
pip install cognee-community-connector-grain
```

Or install from source within `cognee-community`:

```bash
pip install -e packages/connector/grain
```

---

## Authentication

Obtain your API token from Grain Workspace Settings. Set it in your environment:

```bash
export GRAIN_API_TOKEN="your_grain_token"
```

Or pass it directly when initializing `grain_source(token="...")`.

---

## Usage Example

```python
import asyncio
import cognee
from cognee_community_connector_grain import grain_source


async def main():
    # Ingest all visible Grain recordings with highlights and transcripts
    source = grain_source(
        include_highlights=True,
        include_transcript=True,
    )

    await cognee.add(source)
    await cognee.cognify()

    # Query customer objections and highlights across all meetings
    results = await cognee.search(
        "What pricing feedback did enterprise customers provide during onboarding calls?"
    )
    for res in results:
        print(res)


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Running Offline Unit Tests

```bash
pytest packages/connector/grain/tests -v
```
