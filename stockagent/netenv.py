"""Network environment: bypass local VPN/proxy for domestic Chinese hosts.

Every host this app talks to is domestic (AkShare→eastmoney/sina, notifier→
企业微信/飞书/PushPlus, LLM→智谱GLM). A user's local proxy (e.g. Clash on
127.0.0.1:7890, auto-detected from the Windows registry) is meant for foreign
sites and breaks domestic ones with ProxyError. We add those hosts to NO_PROXY
so requests goes direct. Foreign hosts (if ever added) still use the proxy.

Imported once at package import (see stockagent/__init__.py).
"""
import os

_DOMESTIC_HOSTS = ",".join(
    [
        # AkShare data sources
        "eastmoney.com",
        "sina.com.cn",
        "sina.com",
        "akshare.xyz",
        "sse.com.cn",
        "szse.cn",
        # Notifiers
        "weixin.qq.com",
        "feishu.cn",
        "larksuite.com",
        "pushplus.plus",
        # LLM (智谱 GLM / DeepSeek)
        "bigmodel.cn",
        "deepseek.com",
        # PushPlus / others
        "gtimg.com",
    ]
)


def _install_no_proxy() -> None:
    existing = os.environ.get("NO_PROXY", "") or os.environ.get("no_proxy", "")
    parts = [p.strip() for p in existing.split(",") if p.strip()]
    for h in _DOMESTIC_HOSTS.split(","):
        if h and h not in parts:
            parts.append(h)
    val = ",".join(parts)
    os.environ["NO_PROXY"] = val
    os.environ["no_proxy"] = val


_install_no_proxy()
