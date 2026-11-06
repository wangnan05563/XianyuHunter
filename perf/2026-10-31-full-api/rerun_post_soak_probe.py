"""4 h 跑完后的立即复测：不重启 SUT，用 JMeter 重打「当初最慢的接口」与一个廉价接口。

为什么必须"不重启"：4 h 退化的三条解释（tasks 行数 / 线程池传染 / Python 客户端 GIL）
都已被否掉，只剩「运行期累积的服务端状态」或「当时系统状态」。只有在同一个 SUT 上
复测才能分离：仍慢 -> 服务端累积；恢复 -> 系统/引擎侧。

对照基线（冷启动 + 12 k tasks + 同一台机器）：
  /api/tasks/options 12 并发：p50 164 / p95 403 / p99 546 ms（results/rerun_single_endpoint_jmeter.json）
  /api/config       8 并发：p50 59.2 / p95 96.2 ms（results/rerun_ab_pool_probe.json 的 A 组）

用法（SUT 仍在 8011 上跑，且 4 h 引擎刚结束）：
    python rerun_post_soak_probe.py

**判读前必须扣掉一个混淆**：4 h 那 554 ms 是在「12 线程轮转 135 个接口」的混合场景
下测得的，同一时刻还有大量更慢的接口在飞（evaluations 195 ms、category-comparison
580 ms）。本脚本是「12 并发专打单一接口」。两者的在飞请求构成不同，因此：
  - 复测回到 164 ms 只证明「该接口在专打条件下正常」，**不能**直接推出「4 h 的
    554 ms 全来自引擎/系统侧」—— 混合场景里的争用仍可能贡献一部分；
  - 要把这部分也剥掉，需在同一 SUT 上再用同一 `mixed_biz_no_sse` 夹具短复跑
    （按时长 10 min），按 label 取 `tasks/options` 的 p50 与 554 直接比。
两件事都要做，先做本脚本（2 min，趁 4 h 刚结束状态未变），再做混合短复跑。
"""
import csv
import ctypes
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
JMX_TEMPLATE = REPO / "jmeter" / "tasks_options_12.jmx"
OUT_DIR = Path(r"D:\tmp\xh-perf\output")
ENV = Path(r"D:\tmp\xh-perf\sandbox\.env")
JMETER = Path(r"D:\code\Jmeter\apache-jmeter-5.6.3\bin\ApacheJMeter.jar")


def _token() -> str:
    return re.search(r"^WEB_TOKEN=(.+)$", ENV.read_text(encoding="utf-8"), re.M).group(1).strip()


def _run(path: str, tag: str, threads: int, duration: int, token: str) -> dict:
    """把模板的 path/token/线程数替换后跑一轮 JMeter，返回分位数。"""
    jmx = JMX_TEMPLATE.read_text(encoding="utf-8")
    jmx = jmx.replace("/api/tasks/options", path)
    jmx = jmx.replace("Bearer ${__P(token,)}", "Bearer " + token)
    tmp = Path(tempfile.gettempdir()) / f"post_soak_{tag}.jmx"
    tmp.write_text(jmx, encoding="utf-8")
    jtl = OUT_DIR / f"post_soak_{tag}.jtl"
    if jtl.exists():
        jtl.unlink()
    try:
        subprocess.run(
            ["java", "-jar", str(JMETER), "-n", "-t", str(tmp), "-l", str(jtl),
             "-j", str(OUT_DIR / f"post_soak_{tag}.log"),
             "-Jthreads=%d" % threads, "-Jduration=%d" % duration, "-Jramp=1"],
            check=True, capture_output=True, text=True, errors="replace",
        )
    except subprocess.CalledProcessError as e:
        # JMeter 的失败原因只在 stderr，被 check=True 吞掉就没法判断是「服务端慢」
        # 还是「JMX 写坏」，所以这里补印出来再原样抛出。
        sys.stderr.write((e.stderr or "")[-4000:] + "\n")
        raise
    finally:
        tmp.unlink(missing_ok=True)  # 含明文 token，失败路径也不能留
    els, codes = [], set()
    with jtl.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            els.append(float(r["elapsed"]))
            codes.add(r["responseCode"])
    els.sort()
    n = len(els)
    q = lambda x: els[min(n - 1, int(round(x / 100 * (n - 1))))]
    return {"samples": n, "codes": sorted(codes), "p50_ms": q(50), "p95_ms": q(95),
            "p99_ms": q(99), "max_ms": els[-1], "mean_ms": round(sum(els) / n, 1)}


def _verdict(r_opt: float, r_cfg: float):
    """把比值映射成 (verdict, 人读结论)。

    单独成函数是为了能在不跑 JMeter 的情况下把四档全验一遍：判读是这次实验的
    唯一产出，分支漏一档就等于实验白跑。
    """
    if r_opt >= 2.0 and r_cfg < 1.5:
        return "endpoint_runtime_accumulation", (
            "只有被压过的重接口变慢、廉价接口正常：指向该接口路径上的运行期累积（连接/页缓存/锁）")
    if r_opt >= 2.0 and r_cfg >= 1.5:
        return "global_runtime_state", (
            "两类接口同时变慢：指向全局运行期状态（线程池/GIL/系统资源）")
    if r_opt >= 1.5:
        # 1.5~2.0 这个中间带必须显式判：只设 >=2.0 与 <1.5 两档时，落在这里
        # 会一个分支都不命中、打印空白，事后只能凭肉眼猜 —— 而「部分累积」恰恰
        # 是最可能的结果（4 h 的 554 ms 若有一半来自空闲即可释放的排队，就落这里）
        return "partial", (
            "部分变慢（1.5~2.0x）：4 h 的劣化只有一部分可由运行期状态解释，"
            "另一部分仍属引擎/系统侧；两边都要记，不能只归一边")
    return "recovered", (
        "已恢复：4 h 的 554 ms 来自引擎/系统侧，而非服务端累积（需外置压测机复核）")


def main() -> None:
    if not JMX_TEMPLATE.exists():
        sys.exit("缺模板 %s" % JMX_TEMPLATE)
    tok = _token()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("== 复测 1：/api/tasks/options 12 并发 60 s（冷启动基线 p50 164 / p95 403）==")
    a = _run("/api/tasks/options", "options", 12, 60, tok)
    print("  ", json.dumps(a, ensure_ascii=False))

    print("== 复测 2：/api/config 8 并发 60 s（冷启动基线 p50 59.2 / p95 96.2）==")
    b = _run("/api/config", "config", 8, 60, tok)
    print("  ", json.dumps(b, ensure_ascii=False))

    base_opt, base_cfg = 164.0, 59.2
    ratio_opt = a["p50_ms"] / base_opt
    ratio_cfg = b["p50_ms"] / base_cfg
    print("\n[判读]")
    print("  options p50 相对冷启动基线 = %.2fx" % ratio_opt)
    print("  config  p50 相对冷启动基线 = %.2fx" % ratio_cfg)
    verdict, msg = _verdict(ratio_opt, ratio_cfg)
    print("  -> " + msg)

    summary = {"options_12t": a, "config_8t": b, "verdict": verdict,
               "baseline_cold": {"options_p50_ms": base_opt, "config_p50_ms": base_cfg},
               "ratio": {"options": round(ratio_opt, 2), "config": round(ratio_cfg, 2)}}
    (OUT_DIR / "post_soak_probe.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("  samples -> %s" % (OUT_DIR / "post_soak_probe.json"))


if __name__ == "__main__":
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    main()
