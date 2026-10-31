# 三类日报：深读 / 实操 / 开源仓（2026-10-31）

**状态**：代码+测试完成（846+23 全绿）；部署进行中。
**重要**：`week_plan` 默认全 deep——env 未武装时行为与改造前完全一致
（测试/回滚稳）；排班靠生产 spec env `TECH_DIGEST_WEEK_PLAN` 武装。
背景：日报内容距大学生太远，用户裁决改造为三类文章，
每天一帖雷打不动（周末也发），按星期排班。

## 一周排班（main._today_lanes，周一=0）

| 星期 | 主类型 | 定位 |
|------|--------|------|
| 周一 | deep | 一周开局，硬核定调 |
| 周二 | tutorial | 晚上能跟做 |
| 周三 | repo | 课间可读，种草 |
| 周四 | deep | 中周硬核 |
| 周五 | repo | 周末前种草 |
| 周六 | tutorial | 周末有时间真动手；与周六星榜帖互补（星榜=看榜，日报=动手） |
| 周日 | deep | 安静读长文收尾 |

一周 = 深读 3 + 实操 2 + 开源 2。星榜帖（周六）不动。
env 覆盖：`TECH_DIGEST_WEEK_PLAN=deep,tutorial,repo,deep,repo,tutorial,deep`。

**保发机制**：主类型生产失败按 repo → tutorial → deep 顶上（深读全链兜底
能力最强放最后）。每天必发一帖的目标不倒退；rc 契约 / dup 门 / 期数 /
标题管线全部沿用。

## 三条生产链

1. **deep**：现状 editorial.research-v1 全链，一行不改。
2. **tutorial**（app/lanes.py 轻链）：judge 放行 article_type=tutorial
   （质量线：总分≥6.5 且 utility≥7、audience≥6、credibility≥6；judge 带
   lane_hint + hash salt，与深读评分缓存隔离）→ 抓全文 → 契约置顶 JSON
   改写成学生向动手导读 → **命令逐字钳制**（空白归一后必须是原文子串，
   防编造步骤；违例带错误回喂≤2轮，仍违例剔除、全灭则该候选失败）→
   纯校验渲染。
3. **repo**（app/lanes.py 轻链）：trending 泳道最新一班（top_candidates
   之外独立现成数据）→ 按 rank 取未做过的仓 → raw.githubusercontent
   HEAD/README.md（fail-open：抓不到走简版，只用 desc、不上手段）→
   是什么/亮点/上手/适合谁 → 命令钳制同上 → 纯校验渲染。

**轻链 v1 不设独立 LLM 终审**（用户裁决点，可后补）：编造代价评估=教程/仓
介绍的核心事实载荷是命令与代码，已由机械钳制覆盖；正文短且以原文链接兜底。
深读链的重型证据绑定契约不动。

## 落点

- `app/lanes.py`（新）：选材/生产/钳制/verify_result
- `app/digest.py`：render_tutorial_post / render_repo_post（纯 markdown，
  无裸 HTML，emoji 禁令照旧）
- `app/editorial.py`：judge_score 加 lane_hint + salt 参数（默认值=现行为）
- `app/config.py`：week_plan
- `main.py`：_today_lanes + run_daily 三链调度 + _daily_publication_decision
  分 lane 校验 + run_log/publish content_meta 带 lane
- 复用：llmpool 退避、titles.pick、markdown_issues、MAX_PUBLICATION_CHARS、
  strip_emoji、池子 score/stage 落库（断点续跑语义同深读）

## 已知取舍（记账）

- 池模式 top32 候选里 tutorial 类供给量未验证，饿稿则后续加宽 tutorial 日
  的候选窗（ingest 源不动，juejin/sspai/fcc/devto 已是教程富矿）。
- deep 主日 fallback 到 tutorial 时 judge 缓存不带 hint（salt 隔离），
  教程候选按研究视角评分可能偏严——接受的罕见路径。
- repo 去重=近 14 期标题含 owner/repo 或短名子串则跳过；trending 行 7 天
  生命周期天然兜底。
- README 抓取走 raw.githubusercontent，服务器连通性时好时坏 → fail-open
  简版，不挡发帖。
- **部署清单**：①bundle 灌 /root/qiudock/repo-cache/Agent.git（守卫已挡
  cron 打回）；②CronJob spec env 加 TECH_DIGEST_WEEK_PLAN=
  deep,tutorial,repo,deep,repo,tutorial,deep（不改这步=只发深读，等于没上）；
  ③次日 09:15 观察班次 run_log 的 lane/lane_trials 字段。
