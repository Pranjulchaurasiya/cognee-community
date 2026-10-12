"""100% offline unit tests for the Grain AI meeting connector."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from cognee_community_connector_grain.client import (
    GrainAuthError,
    GrainClient,
    GrainNotFoundError,
)
from cognee_community_connector_grain.grain import (
    DOCUMENT_SOURCE_ATTR,
    GRAIN_RECORDINGS_TABLE_NAME,
    _format_timestamp,
    _recording_to_document,
    grain_source,
)
from cognee_community_connector_grain.redaction import redact_secrets

# ===========================================================================
# Client Initialization & Configuration Tests
# ===========================================================================


def test_client_missing_token():
    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(ValueError, match="Grain API token required"),
    ):
        GrainClient(token="")


def test_client_invalid_url_scheme():
    with pytest.raises(ValueError, match="Invalid Grain URL"):
        GrainClient(token="valid_token", base_url="ftp://invalid.grain.com")


def test_client_url_normalization():
    client = GrainClient(token="valid_token", base_url="https://api.grain.com/v1/")
    assert client.base_url == "https://api.grain.com/v1"


def test_client_headers():
    client = GrainClient(token="sec_grain_12345")
    headers = client._headers()
    assert headers["Authorization"] == "Bearer sec_grain_12345"
    assert headers["Accept"] == "application/json"


def test_client_credential_redaction():
    client = GrainClient(token="super_secret_grain_token")
    msg = "Failed with token super_secret_grain_token at https://api.grain.com"
    redacted = client.redact(msg)
    assert "super_secret_grain_token" not in redacted
    assert "[REDACTED]" in redacted


# ===========================================================================
# Client HTTP operations & error handling
# ===========================================================================


def test_client_auth_error_handling():
    client = GrainClient(token="bad_tok")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 401
    with (
        patch("httpx.Client.request", return_value=mock_resp),
        pytest.raises(GrainAuthError, match="Grain auth failed"),
    ):
        client.request("GET", "/recordings")


def test_client_not_found_error_handling():
    client = GrainClient(token="good_tok")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    with (
        patch("httpx.Client.request", return_value=mock_resp),
        pytest.raises(GrainNotFoundError, match="resource not found"),
    ):
        client.request("GET", "/recordings/rec_missing")


def test_client_retry_and_recovery():
    client = GrainClient(token="good_tok")
    resp_fail = MagicMock(spec=httpx.Response)
    resp_fail.status_code = 503
    resp_fail.headers = {"Retry-After": "0.1"}

    resp_ok = MagicMock(spec=httpx.Response)
    resp_ok.status_code = 200
    resp_ok.content = b'{"recordings": [{"id": "rec_1"}]}'
    resp_ok.json.return_value = {"recordings": [{"id": "rec_1"}]}

    with (
        patch("httpx.Client.request", side_effect=[resp_fail, resp_ok]),
        patch("time.sleep"),
    ):
        res = client.get_recordings()
        assert len(res["recordings"]) == 1
        assert res["recordings"][0]["id"] == "rec_1"


def test_client_get_highlights_and_transcript():
    client = GrainClient(token="good_tok")
    with patch.object(
        client, "request", return_value={"highlights": [{"id": "hl_1", "text": "Key decision"}]}
    ):
        hls = client.get_highlights("rec_1")
        assert len(hls) == 1
        assert hls[0]["text"] == "Key decision"

    with patch.object(
        client,
        "request",
        return_value=[{"speaker": "Alice", "text": "Hello world", "start_time": 2.0}],
    ):
        tr = client.get_transcript("rec_1")
        assert len(tr) == 1
        assert tr[0]["speaker"] == "Alice"


# ===========================================================================
# Secret Redaction Tests
# ===========================================================================


def test_redact_secrets_patterns():
    text = (
        "User shared sk-12345678901234567890123456789012 and "
        "ghp_123456789012345678901234567890123456 in meeting. "
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz."
    )
    scrubbed = redact_secrets(text)
    assert "sk-12345678901234567890123456789012" not in scrubbed
    assert "[REDACTED_API_KEY]" in scrubbed
    assert "ghp_123456789012345678901234567890123456" not in scrubbed
    assert "[REDACTED_GITHUB_PAT]" in scrubbed
    assert "Bearer [REDACTED]" in scrubbed


def test_redact_secrets_clean_text():
    clean = "Just a standard product demo with no credentials."
    assert redact_secrets(clean) == clean
    assert redact_secrets("") == ""
    assert redact_secrets(None) == ""


# ===========================================================================
# Document Formatting Tests
# ===========================================================================


def test_format_timestamp():
    assert _format_timestamp(None) == "00:00"
    assert _format_timestamp(45) == "00:45"
    assert _format_timestamp(125) == "02:05"
    assert _format_timestamp(3665) == "01:01:05"


def test_recording_to_document_structure():
    rec = {
        "id": "rec_101",
        "title": "Enterprise Onboarding Call token=sk-abcdefabcdefabcdefabcdefabcdef12",
        "created_at": "2026-10-10T14:00:00Z",
        "url": "https://grain.com/recordings/rec_101",
        "participants": [
            {"name": "Alice Engineer", "email": "alice@company.com"},
            "Bob Architect",
        ],
    }
    highlights = [
        {
            "id": "hl_1",
            "text": "Client requested SOC2 report and SSO integration.",
            "start_time_ms": 135000,
            "tags": ["security", "compliance"],
            "creator": {"name": "Alice Engineer"},
        }
    ]
    transcript = [
        {
            "speaker": "Alice",
            "text": "Welcome everyone to our session.",
            "start_time": 5.0,
        },
        {
            "speaker": "Bob",
            "text": (
                "Our primary concern is enterprise auth token=sk-abcdefabcdefabcdefabcdefabcdef12."
            ),
            "start_time": 130.0,
        },
    ]

    doc = _recording_to_document(rec, highlights, transcript)

    assert doc["id"] == "grain:recording:rec_101"
    assert doc["external_metadata"]["source"] == "grain"
    assert doc["external_metadata"]["recording_id"] == "rec_101"
    assert doc["external_metadata"]["highlight_count"] == 1
    assert doc["external_metadata"]["transcript_turn_count"] == 2

    # Verify secrets scrubbing in title and transcript
    assert "sk-abcdefabcdefabcdefabcdefabcdef12" not in doc["text"]
    assert "[REDACTED_API_KEY]" in doc["text"]

    # Verify highlight-first ordering: highlights must appear before transcript
    hl_idx = doc["text"].find("Executive Highlights & Key Takeaways")
    tr_idx = doc["text"].find("Speaker Transcript")
    assert hl_idx != -1
    assert tr_idx != -1
    assert hl_idx < tr_idx

    # Check tags, timestamp, and creator in highlights
    assert "[Tags: security, compliance]" in doc["text"]
    assert "[02:15]" in doc["text"]
    assert "Created by: Alice Engineer" in doc["text"]


def test_recording_to_document_empty_highlights_and_transcript():
    rec = {"id": "rec_empty", "title": "Quick Sync"}
    doc = _recording_to_document(rec, [], [])

    assert "No curated highlights recorded" in doc["text"]
    assert "Transcript not available or omitted" in doc["text"]
    assert doc["external_metadata"]["highlight_count"] == 0
    assert doc["external_metadata"]["transcript_turn_count"] == 0


# ===========================================================================
# DLT Source Ingestion Tests
# ===========================================================================


def test_grain_source_attributes_and_write_disposition():
    mock_client = MagicMock(spec=GrainClient)

    # Full snapshot mode (replace for forget-on-delete)
    src_full = grain_source(client=mock_client)
    assert getattr(src_full, DOCUMENT_SOURCE_ATTR) == "grain"
    resources_full = list(src_full.resources.values())
    assert len(resources_full) == 1
    assert resources_full[0].write_disposition == "replace"

    # Incremental mode (merge when since is provided)
    src_inc = grain_source(client=mock_client, since="2026-10-01T00:00:00Z")
    resources_inc = list(src_inc.resources.values())
    assert resources_inc[0].write_disposition == "merge"


def test_grain_source_execution_with_recordings():
    mock_client = MagicMock(spec=GrainClient)
    mock_client.get_recordings.side_effect = [
        {
            "recordings": [
                {"id": "rec_1", "title": "Design Review", "created_at": "2026-10-10T10:00:00Z"},
                {"id": "rec_2", "title": "Customer Feedback", "created_at": "2026-10-11T12:00:00Z"},
            ],
            "next_cursor": None,
        }
    ]
    mock_client.get_highlights.return_value = [
        {"id": "h1", "text": "Approved spec", "start_time_ms": 10000}
    ]
    mock_client.get_transcript.return_value = [
        {"speaker": "Team", "text": "Looks good", "start_time": 10.0}
    ]

    source = grain_source(client=mock_client)
    res = source.resources[GRAIN_RECORDINGS_TABLE_NAME]

    docs = list(res)
    assert len(docs) == 2
    assert docs[0]["id"] == "grain:recording:rec_1"
    assert docs[1]["id"] == "grain:recording:rec_2"
    assert "Approved spec" in docs[0]["text"]
    assert "Looks good" in docs[0]["text"]


def test_grain_source_explicit_recording_ids():
    mock_client = MagicMock(spec=GrainClient)
    mock_client.request.return_value = {
        "id": "rec_target",
        "title": "Target Session",
        "created_at": "2026-10-12T15:00:00Z",
    }
    mock_client.get_highlights.return_value = []
    mock_client.get_transcript.return_value = []

    source = grain_source(client=mock_client, recording_ids=["rec_target"])
    res = source.resources[GRAIN_RECORDINGS_TABLE_NAME]

    docs = list(res)
    assert len(docs) == 1
    assert docs[0]["id"] == "grain:recording:rec_target"
    mock_client.request.assert_called_once_with("GET", "/recordings/rec_target")
