"""Notifier interface + factory. Channel-agnostic (Q12): pick by which app you check."""
from __future__ import annotations

import logging
from typing import Optional

import requests

from ..config import get_config

log = logging.getLogger(__name__)


class Notifier:
    name = "base"

    def __init__(self, **kwargs):
        self.cfg = {k: v for k, v in kwargs.items() if v}

    @property
    def configured(self) -> bool:
        return bool(self.cfg)

    def send(self, text: str, title: str = "stock-agent") -> bool:
        raise NotImplementedError


def _post_json(url: str, body: dict, timeout: int = 15) -> dict:
    r = requests.post(url, json=body, timeout=timeout)
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}


class WeComBot(Notifier):
    """企业微信群机器人 webhook."""
    name = "wecom"

    def __init__(self, key: Optional[str] = None, **kw):
        super().__init__(key=key, **kw)
        self.key = key

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def send(self, text: str, title: str = "stock-agent") -> bool:
        if not self.configured:
            return False
        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={self.key}"
        try:
            res = _post_json(url, {"msgtype": "markdown", "markdown": {"content": text}})
            ok = res.get("errcode") == 0
            if not ok:
                log.error("wecom send failed: %s", res)
            return ok
        except Exception as e:  # noqa: BLE001
            log.error("wecom send error: %s", e)
            return False


class FeishuBot(Notifier):
    """飞书自定义机器人 webhook (text)."""
    name = "feishu"

    def __init__(self, url: Optional[str] = None, **kw):
        super().__init__(url=url, **kw)
        self.url = url

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def send(self, text: str, title: str = "stock-agent") -> bool:
        if not self.configured:
            return False
        try:
            res = _post_json(self.url, {"msg_type": "text", "content": {"text": f"{title}\n{text}"}})
            ok = (res.get("StatusCode") == 0) or (res.get("code") == 0) or (res.get("msg") == "success")
            if not ok:
                log.error("feishu send failed: %s", res)
            return ok
        except Exception as e:  # noqa: BLE001
            log.error("feishu send error: %s", e)
            return False


class PushPlus(Notifier):
    """PushPlus 推送."""
    name = "pushplus"

    def __init__(self, token: Optional[str] = None, **kw):
        super().__init__(token=token, **kw)
        self.token = token

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def send(self, text: str, title: str = "stock-agent") -> bool:
        if not self.configured:
            return False
        try:
            r = requests.post(
                "https://www.pushplus.plus/send",
                json={"token": self.token, "title": title, "content": text, "template": "txt"},
                timeout=15,
            )
            res = r.json()
            ok = res.get("code") == 200
            if not ok:
                log.error("pushplus send failed: %s", res)
            return ok
        except Exception as e:  # noqa: BLE001
            log.error("pushplus send error: %s", e)
            return False


def get_notifiers(env: dict | None = None) -> list[Notifier]:
    """Return all configured notifiers (those with credentials in env)."""
    env = env or get_config().env
    ns: list[Notifier] = [
        WeComBot(key=env.get("WECOM_BOT_KEY")),
        FeishuBot(url=env.get("FEISHU_BOT_URL")),
        PushPlus(token=env.get("PUSHPLUS_TOKEN")),
    ]
    return [n for n in ns if n.configured]


def broadcast(text: str, title: str = "stock-agent") -> dict:
    """Send to all configured channels. Returns {name: ok}."""
    results = {}
    for n in get_notifiers():
        results[n.name] = n.send(text, title=title)
    if not results:
        log.warning("no notifier configured; report not sent. Print instead:\n%s", text[:400])
    return results
