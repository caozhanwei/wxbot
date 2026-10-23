# -*- coding: utf-8 -*-
"""版本检查与一键自更新：查 GitHub Releases 最新版 → 流式下载 MSI → 分离升级脚本
（等主进程退出 → msiexec 静默安装 → 重启）。全程不阻塞 Web 控制台。"""
import hashlib
import os
import re
import subprocess
import threading
import time

import requests

from .version import VERSION

REPO = "caozhanwei/wxbot"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
# 内置国内镜像（GitHub 直连不稳的环境开箱即用）；app.update_mirror 可覆盖，镜像挂了自动回落 GitHub
DEFAULT_MIRROR = "http://118.196.92.227:21120/wxbot-update"
_UA = {"User-Agent": f"wxbot-updater/{VERSION}"}

_STATUS = {"state": "idle", "progress": 0, "total": 0, "msi": "", "latest": "", "error": "", "ts": 0.0}
_DL_THREAD = [None]   # 下载线程引用；"state=downloading 但线程早死了" 靠它识别自愈


def current_version() -> str:
    return VERSION


def parse_version(tag) -> tuple:
    """'v1.4.10' → (1,4,10)；非法返回 ()。"""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", (tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else ()


def is_newer(latest_tag, current_tag=VERSION) -> bool:
    a, b = parse_version(latest_tag), parse_version(current_tag)
    return bool(a and b) and a > b


def _check_mirror(base, timeout) -> dict:
    """国内镜像源（自建 nginx 静态目录）：GET {base}/manifest.json。"""
    try:
        r = requests.get(f"{base}/manifest.json", timeout=timeout, headers=_UA)
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        return {"ok": False, "latest": "", "newer": False, "msi_url": "",
                "notes": "", "message": f"镜像源检查失败：{e}"}
    tag = str(d.get("version") or "")
    msi = str(d.get("msi") or "")
    if not tag or not msi:
        return {"ok": False, "latest": "", "newer": False, "msi_url": "",
                "notes": "", "message": "镜像源 manifest 缺 version/msi"}
    try:
        size = int(d.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return {"ok": True, "latest": tag, "newer": is_newer(tag),
            "msi_url": f"{base}/{msi}", "notes": str(d.get("notes") or "").strip()[:1200],
            "sha256": str(d.get("sha256") or "").strip().lower(), "size": size,
            "message": ""}


def check_latest(timeout=10, mirror="") -> dict:
    """查最新版 → {ok, latest, newer, msi_url, notes, message}。

    镜像源默认内置（DEFAULT_MIRROR，app.update_mirror 可覆盖）；
    镜像失败自动回落 GitHub Releases。
    """
    base = (mirror or "").strip() or DEFAULT_MIRROR
    res = _check_mirror(base.rstrip("/"), timeout)
    if res["ok"]:
        return res
    try:
        r = requests.get(API_LATEST, timeout=timeout, headers=_UA)
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        return {"ok": False, "latest": "", "newer": False, "msi_url": "",
                "notes": "", "message": f"检查更新失败：{e}"}
    tag = str(d.get("tag_name") or "")
    msi_url = ""
    for a in d.get("assets") or []:
        name = str(a.get("name") or "")
        if name.startswith("wxbot-v") and name.endswith(".msi"):
            msi_url = str(a.get("browser_download_url") or "")
            break
    return {"ok": True, "latest": tag, "newer": is_newer(tag), "msi_url": msi_url,
            "notes": str(d.get("body") or "").strip()[:1200],
            "sha256": "", "size": 0, "message": ""}


def status() -> dict:
    return dict(_STATUS)


def download_alive() -> bool:
    """下载线程是否还活着（state=downloading 但线程已死=卡死，可安全重启下载）。"""
    t = _DL_THREAD[0]
    return bool(t and t.is_alive())


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _update_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "wxbot_update")


def download_async(msi_url: str, latest: str, sha256: str = "", size: int = 0) -> bool:
    """后台线程下载 MSI（.part 临时文件，完成后原子改名）。

    镜像 manifest 提供 sha256/size 时下载完自动校验，坏包删除并报错——
    绝不让损坏安装包进入"一键安装"（装坏了 app 起不来只能人工救）。
    state=downloading 但线程已死（历史卡死）时允许重新发起，自愈。
    """
    if _STATUS["state"] == "downloading" and download_alive():
        return False
    _STATUS.update(state="downloading", progress=0, total=0, msi="", latest=latest,
                   error="", ts=time.time())

    def _run():
        dest, replaced = "", False
        try:
            os.makedirs(_update_dir(), exist_ok=True)
            name = os.path.basename((msi_url or "").split("?")[0]) or "wxbot-update.msi"
            dest = os.path.join(_update_dir(), name)
            with requests.get(msi_url, stream=True, timeout=(10, 300), headers=_UA) as r:
                r.raise_for_status()
                _STATUS["total"] = int(r.headers.get("Content-Length") or 0) or int(size or 0)
                done = 0
                with open(dest + ".part", "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        _STATUS["progress"] = done
                        _STATUS["ts"] = time.time()
            os.replace(dest + ".part", dest)
            replaced = True
            if size and done != int(size):
                raise RuntimeError(f"大小不符：应 {size} 实得 {done}")
            if sha256 and _file_sha256(dest) != sha256.lower():
                raise RuntimeError("sha256 校验不符，安装包可能损坏")
            _STATUS.update(state="ready", msi=dest, ts=time.time())
        except Exception as e:
            # 半成品必删；最终文件只在"替换后校验失败"才删（网络失败不误删旧好包）
            for junk in ([dest + ".part"] + ([dest] if replaced else []) if dest else []):
                try:
                    os.remove(junk)
                except OSError:
                    pass
            _STATUS.update(state="error", error=f"{type(e).__name__}: {e}", ts=time.time())

    t = threading.Thread(target=_run, daemon=True)
    _DL_THREAD[0] = t
    t.start()
    return True


_UPDATER_PS = r"""$ErrorActionPreference = 'Continue'
$msi = $args[0]
$exe = $args[1]
Start-Sleep -Seconds 3
Stop-Process -Name wxbot -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$p = Start-Process msiexec.exe -ArgumentList '/i', ('"' + $msi + '"'), '/qn', '/norestart' -Wait -PassThru
if ($p.ExitCode -eq 0) {
  Remove-Item $msi -Force -ErrorAction SilentlyContinue
  Start-Process $exe -WorkingDirectory (Split-Path $exe)
}
"""


def _install_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or ""
    return os.path.join(base, "Programs", "Wxbot") if base else "."


def apply_update(msi_path: str, exe_path=None) -> str:
    """写升级脚本并分离启动：脚本自行等 3 秒→杀 wxbot.exe→静默装→成功后清 MSI 并重启。
    返回脚本路径。调用方随后自行展示告别页即可（进程 3 秒后被脚本终止）。"""
    if not (msi_path and os.path.exists(msi_path)):
        raise FileNotFoundError(f"安装包不存在：{msi_path}")
    exe_path = exe_path or os.path.join(_install_dir(), "wxbot.exe")
    script = os.path.join(os.path.dirname(os.path.abspath(msi_path)), "apply_update.ps1")
    # utf-8-sig：PowerShell 5 靠 BOM 识别 UTF-8，中文路径才不会乱码
    with open(script, "w", encoding="utf-8-sig") as f:
        f.write(_UPDATER_PS)
    # CREATE_NO_WINDOW：DETACHED_PROCESS 会让 powershell 这类控制台程序无控制台启动，
    # 直接静默退出、脚本一行都不执行（实测，别改回 0x8）
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-WindowStyle", "Hidden", "-File", script, msi_path, exe_path],
                     creationflags=0x08000000, close_fds=True)
    return script
