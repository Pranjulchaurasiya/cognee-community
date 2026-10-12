"""DLT source for Grain AI meeting recordings, highlights, and transcripts."""

from typing import Any

from .client import GrainClient
from .redaction import redact_secrets

try:
    import dlt
except ImportError:
    dlt = None

try:
    from cognee.shared.logging_utils import get_logger

    logger = get_logger("grain_connector")
except ImportError:
    import logging

    logger = logging.getLogger("grain_connector")

DOCUMENT_SOURCE_ATTR = "cognee_document_source"
GRAIN_SOURCE_NAME = "grain"
GRAIN_RECORDINGS_TABLE_NAME = "grain_recordings"


def _format_timestamp(seconds: float | int | None) -> str:
    """Convert offset in seconds into [mm:ss] or [hh:mm:ss] format."""
    if seconds is None:
        return "00:00"
    total_sec = int(seconds)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _recording_to_document(
    recording: dict[str, Any],
    highlights: list[dict[str, Any]],
    transcript: list[dict[str, Any]],
) -> dict[str, Any]:
    """Render a Grain recording into a highlight-first markdown document."""
    rec_id = recording.get("id") or "unknown"
    title = redact_secrets(recording.get("title") or recording.get("name") or "Untitled Meeting")
    created_at = recording.get("created_at") or recording.get("start_time") or "N/A"
    updated_at = recording.get("updated_at") or created_at
    url = (
        recording.get("url")
        or recording.get("share_url")
        or f"https://grain.com/recordings/{rec_id}"
    )

    # Format participants
    participants_raw = recording.get("participants") or []
    part_strs: list[str] = []
    for p in participants_raw:
        if isinstance(p, dict):
            name = p.get("name") or p.get("full_name") or "Participant"
            email = p.get("email")
            part_strs.append(f"{name} ({email})" if email else name)
        elif isinstance(p, str):
            part_strs.append(p)
    participants_line = ", ".join(part_strs) if part_strs else "None recorded"

    lines: list[str] = [
        f"# {title}",
        "",
        "**Source:** Grain AI Meeting Recording",
        f"**Recording ID:** `{rec_id}`",
        f"**Date:** {created_at}",
        f"**URL:** {url}",
        f"**Participants:** {participants_line}",
        "",
    ]

    # Prioritize Highlights at the top
    lines.append("## Executive Highlights & Key Takeaways")
    if highlights:
        for h in highlights:
            text = redact_secrets(
                h.get("text") or h.get("summary") or h.get("takeaway") or h.get("title") or ""
            )
            offset_sec = (
                h.get("start_time_ms", 0) / 1000.0
                if "start_time_ms" in h
                else h.get("start_time", 0)
            )
            ts = _format_timestamp(offset_sec)
            tags = h.get("tags") or h.get("labels") or []
            tag_str = f" *[Tags: {', '.join(tags)}]*" if tags else ""
            creator = h.get("creator") or {}
            creator_name = (
                creator.get("name")
                if isinstance(creator, dict)
                else str(creator)
                if creator
                else None
            )
            creator_str = f" *(Created by: {creator_name})*" if creator_name else ""
            lines.append(f"- **[{ts}]**{tag_str} {text}{creator_str}")
    else:
        lines.append("- *No curated highlights recorded for this meeting.*")

    lines.append("")

    # Speaker-Diarized Transcript at the bottom
    lines.append("## Speaker Transcript")
    if transcript:
        for turn in transcript:
            speaker = turn.get("speaker") or turn.get("speaker_name") or "Speaker"
            utterance = redact_secrets(turn.get("text") or turn.get("utterance") or "")
            offset_sec = turn.get("start_time", 0)
            ts = _format_timestamp(offset_sec)
            lines.append(f"- **[{ts}] {speaker}:** {utterance}")
    else:
        lines.append("- *Transcript not available or omitted.*")

    content = "\n".join(lines)

    return {
        "id": f"grain:recording:{rec_id}",
        "text": content,
        "external_metadata": {
            "source": GRAIN_SOURCE_NAME,
            "recording_id": rec_id,
            "title": title,
            "created_at": created_at,
            "updated_at": updated_at,
            "url": url,
            "highlight_count": len(highlights),
            "transcript_turn_count": len(transcript),
        },
    }


def grain_source(
    token: str | None = None,
    base_url: str | None = None,
    recording_ids: list[str] | None = None,
    since: str | None = None,
    include_highlights: bool = True,
    include_transcript: bool = True,
    limit: int = 50,
    verify_ssl: bool = True,
    client: GrainClient | None = None,
):
    """Create a dlt source yielding Grain meeting documents with highlight prioritization.

    Args:
        token: Grain API Bearer token. Falls back to ``GRAIN_API_TOKEN``.
        base_url: Grain API root URL (default: ``https://api.grain.com/v1``).
        recording_ids: Optional list of recording IDs to restrict ingestion.
        since: Optional ISO 8601 timestamp string for incremental sync.
        include_highlights: Whether to fetch curated highlights (default: True).
        include_transcript: Whether to fetch speaker transcripts (default: True).
        limit: Max recordings to retrieve per page (default: 50).
        verify_ssl: Whether to verify SSL certificates (default: True).
        client: Pre-instantiated ``GrainClient`` (for testing/mocking).

    Returns:
        A dlt source yielding Markdown documents for cognee.
    """
    if dlt is None:
        raise ImportError("dlt is required to run grain_source. Install via: pip install dlt")

    resolved_client = client or GrainClient(
        token=token,
        base_url=base_url,
        verify_ssl=verify_ssl,
    )

    # Use 'replace' by default for full-snapshot sync and automatic orphan cleanup,
    # or 'merge' if incremental sync by timestamp is requested.
    write_disp = "merge" if since else "replace"

    @dlt.resource(
        name=GRAIN_RECORDINGS_TABLE_NAME,
        primary_key="id",
        write_disposition=write_disp,
    )
    def grain_recordings():
        total_synced = 0

        # Path A: Explicit list of recording IDs
        if recording_ids:
            for rid in recording_ids:
                try:
                    rec_data = resolved_client.request("GET", f"/recordings/{rid}")
                    hl = resolved_client.get_highlights(rid) if include_highlights else []
                    tr = resolved_client.get_transcript(rid) if include_transcript else []
                    yield _recording_to_document(rec_data, hl, tr)
                    total_synced += 1
                except Exception as exc:
                    logger.warning("Grain: error ingesting recording '%s': %s", rid, exc)
            logger.info("Grain: synced %d explicit recording(s).", total_synced)
            return

        # Path B: Paginated listing (supports incremental filter `since`)
        cursor = None
        while True:
            resp = resolved_client.get_recordings(limit=limit, cursor=cursor, since=since)
            recs = resp.get("recordings") or []
            if not recs:
                break

            for rec in recs:
                rid = rec.get("id")
                if not rid:
                    continue
                hl = resolved_client.get_highlights(rid) if include_highlights else []
                tr = resolved_client.get_transcript(rid) if include_transcript else []
                yield _recording_to_document(rec, hl, tr)
                total_synced += 1

            cursor = resp.get("next_cursor")
            if not cursor:
                break

        logger.info("Grain: synced %d recording(s).", total_synced)

    @dlt.source(name=GRAIN_SOURCE_NAME)
    def _grain():
        return grain_recordings

    src = _grain()
    setattr(src, DOCUMENT_SOURCE_ATTR, GRAIN_SOURCE_NAME)
    return src
