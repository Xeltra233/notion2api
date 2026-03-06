# codex-0305_afternoon 修改记录

## 背景
用户反馈前端只显示“联网搜索 + thinking”，最终正式回复不显示。

## 复现结论
- 用同一提示词多次真实上游请求可稳定复现：`content=0`，只有 `search` 与 `thinking`。
- 原始 NDJSON 中最终回复实际存在，主要以 `o:"x"` + `/s/<idx>/value/.../content` 连续增量下发。

## 根因
- `app/stream_parser.py` 中 `max_thinking_segment` 被同段位元数据 patch（如 `/finishedAt`、`/model`、`/tokens`）污染。
- 当最终正文与 thinking 落在同一段位时，旧逻辑在 `phase == THINKING` 仅允许 `patch_seg > max_thinking_segment` 才进入 `ANSWER`，导致正文被持续归类为 `thinking`。

## 修改内容

### 1) stream_parser 核心修复
文件：`app/stream_parser.py`

- 新增 `_is_value_content_path(path)`：
  - 仅将 `/value/.../content` 识别为真实流式文本路径。
- 修改 `_record_thinking_segment(seg, path)`：
  - 只在真实文本路径上更新 `max_thinking_segment`。
  - 避免元数据路径抬高 thinking 段位边界。
- 在 `phase == THINKING` + `patch_op == "x"` 分支补充“同段切换”规则：
  - 若 `patch_seg == max_thinking_segment` 且路径为正文路径，且该段未被显式标记为 `THINKING/TOOL`，允许切换到 `ANSWER` 并输出 `content`。

### 2) 调试文档补充
文件：`codex-note/codex-0305_thinking_debug.md`

- 追加“晚间补充修复”章节，记录：
  - 真实流复现形态；
  - 具体根因；
  - 修复逻辑；
  - 回放样本与真实请求验证数据。

## 验证

### 回放失败样本
- 样本：`codex-note/tmp_fail_rich_2.ndjson`
- 修复前：`{'search': 2, 'thinking': 89, 'content': 0}`
- 修复后：`{'search': 2, 'thinking': 2, 'content': 87}`

### 真实上游回归
同提示词连续 3 次请求均恢复 `content` 输出：
- `{'search': 1, 'thinking': 1, 'content': 74}`
- `{'search': 1, 'thinking': 1, 'content': 52}`
- `{'search': 1, 'thinking': 2, 'content': 62}`

## 结论
本次修复后，流式解析已可正确透传最终正式回复，不再出现“只有搜索和 thinking、正文缺失”的问题。
