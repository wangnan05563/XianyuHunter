"""C2 取证四：被节流掉的「每请求一次订单扫描」到底值多少

阶梯实测（`c2_ladder.py` before_r1/r2 + 本轮对照）证明：**HTTP 平均延迟在这台机器上
分辨不出这个改动**。同一份代码（未节流基线）在 c=48 三轮分别是 256.2 / 117.7 / 233.5 ms，
而「近乎空处理器」这一条对照线自己就在 57.9 ~ 178.4 ms 之间摆 —— 摆幅比改动期望收益还大。
拿这种数字宣称「/me 降到 ≤50 ms」是把噪声当结论。

所以本探针改测**确定量**：每请求被省掉的那条 `list_orders(limit=200)` + 规则遍历，
到底占多少毫秒、以及它随订单表规模怎么长。StaticPool 单连接（`infra/db_models.py:872`）
意味着这段时间是**独占全进程那一条连接**的时间，不是本请求自己的私事。

只读真实数据：临时库里造合成订单，不碰 `data/xianyu.db`。
"""
from __future__ import annotations

import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
TMP = Path(r"D:/tmp/xh-c2-scan")
TMP.mkdir(parents=True, exist_ok=True)
import os
os.chdir(TMP)

from sqlalchemy import insert  # noqa: E402

from xianyu_hunter.infra.db_models import OrderRow, _utcnow  # noqa: E402
from xianyu_hunter.infra.repository import Repository  # noqa: E402
from xianyu_hunter.web.services import notification_engine as eng  # noqa: E402

UID = "probe-user"


class _Shim:
    def __init__(self, repo):
        self.repo = repo


def seed(repo, n: int) -> None:
    """灌 n 条待支付订单（全属同一用户，最坏情况：每条都要过阈值判定）"""
    now = _utcnow()
    with repo.engine.begin() as conn:
        conn.execute(insert(OrderRow), [
            {
                "id": f"o-{i}", "item_id": f"i-{i}", "price": 1.0,
                "status": "pending_pay", "user_id": UID,
                "created_at": now - timedelta(minutes=10 + i % 40),
            }
            for i in range(n)
        ])


def bench(fn, reps: int = 30, reset_gate_each: bool = False) -> float:
    """reset_gate_each=True 测「放行的一轮」（每请求真的去扫）；
    False 测「窗口内的后续请求」（只有闸门判定，这才是 /me 稳态）"""
    if reset_gate_each:
        eng.reset_scan_gate()
        fn()  # 预热
        ts = []
        for _ in range(reps):
            eng.reset_scan_gate()  # 清零在计时区之外
            t0 = time.perf_counter()
            fn()
            ts.append((time.perf_counter() - t0) * 1000)
        return statistics.median(ts)
    fn()
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def main() -> None:
    print(f"{'订单行数':>8} {'list_orders(200)':>18} {'放行的一轮扫描':>18} {'窗口内的 /me':>16}")
    for n in (0, 50, 200, 2000):
        db = TMP / f"orders_{n}.db"
        if db.exists():
            db.unlink()
        repo = Repository(db_path=str(db))
        if n:
            seed(repo, n)
        list_ms = bench(lambda r=repo: r.list_orders(limit=200, user_id=UID))
        # 通知写入走替身仓储：只测扫描侧（list_orders + 阈值遍历），
        # 不把 add_notification 的写放大混进「这条 SQL 值多少毫秒」里
        sink: list[str] = []

        class _ScanOnly:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def list_orders(self, **kw):
                return self._inner.list_orders(**kw)

            def add_notification(self, **kw):
                sink.append(kw["dedup_key"])
                return None

        scan = lambda r=repo: eng.scan_order_timeouts(
            _Shim(_ScanOnly(r)), user_id=UID,
            min_interval=eng.ORDER_SCAN_MIN_INTERVAL_SEC,
        )
        open_ms = bench(scan, reps=30, reset_gate_each=True)
        gated_ms = bench(scan, reps=30)
        print(f"{n:>8} {list_ms:>15.2f} ms {open_ms:>15.2f} ms {gated_ms:>13.3f} ms")
        del repo


if __name__ == "__main__":
    main()
