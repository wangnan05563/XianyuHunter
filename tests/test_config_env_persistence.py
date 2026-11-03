"""T04183：.env 里同名 key 必须收敛成一行。

历史缺陷：`_generate_and_persist_token()` 用 `open("a")` 追加，绕开了 `_persist_to_env()`
的 upsert 通道，实机 %APPDATA%/XianyuHunter/.env 因此累积 21 行 WEB_TOKEN。
"""

from pathlib import Path

import pytest

from xianyu_hunter import config


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    monkeypatch.setattr(config, "get_env_file", lambda: path)
    return path


def test_upsert_collapses_duplicate_keys_into_one_line(env_file: Path) -> None:
    """重复 key 只在首次出现处落新值，其余重复行丢弃，注释与其它键不动。"""
    env_file.write_text(
        "# 首部注释\n"
        "WEB_TOKEN=old1\n"
        "OTHER=keep-me\n"
        "WEB_TOKEN=old2\n"
        "WEB_TOKEN=old3\n"
        "# 尾部注释\n",
        encoding="utf-8",
    )

    config._persist_to_env({"WEB_TOKEN": "new"})

    lines = env_file.read_text(encoding="utf-8").splitlines()
    token_lines = [line for line in lines if line.startswith("WEB_TOKEN=")]
    assert token_lines == ["WEB_TOKEN=new"], "同名 key 未收敛成单行"
    assert lines.index("WEB_TOKEN=new") == 1, "新值应落在首次出现的位置"
    assert "# 首部注释" in lines and "# 尾部注释" in lines
    assert "OTHER=keep-me" in lines, "upsert 不得动其它键"
    assert not any(line.startswith("WEB_TOKEN=old") for line in lines)


def test_token_generation_replaces_instead_of_appending(env_file: Path) -> None:
    """重生成后文件行数不变且只有一行 WEB_TOKEN（追加旁路已封）。"""
    env_file.write_text("WEB_TOKEN=stale\n", encoding="utf-8")

    token = config._generate_and_persist_token()

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, "重生成不得让文件变长"
    assert lines[0] == f"WEB_TOKEN={token}"


def test_token_generation_creates_missing_file(env_file: Path) -> None:
    token = config._generate_and_persist_token()

    assert env_file.exists(), "首次启动没有 .env 时应当创建"
    assert env_file.read_text(encoding="utf-8").splitlines() == [f"WEB_TOKEN={token}"]


def test_effective_token_survives_a_twenty_one_line_history(env_file: Path) -> None:
    """按实机形态（21 行历史值）复现：一次写入后生效值即新 token，无需依赖「最后一行胜出」。

    旧语义下 dotenv 取最后一行，所以第 21 个值才生效；收敛成单行后这个歧义彻底消失。
    """
    history = "".join(f"WEB_TOKEN=hist{i}\n" for i in range(21))
    env_file.write_text(history, encoding="utf-8")

    token = config._generate_and_persist_token()

    remaining = [line for line in env_file.read_text(encoding="utf-8").splitlines()
                 if line.startswith("WEB_TOKEN=")]
    assert remaining == [f"WEB_TOKEN={token}"]


def test_write_failure_does_not_raise(env_file: Path, tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """写盘失败保持「仅警告、不阻断启动」的既有语义。"""
    missing_parent = tmp_path / "no-such-dir" / ".env"
    monkeypatch.setattr(config, "get_env_file", lambda: missing_parent)

    config._persist_to_env({"WEB_TOKEN": "x"})  # 不抛异常即通过
