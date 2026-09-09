from pathlib import Path

from ue4ss_mcp_server.mod_management import disable_mod, enable_mod, set_mod_enabled


def _make_fake_ue4ss_install(tmp_path: Path, mods_txt_content: str | None, extra_mod_dirs: list[str] | None = None) -> Path:
    install_dir = tmp_path / "Binaries" / "Win64"
    mods_dir = install_dir / "Mods"
    mods_dir.mkdir(parents=True)
    if mods_txt_content is not None:
        (mods_dir / "mods.txt").write_text(mods_txt_content, encoding="utf-8")
    for name in extra_mod_dirs or []:
        (mods_dir / name / "scripts").mkdir(parents=True)
        (mods_dir / name / "scripts" / "main.lua").write_text("-- test mod\n", encoding="utf-8")
    return install_dir


def test_set_mod_enabled_adds_new_entry(tmp_path):
    mods_txt = tmp_path / "mods.txt"
    mods_txt.write_text("ConsoleEnablerMod : 1\n", encoding="utf-8")

    changed = set_mod_enabled(mods_txt, "MyTestMod", True)

    assert changed is True
    assert "MyTestMod : 1" in mods_txt.read_text(encoding="utf-8")


def test_set_mod_enabled_flips_existing_entry(tmp_path):
    mods_txt = tmp_path / "mods.txt"
    mods_txt.write_text("MyTestMod : 0\n", encoding="utf-8")

    changed = set_mod_enabled(mods_txt, "MyTestMod", True)

    assert changed is True
    assert "MyTestMod : 1" in mods_txt.read_text(encoding="utf-8")
    assert "MyTestMod : 0" not in mods_txt.read_text(encoding="utf-8")


def test_set_mod_enabled_idempotent(tmp_path):
    mods_txt = tmp_path / "mods.txt"
    mods_txt.write_text("MyTestMod : 1\n", encoding="utf-8")

    assert set_mod_enabled(mods_txt, "MyTestMod", True) is False


def test_set_mod_enabled_disable_unlisted_mod_is_a_noop(tmp_path):
    mods_txt = tmp_path / "mods.txt"
    mods_txt.write_text("ConsoleEnablerMod : 1\n", encoding="utf-8")

    changed = set_mod_enabled(mods_txt, "NeverHeardOfIt", False)

    assert changed is False
    assert "NeverHeardOfIt" not in mods_txt.read_text(encoding="utf-8")


def test_set_mod_enabled_disable_existing_entry(tmp_path):
    mods_txt = tmp_path / "mods.txt"
    mods_txt.write_text("MyTestMod : 1\n", encoding="utf-8")

    changed = set_mod_enabled(mods_txt, "MyTestMod", False)

    assert changed is True
    assert "MyTestMod : 0" in mods_txt.read_text(encoding="utf-8")


def test_set_mod_enabled_keeps_keybinds_comment_attached(tmp_path):
    mods_txt = tmp_path / "mods.txt"
    mods_txt.write_text(
        "ConsoleEnablerMod : 1\n; Built-in keybinds, do not move up!\nKeybinds : 1\n", encoding="utf-8"
    )

    set_mod_enabled(mods_txt, "MyTestMod", True)

    lines = mods_txt.read_text(encoding="utf-8").splitlines()
    comment_idx = next(i for i, l in enumerate(lines) if l.startswith(";"))
    keybinds_idx = next(i for i, l in enumerate(lines) if l.startswith("Keybinds"))
    assert keybinds_idx == comment_idx + 1


def test_enable_mod_requires_mod_folder_to_exist(tmp_path):
    install_dir = _make_fake_ue4ss_install(tmp_path, "ConsoleEnablerMod : 1\n")

    result = enable_mod(str(install_dir), "MyTestMod")

    assert result["enabled"] is False
    assert "error" in result
    assert "MyTestMod" not in (install_dir / "Mods" / "mods.txt").read_text(encoding="utf-8")


def test_enable_mod_succeeds_when_mod_folder_exists(tmp_path):
    install_dir = _make_fake_ue4ss_install(tmp_path, "ConsoleEnablerMod : 1\n", extra_mod_dirs=["MyTestMod"])

    result = enable_mod(str(install_dir), "MyTestMod")

    assert result == {"enabled": True, "mods_txt_updated": True}
    assert "MyTestMod : 1" in (install_dir / "Mods" / "mods.txt").read_text(encoding="utf-8")


def test_enable_mod_missing_mods_dir_reports_error(tmp_path):
    result = enable_mod(str(tmp_path / "nonexistent"), "MyTestMod")
    assert result["enabled"] is False
    assert "error" in result


def test_disable_mod_does_not_require_mod_folder(tmp_path):
    install_dir = _make_fake_ue4ss_install(tmp_path, "MyTestMod : 1\n")

    result = disable_mod(str(install_dir), "MyTestMod")

    assert result == {"disabled": True, "mods_txt_updated": True}
    assert "MyTestMod : 0" in (install_dir / "Mods" / "mods.txt").read_text(encoding="utf-8")
