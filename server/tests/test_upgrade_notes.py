from pathlib import Path

from ue4ss_mcp_server.upgrade_notes import get_upgrade_notes

DOCS_SAMPLE = Path(__file__).parent / "fixtures" / "docs_sample"


def test_get_upgrade_notes_finds_documented_transition():
    result = get_upgrade_notes(DOCS_SAMPLE, "3.0.1", "4.0.0", "experimental-latest")

    assert result["sections"]
    assert "3.x to 4.x" in result["sections"][0]["heading"]
    assert "FName" in result["sections"][0]["content"]


def test_get_upgrade_notes_no_overlap_returns_empty_with_note():
    result = get_upgrade_notes(DOCS_SAMPLE, "1.0.0", "2.0.0", "experimental-latest")

    assert result["sections"] == []
    assert "note" in result


def test_get_upgrade_notes_missing_file_returns_empty_with_note(tmp_path):
    result = get_upgrade_notes(tmp_path, "3.0.1", "4.0.0", "some-ref")

    assert result["sections"] == []
    assert result["source_ref"] == "some-ref"
    assert "note" in result
