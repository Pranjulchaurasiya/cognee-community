# Cognee Read.ai Data-Source Connector

A data-source connector for **Read.ai meeting summaries, metrics, and transcripts** in [Cognee](https://github.com/topoteretes/cognee).

Read.ai generates automated meeting summaries, action items, sentiment analytics, and transcripts for video conferences across Zoom, Microsoft Teams, and Google Meet. This connector normalizes meeting intelligence into Cognee's entity graph and vector memory with **summary-first prioritization**.

---

## Key Features

1. **Summary-First Normalization:**
   Executive summaries, key takeaways, and action items are prioritized at the top of the ingested document, ensuring Cognee extracts high-value business decisions and tasks before conversational dialogue turns.
2. **Action Item & Deliverable Tracking:**
   Parses deliverables and assignee ownership into structured Markdown checkboxes (`- [ ] task (Owner: name)`).
3. **Meeting Metrics & Sentiment:**
   Ingests engagement and sentiment scores to provide organizational context.
4. **Speaker-Diarized Transcripts:**
   Includes speaker-attributed turns (`[Speaker (mm:ss)]: ...`) for full context without heavy video/audio blobs.
5. **Pre-Ingestion Credential Redaction:**
   Automatically scrubs sensitive keys, Bearer tokens, and passwords from meeting summaries and transcripts.
6. **Forget-on-Delete & Snapshot Sync:**
   Uses `write_disposition="replace"` and `DOCUMENT_SOURCE_ATTR="read_ai"` so Cognee's automatic orphan cleanup purges deleted upstream calls.
7. **100% Offline Testing:**
   Comprehensive pytest suite mocking all external HTTP interactions.

---

## Installation

```bash
pip install cognee-community-connector-read-ai
```

Or from source:

```bash
pip install -e packages/connector/read-ai
```

---

## Authentication

Set your Read.ai API key in your environment:

```bash
export READAI_API_KEY="your_api_key_here"
```

Or pass it directly: `read_ai_source(api_key="...")`.

---

## Usage Example

```python
import asyncio
import cognee
from cognee_community_connector_read_ai import read_ai_source


async def main():
    source = read_ai_source(
        include_summary=True,
        include_transcript=True,
    )

    await cognee.add(source)
    await cognee.cognify()

    results = await cognee.search(
        "What deliverables and pricing commitments were made during customer meetings?"
    )
    for res in results:
        print(res)


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Running Offline Unit Tests

```bash
pytest packages/connector/read-ai/tests -v
```
