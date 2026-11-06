"""把入库 jmx 派生为原生 JMeter 可直接跑的版本

为什么需要派生：入库 jmx 里**没有 `HeaderManager`** —— 鉴权是
`python_replay` 引擎在请求层注入的（`target.auth.token_file`），原生 JMeter
拿到的是 401。这里补一个 `Authorization: Bearer ${__P(token)}`，并把
线程数 / 爬坡 / 时长改成 `${__P(...)}` 变量，一份文件就能跑任意并发档。

为什么 token 走 `-J` 而不是写进文件：令牌不进仓库；运行时从
`D:/tmp/xh-perf/token.txt` 读出来由命令行传入。

用法：
    python rerun_jmx_for_jmeter.py            # 派生三份（读/写/混合）
    jmeter -n -t D:/tmp/xh-perf/jmx_native/reads_light_native.jmx \
           -Jthreads=96 -Jduration=300 -Jhost=127.0.0.1 -Jport=8011 -Jtoken=<token> \
           -l D:/tmp/xh-perf/output/jmeter_light_t96.jtl
"""
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent
OUT = Path(r"D:\tmp\xh-perf\jmx_native")
SOURCES = ("reads_light.jmx", "reads_heavy.jmx", "writes_idem.jmx", "mixed_biz_no_sse.jmx")


def _new_header_manager() -> ET.Element:
    hm = ET.Element("HeaderManager", {
        "guiclass": "HeaderManagerGui", "testclass": "HeaderManager",
        "testname": "Auth Header", "enabled": "true",
    })
    col = ET.SubElement(hm, "collectionProp", {"name": "HeaderManager.headers"})
    ep = ET.SubElement(col, "elementProp", {"name": "", "elementType": "Header"})
    ET.SubElement(ep, "stringProp", {"name": "Header.name"}).text = "Authorization"
    ET.SubElement(ep, "stringProp", {"name": "Header.value"}).text = "Bearer ${__P(token)}"
    return hm


def _parametrize(tg: ET.Element) -> None:
    """把线程数/爬坡/时长换成 -J 变量，原值作为默认值"""
    want = {
        "ThreadGroup.num_threads": "1",
        "ThreadGroup.ramp_time": "1",
        "ThreadGroup.duration": "300",
    }
    for prop in tg.iter("stringProp"):
        name = prop.get("name")
        if name in want:
            prop.text = "${__P(%s,%s)}" % (
                {"ThreadGroup.num_threads": "threads",
                 "ThreadGroup.ramp_time": "ramp",
                 "ThreadGroup.duration": "duration"}[name], want[name])


def derive(src: Path, dst: Path) -> int:
    tree = ET.parse(src)
    root = tree.getroot()
    parents = {c: p for p in root.iter() for c in p}
    added = 0
    for tg in root.iter("ThreadGroup"):
        _parametrize(tg)
        parent = parents[tg]
        idx = list(parent).index(tg)
        ht = parent[idx + 1]
        assert ht.tag == "hashTree", "ThreadGroup 后必须是 hashTree"
        ht.insert(0, _new_header_manager())
        ht.insert(1, ET.Element("hashTree"))
        added += 1
    if not added:
        raise SystemExit(f"{src.name}: 没找到 ThreadGroup")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tree.write(dst, encoding="utf-8", xml_declaration=True)
    return added


def main() -> None:
    for name in SOURCES:
        src = REPO / "jmx" / name
        if not src.exists():
            print(f"[skip] {name} 不存在（先跑 rerun_prepare_soak.py 生成 mixed_biz_no_sse）")
            continue
        dst = OUT / name.replace(".jmx", "_native.jmx")
        n = derive(src, dst)
        print(f"[ok] {name} -> {dst.name}（{n} 个 ThreadGroup 已加 Auth Header 并参数化）")


if __name__ == "__main__":
    main()
