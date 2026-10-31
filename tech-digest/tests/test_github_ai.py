# -*- coding: utf-8 -*-
"""GitHub 前沿 AI 仓库源：topic 搜索、星数过滤、去重、失败语义（离线 mock）。"""
import json
import unittest
from unittest.mock import patch

from app.sources import github_ai


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def repo(full, stars=5000, desc="x"):
    return {"full_name": full, "html_url": f"https://github.com/{full}",
            "description": desc, "stargazers_count": stars}


class TestGithubAI(unittest.TestCase):
    @patch.object(github_ai, "http_get")
    def test_items_and_filters(self, get):
        get.return_value = _FakeResp({"items": [
            repo("ollama/ollama", 90000), repo("tiny/x", 10), repo("ollama/ollama", 90000)]})
        items, label, _ = github_ai.fetch()
        self.assertEqual(label, "github-ai")
        names = {it["title"] for it in items}
        self.assertIn("ollama/ollama", names)   # 星数过关
        self.assertNotIn("tiny/x", names)       # 低于 MIN_STARS 剔除
        for it in items:
            self.assertEqual(it["source"], "github-ai")
            self.assertEqual(it["type"], "news")
            self.assertTrue(it["url"].startswith("https://github.com/"))
            self.assertGreater(it["points"], 0)

    @patch.object(github_ai, "http_get")
    def test_all_fail_raises(self, get):
        get.side_effect = RuntimeError("HTTP 403")
        with self.assertRaises(RuntimeError):
            github_ai.fetch()

    @patch.object(github_ai, "http_get")
    def test_partial_topic_failure_still_returns(self, get):
        calls = {"n": 0}

        def fake(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return _FakeResp({"items": [repo("a/good", 800)]})
        get.side_effect = fake
        items, _, detail = github_ai.fetch()
        self.assertTrue(items)
        self.assertTrue(any("error" in str(v) for v in detail.values()))


if __name__ == "__main__":
    unittest.main()
