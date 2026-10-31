# -*- coding: utf-8 -*-
"""轻链栏目（实操/开源仓）回归：选材质量线、契约重试、命令逐字钳制、渲染与复检。

全部离线：LLM 经 llmpool.map_chat mock，README 经 lanes._fetch_readme mock，
池子用 tmp 库。排班见 main._today_lanes（week_plan 默认全 deep=轻链不跑）。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from app import lanes
from app.pool import PoolStore

SRC_TEXT = ("先安装依赖：\npip install requests\n然后运行服务：\n"
            "python main.py --port 8080\n注意本机 8080 被占用时会启动失败。\n"
            "默认账号是 admin。\n")

SRC_BLOCKS = [
    {"id": "b1", "kind": "paragraph", "text": "先安装依赖："},
    {"id": "b2", "kind": "code", "text": "pip install requests"},
    {"id": "b3", "kind": "paragraph", "text": "然后运行服务："},
    {"id": "b4", "kind": "code", "text": "python main.py --port 8080"},
    {"id": "b5", "kind": "paragraph", "text": "注意本机 8080 被占用时会启动失败。默认账号是 admin。"},
]


def src_packet(title, author="作者", published_at=None, blocks=None):
    return {"text": SRC_TEXT, "title": title, "author": author,
            "published_at": published_at, "blocks": SRC_BLOCKS if blocks is None else blocks}


README_TEXT = ("# demo-kit\n一个把命令行输出变成网页面板的小工具。\n\n"
               "## 安装\n```\nnpm install -g demo-kit\n```\n\n## 使用\n```\ndemo-kit serve ./logs\n```\n")


def tut_item(n=1, source="juejin", url=None):
    url = url or f"https://juejin.cn/post/{n}"
    return {"type": "news", "title": f"手把手教你部署本地知识库{n}", "source": source,
            "url": url, "_pool_url": url,
            "summary": "本地知识库部署教程", "author": "作者", "points": 50}


def tut_scores(urls, total=0.7, article_type="tutorial"):
    dims = {"utility": 8, "depth": 5, "credibility": 8, "audience": 8, "novelty": 5}
    return {u: {"dims": dict(dims), "total": total, "article_type": article_type,
                "content_purpose": "tutorial", "kind": "judge"} for u in urls}


def tutorial_reply(title="十分钟部署你的本地知识库",
                   cmd=("pip install requests", "python main.py --port 8080", "")):
    return json.dumps({
        "title": title, "post_titles": [title],
        "learning_question": "怎么在本机跑起一个本地知识库",
        "environment": "Linux 或 macOS，Python 3.10+",
        "prerequisites": ["装好 Python"],
        "intro": "跟做完你就能在自己电脑上跑起一个本地知识库。",
        "steps": [
            {"heading": "安装依赖", "text": "先装 requests，装完不报错即可。", "command": cmd[0]},
            {"heading": "启动服务", "text": "把服务跑起来，看到监听 8080 即成功。", "command": cmd[1]},
            {"heading": "访问页面", "text": "浏览器打开本地地址确认页面出现。", "command": cmd[2]},
        ],
        "troubleshooting": ["端口被占用会导致启动失败，换端口即可"],
        "pitfalls": ["默认账号是 admin，记得改密码"],
        "takeaway": "动手是最快的学习方式。"})


def repo_item(n=1, full=None):
    full = full or f"a/b{n}"
    return {"type": "trending", "title": full, "url": f"https://github.com/{full}",
            "source": "github-trending", "author": full.split("/")[0],
            "published_at": None, "summary": "repo desc",
            "trending": {"rank": n, "language": "Go", "stars": 1200, "today_stars": 300}}


def repo_reply(cmd="npm install -g demo-kit"):
    return json.dumps({
        "title": "demo-kit：日志一键变网页面板", "post_titles": ["demo-kit：日志一键变网页面板"],
        "learning_question": "怎么把命令行日志变成可看的网页面板",
        "intro": "demo-kit 把命令行输出变成网页面板，省去自己搭看板。",
        "highlights": ["一条命令起服务", "支持任意日志目录"],
        "start_note": "全局安装后对日志目录启动", "command": cmd,
        "for_whom": "天天看终端输出的同学"})


class LaneBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pool = PoolStore(Path(self.tmp.name) / "pool.db")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.pool.close)


class TestTutorialQualified(LaneBase):
    def test_dims_gate(self):
        good = {"article_type": "tutorial",
                "dims": {"utility": 8, "depth": 5, "credibility": 8, "audience": 8, "novelty": 5}}
        self.assertTrue(lanes.tutorial_qualified(good))
        # 深度型研究文不是 tutorial 类型 → 不进实操栏目
        self.assertFalse(lanes.tutorial_qualified({**good, "article_type": "research"}))
        # 实用性不足
        low = {**good, "dims": {**good["dims"], "utility": 5}}
        self.assertFalse(lanes.tutorial_qualified(low))
        # 上手可行性硬线（2026-10-31 首日复盘：企业级运维内容 audience=6 混进合格线）
        hard = {**good, "dims": {**good["dims"], "audience": 6}}
        self.assertFalse(lanes.tutorial_qualified(hard))
        # 总分不足（全 6 = 6.0 < 6.5）
        flat = {**good, "dims": dict.fromkeys(good["dims"], 6)}
        self.assertFalse(lanes.tutorial_qualified(flat))
        self.assertFalse(lanes.tutorial_qualified({}))
        self.assertFalse(lanes.tutorial_qualified({"article_type": "tutorial", "dims": {"utility": True}}))

    def test_frontier_repo_accepts_resource(self):
        # github_ai 源：README 部署指南常被 judge 归 resource——前沿源放宽类型，
        # 命令闸在生产侧把关；非前沿源的 resource 仍然不收。
        good = {"article_type": "resource",
                "dims": {"utility": 8, "depth": 5, "credibility": 8, "audience": 8, "novelty": 5}}
        self.assertTrue(lanes.tutorial_qualified(good, frontier_repo=True))
        self.assertFalse(lanes.tutorial_qualified(good, frontier_repo=False))


class TestRunTutorial(LaneBase):
    @patch("app.llmpool.map_chat")
    @patch("app.extract.fetch_cached")
    def test_ready(self, fetch, chat):
        item = tut_item()
        fetch.return_value = src_packet(item["title"], published_at="2026-10-30")
        chat.return_value = [tutorial_reply()]
        with patch.object(lanes.editorial, "judge_score", return_value=tut_scores([item["_pool_url"]])):
            r = lanes.run_tutorial([item], pool=self.pool, recent_titles=[])
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["lane"], "tutorial")
        self.assertIn("pip install requests", r["markdown"])
        self.assertIn("python main.py --port 8080", r["markdown"])
        self.assertLessEqual(len(r["title"]), 30)
        self.assertEqual(r["draft"]["article_type"], "tutorial")
        self.assertEqual(r["source_packet"]["verified_commands"],
                         ["pip install requests", "python main.py --port 8080"])
        # 消费标记所需的 item 透传
        self.assertEqual(r["item"]["_pool_url"], item["_pool_url"])

    @patch("app.llmpool.map_chat")
    @patch("app.extract.fetch_cached")
    def test_contract_retry(self, fetch, chat):
        item = tut_item()
        fetch.return_value = src_packet(item["title"], author="")
        chat.side_effect = [["不是JSON的正文"], [tutorial_reply()]]
        with patch.object(lanes.editorial, "judge_score", return_value=tut_scores([item["_pool_url"]])):
            r = lanes.run_tutorial([item], pool=self.pool, recent_titles=[])
        self.assertEqual(r["status"], "ready")
        self.assertEqual(chat.call_count, 2)

    @patch("app.llmpool.map_chat")
    @patch("app.extract.fetch_cached")
    def test_command_clamp_rejects_fabricated(self, fetch, chat):
        item = tut_item()
        fetch.return_value = src_packet(item["title"], author="")
        # 编造的命令在两轮回喂后仍在 → 候选失败
        chat.side_effect = [tutorial_reply(cmd=("pip install requests", "rm -rf / 编造命令", ""))] * 3
        with patch.object(lanes.editorial, "judge_score", return_value=tut_scores([item["_pool_url"]])):
            r = lanes.run_tutorial([item], pool=self.pool, recent_titles=[])
        self.assertEqual(r["status"], "skip_quality")
        self.assertEqual(r["reasons"], ["tutorial_production_failed"])
        self.assertEqual(chat.call_count, 3)

    @patch("app.llmpool.map_chat")
    @patch("app.extract.fetch_cached")
    def test_clamp_drops_empty_command_steps(self, fetch, chat):
        item = tut_item()
        fetch.return_value = src_packet(item["title"], author="")
        chat.return_value = [tutorial_reply(cmd=("pip install requests", "", ""))]
        with patch.object(lanes.editorial, "judge_score", return_value=tut_scores([item["_pool_url"]])):
            r = lanes.run_tutorial([item], pool=self.pool, recent_titles=[])
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["source_packet"]["verified_commands"], ["pip install requests"])

    def test_no_qualified_candidates(self):
        with patch.object(lanes.editorial, "judge_score", return_value={}):
            r = lanes.run_tutorial([tut_item()], pool=self.pool, recent_titles=[])
        self.assertEqual(r["status"], "skip_quality")
        self.assertEqual(r["reasons"], ["no_tutorial_candidates"])

    @patch("app.llmpool.map_chat")
    @patch("app.extract.fetch_cached")
    def test_source_without_commands_rejected(self, fetch, chat):
        # 2026-10-31 用户裁决：课程介绍/学习路线类（原文无可执行内容）不配进实操栏目
        item = tut_item()
        fetch.return_value = src_packet(item["title"], blocks=[
            {"id": "b1", "kind": "paragraph", "text": "观看视频了解整体流程。"}])
        with patch.object(lanes.editorial, "judge_score", return_value=tut_scores([item["_pool_url"]])):
            r = lanes.run_tutorial([item], pool=self.pool, recent_titles=[])
        self.assertEqual(r["status"], "skip_quality")
        self.assertEqual(r["reasons"], ["tutorial_production_failed"])
        self.assertEqual(r["attempts"][0]["reasons"], ["source_without_commands"])
        chat.assert_not_called()

    @patch("app.llmpool.map_chat")
    @patch("app.extract.fetch_cached")
    def test_output_without_command_rejected(self, fetch, chat):
        # 原文有命令但成稿一条没有 → 仍是导读不是指南，契约回喂后拒
        item = tut_item()
        fetch.return_value = src_packet(item["title"])
        chat.side_effect = [tutorial_reply(cmd=("", "", ""))] * 3
        with patch.object(lanes.editorial, "judge_score", return_value=tut_scores([item["_pool_url"]])):
            r = lanes.run_tutorial([item], pool=self.pool, recent_titles=[])
        self.assertEqual(r["status"], "skip_quality")
        self.assertEqual(chat.call_count, 3)

    @patch("app.llmpool.map_chat")
    @patch("app.extract.fetch_cached")
    def test_fetch_failure(self, fetch, chat):
        item = tut_item()
        fetch.side_effect = RuntimeError("HTTP 403")
        with patch.object(lanes.editorial, "judge_score", return_value=tut_scores([item["_pool_url"]])):
            r = lanes.run_tutorial([item], pool=self.pool, recent_titles=[])
        self.assertEqual(r["status"], "skip_quality")
        self.assertEqual(r["reasons"], ["tutorial_production_failed"])
        self.assertEqual(r["attempts"][0]["reasons"], ["source_fetch_failed"])
        chat.assert_not_called()


class TestGithubTutorial(LaneBase):
    """github-ai 源：README 即部署指南（raw 抓取 + 围栏解析成 code 块）。"""

    @patch("app.lanes._fetch_readme")
    @patch("app.llmpool.map_chat")
    def test_github_repo_ready(self, chat, readme):
        item = tut_item(source="github-ai", url="https://github.com/a/demo-kit")
        readme.return_value = README_TEXT
        chat.return_value = [tutorial_reply(
            title="部署 demo-kit：日志一键变看板",
            cmd=("npm install -g demo-kit", "demo-kit serve ./logs", ""))]
        scores = tut_scores([item["_pool_url"]], article_type="resource")
        with patch.object(lanes.editorial, "judge_score", return_value=scores):
            r = lanes.run_tutorial([item], pool=self.pool, recent_titles=[])
        self.assertEqual(r["status"], "ready")
        self.assertIn("npm install -g demo-kit", r["markdown"])
        self.assertEqual(r["source_packet"]["verified_commands"],
                         ["npm install -g demo-kit", "demo-kit serve ./logs"])

    @patch("app.lanes._fetch_readme")
    @patch("app.llmpool.map_chat")
    def test_readme_without_fences_rejected(self, chat, readme):
        item = tut_item(source="github-ai", url="https://github.com/a/awesome-list")
        readme.return_value = "# Awesome\n- 收藏各种教程的列表\n- 没有命令\n"
        scores = tut_scores([item["_pool_url"]], article_type="resource")
        with patch.object(lanes.editorial, "judge_score", return_value=scores):
            r = lanes.run_tutorial([item], pool=self.pool, recent_titles=[])
        self.assertEqual(r["status"], "skip_quality")
        self.assertEqual(r["attempts"][0]["reasons"], ["source_without_commands"])
        chat.assert_not_called()


class TestRunRepo(LaneBase):
    @patch("app.lanes._fetch_readme")
    @patch("app.llmpool.map_chat")
    def test_ready_with_readme(self, chat, readme):
        readme.return_value = README_TEXT
        chat.return_value = [repo_reply()]
        r = lanes.run_repo([repo_item()], recent_titles=[])
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["lane"], "repo")
        self.assertIn("npm install -g demo-kit", r["markdown"])
        self.assertIn("+300", r["markdown"])
        self.assertEqual(r["source_packet"]["verified_commands"], ["npm install -g demo-kit"])
        self.assertIsNone(r["item"])   # trending 不做池消费标记

    @patch("app.lanes._fetch_readme")
    @patch("app.llmpool.map_chat")
    def test_simple_mode_without_readme(self, chat, readme):
        readme.return_value = ""
        chat.return_value = [repo_reply(cmd="")]
        r = lanes.run_repo([repo_item()], recent_titles=[])
        self.assertEqual(r["status"], "ready")
        self.assertIn("README 未能抓取", r["markdown"])
        self.assertNotIn("```bash", r["markdown"])
        self.assertEqual(r["source_packet"]["verified_commands"], [])

    @patch("app.lanes._fetch_readme")
    @patch("app.llmpool.map_chat")
    def test_simple_mode_rejects_command(self, chat, readme):
        readme.return_value = ""
        chat.return_value = [repo_reply(cmd="npm install -g demo-kit")] * 3
        r = lanes.run_repo([repo_item()], recent_titles=[])
        self.assertEqual(r["status"], "skip_quality")
        self.assertEqual(chat.call_count, 3)

    @patch("app.lanes._fetch_readme")
    @patch("app.llmpool.map_chat")
    def test_clamp_rejects_fabricated_command(self, chat, readme):
        readme.return_value = README_TEXT
        chat.return_value = [repo_reply(cmd="npm install 瞎编的包")] * 3
        r = lanes.run_repo([repo_item()], recent_titles=[])
        self.assertEqual(r["status"], "skip_quality")
        self.assertEqual(chat.call_count, 3)

    def test_recent_title_dedup(self):
        r = lanes.run_repo([repo_item(full="x/cooltool")],
                           recent_titles=["试试 cooltool 这个工具"])
        self.assertEqual(r["status"], "skip_quality")
        self.assertEqual(r["reasons"], ["no_repo_candidates"])

    @patch("app.lanes._fetch_readme")
    def test_no_desc_no_readme_fails(self, readme):
        readme.return_value = ""
        it = repo_item()
        it["summary"] = ""
        r = lanes.run_repo([it], recent_titles=[])
        self.assertEqual(r["status"], "skip_quality")
        self.assertEqual(r["attempts"][0]["reasons"], ["readme_and_desc_missing"])


class TestVerifyResult(LaneBase):
    def _ready(self):
        item = tut_item()
        with patch.object(lanes.extract, "fetch_cached",
                          return_value=src_packet(item["title"])), \
             patch.object(lanes.llmpool, "map_chat", return_value=[tutorial_reply()]), \
             patch.object(lanes.editorial, "judge_score",
                          return_value=tut_scores([item["_pool_url"]])):
            return lanes.run_tutorial([item], pool=self.pool, recent_titles=[])

    def test_ready_passes_verify(self):
        r = self._ready()
        self.assertEqual(r["status"], "ready")
        self.assertEqual(main._daily_publication_decision(r)["status"], "ready")

    def test_tampered_markdown_rejected(self):
        r = self._ready()
        tampered = dict(r, markdown=r["markdown"].replace("pip install requests", "pip3 install requests"))
        d = main._daily_publication_decision(tampered)
        self.assertEqual(d["status"], "skip_quality")
        self.assertIn("command_missing_from_markdown", d["reasons"])

    def test_title_mismatch_rejected(self):
        r = self._ready()
        d = main._daily_publication_decision(dict(r, title="换一个标题"))
        self.assertEqual(d["status"], "skip_quality")
        self.assertIn("final_title_mismatch", d["reasons"])

    def test_deep_results_still_go_through_old_gate(self):
        d = main._daily_publication_decision({"status": "skip_quality", "reasons": ["no_qualified_candidates"]})
        self.assertEqual(d["status"], "skip_quality")


class TestRenderers(unittest.TestCase):
    def _tut_ctx(self):
        item = tut_item()
        return {"title": "十分钟部署你的本地知识库", "item": item,
                "intro": "跟做完你就能在本机跑起一个本地知识库。",
                "prerequisites": ["装好 Python"],
                "steps": [{"heading": "安装依赖", "text": "先装 requests", "command": "pip install requests"},
                          {"heading": "启动服务", "text": "", "command": ""}],
                "pitfalls": ["端口冲突会导致启动失败"], "takeaway": "动手是最快的学习方式。"}

    def test_tutorial_post_shape(self):
        md = lanes.digest.render_tutorial_post(self._tut_ctx()).strip()
        self.assertTrue(md.startswith("## 十分钟部署你的本地知识库"))
        self.assertIn("### 跟着做", md)
        self.assertIn("```bash\npip install requests\n```", md)
        self.assertIn("**前置条件**：装好 Python", md)
        self.assertIn("来源：[手把手教你部署本地知识库1](https://juejin.cn/post/1)", md)
        self.assertLessEqual(len(md), 9000)
        self.assertEqual(lanes.digest.render_tutorial_post(self._tut_ctx()).strip(), md)   # 确定性

    def test_repo_post_shape(self):
        md = lanes.digest.render_repo_post({
            "title": "demo-kit：日志一键变网页面板", "item": repo_item(),
            "intro": "demo-kit 把命令行输出变成网页面板。", "highlights": ["一条命令起服务"],
            "start_note": "全局安装后启动", "command": "npm install -g demo-kit",
            "for_whom": "天天看终端输出的同学", "simple": False})
        self.assertIn("## demo-kit：日志一键变网页面板", md)
        self.assertIn("**亮点**", md)
        self.assertIn("GitHub 今日 +300 star · Go · 累计 1,200", md)
        self.assertIn("来源：[a/b1](https://github.com/a/b1)", md)
        simple = lanes.digest.render_repo_post({
            "title": "t", "item": repo_item(), "intro": "i", "highlights": [],
            "start_note": "", "command": "", "for_whom": "w", "simple": True})
        self.assertIn("README 未能抓取", simple)
        self.assertNotIn("```bash", simple)


class TestDayLanes(unittest.TestCase):
    def test_default_week_plan_is_deep_only(self):
        # 默认（env 未武装）任何一天都只跑深读——行为与三类改造前完全一致
        self.assertEqual(main._today_lanes("2026-11-02"), ["deep"])
        self.assertEqual(main._today_lanes("2026-11-07"), ["deep"])

    def test_armed_schedule(self):
        plan = ["deep", "tutorial", "repo", "deep", "repo", "tutorial", "deep"]
        with patch.object(main.settings, "week_plan", plan):
            self.assertEqual(main._today_lanes("2026-11-02"), ["deep", "repo", "tutorial"])   # 周一
            self.assertEqual(main._today_lanes("2026-11-03"), ["tutorial", "repo", "deep"])   # 周二
            self.assertEqual(main._today_lanes("2026-11-04"), ["repo", "tutorial", "deep"])   # 周三
            self.assertEqual(main._today_lanes("2026-11-06"), ["repo", "tutorial", "deep"])   # 周五
            self.assertEqual(main._today_lanes("2026-11-07"), ["tutorial", "repo", "deep"])   # 周六
            self.assertEqual(main._today_lanes("2026-11-08"), ["deep", "repo", "tutorial"])   # 周日

    def test_partial_plan_enables_only_listed_lanes(self):
        with patch.object(main.settings, "week_plan", ["repo", "deep", "deep", "deep", "deep", "deep", "deep"]):
            self.assertEqual(main._today_lanes("2026-11-02"), ["repo", "deep"])   # tutorial 未启用不顶
            self.assertEqual(main._today_lanes("2026-11-03"), ["deep", "repo"])   # deep 为主，repo 仍可顶

    def test_invalid_primary_falls_back_to_deep(self):
        with patch.object(main.settings, "week_plan", ["nonsense"] * 7):
            self.assertEqual(main._today_lanes("2026-11-02"), ["deep"])


if __name__ == "__main__":
    unittest.main()
