#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""热激活微信 4.x 的 Qt 无障碍 gate：只改运行中进程里的 1 个字节。

微信内置的 Qt 在每次无障碍查询前都看一个 `QAccessible` 的 active byte。冷启动时
它是 0，UIA 那边只能拿到 Qt 外壳（根节点类名 `Qt51514QWindowIcon`）；把它热写成
1，当前进程立刻开始返回 `mmui::` 控件树（根节点变成 `mmui::MainWindow`），
**不用重启微信**，也不注入代码、不设钩子、不挂 DLL。

不依赖 wechatauto：只用标准库，加上校验控件树时的 `pip install uiautomation`
（没装也能写，只是无法证明树真的物化了）。

    python wake_uia.py             # 激活 + 校验
    python wake_uia.py --status    # 只报告，一个字节都不写
    python wake_uia.py --off       # 写回 0（关掉树）
    python wake_uia.py --rva 0x0B135C38   # 跳过扫描，直接用给定地址

退出码：0 = 树已物化；1 = 没成功；2 = 没找到在跑的微信。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import struct
import sys
import time
from ctypes import wintypes

# 与库共用同一份缓存文件，命中就零扫描（Weixin.dll 198MB，扫一次实测 ~8 秒）。
GATE_CACHE_FILE = os.path.join(os.path.expanduser("~"), ".wechatauto",
                               "gate_cache.json")

# 已知的 active byte 位置：只作扫描失败时的兜底，扫描结果优先。
QACCESSIBLE_ACTIVE_RVA_BY_VERSION = {
    "4.1.11.22": 0x0A1E7DB8,
    "4.1.13.65": 0x0AE2B0C8,
    "4.1.15.13": 0x0B135C38,
    "4.1.15.53": 0x0B3959D8,
}
QACCESSIBLE_CORE_STRING = b"qt.accessibility.core"
# test rcx,rcx / jz / cmp byte ptr [rip+disp32],0 / jz —— gate 检查的标准形状
QACCESSIBLE_GATE_PATTERN = re.compile(
    rb"\x48\x85\xc9\x0f\x84....\x80\x3d(?P<disp>.{4})"
    rb"\x00\x0f\x84",
    re.DOTALL,
)

IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_WRITE = 0x80000000
TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32 = 0x00000008, 0x00000010
PROCESS_ACCESS = 0x0400 | 0x0010 | 0x0020 | 0x0008   # QUERY|VM_READ|VM_WRITE|VM_OP
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_MODULE_NAME32, MAX_PATH = 255, 260


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("th32ModuleID", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("GlblcntUsage", ctypes.c_ulong),
        ("ProccntUsage", ctypes.c_ulong),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_ubyte)),
        ("modBaseSize", ctypes.c_ulong),
        ("hModule", ctypes.c_void_p),
        ("szModule", ctypes.c_wchar * (MAX_MODULE_NAME32 + 1)),
        ("szExePath", ctypes.c_wchar * MAX_PATH),
    ]


def log(msg: str) -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------------ 缓存
def dll_identity(dll_path: str) -> str:
    """Weixin.dll 身份 = 版本目录 + 大小 + mtime；换版本自动失效。"""
    try:
        st = os.stat(dll_path)
        return "%s|%d|%d" % (os.path.basename(os.path.dirname(dll_path)),
                             st.st_size, int(st.st_mtime))
    except OSError:
        return dll_path


def load_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(path: str, data: dict) -> None:
    try:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        log("  缓存写盘失败（不影响功能，下次重扫）：%s" % e)


# -------------------------------------------------------------------------- PE
def pe_sections(data: bytes):
    try:
        pe_off = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_off:pe_off + 4] != b"PE\0\0":
            return []
        coff = pe_off + 4
        count = struct.unpack_from("<H", data, coff + 2)[0]
        opt_size = struct.unpack_from("<H", data, coff + 16)[0]
        sec_off = coff + 20 + opt_size
        out = []
        for i in range(count):
            off = sec_off + i * 40
            name = data[off:off + 8].split(b"\0", 1)[0].decode("ascii", "ignore")
            vsize, rva, raw_size, raw_ptr = struct.unpack_from("<IIII", data, off + 8)
            out.append({"name": name, "rva": rva, "vsize": vsize,
                        "raw_size": raw_size, "raw_ptr": raw_ptr,
                        "chars": struct.unpack_from("<I", data, off + 36)[0]})
        return out
    except Exception:
        return []


def section_for_rva(sections, rva):
    for s in sections:
        if s["rva"] <= rva < s["rva"] + max(s["vsize"], s["raw_size"]):
            return s
    return None


def offset_to_rva(sections, offset):
    for s in sections:
        if s["raw_ptr"] <= offset < s["raw_ptr"] + s["raw_size"]:
            return s["rva"] + offset - s["raw_ptr"]
    return None


def rip_xrefs(data, sections, target_rva):
    """可执行段里用 RIP 相对寻址引用 target_rva 的 LEA 指令 RVA。

    numpy 在 198MB 上向量化；缺 numpy 就退回逐字节，慢二十多倍但结果一致。
    """
    try:
        import numpy as np
    except ImportError:
        return rip_xrefs_slow(data, sections, target_rva)

    a = np.frombuffer(data, dtype=np.uint8)
    n = a.size
    if n < 16:
        return []
    p8d = np.flatnonzero(a == 0x8D)
    p8d = p8d[(p8d >= 1) & (p8d <= n - 7)]
    if p8d.size == 0:
        return []
    modrm = np.zeros(256, dtype=bool)
    modrm[[0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x35, 0x3D]] = True
    p8d = p8d[modrm[a[p8d + 1]]]
    if p8d.size == 0:
        return []
    rex = (a[p8d - 1] >= 0x40) & (a[p8d - 1] <= 0x4F)
    b = a[p8d[:, None] + np.arange(2, 6)].astype(np.int64)
    disp = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16) | (b[:, 3] << 24)
    disp -= (disp >= 0x80000000) << 32
    total = p8d + disp

    hits = []
    for s in sections:
        if not (s["chars"] & IMAGE_SCN_MEM_EXECUTE):
            continue
        start = s["raw_ptr"]
        end = min(n, start + s["raw_size"])
        if end - start < 8:
            continue
        k = target_rva - s["rva"] + start - 6
        hit = total == k
        plain = hit & (p8d >= start) & (p8d <= end - 8)
        hits += (s["rva"] + p8d[plain] - start).tolist()
        with_rex = hit & rex & (p8d >= start + 1) & (p8d <= end - 7)
        hits += (s["rva"] + p8d[with_rex] - start - 1).tolist()
    return hits


def rip_xrefs_slow(data, sections, target_rva):
    hits = []
    for s in sections:
        if not (s["chars"] & IMAGE_SCN_MEM_EXECUTE):
            continue
        start = s["raw_ptr"]
        raw = data[start:min(len(data), start + s["raw_size"])]
        for i in range(0, max(0, len(raw) - 7)):
            if 0x40 <= raw[i] <= 0x4F and raw[i + 1] == 0x8D and (raw[i + 2] & 0xC7) == 0x05:
                disp = struct.unpack_from("<i", raw, i + 3)[0]
                if s["rva"] + i + 7 + disp == target_rva:
                    hits.append(s["rva"] + i)
            if raw[i] == 0x8D and (raw[i + 1] & 0xC7) == 0x05:
                disp = struct.unpack_from("<i", raw, i + 2)[0]
                if s["rva"] + i + 6 + disp == target_rva:
                    hits.append(s["rva"] + i)
    return hits


def scan_candidates(dll_path: str, cache_path: str):
    """按可信度排好的 gate RVA 候选；「扫过了但没有」这个负结果也落盘。"""
    identity = dll_identity(dll_path)
    entry = load_cache(cache_path).get(identity) or {}
    if isinstance(entry.get("candidates"), list):
        return [int(x) for x in entry["candidates"]]

    try:
        with open(dll_path, "rb") as f:
            data = f.read()
    except OSError as e:
        log("  读不到 %s：%s" % (dll_path, e))
        return []
    sections = pe_sections(data)
    if not sections:
        log("  %s 不是能认的 PE" % dll_path)
        return []

    core_off = data.find(QACCESSIBLE_CORE_STRING)
    core_rva = offset_to_rva(sections, core_off) if core_off >= 0 else None
    core_xrefs = rip_xrefs(data, sections, core_rva) if core_rva is not None else []

    found = []
    for m in QACCESSIBLE_GATE_PATTERN.finditer(data):
        match_rva = offset_to_rva(sections, m.start())
        disp_rva = offset_to_rva(sections, m.start("disp"))
        if match_rva is None or disp_rva is None:
            continue
        sec = section_for_rva(sections, match_rva)
        if not sec or not (sec["chars"] & IMAGE_SCN_MEM_EXECUTE):
            continue
        cmp_rva = disp_rva - 2
        target_rva = cmp_rva + 7 + struct.unpack("<i", m.group("disp"))[0]
        tgt = section_for_rva(sections, target_rva)
        if not tgt or not (tgt["chars"] & IMAGE_SCN_MEM_WRITE):
            continue
        dist = min(abs(match_rva - x) for x in core_xrefs) if core_xrefs else 0x7FFFFFFF
        found.append((dist, target_rva))

    if not found:
        cache = load_cache(cache_path)
        cache.setdefault(identity, {})["candidates"] = []
        save_cache(cache_path, cache)
        return []
    found.sort(key=lambda x: x[0])
    near = [rva for dist, rva in found if dist <= 0x20000]
    ordered = list(dict.fromkeys(near or [rva for _d, rva in found]))
    cache = load_cache(cache_path)
    cache.setdefault(identity, {})["candidates"] = ordered
    save_cache(cache_path, cache)
    return ordered


def candidates_for(dll_path: str, cache_path: str):
    """顺序：上次真正生效过的地址 > 特征扫描 > 版本兜底表。"""
    out = []
    cache = load_cache(cache_path)
    verified = (cache.get(dll_identity(dll_path)) or {}).get("verified")
    if isinstance(verified, int):
        out.append(verified)
    for rva in scan_candidates(dll_path, cache_path):
        if rva not in out:
            out.append(rva)
    fallback = QACCESSIBLE_ACTIVE_RVA_BY_VERSION.get(
        os.path.basename(os.path.dirname(dll_path)))
    if fallback and fallback not in out:
        out.append(fallback)
    return out


# ---------------------------------------------------------------------- 进程侧
def pid_of_window(hwnd):
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value) or None


def iter_modules(pid):
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for first in (k32.Module32FirstW, k32.Module32NextW):
        first.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        first.restype = wintypes.BOOL
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if ctypes.cast(snap, ctypes.c_void_p).value == INVALID_HANDLE_VALUE:
        return
    try:
        e = MODULEENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        nxt = k32.Module32FirstW(snap, ctypes.byref(e))
        while nxt:
            base = ctypes.cast(e.modBaseAddr, ctypes.c_void_p).value or 0
            yield base, int(e.modBaseSize), e.szModule, e.szExePath
            nxt = k32.Module32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)


def weixin_dll(pid):
    for base, _size, name, path in iter_modules(pid):
        if name.lower() == "weixin.dll":
            return base, path
    return None


def wechat_windows():
    """可见、标题像主窗、且进程加载了 Weixin.dll 的窗口，按面积大到小。"""
    user32 = ctypes.windll.user32
    user32.IsWindowVisible.restype = wintypes.BOOL
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if not buf.value or not any(k in buf.value for k in ("微信", "Weixin", "WeChat")):
            return True
        pid = pid_of_window(hwnd)
        if not pid:
            return True
        mod = weixin_dll(pid)
        if not mod:
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append((max(0, r.right - r.left) * max(0, r.bottom - r.top), hwnd, pid,
                    mod[0], mod[1], buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return sorted(out, key=lambda x: -x[0])


def read_byte(handle, address):
    k32 = ctypes.windll.kernel32
    k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    k32.ReadProcessMemory.restype = wintypes.BOOL
    buf = (ctypes.c_ubyte * 1)()
    got = ctypes.c_size_t(0)
    ok = k32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, 1, ctypes.byref(got))
    return int(buf[0]) if ok and got.value == 1 else None


def write_byte(handle, address, value):
    k32 = ctypes.windll.kernel32
    k32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    k32.WriteProcessMemory.restype = wintypes.BOOL
    buf = (ctypes.c_ubyte * 1)(value & 0xFF)
    done = ctypes.c_size_t(0)
    ok = k32.WriteProcessMemory(handle, ctypes.c_void_p(address), buf, 1, ctypes.byref(done))
    return bool(ok and done.value == 1)


# ------------------------------------------------------------------------ 验树
def tree_state(hwnd, timeout=3.0):
    """返回 (根节点类名, 出现 mmui:: 节点没有)。没装 uiautomation 时返回 (None, None)。"""
    try:
        import uiautomation as auto
    except ImportError:
        return None, None
    try:
        auto.InitializeUIAutomationInCurrentThread()
    except Exception:
        pass
    deadline = time.time() + max(0.1, timeout)
    while True:
        try:
            c = auto.ControlFromHandle(hwnd)
            cn = (c.ClassName or "") if c else ""
            kids = c.GetChildren()[:12] if c else []
        except Exception:
            cn, kids = "", []
        if cn.startswith("mmui::") or any((k.ClassName or "").startswith("mmui::")
                                          for k in kids):
            return cn or "?", True
        if time.time() >= deadline:
            return cn or "?", False
        time.sleep(0.2)


def main() -> int:
    ap = argparse.ArgumentParser(description="热激活微信的 Qt 无障碍 gate（不依赖 wechatauto）")
    ap.add_argument("--status", action="store_true", help="只报告，不写字节")
    ap.add_argument("--off", action="store_true", help="把字节写回 0（收起控件树）")
    ap.add_argument("--rva", type=lambda x: int(x, 16) if x.lower().startswith("0x") else int(x),
                    help="跳过扫描，直接用这个 RVA（十六进制或十进制）")
    ap.add_argument("--cache", default=GATE_CACHE_FILE, help="RVA 缓存文件路径")
    ap.add_argument("--timeout", type=float, default=3.0, help="校验控件树的等待上限（秒）")
    args = ap.parse_args()
    if args.rva is not None and not 0 < args.rva <= 0xFFFFFFFF:
        ap.error("--rva 超出模块范围")

    wins = wechat_windows()
    if not wins:
        log("没找到在跑的微信主窗（未启动 / 未登录 / 最小化到托盘 / 解释器位数不对都算）")
        return 2
    _area, hwnd, pid, base, dll, title = wins[0]
    log("微信窗口 hwnd=%d 标题=%r pid=%d" % (hwnd, title, pid))
    log("Weixin.dll 基址=0x%x  %s" % (base, dll))
    cn, ready = tree_state(hwnd, timeout=0.3)
    if cn is None:
        log("没装 uiautomation，校验不了控件树（pip install uiautomation）；下面只看字节")
    log("当前状态：根节点=%s mmui物化=%s" % (cn, ready))
    if args.status:
        return 0 if ready else 1

    rvas = [args.rva] if args.rva else candidates_for(dll, args.cache)
    if not rvas:
        log("没找到 gate 字节：这个版本的 Weixin.dll 不在特征里，也不在版本表里。"
            "开系统读屏标志（设置 → 辅助功能 → 屏幕阅读器）后重启微信也能拿到控件树。")
        return 1
    log("候选 RVA：%s" % ", ".join("0x%X" % r for r in rvas))

    k32 = ctypes.windll.kernel32
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.OpenProcess(PROCESS_ACCESS, False, pid)
    if not handle:
        log("OpenProcess 失败（err=%d）：通常是权限/完整性级别不一致——"
            "别「以管理员身份运行」，让脚本和微信同级" % ctypes.windll.kernel32.GetLastError())
        return 1

    try:
        if args.off:
            off = 0
            for rva in rvas:
                cur = read_byte(handle, base + rva)
                if cur == 1:
                    off += bool(write_byte(handle, base + rva, 0))
                    log("  Weixin.dll+0x%X：1 -> 0" % rva)
            if not off:
                log("没有哪个候选是 1，本来就没激活")
            _cn, now = tree_state(hwnd, timeout=args.timeout)
            log("控件树：mmui物化=%s（%s）" % (now, _cn))
            return 0 if not now else 1

        for rva in rvas:
            address = base + rva
            original = read_byte(handle, address)
            if original is None:
                log("  Weixin.dll+0x%X 读不到，跳过" % rva)
                continue
            if original != 1:
                if not write_byte(handle, address, 1):
                    log("  Weixin.dll+0x%X 写失败（内存保护？），跳过" % rva)
                    continue
                log("  Weixin.dll+0x%X：%s -> 1" % (rva, original))
            _cn, now = tree_state(hwnd, timeout=args.timeout)
            if now:
                cache = load_cache(args.cache)
                cache.setdefault(dll_identity(dll), {})["verified"] = int(rva)
                save_cache(args.cache, cache)
                log("已物化：根节点=%s（RVA 0x%X 已记入 %s）" % (_cn, rva, args.cache))
                return 0
            if original != 1:
                write_byte(handle, address, original)
            log("  写完树没动（%s），还原后试下一个候选" % _cn)
        log("所有候选都没让 mmui 树物化：这个版本的 gate 字节不在特征/版本表里，"
            "或微信被安全软件护住了。用 --rva 手工指定，或开读屏标志后重启微信。")
        return 1
    finally:
        k32.CloseHandle(handle)


if __name__ == "__main__":
    sys.exit(main())
