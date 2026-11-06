"""原生 JMeter 阶梯：按并发档位跑同一份 jmx，找服务端真实拐点

为什么要脚本而不是手敲命令：四档 × 每档一次 JVM 启动，手敲容易漏参数（尤其
`-Jthreads` 与 `-Jtoken`），而且每档都要用同一套分位算法汇总才可比。回放引擎的
96 并发已近客户端上限，本脚本的目的就是越过那条线看服务端在哪饱和。

前提：`rerun_jmx_for_jmeter.py` 已派生 `D:/tmp/xh-perf/jmx_native/<base>_native.jmx`，
SUT 在 8011 上跑着（本脚本不启停它）。

用法：
    python rerun_jmeter_ladder.py reads_light 16 48 96 192 --duration 120
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rerun_jmeter_summary import summarize  # noqa: E402  同口径的分位算法

REPO = Path(__file__).resolve().parent
WORK = Path(r"D:\tmp\xh-perf")
JMX_DIR = WORK / "jmx_native"
OUT_DIR = WORK / "output"
RESULTS = REPO / "results"
JMETER_HOME = Path(r"D:\code\Jmeter\apache-jmeter-5.6.3")
JAVA = Path(r"D:\code\Java\jdk-25.0.1\bin\java.exe")


def _load_jtl(path: Path) -> list[dict]:
    import csv
    rows = []
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({"ts": float(r["timeStamp"]), "el": float(r["elapsed"]),
                         "ok": r["success"] == "true", "label": r["label"],
                         "code": r["responseCode"]})
    return rows


def run_one(base: str, threads: int, duration: int, token: str, port: int) -> dict:
    jmx = JMX_DIR / f"{base}_native.jmx"
    jtl = OUT_DIR / f"jmeter_{base}_t{threads}.jtl"
    if jtl.exists():
        jtl.unlink()
    cmd = [
        str(JAVA), f"-Djmeter.home={JMETER_HOME}",
        "-jar", str(JMETER_HOME / "bin" / "ApacheJMeter.jar"),
        "-n", "-t", str(jmx),
        f"-Jthreads={threads}", f"-Jramp={max(1, threads // 12)}",
        f"-Jduration={duration}", f"-Jport={port}", f"-Jtoken={token}",
        "-l", str(jtl),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not jtl.exists():
        raise SystemExit(f"t{threads} 未产出 jtl：{proc.stdout[-400:]} {proc.stderr[-400:]}")
    summary = summarize(_load_jtl(jtl))
    return {"threads": threads, **{k: summary[k] for k in
                                   ("total", "tps", "p50", "p95", "p99", "errors", "error_rate_pct")}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", help="reads_light / reads_heavy / writes_idem")
    ap.add_argument("levels", nargs="+", type=int, help="并发档位，如 16 48 96 192")
    ap.add_argument("--duration", type=int, default=120)
    ap.add_argument("--port", type=int, default=8011)
    args = ap.parse_args()

    token = (WORK / "token.txt").read_text(encoding="ascii").strip()
    rows = []
    for n in args.levels:
        print(f"[run] {args.base} t={n} duration={args.duration}s ...", flush=True)
        row = run_one(args.base, n, args.duration, token, args.port)
        print(f"      tps={row['tps']} p50={row['p50']} p95={row['p95']} "
              f"p99={row['p99']} err={row['error_rate_pct']}%", flush=True)
        rows.append(row)

    out = RESULTS / f"rerun_jmeter_ladder_{args.base}.json"
    out.write_text(json.dumps({"base": args.base, "duration_s": args.duration,
                               "levels": rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"[json] {out}")


if __name__ == "__main__":
    main()
