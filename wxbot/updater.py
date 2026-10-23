# -*- coding: utf-8 -*-
"""版本检查与一键自更新：查 GitHub Releases 最新版 → 流式下载 MSI → 分离升级脚本
（等主进程退出 → msiexec 静默安装 → 重启）。全程不阻塞 Web 控制台。"""
import os
import re
import subprocess
import threading

import requests

from .version import VERSION

REPO = "caozhanwei/wxbot"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
# 内置国内镜像（GitHub 直连不稳的环境开箱即用）；app.update_mirror 可覆盖，镜像挂了自动回落 GitHub
DEFAULT_MIRROR = "http://118.196.92.227:21120/wxbot-update"
_UA = {"User-Agent": f"wxbot-updater/{VERSION}"}

_STATUS = {"state": "idle", "progress": 0, "total": 0, "msi": "", "latest": "", "error": ""}


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
    return {"ok": True, "latest": tag, "newer": is_newer(tag),
            "msi_url": f"{base}/{msi}", "notes": str(d.get("notes") or "").strip()[:1200],
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
            "notes": str(d.get("body") or "").strip()[:1200], "message": ""}


def status() -> dict:
    return dict(_STATUS)


def _update_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "wxbot_update")


def download_async(msi_url: str, latest: str) -> bool:
    """后台线程下载 MSI（.part 临时文件，完成后原子改名）。进行中重复调用返回 False。"""
    if _STATUS["state"] == "downloading":
        return False
    _STATUS.update(state="downloading", progress=0, total=0, msi="", latest=latest, error="")

    def _run():
        try:
            os.makedirs(_update_dir(), exist_ok=True)
            name = os.path.basename((msi_url or "").split("?")[0]) or "wxbot-update.msi"
            dest = os.path.join(_update_dir(), name)
            with requests.get(msi_url, stream=True, timeout=(10, 300), headers=_UA) as r:
                r.raise_for_status()
                _STATUS["total"] = int(r.headers.get("Content-Length") or 0)
                done = 0
                with open(dest + ".part", "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        _STATUS["progress"] = done
            os.replace(dest + ".part", dest)
            _STATUS.update(state="ready", msi=dest)
        except Exception as e:
            _STATUS.update(state="error", error=f"{type(e).__name__}: {e}")

    threading.Thread(target=_run, daemon=True).start()
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
