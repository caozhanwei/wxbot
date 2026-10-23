# -*- coding: utf-8 -*-
"""Web 控制台（桌面窗口承载）：可视化配置（联系人/人设/群/模型/知识库）、状态、日志。"""
import html
import io
import json
import logging
import threading
import time
import zipfile
from urllib.parse import quote

import yaml
from flask import Flask, Response, request, redirect, render_template_string

from . import bootstrap, mining, profiles as profiles_mod

log = logging.getLogger("wxbot.web")

_PAGE_SIZE = 20   # 画像/问答库浏览页大小

_NAV = [("仪表盘", "#top"), ("最近会话", "#recent"), ("私聊联系人", "#contacts"),
        ("人设库", "#personas"), ("群管理与答疑", "#groups"), ("模型与节奏", "#llm"), ("知识库", "#kb"),
        ("运行日志", "#logs"), ("待沉淀问题", "#pending"), ("群历史挖掘", "#mine"),
        ("群友画像", "#profiles"), ("高级 YAML", "#yaml")]

_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
{% if mine_status.running or profile_status.running or bootstrap_status.running %}<meta http-equiv="refresh" content="5">{% endif %}
<title>微信智能助手控制台</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{
  --primary:#07c160;--primary-hover:#06ae56;--primary-light:#e8f7ee;
  --bg:#f4f6f9;--surface:#ffffff;--surface-sub:#f9fafb;
  --border:#e5e7eb;--border-hover:#d1d5db;
  --text-main:#111827;--text-sub:#4b5563;--text-muted:#9ca3af;
  --danger:#ef4444;--danger-light:#fef2f2;--danger-hover:#dc2626;
  --warning:#f59e0b;--warning-light:#fffbeb;
  --radius-sm:6px;--radius-md:10px;--radius-lg:14px;
  --shadow-sm:0 1px 2px 0 rgba(0,0,0,0.05);
  --shadow-md:0 4px 6px -1px rgba(0,0,0,0.07),0 2px 4px -2px rgba(0,0,0,0.05);
  --shadow-lg:0 10px 15px -3px rgba(0,0,0,0.08),0 4px 6px -4px rgba(0,0,0,0.04);
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  background:var(--bg);color:var(--text-main);
  line-height:1.5;padding-bottom:60px;-webkit-font-smoothing:antialiased;
}

/* 顶部导航吸顶栏 */
.top-header{
  position:sticky;top:0;z-index:100;
  background:rgba(255,255,255,0.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  border-bottom:1px solid var(--border);box-shadow:var(--shadow-sm);
  padding:10px 24px;
}
.header-inner{max-width:1380px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
.brand{display:flex;align-items:center;gap:10px}
.brand-icon{
  width:32px;height:32px;background:var(--primary);color:#fff;
  border-radius:8px;display:flex;align-items:center;justify-content:center;
  font-weight:bold;font-size:18px;box-shadow:0 2px 6px rgba(7,193,96,0.3);
}
.brand h1{font-size:17px;font-weight:700;color:var(--text-main);letter-spacing:-0.2px}
.brand-status{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-sub);background:var(--surface-sub);padding:3px 10px;border-radius:20px;border:1px solid var(--border)}
.status-dot{width:8px;height:8px;border-radius:50%;background:#10b981;box-shadow:0 0 0 2px rgba(16,185,129,0.2)}
.status-dot.paused{background:#f59e0b;box-shadow:0 0 0 2px rgba(245,158,11,0.2)}
.status-dot.offline{background:#ef4444;box-shadow:0 0 0 2px rgba(239,68,68,0.2)}

/* 快捷导航药丸 */
nav.nav-tabs{max-width:1380px;margin:12px auto 0;padding:0 24px;display:flex;gap:6px;overflow-x:auto;scrollbar-width:none}
nav.nav-tabs::-webkit-scrollbar{display:none}
nav.nav-tabs a{
  display:inline-flex;align-items:center;white-space:nowrap;
  padding:6px 14px;border-radius:20px;font-size:13px;font-weight:500;
  color:var(--text-sub);background:var(--surface);border:1px solid var(--border);
  text-decoration:none;transition:all 0.15s ease;
}
nav.nav-tabs a:hover{color:var(--primary);border-color:var(--primary);background:var(--primary-light)}

/* 主体容器 */
.container{max-width:1380px;margin:16px auto;padding:0 24px}

/* 顶部状态统计仪表盘 Grid */
.dashboard-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:16px}
.metric-card{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-md);
  padding:14px 18px;display:flex;align-items:center;justify-content:space-between;
  box-shadow:var(--shadow-sm);transition:transform 0.15s ease,box-shadow 0.15s ease;
}
.metric-card:hover{transform:translateY(-1px);box-shadow:var(--shadow-md)}
.metric-info{display:flex;flex-direction:column}
.metric-label{font-size:12px;color:var(--text-muted);font-weight:500;margin-bottom:2px}
.metric-value{font-size:20px;font-weight:700;color:var(--text-main);letter-spacing:-0.5px}
.metric-sub{font-size:11px;color:var(--text-sub);margin-top:2px}
.metric-badge{font-size:24px;opacity:0.85}

/* 通用卡片 */
.card{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);
  padding:18px 20px;margin:16px 0;box-shadow:var(--shadow-sm);
  transition:box-shadow 0.15s ease;
}
.card:hover{box-shadow:var(--shadow-md)}
.card-header{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border);padding-bottom:12px;margin-bottom:14px}
/* 卡片折叠：点标题收起/展开，状态记 localStorage */
.card-header{cursor:pointer;user-select:none}
.card-header::after{content:"▾";color:var(--text-muted);font-size:13px;margin-left:8px;transition:transform .15s}
.card.collapsed>.card-header{margin-bottom:0;border-bottom-color:transparent}
.card.collapsed>.card-header::after{transform:rotate(-90deg)}
.card.collapsed>*:not(.card-header){display:none}
.card-title{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:700;color:var(--text-main)}
.card-title::before{content:"";display:inline-block;width:3px;height:14px;background:var(--primary);border-radius:2px}
.card-subtitle{font-size:12px;color:var(--text-muted);font-weight:normal}

/* 表单与输入框 */
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 0}
.row label{font-size:13px;font-weight:500;color:var(--text-sub);display:inline-flex;align-items:center;gap:4px}
input[type=text],input[type=password],input[type=number],select,textarea{
  padding:7px 11px;border:1px solid var(--border);border-radius:var(--radius-sm);
  font-size:13px;color:var(--text-main);background:#fff;
  transition:border-color 0.15s ease,box-shadow 0.15s ease;
}
input[type=text]:focus,input[type=password]:focus,input[type=number]:focus,select:focus,textarea:focus{
  outline:none;border-color:var(--primary);box-shadow:0 0 0 3px rgba(7,193,96,0.15);
}
input[type=checkbox]{
  width:16px;height:16px;accent-color:var(--primary);cursor:pointer;
  vertical-align:middle;border-radius:4px;
}
textarea{width:100%;font-family:Consolas,Monaco,monospace;line-height:1.5;min-height:160px;resize:vertical}

/* 按钮规范 */
button,.btn{
  display:inline-flex;align-items:center;justify-content:center;
  gap:5px;padding:7px 14px;font-size:12.5px;font-weight:600;
  border-radius:var(--radius-sm);border:1px solid var(--border);
  background:#fff;color:var(--text-main);cursor:pointer;
  transition:all 0.15s ease;text-decoration:none;line-height:1.2;
}
button:hover,.btn:hover{background:var(--surface-sub);border-color:var(--border-hover)}
button:active,.btn:active{transform:scale(0.98)}
button.btn-primary,.btn-primary{background:var(--primary);color:#fff;border-color:var(--primary)}
button.btn-primary:hover,.btn-primary:hover{background:var(--primary-hover);border-color:var(--primary-hover);box-shadow:0 2px 6px rgba(7,193,96,0.25)}
button.btn-danger,.btn-danger{background:#fff;color:var(--danger);border-color:#fca5a5}
button.btn-danger:hover,.btn-danger:hover{background:var(--danger-light);border-color:var(--danger)}
.btn-sm{padding:4px 9px;font-size:12px}

/* 状态标签 Badge */
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:12px;font-size:11.5px;font-weight:600}
.badge-success,.on{background:var(--primary-light);color:#059669;font-weight:600}
.badge-danger,.off{background:var(--danger-light);color:var(--danger);font-weight:600}
.badge-warning{background:var(--warning-light);color:#d97706}
.badge-neutral{background:var(--surface-sub);color:var(--text-sub);border:1px solid var(--border)}

/* 提示卡 */
.alert{padding:12px 16px;border-radius:var(--radius-md);margin:12px 0;display:flex;align-items:center;gap:10px;font-size:13px}
.alert-success{background:var(--primary-light);border:1px solid #a7f3d0;color:#065f46}
.alert-danger{background:var(--danger-light);border:1px solid #fecaca;color:#991b1b}
.alert-warning{background:var(--warning-light);border:1px solid #fde68a;color:#92400e}

/* 表格规范 */
.table-responsive{overflow-x:auto;margin:10px 0}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:13px;text-align:left}
th{background:var(--surface-sub);color:var(--text-sub);font-weight:600;padding:10px 12px;border-bottom:1px solid var(--border);border-top:1px solid var(--border)}
th:first-child{border-top-left-radius:var(--radius-sm);border-left:1px solid var(--border)}
th:last-child{border-top-right-radius:var(--radius-sm);border-right:1px solid var(--border)}
td{padding:10px 12px;border-bottom:1px solid #f0f2f5;color:var(--text-main);vertical-align:middle;word-break:break-word}
td:first-child{border-left:1px solid transparent}
td:last-child{border-right:1px solid transparent}
tr:hover td{background:#fafbfc}
tr:last-child td{border-bottom:1px solid var(--border)}
tr:last-child td:first-child{border-bottom-left-radius:var(--radius-sm);border-left:1px solid var(--border)}
tr:last-child td:last-child{border-bottom-right-radius:var(--radius-sm);border-right:1px solid var(--border)}

/* 分类胶囊群 Filter Chips */
.chip-group{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:10px 0}
.chip{
  display:inline-flex;align-items:center;padding:4px 12px;border-radius:16px;
  font-size:12px;font-weight:500;text-decoration:none;
  background:var(--surface-sub);color:var(--text-sub);border:1px solid var(--border);
  transition:all 0.15s ease;
}
.chip:hover{border-color:var(--primary);color:var(--primary);background:var(--primary-light)}
.chip.active,.chip.on{background:var(--primary);color:#fff;border-color:var(--primary);font-weight:600;box-shadow:0 1px 3px rgba(7,193,96,0.3)}

/* 分页控件 Pagination */
.pagination-wrap{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin:14px 0 6px;padding-top:10px;border-top:1px dashed var(--border)}
.pagination-btns{display:flex;align-items:center;gap:8px}

.muted{color:var(--text-muted);font-size:12px}
.err{color:var(--danger);font-weight:600}
.ok{color:#059669;font-weight:600}

/* 折叠区域 */
details summary{cursor:pointer;font-weight:600;font-size:14px;color:var(--text-main);user-select:none;padding:4px 0}
details summary:hover{color:var(--primary)}
</style>
</head>
<body id="top">

<!-- 顶部吸顶条 -->
<header class="top-header">
  <div class="header-inner">
    <div class="brand">
      <div class="brand-icon">微</div>
      <div>
        <h1>微信智能助手 <span class="muted" style="font-weight:normal;font-size:13px">{{ self_name }} · {{ state }} · 已发送 {{ sent }} 条 · 运行 {{ uptime }} 分钟</span></h1>
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <form method="post" style="display:flex;gap:8px;margin:0">
        <button class="btn btn-sm {{ 'btn-danger' if not paused else 'btn-primary' }}" formaction="/pause" formmethod="post">
          {{ '恢复自动回复' if paused else '暂停自动回复' }}
        </button>
        <button class="btn btn-sm" formaction="/reload" formmethod="post">从文件重载配置</button>
      </form>
    </div>
  </div>
</header>

<!-- 快捷导航 -->
<nav class="nav-tabs">
  {% for n, h in nav %}<a href="{{ h }}">{{ n }}</a>{% endfor %}
</nav>

<div class="container">

<!-- 状态统计仪表盘 Cards -->
<div class="dashboard-grid">
  <div class="metric-card">
    <div class="metric-info">
      <span class="metric-label">微信连接状态</span>
      <span class="metric-value">{{ self_name or '未连接' }}</span>
      <span class="metric-sub">{{ '正常连接' if driver_up else '请检查微信主窗口' }}</span>
    </div>
    <div class="metric-badge">💬</div>
  </div>
  <div class="metric-card">
    <div class="metric-info">
      <span class="metric-label">助手运行状态</span>
      <span class="metric-value {{ 'err' if paused else 'ok' }}">{{ state }}</span>
      <span class="metric-sub">{{ '演示模式：只记日志不发送' if dry_run else '实盘模式：自动回复已就绪' }}</span>
    </div>
    <div class="metric-badge">{{ '⏸' if paused else '⚡' }}</div>
  </div>
  <div class="metric-card">
    <div class="metric-info">
      <span class="metric-label">累计自动回复</span>
      <span class="metric-value">{{ sent }} <span style="font-size:12px;font-weight:normal;color:var(--text-muted)">条</span></span>
      <span class="metric-sub">{{ '上限: ' ~ reply.max_replies ~ ' 条' if reply.max_replies else '无条数上限' }}</span>
    </div>
    <div class="metric-badge">📤</div>
  </div>
  <div class="metric-card">
    <div class="metric-info">
      <span class="metric-label">系统运行时长</span>
      <span class="metric-value">{{ uptime }} <span style="font-size:12px;font-weight:normal;color:var(--text-muted)">分钟</span></span>
      <span class="metric-sub">全部保存操作即刻热生效</span>
    </div>
    <div class="metric-badge">⏱</div>
  </div>
</div>

{% if error %}
<div class="alert alert-danger">
  <b class="err">助手未运行：{{ error }}</b>
  <form method="post" action="/retry" style="margin:0 0 0 auto"><button class="btn btn-sm btn-primary">重试启动</button></form>
  <span class="muted">请确认微信 PC 版已登录且主窗口存在，然后点重试。</span>
</div>
{% endif %}

{% if flash %}
<div class="alert alert-success">
  <span>✔</span> <span class="ok">{{ flash }}</span>
</div>
{% endif %}

{% if form_error %}
<div class="alert alert-danger" style="flex-direction:column;align-items:flex-start">
  <div><span>⚠</span> <span class="err">{{ form_error }}</span></div>
  {% if candidates %}
  <div style="margin-top:8px;width:100%">
    <p class="muted" style="margin-bottom:6px">最近会话里找到这些相似的，点一下直接加：</p>
    {% for r in candidates %}
    <form method="post" action="/cfg/pick" class="row" style="margin:4px 0">
      <input type="hidden" name="id" value="{{ r.id }}">
      <input type="hidden" name="display" value="{{ r.display }}">
      <input type="hidden" name="kind" value="{{ 'group' if r.is_group else 'contact' }}">
      <b style="min-width:140px">{{ r.display }}</b>
      <span class="muted" style="min-width:240px">{{ r.id }}{{ ' ·群' if r.is_group }}</span>
      <button class="btn btn-sm btn-primary">加为{{ '群' if r.is_group else '联系人' }}</button>
    </form>
    {% endfor %}
  </div>
  {% endif %}
</div>
{% endif %}

{% if not setup_done %}
<div class="card" style="border-color:#fcd34d;background:#fffdf5">
  <div class="card-header">
    <div class="card-title" style="color:#b45309">🚀 首次使用向导</div>
  </div>
  <ol style="margin:4px 0 14px 18px;font-size:13px;color:var(--text-sub);line-height:1.7">
    <li>确认微信 PC 版已登录（顶部状态显示：<b>{{ self_name }}</b>，不是"未连接"即可）</li>
    <li>填模型接口（自动回复要靠它生成消息）：<b>地址 + API Key + 模型名</b> 三样都填好 → 保存</li>
    <li>到下面的 <a href="#recent" style="color:var(--primary);font-weight:600">最近会话</a> 卡片，搜索后点"加为联系人/加为群"，把要接管的会话加进来</li>
  </ol>
  <form method="post" action="/setup/llm" class="row" style="margin-bottom:12px">
    <label>模型接口地址</label><input type="text" name="base_url" value="{{ llm.base_url or '' }}" style="flex:1;min-width:280px" placeholder="https://your-llm-gateway.example/v1">
    <label>API Key</label><input type="password" name="api_key" style="min-width:180px" placeholder="sk-…">
    <label>模型名</label><input type="text" name="model" style="min-width:120px" placeholder="glm-4-flash">
    <button class="btn btn-primary">保存模型设置</button>
  </form>
  <form method="post" action="/setup/llm" class="row" style="margin-bottom:12px">
    <label>知识库地址（可选，群答疑用）</label><input type="text" name="kb_url" style="flex:1;min-width:260px" placeholder="https://your-kb-gateway.example/mcp">
    <label>知识库 Key</label><input type="text" name="kb_key" style="min-width:170px" placeholder="key…">
    <button class="btn">保存知识库设置</button>
  </form>
  <form method="post" action="/setup/done" class="row" style="padding-top:8px;border-top:1px dashed var(--border)">
    <button class="btn btn-primary">✔ 完成向导，进入控制台</button>
    <span class="muted">当前为演示模式（只记日志不真正发送）；都配好后去"模型与节奏"把演示模式关掉才真正回复</span>
  </form>
</div>
{% endif %}

<!-- 最近会话 -->
<a name="recent"></a>
<div class="card">
  <div class="card-header">
    <div class="card-title">最近会话</div>
    <span class="card-subtitle">点开过的聊天都在这，直接点加，不用查微信 ID</span>
  </div>
  {% if recent %}
  <div class="row" style="margin-bottom:12px">
    <input type="text" id="recentQ" placeholder="🔍 输入昵称/群名关键字实时过滤…" oninput="filterRecent(this.value)" style="min-width:280px">
    <span class="muted" id="recentCount"></span>
  </div>
  <div class="table-responsive">
    <table id="recentT">
      <thead><tr><th style="width:28%">昵称 / 群名</th><th>微信 ID</th><th>类型</th><th style="text-align:right">操作</th></tr></thead>
      <tbody id="recentB">
      {% for r in recent %}{% set _s = ((r.display or '') ~ ' ' ~ (r.id or '')) | lower %}
      <tr data-s="{{ _s }}">
        <td><b>{{ r.display or '(空昵称)' }}</b></td>
        <td class="muted" style="font-family:Consolas,monospace">{{ r.id }}</td>
        <td><span class="badge {{ 'badge-success' if r.is_group else 'badge-neutral' }}">{{ '群' if r.is_group else '私聊' }}</span></td>
        <td style="text-align:right">
          {% if r.id in cfg_ids %}
          <span class="badge badge-neutral">已配置</span>
          {% else %}
          <form method="post" action="/cfg/pick" style="margin:0;display:inline-block">
            <input type="hidden" name="id" value="{{ r.id }}">
            <input type="hidden" name="display" value="{{ r.display or r.id }}">
            <input type="hidden" name="kind" value="{{ 'group' if r.is_group else 'contact' }}">
            <button class="btn btn-sm btn-primary">加为{{ '群' if r.is_group else '联系人' }}</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  <p class="muted" style="margin-top:8px">列表来自微信"最近聊天"，没有的会话先在微信里点开一次就会出现在这里。API 形式：<code>/api/sessions?q=关键字</code></p>
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
  {% else %}
  <p class="muted">{{ '微信未连接，连上后这里会列出最近会话' if not driver_up else '最近会话为空：先在微信里点开几个聊天' }}</p>
  {% endif %}
</div>

<!-- 私聊联系人 -->
<a name="contacts"></a>
<div class="card">
  <div class="card-header">
    <div class="card-title">私聊联系人</div>
    <span class="card-subtitle">白名单机制：只有勾选"启用"的联系人才会由 AI 自动回复</span>
  </div>
  {% for c in contacts %}
  <form method="post" action="/cfg/contact" class="row" style="border-bottom:1px dashed var(--border);padding:8px 0">
    <input type="hidden" name="idx" value="{{ loop.index0 }}">
    <label class="badge {{ 'on' if c.enabled else 'off' }}" style="min-width:68px;justify-content:center">{{ '● 启用' if c.enabled else '○ 停用' }}</label>
    <input type="checkbox" name="enabled" {{ 'checked' if c.enabled }} title="勾选启用">
    <input type="text" name="display" value="{{ c.display or '' }}" style="width:130px" placeholder="联系人昵称">
    <input type="text" name="id" value="{{ c.id or '' }}" style="flex:1;min-width:200px;font-family:Consolas,monospace" placeholder="微信号/wxid">
    <select name="persona">
      {% for p in persona_names %}<option {{ 'selected' if c.persona==p }}>{{ p }}</option>{% endfor %}
    </select>
    <button class="btn btn-sm btn-primary" name="action" value="save">保存</button>
    <button class="btn btn-sm" formaction="/contacts/mine" formmethod="post" title="读私聊历史消息，滑窗提炼该好友的长期记忆（家庭/喜好/约定），回话自动融入">🧠 提炼记忆</button>
    <button class="btn btn-sm btn-danger" name="action" value="del" onclick="return confirm('删除联系人 {{ c.display }}？')">删除</button>
  </form>
  {% endfor %}
  <form method="post" action="/cfg/contact" class="row" style="padding-top:12px">
    <input type="hidden" name="action" value="new">
    <label style="font-weight:600">新增联系人：</label>
    <input type="text" name="display" placeholder="昵称（必填）" style="width:140px">
    <input type="text" name="id" placeholder="微信号/wxid（可留空自动反查）" style="flex:1;min-width:220px">
    <select name="persona">
      {% for p in persona_names %}<option>{{ p }}</option>{% endfor %}
    </select>
    <button class="btn btn-primary">添加联系人</button>
  </form>
  <p class="muted" style="margin-top:6px">不知道微信号就先只填昵称，保存后系统自动通过微信本地反查。</p>
</div>

<!-- 人设库 -->
<a name="personas"></a>
<div class="card">
  <div class="card-header">
    <div class="card-title">人设库</div>
    <span class="card-subtitle">定制对话语气与关系背景，供私聊与群聊绑定引用</span>
  </div>
  {% for name, p in personas.items() %}
  <form method="post" action="/cfg/persona" class="row" style="border-bottom:1px dashed var(--border);padding:8px 0">
    <input type="hidden" name="old_name" value="{{ name }}">
    <b style="min-width:110px;font-size:13.5px">{{ name }}</b>
    <input type="text" name="name" value="{{ name }}" style="width:120px" title="改名会同步更新各处的引用">
    <input type="text" name="style" value="{{ p.style or '' }}" style="flex:1;min-width:260px" placeholder="说话风格（如：口语化、幽默爱接梗…）">
    <input type="text" name="background" value="{{ p.background or '' }}" style="flex:1.4;min-width:260px" placeholder="背景与关系（如：多年老同学…）">
    <button class="btn btn-sm btn-primary" name="do" value="save">保存</button>
    <button class="btn btn-sm btn-danger" name="do" value="del" onclick="return confirm('删除人设 {{ name }}？引用它的联系人将退回默认风格')">删除</button>
  </form>
  {% endfor %}
  <form method="post" action="/cfg/persona" class="row" style="padding-top:12px">
    <input type="hidden" name="old_name" value="">
    <label style="font-weight:600">新增人设：</label>
    <input type="text" name="name" placeholder="人设名称（必填）" style="width:130px">
    <input type="text" name="style" placeholder="说话风格" style="flex:1;min-width:220px">
    <input type="text" name="background" placeholder="背景与关系" style="flex:1;min-width:220px">
    <button class="btn btn-primary" name="do" value="save">添加人设</button>
  </form>
</div>

<!-- 群设置 -->
<a name="groups"></a>
<div class="card">
  <div class="card-header">
    <div class="card-title">群答疑与管理</div>
    <span class="card-subtitle">支持智能问答、知识库挂载、免@接话、进群欢迎与违禁词秩序维护</span>
  </div>
  {% if bootstrap_status.running %}
  <div class="alert alert-warning" style="margin-bottom:12px;display:flex;align-items:center;gap:10px">
    <span class="status-dot" style="background:#2563eb"></span>
    <div>
      <b>🚀 正在全量冷启动导入【{{ bootstrap_status.gname }}】：</b>
      已提炼画像 <b>{{ bootstrap_status.done_profiles }}</b> / {{ bootstrap_status.total_profiles }}，
      沉淀问答 <b>{{ bootstrap_status.done_qa }}</b> 条，挖掘历史未解诉求 <b>{{ bootstrap_status.done_pending }}</b> 条...
    </div>
  </div>
  {% endif %}
  {% for g in groups %}
  <form method="post" action="/cfg/group" style="background:var(--surface-sub);border:1px solid var(--border);border-radius:var(--radius-md);padding:14px 16px;margin:10px 0">
    <input type="hidden" name="idx" value="{{ loop.index0 }}">
    <div class="row" style="margin-bottom:8px">
      <label class="badge {{ 'on' if g.enabled else 'off' }}" style="min-width:64px;justify-content:center">{{ '● 启用' if g.enabled else '○ 停用' }}</label>
      <input type="checkbox" name="enabled" {{ 'checked' if g.enabled }} title="启用该群">
      <input type="text" name="display" value="{{ g.display or '' }}" placeholder="群名（用于搜索打开）" style="width:180px;font-weight:600">
      <input type="text" name="id" value="{{ g.id or '' }}" placeholder="群 id（可空自动匹配）" style="flex:1;min-width:220px;font-family:Consolas,monospace">
      <label>群内口吻：</label>
      <select name="persona">{% for p in persona_names %}<option {{ 'selected' if g.persona==p }}>{{ p }}</option>{% endfor %}</select>
      <button class="btn btn-sm btn-primary" formaction="/cfg/group" formmethod="post" name="do" value="save">保存设置</button>
      <button class="btn btn-sm btn-primary" formaction="/bootstrap" formmethod="post" title="一次性扫描历史消息：批量提炼群友画像、沉淀问答库、发现遗留未解问题（手机聊天记录迁移到电脑后可扫到更早历史）">🚀 全量冷启动</button>
      <input type="number" name="limit" value="1000" min="100" max="10000" step="100" style="width:74px" title="扫描最近多少条历史消息（100~10000，条数越多耗时越久）"> 条
      <button class="btn btn-sm btn-danger" formaction="/cfg/group" formmethod="post" name="do" value="del" onclick="return confirm('删除群 {{ g.display }}？')">删除群</button>
    </div>
    <div class="row" style="padding:6px 0;border-top:1px dashed var(--border)">
      <label><input type="checkbox" name="qa_enabled" {{ 'checked' if (g.qa or {}).get('enabled') }}> 启用答疑</label>
      <label><input type="checkbox" name="grounded" {{ 'checked' if (g.qa or {}).get('grounded_reply', True) }}> 带参考回复</label>
      <label><input type="checkbox" name="llm_fallback" {{ 'checked' if (g.qa or {}).get('llm_fallback') }}> 知识库断线走人设</label>
      <label><input type="checkbox" name="mine" {{ 'checked' if g.mine }}> 自动挖历史</label>
      <span class="muted" style="margin-left:auto">@机器人提问触发；自动挖历史=定时把群聊增量抽成问答</span>
    </div>
    <div class="row" style="padding:6px 0;border-top:1px dashed var(--border)">
      <label><input type="checkbox" name="no_at_reply" {{ 'checked' if g.no_at_reply }}> 免@接话</label>
      <label title="勾选后免@不再做“值不值得”智能判定，冷却一过就像群友一样自然接话，活跃气氛"><input type="checkbox" name="active" {{ 'checked' if g.active }}> 活跃分子</label>
      <label>参考上下文：<input type="number" name="no_at_context" value="{{ g.no_at_context or 20 }}" min="5" max="60" style="width:60px"> 条</label>
      <label>回复冷却：<input type="number" name="no_at_cooldown_min" value="{{ g.no_at_cooldown_min or 3 }}" step="0.1" min="0.1" style="width:60px"> 分钟</label>
      <span class="muted" style="margin-left:auto">免@=智能判断值得才接话；活跃分子=跳过判定，冷却到就接话</span>
    </div>
    <div class="row" style="padding:6px 0;border-top:1px dashed var(--border)">
      <label><input type="checkbox" name="welcome" {{ 'checked' if (g.manage or {}).get('welcome') }}> 进群欢迎语</label>
      <input type="text" name="welcome_text" value="{{ (g.manage or {}).get('welcome_text') or '' }}" style="flex:1;min-width:260px" placeholder="欢迎 {name} 入群！…（{name} 为新人昵称）">
    </div>
    <div class="row" style="padding:6px 0;border-top:1px dashed var(--border)">
      <label>违禁词拦截：</label>
      <input type="text" name="forbidden" value="{{ ((g.manage or {}).get('forbidden_words') or []) | join(', ') }}" style="flex:1;min-width:200px" placeholder="逗号分隔，如：广告, 刷单">
      <label>劝告人设：</label>
      <select name="warn_persona">{% for p in persona_names %}<option {{ 'selected' if (g.manage or {}).get('warn_persona')==p }}>{{ p }}</option>{% endfor %}</select>
      <label>升级阈值：<input type="number" name="warn_threshold" value="{{ (g.manage or {}).get('warn_threshold', 2) }}" min="1" max="9" style="width:55px"> 次</label>
      <span class="muted">满 N 次私信管理员</span>
    </div>
  </form>
  {% endfor %}
  <form method="post" action="/cfg/group" class="row" style="padding-top:10px">
    <input type="hidden" name="idx" value="-1">
    <input type="hidden" name="do" value="save">
    <label style="font-weight:600">新增群：</label>
    <input type="text" name="display" placeholder="群名（必填）" style="width:180px">
    <input type="text" name="id" placeholder="群 id（可留空，自动通过微信匹配）" style="flex:1;min-width:220px">
    <button class="btn btn-primary">添加群</button>
  </form>
  <p class="muted" style="margin-top:6px">只填群名即可：在微信里点开过该群，这里就能按名字自动匹配（群名带 emoji 也能匹配到）。</p>
</div>

<!-- 模型与节奏 -->
<a name="llm"></a>
<div class="card">
  <div class="card-header">
    <div class="card-title">模型接口与运行节奏</div>
    <span class="card-subtitle">配置 OpenAI / 智谱 / 百炼等大模型网关与防封控拟人节奏</span>
  </div>
  <form method="post" action="/cfg/llm" class="row" style="margin-bottom:12px">
    <label>接口地址</label><input type="text" name="base_url" value="{{ llm.base_url or '' }}" style="flex:1;min-width:260px" placeholder="https://api.openai.com/v1">
    <label>API Key</label><input type="password" name="api_key" value="{{ llm.api_key or '' }}" style="min-width:180px" placeholder="sk-…">
    <label>模型名称</label><input type="text" name="model" value="{{ llm.model or '' }}" style="width:140px" placeholder="glm-4-flash">
    <label>温度</label><input type="number" name="temperature" value="{{ llm.temperature or 0.8 }}" step="0.1" min="0" max="2" style="width:60px">
    <label>超时(秒)</label><input type="number" name="timeout" value="{{ llm.timeout or 60 }}" style="width:60px">
    <button class="btn btn-primary">保存模型设置</button>
  </form>
  <form method="post" action="/cfg/app" class="row" style="padding:10px 0;border-top:1px dashed var(--border)">
    <input type="hidden" name="scope" value="run">
    <label class="badge {{ 'badge-warning' if dry_run else 'badge-success' }}"><input type="checkbox" name="dry_run" {{ 'checked' if dry_run }}> 演示模式（只记日志不发微信）</label>
    <label>轮询周期：<input type="number" name="poll_sec" value="{{ reply.poll_sec or 1.5 }}" style="width:55px"> 秒</label>
    <label>合并连发：<input type="number" name="quiesce_sec" value="{{ reply.quiesce_sec or 5 }}" style="width:55px"> 秒</label>
    <label>拟人迟延：<input type="number" name="hd_min" value="{{ (reply.human_delay or [2,5])[0] }}" style="width:50px"> ~ <input type="number" name="hd_max" value="{{ (reply.human_delay or [2,5])[1] }}" style="width:50px"> 秒</label>
    <label>发送上限：<input type="number" name="max_replies" value="{{ reply.max_replies or 0 }}" style="width:65px"> 条</label>
    <button class="btn btn-primary">保存运行设置</button>
  </form>
  <form method="post" action="/cfg/app" class="row" style="padding-top:10px;border-top:1px dashed var(--border)">
    <input type="hidden" name="scope" value="admins">
    <label>管理员微信 ID：</label>
    <input type="text" name="admins" value="{{ admins | join(', ') }}" style="flex:1;min-width:300px;font-family:Consolas,monospace" placeholder="多个微信号逗号分隔">
    <button class="btn btn-primary">保存管理员</button>
    <span class="muted">管理员私聊机器人可发 <code>#状态</code> <code>#暂停</code> <code>#恢复</code> 等控制命令</span>
  </form>
</div>

<!-- 知识库 -->
<a name="kb"></a>
<div class="card">
  <div class="card-header">
    <div class="card-title">知识库（RAG 答疑引擎）</div>
    <span class="card-subtitle">通过 MCP 协议与专业知识库网关对接，群内提问精准检索</span>
  </div>
  <form method="post" action="/cfg/kb" class="row">
    <label>MCP 地址</label><input type="text" name="url" value="{{ kb.url or '' }}" style="flex:1;min-width:240px" placeholder="https://your-kb.example/mcp">
    <label>API Key</label><input type="text" name="api_key" value="{{ kb.api_key or '' }}" style="min-width:200px" placeholder="token…">
    <label>库名称</label><input type="text" name="kb" value="{{ kb.kb or '' }}" style="width:90px" placeholder="team">
    <label>top_k</label><input type="number" name="top_k" value="{{ kb.top_k or 5 }}" style="width:55px">
    <label>超时(秒)</label><input type="number" name="timeout" value="{{ kb.timeout or 60 }}" style="width:60px">
    <button class="btn btn-primary">保存知识库</button>
  </form>
  <p class="muted" style="margin-top:6px">当前 Key 若过期会在日志区提示（HTTP 401）；可在群设置中勾选"知识库断线走人设"保障兜底。</p>
</div>

<!-- 运行日志 -->
<a name="logs"></a>
<div class="card">
  <div class="card-header">
    <div class="card-title">最近运行日志</div>
    <span class="card-subtitle">记录近期消息处理、敏感词预警与发送详情</span>
  </div>
  <div class="table-responsive">
    <table>
      <thead><tr><th style="width:14%">时间</th><th style="width:12%">类型</th><th style="width:20%">会话对象</th><th>详情内容</th></tr></thead>
      <tbody>
      {% for l in logs %}
      <tr>
        <td class="muted" style="font-family:Consolas,monospace">{{ l.t }}</td>
        <td><span class="badge badge-neutral">{{ l.kind }}</span></td>
        <td><b>{{ l.chat }}</b></td>
        <td style="font-family:Consolas,monospace;font-size:12.5px">{{ l.detail }}</td>
      </tr>
      {% endfor %}
      {% if not logs %}<tr><td colspan="4" class="muted" style="text-align:center">暂无日志</td></tr>{% endif %}
      </tbody>
    </table>
  </div>
  {% if warns %}<p class="muted" style="margin-top:8px">违规记录概览：{{ warns }}</p>{% endif %}
</div>

<!-- 待沉淀问题 -->
<a name="pending"></a>
<div class="card">
  <div class="card-header">
    <div class="card-title">待沉淀问题与历史未解诉求</div>
    <span class="card-subtitle">实时群内未命中知识库的问题及冷启动挖掘的历史未解决遗留诉求，可录入知识库或安排跟进</span>
  </div>
  <div class="table-responsive">
    <table>
      <thead><tr><th style="width:60px">#</th><th style="width:20%">来源群</th><th style="width:16%">提问人</th><th>提问/未解诉求内容</th><th>上下文背景</th></tr></thead>
      <tbody>
      {% for q in pending %}
      <tr>
        <td class="muted">{{ q.id }}</td>
        <td><b>{{ q.grp }}</b></td>
        <td>{{ q.questioner }}</td>
        <td>{{ q.question }}</td>
        <td class="muted" style="font-size:12px">群答疑未命中</td>
      </tr>
      {% endfor %}
      {% for p in pending_issues %}
      <tr>
        <td class="muted">{{ p.id }}</td>
        <td><b>{{ p.grp }}</b></td>
        <td><span class="badge badge-warning">{{ p.asker }}</span></td>
        <td>{{ p.question }}</td>
        <td class="muted" style="font-size:12px">{{ p.context }}</td>
      </tr>
      {% endfor %}
      {% if not pending and not pending_issues %}<tr><td colspan="5" class="muted" style="text-align:center;padding:16px">暂无待沉淀问题与未解诉求</td></tr>{% endif %}
      </tbody>
    </table>
  </div>
</div>

<!-- 群定时任务 -->
<a name="tasks"></a>
<div class="card">
  <div class="card-header">
    <div class="card-title">群定时任务</div>
    <span class="card-subtitle">群主/管理员 @机器人 说一句即可固化，如「@机器人 每天晚上9点总结一下群里的消息」；也可说「看看定时任务」「取消第一个任务」</span>
  </div>
  <div class="table-responsive">
    <table>
      <thead><tr><th style="width:60px">#</th><th style="width:18%">来源群</th><th style="width:14%">执行时间</th><th>任务内容</th><th style="width:12%">创建人</th><th style="width:70px">操作</th></tr></thead>
      <tbody>
      {% for t in tasks %}
      <tr>
        <td class="muted">{{ t.id }}</td>
        <td><b>{{ t.grp_name or t.grp }}</b></td>
        <td style="font-family:Consolas,monospace">每天 {{ '%02d' % t.hour }}:{{ '%02d' % t.minute }}</td>
        <td>{{ t.instruction }}</td>
        <td class="muted">{{ t.created_by_name or '-' }}</td>
        <td>
          <form method="post" action="/tasks/delete" onsubmit="return confirm('删除该定时任务？')">
            <input type="hidden" name="id" value="{{ t.id }}">
            <button class="btn btn-sm btn-danger">删除</button>
          </form>
        </td>
      </tr>
      {% endfor %}
      {% if not tasks %}<tr><td colspan="6" class="muted" style="text-align:center;padding:16px">暂无定时任务，去群里 @机器人 说一句试试</td></tr>{% endif %}
      </tbody>
    </table>
  </div>
</div>

<!-- 群历史挖掘 -->
<a name="mine"></a>
<div class="card">
  <div class="card-header">
    <div class="card-title">群历史挖掘问答库</div>
    <span class="card-subtitle">AI 自动解析群聊历史，提炼沉淀为高价值 Q&A 问答对</span>
  </div>
  {% if mine_status.running %}
  <div class="alert alert-warning">
    <b>正在挖掘 {{ mine_status.group }}：{{ mine_status.done }}/{{ mine_status.total }} 条消息，已抽取 {{ mine_status.pairs }} 对问答…（每 5 秒刷新）</b>
  </div>
  {% elif mine_status.error %}
  <div class="alert alert-danger"><span class="err">上次挖掘失败：{{ mine_status.error }}</span></div>
  {% else %}
  <p class="muted" style="margin-bottom:10px">空闲状态 · 本地已累计收录 <b>{{ mined_count }}</b> 对问答</p>
  {% endif %}

  <div class="row" style="margin-bottom:12px">
    {% for g in groups %}
    <form method="post" action="/mine" style="display:inline-flex;align-items:center;gap:6px;background:var(--surface-sub);padding:5px 10px;border-radius:var(--radius-sm);border:1px solid var(--border)">
      <input type="hidden" name="idx" value="{{ loop.index0 }}">
      <b>{{ g.display or g.id }}</b>
      <button class="btn btn-sm" name="mode" value="incr">增量挖掘</button>
      <button class="btn btn-sm btn-danger" name="mode" value="full" onclick="return confirm('全量会重读整个群历史并调用模型，群很大时要跑几分钟，继续？')">全量挖掘</button>
    </form>
    {% endfor %}
    <div style="margin-left:auto;display:flex;gap:8px">
      <form method="post" action="/kb/push_mined">
        <input type="hidden" name="grp" value="{{ m_grp }}">
        <button class="btn btn-primary" title="把本地挖掘的问答对批量写入团队知识库（kb_add_qa 同问自动去重，可重复点按）">同步问答入库</button>
      </form>
      <form method="post" action="/mine/export">
        <button class="btn">导出全部为 Markdown</button>
      </form>
    </div>
  </div>

  <div class="chip-group">
    <span class="muted" style="font-size:12px;margin-right:4px">按群：</span>
    {% for l in m_group_links %}
    <a href="{{ l.href }}" class="chip {{ 'active' if l.active }} {{ 'on' if l.active }}">{{ l.name }}</a>
    {% endfor %}
  </div>

  <div class="table-responsive">
    <table>
      <thead><tr><th style="width:20%">来源群</th><th style="width:35%">提炼问题</th><th>总结回答</th></tr></thead>
      <tbody>
      {% for r in mined %}
      <tr>
        <td><b>{{ r.grp_name or r.grp }}</b></td>
        <td style="font-weight:600;color:var(--text-main)">{{ r.question }}</td>
        <td style="color:var(--text-sub);line-height:1.6">{{ r.answer }}</td>
      </tr>
      {% endfor %}
      {% if not mined %}<tr><td colspan="3" class="muted" style="text-align:center;padding:16px">还没有挖掘记录</td></tr>{% endif %}
      </tbody>
    </table>
  </div>

  <div class="pagination-wrap">
    <span class="muted">第 {{ m_page_no }} / {{ m_pages_n }} 页（共 {{ m_total }} 条）</span>
    <div class="pagination-btns">
      {% if m_prev %}<a href="{{ m_prev }}" class="btn btn-sm">← 上一页</a>{% endif %}
      {% if m_next %}<a href="{{ m_next }}" class="btn btn-sm btn-primary">下一页 →</a>{% endif %}
    </div>
  </div>
</div>

<!-- 群友画像 -->
<a name="profiles"></a>
<div class="card">
  <div class="card-header">
    <div class="card-title">群友多维画像（群 × 成员）</div>
    <span class="card-subtitle">结合群聊发言动态更新（1000字内），深度洞察意图，辅助人设融入作答</span>
  </div>
  {% if profile_status.running %}
  <div class="alert alert-warning">
    <b>正在刷新 {{ profile_status.group }}：{{ profile_status.done }}/{{ profile_status.total }} 个成员…（页面每 5 秒刷新）</b>
  </div>
  {% elif profile_status.error %}
  <div class="alert alert-danger"><span class="err">上次刷新失败：{{ profile_status.error }}</span></div>
  {% endif %}

  <div class="row" style="margin-bottom:12px">
    {% for g in groups %}
    <form method="post" action="/profiles/refresh" style="display:inline-flex;align-items:center;gap:6px;background:var(--surface-sub);padding:5px 10px;border-radius:var(--radius-sm);border:1px solid var(--border)">
      <input type="hidden" name="idx" value="{{ loop.index0 }}">
      <b>{{ g.display or g.id }}</b>
      <button class="btn btn-sm btn-primary">刷新该群画像</button>
    </form>
    {% endfor %}
    <span class="muted" style="margin-left:auto">取群活跃成员深度提炼（同一人至少间隔 1 小时重算防刷）</span>
  </div>

  <div class="row" style="margin-bottom:12px;gap:8px;align-items:center;background:var(--surface-sub);padding:8px 12px;border-radius:var(--radius-sm);border:1px solid var(--border)">
    <b style="white-space:nowrap">备份与迁移</b>
    <span class="muted" style="font-size:12px">换号迁移画像/记忆/问答，或整机备份（含配置）</span>
    <form method="get" action="/knowledge/export" style="margin-left:auto;display:inline-flex;align-items:center;gap:6px">
      <label style="font-size:12px;display:inline-flex;align-items:center;gap:4px" title="勾选后压缩包内含 bot_config.yaml 原文（含 API 密钥，注意保管）"><input type="checkbox" name="cfg" value="1">含配置</label>
      <button class="btn btn-sm btn-primary">⬇ 导出全部（ZIP）</button>
    </form>
    <form method="post" action="/knowledge/import" enctype="multipart/form-data" style="display:inline-flex;align-items:center;gap:6px">
      <input type="file" name="file" accept=".zip,.json" required style="font-size:12px;max-width:200px">
      <label style="font-size:12px;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" name="overwrite" value="1">覆盖已有</label>
      <label style="font-size:12px;display:inline-flex;align-items:center;gap:4px" title="文件里带配置时才会恢复"><input type="checkbox" name="restore_cfg" value="1">恢复配置</label>
      <button class="btn btn-sm">⬆ 导入</button>
    </form>
  </div>

  <div class="chip-group">
    <span class="muted" style="font-size:12px;margin-right:4px">按群：</span>
    {% for l in p_group_links %}
    <a href="{{ l.href }}" class="chip {{ 'active' if l.active }} {{ 'on' if l.active }}">{{ l.name }}</a>
    {% endfor %}
  </div>

  <div class="table-responsive">
    <table>
      <thead><tr><th style="width:18%">群名</th><th style="width:18%">成员昵称</th><th>用户画像与意图特征（1000字以内）</th><th style="width:120px;text-align:right">更新时间</th></tr></thead>
      <tbody>
      {% for p in member_profiles %}
      <tr>
        <td><b>{{ p.grp_name }}</b></td>
        <td><span class="badge badge-neutral" style="font-weight:600">{{ p.nickname }}</span></td>
        <td style="line-height:1.6;color:var(--text-sub)">{{ p.profile }}</td>
        <td class="muted" style="text-align:right;font-family:Consolas,monospace">{{ p.updated_t }}</td>
      </tr>
      {% endfor %}
      {% if not member_profiles %}<tr><td colspan="4" class="muted" style="text-align:center;padding:16px">还没有画像：群答疑后自动生成，或点上面"刷新该群画像"</td></tr>{% endif %}
      </tbody>
    </table>
  </div>

  <div class="pagination-wrap">
    <span class="muted">第 {{ p_page_no }} / {{ p_pages_n }} 页（共 {{ p_total }} 条）</span>
    <div class="pagination-btns">
      {% if p_prev %}<a href="{{ p_prev }}" class="btn btn-sm">← 上一页</a>{% endif %}
      {% if p_next %}<a href="{{ p_next }}" class="btn btn-sm btn-primary">下一页 →</a>{% endif %}
    </div>
  </div>
</div>

<!-- YAML 配置 -->
<a name="yaml"></a>
<div class="card">
  <details>
    <summary>⚙ 高级：YAML 配置文件源码（供 AI 诊断与开发者深度调试，网页保存会去除注释）</summary>
    <form method="post" action="/config" style="margin-top:12px">
      <textarea name="cfg" spellcheck="false">{{ cfg_text }}</textarea>
      <div style="margin-top:8px">
        <button class="btn btn-primary">保存并应用 YAML</button>
      </div>
    </form>
  </details>
</div>

</div>
<script>
(function(){
  var KEY='wxbot-collapsed';
  var st={};
  try{st=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
  var cards=document.querySelectorAll('.card');
  cards.forEach(function(c,i){
    var h=c.querySelector('.card-header');
    if(!h)return;
    if(st[i])c.classList.add('collapsed');
    h.addEventListener('click',function(e){
      if(e.target.closest('button,input,a,select,textarea,label,form'))return;
      c.classList.toggle('collapsed');
      st[i]=c.classList.contains('collapsed');
      try{localStorage.setItem(KEY,JSON.stringify(st))}catch(err){}
    });
  });
})();
</script>
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
            inner._kb_writer = None
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

    def _render(form_error="", candidates=(), p_grp="", p_page=1, m_grp="", m_page=1):
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
        grp_names = {g.get("id"): (g.get("display") or g.get("id") or "")
                     for g in snap.get("groups") or []}

        def _int(v, default=1):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        def _paged(total, page):
            pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
            return min(max(1, page), pages), pages

        def _href(**over):
            q = {"p_grp": p_grp, "p_page": p_page, "m_grp": m_grp, "m_page": m_page}
            q.update(over)
            return "/?" + "&".join(f"{k}={v}" for k, v in q.items() if v not in ("", 1))

        def _group_links(cur, which):
            links = [{"name": "全部", "active": not cur,
                      "href": _href(**{which + "_grp": "", which + "_page": 1})}]
            for g in snap.get("groups") or []:
                gid = g.get("id") or ""
                if gid:
                    links.append({"name": g.get("display") or gid, "active": gid == cur,
                                  "href": _href(**{which + "_grp": gid, which + "_page": 1})})
            return links

        p_grp, m_grp = str(p_grp or ""), str(m_grp or "")
        p_page, p_pages = _paged(bot.store.profiles_count(p_grp or None), _int(p_page))
        m_page, m_pages = _paged(bot.store.mined_count(m_grp or None), _int(m_page))
        try:
            profs = bot.store.profiles(p_grp or None, limit=_PAGE_SIZE,
                                       offset=(p_page - 1) * _PAGE_SIZE)
        except Exception as e:
            log.warning("画像读取失败: %s", e)
            profs = []
        for p in profs:
            p["grp_name"] = grp_names.get(p.get("grp")) or p.get("grp") or ""
            p["updated_t"] = time.strftime("%m-%d %H:%M",
                                           time.localtime(p.get("updated") or 0))
        mined = bot.store.mined_pairs(m_grp or None, limit=_PAGE_SIZE,
                                      offset=(m_page - 1) * _PAGE_SIZE)
        for r in mined:
            r["grp_name"] = grp_names.get(r.get("grp")) or r.get("grp") or ""
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
            pending_issues=bot.store.pending_issues(limit=20),
            tasks=bot.store.list_tasks(),
            bootstrap_status=bootstrap.status(),
            mine_status=mining.status(), mined=mined, mined_count=bot.store.mined_count(),
            m_group_links=_group_links(m_grp, "m"), m_page_no=m_page, m_pages_n=m_pages,
            m_total=bot.store.mined_count(m_grp or None),
            m_prev=(_href(m_page=m_page - 1) if m_page > 1 else ""),
            m_next=(_href(m_page=m_page + 1) if m_page < m_pages else ""),
            member_profiles=profs, profile_status=profiles_mod.status(),
            p_group_links=_group_links(p_grp, "p"), p_page_no=p_page, p_pages_n=p_pages,
            p_total=bot.store.profiles_count(p_grp or None),
            p_prev=(_href(p_page=p_page - 1) if p_page > 1 else ""),
            p_next=(_href(p_page=p_page + 1) if p_page < p_pages else ""),
            cfg_text=yaml.safe_dump(snap, allow_unicode=True, sort_keys=False))

    @app.route("/")
    def index():
        page = _render(p_grp=request.args.get("p_grp", ""),
                       p_page=request.args.get("p_page", 1),
                       m_grp=request.args.get("m_grp", ""),
                       m_page=request.args.get("m_page", 1))
        _flash["msg"] = ""
        return page

    @app.post("/tasks/delete")
    def tasks_delete():
        tid = request.form.get("id", type=int) or 0
        if tid:
            bot.store.remove_task(tid)
            _flash["msg"] = f"已删除定时任务 #{tid}"
        return redirect("/")

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
        data["no_at_reply"] = bool(f.get("no_at_reply"))
        data["active"] = bool(f.get("active"))
        for key, cast in (("no_at_context", int), ("no_at_cooldown_min", float)):
            raw = (f.get(key) or "").strip()
            if raw:
                try:
                    data[key] = cast(raw)
                except ValueError:
                    pass
        if idx < 0:
            groups.append(data)
        elif idx < len(groups):
            old = groups[idx]
            data["manage"]["mention_names"] = (old.get("manage") or {}).get("mention_names") or []
            for key in ("no_at_context", "no_at_cooldown_min"):
                if key not in data and old.get(key) is not None:
                    data[key] = old[key]
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
                          "no_at_reply": False,
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

    @app.post("/profiles/refresh")
    def profiles_refresh():
        if not getattr(bot, "driver", None):
            return _render(form_error="微信未连接，无法刷新画像")
        try:
            g = (bot.cfg.data.get("groups") or [])[int(request.form.get("idx"))]
        except (TypeError, ValueError, IndexError):
            return _render(form_error="参数错误")
        inner = getattr(bot, "_bot", None) or bot
        ok = profiles_mod.start_refresh(inner, g.get("id") or "",
                                        g.get("display") or g.get("id") or "")
        _flash["msg"] = (f"已开始刷新 {g.get('display')} 的群友画像" if ok
                         else "已有画像任务在跑或微信/模型未就绪，稍后再试")
        return redirect("/")

    @app.post("/bootstrap")
    def do_bootstrap():
        if not getattr(bot, "driver", None):
            return _render(form_error="微信未连接，无法执行冷启动导入")
        try:
            g = (bot.cfg.data.get("groups") or [])[int(request.form.get("idx"))]
        except (TypeError, ValueError, IndexError):
            return _render(form_error="参数错误")
        inner = getattr(bot, "_bot", None) or bot
        limit = min(10000, max(100, int(request.form.get("limit") or 1000)))
        ok = bootstrap.start_bootstrap_async(inner, g.get("id") or "",
                                             g.get("display") or g.get("id") or "",
                                             limit=limit)
        _flash["msg"] = (f"已启动群【{g.get('display')}】历史消息全量冷启动导入（扫描最近 {limit} 条）" if ok
                         else "已有冷启动任务在运行中，请稍候再试")
        return redirect("/")

    @app.route("/knowledge/export")
    def knowledge_export():
        cfg_yaml = ""
        if request.args.get("cfg"):
            try:
                with open(bot.cfg.path, encoding="utf-8") as fh:
                    cfg_yaml = fh.read()
            except OSError as e:
                log.warning("读取配置用于备份失败: %s", e)
        data = bot.store.export_knowledge(
            source_wxid=(bot.cfg.data.get("app") or {}).get("self_wxid") or "")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("knowledge.json", json.dumps(data, ensure_ascii=False, indent=1))
            if cfg_yaml:
                z.writestr("bot_config.yaml", cfg_yaml)
        fname = quote(time.strftime("wxbot_backup_%Y%m%d_%H%M%S.zip"))
        return Response(buf.getvalue(), mimetype="application/zip",
                        headers={"Content-Disposition":
                                 f"attachment; filename*=UTF-8''{fname}"})

    @app.post("/knowledge/import")
    def knowledge_import():
        f = request.files.get("file")
        overwrite = bool(request.form.get("overwrite"))
        restore_cfg = bool(request.form.get("restore_cfg"))
        try:
            raw = f.read() if f else b""
            if raw[:2] == b"PK":
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    names = set(z.namelist())
                    if "knowledge.json" not in names:
                        raise ValueError("压缩包里没有 knowledge.json（请上传 wxbot 导出的备份包）")
                    data = json.loads(z.read("knowledge.json").decode("utf-8"))
                    cfg_yaml = (z.read("bot_config.yaml").decode("utf-8")
                                if "bot_config.yaml" in names else "")
            else:
                data = json.loads(raw.decode("utf-8"))
                cfg_yaml = str((data or {}).get("config_yaml") or "")
            if not isinstance(data, dict):
                raise ValueError("文件内容不是有效的导出数据（JSON 或 ZIP）")
            counts = bot.store.import_knowledge(data, overwrite=overwrite)
            cfg_restored = False
            if restore_cfg and cfg_yaml:
                with open(bot.cfg.path, "w", encoding="utf-8") as fh:
                    fh.write(cfg_yaml)
                bot.cfg.reload()
                _hot_apply()
                cfg_restored = True
        except Exception as e:
            _flash["msg"] = f"导入失败：{html.escape(str(e))}"
            return redirect("/")
        msg = (f"导入完成（文件 {f.filename or '?'}，{len(raw) / 1024:.0f} KB）："
               f"群友画像 {counts.get('profiles', 0)} 条、"
               f"私聊记忆 {counts.get('memories', 0)} 条、"
               f"问答沉淀 {counts.get('mined_qa', 0)} 条、"
               f"待沉淀问题 {counts.get('qa_pending', 0)} 条、"
               f"未解诉求 {counts.get('pending_issues', 0)} 条"
               "（已存在且未勾选覆盖的条目跳过）")
        if restore_cfg and not cfg_restored:
            msg += "；文件里没有配置内容，配置未恢复"
        elif cfg_restored:
            msg += "；配置已恢复并生效"
        _flash["msg"] = msg
        return redirect("/")

    @app.post("/contacts/mine")
    def contacts_mine():
        if not getattr(bot, "driver", None):
            return _render(form_error="微信未连接，无法提炼记忆")
        try:
            c = (bot.cfg.data.get("contacts") or [])[int(request.form.get("idx"))]
        except (TypeError, ValueError, IndexError):
            return _render(form_error="参数错误")
        inner = getattr(bot, "_bot", None) or bot
        ok = bootstrap.start_contact_mining_async(
            inner, c.get("display") or c.get("id") or "", limit=2000)
        _flash["msg"] = (f"已开始提炼【{c.get('display') or c.get('id')}】的私聊长期记忆"
                         "（后台运行，完成后自动生效）" if ok
                         else "已有提炼任务在跑，稍后再试")
        return redirect("/")

    @app.post("/kb/push_mined")
    def kb_push_mined():
        """把本地挖掘问答库批量写入团队知识库（kb_add_qa，同问自动去重）。"""
        inner = getattr(bot, "_bot", None) or bot
        grp = (request.form.get("grp") or "").strip()
        try:
            limit = min(2000, max(1, int(request.form.get("limit") or 1000)))
        except ValueError:
            limit = 1000
        rows = inner.store.mined_pairs(grp=grp or None, limit=limit)
        ok = fail = 0
        for r in rows:
            try:
                inner.kb_writer.add_qa(r.get("question") or "", r.get("answer") or "",
                                       context="来源群：" + (r.get("grp") or grp or ""))
                ok += 1
            except Exception:
                fail += 1
        msg = f"问答入库：成功 {ok} 条"
        if fail:
            msg += f"，失败 {fail} 条（网关正常后可重按，同问自动去重不会重复）"
        _flash["msg"] = msg
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
