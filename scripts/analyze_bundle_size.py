"""打包产物体积归因：读取 PyInstaller 的 COLLECT/PYZ 清单 + 外置资源目录，
输出分组件体积表、瘦身杠杆测算，并与上一版落盘结果做体积回归比对。
数据口径见 docs/requirements/打包体积优化评估报告-20261102.md。

用法：
    # 单次报表（构建之后运行，workpath 默认 .cache/pyinstaller-work/xianyu-hunter）
    python scripts/analyze_bundle_size.py

    # 发布链调用：落盘 JSON + 与基线比对 + 回归即非零退出（供 CI 阻断）
    python scripts/analyze_bundle_size.py \\
        --json-out .cache/size-report/size-20261103T120000Z-onnx.json \\
        --baseline .cache/size-report/latest.json \\
        --fail-on-regression

体积回归阈值：单个顶层组件「+50 MB」或「+5%」先到为准（可用 --regress-mb/--regress-pct 覆盖）。
基线不存在时只输出本次报表，不判回归（首次构建无基线属正常）。
"""
from __future__ import annotations

import argparse
import ast
import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MB = 1048576.0
# 回归判定用到「新增组件」的绝对阈值下限：低于此体积的新组件只提示不报警，
# 避免一个 200 KB 的新模块被当成 +∞% 的回归
NEW_COMPONENT_MIN_MB = 5.0
# SPA 无显式构建时间时的活跃判据：与最新 mtime 相差不到 30 秒视为同一次构建产物
SPA_CLUSTER_GAP_SEC = 300.0

# Windows 控制台默认 GBK 代码页，中文表头会变乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load_toc(path: Path):
    top = ast.literal_eval(path.read_text(encoding="utf-8"))
    # 两个清单都是 (条目列表, 归档路径) 形态；PYZ 反过来是 (归档路径, 条目列表)
    for candidate in top if isinstance(top, tuple) else (top,):
        if isinstance(candidate, list) and candidate and isinstance(candidate[0], (list, tuple)):
            return candidate
    raise ValueError(f"无法解析清单结构: {path}")


def _dir_size(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


def _spec_excludes(spec_path: Path) -> tuple[list[str], list[str]]:
    """从 spec 源码里取出「实际生效的排除清单」，不做语义求值。

    返回 (构建期卫生排除项, onnx 引擎额外排除项)。取不到时返回空列表并在调用方标注，
    而不是猜一个默认值——门禁的取证必须来自真实源码。
    """
    build_only: list[str] = []
    onnx_extra: list[str] = []
    if not spec_path.is_file():
        return build_only, onnx_extra
    tree = ast.parse(spec_path.read_text(encoding="utf-8"), filename=str(spec_path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not node.targets:
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        value = node.value
        if "_build_only_excludes" in names and isinstance(value, ast.List):
            build_only = [str(v.value) for v in value.elts if isinstance(v, ast.Constant)]
        elif "_excludes" in names and isinstance(value, ast.BinOp):
            # _excludes = _build_only_excludes + ['torch', ...]
            right = value.right
            if isinstance(right, ast.List):
                onnx_extra = [str(v.value) for v in right.elts if isinstance(v, ast.Constant)]
    return build_only, onnx_extra


def _iter_collect(collect_toc: Path):
    for dest, source, typ in _load_toc(collect_toc):
        try:
            size = os.path.getsize(source)
        except OSError:
            continue
        yield dest.replace("\\", "/"), source, typ, size


def _spa_activity(spa_root: Path, built_after: float | None) -> dict:
    """区分「当次构建的活跃资源」与「目录里的历史残留」。

    背景（T04105 实测 / T04180）：`vite.config.ts` 刻意 `emptyOutDir: false`，
    发布路径又不经过 `scripts/前端构建.bat` 的 `rmdir /s /q`，
    于是 `release/spa` 会累积无入口引用的旧 hash 产物，被整包拷进安装包。
    直接扫目录求和会把残留算进基线，所以体积报表必须两口径并列。

    活跃口径优先信 `--spa-built-after`（由 build-exe.ps1 传入，权威）；
    单独跑脚本没有该时间戳时，退化为 mtime 聚类：按 mtime 降序找到第一个
    大于 5 分钟的间隔作为「上一次构建」的分界。
    """
    files = [f for f in spa_root.rglob("*") if f.is_file()] if spa_root.is_dir() else []
    if not files:
        return {"root": str(spa_root), "total_files": 0, "total_bytes": 0,
                "active_files": 0, "active_bytes": 0, "stale_files": 0, "stale_bytes": 0,
                "cutoff": None, "cutoff_source": "n/a"}
    stats = [(f, f.stat()) for f in files]
    newest = max(st.st_mtime for _f, st in stats)
    if built_after is not None:
        cutoff, cutoff_source = built_after, "--spa-built-after（构建脚本传入）"
    else:
        cutoff = newest - SPA_CLUSTER_GAP_SEC
        prev = None
        for st in sorted((s.st_mtime for _f, s in stats), reverse=True):
            if prev is not None and prev - st > SPA_CLUSTER_GAP_SEC:
                cutoff = prev - 1.0
                break
            prev = st
        cutoff_source = "mtime 聚类（近似：与最新写入的首个 >5 分钟间隔处切分）"
    active = [st for _f, st in stats if st.st_mtime >= cutoff]
    active_bytes = sum(st.st_size for st in active)
    total_bytes = sum(st.st_size for _f, st in stats)
    return {
        "root": str(spa_root),
        "total_files": len(stats),
        "total_bytes": total_bytes,
        "active_files": len(active),
        "active_bytes": active_bytes,
        "stale_files": len(stats) - len(active),
        "stale_bytes": total_bytes - active_bytes,
        "cutoff": dt.datetime.fromtimestamp(cutoff, dt.timezone.utc).isoformat(timespec="seconds"),
        "cutoff_source": cutoff_source,
    }


def _print_diff(baseline: dict, current: dict, regress_mb: float, regress_pct: float) -> list[dict]:
    prev_comp = {k: v["bytes"] for k, v in (baseline.get("components") or {}).items()}
    cur_comp = {k: v["bytes"] for k, v in (current.get("components") or {}).items()}
    warnings: list[dict] = []
    print(f"\n== 与基线比对（基线时间 {baseline.get('generated_at', '未知')}）==")
    print(f"{'组件':<26}{'基线':>11}{'本次':>11}{'增减':>12}{'增减%':>9}  判定")
    for key in sorted(set(prev_comp) | set(cur_comp), key=lambda k: -(cur_comp.get(k, 0) - prev_comp.get(k, 0))):
        now = cur_comp.get(key, 0)
        before = prev_comp.get(key, 0)
        delta = now - before
        pct = (delta / before * 100.0) if before else 0.0
        over_abs = delta > regress_mb * MB
        over_pct = bool(before) and delta > before * regress_pct / 100.0
        new_component = (not before) and delta > NEW_COMPONENT_MIN_MB * MB
        if over_abs or over_pct or new_component:
            verdict = "WARN 回归"
            if not before:
                reason = "新增组件"
            elif over_abs:
                reason = "+绝对阈值"
            else:
                reason = "+百分比阈值"
            warnings.append({"component": key, "before_bytes": before, "after_bytes": now,
                             "delta_bytes": delta, "delta_pct": None if before == 0 else round(pct, 2),
                             "reason": reason})
        elif delta == 0:
            verdict = "持平"
        elif delta < 0:
            verdict = "↓ 变小"
        else:
            verdict = "ok"
        pct_text = "-" if before == 0 else f"{pct:+8.2f}%"
        print(f"{key:<26}{before / MB:9.2f} MB{now / MB:9.2f} MB{delta / MB:+10.2f} MB{pct_text:>9}  {verdict}")
    base_total = sum(prev_comp.values())
    cur_total = sum(cur_comp.values())
    print(f"{'合计（_internal 组件求和）':<24}{base_total / MB:9.2f} MB{cur_total / MB:9.2f} MB"
          f"{(cur_total - base_total) / MB:+10.2f} MB")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "", add_help=False)
    parser.add_argument("--workpath", default=".cache/pyinstaller-work/xianyu-hunter")
    parser.add_argument("--top-files", type=int, default=20, help="单文件体积表条数（默认 20）")
    parser.add_argument("--json-out", default=None, help="本次分组统计落盘路径（JSON）")
    parser.add_argument("--baseline", default=".cache/size-report/latest.json", help="上一版落盘结果")
    parser.add_argument("--regress-mb", type=float, default=50.0, help="单组件绝对上涨阈值（MB）")
    parser.add_argument("--regress-pct", type=float, default=5.0, help="单组件百分比上涨阈值")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="命中回归时以退出码 2 结束（发布流水线阻断用）")
    parser.add_argument("--spa-built-after", default=None,
                        help="SPA 构建起始时间（ISO8601）；给出则据此区分活跃/残留")
    parser.add_argument("--version", default=None, help="应用版本号，仅写入 JSON 便于溯源")
    args = parser.parse_args()

    work = REPO_ROOT / args.workpath
    collect_toc = work / "COLLECT-00.toc"
    pyz_toc = work / "PYZ-00.toc"
    if not collect_toc.is_file():
        print(f"[ERROR] 未找到 {collect_toc}，请先执行 scripts/build-exe.ps1")
        return 1

    now = dt.datetime.now(dt.timezone.utc)
    manifest_at = dt.datetime.fromtimestamp(collect_toc.stat().st_mtime, dt.timezone.utc)
    engine_declared = (os.environ.get("XH_EMBEDDING_ENGINE") or "").strip().lower() or "未知"

    exe = 0
    internal: dict[str, int] = collections.Counter()
    files: dict[str, int] = collections.Counter()
    total = 0
    top_files: list[tuple[str, int]] = []
    for dest, _source, typ, size in _iter_collect(collect_toc):
        total += size
        top_files.append((dest, size))
        if typ == "EXECUTABLE":
            exe += size
            continue
        key = dest.split("/")[0]
        internal[key] += size
        files[key] += 1
    internal_total = total - exe

    print("== PyInstaller 清单 ==")
    print(f"清单生成时间          : {manifest_at.isoformat(timespec='seconds')}（晚于此时间的 spec 改动不体现在本表）")
    print(f"EXE                  : {exe / MB:9.2f} MB")
    print(f"_internal (bin/data) : {internal_total / MB:9.2f} MB")
    print(f"\n{'组件':<28}{'体积':>12}{'文件数':>9}{'占 _internal':>14}")
    for key, size in internal.most_common(15):
        print(f"{key:<28}{size / MB:10.2f} MB{files[key]:9d}{100 * size / internal_total:13.1f}%")

    print(f"\n== Top{args.top_files} 单文件 ==")
    print(f"{'目的路径':<58}{'体积':>12}")
    for dest, size in sorted(top_files, key=lambda kv: -kv[1])[: args.top_files]:
        print(f"{dest:<58}{size / MB:10.2f} MB")

    pyz_src: dict[str, int] = collections.Counter()
    pyz_mods: dict[str, int] = collections.Counter()
    if pyz_toc.is_file():
        for name, path, _typ in _load_toc(pyz_toc):
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            top_pkg = name.split(".")[0]
            pyz_src[top_pkg] += size
            pyz_mods[top_pkg] += 1
        pyz_total = sum(pyz_src.values())
        ratio = exe / pyz_total if pyz_total else 0.0
        print(f"\n== PYZ 纯 Python（源码 {pyz_total / MB:.2f} MB → EXE {exe / MB:.2f} MB，压缩比 {ratio:.3f}）==")
        for key, size in pyz_src.most_common(12):
            print(f"{key:<28}{size / MB:10.2f} MB{pyz_mods[key]:9d} 模块")

    # ---- spec 实际生效的排除清单 + 生效核对（T04104 下一步建议 4）----
    build_only, onnx_extra = _spec_excludes(REPO_ROOT / "xianyu-hunter.spec")
    engine_observed = "onnx" if ("torch" not in internal and "sentence_transformers" not in internal) else "st"
    effective = list(build_only)
    if engine_observed == "onnx":
        effective += onnx_extra
    print(f"\n== spec 排除清单（引擎声明={engine_declared} / 产物观测={engine_observed}）==")
    print(f"  构建期卫生排除：{', '.join(build_only) if build_only else '（未解析到 _build_only_excludes）'}")
    print(f"  onnx 额外排除 ：{', '.join(onnx_extra) if onnx_extra else '（未解析到 _excludes 追加项）'}")
    print("  生效核对（期望命中 0；命中 >0 说明排除项没落地或运行期又被收集）：")
    spec_mtime = dt.datetime.fromtimestamp((REPO_ROOT / "xianyu-hunter.spec").stat().st_mtime, dt.timezone.utc)
    exclude_hits: dict[str, dict[str, int]] = {}
    for name in effective:
        pyz_hit = sum(v for k, v in pyz_mods.items() if k == name or k.startswith(name + "."))
        collect_hit = sum(v for k, v in files.items() if k == name)
        exclude_hits[name] = {"pyz_modules": pyz_hit, "collect_files": collect_hit}
        flag = "OK" if (pyz_hit == 0 and collect_hit == 0) else "命中！"
        print(f"    -{name:<24} PYZ {pyz_hit:5d} 模块  COLLECT {collect_hit:5d} 文件  {flag}")
    if (any(v["pyz_modules"] or v["collect_files"] for v in exclude_hits.values())
            and spec_mtime > manifest_at):
        print(f"    注：清单生成于 {manifest_at.isoformat(timespec='seconds')}，"
              f"早于 spec 修改时间 {spec_mtime.isoformat(timespec='seconds')}——命中属预期，须重跑构建才能验证排除效果")

    print("\n== 外置资源 ==")
    pw = REPO_ROOT / ".cache/playwright_browsers"
    pw_children = {}
    for child in sorted(pw.iterdir()) if pw.is_dir() else []:
        size = _dir_size(child)
        pw_children[child.name] = size
        if child.is_dir() and child.name.startswith("chromium_headless_shell"):
            marker = "（构建时剔除，不进包）"
        else:
            marker = ""
        print(f"{child.name:<30}{size / MB:10.2f} MB {marker}")
    models_path = REPO_ROOT / ".cache/models/bge-small-zh-v1.5"
    print(f"{'models/bge-small-zh-v1.5':<30}{_dir_size(models_path) / MB:10.2f} MB")

    spa_built_after = None
    if args.spa_built_after:
        spa_built_after = dt.datetime.fromisoformat(args.spa_built_after.replace("Z", "+00:00")).timestamp()
    spa = _spa_activity(REPO_ROOT / "release/spa", spa_built_after)
    print(f"{'release/spa（目录口径）':<28}{spa['total_bytes'] / MB:10.2f} MB / {spa['total_files']} 文件")
    print(f"{'release/spa（当次活跃）':<28}{spa['active_bytes'] / MB:10.2f} MB / {spa['active_files']} 文件"
          f"  [活跃判据：{spa['cutoff_source']} @ {spa['cutoff']}]")
    if spa["stale_files"]:
        print(f"{'release/spa（历史残留）':<28}{spa['stale_bytes'] / MB:10.2f} MB / {spa['stale_files']} 文件"
              f"  ← 无入口引用，会被整包拷进安装包（T04180）")

    pw_dist = REPO_ROOT / "release/xianyu-hunter/playwright_browsers"
    shipped = _dir_size(pw_dist)
    if not (REPO_ROOT / "release/xianyu-hunter").is_dir():
        suffix = "（dist 不存在，尚未构建）"
    elif not shipped:
        suffix = "（Lite 形态：不随包）"
    else:
        suffix = ""
    print(f"{'chromium 实际进包':<27}{shipped / MB:10.2f} MB{suffix}")

    print("\n== 瘦身杠杆（engine=onnx 时从 _internal 消失的组件）==")
    for key in ("torch", "transformers", "scipy", "scipy.libs", "sklearn"):
        print(f"  -{key:<24}{internal.get(key, 0) / MB:9.2f} MB")
    onnx_leak = internal.get("onnx", 0) + sum(v for k, v in internal.items() if k.startswith("onnx-"))
    print(f"  -{'onnx（构建期泄漏）':<22}{onnx_leak / MB:9.2f} MB")

    current = {
        "schema": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "app_version": args.version,
        "engine": {"declared": engine_declared, "observed": engine_observed},
        "workpath": args.workpath,
        "manifest_generated_at": manifest_at.isoformat(timespec="seconds"),
        "exe_bytes": exe,
        "internal_bytes": internal_total,
        "components": {k: {"bytes": v, "files": files[k]} for k, v in internal.items()},
        "pyz_packages": {k: {"bytes": v, "modules": pyz_mods[k]} for k, v in pyz_src.items()},
        "spec_excludes": {
            "build_only": build_only,
            "onnx_extra": onnx_extra,
            "effective": effective,
            "hits": exclude_hits,
        },
        "external": {
            "playwright_cache": pw_children,
            "models_bytes": _dir_size(models_path),
            "chromium_shipped_bytes": shipped,
            "spa": spa,
        },
        "thresholds": {"regress_mb": args.regress_mb, "regress_pct": args.regress_pct},
    }

    warnings: list[dict] = []
    baseline_path = REPO_ROOT / args.baseline
    if baseline_path.is_file():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"\n[WARN] 基线文件不可读（{exc}），本次跳过回归比对：{baseline_path}")
            baseline = None
        if baseline is not None:
            warnings = _print_diff(baseline, current, args.regress_mb, args.regress_pct)
    else:
        print(f"\n== 与基线比对 ==（无基线：{baseline_path}，首次构建不做回归判定）")

    if args.json_out:
        json_out = REPO_ROOT / args.json_out
        json_out.parent.mkdir(parents=True, exist_ok=True)
        # 落盘位置在 .cache/ 下，不进发布包；build-exe.ps1 另写一份 latest.json 作为下次基线
        current["regression_warnings"] = warnings
        json_out.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
        print(f"\n分组统计已落盘：{json_out.relative_to(REPO_ROOT)}")

    if warnings:
        print(f"\n[WARN] 体积回归：{len(warnings)} 个顶层组件超阈值"
              f"（+{args.regress_mb:g} MB 或 +{args.regress_pct:g}%，先到为准）")
        for w in warnings:
            pct = "新增组件" if w["delta_pct"] is None else f"{w['delta_pct']:+.2f}%"
            print(f"  - {w['component']}: {w['before_bytes'] / MB:.2f} → {w['after_bytes'] / MB:.2f} MB "
                  f"({w['delta_bytes'] / MB:+.2f} MB / {pct})")
        if args.fail_on_regression:
            print("[FAIL] --fail-on-regression 已指定，以退出码 2 阻断发布")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
