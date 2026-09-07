import shutil
from pathlib import Path

from ue4ss_mcp_server.compat import (
    lookup_custom_game_config,
    lookup_known_fork,
    lookup_patternsleuth_status,
    parse_patternsleuth_status,
)

FIXTURES = Path(__file__).parent / "fixtures"
CUSTOM_CONFIGS_ROOT = FIXTURES / "custom_game_configs_sample"
PATTERNSLEUTH_SAMPLE = FIXTURES / "patternsleuth_games_sample.md"


def _fake_cache_dir(tmp_path) -> Path:
    # lookup_custom_game_config expects <cache_dir>/assets/CustomGameConfigs/<Game>
    assets = tmp_path / "assets" / "CustomGameConfigs"
    shutil.copytree(CUSTOM_CONFIGS_ROOT, assets)
    return tmp_path


def test_lookup_custom_game_config_matches_despite_spacing_and_case(tmp_path):
    cache_dir = _fake_cache_dir(tmp_path)

    result = lookup_custom_game_config(cache_dir, "ghostwiretokyo")

    assert result is not None
    assert result.folder_name == "Ghost Wire Tokyo"
    assert "UE4SS_Signatures/FName_Constructor.lua" in result.files


def test_lookup_custom_game_config_no_match_returns_none(tmp_path):
    cache_dir = _fake_cache_dir(tmp_path)
    assert lookup_custom_game_config(cache_dir, "Some Unrelated Game") is None


def test_parse_patternsleuth_status_found_and_not_found_sections():
    text = PATTERNSLEUTH_SAMPLE.read_text(encoding="utf-8")
    status = parse_patternsleuth_status(text)

    assert status["hogwartslegacy"] == "aob_found"
    assert status["ghostwiretokyo"] == "aob_partial"
    assert "torn" in status  # engine-prefix strip: "412S_Torn" -> "torn"


def test_lookup_patternsleuth_status_strips_engine_prefix(tmp_path):
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    (docs_root / "patternsleuth-games.md").write_text(
        PATTERNSLEUTH_SAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )

    assert lookup_patternsleuth_status(docs_root, "Hogwarts Legacy") == "aob_found"
    assert lookup_patternsleuth_status(docs_root, "Ghost Wire Tokyo") == "aob_partial"
    assert lookup_patternsleuth_status(docs_root, "Some Unrelated Game") is None


def test_lookup_patternsleuth_status_missing_file_returns_none(tmp_path):
    assert lookup_patternsleuth_status(tmp_path / "docs", "Hogwarts Legacy") is None


def test_lookup_known_fork_verified_entry():
    fork = lookup_known_fork("Escape the Backrooms")
    assert fork is not None
    assert fork["repo"] == "EscapeTheBackroomsCommunity/UE4SS"


def test_lookup_known_fork_no_entry_for_unverified_game():
    # Deliberately not seeded -- see forks.json's note on why Palworld
    # isn't in the registry despite community reputation.
    assert lookup_known_fork("Palworld") is None
