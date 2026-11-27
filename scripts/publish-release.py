#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用 GitHub Release 发布脚本（零依赖，仅 Python 标准库）【模板，去硬编码】

把「创建 Release + 附带说明（读 Markdown 文件）+ 上传资产」三步原子化做完。
所有项目相关参数（仓库坐标 / 标签 / 资产路径 / 说明文件 / token 来源）全部来自
配置文件或命令行参数，脚本本身不硬编码任何项目名 / 路径 / 版本 / token。

两种用法：
  1) 直接传资产与说明（最通用）：
     python publish-release.py <主资产路径> <说明md路径> [显示名] [repo] [tag] \
         [--asset <附加资产> ...] [--release-name <Release标题>]

  2) 读配置（推荐，去硬编码）：
     python publish-release.py --config config.json [--key github_release]
     脚本从 config 的 github_release 块解析 repo / tag / 资产(glob) / 说明(<VER>模板)。

原子发布（H4）：
  - 首次发布先建 draft；全部资产上传并校验 state/size/digest 后，最后一步才 PATCH 公开。
    任一资产失败都停在草稿，线上不会出现“只有 Full、缺 Lite”的半成品。
  - 已公开 Release 替换同名资产时，先把新文件以临时名上传并校验，再“旧名→备份、
    临时名→正式名”切换，最后删备份；上传失败不会先删掉线上旧资产。
  - Release 标题与资产文件名分离：标题用 --release-name / GH_RELEASE_NAME，
    不再让最后上传的 -Lite.exe 覆盖标题（M1）。

token 读取优先级（见 github_release.token 配置）：
  环境变量 GH_TOKEN  >  本地 .env（gitignored，绝不入库）  >  git credential fill 兜底
说明文件读取：utf-8 -> gbk -> 系统默认 编码回退（兼容中文 Windows 记事本 GBK 保存）。

幂等：Release 已存在则复用；同名资产 digest/size 一致则跳过上传。网络抖动自动重试。

完整规则：SKILL.md 规则 16 + references/retrospective.md 维度 16 + config/traps.md 陷阱 62-66。
"""
import argparse
import hashlib
import http.client
import json
import locale
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API_BASE = os.environ.get("GH_API_BASE", "https://api.github.com").rstrip("/")
REPO = (os.environ.get("GH_REPO") or "").strip()
TAG = (os.environ.get("GH_TAG") or "").strip()
TOKEN = (os.environ.get("GH_TOKEN") or "").strip()
TARGET = (os.environ.get("GH_TARGET") or "").strip()
DRAFT = os.environ.get("GH_DRAFT", "0") == "1"
PRERELEASE = os.environ.get("GH_PRERELEASE", "0") == "1"
DRY_RUN = os.environ.get("GH_DRY_RUN", "0") == "1"


def _load_dotenv():
    """从本地 .env 读取配置（GitHub Token 等），不覆盖已存在的环境变量。

    查找顺序：当前工作目录（仓库根）-> 脚本所在目录。.env 必须被 .gitignore 忽略，
    不会入库；入库模板请用 .env.example（占位符）。配套陷阱 66。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    for d in (os.getcwd(), here):
        p = os.path.join(d, ".env")
        if p not in candidates:
            candidates.append(p)
    for p in candidates:
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if len(v) >= 2 and v[0] in ("'", '"') and v[-1] == v[0]:
                        v = v[1:-1]
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass


def _read_text(path):
    """读取文本文件，编码回退：utf-8 -> gbk -> 系统默认。

    兼容中文 Windows 下记事本以 GBK 保存的 .md / .env 等文件（陷阱 65）。
    """
    for enc in ("utf-8", "gbk", None):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, encoding=locale.getpreferredencoding(False)) as f:
        return f.read()


_load_dotenv()


def sh(cmd):
    """跑一条 shell 命令，返回 stdout（失败返回空串，不抛）。"""
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def detect_repo():
    url = sh("git remote get-url origin")
    url = url.replace("git@github.com:", "https://github.com/").replace(".git", "")
    if "github.com/" in url:
        return url.split("github.com/", 1)[1].strip("/")
    return ""


def detect_tag():
    return sh("git describe --tags --abbrev=0")


def detect_branch():
    b = sh("git rev-parse --abbrev-ref HEAD")
    return b or "master"


def fetch_token():
    """复用 git 凭据库里的 github.com 密码（即 PAT/凭证）。

    必须用 Python 子进程直接喂 stdin，不能依赖 shell 的 `printf | git credential
    fill`（Windows 原生 CMD 没有 printf，.bat 环境下会失败，陷阱 64）。
    """
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=30,
        )
        for line in (proc.stdout or "").splitlines():
            if line.startswith("password="):
                return line[len("password="):].strip()
    except Exception:
        pass
    return ""


def api(path, data=None, method=None, base=None):
    base = base or API_BASE
    body = json.dumps(data).encode("utf-8") if data is not None else None
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                base + path, data=body,
                method=method or ("POST" if body else "GET"),
                headers={
                    "Authorization": "Bearer " + TOKEN,
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "publish-release",
                    "Content-Type": "application/json",
                })
            with urllib.request.urlopen(req, timeout=90) as r:
                txt = r.read().decode("utf-8")
                return r.status, (json.loads(txt) if txt else None)
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(payload) if payload else None
            except Exception:
                parsed = None
            last = (e.code, parsed or payload)
            if e.code in (409, 422):
                return e.code, parsed
            if e.code in (403, 429, 500, 502, 503):
                # 指数退避，避免 GitHub 限流时固定 10s 空转（L6）。
                time.sleep(min(60, 2 ** attempt))
                continue
            return last
        except Exception as e:
            last = ("EXC", str(e))
            time.sleep(min(60, 2 ** attempt))
    return last


def get_release_by_id(release_id):
    st, resp = api(f"/repos/{REPO}/releases/{release_id}")
    if st == 200 and isinstance(resp, dict):
        return resp
    return None


def get_existing_release_by_tag(tag):
    """按 tag 找 Release，包含 draft。

    历史坑（M4）：GET /releases/tags/{tag} 对 draft 返回 404，于是 GH_DRAFT=1 时
    创建后的回查永远找不到，脚本报“Release 不存在”。这里先按 tag 查，404 再翻
    releases 列表按 tag_name 精确匹配（列表里 draft 是可见的）。
    """
    st, resp = api(f"/repos/{REPO}/releases/tags/{tag}")
    if st == 200 and isinstance(resp, dict):
        return resp
    st, resp = api(f"/repos/{REPO}/releases?per_page=100")
    if st == 200 and isinstance(resp, list):
        for rel in resp:
            if isinstance(rel, dict) and rel.get("tag_name") == tag:
                return rel
    return None


def ensure_tag(tag, target):
    """确保 release tag 已在远端存在，再创建 Release。

    2026-11-27 实测：对不存在的 tag 直接 POST /releases（draft=true），GitHub 会把
    draft 的 tag_name 记成 `untagged-<hash>`；后续 PATCH draft=false 不会补建真正的
    tag，最终得到一个公开但 URL 为 untagged-... 的 Release。必须先把 tag 指向
    target 分支的 HEAD，再创建 Release，GitHub 才会使用正式 tag 名。
    """
    st, resp = api(f"/repos/{REPO}/git/ref/tags/{urllib.parse.quote(tag, safe='')}")
    if st == 200 and isinstance(resp, dict):
        return resp
    if st not in (404,):
        print("查询 tag 失败:", st, json.dumps(resp, ensure_ascii=False)[:600])
        sys.exit(1)

    sha = None
    if target:
        st, ref = api(f"/repos/{REPO}/git/ref/heads/{urllib.parse.quote(target, safe='')}")
        if st == 200 and isinstance(ref, dict):
            sha = ((ref.get("object") or {}).get("sha") or "").strip()
    if not sha:
        st, commit = api(f"/repos/{REPO}/commits/{urllib.parse.quote(target or 'main', safe='')}")
        if st == 200 and isinstance(commit, dict):
            sha = (commit.get("sha") or "").strip()
    if not sha:
        sys.exit(f"无法解析 target={target!r} 的 commit sha，不能创建 tag {tag}")

    st, resp = api(f"/repos/{REPO}/git/refs", {
        "ref": f"refs/tags/{tag}",
        "sha": sha,
    })
    if st in (200, 201):
        print(f"已创建 tag {tag} -> {sha[:12]}")
        return resp
    if st == 422:
        # 并发/已存在：再查一次，存在即视为成功。
        st2, existing = api(f"/repos/{REPO}/git/ref/tags/{urllib.parse.quote(tag, safe='')}")
        if st2 == 200 and isinstance(existing, dict):
            print(f"tag {tag} 已存在，继续")
            return existing
    print("创建 tag 失败:", st, json.dumps(resp, ensure_ascii=False)[:600])
    sys.exit(1)


def update_release(release_id, name, body, draft=None, prerelease=None, tag_name=None):
    """刷新 Release 名称/说明；draft/prerelease/tag_name 只在显式传入时修改。

    名称与资产显示名彻底分离（M1）：旧代码把“最后上传的资产文件名”当 Release 名称，
    于是 Full 先建、Lite 后传，最终标题变成 ...-Lite.exe。现在只写 release_name。

    tag_name：GitHub 对 draft 会把不存在的 tag 记成 untagged-<hash>，
    即使 tag 已预先创建，PATCH draft=false 也不会自动绑定；必须在更新时显式
    写回 tag_name，Release URL 才会是 /releases/tag/<版本>。
    """
    payload = {"name": name, "body": body}
    if draft is not None:
        payload["draft"] = draft
    if prerelease is not None:
        payload["prerelease"] = prerelease
    if tag_name:
        payload["tag_name"] = tag_name
    st, resp = api(f"/repos/{REPO}/releases/{release_id}", payload, method="PATCH")
    if st not in (200, 201):
        print("更新 Release 失败:", st, json.dumps(resp, ensure_ascii=False)[:600])
        sys.exit(1)
    return resp


def delete_asset(asset_id, fatal=True):
    if not asset_id:
        return
    st, resp = api(f"/repos/{REPO}/releases/assets/{asset_id}", method="DELETE")
    # 204 No Content 是正常删除；404 说明已经没了，同样视为可继续
    if st not in (200, 204, 404):
        if fatal:
            print("删除旧资产失败:", st, json.dumps(resp, ensure_ascii=False)[:600])
            sys.exit(1)
        print(f"[WARN] 删除备份资产失败（不影响已发布资产）: {st}")


def rename_asset(asset_id, new_name):
    """GitHub 允许 PATCH 资产 name 做原子改名，用于临时名→正式名/旧名备份。"""
    st, resp = api(f"/repos/{REPO}/releases/assets/{asset_id}", {"name": new_name}, method="PATCH")
    if st not in (200, 201):
        print("重命名资产失败:", st, json.dumps(resp, ensure_ascii=False)[:600])
        sys.exit(1)
    return resp


def local_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _remote_asset_matches(asset, size, sha):
    """返回 (ok, detail)。digest 存在时必须比 sha256，不能只看体积。

    H6：同体积不同内容（重新打包时间戳变化、同名旧包）在只有 size/state 校验时
    会被误判“无需上传”。GitHub 资产已返回 digest=sha256:...，有则必须比。
    """
    if not isinstance(asset, dict):
        return False, "资产不存在"
    if asset.get("state") != "uploaded":
        return False, f"state={asset.get('state')}"
    remote_size = int(asset.get("size") or -1)
    if remote_size != int(size):
        return False, f"size {remote_size} != {size}"
    digest = (asset.get("digest") or "").strip().lower()
    if digest.startswith("sha256:"):
        remote_sha = digest.split(":", 1)[1]
        if remote_sha != sha:
            return False, f"sha256 {remote_sha[:12]}… != {sha[:12]}…"
        return True, "sha256+size"
    return True, "size（远端无 digest，降级校验）"


def verify_remote(release_id, asset_name, local_size, local_sha):
    """按 release id 回查（draft 也能查），校验 name/state/size/digest。"""
    if not release_id:
        sys.exit("校验失败：缺少 release id")
    rel = get_release_by_id(release_id)
    if not rel:
        sys.exit(f"校验失败：release id {release_id} 不存在")
    hit = None
    detail = ""
    digest_seen = False
    for _ in range(10):
        rel = get_release_by_id(release_id) or rel
        hit = next((a for a in (rel.get("assets") or []) if a.get("name") == asset_name), None)
        ok, detail = _remote_asset_matches(hit, local_size, local_sha)
        digest = (hit.get("digest") or "") if isinstance(hit, dict) else ""
        digest_seen = bool(digest)
        if ok and digest_seen:
            break
        if ok and not digest_seen:
            # digest 可能在上传完成后延迟出现；多等两轮，最终没有才降级 size 校验。
            time.sleep(3)
            continue
        time.sleep(3)
    if not hit:
        sys.exit(f"校验失败：Release {release_id} 上没有资产 {asset_name}")
    ok, detail = _remote_asset_matches(hit, local_size, local_sha)
    if not ok:
        sys.exit(f"校验失败：资产 {asset_name}：{detail}")
    if not (hit.get("digest") or ""):
        print(f"[WARN] 资产 {asset_name} 远端未返回 digest，已按 size={local_size} 校验")
    print(f"校验通过：Release {rel.get('html_url')} 资产 {asset_name} "
          f"{hit.get('size')} bytes state=uploaded ({detail})")
    return rel


def _upload_stream(url, path, size):
    """流式上传，避免把 292MB 安装包一次性读进内存（L6）。"""
    parts = urllib.parse.urlsplit(url)
    timeout = 1800
    conn = (http.client.HTTPSConnection(parts.hostname, parts.port, timeout=timeout)
            if parts.scheme == "https" else
            http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout))
    target = parts.path + (("?" + parts.query) if parts.query else "")
    try:
        conn.putrequest("POST", target)
        conn.putheader("Authorization", "Bearer " + TOKEN)
        conn.putheader("Accept", "application/vnd.github+json")
        conn.putheader("User-Agent", "publish-release")
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(size))
        conn.endheaders()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                conn.send(chunk)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status not in (200, 201):
            raise urllib.error.HTTPError(
                url, resp.status, resp.reason, dict(resp.getheaders()), None)
        return json.loads(body.decode("utf-8")) if body else {}
    finally:
        conn.close()


def upload_asset(release, path, remote_name):
    size = os.path.getsize(path)
    upload_url = release["upload_url"].split("{")[0] + "?name=" + urllib.parse.quote(remote_name)
    last = None
    for attempt in range(4):
        try:
            j = _upload_stream(upload_url, path, size)
            print(f"资产上传完成: {j.get('name')} {j.get('size')} bytes id={j.get('id')}")
            return j
        except Exception as e:
            last = e
            print(f"  上传第 {attempt + 1} 次失败: {str(e)[:200]}")
            time.sleep(min(30, 5 * (2 ** attempt)))
    sys.exit(f"资产上传失败: {remote_name}: {last}")


def resolve_from_config(cfg, key, repo_root):
    """从 config 的 github_release 块解析 repo/tag/asset/notes（去硬编码核心）。"""
    gh = cfg.get(key) or {}
    repo = gh.get("repo") or REPO or detect_repo()
    tag_source = gh.get("tag_source", "auto_git_describe")
    tag_value = gh.get("tag_value") or TAG
    if tag_source == "auto_git_describe":
        tag = tag_value or detect_tag()
    elif tag_source == "package_json":
        pj = os.path.join(repo_root, "package.json")
        tag = tag_value
        if os.path.isfile(pj):
            try:
                tag = json.load(open(pj, encoding="utf-8")).get("version", tag_value)
            except Exception:
                pass
    elif tag_source in ("config_field", "custom"):
        tag = tag_value
    else:
        tag = tag_value or detect_tag()

    # 资产定位：dir + glob，newest-first，排除 exclude_patterns
    asset = None
    adir = gh.get("asset", {})
    if adir.get("dir") and adir.get("glob"):
        full = os.path.join(repo_root, adir["dir"])
        try:
            files = [os.path.join(full, f) for f in os.listdir(full)
                     if os.path.isfile(os.path.join(full, f))]
        except Exception:
            files = []
        matches = []
        import fnmatch
        for fp in files:
            name = os.path.basename(fp)
            if fnmatch.fnmatch(name, adir["glob"]):
                excl = adir.get("exclude_patterns") or []
                if not any(x in name for x in excl):
                    matches.append(fp)
        # M8：先精确命中当前 tag 的主安装包，绝不按 mtime 把 -Lite.exe 或旧版当主资产。
        if tag:
            exact = os.path.join(full, f"XianyuHunter-Setup-v{tag}.exe")
            if os.path.isfile(exact):
                asset = exact
        if not asset:
            # 兜底仍排除 -Lite.exe；主资产只能是 Full。
            matches = [p for p in matches if not os.path.basename(p).lower().endswith("-lite.exe")]
            matches.sort(key=os.path.getmtime, reverse=True)
            if matches:
                asset = matches[0]

    # 说明文件：<VER> 模板替换；缺失且 auto_generate 则 git log 生成
    notes = None
    ncfg = gh.get("notes") or {}
    pt = ncfg.get("path_template")
    if pt and tag:
        notes = os.path.join(repo_root, pt.replace("<VER>", tag))
        if not os.path.isfile(notes) and ncfg.get("auto_generate", True):
            gen = os.path.join(repo_root, pt.replace("<VER>", tag).replace(".md", ".auto.md"))
            log_args = ncfg.get("auto_generate_git_log", "--no-merges --oneline -20")
            try:
                with open(gen, "w", encoding="utf-8") as f:
                    f.write(f"# Release {tag}\n\n")
                    f.write("（自动生成的发布说明，来自 git 提交记录；正式发布前请替换为正式说明。）\n\n")
                    f.write(sh(f'git -C "{repo_root}" log {log_args}'))
                notes = gen
            except Exception:
                pass

    display = os.path.basename(asset) if asset else (gh.get("publish_script") or "asset")
    return repo, tag, asset, notes, display


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asset", nargs="?", help="主资产路径（兼容旧用法）")
    ap.add_argument("notes", nargs="?", help="Release 说明 Markdown 路径")
    ap.add_argument("display_name", nargs="?", help="兼容旧用法的显示名；Release 名请用 --release-name")
    ap.add_argument("repo", nargs="?")
    ap.add_argument("tag", nargs="?")
    ap.add_argument("--asset", dest="extra_assets", action="append", default=[],
                    help="附加资产路径，可重复（如 Lite 安装包）")
    ap.add_argument("--release-name", help="Release 标题（与资产文件名分离）")
    ap.add_argument("--config", help="配置文件路径（JSON）")
    ap.add_argument("--key", default="github_release", help="config 中发布配置所在 key")
    args = ap.parse_args()

    global REPO, TAG, TOKEN, TARGET
    repo_root = os.getcwd()

    # 配置模式：从 config.github_release 解析全部参数（去硬编码）
    if args.config:
        cfg = json.load(open(args.config, encoding="utf-8"))
        repo_root = os.path.dirname(os.path.abspath(args.config)) or repo_root
        r, t, asset, notes, display = resolve_from_config(cfg, args.key, repo_root)
        REPO = r
        TAG = t
        if args.asset:
            asset = os.path.abspath(args.asset)
        if args.notes:
            notes = os.path.abspath(args.notes)
        if args.display_name:
            display = args.display_name
    else:
        if not args.asset or not args.notes:
            sys.exit("用法: python publish-release.py <资产路径> <说明md路径> [显示名] [repo] [tag] "
                     "[--asset 附加资产 ...] [--release-name 标题]  |  或 --config <config.json>")
        asset = os.path.abspath(args.asset)
        notes = os.path.abspath(args.notes)
        display = args.display_name or os.path.basename(asset)

    if not REPO:
        REPO = args.repo or detect_repo()
    if not TAG:
        TAG = args.tag or detect_tag()
    if not TOKEN:
        TOKEN = fetch_token()
    if not TARGET:
        TARGET = detect_branch()

    if not REPO:
        sys.exit("无法确定仓库（GH_REPO 未设且 git remote 无 github.com origin）")
    if not TAG:
        sys.exit("无法确定标签（GH_TAG 未设且无 git tag）")
    if not notes or not os.path.isfile(notes):
        sys.exit(f"说明文件不存在: {notes}")

    # 全部资产一次性规划：一个 Release 会话上传/校验/公开，避免先发 Full 再发 Lite
    # 中途失败留下公开半成品（H4）。
    paths = []
    if asset:
        paths.append(os.path.abspath(asset))
    for p in args.extra_assets:
        paths.append(os.path.abspath(p))
    if not paths:
        sys.exit("没有可发布资产")
    assets = []
    seen_names = set()
    for p in paths:
        if not os.path.isfile(p):
            sys.exit(f"资产不存在: {p}")
        name = os.path.basename(p)
        if name in seen_names:
            sys.exit(f"资产文件名重复，GitHub Release 不允许同名: {name}")
        seen_names.add(name)
        size = os.path.getsize(p)
        sha = local_sha256(p)
        assets.append({"path": p, "name": name, "size": size, "sha256": sha})

    release_name = (args.release_name or os.environ.get("GH_RELEASE_NAME")
                    or args.display_name or TAG)
    print(f"仓库={REPO}  标签={TAG}  目标={TARGET}  draft={DRAFT} prerelease={PRERELEASE}")
    print(f"Release 名称={release_name}")
    for a in assets:
        print(f"资产={a['path']}  {a['size']} bytes  sha256={a['sha256']}")
    print(f"说明={notes}")

    body = _read_text(notes)

    if DRY_RUN:
        print("\n[DRY-RUN] 以下动作【不会】真正执行：")
        print(f"  POST /repos/{REPO}/releases  draft=True（先草稿，校验后再公开）")
        print(f"    tag_name   = {TAG}")
        print(f"    target     = {TARGET}")
        print(f"    name       = {release_name}")
        print(f"    draft      = {DRAFT}  prerelease = {PRERELEASE}")
        print(f"    body 长度  = {len(body)} 字符")
        for a in assets:
            print(f"  UPLOAD 资产  = {a['name']}  ({a['size']} bytes)")
        print("  （设置 GH_DRY_RUN=0 或清除该变量后重跑即可真正发布）")
        return

    if len(TOKEN) < int(os.environ.get("GH_TOKEN_MIN_LEN", "20")):
        sys.exit("GH_TOKEN 缺失（环境变量 / .env / git 凭据库均无 github.com 凭证）")

    # 必须先有远端 tag，否则 draft 的 tag_name 会变成 untagged-<hash>，公开后 URL 不对。
    ensure_tag(TAG, TARGET)

    existing = get_existing_release_by_tag(TAG)
    if existing and not existing.get("draft") and DRAFT:
        sys.exit(f"Release {TAG} 已公开，无法安全地改回草稿。请换一个新 tag，或去掉 GH_DRAFT=1。")

    if existing is None:
        # 首次发布：即使最终要公开，也先以 draft 创建；上传/校验全部通过后再 PATCH 公开。
        st, resp = api(f"/repos/{REPO}/releases", {
            "tag_name": TAG,
            "target_commitish": TARGET,
            "name": release_name,
            "body": body,
            "draft": True,
            "prerelease": False,
        })
        if st not in (200, 201):
            if st in (409, 422):
                existing = get_existing_release_by_tag(TAG)
                if existing:
                    release = existing
                else:
                    print("create release 失败:", st, json.dumps(resp, ensure_ascii=False)[:800])
                    sys.exit(1)
            else:
                print("create release 失败:", st, json.dumps(resp, ensure_ascii=False)[:800])
                sys.exit(1)
        else:
            release = resp
        print("release id:", release.get("id"), "html:", release.get("html_url"), "新建草稿")
    else:
        release = existing
        print("release id:", release.get("id"), "html:", release.get("html_url"),
              "draft=" if release.get("draft") else "published=", bool(release.get("draft")))

    release_id = release.get("id")
    if not release_id:
        sys.exit("Release 缺少 id，无法继续")
    is_draft = bool(release.get("draft"))

    if is_draft:
        # 草稿阶段：同名旧资产可安全删除后重传，不会影响线上。
        release = update_release(release_id, release_name, body, draft=True, prerelease=False, tag_name=TAG) or release
        for a in assets:
            old = next((x for x in (release.get("assets") or []) if x.get("name") == a["name"]), None)
            if old:
                ok, detail = _remote_asset_matches(old, a["size"], a["sha256"])
                if ok:
                    print(f"草稿中同名资产已一致（{a['name']}，{detail}），跳过上传")
                    verify_remote(release_id, a["name"], a["size"], a["sha256"])
                    continue
                print(f"草稿中同名资产不一致（{a['name']}：{detail}），删除后重传")
                delete_asset(old.get("id"))
                release = get_release_by_id(release_id) or release
            upload_asset(release, a["path"], a["name"])
            verify_remote(release_id, a["name"], a["size"], a["sha256"])

        if DRAFT:
            update_release(release_id, release_name, body, draft=True, prerelease=PRERELEASE, tag_name=TAG)
            print(f"[DRAFT] 已保留草稿 Release：{TAG}（未公开）")
        else:
            # 最后一步才公开：任何资产失败都停在上面的草稿状态，线上不会出现半成品。
            update_release(release_id, release_name, body, draft=False, prerelease=PRERELEASE, tag_name=TAG)
            print(f"[PUBLISHED] Release 已公开：{TAG}")
        return

    # 已公开 Release：不能先删旧资产再传新资产（失败会让线上资产消失）。
    # 先把所有变更资产以临时名上传并校验；全部通过后再逐个“旧名→备份、临时名→正式名”，
    # 最后删备份。任一上传/校验失败，线上资产保持原样。
    staged = []
    for a in assets:
        old = next((x for x in (release.get("assets") or []) if x.get("name") == a["name"]), None)
        if old:
            ok, detail = _remote_asset_matches(old, a["size"], a["sha256"])
            if ok:
                print(f"线上同名资产已一致（{a['name']}，{detail}），跳过上传")
                verify_remote(release_id, a["name"], a["size"], a["sha256"])
                continue
            print(f"线上同名资产不一致（{a['name']}：{detail}），将临时名上传后交换")
        temp_name = f".xh-stage-{uuid.uuid4().hex[:8]}-{a['name']}"
        temp = upload_asset(release, a["path"], temp_name)
        verify_remote(release_id, temp_name, a["size"], a["sha256"])
        staged.append({"asset": a, "old": old, "temp": temp})

    # 所有变更资产已安全落在临时名下，开始切换正式名。
    for item in staged:
        a = item["asset"]
        old = item["old"]
        temp = item["temp"]
        backup = None
        final_renamed = False
        try:
            if old:
                backup_name = f".xh-old-{uuid.uuid4().hex[:8]}-{a['name']}"
                backup = rename_asset(old.get("id"), backup_name)
                print(f"旧资产已备份为 {backup_name}（id={backup.get('id')}）")
            rename_asset(temp.get("id"), a["name"])
            final_renamed = True
            verify_remote(release_id, a["name"], a["size"], a["sha256"])
            if backup:
                delete_asset(backup.get("id"), fatal=False)
                backup = None
        except SystemExit:
            # 切换阶段失败：尽量回滚到旧资产，绝不留下“正式名缺失 + 旧包被备份”的状态。
            try:
                if final_renamed:
                    rename_asset(temp.get("id"), f".xh-failed-{uuid.uuid4().hex[:8]}-{a['name']}")
                if backup:
                    rename_asset(backup.get("id"), a["name"])
                    print(f"已回滚旧资产：{a['name']}")
            except SystemExit:
                print(f"[WARN] 回滚失败，请人工检查 Release {release_id} 资产 {a['name']}")
            raise

    # 资产全部切换完成后刷新标题/说明；标题与资产显示名分离（M1）。
    update_release(release_id, release_name, body, draft=False, prerelease=PRERELEASE, tag_name=TAG)
    print(f"[PUBLISHED] Release 已更新：{TAG}")


if __name__ == "__main__":
    main()
