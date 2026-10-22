# -*- coding: utf-8 -*-
"""语音识别客户端（ASR）：支持 OpenAI 兼容音频转录接口或轻量 HTTP ASR 服务。

支持将本地解密出的语音文件（SILK / WAV / MP3 等）送往 ASR 服务转换为文本；
未配置或调用失败静默降级为 None，由驱动层兜底保持 [语音] 占位。
"""
import logging
import requests

log = logging.getLogger("wxbot.asr")


class AsrClient:
    def __init__(self, config=None):
        cfg = config or {}
        if isinstance(cfg, dict) and "asr" in cfg:
            cfg = cfg.get("asr") or {}
        self.url = str(cfg.get("url") or "").strip()
        self.key = str(cfg.get("api_key") or "").strip()
        self.model = str(cfg.get("model") or "whisper-1").strip()
        self.timeout = int(cfg.get("timeout") or 30)

    @property
    def ready(self) -> bool:
        return bool(self.url)

    def transcribe(self, audio_data: bytes, filename: str = "voice.silk") -> str | None:
        """发送音频二进制至 ASR 服务转录为文字，失败返回 None。"""
        if not self.ready or not audio_data:
            return None
        headers = {}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        files = {
            "file": (filename, audio_data, "application/octet-stream")
        }
        data = {"model": self.model}
        try:
            r = requests.post(self.url, headers=headers, files=files, data=data, timeout=self.timeout)
            if r.status_code == 200:
                d = r.json()
                text = d.get("text") or ""
                return str(text).strip()
            log.warning("ASR 服务响应码异常: %d %s", r.status_code, r.text[:200])
        except Exception as e:
            log.warning("ASR 转写调用失败: %s", e)
        return None
