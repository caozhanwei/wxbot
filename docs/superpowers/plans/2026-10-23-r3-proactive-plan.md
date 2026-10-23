# R3 主动闭环实施计划（v1.6.0：约定提醒 + 主动关怀 + 未解问题闭环 + 群秩序）

设计依据：`docs/superpowers/specs/2026-10-23-wxbot-roadmap-design.md` 第 6 节。
基线：v1.5.1（281 测试）。全程 TDD。

## 步骤

1. **store 扩表**（tests/test_reminders.py / test_care.py store 部分）
   - `reminders(id, ts, src, grp, grp_name, who_name, what, due, status DEFAULT 'pending',
     fired, remind_count DEFAULT 0)`；方法 `add_reminder / due_reminders(now,limit) /
     list_reminders(status) / mark_reminder(id, status, fired=None)`
   - `remind_seq(chat PK, seq, ts)` 私聊约定抽取水位
   - `care_log(id, ts, wxid, name, text)` + `care_count_since(wxid, days)`
   - `mark_qa_done(id)`（qa_pending 已有 status 列，直接用）
2. **wxbot/reminders.py 约定提醒**（tests/test_reminders.py）
   - `extract_chat(bot, w, rows)`：水位后的私聊消息 → personas.extract_reminders
     （LLM 输出 {who,what,date:"YYYY-MM-DD",time:"HH:MM"}，prompt 带基准日期+星期，
     模块换算绝对 ts；失败/未配返回 []；今天没给日期的约定忽略）
   - `maybe_extract(bot)`：bot 主循环节流（默认 30 分钟一轮，state 水位）扫启用联系人
   - `fire_due(bot)`：due_reminders 到期 → grp 有值=群内约定（gate 过闸后发群里提醒相关人），
     无 grp=私事推文件传输助手（gate 过闸）；发后 mark fired；同一约定只发一次
   - 管理命令：`#提醒` 列 pending（id/谁/何事/到期时间），`#销项 N` 置 done
3. **wxbot/care.py 主动关怀**（tests/test_care.py）
   - `app.care {enabled:false, dry_run:true, hour:10, days_min:7, max_per_day:3}`
   - `run_care(bot)`（DailyJob）：启用联系人里挑"距上次聊天 ≥ days_min 天"者 →
     护栏全过才发：care_count_since(wxid,7d)==0（每人每周≤1）、当日已发数 < max_per_day、
     gate.allow("care", display)；personas.care_greeting（记忆+天数生成问候）；
     dry_run=true 只落 kind=care_dry 日志不发送不记 care_log（不占周配额）
4. **wxbot/followup.py 未解问题闭环**（tests/test_followup.py）
   - 数据源=qa_pending（#待审 已能看）：`催办(bot, id)` → 找到所属群（display→cfg gid）→
     gate 过闸发群里"有人跟进吗"；`resolve(bot, id, answer)` → mark_qa_done +
     add_mined(gid, [(question, answer)]) + themes.assign_async（归档直喂 R2 主题库）
   - 命令：`#催办 N` / `#已解 N 答案是…`
5. **bot.py 群秩序自动化**（tests/test_order.py）
   - 刷屏检测：内存滑窗 (grp,sender)→ts 队列，60s 内 ≥ order.flood_max（默认 10）条 →
     notify_admins 告警（每成员每小时至多一次，不群里喊话）
   - 退群预警：系统消息含"移出了群聊/退出了群聊" → notify_admins
   - 新人引导指路主题库：_welcome 附加该群 themes_list 前 3 个主题"群里有沉淀的常见话题…@我问就行"
   - 定时开闭群：**本版不做**（需 UIA 改群设置，风险高价值低；非群主群本就只能提醒），
     记入 README 待办
6. **personas.py**：`extract_reminders(dialog_lines, base_date)` / `care_greeting(who, memory, days)`
7. **bot 接线**：run() 挂 DailyJob("care", hour)；主循环 `_maybe_remind_extract`（30min 节流）
   + `_maybe_fire_reminders`（60s 节流）；_do_admin 加 #提醒/#销项/#催办/#已解（帮助文案更新）
8. **webui**：「约定提醒」卡片（pending 列表+到期时间+销项按钮，POST /reminders/done）
9. **收尾**：版本 1.6.0 三处、模板配置注释（app.care/reminders）、README 能力条目、
   全套测试（281 → 310+）、发版管线（CI→227 镜像→外网验证）

## 风险与对策

- LLM 时间解析错基准日：prompt 带基准日期+星期几；解析失败丢弃该条不误报
- 提醒误扰：全部过 gate（时段窗 8~22 兜底）；同一约定只发一次
- 关怀骚扰：默认 dry_run 演练 + 每人每周 1 次 + 每日全局上限 3 + 手动开
- 刷屏误报：阈值宽松（10 条/60s）+ 每成员每小时只告警一次
- 催办打扰群：命令驱动（不自动发），过闸
