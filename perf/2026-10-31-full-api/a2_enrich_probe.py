"""A2 取证二：把 `list_evaluations` 的 466 ms 归给谁（cProfile）+ 压测沙箱走的是哪条分支

探针一证明了「整段读入 + Python 过滤」确实贵（1867 行前缀读入 ~104 ms，其中精确类型
可省 ~40 ms；行数 ×5 后端点 466 → 1914 ms），但它同时暴露了一个更基础的问题：
`middleware/auth.py:123` 在 WEB_TOKEN 直通时把 `request.state.user_id` 置成 `"default"`，
而真实库里 1867 条 `eval.*` 事件的 user_id 全是账号 ID —— 也就是说
**基线里 `/api/evaluations` 的 19.8 ms 从来没跑到过读事件之后的任何一行代码**（SQL 侧
`user_id='default'` 直接 0 行）。本探针把两件事钉死：
1. 同一条请求在「沙箱态（user_id=default，0 行）」与「数据主人（真实账号，377 行）」下的耗时差；
2. 主人态这一次调用里，CPU 时间花在哪个函数上（cProfile 累计/单次）。
"""
from __future__ import annotations

import cProfile
import io
import os
import pstats
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

REPO = Path(r"D:/code/otherProjects/17_xianyu")
SCRATCH = Path(r"D:/tmp/xh-a2-probe2")

DATA_UID = ""  # main() 里由 resolve_data_owner() 填充；该 id 形如手机号，不写死进版本库


def resolve_data_owner(db_path: str) -> str:
    """真实库中 eval.* 事件的归属 user_id（在快照副本上解析，不涉任何凭据）"""
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "select user_id from events where type like 'eval.%' "
            "group by user_id order by count(*) desc limit 1"
        ).fetchone()[0]


def build_scaffold() -> None:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    (SCRATCH / "config").mkdir(parents=True)
    for f in ("config.yaml", "menu_registry.yaml", "eval.yaml", "param_rules.yaml"):
        src = REPO / "config" / f
        if src.exists():
            shutil.copy2(src, SCRATCH / "config" / f)
    (SCRATCH / "data").mkdir()
    with sqlite3.connect(str(REPO / "data" / "xianyu.db")) as s, \
            sqlite3.connect(str(SCRATCH / "data" / "xianyu.db")) as d:
        s.backup(d)


class FakeRequest:
    def __init__(self, user_id: str | None) -> None:
        self.state = type("S", (), {"user_id": user_id})()
        self.headers: dict[str, str] = {}
        self.cookies: dict[str, str] = {}
        self.query_params: dict = {}


NO_FILTER = dict(item_id=None, task_id=None, brand=None, min_score=None, max_score=None,
                 start_time=None, end_time=None, sold_filter="all", result_category=None,
                 min_price=None, max_price=None, include_out_of_range=False,
                 limit=200, offset=0, page_num=1, page_size=20)


def main() -> None:
    global DATA_UID
    os.environ.setdefault("XH_WITH_SCHEDULER", "0")
    build_scaffold()
    # 原版漏了这一步：DATA_UID 恒为空串，主人态按 user_id='' 查询永远 0 行，
    # cProfile 只归因到「什么都没发生」，A2′ 落地前后无法对比
    DATA_UID = resolve_data_owner(str(SCRATCH / "data" / "xianyu.db"))
    os.chdir(SCRATCH)
    sys.path.insert(0, str(REPO / "backend"))
    from xianyu_hunter.infra.repository import Repository
    from xianyu_hunter.web.routes import evaluations_list as EL

    db = str(SCRATCH / "data" / "xianyu.db")
    repo = Repository(db_path=db)
    container = type("C", (), {"repo": repo, "collector": None, "browser": None,
                               "price_strategy": None})()

    print("=" * 92)
    print("[1] 同一条请求，两种身份（压测沙箱走的是 A，真实控制台用户是 B）")
    for label, uid in (("A 沙箱态 user_id='default'（WEB_TOKEN 直通）", "default"),
                       ("B 数据主人 user_id=真实账号（377 条 eval.scored）", DATA_UID),
                       ("C 无中间件注入 user_id=None（不过滤）", None)):
        req = FakeRequest(uid)
        EL._MARKET_STATS_CACHE.clear()
        ts = []
        for _ in range(4):
            t0 = time.perf_counter()
            out = EL.list_evaluations(req, container=container, **NO_FILTER)
            ts.append((time.perf_counter() - t0) * 1000)
        print(f"  {label:<46} median {statistics.median(ts[1:]):8.1f} ms | "
              f"冷 {ts[0]:8.1f} ms | total={out['total']}")

    print("\n[2] 主人态单次调用的 CPU 归因（cProfile，按累计时间排序）")
    req = FakeRequest(DATA_UID)
    EL._MARKET_STATS_CACHE.clear()
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(3):
        EL.list_evaluations(req, container=container, **NO_FILTER)
    pr.disable()
    buf = io.StringIO()
    stats = pstats.Stats(pr, stream=buf).sort_stats("cumulative")
    stats.print_stats(28)
    print(buf.getvalue()[:4200])

    print("\n[3] 各阶段行数与「精确类型 / 不丢 COUNT」能省多少")
    rows_prefix, total_prefix = repo.list_events_by_type_prefix("eval.")
    rows_scored, total_scored = repo.list_events_by_type_prefix(EL._EVAL_SCORED_TYPE)
    print(f"  LIKE 'eval.%'   → {len(rows_prefix)} 行 + COUNT={total_prefix}")
    print(f"  = 'eval.scored' → {len(rows_scored)} 行 + COUNT={total_scored}")
    print(f"  Python 端随即丢弃：{len(rows_prefix) - len(rows_scored)} 行"
          f"（多读 {(len(rows_prefix) / max(len(rows_scored), 1)):.1f}×）")
    for label, kw in (("default（沙箱态）", {"user_id": "default"}),
                      (f"主人 {DATA_UID}", {"user_id": DATA_UID})):
        t0 = time.perf_counter()
        r, t = repo.list_events_by_type_prefix("eval.", **kw)
        dt_p = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        r2, t2 = repo.list_events_by_type_prefix(EL._EVAL_SCORED_TYPE, **kw)
        dt_s = (time.perf_counter() - t0) * 1000
        print(f"  {label:<20} 前缀 {dt_p:7.1f} ms/{len(r):>5} 行  "
              f"精确 {dt_s:7.1f} ms/{len(r2):>5}  行")

    repo.engine.dispose()
    os.chdir(REPO)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print("\n[清理] scratch 已删除（DB 为一致快照副本，真实库未被写入；未读取任何 cookie 文件）")


if __name__ == "__main__":
    main()
