# tech-digest 数据摄取链路健壮性审计

- 需求单：**SK-26A6F01B**（缺陷 / 平台 content-jobs / normal）
- 审计对象：`tech-digest/` 日报数据摄取链路（各内容源抓取器/解析器、HTTP 层、去重、池化落库）
- 基线提交：`61583b6`；审计分支：`fix/yz-sk-26a6f01b`
- 审计方式：**只读**代码走查 + 定点探针验证 + 全量单测回归；**未修改任何源码**
- 审计日期：2026-11-23

---

## 0. 结论摘要

摄取链路整体是「单源 fail-open、全源失败才报错」的合理骨架：单源异常会被
`fetch_all` 捕获并记入 stats，全源无数据时整体报错、池子保持原状；池化 upsert
以规范化 URL 为唯一键，重跑不会重复入池。**主要风险不在「正常路径」，
而在三类边界**：

1. **失败面不够窄**：一个源模块的 import 失败会绕过单源 try，直接打崩整班 ingest；
   写库异常、损坏的历史快照也没有兜底。
2. **静默降级/静默丢数据**：GitHub 主源改版后自动切「7 天内新建仓库按星排序」，
   但条目仍以 `github-trending` 身份入池（只有当班 run_log 的 `source` 标签能看出
   降级，池内/星榜侧不可见）；RSS 缺 `link` 的条目、池内 `expired` 行的刷新、
   薄池 live 补齐等场景均无区分痕迹/无告警。
3. **去重键覆盖不全**：URL 规范化漏掉 `www.`/`http↔https`/`index.html`/百分号编码等
   常见变体；薄池补齐完全绕过近 14 天历史去重，可能重复发布。

### Top 5 风险（详见 §5）

| # | 风险 | 等级 | 主要位置 |
|---|---|---|---|
| 1 | 单源模块 import 失败 → 整班 ingest 崩溃，无单源降级、无 run_log | 高 | `tech-digest/app/sources/__init__.py:79-81`、`tech-digest/main.py:89-148` |
| 2 | 去重三处漏洞：URL 变体漏判、`expired` 刷新永不复用、薄池补齐绕过历史去重 | 高 | `tech-digest/app/dedup.py:17-46`、`tech-digest/app/pool.py:192-201`、`tech-digest/main.py:479-487` |
| 3 | GitHub 主源改版静默降级为 Search API，条目身份不变、池内/星榜侧不可见 → 星榜语义失真 | 中高 | `tech-digest/app/sources/github.py:30-61`、`tech-digest/main.py:113-114` |
| 4 | 无总超时预算/响应体上限；429 不重试不冷却；掘金源绕过统一抓取层 | 中 | `tech-digest/app/fetcher.py:17-44`、`tech-digest/app/sources/juejin.py:41-49` |
| 5 | 可观测性与重放缺口：detail 被剥离、冷却不入库、解析器丢条无计数、镜像失败静默、无原始响应留存 | 中高 | `tech-digest/main.py:107-141`、`tech-digest/app/mirror.py:37-53`、`tech-digest/app/extract.py:618-641` |

---

## 1. 审计范围与方法

### 1.1 覆盖文件

| 层次 | 文件 |
|---|---|
| HTTP 抓取 | `tech-digest/app/fetcher.py` |
| 源调度 | `tech-digest/app/sources/__init__.py` |
| 各源解析 | `tech-digest/app/sources/{feeds,github,hacker_news,sspai,devto,juejin,wscn,zhihu_hot}.py`、`tech-digest/app/parser.py` |
| 去重 | `tech-digest/app/dedup.py`、`tech-digest/app/pool.py` |
| 落库/台账 | `tech-digest/app/store.py`、`tech-digest/main.py`（`run_ingest`/`run_daily` 取材段） |
| 正文抓取/镜像 | `tech-digest/app/extract.py`、`tech-digest/app/mirror.py` |
| 调度 | `deploy-jobs/jobs.json`、`deploy-jobs/scheduler.py` |

### 1.2 验证手段

- 全量单测：`python3 -m unittest discover -s tests -t .` → **818 tests OK**（tech-digest 目录）。
- 定点探针（不写仓库，只用临时目录）：
  - `normalize_url` 对 6 组常见 URL 变体的判定；
  - 池内 `expired` 行被再次抓取时的状态；
  - `latest_trending` 两班数据混窗；
  - 单源模块 import 失败时的 `fetch_all` 行为；
  - 通用 RSS 解析在 `link` 缺失/相对链接下的行为。
- 探针输出见 §6。

---

## 2. 链路与失败语义概览

```
scheduler (每 2h, 7 */2 * * *)
  └─ main.py ingest
       ├─ PoolStore.cooldowns()            跳过 403/405 冷却源
       ├─ sources.fetch_all(sources)
       │    ├─ importlib 全量导入 KNOWN_SOURCES（24 个模块）   ← 风险点 A
       │    ├─ 逐源 fn() → (items, label, detail)              ← 单源 try/except
       │    │    └─ fetcher.http_get（2s 全局限速、3 次重试、4xx 不重试）
       │    └─ items 为空 → RuntimeError("全部源失败")
       ├─ news/trending 分流
       ├─ pool.upsert_news(news, history=近 14 期已发 news)    ← 去重权威键=规范化 URL
       ├─ pool.upsert_trending(trending)
       ├─ pool.sweep()（TTL / 保留期）
       └─ store.log(run_log, slim_stats)                       ← detail 被剥离
```

失败语义（现状）：

| 失败类型 | 表现 | 是否留痕 |
|---|---|---|
| 单源网络/解析异常 | 该源 `fetched=0,error=...`，其他源继续 | 文本日志 + run_log（error 串截断 200 字） |
| 单源返回 0 条 | 源内主动 `raise RuntimeError`，同上 | 同上 |
| 全部源 0 条 | `fetch_all` 抛 `RuntimeError` → run_log error，rc=1，池不动 | 有 |
| 源模块 import 失败 | 抛 `ImportError`，**绕过全部兜底**，进程崩溃 | 仅进程 stderr（scheduler 日志），无 run_log |
| 写库异常 / 历史 JSON 损坏 | 未捕获，进程崩溃 | 无 run_log |
| 解析部分结构变化 | 部分条目被静默跳过，源仍显示成功 | 无计数、无告警 |

---

## 3. 逐源健壮性矩阵

> 「结构变更表现」指目标站点返回 200 但 schema/HTML 结构变化时。  
> 「静默丢数据」指没有 error、没有计数、源仍显示成功的情况。

| 源 | 抓取/解析 | 超时/重试 | 结构变更表现 | 静默丢数据风险 |
|---|---|---|---|---|
| GitHub Trending | HTML + BS4；`article.Box-row` | `http_get` 默认 timeout=20，3 次 | Box-row < 5 → 自动切 Search API | **高**：降级语义混入 trending，见 F5 |
| Hacker News | Algolia JSON → Firebase 兜底 | 12s/10s，3 次/1 次 | 响应形状异常 → 兜底；兜底失败 → 源失败 | 低（低分/无外链为设计过滤） |
| sspai | RSS（仅 `<item>`） | 15s，3 次 | 改 Atom → 0 条 → 源失败 | 中：缺 link 条目静默跳过，无计数 |
| dev.to | 官方 JSON API | 12s，3 次 | 响应非 list → 源失败 | 低 |
| 掘金 | **直接 `requests.post`** | 15s，**无重试/无限速** | `err_no!=0` → 源失败 | 低（但整班丢源概率抬高） |
| 华尔街见闻 | information-flow JSON | 15s，3 次 | `data` 非 dict → AttributeError → 源失败 | 低 |
| 通用 RSS×17 | RSS2/Atom 共用解析 | 15s，3 次 | 整 feed 0 条 → 源失败 | **中**：缺 link/相对链接/无新鲜度过滤，见 F4 |
| 知乎热榜（默认关） | 正则抓 tophub HTML | 15s，3 次 | 0 条 → ValueError → 源失败 | 中（已下线，默认不抓） |
| 正文抓取 | `extract.extract_article` | 12s，3 次 | 解析失败产出 `issues`，交质量闸门 | 低（有 issues 面） |
| 见闻镜像 | 内容 API | 12s，3 次 | `code!=20000` → **返回 None** | **中**：失败静默，见 F7 |

---

## 4. 分项发现

### A. 网络层（超时/重试/限速）

#### F1【中】无「整班总预算」，串行重试可放大到数十分钟

**现状**
- `tech-digest/app/fetcher.py:17-44`：每次 `http_get` 独立重试 3 次，退避
  1.5s/3s，全局 2s 最低间隔；`timeout = timeout or settings.http_timeout`
  （默认 20s，`tech-digest/app/config.py:91`）。
- `requests` 的 `timeout` 是「连接超时 / 套接字无数据间隔」的超时，不是请求总时长；
  服务器慢速滴流时单次请求可无限延长。
- `tech-digest/app/sources/__init__.py:86-105` 串行抓取（默认 22 个启用源），无共享 deadline。
- HN Firebase 兜底逐条抓 item（`tech-digest/app/sources/hacker_news.py:95-102`），
  最多 `max_items*3=24` 次、每次 10s、`retries=1`，最坏约 4~5 分钟。
- daily 的可行性预检对最多 32（池模式）/48（live 模式）个候选串行 `fetch_cached`（`tech-digest/app/editorial.py:454-465`），
  每篇最多 3 次 × 12s（约 36s，另加限速/退避），32 篇最坏约 20 分钟，48 篇更长。

**风险场景**：某个站点半死（连接成功但响应极慢）时，单源即可吃掉数分钟；多源同时
劣化时，ingest 按「每次重试都超时」估算约 20~25 分钟，而慢速滴流（每 <timeout 秒
发一点数据）下单次请求没有上界，单班可能越过 2h 调度间隔。`deploy-jobs/scheduler.py:96-99`
在同 job 仍存活时会跳过下一班——即**下一班数据直接缺失**；若换成默认并发策略的
k8s CronJob，则可能出现重叠执行（见 F16）。

**建议**
1. 给 `fetch_all` 增加 `deadline`（如 90% 调度间隔），每源开始前计算剩余预算并按剩余
   预算收敛 timeout/retries；预检同理。
2. `timeout` 改为 `(connect, read)` 元组，并对响应体设置大小上限（见 F1-b）。
3. 对慢源做「连续 N 班超时 → 降频/冷却」，而不是每班都撞满重试。

> F1-b【低】响应体无上限：`fetcher.py:32` 直接取 `resp.text` 全量解码；错误配置的
> feed/镜像站返回超大 body 时会占用内存。建议 `stream=True` + 读取上限（如 5MB）。

#### F2【中】429 不重试、不冷却；403/405 冷却 24h 可能误伤

**现状**
- `tech-digest/app/fetcher.py:38-39`：`400 <= status < 500` 一律不重试、直接失败。
- `tech-digest/main.py:84-86`：仅 `403`/`405` 触发 24h 冷却。
- 429（Too Many Requests）既不重试也不冷却，源在本班直接丢失；`Retry-After` 头未使用。
- 反过来，403 也可能是 Cloudflare 的瞬时挑战（不是永久封禁），一次命中就整整
  24h 不抓该源，属于「过度冷却」。

**建议**：429 纳入退避重试并尊重 `Retry-After`；冷却策略改为「连续命中 N 次才冷却」，
并在 run_log 记录冷却事件。

#### F3【中】掘金源绕过统一抓取层

**现状**：`tech-digest/app/sources/juejin.py:41-49` 直接
`requests.post(...timeout=15)` + `raise_for_status()`，没有重试、没有 2s 限速、
没有统一 UA 之外的任何治理；与其余源的行为不一致。

**风险**：一次瞬时 5xx/网络抖动就让该源整班缺数据；无全局限速时若未来并发化，
可能触发对方风控。

**建议**：在 `fetcher.py` 增加 `http_post`（复用重试/限速逻辑）或让 `http_get`
支持 method，掘金改走统一封装。

---

### B. 结构变更失败表现

#### F4【中】通用 RSS/Atom：缺 `link` 静默丢条、相对链接不校验、无新鲜度过滤

**现状**
- `tech-digest/app/sources/feeds.py:77-88`（RSS）与 `:98-111`（Atom）：
  `title` 或 `link` 缺失即 `return None`，条目**静默丢弃**；`fetch_feed`
  （`:128-133`）只返回 `keep` 数量，没有 malformed/skipped 计数。
- RSS 2.0 允许用 `<guid isPermaLink="true">` 作为规范链接；若站点把 `link` 改成
  `guid`，部分/全部条目会丢失。探针验证：只有 `guid` 的 T1 被丢弃、只有 `link`
  的 T2 保留（见 §6）。
- 相对链接原样写入 `new_item`（探针得到 `/relative/3`），`normalize_url` 会生成
  `:///relative/3` 这类畸形键；该条仍会被视为「抓取成功」，直到 daily 预检才判死。
- 取条目按文档顺序 `[:max_items]`（`:130`），不按 `published_at` 过滤；feed 若把
  置顶/历史条目排在前面，会挤掉当天新内容，且没有「过旧条目」过滤。
- 17 个源共用同一套解析（模块 docstring 仍写「十三源」，已过时），任何共性结构变化都会同时影响所有 RSS 源（虽有单源
  fail-open，但会集体降级）。

**建议**：`_rss_entry` 增加 `guid` 回退；`fetch_feed` 记录
`raw_entries/malformed/skipped_old` 计数进 `detail`；对相对 URL 做 `urljoin`，
非 `http(s)` 绝对地址直接判 malformed；可按 `published_at` 加最小新鲜度过滤
（或至少只记录不拦截）。

#### F5【中高】GitHub 主源改版 → 静默语义降级，且不可追溯

**现状**
- `tech-digest/app/sources/github.py:30-41`：HTML 解析失败（Box-row < 5，见
  `tech-digest/app/parser.py:36-41`）时，`detail["parser_note"]` 标注「疑改版」，
  随后走 Search API 备源（`:43-61`）。
- 备源语义是「**7 天内新建仓库按星数排序**」，不是 Trending；但 `_to_v2`
  （`github.py:17-27`）把条目 `source` 固定为 `"github-trending"`，只有 stats 的
  `source` 标签是 `"github-api-fallback"`（`:61`）。
- 入池后按 `github-trending` 计权重/热度（`tech-digest/app/pool.py:31-41, 98-100`），
  池行与真实 Trending 无法区分。run_log 的 `source_stats` 会保留
  `"source": "github-api-fallback"` 这一当班标签（`main.py:113-114` 只剥
  `detail`），但 `detail` 里的 `parser_note`/`primary_error` 不落库，池行 raw
  里也没有降级标记。
- 星榜帖（`main.py:599-635`）直接消费 trending 池；备源结果会被当作
  「GitHub 开源星榜 Top 15」发布。
- 备源还会用 `store.get_daily_since(本周一)` 过滤已入库仓库
  （`github.py:48-57`），过滤异常被静默吞掉；若本周热门仓库都已在快照里，
  备源可能返回空 → 整源失败。

**风险场景**：GitHub 改版 + 本周热点早已入库 → 星榜要么展示「新建仓库星数榜」
冒充 Trending，要么整源失败停发。run_log 只能看到一个 source 标签，改版/降级的
具体原因（detail）已丢失；池内与星榜渲染侧则完全无法区分。

**建议**
1. `_to_v2` 增加 `source_mode: "html" | "api_fallback"`（或独立 source 标签），
   入池 raw 保留，便于下游标注「降级数据」。
2. run_log 至少保留 `detail.parser_note/primary_error`（或专门的 `mode` 字段）。
3. 星榜渲染对 fallback 数据加显式标注，或干脆要求主源成功才发星榜。

#### F6【低】各源内部过滤是「设计性丢条」，但缺统一计数

HN 低分/无外链、dev.to 无效项、sspai 生活类过滤、见闻 live/付费过滤等均为有意过滤，
但只有部分源把 `candidates/keep/skipped` 写进 `detail`，且 `detail` 不进 run_log。
结果是「今天这个源为什么少了 80%」基本无法从台账回答。建议统一
`detail = {raw, kept, skipped_by_reason}` 并入库（见 F15）。

#### F7【中】镜像通道失败静默；`short_text` 不触发镜像

**现状**
- `tech-digest/app/mirror.py:37-53`：内容 API `code != "20000"` 或正文 < 500 字时
  **返回 None**，不打日志、不计数。
- `tech-digest/app/extract.py:618-641` 的 `fetch_with_mirror` 只在**抛异常**时打
  warning；`mirror_fetch` 返回 None 时什么也不留。
- 触发镜像的条件（`:633`）只包含 `empty_content/login_required/fulltext_required`，
  **不含 `short_text`**。华尔街见闻文章页是 SPA 空壳，若抓到的壳文本非空但很短，
  会以短文本进入研究流程而不是走镜像。
- 默认源清单已移除 `wallstreetcn`（`config.py:98-103`），该路径当前默认不活跃，
  但历史 env 回切/池内存量仍可能触发。

**建议**：`unusable` 判定加入 `short_text`（对 `needs_mirror` URL）；`mirror_fetch`
返回 None 时记 info/计数；把镜像命中率写进来源健康指标。

---

### C. 去重机制

#### F8【高】URL 规范化覆盖不全，重复条目可绕过唯一键

**现状**：`tech-digest/app/dedup.py:17-46`

| 变体 | 是否判等 | 探针结果 |
|---|---|---|
| `www.example.com` vs `example.com` | 否 | `False` |
| `http://` vs `https://` | 否 | `False` |
| `/a/index.html` vs `/a/` | 否 | `False` |
| `/a%20b` vs `/a b` | 否 | `False` |
| `:443` 默认端口 vs 无端口 | 否 | `False` |
| query 参数顺序不同 | 是 | `True` |

此外：
- `STOP_PARAMS` 包含 `source`、`from`（`:13-14`），会无差别剔除——某些站点用它们
  作为内容标识，可能造成**误并**；`fro` 疑似 `from` 的笔误。
- query 键只在小写比较时用于过滤，输出保留原大小写，排序区分大小写。
- 相对 URL 未校验（探针 → `:///foo/bar`）。

**影响**：池唯一键 `(kind,url)`（`pool.py:79`）和标题模糊去重都可能被这些变体绕过，
同一文章换 `www`/协议/编码后重复入池、重复进入候选，最终可能重复发帖；跨源去重
（HN 常给无 `www` 链接、feed 常给带 `www`/带跟踪参数链接）尤其受影响。

**建议**（低成本、收益高）：
1. host 去 `www.`、去默认端口；scheme 统一（或跨 scheme 比较时忽略）；
2. path 去 `index.html`/`index.htm`；query 值做百分号解码后再比较；
3. 评估是否保留 `source`/`from` 在 `STOP_PARAMS`（至少改成站点白名单）；
4. 对相对 URL 直接拒绝或 `urljoin` 源站。

#### F9【高】`expired` 行被再次抓到时保持 `expired`，永久不可用

**现状**：`tech-digest/app/pool.py:192-201` 的刷新分支只更新
`raw/heat/last_seen/title/published_at`，**不改 `status`、不改 `first_seen`**；
而 `top_candidates` 只取 `status='candidate'`（`:262-265`）。

**复现（探针）**：一条 8 天前入池、已 `expired` 的行，被同 URL 再次抓取后
`refreshed=1`，但 `status` 仍为 `expired`，`top_candidates()` 返回空。
`first_seen` 也没有更新，即使将来 reset 状态也会因 TTL 立刻再过期。

**影响**：长尾/常青内容（或某源延迟再次推送的同 URL）会永远回不到候选池，
且台账上看是「已刷新」，没有任何告警。

**建议**：刷新分支显式处理状态：
- 若 `status='expired'`：按策略（推荐）将 `first_seen=now,status='candidate'` 复活，
  或明确记为 `dup_dropped` 并计数；
- `daily_used`/`reserved` 保持不复活（防重发，语义正确）；
- 在返回结构里增加 `revived/stale_refreshed` 计数。

#### F10【高】薄池 live 补齐绕过历史去重

**现状**：`tech-digest/main.py:475-487`。池内候选少于 `POOL_MIN_CANDIDATES`
（16）时调用 `fetch_all()` 即时抓取，然后：

```python
added = [c for c in _daily_candidates(live_news)
         if normalize_url(c.get("url") or "") not in seen]   # 只比 URL
```

- 没有调用 `dedup.dedupe_news(..., _dedup_history(...))`（live 分支在 `:492`
  才调用）；
- `seen` 只包含**池内当前候选**的 URL，不包含近 14 天已发快照；
- 也不与池内候选做标题相似度比较。

**影响**：薄池是常态（单源故障/质量全灭后常见）。此时一篇昨天已发过的文章，只要
URL 变了（镜像站/参数不同）或来自另一个源，就会重新进入候选并可能被再次选中发布。
`recent_titles` 只用于标题生成（`editorial.py:516`），不参与候选排除。

**建议**：补齐的 live 候选先过一遍 `dedupe_news(live_news, _dedup_history(store, day))`，
再做 URL/标题对池内候选的去重；把补齐阶段的剔除计数写进 run_log。

#### F11【中】标题相似度的固有盲区

**现状**：`tech-digest/app/dedup.py:54-74`，`SequenceMatcher` 对 lower 后的原始
标题做字符相似度，阈值 0.85。

- 标题带站点后缀（`xxx | Ars Technica`）、中英混排、标点差异会拉低相似度；
- 跨语言转载（英文原文 vs 中文翻译）无法识别；
- 批内去重「先到先得」（`:89-97` 先查历史、再查已保留条目；保留顺序=源列表顺序），
  同一文章多源出现时，保留的是**源清单里靠前的源**，而不是权重/质量更高的源
  （`upsert_news` 同样先到先得）。
- 标题模糊对比是 O(候选×历史)，14 天历史窗口下量级可控，但每个 new item 都会
  与数百条历史做 `SequenceMatcher`；目前靠 `quick_ratio` 剪枝（`:71-73`），
  建议持续关注 ingest 耗时。

**建议**：规范化标题（去站点后缀、标点/空白统一）后再比；按
`source_weight` 决定多源重复时保留哪一条；跨语言去重可后续用 URL/翻译标题做补充，
本期不必强行上。

#### F12【中】`latest_trending` 的 3h 窗口会混入上一班

**现状**：`tech-digest/app/pool.py:312-338`，以 `MAX(last_seen)` 为基准回看
`TRENDING_BATCH_WINDOW_H=3`（`:29`），而 ingest 每 2h 一班
（`deploy-jobs/jobs.json:3`）。

**影响**：上一班出现、本班已掉榜的仓库，`last_seen` 仍在 3h 窗口内，会被当作
「最新一班」返回（探针中连续两批 upsert 的条目被同时返回；按 2h 调度同理）。
星榜帖可能包含已掉榜仓库；若两班合计超过 limit，交叉排序后还可能挤掉本班条目。
现有单测用 26h 前的时间戳构造（`tests/test_pool.py:270-279`），没有覆盖 2h 间隔的
真实调度。

**建议**：给 ingest 每班写一个 `batch_id`（或使用精确的 `last_seen == MAX`），
`latest_trending` 按批次取，而不是时间窗；至少把窗口降到略大于单班耗时
（如 30min）并加测试。

---

### D. 异常处理、幂等与可观测性

#### F13【高】源模块 import 失败打崩整班 ingest

**现状**：`tech-digest/app/sources/__init__.py:79-81` 在 `fetch_all` 进入逐源
try **之前**，用字典推导把 `KNOWN_SOURCES` 里的**全部 24 个模块**都 import 一遍
（无论是否启用）。任何模块顶层 import 失败（语法错误、缺少依赖、循环 import）
都会让 `fetch_all` 抛 `ImportError`。

`main.py:100-106` 只捕获 `RuntimeError`（全源失败），`ImportError` 继续上抛，
`run_ingest` 没有外层 `except`，进程崩溃；`run_log` 不会写入，池子不变但本班丢失。

**探针**：mock 一个源模块 import 抛 `ImportError`，`fetch_all(["hacker-news","sspai"])`
直接抛 `ImportError: boom: broken deploy`，而不是把该源记为 error。

**影响**：一次坏的部署/一次缺 `bs4` 等依赖，会让**所有源**同时不可用（包括没启用的
源），且运维只能在进程 stderr 里找原因。

**建议**（低成本 P0）：
- registry 改为按 `names` 惰性导入（只导入启用源），且每个模块 import 包 try，
  失败记为该源 error；
- `run_ingest` 外层捕获 `Exception` 并写 `run_log(status='error')` 后返回 1；
- 注册表完整性测试已存在（`tests/test_sources.py::TestRegistryContract`），可保留。

#### F14【高】写库/历史读取异常未兜底；损坏快照会让 ingest 永久崩溃

**现状**
- `main.py:128-129` 调用 `_dedup_history(store, ...)`，而 `run_ingest` 没有
  `except` 兜底；后者经
  `store.get_daily_since`（`tech-digest/app/store.py:111-123`）对每行
  `json.loads(items_json)`。任一行 JSON 损坏 → `ValueError` 上抛 → 进程崩溃，
  且**每班都会重复崩溃**，直到人工修库。
- `pool.upsert_news/upsert_trending/sweep/stats`、`store.log` 的
  `sqlite3.OperationalError/IntegrityError` 同样无人捕获。
- `PoolStore.__init__` 的 schema 迁移、`executescript` 失败也会直接崩。

**影响**：单点脏数据/锁冲突 → 整条摄取链路停摆且无台账；与 F13 叠加后，
「源全好但 ingest 就是挂」很难定位。

**建议**：`run_ingest` 用一个大 `try/except Exception` 包住「历史读取 + upsert +
sweep + log」，异常时尽量写一条 `run_log(error)`（若 DB 本身不可用则退化为
stderr），返回非 0；`get_daily_since` 对单行 JSON 损坏跳过并记 warning/计数。

#### F15【中高】可观测性与重放缺口

**现状**
- `main.py:113-114` 构造 `slim_stats` 时把 `detail` 整个剥掉，`run_log` 里没有
  `parser_note/primary_error/api/candidates/keep/errors/life_filtered` 等依据；
  `main.py:540` 的 daily 路径同样剥 `detail`。
- 冷却事件（`main.py:107-110`）只写文本日志，不进 `run_log`；历史冷却状态只在
  下一班的 `cooldowns` 字段里出现。
- 镜像失败返回 None 静默（F7）；RSS 缺 link 静默（F4）；`parse_num` 无法识别
  数字格式时静默返回 0（`tech-digest/app/parser.py:19-33`，影响排序信号）。
- **无原始响应留存**：池表 `raw` 存的是解析后的 v2 条目（`pool.py:56-67`），
  不是 HTTP body。解析器升级/修 bug 后无法对旧响应重放，只能重新抓取（内容可能
  已变）；`ingest --dry-run` 刻意不落池、只写 run_log（`main.py:115-125`），
  因此也没有可供重放的解析产物。
- 外部告警（qdock 死人开关，见 `tech-digest/docs/superpowers/plans/2026-10-15-digest-resilience-sources.md`）
  以「今日是否有成功发帖」为准，不监控「源连续失败/条数骤降」；源悄悄死掉时，
  在内容耗尽前可能无人发现。
- 文本日志为 `RotatingFileHandler(500KB × 14)`（`main.py:66-68`），约 7MB 上限，
  长期回溯有限。

**建议**
1. run_log 保留 `detail`（体积可控；或在 `detail` 内裁剪到关键字段），至少保留
   mode/fallback/malformed 计数。
2. 冷却事件写入 run_log（`source_cooldown`），便于周报/运维面板聚合。
3. 增加来源健康指标：连续失败班数、本班条数 / 近 7 天中位数、0 条但 HTTP 200 的
   次数；阈值触发告警。
4. 可选「原始响应留存」：失败源或全量源响应 gzip 到 `/data/raw/<date>/<source>`，
   保留 3~7 天，支持离线重放与回归 fixture（注意 `/data` 配额）。
5. 增加 `ingest --only-source=<name>` 或 `--replay=<file>` 之类的运维入口。

#### F16【中】并发与幂等：调度器防同 job 重入，但存储层无事务保护

**现状**
- `deploy-jobs/scheduler.py:96-99` 对同名 job 检查存活 PID，避免 ingest 自我重叠；
  但仓库内没有 k8s CronJob manifest（`concurrencyPolicy` 未知），README 提到生产已
  迁秋坞；若平台侧采用默认 `Allow`，两班重叠时：
  - `upsert_news` 的「SELECT 查存在 → INSERT」之间没有事务/唯一冲突兜底
    （`pool.py:192-214`），并发进程可能触发 `UNIQUE(kind,url)` 的
    `IntegrityError`，而该异常无人捕获（F14）。
  - `upsert_news` 只在循环末尾 `commit()` 一次（`:216`），单进程内是原子的；
    `upsert_trending` 同样（`:243`）。
- 重跑幂等本身是好的：同 URL 二次抓取只刷新（`tests/test_pool.py:82-85`）；
  daily 有 `is_daily_posted` 防重发（`main.py:461-463`），池消费标记只在发布成功后
  （`main.py:552-553`）。
- 刷新分支不更新 `source/source_weight`（`pool.py:195-199`）：同一文章先由低权重源
  入池后，后到的高权重源不会提升其权重。

**建议**：`INSERT` 改为 `INSERT ... ON CONFLICT(kind,url) DO UPDATE`，消除
check-then-insert 竞态；明确部署侧 `concurrencyPolicy: Forbid`；刷新时按权重保留
更高权重来源（或至少记录多源）。

---

## 5. 改进建议（按风险/成本排序）

### P0（低风险、低成本，建议本迭代内做）

| # | 动作 | 对应发现 | 预估成本 |
|---|---|---|---|
| 1 | `fetch_all` 按启用源惰性 import，单模块 import 包 try；`run_ingest` 外层兜底并写 run_log | F13/F14 | S |
| 2 | `upsert_news` 刷新分支处理 `expired` 状态（复活或显式计数）；返回结构加 `revived` | F9 | S |
| 3 | 薄池补齐先过 `dedupe_news(..., _dedup_history(...))` | F10 | S |
| 4 | run_log 保留 `detail` 关键字段（mode/fallback/malformed/冷却） | F5/F15 | S |
| 5 | 429 加入退避重试；冷却改为「连续 N 次」并记 run_log | F2 | S |

### P1（中等成本，需设计/测试）

| # | 动作 | 对应发现 | 预估成本 |
|---|---|---|---|
| 6 | `normalize_url` 补 www/默认端口/scheme/index.html/百分号解码；相对 URL 拒绝或 urljoin | F8/F4 | M |
| 7 | `fetch_all` 全链路 deadline + `(connect, read)` 超时 + 响应体上限 | F1 | M |
| 8 | GitHub fallback 增加 `source_mode` 并透传到 run_log/渲染；星榜对降级数据标注 | F5 | M |
| 9 | 掘金改走统一 `http_post`（重试/限速） | F3 | M |
| 10 | `latest_trending` 改按 ingest 批次标识取数 | F12 | M |
| 11 | 镜像 `short_text` 触发 + None 计数；`unusable` 判定收敛 | F7 | S/M |

### P2（中长期，依运维需求）

| # | 动作 | 对应发现 | 预估成本 |
|---|---|---|---|
| 12 | 来源健康度指标（连续失败、条数/中位数比、0 条但 200），接入告警 | F15 | M/L |
| 13 | 原始响应留存 `/data/raw` + 重放入口 | F15 | L |
| 14 | 标题规范化（去站点后缀/标点）+ 多源重复按权重保留 | F11 | M |
| 15 | `INSERT ... ON CONFLICT` 消除竞态 + 部署侧 `Forbid` | F16 | S/M |
| 16 | XML 解析加大小/实体限制（`defusedxml` 或自定义 resolver） | F4 附注 | S |

> 安全附注：`feeds.parse_feed` 使用标准库 `xml.etree.ElementTree`
> （`tech-digest/app/sources/feeds.py:114-115`）解析外部 XML。Python 文档明确
> 提示该模块易受实体膨胀类攻击；当前源均为可信媒体，风险低，但若未来允许
> 用户配置任意 feed，应改用 `defusedxml` 并限制响应大小。

---

## 6. 自测与验证记录

### 6.1 回归

```bash
cd tech-digest
python3 -m unittest discover -s tests -t .
# Ran 818 tests in 13.099s — OK
```

依赖说明：沙箱初始环境缺少 `requests`/`beautifulsoup4`，已通过
`pip install --break-system-packages -i https://mirrors.aliyun.com/pypi/simple
requests beautifulsoup4` 安装后完成回归；**未改动仓库依赖文件**。

定向覆盖：`tests.test_dedup`、`tests.test_sources`、`tests.test_feeds`、
`tests.test_ingest` 全部通过；全量 discover 同时包含 `tests.test_pool` 等
去重/池化契约测试。

### 6.2 定点探针（临时目录，不写仓库）

**URL 规范化变体**

```
False | https://www.example.com/a | https://example.com/a
False | http://example.com/a      | https://example.com/a
False | https://example.com/a/index.html | https://example.com/a
True  | ?b=2&a=1                  | ?a=1&b=2
False | https://example.com/a%20b | https://example.com/a b
False | https://example.com:443/a | https://example.com/a
relative: ':///foo/bar'
```

**expired 行刷新**

```
res= {'inserted': 0, 'refreshed': 1, 'dup_dropped': 0}
status= expired  first_seen= 2026-11-15  last_seen= 2026-11-23T05:47:29
candidates: []
```

**latest_trending 两班混窗**

```
latest_trending: ['a/old', 'b/new']   # 窗口内多批 upsert 的条目同时返回
```

**单源 import 失败**

```
fetch_all raised: ImportError boom: broken deploy
```

**RSS 结构变更**

```
items: [('T2', 'https://x.com/2')]    # 只有 guid 的 T1 被静默丢弃
relative: [('T3', '/relative/3')]     # 相对链接原样入池
```

---

## 7. 范围外与假设

- 本次聚焦「源 → 池」摄取链路；LLM 挑选/研究/成稿/发布链路只在涉及取材与去重时
  引用，未做完整审计。
- `ops_report/`、`manual_server.py`、`post-curator` 等不在本单范围。
- 生产调度以 `deploy-jobs/scheduler.py`（当前仓库可见形态）为准；若线上为 k8s
  CronJob，并发相关结论（F16）需结合平台 `concurrencyPolicy` 复核。
- 未做真实外网抓取（沙箱无稳定公网），所有「结构变更」结论基于解析器代码与本地
  fixture/探针，未在生产端点复现。
