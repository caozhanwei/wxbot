# R2 记忆闭环实施计划（v1.5.1：跨聊天问答 + 群精华主题库）

设计依据：`docs/superpowers/specs/2026-10-23-wxbot-roadmap-design.md` 第 3 节（关键决策 1：
文件传输助手=主人对话台）与第 5 节。
基线：v1.5.0（261 测试）。全程 TDD。

## 步骤

1. **store 主题表 + 方法**（tests/test_themes.py store 部分）
   - 新表 `mined_themes(qa_id INTEGER PRIMARY KEY, theme TEXT, updated REAL)`
   - 方法：`set_theme(qa_id, theme)` / `themes_list(grp)`（主题名+条数，供挖掘卡片与聚类参考）
     / `mined_unthemed(grp, limit)`；`mined_pairs` LEFT JOIN 带出 `theme` 字段（加键不破坏旧断言）
2. **wxbot/recall.py 跨聊天问答**（tests/test_recall.py）
   - `extract_terms(bot, question)`：LLM 抽 {keywords, who, days}；失败/未配走朴素切词
     （2 字以上、去停用词，最多 6 个）
   - `search_history(bot, keywords, who=None, days=30)`：遍历 watch（群+联系人+被动私聊），
     各取 `driver.db.get_messages(uid, limit=800)`，过滤 文本/非xml/时间窗/关键词任一命中，
     可选说话人过滤；按命中词数+时间排序，返回前 60 行结构化（chat/display/sender_display/ts/content）
   - `answer(bot, question)`：检索行渲染成 `【群】昵称(MM-DD HH:mm): 片段` 上下文 →
     personas LLM 整合回答（**必须带出处：谁/哪群/大概时间**，口语化）；零命中直接回
     "没查到相关记录"（不调 LLM 硬凑）
3. **bot 接线：主人对话台**（tests/test_console.py）
   - `build_watch`：`app.console` 未显式关（默认开）→ 追加
     `{"user": "filehelper", "display": "文件传输助手", "kind": "console", "since": baseline}`
   - `run()` 分发 `_on_console_msgs`：
     - 只认 `is_self=True` 文本（filehelper 只有主人自己）；跳过 xml/非文本
     - **自发自收去重**：内容命中 `_last_sent_cache`（bot 的 recall 回复/日报推送也落这里）窗口内跳过
     - `#` 开头 → `_do_admin` 直通（回复也发 filehelper）
     - 其余 → `recall.answer`，`bot.send(filehelper, ans, "recall")`
     - 对话台回复=应答主人直接提问，**不过 gate**（同私聊回复定位）；dry_run 照旧兜底
4. **主题库聚类 + 本地命中**（tests/test_themes.py 其余）
   - `personas.assign_themes(existing_names, pairs)`：LLM JSON `{items:[{id,theme}]}`，
     主题名收敛（复用既有名或起新名，≤8 字）
   - `themes.assign(bot, grp, batch=20)`：取 unthemed → 既有主题名做参考 → 批量写回；
     mining 完成后自动跑（mining 线程收尾处）+ webui 挖掘卡片「整理主题」按钮（POST /themes/assign）
   - 群答疑本地先撞：`bot._local_qa_hit(w, question)`——该群 mined_qa 中问题与提问
     **共享 ≥2 个 2 字以上关键词**才命中（保守阈值），命中则以人设口语化转述 mined 答案作答
     （标注"群内沉淀"），未命中走原 KB 链路；`qa.local_first` 默认 true 可关
5. **webui**：挖掘卡片问答对显示主题列 + 主题筛选下拉 + 「整理主题」按钮
6. **收尾**：版本 1.5.1（version.py/product.wxs/tag 三处）、模板配置注释（app.console/recall 天数
   默认 30）、README 能力条目、全套测试（261 → 290+）、发版管线（CI→227 镜像→验证）

## 风险与对策

- filehelper since=0 会拉全量历史：console 条目 since 必走 baseline（build_watch 既有循环已保证）
- 主人自发自收回环：`_last_sent_cache` 内容级去重 + 只处理文本
- 本地命中误答：阈值 ≥2 共享关键词；`qa.local_first` 可按群关闭
- LLM 关键词抽取失败：朴素切词兜底，检索永不因 LLM 挂而不可用
