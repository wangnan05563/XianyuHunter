"""scripts/analyze_bundle_size.py 的回归门禁与两口径判据单测（T04106）。

体积归因报表本身不改变产物，它的价值全在「阈值真的咬人」和
「SPA 活跃/残留分得开」这两条判定上，所以针对纯函数测，不起 PyInstaller。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MB = 1048576.0


def _load_module():
    path = REPO_ROOT / "scripts" / "analyze_bundle_size.py"
    spec = importlib.util.spec_from_file_location("analyze_bundle_size_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _snapshot(components: dict[str, int]) -> dict:
    return {"components": {k: {"bytes": v, "files": 1} for k, v in components.items()}}


def test_new_large_component_triggers_warning(capsys) -> None:
    """torch 这类「基线里没有、本次 >5MB」的组件必须被当成回归（P1 的历史教训）。"""
    baseline = _snapshot({"numpy": int(6 * MB)})
    current = _snapshot({"numpy": int(6 * MB), "torch": int(360 * MB)})
    warnings = mod._print_diff(baseline, current, regress_mb=50.0, regress_pct=5.0)
    assert [w["component"] for w in warnings] == ["torch"]
    assert warnings[0]["reason"] == "新增组件"
    assert "WARN 回归" in capsys.readouterr().out


def test_small_new_component_is_not_noise(capsys) -> None:
    baseline = _snapshot({"numpy": int(6 * MB)})
    current = _snapshot({"numpy": int(6 * MB), "tiny_pkg": int(1 * MB)})
    assert mod._print_diff(baseline, current, regress_mb=50.0, regress_pct=5.0) == []
    capsys.readouterr()


def test_absolute_or_percentage_threshold_whichever_first(capsys) -> None:
    """+50MB 与 +5% 先到为准：60MB 涨幅但占比 0.6% 要报；3% 涨幅不报。"""
    big = int(10_000 * MB)
    baseline = _snapshot({"pkg": big})
    over_abs = _snapshot({"pkg": big + int(60 * MB)})
    warnings = mod._print_diff(baseline, over_abs, regress_mb=50.0, regress_pct=5.0)
    assert len(warnings) == 1
    assert warnings[0]["reason"] == "+绝对阈值"

    over_pct = _snapshot({"pkg": big + int(600 * MB)})
    under_pct = _snapshot({"pkg": big + int(40 * MB)})
    assert len(mod._print_diff(baseline, over_pct, regress_mb=50.0, regress_pct=5.0)) == 1
    assert mod._print_diff(baseline, under_pct, regress_mb=50.0, regress_pct=5.0) == []
    capsys.readouterr()


def test_shrink_is_not_a_regression(capsys) -> None:
    baseline = _snapshot({"torch": int(360 * MB), "numpy": int(6 * MB)})
    current = _snapshot({"numpy": int(6 * MB)})
    assert mod._print_diff(baseline, current, regress_mb=50.0, regress_pct=5.0) == []
    assert "↓ 变小" in capsys.readouterr().out


def test_spa_activity_uses_explicit_build_timestamp(tmp_path: Path) -> None:
    built_at = dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.timezone.utc)
    old = built_at - dt.timedelta(days=1)
    fresh = built_at + dt.timedelta(seconds=30)

    stale_file = tmp_path / "assets" / "index-OLD.js"
    stale_file.parent.mkdir(parents=True)
    stale_file.write_text("x" * 100)
    active_file = tmp_path / "assets" / "index-NEW.js"
    active_file.write_text("y" * 10)

    def _epoch(moment: dt.datetime) -> int:
        return int(moment.timestamp())

    os.utime(stale_file, (_epoch(old), _epoch(old)))
    os.utime(active_file, (_epoch(fresh), _epoch(fresh)))

    result = mod._spa_activity(tmp_path, built_at.timestamp())
    assert result["total_files"] == 2
    assert result["active_files"] == 1
    assert result["stale_files"] == 1
    assert result["active_bytes"] == 10
    assert "spa-built-after" in result["cutoff_source"]


def test_spa_activity_counts_public_passthrough_as_active(tmp_path: Path) -> None:
    """vite 逐字节拷贝 public/ 且保留 mtime，纯时间判据不得把 favicon 记成残留。"""
    built_at = dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.timezone.utc)
    checkout = built_at - dt.timedelta(days=100)
    epoch = int(checkout.timestamp())

    public = tmp_path / "public"
    public.mkdir()
    src = public / "favicon.ico"
    src.write_bytes(b"ico-bytes")
    os.utime(src, (epoch, epoch))

    spa = tmp_path / "spa"
    spa.mkdir()
    copied = spa / "favicon.ico"
    copied.write_bytes(b"ico-bytes")
    os.utime(copied, (epoch, epoch))
    stale = spa / "index-OLD.js"
    stale.write_text("z" * 50)
    os.utime(stale, (epoch, epoch))

    result = mod._spa_activity(spa, built_at.timestamp(), public_dir=public)
    assert result["active_files"] == 1
    assert result["active_bytes"] == 9
    assert result["stale_files"] == 1


def test_spa_activity_falls_back_to_mtime_cluster(tmp_path: Path) -> None:
    """无构建时间戳时按 mtime 聚类切分：最近一批写入为活跃，更早批次为残留。"""
    recent = dt.datetime.now(dt.timezone.utc)
    group_a = tmp_path / "a.js"
    group_b = tmp_path / "b.js"
    group_a.write_text("aaaa")  # 4 bytes, 当次构建
    group_b.write_text("bbbbbbbb")  # 8 bytes, 上一版残留
    for path, moment in ((group_a, recent), (group_b, recent - dt.timedelta(minutes=20))):
        epoch = int(moment.timestamp())
        os.utime(path, (epoch, epoch))

    result = mod._spa_activity(tmp_path, None)
    assert result["active_files"] == 1
    assert result["stale_files"] == 1
    assert result["active_bytes"] == 4
    assert "近似" in result["cutoff_source"]


def test_spa_activity_on_missing_dir_is_zeroed(tmp_path: Path) -> None:
    result = mod._spa_activity(tmp_path / "nope", None)
    assert result["total_files"] == 0
    assert result["cutoff"] is None


def test_spec_excludes_are_read_from_source_not_hardcoded() -> None:
    build_only, onnx_extra = mod._spec_excludes(REPO_ROOT / "xianyu-hunter.spec")
    assert build_only == ["onnx", "_pytest", "pytest", "pluggy", "iniconfig", "PIL.AvifImagePlugin"]
    assert {"torch", "sentence_transformers", "transformers", "scipy", "sklearn"} <= set(onnx_extra)


def test_json_snapshot_round_trips(tmp_path: Path) -> None:
    """门禁依赖落盘 JSON 做跨版本比对，结构必须可原样读回。"""
    payload = {"schema": 1, "components": {"torch": {"bytes": 123, "files": 2}}}
    out = tmp_path / "size.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    assert json.loads(out.read_text(encoding="utf-8")) == payload
