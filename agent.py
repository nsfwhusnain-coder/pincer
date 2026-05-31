#!/usr/bin/env python3
"""
PINCER v2 — agent.py
Single-File Autonomous Coding Agent | Claude-Code Style TUI
Target: Mac M4 Air 16GB | Model: qwen3:8b | Architecture: 5-Layer
Master Plan Sections: 1-15 | Dependencies: ollama, prompt_toolkit, rich, httpx, bs4
"""

# ==============================================================================
# === SECTION 1: CONFIGURATION & DEPENDENCIES ==================================
# ==============================================================================
import asyncio
import sys
import os
import json
import re
import time
import sqlite3
import hashlib
import subprocess
import shutil
from pathlib import Path
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple, Callable
from collections import deque, defaultdict
from io import StringIO
from concurrent.futures import ThreadPoolExecutor

# External dependencies (installed via pip)
try:
    import ollama
    from prompt_toolkit import Application, PromptSession
    from prompt_toolkit.layout import (
        Layout, HSplit, VSplit, Window, Float, FloatContainer, WindowAlign
    )
    from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
    from prompt_toolkit.layout.containers import ConditionalContainer
    from prompt_toolkit.layout.margins import ScrollbarMargin
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    from prompt_toolkit.filters import has_focus
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.tree import Tree
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.table import Table
    from rich.text import Text
    from rich.live import Live
    from rich.rule import Rule
    from rich.markdown import Markdown
    import httpx
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"🛑 Missing dependency: {e.name}")
    print("💡 Install: pip install ollama prompt_toolkit rich httpx beautifulsoup4")
    sys.exit(1)

# --- CONSTANTS & CONFIG ---
CONFIG = {
    "MODEL": "qwen3:8b",
    "OLLAMA_URL": "http://localhost:11434",
    "MAX_TURNS": 12,
    "CTX_BUDGET": 12000,
    "DB_PATH": Path.home() / ".pincer" / "pincer_v2.db",
    "NOTES_PATH": Path.home() / ".pincer" / "notes.json",
    "TRUST_MODE": "default",  # plan, default, acceptEdits, auto, dontAsk
    "MAX_TOOL_OUTPUT_CHARS": 2000,
    "REFRESH_HZ": 6,
}

COLORS = {
    "PRIMARY": "#2563EB", "SURFACE": "#1E293B", "BG": "#0F172A",
    "TEXT": "#E2E8F0", "DIM": "#94A3B8", "THINKING": "#60A5FA",
    "SUCCESS": "#10B981", "WARNING": "#FBBF24", "ERROR": "#EF4444", "ACCENT": "#93C5FD"
}

# ==============================================================================
# === SECTION 2: STATE & MEMORY LAYER =========================================
# ==============================================================================
class AgentState(Enum):
    IDLE = auto()
    THINKING = auto()
    PLANNING = auto()
    EXECUTING = auto()
    SEARCHING = auto()
    COMPACTING = auto()
    GUARDRAIL = auto()
    ERROR = auto()

MASCOT_STATE = {
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
    system: int = 500
    memory: int = 1000
    conversation: int = 0
    tools: int = 0
    @property
    def used(self): return self.system + self.memory + self.conversation + self.tools
    @property
    def pct(self): return min(1.0, self.used / self.max)

class SQLiteMemory:
    def __init__(self):
        CONFIG["DB_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(CONFIG["DB_PATH"], check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, ts REAL, role TEXT, content TEXT, tokens INT);
            CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, ts REAL, cat TEXT, content TEXT, task_id TEXT);
            CREATE TABLE IF NOT EXISTS checkpoints (id INTEGER PRIMARY KEY, ts REAL, plan JSON, context JSON, status TEXT);
        """)
        self.conn.commit()

    def add_message(self, role, content, tokens=0):
        self.conn.execute("INSERT INTO messages (ts, role, content, tokens) VALUES (?,?,?,?)",
                          (time.time(), role, content, tokens))
        self.conn.commit()

    def get_recent(self, limit=50):
        return [dict(r) for r in self.conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))]

    def write_note(self, cat, content, task_id=None):
        self.conn.execute("INSERT INTO notes (ts, cat, content, task_id) VALUES (?,?,?,?)",
                          (time.time(), cat, content, task_id))
        self.conn.commit()

    def query_notes(self, query="", limit=5):
        # Basic FTS fallback. sqlite-vec can be swapped in later.
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM notes WHERE content LIKE ? ORDER BY ts DESC LIMIT ?",
            (f"%{query}%", limit)
        )]

    def save_checkpoint(self, plan, ctx, status="active"):
        self.conn.execute("INSERT INTO checkpoints (ts, plan, context, status) VALUES (?,?,?,?)",
                          (time.time(), json.dumps(plan), json.dumps(ctx), status))
        self.conn.commit()

# ==============================================================================
# === SECTION 3: BACKEND & WEB LAYER ==========================================
# ==============================================================================
class OllamaBackend:
    def __init__(self):
        self.client = ollama.Client(host=CONFIG["OLLAMA_URL"])
        self.model = CONFIG["MODEL"]

    async def chat(self, messages, tools=None, stream=True, think=False):
        try:
            kwargs = dict(model=self.model, messages=messages, stream=stream)
            if tools: kwargs["tools"] = tools
            if think: kwargs["options"] = {"num_ctx": CONFIG["CTX_BUDGET"]}
            return self.client.chat(**kwargs)
        except Exception as e:
            raise ConnectionError(f"Ollama connection failed: {e}")

    def pull(self, model):
        print(f"📥 Pulling {model}...")
        for chunk in self.client.pull(model, stream=True):
            sys.stdout.write(f"\r{chunk.get('status','')}")
            sys.stdout.flush()

class WebTools:
    async def search(self, query: str, num=5) -> list:
        # DuckDuckGo Lite fallback
        async with httpx.AsyncClient() as client:
            r = await client.get("https://html.duckduckgo.com/html/", params={"q": query}, headers={"User-Agent": "Pincer/2.0"})
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for a in soup.select(".result__snippet"):
                link = a.find_previous_sibling("a")
                title = a.find_previous_sibling("a", class_="result__url")
                if link: results.append({"title": link.text, "url": link.get("href"), "snippet": a.text[:150]})
            return results[:num]

    async def fetch(self, url: str, max_len=3000) -> str:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=20, follow_redirects=True)
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]): tag.decompose()
            return soup.get_text(separator="\n", strip=True)[:max_len]

# ==============================================================================
# === SECTION 4: SAFETY & TOOL LAYER ==========================================
# ==============================================================================
DENY_PATTERNS = [r"^rm\s+-[a-zA-Z]*f\s+/", r"^mkfs", r"^dd\s+if=", r"curl\s+.*\|\s*(ba)?sh", r"^sudo\s+rm"]
SAFE_PATTERNS = [r"^git\s+(status|log|diff|show)", r"^ls\s+.*", r"^cat\s+.*", r"^pwd$", r"^python\s+.*\.py$", r"^pytest\s+.*"]

class Guardrails:
    LIMITS = {"max_shell": 30, "max_rm": 2, "max_writes": 15, "max_time_min": 90}
    def __init__(self):
        self.counts = defaultdict(int)
        self.start = time.time()

    def check(self, cmd: str, mode: str) -> Tuple[bool, str]:
        self.counts["shell"] += 1
        for p in DENY_PATTERNS:
            if re.search(p, cmd): return False, f"❌ Denied: High-risk command blocked."
        if self.counts["shell"] > self.LIMITS["max_shell"]: return False, "⛔ Limit reached."
        if time.time() - self.start > self.LIMITS["max_time_min"] * 60: return False, "⏱️ Time limit reached."
        return True, "✅ Safe."

    def should_ask(self, cmd: str, mode: str) -> bool:
        if mode == "auto" or mode == "acceptEdits": return False
        if any(re.search(p, cmd) for p in SAFE_PATTERNS): return False
        return True

class ToolRegistry:
    def __init__(self, guardrails, memory, web):
        self.gr = guardrails
        self.mem = memory
        self.web = web
        self.schemas = self._build_schemas()

    def _build_schemas(self):
        return [
            {"type": "function", "function": {"name": "read_file", "description": "Read file with line numbers", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
            {"type": "function", "function": {"name": "write_file", "description": "Create/overwrite file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
            {"type": "function", "function": {"name": "execute_command", "description": "Run shell command safely", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
            {"type": "function", "function": {"name": "web_search", "description": "Search the web", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "think", "description": "Reason step-by-step", "parameters": {"type": "object", "properties": {"reasoning": {"type": "string"}}, "required": ["reasoning"]}}}
        ]

    async def execute(self, tool_call, trust_mode):
        tc = tool_call["function"]
        name, args = tc["name"], json.loads(tc["arguments"])
        allowed, msg = self.gr.check(args.get("command", ""), trust_mode)
        if not allowed and name == "execute_command":
            return {"error": msg}
            
        try:
            if name == "read_file":
                p = Path(args["path"])
                return {"content": p.read_text()[:CONFIG["MAX_TOOL_OUTPUT_CHARS"]]}
            elif name == "write_file":
                p = Path(args["path"])
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(args["content"])
                return {"status": "ok"}
            elif name == "execute_command":
                res = subprocess.run(args["command"], shell=True, capture_output=True, text=True, timeout=15)
                out = (res.stdout + res.stderr).strip()
                return {"output": out[:CONFIG["MAX_TOOL_OUTPUT_CHARS"]]}
            elif name == "web_search":
                return {"results": await self.web.search(args["query"])}
            elif name == "think":
                return {"status": "reasoning_logged"}
            return {"error": "Unknown tool"}
        except Exception as e:
            return {"error": str(e)}

# ==============================================================================
# === SECTION 5: CORE REACT & COMPACTION LAYER ================================
# ==============================================================================
class AgentCore:
    def __init__(self, ui_queue: asyncio.Queue):
        self.ui = ui_queue
        self.backend = OllamaBackend()
        self.web = WebTools()
        self.gr = Guardrails()
        self.mem = SQLiteMemory()
        self.tools = ToolRegistry(self.gr, self.mem, self.web)
        self.ctx = ContextBudget()
        self.state = AgentState.IDLE
        self.plan = []
        self.turn = 0

    def _compact(self, msgs):
        # Layer 1-4 compaction: trim old, keep system/recent, collapse tool outputs
        if self.ctx.pct > 0.85:
            self.state = AgentState.COMPACTING
            self._notify_ui("Compacting context...", AgentState.COMPACTING)
            # Keep last N messages + system
            recent = msgs[-12:]
            # Summarize old into memory note
            old = msgs[:-12]
            if old:
                summary = " | ".join([m["content"][:50] for m in old if m["role"]=="assistant"])
                self.mem.write_note("summary", f"Auto-compact: {summary}")
            self.ctx.conversation = sum(m.get("tokens", len(m["content"].split())) for m in recent)
            return recent
        return msgs

    def _notify_ui(self, text, state=None):
        if state: self.state = state
        self.ui.put_nowait({"type": "agent_text", "role": "assistant", "content": text, "state": self.state})

    async def run_loop(self, user_input: str):
        self.turn = 0
        messages = [{"role": "system", "content": "You are PINCER, a precise, transparent coding agent. Show thinking. Use tools. Respect safety."}]
        messages.extend(self.mem.get_recent(limit=10))
        messages.append({"role": "user", "content": user_input})
        self.mem.add_message("user", user_input)
        self.ctx.conversation += len(user_input.split())

        while self.turn < CONFIG["MAX_TURNS"]:
            self.turn += 1
            self._notify_ui(f"🦞 Turn {self.turn}/{CONFIG['MAX_TURNS']}", AgentState.THINKING)
            await asyncio.sleep(0.1) # Simulate thinking delay

            # LLM Call
            try:
                response = self.backend.chat(messages, tools=self.tools.schemas, stream=False)
                msg = response["message"]
                self._notify_ui(msg["content"][:100] + "...", AgentState.PLANNING)
            except Exception as e:
                self._notify_ui(f"LLM Error: {e}", AgentState.ERROR)
                break

            if "tool_calls" in msg and msg["tool_calls"]:
                tool_results = []
                for tc in msg["tool_calls"]:
                    self._notify_ui(f"🔧 Calling: {tc['function']['name']}", AgentState.EXECUTING)
                    await asyncio.sleep(0.3)
                    res = await self.tools.execute(tc, CONFIG["TRUST_MODE"])
                    tool_results.append({"role": "tool", "content": json.dumps(res), "tool_call_id": tc["id"]})
                
                messages.extend(tool_results)
                self._notify_ui("✅ Tools executed", AgentState.EXECUTING)
                continue

            # Final response
            messages.append({"role": "assistant", "content": msg["content"]})
            self.mem.add_message("assistant", msg["content"], len(msg["content"].split()))
            self._notify_ui(msg["content"], AgentState.IDLE)
            messages = self._compact(messages)
            break

# ==============================================================================
# === SECTION 6: SURFACE UI LAYER (Claude-Code Style) ==========================
# ==============================================================================
class PincerUI:
    def __init__(self):
        self.ui_queue = asyncio.Queue()
        self.core = AgentCore(self.ui_queue)
        self.agent = AgentCore(self.ui_queue) # Alias for clarity
        self.msg_buffer = Buffer()
        self.input_buffer = Buffer()
        self.state = AgentState.IDLE
        self.plan = []
        self.ctx = ContextBudget()
        
        self.kb = KeyBindings()
        self._setup_keys()
        self.app = self._build_app()

    def _setup_keys(self):
        @self.kb.add("enter")
        def _(event):
            text = self.input_buffer.text.strip()
            if text:
                self.input_buffer.text = ""
                self.msg_buffer.insert_text(f"\n[bold {COLORS['PRIMARY']}]>[/] {text}\n")
                asyncio.create_task(self.core.run_loop(text))
                
        @self.kb.add("c-p")
        def _(event): pass # Toggle panels (stub for layout filter)

    def _build_app(self):
        header = Window(FormattedTextControl(lambda: self._render_header()), height=1)
        footer = Window(FormattedTextControl(lambda: "[dim]Enter: Send │ C-P: Toggle │ /help: Commands │ 🦞 Blue OS Theme[/]"), height=1)
        
        left = Window(FormattedTextControl(lambda: self._render_file_tree()), width=22, style=f"bg:{COLORS['SURFACE']}")
        center = Window(BufferControl(buffer=self.msg_buffer, focusable=True, search_buffer=self.input_buffer), style=f"bg:{COLORS['BG']} fg:{COLORS['TEXT']}")
        right = Window(FormattedTextControl(lambda: self._render_activity()), width=26, style=f"bg:{COLORS['SURFACE']}")
        
        main = VSplit([left, center, right])
        layout = Layout(HSplit([header, main, footer]))
        
        return Application(layout=layout, key_bindings=self.kb, full_screen=True, mouse_support=True,
                           style=Style.from_dict({"buffer": f"bg:{COLORS['BG']} fg:{COLORS['TEXT']}"}))

    def _render_header(self):
        icon, color, label = MASCOT_STATE[self.state]
        bar = "▓"*int(self.ctx.pct*12) + "░"*(12-int(self.ctx.pct*12))
        return f"[{color}]{icon}[/]  [bold]{label}[/] │ qwen3:8b │ ctx: [{COLORS['ACCENT']}]{bar} {self.ctx.used}/{self.ctx.max}[/]"

    def _render_file_tree(self):
        return "📁 .pincer/\n  📄 agent.py\n  📄 config.yaml\n  📁 .git/"

    def _render_activity(self):
        t = Table.grid(padding=(0,1))
        t.add_column(style="dim", width=10)
        t.add_column()
        t.add_row("[dim]mode:[/]", CONFIG["TRUST_MODE"])
        t.add_row("[dim]turns:[/]", str(self.core.turn))
        t.add_row("[dim]guard:[/]", "✅ Active")
        return f"[{COLORS['SURFACE']}]{t}[/{COLORS['SURFACE']}]"

    def _update_loop(self):
        while True:
            try:
                data = self.ui_queue.get_nowait()
                if data["type"] == "agent_text":
                    self.msg_buffer.insert_text(f"{data['content']}\n")
                    if data.get("state"): self.state = data["state"]
            except asyncio.QueueEmpty:
                pass
            self.app.invalidate()
            time.sleep(1/CONFIG["REFRESH_HZ"])

# ==============================================================================
# === SECTION 7: MAIN & CLI ====================================================
# ==============================================================================
async def main():
    print(f"\n[bold blue]🦞 PINCER v2 — Initializing...[/bold blue]")
    ui = PincerUI()
    asyncio.create_task(asyncio.to_thread(ui._update_loop))
    try:
        await ui.app.run_async()
    except KeyboardInterrupt:
        print("\n[bold red]🦞 Pincer shut down. Memory saved.[/bold red]")

if __name__ == "__main__":
    asyncio.run(main())
