#!/usr/bin/env python3
"""
PINCER v2 — agent.py (FIXED)
Single-File Autonomous Coding Agent | Claude-Code Style TUI
Target: Mac M4 Air 16GB | Model: qwen3:8b | Architecture: 5-Layer
Dependencies: ollama, prompt_toolkit>=3.0, rich, httpx, beautifulsoup4
"""

# ==============================================================================
# === SECTION 1: CONFIGURATION & IMPORTS ======================================
# ==============================================================================
import asyncio, sys, os, json, re, time, sqlite3, hashlib, subprocess, shutil
from pathlib import Path
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple, Callable
from collections import deque, defaultdict
from io import StringIO

# External deps with graceful fallback
try:
    import ollama
    from prompt_toolkit import Application, PromptSession
    from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, Float, FloatContainer
    from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
    from prompt_toolkit.layout.containers import ConditionalContainer
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import WordCompleter  # Fixed: removed FuzzyWordCompleter ignore_case
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.tree import Tree
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.table import Table
    from rich.text import Text
    from rich.live import Live
    from rich.rule import Rule
    import httpx
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"🛑 Missing: {e.name} → pip install ollama prompt_toolkit rich httpx beautifulsoup4")
    sys.exit(1)

# --- CONFIG ---
CONFIG = {
    "MODEL": "qwen3:8b", "OLLAMA_URL": "http://localhost:11434", "MAX_TURNS": 12,
    "CTX_BUDGET": 12000, "DB_PATH": Path.home() / ".pincer" / "pincer_v2.db",
    "NOTES_PATH": Path.home() / ".pincer" / "notes.json", "TRUST_MODE": "default",
    "MAX_TOOL_OUTPUT": 2000, "REFRESH_HZ": 6
}

COLORS = {
    "PRIMARY": "#2563EB", "SURFACE": "#1E293B", "BG": "#0F172A", "TEXT": "#E2E8F0",
    "DIM": "#94A3B8", "THINKING": "#60A5FA", "SUCCESS": "#10B981",
    "WARNING": "#FBBF24", "ERROR": "#EF4444", "ACCENT": "#93C5FD"
}

# ==============================================================================
# === SECTION 2: STATE & MEMORY ===============================================
# ==============================================================================
class AgentState(Enum):
    IDLE=auto(); THINKING=auto(); PLANNING=auto(); EXECUTING=auto()
    SEARCHING=auto(); COMPACTING=auto(); GUARDRAIL=auto(); ERROR=auto()

MASCOT = {
    AgentState.IDLE: ("🦞", COLORS["PRIMARY"], "Ready"),
    AgentState.THINKING: ("🦞💭", COLORS["THINKING"], "Thinking"),
    AgentState.PLANNING: ("🦞📐", "#818CF8", "Planning"),
    AgentState.EXECUTING: ("🦞🔧", COLORS["PRIMARY"], "Executing"),
    AgentState.SEARCHING: ("🦞🔍", "#67E8F9", "Searching"),
    AgentState.COMPACTING: ("🦞🗜️", COLORS["ACCENT"], "Compacting"),
    AgentState.GUARDRAIL: ("🦞⚠️", COLORS["WARNING"], "Permission"),
    AgentState.ERROR: ("🦞🛑", COLORS["ERROR"], "Error"),
}

@dataclass
class ContextBudget:
    max: int = CONFIG["CTX_BUDGET"]
    system: int = 500; memory: int = 1000; conversation: int = 0; tools: int = 0
    @property
    def used(self): return self.system + self.memory + self.conversation + self.tools
    @property
    def pct(self): return min(1.0, self.used / self.max)

class PincerDB:
    def __init__(self):
        CONFIG["DB_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(CONFIG["DB_PATH"], check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
    
    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, role TEXT, content TEXT, tokens INT, session_id TEXT
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, category TEXT, content TEXT, task_id TEXT, embedding BLOB
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, plan TEXT, context TEXT, status TEXT
            );
        """)
        self.conn.commit()
    
    def add_msg(self, role, content, tokens=0, session_id="default"):
        self.conn.execute("INSERT INTO messages (ts,role,content,tokens,session_id) VALUES (?,?,?,?,?)",
                         (time.time(), role, content, tokens, session_id))
        self.conn.commit()
    
    def get_recent(self, limit=50, session_id="default"):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit))]
    
    def write_note(self, cat, content, task_id=None):
        self.conn.execute("INSERT INTO notes (ts,category,content,task_id) VALUES (?,?,?,?)",
                         (time.time(), cat, content, task_id))
        self.conn.commit()
    
    def query_notes(self, query="", limit=5):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM notes WHERE content LIKE ? ORDER BY ts DESC LIMIT ?",
            (f"%{query}%", limit))]
    
    def save_checkpoint(self, plan, ctx, status="active"):
        self.conn.execute("INSERT INTO checkpoints (ts,plan,context,status) VALUES (?,?,?,?)",
                         (time.time(), json.dumps(plan), json.dumps(ctx), status))
        self.conn.commit()

# ==============================================================================
# === SECTION 3: BACKEND & WEB ================================================
# ==============================================================================
class OllamaBackend:
    def __init__(self):
        self.client = ollama.Client(host=CONFIG["OLLAMA_URL"])
        self.model = CONFIG["MODEL"]
    
    def chat(self, messages, tools=None, stream=False, options=None):
        kwargs = dict(model=self.model, messages=messages, stream=stream)
        if tools: kwargs["tools"] = tools
        if options: kwargs["options"] = options
        return self.client.chat(**kwargs)
    
    def pull(self, model):
        for chunk in self.client.pull(model, stream=True):
            sys.stdout.write(f"\r{chunk.get('status','')}")
            sys.stdout.flush()

class WebTools:
    async def search(self, query: str, num=5) -> list:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://html.duckduckgo.com/html/", 
                               params={"q": query}, 
                               headers={"User-Agent": "Pincer/2.0"})
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for a in soup.select(".result__a")[:num]:
                results.append({"title": a.text, "url": a.get("href"), "snippet": ""})
            return results
    
    async def fetch(self, url: str, max_len=3000) -> str:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=20, follow_redirects=True)
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]): tag.decompose()
            return soup.get_text(separator="\n", strip=True)[:max_len]

# ==============================================================================
# === SECTION 4: SAFETY & TOOLS ===============================================
# ==============================================================================
DENY_PATTERNS = [r"^rm\s+-[a-zA-Z]*f\s+/", r"^mkfs", r"^dd\s+if=", r"curl\s+.*\|\s*(ba)?sh", r"^sudo\s+rm"]
SAFE_PATTERNS = [r"^git\s+(status|log|diff|show)", r"^ls\s+.*", r"^cat\s+.*", r"^pwd$", r"^python\s+.*\.py$"]

class Guardrails:
    LIMITS = {"max_shell": 30, "max_rm": 2, "max_writes": 15, "max_time_min": 90}
    def __init__(self):
        self.counts = defaultdict(int)
        self.start = time.time()
    
    def check(self, cmd: str, mode: str) -> Tuple[bool, str]:
        self.counts["shell"] += 1
        for p in DENY_PATTERNS:
            if re.search(p, cmd): return False, f"❌ Denied: High-risk command"
        if self.counts["shell"] > self.LIMITS["max_shell"]: return False, "⛔ Limit reached"
        if time.time() - self.start > self.LIMITS["max_time_min"] * 60: return False, "⏱️ Time limit"
        return True, "✅ Safe"
    
    def should_ask(self, cmd: str, mode: str) -> bool:
        if mode in ("auto", "acceptEdits"): return False
        if any(re.search(p, cmd) for p in SAFE_PATTERNS): return False
        return True

class ToolRegistry:
    def __init__(self, guardrails, memory, web):
        self.gr, self.mem, self.web = guardrails, memory, web
        self.schemas = self._build_schemas()
    
    def _build_schemas(self):
        return [
            {"type":"function","function":{"name":"read_file","description":"Read file","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
            {"type":"function","function":{"name":"write_file","description":"Write file","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}}},
            {"type":"function","function":{"name":"execute_command","description":"Run shell command","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}},
            {"type":"function","function":{"name":"web_search","description":"Search web","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
            {"type":"function","function":{"name":"think","description":"Reason step-by-step","parameters":{"type":"object","properties":{"reasoning":{"type":"string"}},"required":["reasoning"]}}}
        ]
    
    async def execute(self, tool_call, trust_mode):
        tc = tool_call["function"]
        name, args = tc["name"], json.loads(tc["arguments"])
        allowed, msg = self.gr.check(args.get("command",""), trust_mode)
        if not allowed and name == "execute_command": return {"error": msg}
        try:
            if name == "read_file":
                p = Path(args["path"])
                return {"content": p.read_text()[:CONFIG["MAX_TOOL_OUTPUT"]]}
            elif name == "write_file":
                p = Path(args["path"]); p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(args["content"]); return {"status": "ok"}
            elif name == "execute_command":
                res = subprocess.run(args["command"], shell=True, capture_output=True, text=True, timeout=15)
                return {"output": (res.stdout+res.stderr).strip()[:CONFIG["MAX_TOOL_OUTPUT"]]}
            elif name == "web_search": return {"results": await self.web.search(args["query"])}
            elif name == "think": return {"status": "reasoning_logged"}
            return {"error": "Unknown tool"}
        except Exception as e: return {"error": str(e)}

# ==============================================================================
# === SECTION 5: CORE AGENT LOOP ==============================================
# ==============================================================================
class AgentCore:
    def __init__(self, ui_queue: asyncio.Queue):
        self.ui = ui_queue
        self.backend = OllamaBackend()
        self.web = WebTools()
        self.gr = Guardrails()
        self.mem = PincerDB()
        self.tools = ToolRegistry(self.gr, self.mem, self.web)
        self.ctx = ContextBudget()
        self.state = AgentState.IDLE
        self.turn = 0
    
    def _compact(self, msgs):
        if self.ctx.pct > 0.85:
            self.state = AgentState.COMPACTING
            self._notify("Compacting context...", AgentState.COMPACTING)
            recent = msgs[-12:]
            old = msgs[:-12]
            if old:
                summary = " | ".join([m["content"][:50] for m in old if m["role"]=="assistant"])
                self.mem.write_note("summary", f"Auto-compact: {summary}")
            self.ctx.conversation = sum(m.get("tokens", len(m["content"].split())) for m in recent)
            return recent
        return msgs
    
    def _notify(self, text, state=None):
        if state: self.state = state
        self.ui.put_nowait({"type":"agent_text","role":"assistant","content":text,"state":self.state})
    
    async def run_loop(self, user_input: str):
        self.turn = 0
        messages = [{"role":"system","content":"You are PINCER, a precise coding agent. Use tools. Show thinking."}]
        messages.extend(self.mem.get_recent(limit=10))
        messages.append({"role":"user","content":user_input})
        self.mem.add_msg("user", user_input)
        self.ctx.conversation += len(user_input.split())
        
        while self.turn < CONFIG["MAX_TURNS"]:
            self.turn += 1
            self._notify(f"🦞 Turn {self.turn}/{CONFIG['MAX_TURNS']}", AgentState.THINKING)
            await asyncio.sleep(0.1)
            try:
                response = self.backend.chat(messages, tools=self.tools.schemas, stream=False)
                msg = response["message"]
                self._notify(msg["content"][:100]+"...", AgentState.PLANNING)
            except Exception as e:
                self._notify(f"LLM Error: {e}", AgentState.ERROR); break
            
            if "tool_calls" in msg and msg["tool_calls"]:
                for tc in msg["tool_calls"]:
                    self._notify(f"🔧 Calling: {tc['function']['name']}", AgentState.EXECUTING)
                    await asyncio.sleep(0.3)
                    res = await self.tools.execute(tc, CONFIG["TRUST_MODE"])
                    messages.append({"role":"tool","content":json.dumps(res),"tool_call_id":tc["id"]})
                self._notify("✅ Tools executed", AgentState.EXECUTING); continue
            
            messages.append({"role":"assistant","content":msg["content"]})
            self.mem.add_msg("assistant", msg["content"], len(msg["content"].split()))
            self._notify(msg["content"], AgentState.IDLE)
            messages = self._compact(messages)
            break

# ==============================================================================
# === SECTION 6: UI LAYER (FIXED prompt_toolkit API) ==========================
# ==============================================================================
class PincerUI:
    def __init__(self):
        self.ui_queue = asyncio.Queue()
        self.core = AgentCore(self.ui_queue)
        self.msg_buf = Buffer()
        self.input_buf = Buffer()
        self.state = AgentState.IDLE
        self.ctx = ContextBudget()
        self.kb = KeyBindings(); self._setup_keys()
        self.app = self._build_app()
    
    def _setup_keys(self):
        @self.kb.add("enter")
        def _(e):
            text = self.input_buf.text.strip()
            if text:
                self.msg_buf.insert_text(f"\n[bold {COLORS['PRIMARY']}]>[/] {text}\n")
                self.input_buf.text = ""
                asyncio.create_task(self.core.run_loop(text))
        @self.kb.add("c-p"): pass  # Toggle panels (stub)
    
    def _build_app(self):
        header = Window(FormattedTextControl(lambda: self._header()), height=1)
        footer = Window(FormattedTextControl(lambda: "[dim]Enter:Send │ C-P:Panels │ /help:Commands │ 🦞 Blue OS[/]"), height=1)
        left = Window(FormattedTextControl(lambda: self._files()), width=22, style=f"bg:{COLORS['SURFACE']}")
        # FIXED: Removed invalid 'search_buffer' parameter
        center = Window(BufferControl(buffer=self.msg_buf, focusable=True), style=f"bg:{COLORS['BG']} fg:{COLORS['TEXT']}")
        right = Window(FormattedTextControl(lambda: self._activity()), width=26, style=f"bg:{COLORS['SURFACE']}")
        main = VSplit([left, center, right])
        layout = Layout(HSplit([header, main, footer]))
        return Application(layout=layout, key_bindings=self.kb, full_screen=True, mouse_support=True,
                          style=Style.from_dict({"buffer":f"bg:{COLORS['BG']} fg:{COLORS['TEXT']}"}))
    
    def _header(self):
        icon, color, label = MASCOT[self.state]
        bar = "▓"*int(self.ctx.pct*12)+"░"*(12-int(self.ctx.pct*12))
        return f"[{color}]{icon}[/] [bold]{label}[/] │ qwen3:8b │ ctx:[{COLORS['ACCENT']}]{bar} {self.ctx.used}/{self.ctx.max}[/]"
    
    def _files(self): return "📁 .pincer/\n  📄 agent.py\n  📄 config.yaml\n  📁 .git/"
    
    def _activity(self):
        t = Table.grid(padding=(0,1)); t.add_column(style="dim",width=10); t.add_column()
        t.add_row("[dim]mode:[/]", CONFIG["TRUST_MODE"]); t.add_row("[dim]turns:[/]", str(self.core.turn))
        t.add_row("[dim]guard:[/]", "✅ Active")
        return f"[{COLORS['SURFACE']}]{t}[/{COLORS['SURFACE']}]"
    
    def _update_loop(self):
        while True:
            try:
                data = self.ui_queue.get_nowait()
                if data["type"]=="agent_text":
                    self.msg_buf.insert_text(f"{data['content']}\n")
                    if data.get("state"): self.state = data["state"]
            except asyncio.QueueEmpty: pass
            self.app.invalidate(); time.sleep(1/CONFIG["REFRESH_HZ"])

# ==============================================================================
# === SECTION 7: MAIN ==========================================================
# ==============================================================================
async def main():
    print(f"\n[bold blue]🦞 PINCER v2 — Initializing...[/bold blue]")
    ui = PincerUI()
    asyncio.create_task(asyncio.to_thread(ui._update_loop))
    try: await ui.app.run_async()
    except KeyboardInterrupt: print("\n[bold red]🦞 Pincer shut down. Memory saved.[/bold red]")

if __name__ == "__main__":
    asyncio.run(main())
