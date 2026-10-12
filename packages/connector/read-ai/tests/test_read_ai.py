"""100% offline unit tests for the Read.ai meeting connector."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from cognee_community_connector_read_ai.client import (
    ReadAIAuthError,
    ReadAIClient,
    ReadAINotFoundError,
)
from cognee_community_connector_read_ai.read_ai import (
    DOCUMENT_SOURCE_ATTR,
    READ_AI_MEETINGS_TABLE_NAME,
    _format_timestamp,
    _meeting_to_document,
    read_ai_source,
)
from cognee_community_connector_read_ai.redaction import redact_secrets

# ===========================================================================
# Client Initialization & Configuration Tests
# ===========================================================================


def test_client_missing_api_key():
    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(ValueError, match=r"Read\.ai API key required"),
    ):
        ReadAIClient(api_key="")


def test_client_invalid_url_scheme():
    with pytest.raises(ValueError, match=r"Invalid Read\.ai URL"):
        ReadAIClient(api_key="valid_key", base_url="ftp://invalid.read.ai")


def test_client_url_normalization():
    client = ReadAIClient(api_key="valid_key", base_url="https://api.read.ai/v1/")
    assert client.base_url == "https://api.read.ai/v1"


def test_client_headers():
    client = ReadAIClient(api_key="read_key_99999")
    headers = client._headers()
    assert headers["Authorization"] == "Bearer read_key_99999"
    assert headers["x-api-key"] == "read_key_99999"
    assert headers["Accept"] == "application/json"


def test_client_credential_redaction():
    client = ReadAIClient(api_key="secret_read_key_to_redact")
    msg = "Failed with key secret_read_key_to_redact on endpoint https://api.read.ai"
    redacted = client.redact(msg)
    assert "secret_read_key_to_redact" not in redacted
    assert "[REDACTED]" in redacted


# ===========================================================================
# Client HTTP Operations & Error Handling
# ===========================================================================


def test_client_auth_error_handling():
    client = ReadAIClient(api_key="bad_key")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 401
    with (
        patch("httpx.Client.request", return_value=mock_resp),
        pytest.raises(ReadAIAuthError, match=r"Read\.ai auth failed"),
    ):
        client.request("GET", "/meetings")


def test_client_not_found_error_handling():
    client = ReadAIClient(api_key="good_key")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    with (
        patch("httpx.Client.request", return_value=mock_resp),
        pytest.raises(ReadAINotFoundError, match="resource not found"),
    ):
        client.request("GET", "/meetings/mid_missing")


def test_client_retry_and_recovery():
    client = ReadAIClient(api_key="good_key")
    resp_fail = MagicMock(spec=httpx.Response)
    resp_fail.status_code = 503
    resp_fail.headers = {"Retry-After": "0.1"}

    resp_ok = MagicMock(spec=httpx.Response)
    resp_ok.status_code = 200
    resp_ok.content = b'{"meetings": [{"id": "m_1"}]}'
    resp_ok.json.return_value = {"meetings": [{"id": "m_1"}]}

    with (
        patch("httpx.Client.request", side_effect=[resp_fail, resp_ok]),
        patch("time.sleep"),
    ):
        res = client.get_meetings()
        assert len(res["meetings"]) == 1
        assert res["meetings"][0]["id"] == "m_1"


def test_client_get_summary_and_transcript():
    client = ReadAIClient(api_key="good_key")
    with patch.object(client, "request", return_value={"summary": "Product launch roadmap agreed"}):
        summary = client.get_meeting_summary("m_1")
        assert summary["summary"] == "Product launch roadmap agreed"

    with patch.object(
        client,
        "request",
        return_value=[{"speaker": "Bob", "text": "Ship on Tuesday", "start_time": 10.0}],
    ):
        tr = client.get_meeting_transcript("m_1")
        assert len(tr) == 1
        assert tr[0]["speaker"] == "Bob"


# ===========================================================================
# Secret Redaction Tests
# ===========================================================================


def test_redact_secrets_patterns():
    text = (
        "Spoke about token=sk-abcdefabcdefabcdefabcdefabcdef12 and "
        "ghp_123456789012345678901234567890123456 in sync. "
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret."
    )
    scrubbed = redact_secrets(text)
    assert "sk-abcdefabcdefabcdefabcdefabcdef12" not in scrubbed
    assert "[REDACTED_API_KEY]" in scrubbed
    assert "ghp_123456789012345678901234567890123456" not in scrubbed
    assert "[REDACTED_GITHUB_PAT]" in scrubbed
    assert "Bearer [REDACTED]" in scrubbed


def test_redact_secrets_clean_text():
    clean = "General weekly standup with engineering leads."
    assert redact_secrets(clean) == clean
    assert redact_secrets("") == ""
    assert redact_secrets(None) == ""


# ===========================================================================
# Document Formatting Tests
# ===========================================================================


def test_format_timestamp():
    assert _format_timestamp(None) == "00:00"
    assert _format_timestamp(30) == "00:30"
    assert _format_timestamp(90) == "01:30"
    assert _format_timestamp(3700) == "01:01:40"


def test_meeting_to_document_structure():
    meeting = {
        "id": "mid_404",
        "title": "Strategy Sync token=sk-12345678901234567890123456789012",
        "start_time": "2026-10-12T09:00:00Z",
        "duration": "45m",
        "url": "https://app.read.ai/analytics/meetings/mid_404",
        "participants": [
            {"name": "Alice Lead", "email": "alice@company.com"},
            "Bob PM",
        ],
    }
    summary_data = {
        "summary": "Agreed on enterprise tier pricing and EU cloud deployment.",
        "key_takeaways": ["EU expansion planned for Q1 2027."],
        "action_items": [
            {
                "text": (
                    "Alice to finalize vendor contract token=sk-12345678901234567890123456789012"
                ),
                "assignee": "Alice",
            }
        ],
        "metrics": {"engagement_score": 88, "sentiment": "Positive"},
    }
    transcript = [
        {"speaker": "Alice", "text": "Good morning everyone.", "start_time": 2.0},
        {
            "speaker": "Bob",
            "text": "Let's review the EU cloud roadmap.",
            "start_time": 45.0,
        },
    ]

    doc = _meeting_to_document(meeting, summary_data, transcript)

    assert doc["id"] == "readai:meeting:mid_404"
    assert doc["external_metadata"]["source"] == "read_ai"
    assert doc["external_metadata"]["meeting_id"] == "mid_404"
    assert doc["external_metadata"]["action_item_count"] == 1
    assert doc["external_metadata"]["transcript_turn_count"] == 2

    # Check secret redaction in title and action items
    assert "sk-12345678901234567890123456789012" not in doc["text"]
    assert "[REDACTED_API_KEY]" in doc["text"]

    # Verify summary-first ordering: executive summary appears before transcript
    sum_idx = doc["text"].find("Executive Summary")
    tr_idx = doc["text"].find("Speaker Transcript")
    assert sum_idx != -1
    assert tr_idx != -1
    assert sum_idx < tr_idx

    # Check action items and metrics
    assert "EU expansion planned for Q1 2027." in doc["text"]
    assert "Alice to finalize vendor contract" in doc["text"]
    assert "**Engagement Score**: 88" in doc["text"]


def test_meeting_to_document_empty_fields():
    meeting = {"id": "m_empty", "title": "Empty Call"}
    doc = _meeting_to_document(meeting, {}, [])

    assert "No executive summary recorded" in doc["text"]
    assert "No action items recorded" in doc["text"]
    assert "Transcript not available or omitted" in doc["text"]


# ===========================================================================
# DLT Source Ingestion Tests
# ===========================================================================


def test_read_ai_source_attributes_and_write_disposition():
    mock_client = MagicMock(spec=ReadAIClient)

    # Full snapshot mode (replace for forget-on-delete)
    src_full = read_ai_source(client=mock_client)
    assert getattr(src_full, DOCUMENT_SOURCE_ATTR) == "read_ai"
    resources_full = list(src_full.resources.values())
    assert len(resources_full) == 1
    assert resources_full[0].write_disposition == "replace"

    # Incremental mode (merge when since is provided)
    src_inc = read_ai_source(client=mock_client, since="2026-10-01T00:00:00Z")
    resources_inc = list(src_inc.resources.values())
    assert resources_inc[0].write_disposition == "merge"


def test_read_ai_source_execution_with_meetings():
    mock_client = MagicMock(spec=ReadAIClient)
    mock_client.get_meetings.side_effect = [
        {
            "meetings": [
                {"id": "m_1", "title": "Sprint Planning", "start_time": "2026-10-10T10:00:00Z"},
                {"id": "m_2", "title": "Retrospective", "start_time": "2026-10-11T12:00:00Z"},
            ],
            "next_cursor": None,
        }
    ]
    mock_client.get_meeting_summary.return_value = {
        "summary": "Sprint objectives locked",
        "action_items": [],
    }
    mock_client.get_meeting_transcript.return_value = [
        {"speaker": "Team", "text": "Agreed", "start_time": 5.0}
    ]

    source = read_ai_source(client=mock_client)
    res = source.resources[READ_AI_MEETINGS_TABLE_NAME]

    docs = list(res)
    assert len(docs) == 2
    assert docs[0]["id"] == "readai:meeting:m_1"
    assert docs[1]["id"] == "readai:meeting:m_2"
    assert "Sprint objectives locked" in docs[0]["text"]


def test_read_ai_source_explicit_meeting_ids():
    mock_client = MagicMock(spec=ReadAIClient)
    mock_client.get_meeting_summary.return_value = {
        "id": "m_target",
        "title": "Target Sync",
        "summary": "Focus sync",
    }
    mock_client.get_meeting_transcript.return_value = []

    source = read_ai_source(client=mock_client, meeting_ids=["m_target"])
    res = source.resources[READ_AI_MEETINGS_TABLE_NAME]

    docs = list(res)
    assert len(docs) == 1
    assert docs[0]["id"] == "readai:meeting:m_target"
    mock_client.get_meeting_summary.assert_called_once_with("m_target")
