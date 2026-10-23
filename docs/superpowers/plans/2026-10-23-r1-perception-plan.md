# R1 感知闭环实施计划（v1.5.0：群日报 + 错过补报 + 驾驶舱）

设计依据：`docs/superpowers/specs/2026-10-23-wxbot-roadmap-design.md` 第 4 节。
基线：v1.4.13（230 测试）。全程 TDD：每步先写测试再实现。

## 步骤

1. **store 扩表 + 方法**（tests/test_digest.py store 部分）
   - 新表：`digest_log(day, grp, last_seq, created, PK(day,grp))`、`missed_cache(id, ts, src, chat, sender, sender_display, content, urgency, digested)`、`daily_runs(job, day, PK(job,day))`、`daily_stats(day, grp, msg_count, active_members, top_words, pending_count, PK(day,grp))`
   - 方法：add_missed / missed_unconsumed / mark_missed_consumed；digest_done / digest_done_day；mark_daily_run / daily_ran；upsert_daily_stats / daily_stats_list
2. **gate.py 动作闸门**（tests/test_gate.py）
   - `Gate(bot)`：读 `app.gate {enabled, hourly_max=20, per_chat_daily_max=30, window=[8,22]}`
   - `allow(key, chat, text, urgent=False, now=None) -> (ok, reason)`：急停(state.gate_stop) > 时段窗(urgent 豁免) > 全局每小时 > 每会话每日；放行即计数 + `store.log("gate", chat, key, text[:80])` 审计
   - `stop()/resume()`；`#急停 主动` / `#恢复主动`
3. **scheduler.py 通用日任务**（tests/test_scheduler.py）
   - `due(job, now, ran_today)` 纯函数：槽点已过 + 今天没跑过 + 当天 23:59 宽限内（错过补跑，跨天不补）
   - `Scheduler(bot, jobs)` 守护线程 60s tick：到期的后台线程执行、异常隔离告警；用 store.daily_runs 幂等
4. **digest.py 日报 + 补报**（tests/test_digest.py）
   - `on_private_msg(bot, w, m)`：非白名单私聊被动监听入库 missed_cache；紧急规则（急/紧急/尽快/马上）→ `urgent_notify` 立即推 push 目标（过 gate urgent=True）
   - `record_at_missed(bot, w, m)`：群内 @我未答（被顶替/过气/暂停）入库
   - `run_digest(bot)`：按 `app.daily_digest{enabled,hour=21,push_to=文件传输助手}` + 群级 `digest.enabled`；mining._normalize 近24h → personas.summarize_day 生成热点与精选；未解清单读 pending_issues；missed 未消化条目汇总成"找你的事"（LLM 失败→重试1次→机械版`[简版]` 条数+高频词）；按群分条过 gate 推送；写 digest_log/daily_stats（top_words 本地词频，无 LLM）、mark_missed_consumed、daily_runs
   - `report_missed_now(bot)`：`#补报` 即时汇总未消化 missed
5. **bot.py 接线**
   - `build_watch`：`app.missed_private != false` 时把最近会话里"非群/非已监控/非自己/非文件传输助手"的私聊加为 `contact_passive`（只记录不回复）；run() 分发
   - `_on_group_msgs`：被顶替/过期/暂停的 @ → `digest.record_at_missed`
   - run()：起 Scheduler（digest job）；`_do_admin` 增 `#补报 #急停 主动 #恢复主动`，帮助文本更新
6. **webui.py 驾驶舱 + 配置**（tests/test_dashboard.py）
   - 「驾驶舱」卡片：每启用群一行（今日统计来自 daily_stats 最近一条 + 未解/待沉淀实时数 + digest 开关状态）；「动作审计」简版（kind=gate 最近日志）
   - 群卡片加 digest 复选框；全局 daily_digest（enabled/hour/push_to）表单；保存 carry-over 防丢键
7. **收尾**：版本 1.5.0、模板配置注释、README、全套测试（230 → 300 目标）、发版（CI → 227 镜像 → 验证）

## 风险与对策

- 被动监听私聊 = 新增 DB 轮询面：只读最近 60 会话、复用 poll 增量，成本极低
- 日报 LLM 多群多调用：逐群串行 + `_LLM_INTERVAL` 节流；单群失败不拖垮整份
- push_to 发送失败：gate 记录 + notify_admins 告警；次日日报照常
