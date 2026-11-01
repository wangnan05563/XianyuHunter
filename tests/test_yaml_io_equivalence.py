"""C6：`infra/yaml_io.safe_load` 必须与 `yaml.safe_load` 可互换

这条等价性是「把配置读取切到 libyaml C 后端」的前置判据（动机与实测数字见
`perf/2026-10-31-full-api/OPTIMIZATIONS.md` §C6）。要同时成立才算安全：

1. **读出的结构相等**（含交叉：纯 Python 后端写出的文本，C loader 读回来必须一模一样；
   仓库里 6 份真实 YAML 逐个比对）。
2. **异常类不变** —— `menu_manager` 靠 `except yaml.YAMLError` 兜住坏文件；换后端如果
   换了异常类型，那条 `except` 会静默失去覆盖面。
3. **反序列化面没有变宽** —— `!!python/object` 之类标签必须照旧被拒。
4. **文件对象入参仍支持** —— `yaml_config.load_yaml` / `api_templates._load_presets`
   传的是句柄而不是文本。
5. **后端选择要跟 libyaml 可用性走** —— 否则将来某天悄悄退回纯 Python，提速没了而用例全绿。

另有一条用例钉住**写侧为什么不切**（`CSafeDumper` 把 emoji 写成 `\\U0001F4F7`，产物不再
逐字节相同）：它断言的是「C dumper 与纯 Python dumper 在 astral 字符上不一致」，用来挡住
「顺手把 dump 也换过去」的下一步。本文件只读仓库里的 YAML，绝不写盘。
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest
import yaml

from xianyu_hunter.infra import yaml_io
from xianyu_hunter.paths import get_config_dir

# 仓库里真实在用的三组 dump 参数（逐个调用点核对过）——读侧要能原样吃回这三类产物
KW_FULL = {"allow_unicode": True, "sort_keys": False, "default_flow_style": False}
KW_NOFLOW = {"allow_unicode": True, "sort_keys": False}
KW_UNICODE = {"allow_unicode": True, "default_flow_style": False}
KWARGS_CASES = [
    pytest.param(KW_FULL, id="allow_unicode+sort_keys+flow"),
    pytest.param(KW_NOFLOW, id="allow_unicode+sort_keys"),
    pytest.param(KW_UNICODE, id="allow_unicode+flow"),
]

# 形状取自真实配置族：中文值、需要引号的裸词、emoji 图标、空容器、嵌套列表、时间戳
SHAPES: dict[str, object] = {
    "nested": {"search": {"keyword": "二手 相机", "min_price": 10.5, "max_price": None}},
    "empty_containers": {"a": {}, "b": [], "c": {"d": {}}},
    "needs_quoting": {
        "boolish": ["yes", "no", "on", "off"],
        "numeric_strings": ["12345", "01", "1.0", "1e5"],
        "punctuation": ["带: 冒号", "带# 井号", "尾随空格 ", "  前置空格"],
        "quote_mix": ["单'引号", '双"引号', "两种'\"都有"],
    },
    "multiline": {"note": "第一行\n第二行\n第三行\n", "block": "a\n\nb"},
    "unicode_emoji": {"icon": "\U0001F4CB", "title": "抢单模板 · 摄影器材（佳能/尼康）"},
    "list_of_dicts": [
        {"keyword": "A", "mode": "notify", "icon": "\U0001F4CB"},
        {"keyword": "B", "mode": "auto", "min_price": None},
    ],
    "timestamps": {"created": datetime.date(2026, 10, 31),
                   "at": datetime.datetime(2026, 10, 31, 12, 35, 1)},
    "deep_list_dict": {"l": [{"m": [{"k": "v"}]}], "n": [[1, 2], [3]]},
    "booleans_and_ints": {"t": True, "f": False, "i": -7, "z": 0, "big": 10**12},
    "top_level_list": ["纯", "列表", "顶层"],
}
SAMPLE_NAMES = list(SHAPES)


# ==================== 1. 读出的结构相等 ====================
@pytest.mark.parametrize("name", SAMPLE_NAMES)
@pytest.mark.parametrize("kwargs", KWARGS_CASES, ids=[p.id for p in KWARGS_CASES])
def test_c_loader_reads_what_python_dumper_writes(name, kwargs):
    data = SHAPES[name]
    text = yaml.safe_dump(data, **kwargs)
    assert yaml_io.safe_load(text) == yaml.safe_load(text), f"{name} 在 {kwargs} 下读得不一样"


@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_round_trip_through_c_loader(name):
    data = SHAPES[name]
    text = yaml.safe_dump(data, **KW_FULL)
    assert yaml_io.safe_load(text) == data


def test_empty_document_returns_none_like_safe_load():
    """调用点普遍写 `... or {}`，前提是空文档返回 None 而不是别的"""
    assert yaml_io.safe_load("") is yaml.safe_load("") is None
    assert yaml_io.safe_load("# 只有注释\n") is None


# ==================== 2. 异常类必须与纯 Python 后端一致 ====================
@pytest.mark.parametrize("text", [
    pytest.param("a: 1\n\tb: 2\n", id="tab_indent"),
    pytest.param("key: [unclosed\n", id="flow_not_closed"),
    pytest.param("a: 1\n  b: 2\n", id="bad_indent"),
    pytest.param("key: 'unclosed\n", id="quote_not_closed"),
])
def test_malformed_raises_same_class_as_pyyaml(text):
    with pytest.raises(yaml.YAMLError) as py_exc:
        yaml.safe_load(text)
    with pytest.raises(type(py_exc.value)) as c_exc:
        yaml_io.safe_load(text)
    # menu_manager 那类 `except yaml.YAMLError` 的覆盖面不能缩
    assert isinstance(c_exc.value, yaml.YAMLError)


# ==================== 3. 反序列化面没有变宽 ====================
UNSAFE_TAGS = [
    pytest.param("a: !!python/object/apply:os.getpid []", id="object_apply"),
    pytest.param("a: !!python/name:os.getcwd", id="python_name"),
    pytest.param("a: !!python/module:os", id="python_module"),
]


@pytest.mark.parametrize("payload", UNSAFE_TAGS)
def test_rejects_python_object_tags(payload):
    with pytest.raises(yaml.YAMLError):
        yaml_io.safe_load(payload)
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(payload)


def test_rejection_is_not_vacuous():
    """上面那条必须真的能区分后端，否则它等于没测

    变异验证逼出来的：第二腿我把 `_LOADER` 换成 `FullLoader`，「拒绝 python 标签」那条
    用例照样全绿 —— 因为 `!!python/object/apply` 连 FullLoader 都拒。实测只有
    `!!python/name` 能把 Safe 与 Full 分开（C 侧同理），所以这里正面断言
    「FullLoader 接受、我们拒」，把判据本身钉住。
    """
    payload = "a: !!python/name:os.getcwd"
    import os

    # 这里的 FullLoader 是刻意用的：唯一入参是上面这行写死的常量，作用只是证明
    # 「Safe 后端确实比 Full 后端多拒了一个标签」。生产读盘一律走 yaml_io.safe_load。
    assert yaml.load(payload, Loader=yaml.FullLoader)["a"] is os.getcwd
    with pytest.raises(yaml.YAMLError):
        yaml_io.safe_load(payload)
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(payload)


# ==================== 4. 文件对象入参仍支持 ====================
def test_accepts_file_objects(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump({"search": {"keyword": "相机"}}, **KW_FULL), encoding="utf-8")
    with p.open(encoding="utf-8") as f:
        assert yaml_io.safe_load(f) == {"search": {"keyword": "相机"}}


# ==================== 5. 仓库里真实配置文件：两后端读出的结构必须相等 ====================
def _repo_yaml_files() -> list[Path]:
    files = sorted(Path(get_config_dir()).glob("*.yaml"))
    presets = Path(__file__).resolve().parents[1] / "backend" / "xianyu_hunter" / "data" / "preset_templates.yaml"
    if presets.exists():
        files.append(presets)
    return [f for f in files if f.stat().st_size > 0]


REPO_FILES = _repo_yaml_files()


@pytest.mark.skipif(not REPO_FILES, reason="仓库里没有可读的 YAML 样本")
@pytest.mark.parametrize("path", REPO_FILES, ids=[f.name for f in REPO_FILES])
def test_real_repo_files_parse_identically(path: Path):
    text = path.read_text(encoding="utf-8")
    assert yaml_io.safe_load(text) == yaml.safe_load(text)


# ==================== 6. 后端选择本身要有牙 ====================
def test_backend_follows_libyaml_availability():
    """libyaml 在就必须用 C loader —— 否则这轮优化等于没做，而用例全绿"""
    has_c = hasattr(yaml, "CSafeLoader")
    assert (yaml_io._LOADER is getattr(yaml, "CSafeLoader", None)) is has_c


# ==================== 7. 写侧为什么不切（挡住「顺手把 dump 也换过去」） ====================
def test_c_dumper_would_mangle_astral_chars_so_dump_stays_python():
    """`CSafeDumper` 对 emoji 不遵守 `allow_unicode=True`

    模板图标就是这类字符（`preset_templates.yaml` 里的 `📷`）。切了 dump 会把磁盘上的
    人可读字符改成 `"\\U0001F4F7"` 转义，写盘/备份/`_diff` 的产物稳定性随之失效 ——
    所以 `yaml_io` 只提供读侧。这条断言的是「两边确实不一致」这个事实。
    """
    if not hasattr(yaml, "CSafeDumper"):
        pytest.skip("此环境没有 libyaml，C dumper 的分歧无从复现")
    data = {"icon": "\U0001F4F7"}
    c_out = yaml.dump(data, Dumper=yaml.CSafeDumper, **KW_FULL)
    py_out = yaml.safe_dump(data, **KW_FULL)
    assert c_out != py_out, (
        "C dumper 的 emoji 行为变了：如果它现在也写原字符，可以重新评估把写侧一起切过去"
        "（届时需重跑逐字节对比，而不只看这一条）"
    )
    assert yaml.safe_load(c_out) == yaml.safe_load(py_out) == data, (
        "两种写法至少必须等价可还原，否则历史文件里的转义写法读不回来"
    )
