from pathlib import Path

from ue4ss_mcp_server.bridge_install import install_bridge_mod


def _make_fake_ue4ss_install(tmp_path: Path, mods_txt_content: str | None) -> Path:
    install_dir = tmp_path / "Binaries" / "Win64"
    mods_dir = install_dir / "Mods"
    mods_dir.mkdir(parents=True)
    if mods_txt_content is not None:
        (mods_dir / "mods.txt").write_text(mods_txt_content, encoding="utf-8")
    return install_dir


def test_install_bridge_mod_copies_and_substitutes_token(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ue4ss_mcp_server.bridge_install.get_or_create_token", lambda: "deadbeef"
    )
    install_dir = _make_fake_ue4ss_install(tmp_path, "ConsoleEnablerMod : 1\n")

    result = install_bridge_mod(str(install_dir))

    assert result["installed"] is True
    main_lua = install_dir / "Mods" / "UE4SSMCPBridge" / "scripts" / "main.lua"
    content = main_lua.read_text(encoding="utf-8")
    assert '"deadbeef"' in content
    assert "__BRIDGE_TOKEN__" not in content


def test_install_bridge_mod_missing_mods_dir_reports_error(tmp_path):
    result = install_bridge_mod(str(tmp_path / "nonexistent"))
    assert result["installed"] is False
    assert "error" in result


def test_install_bridge_mod_inserts_before_keybinds(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ue4ss_mcp_server.bridge_install.get_or_create_token", lambda: "tok"
    )
    install_dir = _make_fake_ue4ss_install(
        tmp_path, "ConsoleEnablerMod : 1\n; Built-in keybinds, do not move up!\nKeybinds : 1\n"
    )

    result = install_bridge_mod(str(install_dir))

    assert result["mods_txt_updated"] is True
    lines = (install_dir / "Mods" / "mods.txt").read_text(encoding="utf-8").splitlines()
    bridge_idx = next(i for i, l in enumerate(lines) if l.startswith("UE4SSMCPBridge"))
    keybinds_idx = next(i for i, l in enumerate(lines) if l.startswith("Keybinds"))
    assert bridge_idx < keybinds_idx


def test_install_bridge_mod_no_mods_txt_creates_one(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ue4ss_mcp_server.bridge_install.get_or_create_token", lambda: "tok"
    )
    install_dir = _make_fake_ue4ss_install(tmp_path, None)

    result = install_bridge_mod(str(install_dir))

    assert result["mods_txt_updated"] is True
    content = (install_dir / "Mods" / "mods.txt").read_text(encoding="utf-8")
    assert "UE4SSMCPBridge : 1" in content


def test_install_bridge_mod_inserts_above_keybinds_comment_not_between(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ue4ss_mcp_server.bridge_install.get_or_create_token", lambda: "tok"
    )
    install_dir = _make_fake_ue4ss_install(
        tmp_path, "ConsoleEnablerMod : 1\n; Built-in keybinds, do not move up!\nKeybinds : 1\n"
    )

    install_bridge_mod(str(install_dir))

    lines = (install_dir / "Mods" / "mods.txt").read_text(encoding="utf-8").splitlines()
    comment_idx = next(i for i, l in enumerate(lines) if l.startswith(";"))
    keybinds_idx = next(i for i, l in enumerate(lines) if l.startswith("Keybinds"))
    # the comment must stay immediately above Keybinds, not get separated
    # from it by our inserted line
    assert keybinds_idx == comment_idx + 1


def test_install_bridge_mod_idempotent_when_already_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ue4ss_mcp_server.bridge_install.get_or_create_token", lambda: "tok"
    )
    install_dir = _make_fake_ue4ss_install(tmp_path, "UE4SSMCPBridge : 1\n")

    result = install_bridge_mod(str(install_dir))

    assert result["mods_txt_updated"] is False
