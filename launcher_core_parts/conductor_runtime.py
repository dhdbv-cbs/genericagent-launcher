from __future__ import annotations

import os

from .runtime import launcher_data_path

_CONDUCTOR_SCRIPT_TEXT = r'''import os, sys, re, time, json, uuid, queue, asyncio, threading, builtins
from dataclasses import dataclass, field
from typing import Dict, Optional, List

# Silence print() from subagent threads (they share stdout with conductor)
_original_print = builtins.print
def _filtered_print(*args, **kwargs):
    t = threading.current_thread()
    if t.name.startswith('subagent-'):
        return
    return _original_print(*args, **kwargs)
builtins.print = _filtered_print

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse
from pydantic import BaseModel

AGENT_DIR = os.path.abspath(os.environ.get("GA_LAUNCHER_AGENT_DIR") or os.getcwd())
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from agentmain import GenericAgent

HOST = str(os.environ.get("GA_LAUNCHER_CONDUCTOR_HOST") or "127.0.0.1").strip() or "127.0.0.1"
try:
    PORT = int(os.environ.get("GA_LAUNCHER_CONDUCTOR_PORT") or 8900)
except Exception:
    PORT = 8900
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conductor.html")

app = FastAPI(title="Conductor")

class ChatIn(BaseModel):
    msg: str
    role: str = "conductor"  # conductor | system | user

class StartSubagentIn(BaseModel):
    prompt: str

class SubagentActionIn(BaseModel):
    action: str = "intervene"  # intervene | abort | kill
    msg: str = ""

@dataclass
class SubAgentState:
    id: str
    agent: GenericAgent
    prompt: str
    reply: str = ""
    status: str = "running"  # running | stopped | failed | aborted
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_done: str = ""
    monitor_threads: List[threading.Thread] = field(default_factory=list)

subagents: Dict[str, SubAgentState] = {}
sub_lock = threading.RLock()
ws_clients: set[WebSocket] = set()
main_loop: Optional[asyncio.AbstractEventLoop] = None
conductor_events: "queue.Queue[dict]" = queue.Queue()
conductor_agent: Optional[GenericAgent] = None
conductor_started = False
conductor_error = ""
chat_messages: List[dict] = []

def now_ms() -> int:
    return int(time.time() * 1000)

def short_id() -> str:
    return uuid.uuid4().hex[:8]

_TURN_SPLIT_RE = re.compile(r'\**LLM Running \(Turn \d+\) \.\.\.\**')
_SUMMARY_RE = re.compile(r'<summary>(.*?)</summary>\s*', re.DOTALL)

def extract_last_summary(full: str) -> str:
    matches = _SUMMARY_RE.findall(full or "")
    if not matches:
        return ""
    s = matches[-1].strip()
    return s[-1000:] if len(s) > 1000 else s

def extract_last_text_reply(full: str) -> str:
    parts = _TURN_SPLIT_RE.split(full)
    last = parts[-1] if parts else full
    last = _SUMMARY_RE.sub('', last)
    last = re.sub(r'\[(Status|Info)\][^\n]*\n?', '', last)
    last = last.strip()
    return last[-3000:] if len(last) > 3000 else last

def subagent_snapshot() -> list[dict]:
    with sub_lock:
        return [
            {
                "id": s.id,
                "prompt": s.prompt,
                "reply": (extract_last_summary(s.reply) if s.status == "running" else extract_last_text_reply(s.reply)) if s.reply else "",
                "status": s.status,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in subagents.values()
            if s.status != "aborted"
        ]

def schedule_broadcast(payload: dict):
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast(payload), main_loop)

async def broadcast(payload: dict):
    dead = []
    for ws in list(ws_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)

def push_cards():
    schedule_broadcast({"type": "subagents", "items": subagent_snapshot()})

def add_chat(msg: str, role: str = "conductor"):
    item = {"id": short_id(), "role": role, "msg": msg, "ts": now_ms(), "read": role != "user"}
    chat_messages.append(item)
    if len(chat_messages) > 200:
        del chat_messages[:-200]
    schedule_broadcast({"type": "chat", "item": item})
    return item

def mark_all_user_messages_read():
    changed = False
    for item in chat_messages:
        if item.get("role") == "user" and not item.get("read"):
            item["read"] = True
            changed = True
    if changed:
        schedule_broadcast({"type": "chat_read"})
    return changed

def _short_error_text(exc: Exception) -> str:
    text = " ".join(str(exc or "").strip().split())
    return text[:240] if len(text) > 240 else text

def _ensure_conductor_agent_started() -> bool:
    global conductor_agent, conductor_started, conductor_error
    if conductor_agent is not None:
        return True
    prev_error = str(conductor_error or "").strip()
    try:
        agent = GenericAgent()
        agent.inc_out = True
        start_agent_runner(agent, "conductor-agent")
        conductor_agent = agent
        conductor_started = True
        conductor_error = ""
        if prev_error:
            add_chat("Conductor 总管已恢复，可以继续处理消息。", role="system")
        return True
    except Exception as e:
        conductor_agent = None
        conductor_started = False
        conductor_error = _short_error_text(e)
        if conductor_error and conductor_error != prev_error:
            add_chat(f"⚠ Conductor 总管未就绪：{conductor_error}", role="system")
        return False

def start_agent_runner(agent: GenericAgent, name: str):
    t = threading.Thread(target=agent.run, name=name, daemon=True)
    t.start()
    return t

def monitor_display_queue(agent_id: str, dq: "queue.Queue", trigger_when_done: bool):
    acc = ""
    while True:
        item = dq.get()
        if "next" in item:
            chunk = item.get("next") or ""
            acc += chunk
            with sub_lock:
                s = subagents.get(agent_id)
                if s:
                    s.reply = acc
                    s.status = "running"
                    s.updated_at = time.time()
            push_cards()
        if "done" in item:
            done = item.get("done") or acc
            with sub_lock:
                s = subagents.get(agent_id)
                if s:
                    s.reply = done
                    s.last_done = done
                    if s.status != "aborted":
                        s.status = "stopped"
                    s.updated_at = time.time()
            push_cards()
            if trigger_when_done:
                conductor_events.put({"type": "subagent_done", "id": agent_id, "reply": done})
            break

def start_subagent(prompt: str) -> dict:
    sid = short_id()
    agent = GenericAgent()
    agent.inc_out = True
    agent.verbose = False

    start_agent_runner(agent, f"subagent-{sid}")
    state = SubAgentState(id=sid, agent=agent, prompt=prompt, status="running")
    with sub_lock:
        subagents[sid] = state
    dq = agent.put_task(prompt, source=f"subagent:{sid}")
    mt = threading.Thread(target=monitor_display_queue, args=(sid, dq, True), name=f"monitor-{sid}", daemon=True)
    mt.start()
    state.monitor_threads.append(mt)
    push_cards()
    return {"id": sid, "status": "running"}

def keyinfo_subagent(sid: str, msg: str) -> dict:
    with sub_lock:
        s = subagents.get(sid)
    if not s:
        return {"error": "subagent not found", "id": sid}
    h = s.agent.handler
    h.working['key_info'] = h.working.get('key_info', '') + f"\n[MASTER] {msg}"
    s.updated_at = time.time()
    return {"id": sid, "status": "keyinfo_injected"}

def input_subagent(sid: str, msg: str) -> dict:
    with sub_lock:
        s = subagents.get(sid)
    if not s:
        return {"error": "subagent not found", "id": sid}
    if s.status == "running":
        return {"error": "subagent is still running, cannot input/reply. Start a new subagent instead.", "id": sid}
    s.prompt = msg
    s.reply = ""
    s.status = "running"
    s.updated_at = time.time()
    dq = s.agent.put_task(msg, source=f"subagent:{sid}")
    mt = threading.Thread(target=monitor_display_queue, args=(sid, dq, True), name=f"monitor-{sid}", daemon=True)
    mt.start()
    s.monitor_threads.append(mt)
    push_cards()
    return {"id": sid, "status": "running"}

def conductor_readme() -> str:
    base = f"http://{HOST}:{PORT}"
    return "\n".join([
        f"Conductor API\tBase: {base}",
        "",
        "POST /chat\tbody: {\"msg\": \"...\"}\t给用户发消息",
        "POST /subagent\tbody: {\"prompt\": \"...\"}\t启动新subagent，返回 {\"id\": \"xxx\"}",
        'POST /subagent/{id}\tbody: {\"action\": \"keyinfo\", \"msg\": \"...\"}\t注入key_info（agent下轮可见）',
        'POST /subagent/{id}\tbody: {\"action\": \"input\", \"msg\": \"...\"}\t开新一轮任务（agent停下后追加）',
        'POST /subagent/{id}\tbody: {\"action\": \"stop\"}\t中断执行但保留（可继续input/reply）',
        'POST /subagent/{id}\tbody: {\"action\": \"kill\"}\t彻底杀死（从卡片消失，不可复用）',
        "GET /chat?last=N\t返回最近N条对话（默认20）",
        "GET /subagent\t返回 {\"items\": [...]}\t查看所有subagent状态",
        "GET /readme\t本文档",
        "",
        "触发时机: 用户新消息 | subagent done",
    ])

def conductor_prompt_from_events(events: list) -> str:
    with sub_lock:
        running = sum(1 for s in subagents.values() if s.status == "running")
        stopped = sum(1 for s in subagents.values() if s.status != "running")
    unread = sum(1 for m in chat_messages if m.get("role") == "user" and not m.get("read"))
    done_count = sum(1 for e in events if e.get("type") == "subagent_done")
    summary = f"subagents: {running} running, {stopped} stopped | {unread}条用户未读消息, {done_count}个subagent完成报告"
    base = f"http://{HOST}:{PORT}"
    return f"""你是agent总管。用户只和你对话，你负责调度、验收、交付，目标是降低用户管理多个agent的负担。
API: {base}；先requests，GET /readme查用法，GET /chat读未读对话，GET /subagent看状态；POST /chat是唯一对用户说话方式。

铁律：
- 绝不亲自执行任务/探测环境；一切执行交给subagent。你只分析、派遣、审查、沟通。
- 每次唤醒只做最小必要动作（发消息/开subagent/reply/keyinfo/abort），做完立刻停，等待下次事件唤醒。
- 改写prompt时严禁添加用户未提及的假设、工具、前提条件。只能精炼/结构化用户原意，不能脑补，只能做很小的改写

用户消息流程：
1. 结合记忆、上下文和用户偏好判断真实需求；不清楚/不能代劳时，用精简checklist一次性问用户。
2. 判断是新任务还是延续现有任务；优先复用已有stopped subagent（用input追加），只有确实无关的新任务才新建。
3. 分派前必须POST /chat告知用户：改写后的prompt + 分派方案（新建/复用哪个subagent）。
4. 执行分派，完成即停。危险操作（改源码/删数据/安全敏感）必须改成先让subagent出方案；你验收后POST /chat请用户确认，确认后才继续执行。

subagent完成流程：
1. 读subagent输出；若最后一条不足以判断，GET /subagent或日志补足信息。
2. 预测用户是否满意；不满意就reply/keyinfo要求返工、修改、优化，继续监督，不急着报告。
3. 预计用户满意后，POST /chat给简洁交付报告。

原则：
- 信任subagent足够聪明，不要写具体步骤和容易探测的信息；能自己判断的自己判断，只在真正需要用户决策时打扰。
{summary}"""

def _auto_cleanup_loop():
    IDLE_TIMEOUT = 3600
    while True:
        time.sleep(300)
        now = time.time()
        to_abort = []
        with sub_lock:
            for sid, s in subagents.items():
                if s.status == "stopped" and (now - s.updated_at) > IDLE_TIMEOUT:
                    to_abort.append((sid, s))
        for sid, s in to_abort:
            s.agent.abort()
            with sub_lock:
                s.status = "aborted"
                s.updated_at = now
        if to_abort:
            push_cards()

def monitor_conductor_queue(dq: "queue.Queue") -> str:
    while True:
        item = dq.get()
        if "done" in item:
            print("Conductor task done")
            return item.get("done", "") or ""

def conductor_loop():
    threading.Thread(target=_auto_cleanup_loop, name="subagent-cleanup", daemon=True).start()
    while True:
        first = conductor_events.get()
        conductor_events.task_done()
        time.sleep(0.3)
        events = [first]
        while not conductor_events.empty():
            try:
                events.append(conductor_events.get_nowait())
                conductor_events.task_done()
            except Exception:
                break
        try:
            if not _ensure_conductor_agent_started():
                continue
            if all(str((event or {}).get("type") or "").strip().lower() == "startup" for event in events):
                continue
            if any(str((event or {}).get("type") or "").strip().lower() == "user_message" for event in events):
                mark_all_user_messages_read()
            prompt = conductor_prompt_from_events(events)
            dq = conductor_agent.put_task(prompt, source="conductor")
            done_text = monitor_conductor_queue(dq)
            tail = (done_text or '')[-1000:]
            if '!!!Error:' in tail:
                last = chat_messages[-1] if chat_messages else None
                if not (last and last.get('role') == 'system' and last.get('msg', '').startswith('⚠ LLM')):
                    err = next((l for l in reversed(tail.splitlines()) if l.startswith('!!!Error:')), '')
                    add_chat(f"⚠ LLM 暂不可用：{err[:200]}", role="system")
        except Exception as e:
            add_chat(f"Conductor error: {e}", role="system")

@app.on_event("startup")
async def on_startup():
    global main_loop
    main_loop = asyncio.get_running_loop()
    threading.Thread(target=conductor_loop, name="conductor-loop", daemon=True).start()
    conductor_events.put({"type": "startup"})

@app.get("/")
def index():
    return FileResponse(HTML_PATH)

@app.get("/readme")
def readme():
    return PlainTextResponse(conductor_readme())

@app.get("/health")
def health():
    return {
        "ok": bool(conductor_agent is not None and conductor_started and not str(conductor_error or "").strip()),
        "conductor_started": bool(conductor_started),
        "conductor_ready": bool(conductor_agent is not None),
        "error": str(conductor_error or "").strip(),
        "unread_user_count": sum(1 for m in chat_messages if m.get("role") == "user" and not m.get("read")),
        "subagent_count": len(subagent_snapshot()),
        "agent_dir": AGENT_DIR,
    }

@app.get("/subagent")
def list_subagents():
    return {"items": subagent_snapshot()}

@app.post("/subagent")
def api_start_subagent(body: StartSubagentIn):
    result = start_subagent(body.prompt)
    result["instruction"] = "Task received. I'll handle it from here. You MUST stop now and end your reply. Wait for next event."
    return result

@app.post("/subagent/{sid}")
def api_subagent_action(sid: str, body: SubagentActionIn):
    with sub_lock:
        s = subagents.get(sid)
    if not s:
        return JSONResponse({"error": "subagent not found", "id": sid}, status_code=404)
    action = body.action.lower().strip()
    if action == "keyinfo":
        result = keyinfo_subagent(sid, body.msg)
        result["instruction"] = "Received. I'll incorporate this. You MUST stop now and end your reply."
        return result
    if action in ("input", "reply", "append", "message", "msg"):
        result = input_subagent(sid, body.msg)
        result["instruction"] = "Task received. I'll handle it from here. You MUST stop now and end your reply."
        return result
    if action in ("abort", "stop"):
        s.agent.abort()
        s.status = "stopped"
        s.updated_at = time.time()
        push_cards()
        return {"id": sid, "status": "stopped"}
    if action == "kill":
        s.agent.abort()
        s.status = "aborted"
        s.updated_at = time.time()
        push_cards()
        return {"id": sid, "status": "aborted"}
    return JSONResponse({"error": f"unknown action: {body.action}"}, status_code=400)

@app.get("/chat")
def api_get_chat(last: int = 20):
    mark_all_user_messages_read()
    return {"items": chat_messages[-last:]}

@app.post("/chat")
def api_chat(body: ChatIn):
    return add_chat(body.msg, role=body.role)

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        await ws.send_json({"type": "hello", "subagents": subagent_snapshot(), "chat": chat_messages})
        while True:
            data = await ws.receive_json()
            msg = (data.get("msg") or "").strip()
            if not msg:
                continue
            add_chat(msg, role="user")
            conductor_events.put({"type": "user_message", "msg": msg})
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)

if __name__ == "__main__":
    import uvicorn, webbrowser
    threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    uvicorn.run("conductor:app", host=HOST, port=PORT, reload=False)
'''

_CONDUCTOR_HTML_TEXT = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Conductor</title>
  <style>
    :root {
      --bg: #f4f6fa;
      --panel: rgba(255,255,255,.82);
      --line: rgba(27,39,59,.10);
      --text: #162033;
      --muted: #6f7b8e;
      --accent: #2f6fed;
      --accent-soft: rgba(47,111,237,.12);
      --green: #169c6b;
      --amber: #d88a1d;
      --gray: #8d97a8;
      --red: #d95c5c;
      --shadow: 0 16px 42px rgba(26, 39, 61, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--text);
      background:
        radial-gradient(720px 520px at 10% 0%, rgba(47,111,237,.11), transparent 60%),
        radial-gradient(640px 460px at 100% 0%, rgba(22,156,107,.08), transparent 58%),
        linear-gradient(180deg, #f7f9fc, var(--bg));
    }
    .app {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 380px minmax(0, 1fr);
      gap: 18px;
      padding: 18px;
    }
    .panel {
      min-height: 0;
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--panel);
      backdrop-filter: blur(18px);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      padding: 18px 20px 14px;
      border-bottom: 1px solid var(--line);
    }
    .title { font-size: 17px; font-weight: 700; }
    .hint { font-size: 12px; color: var(--muted); }
    .side-body, .chat-body {
      min-height: 0;
      overflow: auto;
    }
    .side-body { padding: 14px; display: flex; flex-direction: column; gap: 12px; }
    .card {
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px;
      background: rgba(255,255,255,.78);
    }
    .card.running { box-shadow: inset 3px 0 0 var(--green); }
    .card.stopped { box-shadow: inset 3px 0 0 var(--gray); }
    .card.failed { box-shadow: inset 3px 0 0 var(--red); }
    .status {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
      font-size: 12px;
      color: var(--muted);
    }
    .prompt {
      font-size: 13px;
      line-height: 1.45;
      margin-bottom: 10px;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .reply {
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid rgba(27,39,59,.08);
      background: rgba(244,246,250,.9);
      font-size: 12px;
      line-height: 1.45;
      color: #42506a;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 160px;
      overflow: auto;
    }
    .chat {
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .chat-body {
      flex: 1;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .bubble {
      max-width: min(76%, 760px);
      padding: 12px 14px;
      border-radius: 18px;
      line-height: 1.45;
      font-size: 14px;
      white-space: pre-wrap;
      word-break: break-word;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.88);
    }
    .bubble.user {
      align-self: flex-end;
      background: linear-gradient(180deg, rgba(47,111,237,.94), rgba(47,111,237,.84));
      color: #fff;
      border-color: transparent;
    }
    .bubble.system {
      background: rgba(244,246,250,.95);
      color: #4d5a71;
    }
    .composer {
      border-top: 1px solid var(--line);
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      padding: 16px 18px 18px;
    }
    textarea {
      width: 100%;
      min-height: 72px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px 14px;
      font: inherit;
      color: inherit;
      background: rgba(255,255,255,.9);
      outline: none;
    }
    textarea:focus {
      border-color: rgba(47,111,237,.35);
      box-shadow: 0 0 0 4px var(--accent-soft);
    }
    button {
      align-self: end;
      min-width: 104px;
      height: 44px;
      border: 0;
      border-radius: 14px;
      background: var(--accent);
      color: #fff;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }
    button:disabled {
      cursor: default;
      background: #9fb4e8;
    }
    .empty {
      color: var(--muted);
      padding: 8px 2px;
      font-size: 13px;
    }
    @media (max-width: 960px) {
      .app { grid-template-columns: 1fr; }
      .panel { min-height: 340px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <section class="panel">
      <div class="head">
        <div class="title">子 Agent</div>
        <div class="hint" id="sideHint">0 个任务</div>
      </div>
      <div class="side-body" id="cards"></div>
    </section>
    <section class="panel chat">
      <div class="head">
        <div class="title">Conductor</div>
        <div class="hint" id="connHint">连接中</div>
      </div>
      <div class="chat-body" id="chat"></div>
      <div class="composer">
        <textarea id="input" placeholder="直接给总管发消息，总管会调度子 Agent。"></textarea>
        <button id="sendBtn">发送</button>
      </div>
    </section>
  </div>
  <script>
    const cardsEl = document.getElementById('cards');
    const chatEl = document.getElementById('chat');
    const inputEl = document.getElementById('input');
    const sendBtn = document.getElementById('sendBtn');
    const connHintEl = document.getElementById('connHint');
    const sideHintEl = document.getElementById('sideHint');
    let ws = null;
    let reconnectTimer = null;
    let messages = [];

    function scrollBottom() {
      requestAnimationFrame(() => { chatEl.scrollTop = chatEl.scrollHeight; });
    }

    function bubbleClass(role) {
      if (role === 'user') return 'bubble user';
      if (role === 'system') return 'bubble system';
      return 'bubble';
    }

    function renderChat() {
      chatEl.innerHTML = '';
      if (!messages.length) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = '还没有消息。';
        chatEl.appendChild(empty);
        return;
      }
      for (const item of messages) {
        const div = document.createElement('div');
        div.className = bubbleClass(item.role);
        div.textContent = item.msg || '';
        chatEl.appendChild(div);
      }
      scrollBottom();
    }

    function renderCards(items) {
      cardsEl.innerHTML = '';
      sideHintEl.textContent = `${items.length} 个任务`;
      if (!items.length) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = '当前没有子 Agent 任务。';
        cardsEl.appendChild(empty);
        return;
      }
      for (const item of items) {
        const card = document.createElement('div');
        card.className = `card ${item.status || 'stopped'}`;
        const status = document.createElement('div');
        status.className = 'status';
        status.innerHTML = `<span>${item.id || ''}</span><span>${item.status || ''}</span>`;
        const prompt = document.createElement('div');
        prompt.className = 'prompt';
        prompt.textContent = item.prompt || '';
        const reply = document.createElement('div');
        reply.className = 'reply';
        reply.textContent = item.reply || '暂无输出';
        card.appendChild(status);
        card.appendChild(prompt);
        card.appendChild(reply);
        cardsEl.appendChild(card);
      }
    }

    function setConn(text) {
      connHintEl.textContent = text;
    }

    function connect() {
      clearTimeout(reconnectTimer);
      const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
      setConn('连接中');
      ws = new WebSocket(url);
      ws.onopen = () => {
        setConn('已连接');
      };
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'hello') {
          messages = Array.isArray(data.chat) ? data.chat.slice() : [];
          renderChat();
          renderCards(Array.isArray(data.subagents) ? data.subagents : []);
          return;
        }
        if (data.type === 'chat' && data.item) {
          messages.push(data.item);
          renderChat();
          return;
        }
        if (data.type === 'subagents') {
          renderCards(Array.isArray(data.items) ? data.items : []);
        }
      };
      ws.onclose = () => {
        setConn('连接断开，重连中');
        reconnectTimer = setTimeout(connect, 1500);
      };
      ws.onerror = () => {
        try { ws.close(); } catch (_) {}
      };
    }

    function send() {
      const text = inputEl.value.trim();
      if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ msg: text }));
      inputEl.value = '';
    }

    sendBtn.addEventListener('click', send);
    inputEl.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') send();
    });

    connect();
  </script>
</body>
</html>
'''


def launcher_conductor_runtime_dir():
    return launcher_data_path("runtime", "conductor")


def launcher_conductor_runtime_paths():
    root = launcher_conductor_runtime_dir()
    return {
        "root": root,
        "script": os.path.join(root, "conductor.py"),
        "html": os.path.join(root, "conductor.html"),
    }


def _write_text_if_changed(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    current = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            current = f.read()
    except Exception:
        current = None
    if current == text:
        return
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def ensure_launcher_conductor_runtime():
    paths = launcher_conductor_runtime_paths()
    _write_text_if_changed(paths["script"], _CONDUCTOR_SCRIPT_TEXT)
    _write_text_if_changed(paths["html"], _CONDUCTOR_HTML_TEXT)
    return dict(paths)
