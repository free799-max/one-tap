"""OneTap 资源网关技能包。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

GENERATED_AT = "__GENERATED_AT__"

_SKILL = """---
name: onetap
description: 通过 OneTap MCP 管理影视、漫画、音乐、小说资源：查询、订阅、下载、整理、通知、替换。用户提到找片、找资源、订阅、下载进度、洗版、入库时使用。
---

# OneTap 资源助手

## 工具入口
先调用 `onetap_capabilities` 看哪些资源域可用，再按需要选择工具。同一个域有两套方案（影视 local/cloud、漫画 online/local）时，先问用户用哪一套，并把同一个 `instance_id` 贯穿本次所有调用：

- `onetap_search`：搜索资源，返回候选，每项都有 `item_id`。**不要替用户挑**。音乐默认按音质（码率）降序，结果带 `bitrate`、`size`。影视网盘方案（`cloud`）的搜索走 TMDB，只读、不会开始转存，用户选中后再订阅。
- `onetap_detail`：读取单个条目详情。
- `onetap_subscriptions`：列出订阅，每项都有 `subscription_id`。
- `onetap_subscribe`：添加订阅（音乐域是加入下载队列；漫画本地变体传 name/category 即可自动匹配源；影视网盘变体先 `onetap_search` 让用户选，再用返回的 `tmdb_id` 订阅）。
- `onetap_unsubscribe`：删除订阅（破坏性）。
- `onetap_downloads`：查询下载或缓存进度，每项都有 `task_id`。
- `onetap_download`：start / stop / delete 下载任务；delete 需要 `confirm=true`。
- `onetap_replace`：替换下载不正确的资源，需要 `confirm=true`。
- `onetap_present`：把候选按图文卡片推到通知渠道（飞书带封面，其他渠道是文本列表）。
- `onetap_notifications`：读取通知方式（`chat` 对话内回复 / `push` OneTap 推送）与已启用渠道。
- `onetap_set_notify_mode`：保存用户选定的通知方式，必须先问过用户。
- `onetap_capabilities`：查看可用实例与资源域；具体参数以 `tools/list` 的 schema 为准。

`domain` 取 `video`（电影、电视剧、动漫）、`comic`（漫画）、`music`（音乐）、`novel`（小说）。
各域的 `item_id`、`options`、`subscription_id`、`task_id` 含义见 `references/domains.md`。

## 安装 / 更新引导（先做这一步）
每次拿到新版本技能，在开始任何资源操作之前先确认「通知谁来发」：

1. 调 `onetap_notifications`。
2. `needs_onboarding=false`：沿用返回的 `mode`，不要重复询问。
3. `needs_onboarding=true`：用一句话问用户二选一，不要展开成长篇说明：
   - **对话内回复**：候选和进度都由我在对话里说，OneTap 不再外发。
   - **OneTap 推送**：候选发成带封面的卡片，订阅、下载完成、整理入库、失败等自动推到飞书等渠道。
   如果 `channels` 为空，先提醒「还没配通知渠道，选推送前要在 OneTap 管理端加一个」，不要替用户做决定。
4. 用户选完调 `onetap_set_notify_mode`：对话内回复 → `chat`，OneTap 推送 → `push`。然后只说一句「已按对话回复 / OneTap 推送处理」，不要解释实现。
5. 用户说「随便 / 你定」：传 `chat`（更安静），并说明随时可以改。用户中途想换方式时，同样先读再写，不要自己假设当前模式。

## 两种通知方式的行为差异
- 搜索结果：两种模式都要在对话里按列表规范回复；`push` 额外自动调一次 `onetap_present` 把同一批候选推成卡片，`chat` 不自动推，只有用户明确要求时才推。
- 订阅、替换成功：两种模式都在对话里确认一次，不依赖推送。
- 下载完成、整理入库、新集新章、失败：`push` 由 OneTap 推送，Agent 不重复播报、不轮询；`chat` 不会有任何推送，用户问进度时用 `onetap_downloads` 查一次并汇报（含失败原因），仍然不要主动轮询。
- 通知渠道为空：`chat` 不受影响；`push` 下事件只进站内收件箱，要提醒用户补渠道。

## 搜索结果展示（必须遵守）
- 所有查询结果统一规则：`onetap_search`、`onetap_subscriptions`、`onetap_downloads` 一律用编号列表，**禁止 Markdown 表格**（手机/窄屏下表格会被截断）；`item_id`、`subscription_id`、`task_id`、bookUrl 等内部 ID 一律不展示，用户只需要序号和可读名称。
- 搜索成功后用「编号 + 多行字段」的紧凑列表展示当前页全部候选。不得自行按“完全匹配 / 相关”等分组、筛选、遗漏候选或改变工具返回顺序。
- 序号从 1 开始连续编号，每条候选有且仅有一个序号。用户回复“第 N 个”时，使用该条的 `item_id` 继续操作，不要把序号当作 `item_id`。
- 每条候选最多两行（video 例外：用「标签： 值」逐行展开，见下方字段顺序）：第一行是 `序号. **标题** — 作者/歌手`（标题加粗，序号后用半角句点加空格，破折号前保留一个空格）；第二行以两个空格缩进开始，放其余字段，用「 · 」（空格-间隔号-空格）分隔，空值字段直接省略。不要杜撰缺失字段，不要在列表前复述工具调用、实例信息或排序过程。
- 单位一律紧凑书写：码率 `2896kbps`（不加空格），大小 `75.6MB`（不加空格、统一大写 MB/GB），时长 `03:39`。第二行不加结尾标点。
- `item_id` 只在内部用于详情、订阅等后续调用，**所有域都不要展示给用户**（video 取 `media_id`，music 形如 `kuwo:228908`，novel 是长 bookUrl），用户只需要序号。
- 各域字段顺序（第二行）：
  - music：`时长 · 码率 · 大小 · 专辑`（专辑与标题同名时省略，专辑用《》括起），时长秒数格式化为 `mm:ss`，码率和大小用工具原值。
  - video：第一行 `**标题** — 年份`；下面每行一个字段，格式 `**标签：** 值`（标签加粗，冒号后用半角空格），顺序为 `类型`、`评分`、`来源`，评分取 `vote`，来源取 `media_source`，空值省略；不要再把字段用「 · 」合并成一行。
  - comic：`类型 · 状态 · 已入库`；`domains.comic.variants` 多于一个时最后一项改为 `书库：在线/本地`。已入库取 `in_library`。
  - novel：`书源 · 章节数`，书源取 `origin_name`（为空时取 `origin`）。
- video 排版示例：
  1. **好东西** — 2024
     **类型：** 电影
     **评分：** 7.6 分
     **来源：** themoviedb
- 排版示例（music，照此对齐字段位置）：
  1. **花开了就好** — 姚佳顺子
     03:45 · 320kbps · 8.2MB · 《花开了就好》
- 列表后只简短提示“回复序号选择一首/一部”，不要另起候选列表。没有结果时直接说明未找到，不输出空列表。
- `onetap_subscriptions`：第一行「序号. **名称** — 年份或作者」；第二行「状态 · 方案/书源 · 集数或章节进度」，按工具返回的字段填写，空值省略。
- `onetap_downloads`：第一行「序号. **名称**」；第二行「状态 · 进度 · 大小或速度（工具返回才显示）」。
- 默认纯文本列表，不要输出封面图片链接：飞书、微信等客户端不会渲染外链图片，贴出来只是噪音。用户要图时走下面的推送技能，不要把 `poster` / `cover` 贴进文字。

## 推送候选（图文卡片）
`push` 模式下每次搜索后自动推一次；`chat` 模式下只在用户说「推给我 / 发飞书 / 要带图的列表」时推。用 `onetap_present` 把这一批候选推成一张卡片：

- `domain` 与 `keyword` 用这次搜索的值；`items` **原样**传 `onetap_search` 返回的 `items`（按原顺序，最多 20 条），不要自己改写字段。
- 不要传 `title` / `note`，除非用户指定了标题或提示语；渠道由 OneTap 自动选择已启用的，默认不用传 `channel_names`。
- 只推一次。推送成功后在对话里仍按上面的列表规范回复候选，并简短说明「已推送到飞书」；推送失败就说明渠道错误，不要重试第二次。
- 不要用它代替正常回复，也不要推订阅列表、下载进度、生命周期事件。

## 流程
查询 → 用户挑选 → 订阅或下载 → 立即确认已提交 → 结束本轮操作。

1. 搜索后严格按上面的列表规范展示候选，再等待用户按序号挑选。
2. 用户确认后订阅；调用成功后立即回报成功、重复与否，并说明后续会在后台处理。漫画要先按 `domains.comic.variants` 判断方案：在线只说已加入书库，本地才说下载和整理会在后台进行。
3. 不要调用 `onetap_downloads` 轮询，不要等待下载、整理或入库。只有用户主动询问进度时才查询一次；失败时报告工具名与原始错误，重试一次仍失败就停。
4. 需要替换时先 `onetap_subscriptions` 找到对应条目，确认后调用 `onetap_replace`。
5. 关键节点：订阅成功、替换成功在对话里确认；下载完成、整理入库、失败在 `push` 模式下由 OneTap 独立推送，不要重复发送，`chat` 模式下等用户问进度时查一次再汇报。
6. 收到「怎么设置通知」一类问题时，走上面的安装 / 更新引导，不要自己发明第三种方式。

## 纪律
- 订阅、替换或启动下载成功后立即结束本轮操作；不要 sleep、轮询或持续调用工具等待状态变化。
- 写操作前复述要改什么；删除、替换必须用户明确同意。
- 只调用本清单和 `tools/list` 里的工具，不臆造参数，不绕过 MCP 直接发 HTTP。
- 某个域 `available: false` 就如实报告原因，不要拿其它域或其它实例顶替。
- 不在回复里回显密钥、Authorization 或 Cookie。
"""

_DOMAINS = """# 资源域参数

所有工具都接受 `domain`、`instance_id`。`instance_id` 不填时用第一个可用实例。

## video（电影、电视剧、动漫）
影视有两套方案，先用 `onetap_capabilities` 看 `domains.video.variants`：
- `local`（MoviePilot + qBittorrent）：搜索候选 → 订阅 → 本地下载 → 刮削整理入库，播放用 Jellyfin / Emby / 飞牛影视。
- `cloud`（巡影 Mediary Scout）：不走本地下载，订阅即转存到用户网盘，巡影后台回读校验并持续追更，播放走网盘或同组播放器。
- 两套同时可用（`requires_instance_id=true`）时，先问用户「本地下载还是转存网盘」，再把同一个 `instance_id` 贯穿本次所有调用；不要混用两边的 `item_id` / `subscription_id`。

### local（MoviePilot）
- `item_id`：搜索结果的 `media_id`。
- `onetap_detail`：`options` 传 `media_source` 与 `type_name`（都来自同一条搜索结果）。
- `onetap_subscribe`：`options` 需要 `name`、`type`（电影/电视剧），可选 `year`、`media_source`、`media_id`、`season`、`resolution`、`quality`。
- `subscription_id`：订阅 ID（整数）。`task_id`：下载器任务 ID。
- 影视下载耗时较长：订阅或替换提交成功后立即回复并结束本轮操作，严禁等待、sleep 或轮询进度。

### cloud（巡影）
- `onetap_search`：只读 TMDB 搜索，返回候选（标题、年份、类型、简介、`item_id`/`tmdb_id`），不会开始转存；先把候选项列给用户选，不要替用户拍板。
- `onetap_subscribe`：`options` 传 `name`（片名），可选 `type`（电影/电视剧）、`year`、`season`、`storage_id`、`tmdb_id`。带 `tmdb_id` 时巡影直接用该条目入队；返回 `status=ambiguous` 时把 `candidates`（标题、年份、类型）列给用户选，用户选完用 `options.tmdb_id` 重发。
- 返回 `requested` / `already_tracked` 等状态即表示已入队，立即回复「已提交到网盘获取队列」，不要等待、sleep 或轮询。
- `onetap_subscriptions` 读巡影媒体库（已获取集数、缺集、所属网盘）；`onetap_downloads` 读正在获取的任务与最近完成记录。
- 转存、回读校验、追更由巡影在后台完成；OneTap 会同步巡影的任务通知，按与其他域相同的方式推送「已整理入库 / 有更新 / 已全部就绪 / 处理失败 / 已替换」，Agent 不要重复播报，也不要轮询。
- 不支持退订、替换、单任务暂停/删除：如实说明，并让用户去巡影「媒体库」页面处理。

## comic（漫画）
漫画有两套方案，先用 `onetap_capabilities` 看 `domains.comic.variants`：
- `online`（Suwayomi）：订阅即加入在线书库，阅读用 Tsumiru / Mihon；没有下载→整理入库环节。
- `local`（Comic Auto + Komga/Kavita）：订阅后自动下载章节并刮削入库，阅读用 Komga / Kavita。
- 两套同时可用（`requires_instance_id=true`）时，先问用户用哪一套书库，再把 `instance_id` 贯穿本次所有调用；不要混用两边的 `subscription_id`。

### online（Suwayomi）
- `item_id`：漫画 ID。搜索需要在线图源；可以传 `source_id` 指定图源。
- `onetap_subscribe`：传漫画 `item_id` 即可，会加入书库并拉取章节，不要触发下载。
- `onetap_downloads`：返回已缓存章节数；`onetap_download start` 只用于离线缓存，`stop` 停止下载器。
- 订阅后直接回复「已加入在线书库，可用 Tsumiru / Mihon 阅读」，不要等下载或整理。

### local（Comic Auto）
- 搜索结果每个条目带 `crawling_type`、`station_url`、`comic_href`、`category`；授权库条目带 `license_subscription_id`、`license_comic_id`。
- `onetap_subscribe`：`options` 传 `name`、`category` 即可，Comic Auto 会自己搜索全部站点并匹配最优源；想固定用搜索结果里的某个源，再把 `crawling_type`、`station_url`、`comic_href` 原样放进 `options`。搜索结果里的 `author`、`status`、`tags` 也一并带上，通知卡片会展示作者、状态、话数、更新进度与标签。订阅会触发「搜索 → 匹配 → 下载 → 刮削入库」，返回 `subscription_id` 与 `library`（Komga / Kavita）。
- `onetap_detail`：`options` 传同一份搜索结果字段，返回章节总数。
- `onetap_downloads`：返回已下载/总章节数；`onetap_download start` 触发补下载，不支持 `stop` / `delete`。
- Comic Auto 自己的原生 MCP 工具也会通过 OneTap 暴露为 `comic_auto_<实例ID>_search_comics`、`list_subscriptions`、`create_subscription`、`process_subscription`、`delete_subscription`。只有用户明确要求用 Comic Auto 原生工具，或需要按名称直接建订阅时才用；统一生命周期（通知、进度、详情）仍走 `onetap_*`，同一部漫画不要两条路径重复订阅。
- 订阅后回复「已提交订阅，下载和入库在后台进行」，不要轮询；完成情况由 OneTap 推送。

- `subscription_id`：online 是漫画 ID，local 是订阅 ID；`task_id` 同理，两者都是字符串。

## music（音乐）
- 来源：music-dl。没有订阅概念：`onetap_subscribe` 等于加入下载队列，`onetap_subscriptions` 列出下载队列。
- `onetap_search` 默认 `sort=quality`，先读取完整候选的码率再降序返回；`sort=default` 可关闭音质排序。
- `item_id`：`<source>:<id>`（例如 `kuwo:228908`）。
- `onetap_detail`：`options` 传搜索结果里的 `extra`。
- `onetap_subscribe`：`options` 传 `id`、`source`、`name`、`artist`、`album`、`cover`、`extra`。
- `subscription_id` 与 `task_id` 都是下载队列任务 ID。

## novel（小说）
- 来源：Reader。订阅即加入书架，下载即服务器缓存章节。
- `item_id`：书籍的 `bookUrl`。
- `onetap_subscribe`：`options` 传搜索结果里的 `title`（或 `name`）、`author`、`origin`、`origin_name`、`cover`、`intro`、`total_chapters`，通知卡片会展示作者、书源与章节数。
- 首次订阅会按同书名、同作者比较最多 8 个候选书源，选择目录章节数最多的源。这个调用可能耗时数十秒，只调用一次，不要额外轮询。结果中的 `source_selection.selected` 是最终源，向用户简短说明最终书源和章节数；`source_selection.skipped` 为 `already_subscribed` 时说明书籍已在书架，不要重复订阅。
- `onetap_download`：只支持 `start`（补齐缺失章节缓存）。
- `subscription_id` 与 `task_id` 都是 `bookUrl`。

## 通知
异步通知怎么发由 `onetap_notifications` 的 `mode` 决定，不要自己假设：
- `push`：OneTap 外发订阅添加/替换、下载完成、整理入库、新集新章、失败到已启用渠道；你只做同步确认，不重复播报、不轮询。影视网盘方案（巡影）的入库、更新、失败、替换通知也由 OneTap 同步后外发，不要在对话里另说一遍。
- `chat`：OneTap 不外发。用户问进度时用 `onetap_downloads` 查一次并汇报（含失败原因），仍然不要主动轮询。
- 调用 `onetap_subscribe` / `onetap_replace` 时，把搜索结果里的 `poster`（或 `cover`）放进 `options`，推送才能带封面。
- 不要在对话里重复发送 `send_message` 一类的消息；用户主动要求的候选卡片走 `onetap_present`，同批候选只推一次。
- 同步动作（搜索、订阅、替换）的结果始终由你在对话里汇报。
"""

_REPLACE = """# 资源替换

用户说「下载的不对 / 画质不行 / 没字幕 / 音轨不对 / 缺章节」时：

1. 用 `onetap_subscriptions` 找到对应条目，确认 `subscription_id`。
2. 复述：替换哪一部、新的偏好、旧文件会不会被删除。
3. 用户确认后调用 `onetap_replace`（`confirm=true`）。
4. 各域行为：
   - video：本地（MoviePilot）开启洗版并立即搜索，旧文件在新版本入库前保留，新版本入库后才算完成；网盘（巡影）不支持替换，如实说明并让用户在巡影媒体库里重新获取。
   - comic：在线（Suwayomi）补齐未下载章节并重启下载器；本地（Comic Auto）重新提交处理任务，缺失章节重新下载并重新入库；两者都不删除已有文件。
   - music：取消旧任务后重新入队，需要 `options` 提供歌曲字段。
   - novel：重新提交章节缓存，已有缓存不会删除。
5. 替换请求提交成功后立即汇报已提交，并结束本轮操作；不要轮询或等待下载、整理、入库。
6. 只有用户主动询问进度时才用 `onetap_downloads` 查询一次；失败时报告原始错误，不要连续重试。后续状态由 OneTap 异步通知。
"""


def skill_files() -> list[dict[str, str]]:
    return [
        {"path": "onetap/SKILL.md", "content": _SKILL},
        {"path": "onetap/references/domains.md", "content": _DOMAINS},
        {"path": "onetap/references/replace.md", "content": _REPLACE},
    ]


def build_skills_bundle() -> dict[str, Any]:
    moment = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "name": "onetap",
        "version": "7",
        "generated_at": moment,
        "files": [
            {"path": item["path"], "content": item["content"].replace(GENERATED_AT, moment)}
            for item in skill_files()
        ],
    }


def build_skills_json() -> str:
    return json.dumps(build_skills_bundle(), ensure_ascii=False, indent=2)
