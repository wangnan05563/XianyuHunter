"""config.yaml 读路径的成本拆分：解析 vs 序列化 vs 脱敏各占多少（B2 残留判据）

为什么要有这个脚本：B2 原建议给 `config/raw`、`config/share` 加 TTL 缓存，把它们从
132.4 / 104.7 ms 拉到「地板级 10~20 ms」。但这两个接口每个请求做的事不止读文件，
还包含 `_sanitize_dict` + `_dump_yaml`。只 memo 住 `_load_yaml` 能省下的**只有解析那一段**，
其余照旧。不拆开就报「132 → 20 ms」是编数字。

拆开后暴露了第二个事实（见输出第 5~6 行）：`yaml.safe_load` / `yaml.safe_dump` 走的是
PyYAML 的**纯 Python** 后端，本机 libyaml 其实在（`__with_libyaml__=True`），换成
CSafeLoader/CSafeDumper 是同一份数据、逐字节相同的输出，但快一个数量级。

只读，不写任何配置；打印量级与耗时，绝不打印配置内容（里面有真实凭据）。
用法：PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe perf/2026-10-31-full-api/config_memo_probe.py
"""
from __future__ import annotations

import copy
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

import yaml  # noqa: E402

from xianyu_hunter.web.routes import api_config  # noqa: E402

CONFIG_FILE = REPO / "config" / "config.yaml"
REPEAT = 20


def timed(fn, repeat: int = REPEAT) -> tuple[float, float, float]:
    """返回 (中位数 ms, 最小值 ms, 最大值 ms)；预热一次不计入（冷启动/页缓存噪声）"""
    fn()
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples), min(samples), max(samples)


def show(label: str, r: tuple[float, float, float]) -> None:
    print(f"{label}：中位 {r[0]:.2f} / 最快 {r[1]:.2f} / 最慢 {r[2]:.2f} ms")


def main() -> int:
    if not CONFIG_FILE.exists():
        print(f"跳过：{CONFIG_FILE} 不存在（沙箱环境请指向沙箱配置）")
        return 0

    text = CONFIG_FILE.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text) or {}
    print(f"config.yaml：{len(text.encode('utf-8')) / 1024:.1f} KB、顶层 {len(parsed)} 个键")
    print(f"PyYAML {yaml.__version__} | libyaml 可用={yaml.__with_libyaml__} | "
          f"但 safe_load 用的是纯 Python SafeLoader")

    show("① 读文件 read_text", timed(lambda: CONFIG_FILE.read_text(encoding="utf-8")))
    pure = timed(lambda: yaml.safe_load(text))
    show("② safe_load 解析（文本已在内存）", pure)
    cload = timed(lambda: yaml.load(text, Loader=yaml.CSafeLoader))
    show("③ CSafeLoader 解析同一份文本", cload)
    same = yaml.load(text, Loader=yaml.CSafeLoader) == parsed
    print(f"   等价性：{'CSafeLoader 结果 == safe_load 结果' if same else '不一致（不可换！）'}"
          f" → 快 {pure[0] / max(cload[0], 0.001):.1f}×")
    cold = timed(lambda: yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")))
    print(f"①+② = _load_yaml 冷路径：中位 {cold[0]:.2f} ms   ← memo 省掉的就是这一段")

    copy_cost = timed(lambda: copy.deepcopy(parsed))
    show("deepcopy 返回值（memo 新增成本）", copy_cost)
    show("_sanitize_dict 脱敏（memo 省不掉）", timed(lambda: api_config._sanitize_dict(parsed)))
    sanitized = api_config._sanitize_dict(parsed)
    dump_pure = timed(lambda: api_config._dump_yaml(sanitized))
    show("_dump_yaml 序列化（memo 省不掉）", dump_pure)
    dump_c = timed(lambda: yaml.dump(
        sanitized, Dumper=yaml.CSafeDumper,
        allow_unicode=True, sort_keys=False, default_flow_style=False))
    show("同样参数的 CSafeDumper", dump_c)
    print("   写侧能不能换：逐份比对仓库里真实 YAML（只读，不写盘）")
    from pathlib import Path as _P
    samples = sorted(_P(api_config.CONFIG_FILE).parent.glob("*.yaml"))
    samples += [_P(__file__).resolve().parents[2] / "backend" / "xianyu_hunter" / "data" / "preset_templates.yaml"]
    diverging = []
    for sp in samples:
        if not (sp.exists() and sp.stat().st_size):
            continue
        data = yaml.safe_load(sp.read_text(encoding="utf-8"))
        same_bytes = (
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
            == yaml.dump(data, Dumper=yaml.CSafeDumper,
                         allow_unicode=True, sort_keys=False, default_flow_style=False)
        )
        if not same_bytes:
            diverging.append(sp.name)
    print(f"   dump 产物逐字节相同的文件：{len(samples) - len(diverging)}/{len(samples)}"
          f"；不一致的：{diverging or '无'}")
    print("   ⚠️ 不一致的原因是 CSafeDumper 对超出 BMP 的字符（emoji，如模板图标 📷）不遵守"
          " allow_unicode，会写成 \"\\U0001F4F7\"。单看 config.yaml 会以为可以换 —— 别只测一份。")

    print(f"\n[A] memo 净收益 ≈ 冷路径 {cold[0]:.2f} − 深拷贝 {copy_cost[0]:.2f}"
          f" = {cold[0] - copy_cost[0]:.2f} ms/请求")
    print(f"[B] C loader 净收益 ≈ 解析省 {cold[0] - cload[0]:.2f} ms/请求（已落地，读侧）；"
          f"序列化那 {dump_pure[0] - dump_c[0]:.2f} ms 没省 —— C dumper 的 emoji 写法不一致，见上")
    print("对照基线（1 并发）：/raw 132.4 ms、/share 104.7 ms")
    print("→ memo 把重复解析变成一次；C loader 把那一次本身砍掉一个数量级。两者不冲突。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
