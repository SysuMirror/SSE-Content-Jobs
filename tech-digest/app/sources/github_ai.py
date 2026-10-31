# -*- coding: utf-8 -*-
"""GitHub 前沿 AI 仓库源（2026-10-31 三类日报·实操栏目）：topic 搜索近期活跃高星仓库。

为什么走 search API 而不是 trending：trending 是「今天热」的全领域榜，AI 本地部署
项目经常不在榜上；topic 搜索（local-llm / text-to-video / llm-inference）能圈住
「能自己动手跑起来」的前沿项目，README 即带命令的部署指南（lanes 用 raw README
成稿）。未认证 search API 限 10 次/分钟——每班 3 个查询，安全。
"""
from __future__ import annotations

import datetime

from app.fetcher import http_get
from app.sources import new_item

SEARCH_URL = "https://api.github.com/search/repositories"
# 2026-10-31 用户裁决补 Agent 向：「更偏本地自己部署」——AI Agent / MCP 是
# 当前最活跃的可自部署方向；6 个 topic × 8 条，未认证 search 限 10 次/分钟安全。
TOPICS = ("local-llm", "text-to-video", "llm-inference",
          "ai-agents", "mcp", "autonomous-agents")
PER_QUERY = 8
MIN_STARS = 500
FRESH_DAYS = 45


def fetch() -> tuple[list[dict], str, dict]:
    since = (datetime.date.today() - datetime.timedelta(days=FRESH_DAYS)).isoformat()
    items: list[dict] = []
    seen: set[str] = set()
    detail: dict = {}
    for topic in TOPICS:
        try:
            resp = http_get(SEARCH_URL, params={
                "q": f"topic:{topic} pushed:>{since}",
                "sort": "stars", "order": "desc", "per_page": PER_QUERY}, timeout=20)
            data = resp.json()
        except Exception as e:  # noqa: BLE001 单 topic 失败不影响其余
            detail[topic] = f"error: {str(e)[:120]}"
            continue
        got = 0
        for repo in data.get("items", []) if isinstance(data, dict) else []:
            full = repo.get("full_name") or ""
            if not full or full in seen:
                continue
            stars = int(repo.get("stargazers_count") or 0)
            if stars < MIN_STARS:
                continue
            seen.add(full)
            it = new_item(
                type_="news", title=full,
                url=repo.get("html_url") or f"https://github.com/{full}",
                source="github-ai", author=full.split("/", 1)[0],
                summary=(repo.get("description") or "")[:200])
            it["points"] = stars   # 热度信号（pool._heat_signal 认 github-ai）
            items.append(it)
            got += 1
        detail[topic] = got
    if not items:
        raise RuntimeError(f"GitHub AI 搜索全部失败: {detail}")
    return items, "github-ai", detail
