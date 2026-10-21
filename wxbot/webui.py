# -*- coding: utf-8 -*-
"""Web 控制台（桌面窗口承载）：可视化配置（联系人/人设/群/模型/知识库）、状态、日志。"""
import html
import logging
import threading
import time

import yaml
from flask import Flask, request, redirect, render_template_string

from . import mining

log = logging.getLogger("wxbot.web")

_NAV = [("状态", "#top"), ("最近会话", "#recent"), ("联系人", "#contacts"),
        ("人设", "#personas"), ("群", "#groups"), ("模型与节奏", "#llm"), ("知识库", "#kb"),
        ("日志", "#logs"), ("待沉淀", "#pending"), ("挖掘", "#mine"), ("YAML", "#yaml")]

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
{% if mine_status.running %}<meta http-equiv="refresh" content="5">{% endif %}
<title>微信智能助手</title><style>
body{font-family:"Microsoft YaHei",sans-serif;margin:20px 28px;background:#f5f6f8;color:#222}
h1{font-size:20px;margin:0 0 4px} h2{font-size:15px;margin:14px 0 8px}
nav a{margin-right:12px;font-size:13px;color:#0a5;text-decoration:none}
.card{background:#fff;border:1px solid #e2e4e8;border-radius:8px;padding:12px 16px;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{padding:4px 6px;border-bottom:1px solid #eee;text-align:left;vertical-align:middle}
input[type=text],input[type=password],input[type=number],select{padding:3px 6px;border:1px solid #ccd0d6;border-radius:4px;font-size:13px}
input[type=text]{min-width:120px}
.on{color:#1a7f37;font-weight:bold}.off{color:#c93c37}
button{margin:2px;padding:3px 12px;font-size:12px;cursor:pointer}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0}
.row label{font-size:13px;color:#555;min-width:90px}
.muted{color:#777;font-size:12px}
.err{color:#c93c37;font-weight:bold}.ok{color:#1a7f37}
textarea{width:98%;padding:6px;border:1px solid #ccd0d6;border-radius:4px;font-family:inherit;font-size:13px}
details summary{cursor:pointer;font-weight:bold;font-size:14px}
</style></head><body id="top">
<h1>微信智能助手 <span class="muted">{{ self_name }} · {{ state }} · 已发送 {{ sent }} 条 · 运行 {{ uptime }} 分钟</span></h1>
<nav>{% for n, h in nav %}<a href="{{ h }}">{{ n }}</a>{% endfor %}</nav>

{% if error %}<div class="card" style="border-color:#c93c37">
<b class="err">助手未运行：{{ error }}</b>
<form method="post" action="/retry"><button>重试启动</button></form>
<p class="muted">请确认微信 PC 版已登录且主窗口存在，然后点重试。</p></div>{% endif %}

{% if flash %}<div class="card"><span class="ok">{{ flash }}</span></div>{% endif %}
{% if form_error %}<div class="card"><span class="err">{{ form_error }}</span>
{% if candidates %}<p class="muted">最近会话里找到这些相似的，点一下直接加：</p>
{% for r in candidates %}<form method="post" action="/cfg/pick" class="row">
  <input type="hidden" name="id" value="{{ r.id }}">
  <input type="hidden" name="display" value="{{ r.display }}">
  <input type="hidden" name="kind" value="{{ 'group' if r.is_group else 'contact' }}">
  <b style="min-width:150px">{{ r.display }}</b>
  <span class="muted" style="min-width:250px">{{ r.id }}{{ ' ·群' if r.is_group }}</span>
  <button>加为{{ '群' if r.is_group else '联系人' }}</button>
</form>{% endfor %}{% endif %}
</div>{% endif %}

{% if not setup_done %}<div class="card" style="border-color:#e8a33d;background:#fffbe8">
<h2>🚀 首次使用向导</h2>
<ol style="margin:6px 0 10px;font-size:13px">
  <li>确认微信 PC 版已登录（顶部状态显示：<b>{{ self_name }}</b>，不是"未连接"即可）</li>
  <li>填模型接口（自动回复要靠它生成消息）：<b>地址 + API Key + 模型名</b> 三样都填好 → 保存</li>
  <li>到下面的 <a href="#recent">最近会话</a> 卡片，搜索后点"加为联系人/加为群"，把要接管的会话加进来</li>
</ol>
<form method="post" action="/setup/llm" class="row">
  <label>模型接口地址</label><input type="text" name="base_url" value="{{ llm.base_url or '' }}" style="min-width:280px" placeholder="https://your-llm-gateway.example/v1">
  <label>API Key</label><input type="password" name="api_key" style="min-width:180px" placeholder="sk-…">
  <label>模型名</label><input type="text" name="model" style="min-width:130px">
  <button>保存模型设置</button>
</form>
<form method="post" action="/setup/llm" class="row">
  <label>知识库地址（可选，群答疑用）</label><input type="text" name="kb_url" style="min-width:260px">
  <label>知识库 Key（可选）</label><input type="text" name="kb_key" style="min-width:170px">
  <button>保存知识库设置</button>
</form>
<form method="post" action="/setup/done" class="row">
  <button>✔ 完成，进入控制台</button>
  <span class="muted">当前为演示模式（只记日志不真正发送）；都配好后去"模型与节奏"把演示模式关掉才真正回复</span>
</form>
</div>{% endif %}

<div class="card">
<form method="post" class="row">
  <button formaction="/pause" formmethod="post">{{ '恢复自动回复' if paused else '暂停自动回复' }}</button>
  <button formaction="/reload" formmethod="post">从文件重载配置</button>
  <span class="muted">所有保存操作立即生效（热重载）</span>
</form></div>

<a name="recent"></a><div class="card"><h2>最近会话（点开过的聊天都在这，直接点加，不用查 id）</h2>
{% if recent %}
<div class="row">
  <input type="text" id="recentQ" placeholder="🔍 输入昵称/群名关键字过滤…" oninput="filterRecent(this.value)"
         style="min-width:240px"><span class="muted" id="recentCount"></span>
</div>
<table id="recentT"><thead><tr><th style="width:26%">昵称/群名</th><th>微信id</th><th>类型</th><th>操作</th></tr></thead>
<tbody id="recentB">
{% for r in recent %}{% set _s = ((r.display or '') ~ ' ' ~ (r.id or '')) | lower %}
<tr data-s="{{ _s }}"><td>{{ r.display or '(空昵称)' }}</td><td class="muted">{{ r.id }}</td>
<td>{{ '群' if r.is_group else '私聊' }}</td><td>
{% if r.id in cfg_ids %}<span class="muted">已配置</span>
{% else %}<form method="post" action="/cfg/pick" style="margin:0">
  <input type="hidden" name="id" value="{{ r.id }}">
  <input type="hidden" name="display" value="{{ r.display or r.id }}">
  <input type="hidden" name="kind" value="{{ 'group' if r.is_group else 'contact' }}">
  <button>加为{{ '群' if r.is_group else '联系人' }}</button>
</form>{% endif %}
</td></tr>{% endfor %}
</tbody></table>
<p class="muted">列表来自微信"最近聊天"，没有的会话先在微信里点开一次就会出现在这里。API 形式：<code>/api/sessions?q=关键字</code></p>
<script>
function filterRecent(q){
  q=(q||'').trim().toLowerCase();
  var rows=document.getElementById('recentB').rows, n=0;
  for(var i=0;i<rows.length;i++){
    var hit=!q||rows[i].getAttribute('data-s').indexOf(q)>=0;
    rows[i].style.display=hit?'':'none'; if(hit)n++;
  }
  document.getElementById('recentCount').textContent=n+' 个会话';
}
filterRecent('');
</script>
{% else %}<p class="muted">{{ '微信未连接，连上后这里会列出最近会话' if not driver_up else '最近会话为空：先在微信里点开几个聊天' }}</p>{% endif %}
</div>

<a name="contacts"></a><div class="card"><h2>私聊联系人（白名单：勾选"启用"的才会 AI 自动回复）</h2>
{% for c in contacts %}
<form method="post" action="/cfg/contact" class="row" style="border-bottom:1px dashed #e5e5e5;padding:6px 0">
  <input type="hidden" name="idx" value="{{ loop.index0 }}">
  <label class="{{ 'on' if c.enabled else 'off' }}">{{ '● 启用' if c.enabled else '○ 停用' }}</label>
  <input type="checkbox" name="enabled" {{ 'checked' if c.enabled }} title="启用">
  <input type="text" name="display" value="{{ c.display or '' }}" style="min-width:110px" placeholder="昵称">
  <input type="text" name="id" value="{{ c.id or '' }}" style="min-width:220px" placeholder="微信号/wxid">
  <select name="persona">{% for p in persona_names %}<option {{ 'selected' if c.persona==p }}>{{ p }}</option>{% endfor %}</select>
  <button name="action" value="save">保存</button>
  <button name="action" value="del" onclick="return confirm('删除联系人 {{ c.display }}？')">删除</button>
</form>
{% endfor %}
<form method="post" action="/cfg/contact" class="row" style="padding-top:8px">
  <input type="hidden" name="action" value="new">
  <label>新增：</label><input type="text" name="display" placeholder="昵称（必填）">
  <input type="text" name="id" placeholder="微信号/wxid（可空自动反查）">
  <select name="persona">{% for p in persona_names %}<option>{{ p }}</option>{% endfor %}</select>
  <button>添加联系人</button>
</form>
<p class="muted">不知道微信号就先只填昵称，保存后系统自动反查。</p>
</div>

<a name="personas"></a><div class="card"><h2>人设库（语气与背景，联系人/群引用）</h2>
{% for name, p in personas.items() %}
<form method="post" action="/cfg/persona" class="row" style="border-bottom:1px dashed #e5e5e5;padding:6px 0">
  <input type="hidden" name="old_name" value="{{ name }}">
  <b style="min-width:110px">{{ name }}</b>
  <input type="text" name="name" value="{{ name }}" style="min-width:110px" title="改名会同步更新引用">
  <input type="text" name="style" value="{{ p.style or '' }}" style="flex:1;min-width:280px" placeholder="说话风格">
  <input type="text" name="background" value="{{ p.background or '' }}" style="flex:1.4;min-width:280px" placeholder="背景/关系">
  <button name="do" value="save">保存</button>
  <button name="do" value="del" onclick="return confirm('删除人设 {{ name }}？引用它的联系人将退回默认风格')">删除</button>
</form>
{% endfor %}
<form method="post" action="/cfg/persona" class="row" style="padding-top:8px">
  <input type="hidden" name="old_name" value="">
  <label>新增：</label><input type="text" name="name" placeholder="人设名（必填）">
  <input type="text" name="style" placeholder="说话风格" style="flex:1;min-width:240px">
  <input type="text" name="background" placeholder="背景/关系" style="flex:1;min-width:240px">
  <button name="do" value="save">添加人设</button>
</form>
</div>

<a name="groups"></a><div class="card"><h2>群（答疑 + 管理）</h2>
{% for g in groups %}
<form method="post" action="/cfg/group" style="border:1px solid #e8eaee;border-radius:6px;padding:10px;margin:8px 0">
  <input type="hidden" name="idx" value="{{ loop.index0 }}">
  <div class="row">
    <label class="{{ 'on' if g.enabled else 'off' }}">{{ '● 启用' if g.enabled else '○ 停用' }}</label>
    <input type="checkbox" name="enabled" {{ 'checked' if g.enabled }}>
    <input type="text" name="display" value="{{ g.display or '' }}" placeholder="群名（用于搜索打开）">
    <input type="text" name="id" value="{{ g.id or '' }}" placeholder="群id（可空，按群名自动匹配）" style="min-width:220px">
    <label>群内口吻</label><select name="persona">{% for p in persona_names %}<option {{ 'selected' if g.persona==p }}>{{ p }}</option>{% endfor %}</select>
    <button formaction="/cfg/group" formmethod="post" name="do" value="save">保存</button>
    <button formaction="/cfg/group" formmethod="post" name="do" value="del" onclick="return confirm('删除群 {{ g.display }}？')">删除</button>
  </div>
  <div class="row">
    <label>答疑</label><input type="checkbox" name="qa_enabled" {{ 'checked' if (g.qa or {}).get('enabled') }}>
    <label>带参考回复</label><input type="checkbox" name="grounded" {{ 'checked' if (g.qa or {}).get('grounded_reply', True) }}>
    <label>知识库断线走人设</label><input type="checkbox" name="llm_fallback" {{ 'checked' if (g.qa or {}).get('llm_fallback') }}>
    <label>自动挖历史</label><input type="checkbox" name="mine" {{ 'checked' if g.mine }}>
    <span class="muted">@机器人提问触发；自动挖历史=定时把群聊增量抽成问答</span>
  </div>
  <div class="row">
    <label>进群欢迎</label><input type="checkbox" name="welcome" {{ 'checked' if (g.manage or {}).get('welcome') }}>
    <input type="text" name="welcome_text" value="{{ (g.manage or {}).get('welcome_text') or '' }}" style="flex:1" placeholder="欢迎 {name} 入群！…（{name}=新人昵称）">
  </div>
  <div class="row">
    <label>违禁词</label><input type="text" name="forbidden" value="{{ ((g.manage or {}).get('forbidden_words') or []) | join(', ') }}" style="flex:1" placeholder="逗号分隔：广告, 刷单">
    <label>劝告人设</label><select name="warn_persona">{% for p in persona_names %}<option {{ 'selected' if (g.manage or {}).get('warn_persona')==p }}>{{ p }}</option>{% endfor %}</select>
    <label>升级阈值</label><input type="number" name="warn_threshold" value="{{ (g.manage or {}).get('warn_threshold', 2) }}" min="1" max="9" style="width:60px">
    <span class="muted">满 N 次私信管理员</span>
  </div>
</form>
{% endfor %}
<form method="post" action="/cfg/group" class="row">
  <input type="hidden" name="idx" value="-1">
  <input type="hidden" name="do" value="save">
  <label>新增群：</label><input type="text" name="display" placeholder="群名（必填）">
  <input type="text" name="id" placeholder="群id（可空，自动匹配）" style="min-width:220px">
  <button>添加群</button>
</form>
<p class="muted">只填群名即可：在微信里点开过该群，这里就能按名字自动匹配（群名带 emoji 也能匹配到）。</p>
</div>

<a name="llm"></a><div class="card"><h2>模型与节奏</h2>
<form method="post" action="/cfg/llm" class="row">
  <label>接口地址</label><input type="text" name="base_url" value="{{ llm.base_url or '' }}" style="min-width:260px">
  <label>API Key</label><input type="password" name="api_key" value="{{ llm.api_key or '' }}" style="min-width:200px">
  <label>模型</label><input type="text" name="model" value="{{ llm.model or '' }}">
  <label>温度</label><input type="number" name="temperature" value="{{ llm.temperature or 0.8 }}" step="0.1" min="0" max="2" style="width:64px">
  <label>超时(秒)</label><input type="number" name="timeout" value="{{ llm.timeout or 60 }}" style="width:64px">
  <button>保存模型设置</button>
</form>
<form method="post" action="/cfg/app" class="row">
  <input type="hidden" name="scope" value="run">
  <label>演示模式</label><input type="checkbox" name="dry_run" {{ 'checked' if dry_run }}><span class="muted">只记日志不真正发送&nbsp;&nbsp;</span>
  <label>轮询(秒)</label><input type="number" name="poll_sec" value="{{ reply.poll_sec or 3 }}" style="width:60px">
  <label>合并连发(秒)</label><input type="number" name="quiesce_sec" value="{{ reply.quiesce_sec or 5 }}" style="width:60px">
  <label>迟延(秒)</label><input type="number" name="hd_min" value="{{ (reply.human_delay or [2,5])[0] }}" style="width:55px"> ~
  <input type="number" name="hd_max" value="{{ (reply.human_delay or [2,5])[1] }}" style="width:55px">
  <label>发送上限</label><input type="number" name="max_replies" value="{{ reply.max_replies or 0 }}" style="width:70px">
  <button>保存运行设置</button>
</form>
<form method="post" action="/cfg/app" class="row">
  <input type="hidden" name="scope" value="admins">
  <label>管理员 wxid</label><input type="text" name="admins" value="{{ admins | join(', ') }}" style="min-width:320px">
  <button>保存管理员</button>
  <span class="muted">逗号分隔；管理员私聊可发 #状态 #暂停 等命令</span>
</form>
</div>

<a name="kb"></a><div class="card"><h2>知识库（群答疑后端）</h2>
<form method="post" action="/cfg/kb" class="row">
  <label>地址</label><input type="text" name="url" value="{{ kb.url or '' }}" style="min-width:260px">
  <label>API Key</label><input type="text" name="api_key" value="{{ kb.api_key or '' }}" style="min-width:240px">
  <label>库</label><input type="text" name="kb" value="{{ kb.kb or '' }}" style="width:70px">
  <label>top_k</label><input type="number" name="top_k" value="{{ kb.top_k or 5 }}" style="width:55px">
  <label>超时</label><input type="number" name="timeout" value="{{ kb.timeout or 60 }}" style="width:60px">
  <button>保存</button>
</form>
<p class="muted">当前 key 若过期会显示在日志区（HTTP 401）；群答疑在 kb 失败时可按群配置回退到人设 LLM。</p>
</div>

<a name="logs"></a><div class="card"><h2>最近日志</h2>
<table><tr><th>时间</th><th>类型</th><th>会话</th><th>内容</th></tr>
{% for l in logs %}<tr><td class="muted">{{ l.t }}</td><td>{{ l.kind }}</td>
<td>{{ l.chat }}</td><td>{{ l.detail }}</td></tr>{% endfor %}</table>
{% if warns %}<p class="muted">劝告记录：{{ warns }}</p>{% endif %}
</div>

<a name="pending"></a><div class="card"><h2>待沉淀问题（群答疑未正中，可复制给知识库管理员入库）</h2>
<table><tr><th>#</th><th>群</th><th>提问人</th><th>问题</th></tr>
{% for q in pending %}<tr><td>{{ q.id }}</td><td>{{ q.grp }}</td>
<td>{{ q.questioner }}</td><td>{{ q.question }}</td></tr>{% endfor %}
{% if not pending %}<tr><td colspan="4" class="muted">无</td></tr>{% endif %}
</table></div>

<a name="mine"></a><div class="card"><h2>群历史挖掘（把群聊记录抽成问答知识）</h2>
{% if mine_status.running %}<p><b>正在挖掘 {{ mine_status.group }}：{{ mine_status.done }}/{{ mine_status.total }} 条消息，已抽 {{ mine_status.pairs }} 对…（页面每 5 秒自动刷新）</b></p>
{% elif mine_status.error %}<p class="err">上次挖掘失败：{{ mine_status.error }}</p>
{% else %}<p class="muted">空闲 · 本地已累计 {{ mined_count }} 对问答</p>{% endif %}
{% for g in groups %}
<form method="post" action="/mine" class="row">
  <input type="hidden" name="idx" value="{{ loop.index0 }}">
  <b style="min-width:140px">{{ g.display or g.id }}</b>
  <button name="mode" value="full" onclick="return confirm('全量会重读整个群历史并调用模型，群很大时要跑几分钟，继续？')">全量挖掘</button>
  <button name="mode" value="incr">增量挖掘</button>
  <span class="muted">增量只处理上次之后的新消息；自动增量请在群配置勾"自动挖历史"</span>
</form>
{% endfor %}
<form method="post" action="/mine/export" class="row">
  <button>导出全部为 Markdown</button>
  <span class="muted">导出文件交给知识库管理员导入即可入库</span>
</form>
<table><tr><th>群</th><th>问题</th><th>回答</th></tr>
{% for r in mined %}<tr><td>{{ r.grp }}</td><td>{{ r.question }}</td><td>{{ r.answer }}</td></tr>
{% endfor %}
{% if not mined %}<tr><td colspan="3" class="muted">还没有挖掘记录</td></tr>{% endif %}
</table></div>

<a name="yaml"></a><div class="card"><details><summary>高级：YAML 源码（给 AI / 调试用，网页保存会丢注释）</summary>
<form method="post" action="/config">
<textarea name="cfg">{{ cfg_text }}</textarea><br>
<button>保存 YAML</button>
</form></details></div>
</body></html>"""


_recent_cache = {"ts": 0.0, "rows": []}


def _recent_chats(bot):
    """最近会话（带 30s 缓存——每次渲染都查一遍 80 个昵称太重）。"""
    drv = getattr(bot, "driver", None)
    lister = getattr(drv, "list_recent_chats", None)
    if not (drv and callable(lister)):
        return []
    if time.time() - _recent_cache["ts"] < 30:
        return _recent_cache["rows"]
    try:
        rows = list(lister(80))
    except Exception as e:
        log.warning("最近会话读取失败: %s", e)
        return []
    _recent_cache.update(ts=time.time(), rows=rows)
    return rows


def start_web(bot):
    app = Flask(__name__)
    app.debug = False
    _flash = {"msg": ""}

    def _hot_apply():
        """配置变更后刷新运行时（对 Bot 与 BotShell 都安全）。"""
        personas = getattr(bot, "personas", None)
        if personas:
            try:
                personas.refresh()
            except Exception:
                pass
        inner = getattr(bot, "_bot", None) or bot
        try:
            inner._kb = None
        except AttributeError:
            pass
        if getattr(bot, "build_watch", None):
            try:
                bot.build_watch()
            except Exception:
                pass

    def _persist():
        """把 cfg.data 写回 yaml 并热重载。"""
        with open(bot.cfg.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(bot.cfg.data, f, allow_unicode=True, sort_keys=False)
        bot.cfg.reload()
        _hot_apply()
        _flash["msg"] = "已保存并生效"

    def _candidates(q):
        """反查失败时的模糊候选（昵称/群名/ID 子串匹配，忽略空格大小写）。"""
        drv = getattr(bot, "driver", None)
        searcher = getattr(drv, "search_chats", None)
        if not callable(searcher):
            return []
        try:
            return list(searcher(q, 8))
        except Exception as e:
            log.warning("会话搜索失败: %s", e)
            return []

    def _render(form_error="", candidates=()):
        cfg = bot.cfg
        snap = cfg.snapshot()
        logs = bot.store.recent_logs(30)
        for l in logs:
            l["t"] = time.strftime("%m-%d %H:%M:%S", time.localtime(l["ts"]))
            l["detail"] = html.escape(str(l["detail"]))[:120]
        driver = getattr(bot, "driver", None)
        recent = _recent_chats(bot)
        cfg_ids = ({(c or {}).get("id") for c in snap.get("contacts") or []}
                   | {(g or {}).get("id") for g in snap.get("groups") or []})
        return render_template_string(
            _PAGE, nav=_NAV,
            self_name=(driver.self_name if driver else "未连接"),
            state=("已暂停" if bot.state["paused"] else "运行中") if driver else "未启动",
            paused=bot.state["paused"],
            error=getattr(bot, "startup_error", None),
            flash=_flash["msg"], form_error=form_error,
            candidates=candidates or (),
            setup_done=bool((snap.get("app") or {}).get("setup_done", True)),
            recent=recent, cfg_ids=cfg_ids, driver_up=bool(driver),
            contacts=snap.get("contacts") or [], groups=snap.get("groups") or [],
            personas=snap.get("personas") or {}, persona_names=cfg.persona_names,
            llm=snap.get("llm") or {}, kb=snap.get("kb") or {}, reply=snap.get("reply") or {},
            admins=snap.get("admins") or [], dry_run=bool(snap.get("app", {}).get("dry_run")),
            uptime=int((time.time() - bot.state["start"]) / 60),
            sent=bot.state["sent"],
            warns=" / ".join(f'{w["chat"]}·{w["member"]}×{w["n"]}'
                             for w in bot.store.warn_counts()[:8]) or "无",
            logs=logs, pending=bot.store.qa_pending(20),
            mine_status=mining.status(), mined=bot.store.mined_pairs(limit=15),
            mined_count=bot.store.mined_count(),
            cfg_text=yaml.safe_dump(snap, allow_unicode=True, sort_keys=False))

    @app.route("/")
    def index():
        _flash["msg"] = ""
        return _render()

    @app.post("/pause")
    def pause():
        bot.state["paused"] = not bot.state["paused"]
        return redirect("/")

    @app.post("/reload")
    def reload_cfg():
        bot.cfg.reload()
        _hot_apply()
        return redirect("/")

    @app.post("/retry")
    def retry():
        starter = getattr(bot, "restart", None)
        if callable(starter):
            starter()
        return redirect("/")

    # ---------- 可视化配置 ----------
    @app.post("/cfg/contact")
    def cfg_contact():
        f = request.form
        act = f.get("action") or f.get("do")
        items = bot.cfg.data.setdefault("contacts", [])
        if act == "new":
            display = (f.get("display") or "").strip()
            if not display:
                return _render(form_error="联系人昵称不能为空")
            cid = (f.get("id") or "").strip()
            if not cid:
                drv = getattr(bot, "driver", None)
                resolver = getattr(drv, "resolve_id", None)
                cid = resolver(display) if callable(resolver) else ""
                if not cid:
                    return _render(
                        form_error=f"反查不到 {display} 的微信号，看看下面候选或去『最近会话』点选",
                        candidates=_candidates(display))
            if any(c.get("id") == cid for c in items):
                return _render(form_error=f"{display}（{cid}）已在列表中")
            items.append({"id": cid, "display": display,
                          "persona": f.get("persona") or "通用朋友", "enabled": True})
        else:
            try:
                idx = int(f.get("idx"))
            except (TypeError, ValueError):
                return _render(form_error="参数错误")
            if not 0 <= idx < len(items):
                return _render(form_error="行不存在")
            if act == "del":
                items.pop(idx)
            else:
                c = items[idx]
                c["display"] = (f.get("display") or "").strip() or c.get("display", "")
                if (f.get("id") or "").strip():
                    c["id"] = f.get("id").strip()
                c["persona"] = f.get("persona") or c.get("persona", "通用朋友")
                c["enabled"] = bool(f.get("enabled"))
        _persist()
        return redirect("/")

    @app.post("/cfg/persona")
    def cfg_persona():
        f = request.form
        old = (f.get("old_name") or "").strip()
        name = (f.get("name") or "").strip()
        personas = bot.cfg.data.setdefault("personas", {})
        if f.get("do") == "del" and old:
            personas.pop(old, None)
        elif name:
            if old and old != name:                      # 改名：同步引用
                personas[name] = personas.pop(old, {})
                for c in bot.cfg.data.get("contacts") or []:
                    if c.get("persona") == old:
                        c["persona"] = name
                for g in bot.cfg.data.get("groups") or []:
                    if g.get("persona") == old:
                        g["persona"] = name
                    if (g.get("manage") or {}).get("warn_persona") == old:
                        g["manage"]["warn_persona"] = name
            personas.setdefault(name, {})
            personas[name]["style"] = (f.get("style") or "").strip()
            personas[name]["background"] = (f.get("background") or "").strip()
        else:
            return _render(form_error="人设名不能为空")
        _persist()
        return redirect("/")

    @app.post("/cfg/group")
    def cfg_group():
        f = request.form
        try:
            idx = int(f.get("idx"))
        except (TypeError, ValueError):
            return _render(form_error="参数错误")
        groups = bot.cfg.data.setdefault("groups", [])
        if f.get("do") == "del" and 0 <= idx < len(groups):
            groups.pop(idx)
            _persist()
            return redirect("/")
        gname = (f.get("display") or "").strip()
        gid = (f.get("id") or "").strip()
        if not gname:
            return _render(form_error="群名必填")
        if not gid:
            driver = getattr(bot, "driver", None)
            hit = driver.resolve_group_id(gname) if driver else None
            if not hit:
                return _render(
                    form_error=f"最近会话里找不到“{gname}”：请在微信里点开这个群聊一次，再回来添加",
                    candidates=_candidates(gname))
            gid, matched = hit
            _flash["msg"] = f"已自动匹配群“{matched}”"
        data = {
            "display": gname,
            "id": gid,
            "enabled": bool(f.get("enabled")),
            "persona": f.get("persona") or "通用朋友",
            "mine": bool(f.get("mine")),
            "qa": {"enabled": bool(f.get("qa_enabled")),
                   "grounded_reply": bool(f.get("grounded")),
                   "llm_fallback": bool(f.get("llm_fallback"))},
            "manage": {"welcome": bool(f.get("welcome")),
                       "welcome_text": (f.get("welcome_text") or "").strip(),
                       "forbidden_words": [w.strip() for w in (f.get("forbidden") or "").split(",") if w.strip()],
                       "warn_persona": f.get("warn_persona") or "群管家-热心",
                       "warn_threshold": int(f.get("warn_threshold") or 2)},
        }
        if idx < 0:
            groups.append(data)
        elif idx < len(groups):
            old = groups[idx]
            data["manage"]["mention_names"] = (old.get("manage") or {}).get("mention_names") or []
            groups[idx] = data
        _persist()
        return redirect("/")

    @app.post("/cfg/pick")
    def cfg_pick():
        """最近会话选择器/候选按钮的一键添加（id 直接给全，无需反查）。"""
        f = request.form
        cid = (f.get("id") or "").strip()
        display = (f.get("display") or "").strip()
        if not cid or not display:
            return _render(form_error="参数错误（缺 id 或昵称）")
        kind = "group" if (f.get("kind") == "group" or cid.endswith("@chatroom")) else "contact"
        if kind == "group":
            items = bot.cfg.data.setdefault("groups", [])
            if any(g.get("id") == cid for g in items):
                _flash["msg"] = f"群 {display} 已经在列表里了"
                return redirect("/")
            items.append({"id": cid, "display": display, "enabled": True,
                          "persona": "群管家-热心", "mine": False,
                          "qa": {"enabled": True, "grounded_reply": True, "llm_fallback": False},
                          "manage": {"welcome": True,
                                     "welcome_text": "欢迎 {name} 入群！有问题可以直接 @我 提问～",
                                     "forbidden_words": [],
                                     "warn_persona": "群管家-热心", "warn_threshold": 2}})
            _persist()
            _flash["msg"] = f"已加群 {display}（启用中，答疑/欢迎默认开，可在上方调整）"
        else:
            items = bot.cfg.data.setdefault("contacts", [])
            if any(c.get("id") == cid for c in items):
                _flash["msg"] = f"{display} 已经在列表里了"
                return redirect("/")
            items.append({"id": cid, "display": display, "persona": "通用朋友", "enabled": True})
            _persist()
            _flash["msg"] = f"已加联系人 {display}（人设默认“通用朋友”，可在上方调整）"
        return redirect("/")

    @app.post("/setup/llm")
    def setup_llm():
        """首启向导：模型三件套（地址/Key/模型名）+ 可选知识库。"""
        f = request.form
        base = (f.get("base_url") or "").strip()
        key = (f.get("api_key") or "").strip()
        model = (f.get("model") or "").strip()
        if base or key or model:
            bad = [n for n, v in (("接口地址", base), ("API Key", key), ("模型名", model))
                   if not v or "your-" in v.lower()]
            if bad:
                return _render(form_error="请填真实值再保存（还是占位符/空值）：" + "、".join(bad),
                               candidates=())
            llm = bot.cfg.data.setdefault("llm", {})
            llm.update({"base_url": base, "api_key": key, "model": model})
        kb_url = (f.get("kb_url") or "").strip()
        kb_key = (f.get("kb_key") or "").strip()
        if kb_url or kb_key:
            kb = bot.cfg.data.setdefault("kb", {})
            for k, v in (("url", kb_url), ("api_key", kb_key)):
                if v and "your-" not in v.lower():
                    kb[k] = v
        _persist()
        return redirect("/")

    @app.post("/setup/done")
    def setup_done():
        bot.cfg.data.setdefault("app", {})["setup_done"] = True
        _persist()
        _flash["msg"] = ("向导完成！注意：当前是演示模式（只记日志不发送），"
                         "确认无误后到『模型与节奏』取消勾选才真正回复")
        return redirect("/")

    @app.route("/api/sessions")
    def api_sessions():
        """JSON 形式的最近会话（?q= 走模糊搜索）。"""
        drv = getattr(bot, "driver", None)
        if not (drv and callable(getattr(drv, "list_recent_chats", None))):
            return {"ok": False, "sessions": []}
        q = (request.args.get("q") or "").strip()
        try:
            if q and callable(getattr(drv, "search_chats", None)):
                rows = drv.search_chats(q, 20)
            else:
                rows = drv.list_recent_chats(60)
        except Exception as e:
            return {"ok": False, "sessions": [], "error": str(e)}
        return {"ok": True, "sessions": rows}

    @app.post("/cfg/llm")
    def cfg_llm():
        f = request.form
        llm = bot.cfg.data.setdefault("llm", {})
        if (f.get("base_url") or "").strip():
            llm["base_url"] = f["base_url"].strip()
        if f.get("api_key"):
            llm["api_key"] = f["api_key"].strip()
        if f.get("model"):
            llm["model"] = f["model"].strip()
        try:
            llm["temperature"] = float(f.get("temperature") or 0.8)
            llm["timeout"] = int(f.get("timeout") or 60)
        except ValueError:
            return _render(form_error="温度/超时必须是数字")
        _persist()
        return redirect("/")

    @app.post("/cfg/kb")
    def cfg_kb():
        f = request.form
        kb = bot.cfg.data.setdefault("kb", {})
        for k in ("url", "api_key", "kb"):
            if (f.get(k) or "").strip():
                kb[k] = f[k].strip()
        for k in ("top_k", "timeout"):
            if f.get(k):
                try:
                    kb[k] = int(f[k])
                except ValueError:
                    pass
        _persist()
        return redirect("/")

    @app.post("/cfg/app")
    def cfg_app():
        f = request.form
        if f.get("scope") == "admins":
            admins = [a.strip() for a in (f.get("admins") or "").split(",") if a.strip()]
            if not admins:
                return _render(form_error="管理员不能为空")
            bot.cfg.data["admins"] = admins
            _persist()
            return redirect("/")
        appc = bot.cfg.data.setdefault("app", {})
        rep = bot.cfg.data.setdefault("reply", {})
        appc["dry_run"] = bool(f.get("dry_run"))
        for k in ("poll_sec", "quiesce_sec"):
            if f.get(k):
                try:
                    rep[k] = int(f[k])
                except ValueError:
                    pass
        try:
            rep["human_delay"] = [int(f.get("hd_min") or 2), int(f.get("hd_max") or 5)]
            if f.get("max_replies") is not None and f["max_replies"] != "":
                rep["max_replies"] = int(f["max_replies"])
        except ValueError:
            return _render(form_error="数值格式错误")
        _persist()
        return redirect("/")

    @app.post("/mine")
    def mine():
        if not getattr(bot, "driver", None):
            return _render(form_error="微信未连接，无法挖掘")
        f = request.form
        try:
            g = (bot.cfg.data.get("groups") or [])[int(f.get("idx"))]
        except (TypeError, ValueError, IndexError):
            return _render(form_error="参数错误")
        full = f.get("mode") == "full"
        ok = mining.start_mine(bot, g.get("id") or "", g.get("display") or g.get("id") or "", full)
        if ok:
            _flash["msg"] = ("已开始全量挖掘" if full else "已开始增量挖掘") + f" {g.get('display')}"
        else:
            _flash["msg"] = "已有挖掘任务在跑或微信/模型未就绪，稍后再试"
        return redirect("/")

    @app.post("/mine/export")
    def mine_export():
        path = mining.export_markdown(bot.store)
        _flash["msg"] = f"已导出：{path}" if path else "还没有可导出的问答"
        return redirect("/")

    @app.post("/config")
    def save_cfg_yaml():
        raw = request.form.get("cfg", "")
        try:
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                raise ValueError("顶层应为配置字典")
            with open(bot.cfg.path, "w", encoding="utf-8") as fh:
                fh.write(raw)
            bot.cfg.reload()
            _hot_apply()
            _flash["msg"] = "YAML 已保存并生效"
        except Exception as e:
            return _render(form_error=f"保存失败: {html.escape(str(e))}")
        return redirect("/")

    def _serve():
        app.run(host="127.0.0.1", port=bot.cfg.web_port, threaded=True)

    threading.Thread(target=_serve, daemon=True).start()
    log.info("控制台 http://127.0.0.1:%s", bot.cfg.web_port)
    return app
