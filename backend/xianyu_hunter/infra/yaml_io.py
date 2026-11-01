"""YAML 读取的统一入口：libyaml 在就用 C loader，不在就退回纯 Python。

为什么要换：`yaml.safe_load` 绑的是 PyYAML 的**纯 Python** `SafeLoader`。同一份
7.2 KB / 35 个顶层键的 `config/config.yaml`，实测（`perf/2026-10-31-full-api/config_memo_probe.py`，
各 20 次采样取中位）解析 **52.5 ms → `CSafeLoader` 4.7 ms（11.3×）**，而读文件本身只要
0.36 ms —— 配置族接口的成本几乎全在这段解析上。

安全面没有变宽：`CSafeLoader` 是 `SafeLoader` 的 C 实现，不是 `yaml.load` 的裸
`Loader` / `FullLoader` —— 同样拒绝 `!!python/object` 之类的任意对象构造，
`tests/test_yaml_io_equivalence.py` 把这条钉住了。

**写侧为什么不在这里**（重要，别再顺手加回来）：`CSafeDumper` 对超出 BMP 的字符
（emoji，如模板图标 `📷` = U+1F4F7）**不遵守 `allow_unicode=True`**，会写成
`"\U0001F4F7"`，而纯 Python dumper 写原字符 —— 同一份 dict 两边产物不逐字节相同。
配置文件的写盘、备份与 `_diff` 都建立在「产物稳定」上，所以 dump 继续走
`yaml.safe_dump`。等价性用例里保留了一条断言专门复现这个分歧，防止后来人「补齐对称性」
时把写盘格式换掉。

回退分支不是防御想象的场景：PyPI 上的 PyYAML 是否带 `_yaml`（libyaml 绑定）取决于安装
平台，精简镜像里纯 Python 后端是真实存在的形态。
"""
from __future__ import annotations

from typing import Any

import yaml

_LOADER = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader


def safe_load(stream: Any) -> Any:
    """等价于 `yaml.safe_load`，但优先走 libyaml 的 C 后端（约 11× 快）。

    入参可以是文本也可以是文件对象（`yaml.safe_load` 的两种用法这里都保留）。
    解析失败抛出的异常类与纯 Python 后端一致（都是 `yaml.YAMLError` 的子类），
    所以 `except yaml.YAMLError` 的既有覆盖面不变。
    """
    return yaml.load(stream, Loader=_LOADER)
