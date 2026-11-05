"""C3 残余取证：模板列表每次请求重读/重解析 presets YAML 与 user JSON 的成本

落地前后各跑一次，取 21 次中位。只读仓库内随包模板文件，不写盘、不碰数据库。
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from xianyu_hunter.web.routes import api_templates as T  # noqa: E402

ROUNDS = 21


def bench(fn, rounds: int = ROUNDS) -> float:
    samples = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


def main() -> None:
    print(f"preset={T.PRESET_FILE} exists={Path(T.PRESET_FILE).exists()} "
          f"size={Path(T.PRESET_FILE).stat().st_size}")
    print(f"user  ={T.USER_FILE} exists={Path(T.USER_FILE).exists()} "
          f"size={Path(T.USER_FILE).stat().st_size}")
    n_presets = len(T._load_presets())
    n_users = len(T._load_user())
    print(f"条数：presets={n_presets} users={n_users}")

    read_only = lambda: Path(T.PRESET_FILE).read_text(encoding="utf-8")  # noqa: E731
    parse_only = lambda: T.yaml_io.safe_load(  # noqa: E731
        Path(T.PRESET_FILE).read_text(encoding="utf-8")
    )
    user_read = lambda: Path(T.USER_FILE).read_text(encoding="utf-8")  # noqa: E731
    user_parse = lambda: json.loads(Path(T.USER_FILE).read_text(encoding="utf-8"))  # noqa: E731

    print(f"\n[中位，{ROUNDS} 次]")
    print(f"  presets: read {bench(read_only):6.2f} ms | read+parse {bench(parse_only):6.2f} ms "
          f"| _load_presets() {bench(T._load_presets):6.2f} ms")
    print(f"  user   : read {bench(user_read):6.2f} ms | read+parse {bench(user_parse):6.2f} ms "
          f"| _load_user()    {bench(T._load_user):6.2f} ms")
    print(f"  GET /api/templates 处理器（_load_presets + _load_user + 拼接）: "
          f"{bench(T.list_templates):6.2f} ms")


if __name__ == "__main__":
    main()
