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
python -m pytest tests/ -q               # 46 个用例
```

## 能力

- **私聊自动回复（白名单制）**：`bot_config.yaml` 的 `contacts` 里 `enabled: true` 的联系人才会 AI 回复；每个联系人可指定 `persona`（人设库自定义，语气/背景/表情习惯）
- **群答疑**：群内 `@机器人 + 问题` → 团队知识库（MCP `kb_ask`）RAG 回答；未正中但有引用来源 → 附参考回复并记入"待沉淀"（Web 页可见）；完全未命中 → 记录并私信管理员
- **群管理**：进群欢迎（模板）、违禁词监控 → 按人设生成劝告话术 → 达阈值私信管理员建议移出（**踢人需人工在微信操作**，避免误伤）
- **管理员命令**：私聊发 `#状态 #暂停 #恢复 #开 <名> #关 <名> #重载 #待审 #帮助`
- **Web 状态页**（127.0.0.1:8778）：联系人/群开关、运行统计、日志、待沉淀问题、在线编辑配置（热重载；网页保存会丢注释，介意请直接编辑文件）
- **配置热重载**：`bot_config.yaml` 改完即生效（LLM 模型/温度/超时、联系人、人设、群、违禁词阈值等）

## 配置要点（bot_config.yaml）

- `llm`：人设对话生成（provider/base_url/api_key/model/temperature/timeout 均可配）
- `kb`：群答疑知识库（MCP 网关，需自行部署或填写可用网关；未配置时私聊功能不受影响）
- `contacts`：`id`（微信号/wxid，不知道可只填 `display` 自动反查）+ `persona` + `enabled`
- `groups`：`--list` 查 id 后填写；`qa.enabled` 答疑开关；`manage` 段配欢迎/违禁词/`warn_persona`/`warn_threshold`；`mention_names` 可加额外 @称呼（默认自动含自己的微信昵称）
- `app.dry_run: true` 只记日志不发送

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
- `kb` 网关无上传 API，待沉淀问题走 Web 页复制 → 人工导入知识库

## 目录

```
run_bot.py        入口（--list/--check/--test-reply/运行）
bot_config.yaml   全部配置（keys 勿外传；模板见 bot_config_template.yaml）
wxbot/            config(热重载) driver(replica封装) personas(人设引擎)
                  group_logic(纯逻辑) store(sqlite) bot(编排) webui(Flask状态页) mining(群历史挖掘)
tests/            46 个用例
vendor/           wechatauto-replica 源码（editable 安装，勿随意升级）
build/            MSI 打包（PyInstaller → gen_files.py → WiX）
```

## 构建 MSI（Windows）

```
pip install pyinstaller && pip install -e vendor/wechatauto-replica
pyinstaller wxbot.spec --noconfirm
cd build && python gen_files.py ..\dist\wxbot
wix build -arch x64 "-define SourceDir=%CD%\..\dist\wxbot" product.wxs files.wxs -o wxbot.msi
```

需要 [WiX v6](https://wixtoolset.org/)（`dotnet tool install --global wix`）与 .NET 8 SDK。

**CI**：GitHub Actions 会在每次 push 时自动跑测试并构建 MSI（Actions 页下载产物）；打 `v*` 标签自动发布 Release。
