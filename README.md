# 微信智能助手 PC 版（wxbot）

基于 [wechatauto-replica](vendor/wechatauto-replica)（本地库解密读消息 + UIA 写操作）的微信自动回复/群管理工具。适配微信 4.1.x（UIA 树需热激活，驱动会自动处理）。

> **免责声明**：本项目仅供个人学习研究 Windows UI 自动化与 RAG 应用技术。使用前请自行评估并遵守《微信个人帐号使用规范》等相关条款；因使用本工具产生的账号风控等一切后果由使用者自行承担。

## 快速开始

```
pip install -r requirements.txt
pip install -e vendor/wechatauto-replica   # 微信驱动库（Apache-2.0）
cp bot_config_template.yaml bot_config.yaml  # 填入你的 LLM/KB 网关地址与 key
python run_bot.py --check                # 自检：窗口/自身/LLM/KB 连通
python run_bot.py --list                 # 列出最近会话与群 id（用于填配置）
python run_bot.py --test-reply 文件传输助手 你好   # 全链路测试（请先用文件传输助手）
python run_bot.py                        # 启动（含 Web 状态页 http://127.0.0.1:8778）
python -m pytest tests/ -q               # 66 个用例
```

## 能力

- **私聊自动回复（白名单制）**：`bot_config.yaml` 的 `contacts` 里 `enabled: true` 的联系人才会 AI 回复；每个联系人可指定 `persona`（人设库自定义，语气/背景/表情习惯）
- **群答疑**：群内 `@机器人 + 问题` → 先结合群聊上下文**理解提问**（指代补全成独立问题再查库，"它多少钱"也能正中；闲聊/玩笑@不查库只轻接一句）→ 团队知识库（MCP `kb_ask`）RAG 回答；未正中但有引用来源 → 附参考回复并记入"待沉淀"（Web 页可见）；完全未命中 → 记录并私信管理员；知识库断线可按群回退人设 LLM **融入群讨论**作答（能引用群友昵称的观点，像顺着话头接话）
- **图片 OCR 理解**（v1.3.2）：群里的图片本地解密后经 OCR 服务识别成文字（如 `[图片:通知：明天停水]`）参与上下文理解与画像——问"这个通知说了啥"也能答；不配 `ocr` 段则图片仍是 `[图片]` 占位（语音识别暂缓，显示 `[语音]`）
- **群友画像**（v1.3.2，v1.3.3 增强）：按 群×成员 维护画像（身份线索/关注的事/语气态度/佐证事例，≤1000字，卡号证件等脱敏不记），答疑后后台自动更新（同一人 1 小时节流），回答时注入画像帮着洞察提问意图；Web 页"群友画像"卡片可**按群筛选、分页查看**、按群刷新；群问答库（挖掘）同样**按群筛选 + 分页**浏览
- **免@上下文回复**（v1.3.2）：群配置 `no_at_reply` 打开后，没人@机器人也会结合最近聊天判断——先把增量消息（脱敏后）沉淀进知识库，再由 LLM 判定"值得接话"（有人提问/求资源/讨论可答话题）才回复（回复 prompt 并入知识库检索参考；纯闲聊不吭声）；同群冷却默认 3 分钟可配，@提问不受冷却限制
- **群管理**：进群欢迎（模板）、违禁词监控 → 按人设生成劝告话术 → 达阈值私信管理员建议移出（**踢人需人工在微信操作**，避免误伤）
- **管理员命令**：私聊发 `#状态 #暂停 #恢复 #开 <名> #关 <名> #重载 #待审 #帮助`
- **Web 状态页**（127.0.0.1:8778）：联系人/群开关、运行统计、日志、待沉淀问题、在线编辑配置（热重载；网页保存会丢注释，介意请直接编辑文件）
- **配置热重载**：`bot_config.yaml` 改完即生效（LLM 模型/温度/超时、联系人、人设、群、违禁词阈值等）
- **最近会话选择器**（v1.3.0）：Web 页"最近会话"卡片列出微信里点开过的聊天（支持关键字过滤），一键"加为联系人/加为群"，**无需知道任何 id**；手填昵称反查不到时自动给模糊候选；另有 `/api/sessions?q=关键字` JSON 接口
- **首启向导**（v1.3.0）：新装机第一次打开 Web 页引导三步——确认微信已登录 → 填模型接口/Key → 从最近会话挑对象；模板默认 `dry_run: true`（演示模式，只记日志不发送），验证后再关
- **多账号隔离**（v1.3.0）：登录后配置/数据库自动落到 `%APPDATA%\wxbot\<你的wxid>\`；换一个微信号登录自动切换（老账号数据原样保留，新账号从模板干净起步），给不同的人/号用互不串号

## 配置要点（bot_config.yaml）

- `llm`：人设对话生成（provider/base_url/api_key/model/temperature/timeout 均可配）
- `kb`：群答疑知识库（MCP 网关，需自行部署或填写可用网关；未配置时私聊功能不受影响）
- `ocr`：图片识别服务（可选，paddle OCR MCP：url/api_key/timeout；不配则图片只显示 `[图片]`）
- `contacts`：`id`（微信号/wxid，不知道可只填 `display` 自动反查；更推荐直接从 Web 页"最近会话"点选）+ `persona` + `enabled`
- `groups`：`--list` 查 id 或 Web 页点选；`qa.enabled` 答疑开关；`no_at_reply` 免@接话（`no_at_context`/`no_at_cooldown_min` 可调，Web 页群卡片有开关）；`manage` 段配欢迎/违禁词/`warn_persona`/`warn_threshold`；`mention_names` 可加额外 @称呼（默认自动含自己的微信昵称）
- `app.dry_run: true` 只记日志不发送（**模板默认开**，新装机先在演示模式验证）
- `app.self_wxid` / `app.setup_done`：程序自动维护（多账号隔离标识/向导完成标志），勿手改

## 安全与风控

- 发送前**硬校验**目标会话（会话名不符绝不输入），粘贴后内容比对不符不回车，open_chat 偶发失败自动重试 3 次
- 回复带拟人迟延（2~5s 随机）、连发合并安静窗口（5s）、发送串行
- 锁屏会拦截一切模拟输入（读消息不受影响）——常开请禁自动锁屏
- 微信窗口须保持存在；建议小号专用，注意频控
- 聊天记录中的卡号/开户/证件号等会脱敏后再送 LLM

## 已知限制

- 踢人未做全自动（4.x 群设置 UI 自动化误踢风险高），走"人设劝告 + 阈值告警 + 人工移出"
- @提醒通知依赖文本匹配（群昵称与微信昵称不一致时需在 `mention_names` 补充）
- 群内 @某人 仅文本显示，不含微信官方 @ 通知效果
- 语音消息暂不识别（上下文里显示 `[语音]`）；图片 OCR 需自备 paddle OCR MCP 服务，图片为"已收到并在本地有缓存"时才可解密（很老的图/未点开过的可能拿不到）
- `kb` 网关上传依赖其 MCP 工具（默认 `kb_upload` → `kb_add_document` → `kb_add_file` 顺序试探，可用 `kb.upload_tool` 指定）；不可用时免@沉淀静默跳过（本地水位不前移，待下次重试），待沉淀问题仍可走 Web 页复制 → 人工导入

## 目录

```
run_bot.py        入口（--list/--check/--test-reply/运行）
bot_config.yaml   全部配置（keys 勿外传；分发模板见 bot_config_template.yaml——不含任何真实密钥）
wxbot/            config(热重载) driver(replica封装) personas(人设引擎)
                  group_logic(纯逻辑) store(sqlite) bot(编排) webui(Flask状态页) mining(群历史挖掘)
                  paths(多账号隔离：%APPDATA%\wxbot\<wxid>\)
tests/            pytest 用例（含 webui 配置页 / 多账号隔离）
vendor/           wechatauto-replica 源码（editable 安装，勿随意升级）
build/            MSI 打包（PyInstaller → gen_files.py → WiX）；redist/ 内置 VC++ MFC 运行库 DLL，
                  随包装进 _internal，用户机器免装 VC 运行库
```

## 构建 MSI（Windows）

```
pip install pyinstaller && pip install -e vendor/wechatauto-replica
pyinstaller wxbot.spec --noconfirm
cd build && python gen_files.py ..\dist\wxbot
wix build -arch x64 product.wxs files.wxs -o wxbot.msi
```

需要 [WiX v6](https://wixtoolset.org/)（`dotnet tool install --global wix`）与 .NET 8 SDK。

**CI**：GitHub Actions 会在每次 push 时自动跑测试并构建 MSI（Actions 页下载产物）；打 `v*` 标签自动发布 Release。
