"""DLT source for Read.ai AI meeting summaries, metrics, and transcripts."""

from typing import Any

from .client import ReadAIClient
from .redaction import redact_secrets

try:
    import dlt
except ImportError:
    dlt = None

try:
    from cognee.shared.logging_utils import get_logger

    logger = get_logger("read_ai_connector")
except ImportError:
    import logging

    logger = logging.getLogger("read_ai_connector")

DOCUMENT_SOURCE_ATTR = "cognee_document_source"
READ_AI_SOURCE_NAME = "read_ai"
READ_AI_MEETINGS_TABLE_NAME = "read_ai_meetings"


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


def _meeting_to_document(
    meeting: dict[str, Any],
    summary_data: dict[str, Any] | None,
    transcript: list[dict[str, Any]],
) -> dict[str, Any]:
    """Render a Read.ai meeting into a summary-first markdown document."""
    mid = meeting.get("id") or meeting.get("meeting_id") or "unknown"
    title = redact_secrets(meeting.get("title") or meeting.get("name") or "Untitled Meeting")
    start_time = meeting.get("start_time") or meeting.get("date") or "N/A"
    duration = meeting.get("duration") or meeting.get("duration_minutes") or "N/A"
    url = meeting.get("url") or f"https://app.read.ai/analytics/meetings/{mid}"

    # Format participants
    participants_raw = meeting.get("participants") or []
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
        "**Source:** Read.ai Meeting Report",
        f"**Meeting ID:** `{mid}`",
        f"**Date:** {start_time}",
        f"**Duration:** {duration}",
        f"**URL:** {url}",
        f"**Participants:** {participants_line}",
        "",
    ]

    # Prioritize Executive Summary
    details = summary_data or meeting
    exec_summary = details.get("summary") or details.get("executive_summary") or ""
    if isinstance(exec_summary, dict):
        exec_summary = exec_summary.get("text") or str(exec_summary)

    lines.append("## Executive Summary")
    if exec_summary:
        lines.append(redact_secrets(str(exec_summary)))
    else:
        lines.append("*No executive summary recorded for this meeting.*")
    lines.append("")

    # Action Items & Key Takeaways
    action_items = details.get("action_items") or details.get("tasks") or []
    key_takeaways = details.get("key_takeaways") or details.get("takeaways") or []

    lines.append("## Key Takeaways & Action Items")
    if action_items or key_takeaways:
        for t in key_takeaways:
            lines.append(f"- **[Key Takeaway]** {redact_secrets(str(t))}")
        for item in action_items:
            if isinstance(item, dict):
                task_text = item.get("text") or item.get("description") or ""
                assignee = item.get("assignee") or item.get("owner")
                assignee_str = f" *(Owner: {assignee})*" if assignee else ""
                lines.append(f"- [ ] {redact_secrets(task_text)}{assignee_str}")
            else:
                lines.append(f"- [ ] {redact_secrets(str(item))}")
    else:
        lines.append("- *No action items recorded.*")
    lines.append("")

    # Meeting Metrics & Sentiment
    metrics = details.get("metrics") or details.get("analytics") or {}
    lines.append("## Meeting Metrics & Engagement")
    if metrics:
        for k, v in metrics.items():
            k_name = k.replace("_", " ").title()
            lines.append(f"- **{k_name}**: {v}")
    else:
        lines.append("- *No metrics recorded.*")
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
        "id": f"readai:meeting:{mid}",
        "text": content,
        "external_metadata": {
            "source": READ_AI_SOURCE_NAME,
            "meeting_id": mid,
            "title": title,
            "start_time": start_time,
            "url": url,
            "action_item_count": len(action_items),
            "transcript_turn_count": len(transcript),
        },
    }


def read_ai_source(
    api_key: str | None = None,
    base_url: str | None = None,
    meeting_ids: list[str] | None = None,
    since: str | None = None,
    include_summary: bool = True,
    include_transcript: bool = True,
    limit: int = 50,
    verify_ssl: bool = True,
    client: ReadAIClient | None = None,
):
    """Create a dlt source yielding Read.ai meeting documents with summary-first prioritization.

    Args:
        api_key: Read.ai API key. Falls back to ``READAI_API_KEY``.
        base_url: Read.ai API root URL (default: ``https://api.read.ai/v1``).
        meeting_ids: Optional list of meeting IDs to restrict ingestion.
        since: Optional ISO 8601 timestamp string for date-window incremental sync.
        include_summary: Whether to fetch meeting summaries and action items (default: True).
        include_transcript: Whether to fetch speaker transcripts (default: True).
        limit: Max meetings to retrieve per page (default: 50).
        verify_ssl: Whether to verify SSL certificates (default: True).
        client: Pre-instantiated ``ReadAIClient`` (for testing/mocking).

    Returns:
        A dlt source yielding Markdown documents for cognee.
    """
    if dlt is None:
        raise ImportError("dlt is required to run read_ai_source. Install via: pip install dlt")

    resolved_client = client or ReadAIClient(
        api_key=api_key,
        base_url=base_url,
        verify_ssl=verify_ssl,
    )

    # Use 'replace' by default for full-snapshot sync and automatic orphan cleanup,
    # or 'merge' if incremental date-window sync is requested.
    write_disp = "merge" if since else "replace"

    @dlt.resource(
        name=READ_AI_MEETINGS_TABLE_NAME,
        primary_key="id",
        write_disposition=write_disp,
    )
    def read_ai_meetings():
        total_synced = 0

        # Path A: Explicit list of meeting IDs
        if meeting_ids:
            for mid in meeting_ids:
                try:
                    summary_data = (
                        resolved_client.get_meeting_summary(mid) if include_summary else {}
                    )
                    tr = resolved_client.get_meeting_transcript(mid) if include_transcript else []
                    yield _meeting_to_document(summary_data, summary_data, tr)
                    total_synced += 1
                except Exception as exc:
                    logger.warning("Read.ai: error ingesting meeting '%s': %s", mid, exc)
            logger.info("Read.ai: synced %d explicit meeting(s).", total_synced)
            return

        # Path B: Paginated listing (supports incremental date window `since`)
        cursor = None
        while True:
            resp = resolved_client.get_meetings(limit=limit, cursor=cursor, start_time=since)
            meetings = resp.get("meetings") or []
            if not meetings:
                break

            for m in meetings:
                mid = m.get("id") or m.get("meeting_id")
                if not mid:
                    continue
                summary_data = resolved_client.get_meeting_summary(mid) if include_summary else {}
                tr = resolved_client.get_meeting_transcript(mid) if include_transcript else []
                yield _meeting_to_document(m, summary_data, tr)
                total_synced += 1

            cursor = resp.get("next_cursor")
            if not cursor:
                break

        logger.info("Read.ai: synced %d meeting(s).", total_synced)

    @dlt.source(name=READ_AI_SOURCE_NAME)
    def _read_ai():
        return read_ai_meetings

    src = _read_ai()
    setattr(src, DOCUMENT_SOURCE_ATTR, READ_AI_SOURCE_NAME)
    return src
