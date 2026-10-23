# -*- coding: utf-8 -*-
"""updater：版本比较 / GitHub 最新版检查 / MSI 流式下载 / 一键升级脚本。"""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import updater
from wxbot.version import VERSION


# ---------- 版本比较 ----------
def test_parse_version():
    assert updater.parse_version("v1.4.10") == (1, 4, 10)
    assert updater.parse_version("1.4.7") == (1, 4, 7)
    assert updater.parse_version("junk") == ()
    assert updater.parse_version("") == ()
    assert updater.parse_version("v1.4") == ()


def test_is_newer():
    assert updater.is_newer("v1.4.8", "v1.4.7")
    assert updater.is_newer("v1.10.0", "v1.9.9")     # 数值比较不是字符串
    assert not updater.is_newer("v1.4.7", "v1.4.7")
    assert not updater.is_newer("v1.4.6", "v1.4.7")
    assert not updater.is_newer("latest", "v1.4.7")  # 非法 tag 不误判


def test_current_version_is_semver():
    assert updater.parse_version(VERSION) == tuple(int(x) for x in VERSION.lstrip("v").split("."))


# ---------- GitHub 最新版检查 ----------
class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_check_latest_parses_release(monkeypatch):
    called = {}

    def fake_get(url, timeout=10, headers=None):
        called["url"] = url
        return _FakeResp({
            "tag_name": "v9.9.9",
            "body": "# 更新内容\n- 修了一些问题",
            "assets": [
                {"name": "wxbot-v9.9.9.msi", "browser_download_url": "https://x/wxbot-v9.9.9.msi"},
                {"name": "checksum.txt", "browser_download_url": "https://x/c.txt"},
            ],
        })

    monkeypatch.setattr(updater.requests, "get", fake_get)
    res = updater.check_latest()
    assert res["ok"] and res["latest"] == "v9.9.9" and res["newer"] is True
    assert res["msi_url"] == "https://x/wxbot-v9.9.9.msi"
    assert "api.github.com" in called["url"]


def test_check_latest_handles_network_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(updater.requests, "get", boom)
    res = updater.check_latest()
    assert res["ok"] is False and res["newer"] is False and "no network" in res["message"]


def test_check_latest_no_msi_asset(monkeypatch):
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResp({"tag_name": "v9.9.9", "assets": []}))
    res = updater.check_latest()
    assert res["ok"] and res["newer"] and res["msi_url"] == ""


# ---------- 流式下载 ----------
class _FakeStreamResp:
    headers = {"Content-Length": "5"}
    status_code = 200

    def __init__(self, chunks):
        self._chunks = chunks

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=0):
        yield from self._chunks


def test_download_async_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_update_dir", lambda: str(tmp_path))
    monkeypatch.setattr(updater.requests, "get",
                        lambda url, stream=True, timeout=None, headers=None:
                        _FakeStreamResp([b"hello", b"world"][:2]))
    # Content-Length 5 但内容 10 字节，以实际收到的为准
    assert updater.download_async("https://x/wxbot-v9.9.9.msi", "v9.9.9")
    for _ in range(100):
        if updater.status()["state"] in ("ready", "error"):
            break
        threading.Event().wait(0.05)
    st = updater.status()
    assert st["state"] == "ready", st
    assert st["latest"] == "v9.9.9"
    assert os.path.basename(st["msi"]) == "wxbot-v9.9.9.msi"
    assert os.path.exists(st["msi"])
    assert not os.path.exists(st["msi"] + ".part")
    assert st["progress"] == 10


def test_download_async_error_state(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_update_dir", lambda: str(tmp_path))

    def boom(*a, **k):
        raise RuntimeError("404 gone")

    monkeypatch.setattr(updater.requests, "get", boom)
    updater.download_async("https://x/wxbot-v9.9.9.msi", "v9.9.9")
    for _ in range(100):
        if updater.status()["state"] in ("ready", "error"):
            break
        threading.Event().wait(0.05)
    st = updater.status()
    assert st["state"] == "error" and "404" in st["error"]


# ---------- 一键升级脚本 ----------
def test_apply_update_writes_script_and_launches_detached(tmp_path, monkeypatch):
    msi = tmp_path / "wxbot-v1.4.8.msi"
    msi.write_bytes(b"PK")
    launched = {}

    def fake_popen(args, creationflags=0, close_fds=True):
        launched["args"] = args
        launched["flags"] = creationflags

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    exe = os.path.join(str(tmp_path), "wxbot.exe")
    script = updater.apply_update(str(msi), exe_path=exe)
    assert os.path.exists(script)
    body = open(script, encoding="utf-8-sig").read()
    assert "msiexec" in body and "Stop-Process" in body and "wxbot" in body
    args = launched["args"]
    assert args[0].lower().startswith("powershell") and script in args
    assert str(msi) in args and exe in args
    # CREATE_NO_WINDOW(0x08000000)：DETACHED_PROCESS(0x8) 会让 powershell 静默退出不执行脚本
    assert launched["flags"] == 0x08000000


def test_apply_update_default_install_dir(tmp_path, monkeypatch, ):
    msi = tmp_path / "up.msi"
    msi.write_bytes(b"PK")
    seen = {}
    monkeypatch.setattr(updater.subprocess, "Popen",
                        lambda args, creationflags=0, close_fds=True: seen.update(args=args))
    updater.apply_update(str(msi))
    assert "wxbot.exe" in seen["args"][-1]


# ---------- 镜像源（app.update_mirror 优先，失败回落 GitHub） ----------
class _ManifestResp(_FakeResp):
    def __init__(self, payload):
        super().__init__(payload)


def test_check_latest_uses_mirror_manifest(monkeypatch):
    def fake_get(url, timeout=10, headers=None, stream=False):
        assert url == "http://m.example/wxbot-update/manifest.json"
        return _ManifestResp({"version": "v9.9.9", "msi": "wxbot-v9.9.9.msi",
                              "notes": "镜像版说明"})

    monkeypatch.setattr(updater.requests, "get", fake_get)
    res = updater.check_latest(mirror="http://m.example/wxbot-update/")
    assert res["ok"] and res["latest"] == "v9.9.9" and res["newer"] is True
    assert res["msi_url"] == "http://m.example/wxbot-update/wxbot-v9.9.9.msi"
    assert res["notes"] == "镜像版说明"


def test_check_latest_mirror_same_version_not_newer(monkeypatch):
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _ManifestResp({"version": "v" + VERSION,
                                                       "msi": "x.msi"}))
    res = updater.check_latest(mirror="http://m")
    assert res["ok"] and res["newer"] is False


def test_check_latest_mirror_error_falls_back_to_github(monkeypatch):
    def fake_get(url, timeout=10, headers=None, stream=False):
        if "manifest.json" in url:
            raise RuntimeError("mirror down")
        return _FakeResp({"tag_name": "v9.9.9", "assets": [
            {"name": "wxbot-v9.9.9.msi", "browser_download_url": "https://gh/x.msi"}]})

    monkeypatch.setattr(updater.requests, "get", fake_get)
    res = updater.check_latest(mirror="http://m")
    assert res["ok"] and res["latest"] == "v9.9.9"
    assert res["msi_url"] == "https://gh/x.msi"


def test_check_latest_mirror_bad_manifest_falls_back(monkeypatch):
    def fake_get(url, timeout=10, headers=None, stream=False):
        if "manifest.json" in url:
            return _ManifestResp({"foo": 1})          # 缺 version/msi
        return _FakeResp({"tag_name": "v9.9.9", "assets": []})

    monkeypatch.setattr(updater.requests, "get", fake_get)
    res = updater.check_latest(mirror="http://m")
    assert res["ok"] and res["latest"] == "v9.9.9"


def test_check_latest_defaults_to_builtin_mirror(monkeypatch):
    seen = {}

    def fake_get(url, timeout=10, headers=None, stream=False):
        seen["url"] = url
        return _ManifestResp({"version": "v9.9.9", "msi": "wxbot-v9.9.9.msi"})

    monkeypatch.setattr(updater.requests, "get", fake_get)
    res = updater.check_latest()          # 不传 mirror：走内置默认镜像
    assert seen["url"].startswith(updater.DEFAULT_MIRROR)
    assert seen["url"].endswith("/manifest.json")
    assert res["ok"] and res["latest"] == "v9.9.9"


# ---------- 下载健壮化：校验 / 卡死自愈 ----------

def _wait_terminal():
    for _ in range(200):
        if updater.status()["state"] in ("ready", "error"):
            return updater.status()
        threading.Event().wait(0.05)
    return updater.status()


def test_check_latest_mirror_carries_sha256_size(monkeypatch):
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _ManifestResp(
                            {"version": "v9.9.9", "msi": "wxbot-v9.9.9.msi",
                             "sha256": "ABCD1234", "size": 10}))
    res = updater.check_latest(mirror="http://m")
    assert res["sha256"] == "abcd1234" and res["size"] == 10


def test_download_async_verifies_sha256_ok(tmp_path, monkeypatch):
    import hashlib
    body = b"hello wxbot"
    monkeypatch.setattr(updater, "_update_dir", lambda: str(tmp_path))
    monkeypatch.setattr(updater.requests, "get",
                        lambda url, stream=True, timeout=None, headers=None:
                        _FakeStreamResp([body]))
    good = hashlib.sha256(body).hexdigest()
    updater.download_async("https://x/wxbot-v9.9.9.msi", "v9.9.9", sha256=good, size=len(body))
    st = _wait_terminal()
    assert st["state"] == "ready", st
    assert os.path.exists(st["msi"])


def test_download_async_bad_sha256_rejected_and_cleaned(tmp_path, monkeypatch):
    body = b"hello wxbot"
    monkeypatch.setattr(updater, "_update_dir", lambda: str(tmp_path))
    monkeypatch.setattr(updater.requests, "get",
                        lambda url, stream=True, timeout=None, headers=None:
                        _FakeStreamResp([body]))
    updater.download_async("https://x/wxbot-v9.9.9.msi", "v9.9.9",
                           sha256="dead" * 16, size=len(body))
    st = _wait_terminal()
    assert st["state"] == "error" and "sha256" in st["error"]
    leftovers = [p for p in os.listdir(str(tmp_path)) if p.endswith((".msi", ".part"))]
    assert not leftovers          # 坏包删干净，绝不留给"一键安装"


def test_download_async_size_mismatch_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_update_dir", lambda: str(tmp_path))
    monkeypatch.setattr(updater.requests, "get",
                        lambda url, stream=True, timeout=None, headers=None:
                        _FakeStreamResp([b"12345"]))       # 实得 5 字节
    updater.download_async("https://x/wxbot-v9.9.9.msi", "v9.9.9", size=99)
    st = _wait_terminal()
    assert st["state"] == "error" and "大小不符" in st["error"]


def test_download_async_dead_thread_allows_restart(tmp_path, monkeypatch):
    """state 卡在 downloading 但线程已死：再调 download_async 必须重新发起。"""
    monkeypatch.setattr(updater, "_update_dir", lambda: str(tmp_path))
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    updater._STATUS.update(state="downloading", progress=1, total=0, msi="",
                           latest="v9.9.9", error="")
    updater._DL_THREAD[0] = dead
    monkeypatch.setattr(updater.requests, "get",
                        lambda url, stream=True, timeout=None, headers=None:
                        _FakeStreamResp([b"abc"]))
    assert updater.download_async("https://x/wxbot-v9.9.9.msi", "v9.9.9") is True
    st = _wait_terminal()
    assert st["state"] == "ready"


def test_download_async_live_thread_blocked(tmp_path, monkeypatch):
    class _Alive:
        def is_alive(self):
            return True

    updater._STATUS.update(state="downloading", progress=1, total=0, msi="",
                           latest="v9.9.9", error="")
    updater._DL_THREAD[0] = _Alive()
    try:
        assert updater.download_async("https://x/wxbot-v9.9.9.msi", "v9.9.9") is False
    finally:
        updater._DL_THREAD[0] = None
        updater._STATUS.update(state="idle", progress=0, total=0, msi="",
                               latest="", error="", ts=0.0)
