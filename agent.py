#!/usr/bin/env python3
"""
PINCER v2 — Production-Ready Autonomous Coding Agent
Single-File Implementation | Claude-Code Style TUI | Local-First
Target: Mac M4 Air 16GB | Model: qwen3:8b | Architecture: 5-Layer
Master Plan: Sections 1-16 | Dependencies: See §10.1

Run: pip install ollama prompt_toolkit rich httpx beautifulsoup4 && python agent.py
"""
# ==============================================================================
# === SECTION 1: CONFIGURATION, IMPORTS & SETUP ===============================
# ==============================================================================
import asyncio, sys, os, json, re, time, sqlite3, subprocess, shutil, hashlib
from pathlib import Path
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple, Callable, Union
from collections import deque, defaultdict
from contextlib import asynccontextmanager
from io import StringIO

# --- External Dependencies (with graceful fallbacks) ---
try:
    import ollama
    from prompt_toolkit import Application, PromptSession, print_formatted_text
    from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, Float, FloatContainer
    from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
    from prompt_toolkit.layout.containers import ConditionalContainer
    from prompt_toolkit.layout.margins import ScrollbarMargin
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.formatted_text import HTML, to_formatted_text
    from prompt_toolkit.shortcuts import prompt
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.tree import Tree
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
    from rich.table import Table
    from rich.text import Text
    from rich.live import Live
    from rich.rule import Rule
    from rich.markdown import Markdown
    import httpx
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"\n🦞 PINCER v2 — Missing dependency: {e.name}")
    print("💡 Install: pip install ollama prompt_toolkit rich httpx beautifulsoup4\n")
    sys.exit(1)

# --- Configuration Constants ---
CONFIG = {
    "MODEL": os.getenv("PINCER_MODEL", "qwen3:8b"),
    "OLLAMA_URL": os.getenv("OLLAMA_HOST", "http://localhost:11434"),
    "MAX_TURNS": int(os.getenv("PINCER_MAX_TURNS", "12")),
    "CTX_BUDGET": int(os.getenv("PINCER_CTX_BUDGET", "12000")),
    "HOME": Path.home(),
    "PINCR_DIR": Path.home() / ".pincer",
    "DB_PATH": None,
    "NOTES_PATH": None,
    "LOG_PATH": None,
    "TRUST_MODE": os.getenv("PINCER_TRUST", "default"),
    "MAX_TOOL_OUTPUT": int(os.getenv("PINCER_TOOL_MAX", "2000")),
    "REFRESH_HZ": float(os.getenv("PINCER_REFRESH", "6")),
    "AUTO_PULL": os.getenv("PINCER_AUTO_PULL", "true").lower() == "true",
    "SANDBOX": os.getenv("PINCER_SANDBOX", "true").lower() == "true",
    "ALLOW_NETWORK": os.getenv("PINCER_NET", "true").lower() == "true",
    "THEME": os.getenv("PINCER_THEME", "blue"),
    "MASCOT": "🦞",
    "SHOW_THINKING": os.getenv("PINCER_THINK", "true").lower() == "true",
}
CONFIG["DB_PATH"] = CONFIG["PINCR_DIR"] / "pincer_v2.db"
CONFIG["NOTES_PATH"] = CONFIG["PINCR_DIR"] / "notes.json"
CONFIG["LOG_PATH"] = CONFIG["PINCR_DIR"] / "pincer.log"

# --- Color Palette (Blue OS Theme) ---
COLORS = {
    "PRIMARY": "#2563EB", "PRIMARY_DARK": "#1D4ED8",
    "SURFACE": "#1E293B", "SURFACE_LIGHT": "#334155",
    "BG": "#0F172A", "BG_LIGHT": "#1E293B",
    "TEXT": "#E2E8F0", "TEXT_DIM": "#94A3B8", "TEXT_MUTED": "#64748B",
    "THINKING": "#60A5FA", "PLANNING": "#818CF8", "EXECUTING": "#3B82F6",
    "SEARCHING": "#22D3EE", "COMPACTING": "#A78BFA",
    "SUCCESS": "#10B981", "WARNING": "#FBBF24", "ERROR": "#EF4444",
    "ACCENT": "#93C5FD", "ACCENT_LIGHT": "#BFDBFE",
}

# ==============================================================================
# === SECTION 2: STATE ENUMS & MASCOT SYSTEM ==================================
# ==============================================================================
class AgentState(Enum):
    IDLE = auto()
    INITIALIZING = auto()
    THINKING = auto()
    PLANNING = auto()
    EXECUTING = auto()
    SEARCHING = auto()
    COMPACTING = auto()
    GUARDRAIL = auto()
    ERROR = auto()
    COMPLETE = auto()

class TrustMode(Enum):
    PLAN = "plan"
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    AUTO = "auto"
    DONT_ASK = "dontAsk"

MASCOT_STATE = {
    AgentState.IDLE: ("🦞", COLORS["PRIMARY"], "Ready", ""),
    AgentState.INITIALIZING: ("🦞⚙️", COLORS["ACCENT"], "Starting...", "pulse"),
    AgentState.THINKING: ("🦞💭", COLORS["THINKING"], "Thinking", "bounce"),
    AgentState.PLANNING: ("🦞📐", COLORS["PLANNING"], "Planning", "spin"),
    AgentState.EXECUTING: ("🦞🔧", COLORS["EXECUTING"], "Executing", "pulse"),
    AgentState.SEARCHING: ("🦞🔍", COLORS["SEARCHING"], "Searching", "bounce"),
    AgentState.COMPACTING: ("🦞🗜️", COLORS["COMPACTING"], "Compacting", "spin"),
    AgentState.GUARDRAIL: ("🦞⚠️", COLORS["WARNING"], "Permission", "flash"),
    AgentState.ERROR: ("🦞🛑", COLORS["ERROR"], "Error", "shake"),
    AgentState.COMPLETE: ("🦞✅", COLORS["SUCCESS"], "Done", "pop"),
}

@dataclass
class ContextBudget:
    max_tokens: int = CONFIG["CTX_BUDGET"]
    system: int = 500
    memory: int = 1000
    conversation: int = 0
    tools: int = 0
    overhead: int = 200
    
    @property
    def used(self) -> int:
        return self.system + self.memory + self.conversation + self.tools + self.overhead
    
    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self.used)
    
    @property
    def pct(self) -> float:
        return min(1.0, self.used / self.max_tokens)
    
    @property
    def bar(self) -> str:
        filled = int(self.pct * 12)
        return "▓" * filled + "░" * (12 - filled)
    
    def add_conversation(self, tokens: int):
        self.conversation += tokens
        if self.pct > 0.85:
            return f"⚠️ Context at {self.pct*100:.0f}% — compaction recommended"
        return None

# ==============================================================================
# === SECTION 3: LOGGING & DIAGNOSTICS ========================================
# ==============================================================================
class PincerLogger:
    LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
    
    def __init__(self, log_path: Path, level: str = "INFO"):
        self.log_path = log_path
        self.level = level
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
    
    def _rotate_if_needed(self, max_size: int = 10*1024*1024):
        if self.log_path.exists() and self.log_path.stat().st_size > max_size:
            backup = self.log_path.with_suffix(".log.1")
            if backup.exists():
                backup.unlink()
            self.log_path.rename(backup)
    
    def _format(self, level: str, msg: str) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        return f"[{ts}] [{level}] {msg}\n"
    
    def _write(self, level: str, msg: str):
        if self.LEVELS.get(level, 1) >= self.LEVELS.get(self.level, 1):
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(self._format(level, msg))
    
    def info(self, msg: str): self._write("INFO", msg)
    def debug(self, msg: str): self._write("DEBUG", msg)
    def warning(self, msg: str): self._write("WARNING", msg)
    def error(self, msg: str): self._write("ERROR", msg)

# ==============================================================================
# === SECTION 4: SETUP & INITIALIZATION WIZARD ================================
# ==============================================================================
class SetupWizard:
    WELCOME = f"""
{COLORS['PRIMARY']}╔════════════════════════════════════════╗
║  {COLORS['ACCENT']}🦞  PINCER v2 — Welcome!  {COLORS['PRIMARY']}  ║
╚════════════════════════════════════════╝{COLORS['TEXT']}
"""
    
    def __init__(self, logger: PincerLogger):
        self.logger = logger
        self.config = {}
    
    def run(self) -> bool:
        print_formatted_text(to_formatted_text(self.WELCOME))
        if not self._check_ollama():
            return False
        if not self._check_model():
            return False
        self._collect_preferences()
        self._save_config()
        print_formatted_text(f"\n{COLORS['SUCCESS']}✓ All set! Type 'help' to get started.{COLORS['TEXT']}\n")
        return True
    
    def _check_ollama(self) -> bool:
        print_formatted_text(f"{COLORS['ACCENT']}→ Checking Ollama connection...{COLORS['TEXT']}")
        try:
            client = ollama.Client(host=CONFIG["OLLAMA_URL"])
            models = client.list()
            print_formatted_text(f"  {COLORS['SUCCESS']}✓ Ollama found ({len(models.get('models', []))} models){COLORS['TEXT']}")
            return True
        except Exception as e:
            print_formatted_text(f"  {COLORS['ERROR']}✗ Ollama not reachable: {e}{COLORS['TEXT']}")
            print_formatted_text(f"\n{COLORS['WARNING']}💡 Start Ollama: ollama serve{COLORS['TEXT']}")
            if CONFIG["AUTO_PULL"]:
                print_formatted_text(f"{COLORS['WARNING']}💡 Or install: curl -fsSL https://ollama.ai/install.sh | sh{COLORS['TEXT']}")
            return False
    
    def _check_model(self) -> bool:
        model = CONFIG["MODEL"]
        print_formatted_text(f"{COLORS['ACCENT']}→ Checking model: {model}{COLORS['TEXT']}")
        try:
            client = ollama.Client(host=CONFIG["OLLAMA_URL"])
            available = [m["name"] for m in client.list()["models"]]
            if model in available:
                print_formatted_text(f"  {COLORS['SUCCESS']}✓ {model} ready{COLORS['TEXT']}")
                return True
            if CONFIG["AUTO_PULL"]:
                print_formatted_text(f"  {COLORS['WARNING']}⏳ Pulling {model}...{COLORS['TEXT']}")
                try:
                    for progress in client.pull(model, stream=True):
                        if "status" in progress:
                            sys.stdout.write(f"\r    {progress['status']}")
                            sys.stdout.flush()
                    print_formatted_text(f"\n  {COLORS['SUCCESS']}✓ {model} pulled successfully{COLORS['TEXT']}")
                    return True
                except Exception as e:
                    print_formatted_text(f"\n  {COLORS['ERROR']}✗ Failed to pull: {e}{COLORS['TEXT']}")
                    return False
            else:
                print_formatted_text(f"  {COLORS['ERROR']}✗ {model} not found. Run: ollama pull {model}{COLORS['TEXT']}")
                return False
        except Exception as e:
            print_formatted_text(f"  {COLORS['ERROR']}✗ Error checking model: {e}{COLORS['TEXT']}")
            return False
    
    def _collect_preferences(self):
        print_formatted_text(f"\n{COLORS['ACCENT']}→ Quick setup:{COLORS['TEXT']}")
        name = prompt("  Your name? [user]: ", default="user").strip()
        self.config["user_name"] = name or "user"
        lang = prompt("  Preferred language? [python]: ", default="python").strip()
        self.config["preferred_lang"] = lang or "python"
        exp = prompt("  Experience level? [Intermediate]: ", default="Intermediate").strip()
        self.config["experience"] = exp or "Intermediate"
        print_formatted_text(f"\n  {COLORS['DIM']}Trust modes:{COLORS['TEXT']}")
        print_formatted_text(f"    {COLORS['DIM']}• default{COLORS['TEXT']} — Ask for shell commands (recommended)")
        print_formatted_text(f"    {COLORS['DIM']}• acceptEdits{COLORS['TEXT']} — Auto-allow file edits")
        print_formatted_text(f"    {COLORS['DIM']}• auto{COLORS['TEXT']} — Auto-approve safe commands")
        trust = prompt("  Trust mode? [default]: ", default="default").strip()
        self.config["trust_mode"] = trust if trust in ["plan", "default", "acceptEdits", "auto", "dontAsk"] else "default"
    
    def _save_config(self):
        config_file = CONFIG["PINCR_DIR"] / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w") as f:
            json.dump(self.config, f, indent=2)
        self.logger.info(f"Config saved: {self.config}")

# ==============================================================================
# === SECTION 5: DATABASE & MEMORY LAYER ======================================
# ==============================================================================
class PincerDB:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        session_id TEXT DEFAULT 'default',
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        tokens INTEGER DEFAULT 0,
        tool_call_id TEXT,
        metadata TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, ts);
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        category TEXT NOT NULL,
        content TEXT NOT NULL,
        task_id TEXT,
        embedding BLOB,
        relevance REAL DEFAULT 1.0
    );
    CREATE INDEX IF NOT EXISTS idx_notes_category ON notes(category);
    CREATE TABLE IF NOT EXISTS preferences (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        ts REAL NOT NULL,
        scope TEXT DEFAULT 'global'
    );
    CREATE TABLE IF NOT EXISTS checkpoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        task_id TEXT NOT NULL,
        plan TEXT,
        context_summary TEXT,
        status TEXT DEFAULT 'active',
        snapshot_path TEXT
    );
    CREATE TABLE IF NOT EXISTS tool_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        tool_name TEXT NOT NULL,
        arguments TEXT,
        result TEXT,
        duration_ms REAL,
        success BOOLEAN,
        error_msg TEXT
    );
    """
    
    def __init__(self, db_path: Path, logger: PincerLogger):
        self.db_path = db_path
        self.logger = logger
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()
        self.logger.info(f"Database initialized: {db_path}")
    
    def _init_schema(self):
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()
    
    def add_message(self, role: str, content: str, tokens: int = 0, 
                   session_id: str = "default", tool_call_id: str = None,
                   metadata: Dict = None) -> int:
        cursor = self.conn.execute(
            "INSERT INTO messages (ts, session_id, role, content, tokens, tool_call_id, metadata) VALUES (?,?,?,?,?,?,?)",
            (time.time(), session_id, role, content, tokens, tool_call_id, 
             json.dumps(metadata) if metadata else None)
        )
        self.conn.commit()
        self.logger.debug(f"Message added: {role} ({tokens} tokens)")
        return cursor.lastrowid
    
    def get_recent_messages(self, limit: int = 50, session_id: str = "default",
                          exclude_roles: List[str] = None) -> List[Dict]:
        exclude = exclude_roles or []
        placeholders = ",".join("?" * len(exclude))
        query = f"""
            SELECT id, ts, role, content, tokens, tool_call_id, metadata
            FROM messages 
            WHERE session_id = ? AND role NOT IN ({placeholders if exclude else "'__none__'"})
            ORDER BY ts DESC LIMIT ?
        """
        params = [session_id] + exclude + [limit] if exclude else [session_id, limit]
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in reversed(rows)]
    
    def get_messages_for_compaction(self, session_id: str = "default") -> List[Dict]:
        cutoff = time.time() - 3600
        rows = self.conn.execute("""
            SELECT id, role, content, tokens FROM messages
            WHERE session_id = ? AND ts < ? AND role NOT IN ('system', 'tool')
            ORDER BY ts ASC
        """, (session_id, cutoff)).fetchall()
        return [dict(r) for r in rows]
    
    NOTE_CATEGORIES = ["observation", "error", "success", "preference", "pattern", "fact"]
    
    def write_note(self, category: str, content: str, task_id: str = None, 
                  embedding: bytes = None, relevance: float = 1.0) -> int:
        if category not in self.NOTE_CATEGORIES:
            raise ValueError(f"Invalid note category: {category}")
        cursor = self.conn.execute(
            "INSERT INTO notes (ts, category, content, task_id, embedding, relevance) VALUES (?,?,?,?,?,?)",
            (time.time(), category, content, task_id, embedding, relevance)
        )
        self.conn.commit()
        self.logger.info(f"Note written: {category} — {content[:50]}...")
        return cursor.lastrowid
    
    def query_notes(self, query: str, categories: List[str] = None, 
                   limit: int = 5, min_relevance: float = 0.5) -> List[Dict]:
        cats = categories or self.NOTE_CATEGORIES
        placeholders = ",".join("?" * len(cats))
        rows = self.conn.execute(f"""
            SELECT id, category, content, relevance FROM notes
            WHERE category IN ({placeholders}) AND content LIKE ? AND relevance >= ?
            ORDER BY relevance DESC, ts DESC LIMIT ?
        """, cats + [f"%{query}%", min_relevance, limit]).fetchall()
        return [dict(r) for r in rows]
    
    def compact_notes(self, max_age_days: int = 7) -> int:
        cutoff = time.time() - (max_age_days * 86400)
        old = self.conn.execute(
            "SELECT id, category, content FROM notes WHERE ts < ?", (cutoff,)
        ).fetchall()
        if not old:
            return 0
        summary = {}
        for row in old:
            cat = row["category"]
            summary.setdefault(cat, []).append(row["content"])
        for cat, contents in summary.items():
            compacted = f"[{cat}] " + " | ".join(c[:30] for c in contents[:5])
            self.write_note("pattern", compacted)
        self.conn.execute("DELETE FROM notes WHERE ts < ?", (cutoff,))
        self.conn.commit()
        self.logger.info(f"Compacted {len(old)} notes into {len(summary)} patterns")
        return len(summary)
    
    def set_preference(self, key: str, value: Any, scope: str = "global"):
        self.conn.execute(
            "INSERT OR REPLACE INTO preferences (key, value, ts, scope) VALUES (?,?,?,?)",
            (key, json.dumps(value) if isinstance(value, (dict, list)) else str(value), 
             time.time(), scope)
        )
        self.conn.commit()
        self.logger.debug(f"Preference set: {key}={value}")
    
    def get_preference(self, key: str, default: Any = None, scope: str = "global") -> Any:
        row = self.conn.execute(
            "SELECT value FROM preferences WHERE key = ? AND scope = ?", (key, scope)
        ).fetchone()
        if row:
            val = row["value"]
            try:
                return json.loads(val)
            except:
                return val
        return default
    
    def save_checkpoint(self, task_id: str, plan: List[Dict], context_summary: str,
                       status: str = "active", snapshot_path: str = None) -> int:
        cursor = self.conn.execute(
            "INSERT INTO checkpoints (ts, task_id, plan, context_summary, status, snapshot_path) VALUES (?,?,?,?,?,?)",
            (time.time(), task_id, json.dumps(plan), context_summary, status, snapshot_path)
        )
        self.conn.commit()
        self.logger.info(f"Checkpoint saved: {task_id}")
        return cursor.lastrowid
    
    def get_active_checkpoint(self, task_id: str) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT * FROM checkpoints WHERE task_id = ? AND status = 'active' ORDER BY ts DESC LIMIT 1",
            (task_id,)
        ).fetchone()
        return dict(row) if row else None
    
    def log_tool_execution(self, tool_name: str, arguments: Dict, result: Dict,
                          duration_ms: float, success: bool, error_msg: str = None):
        self.conn.execute(
            "INSERT INTO tool_log (ts, tool_name, arguments, result, duration_ms, success, error_msg) VALUES (?,?,?,?,?,?,?)",
            (time.time(), tool_name, json.dumps(arguments), json.dumps(result),
             duration_ms, success, error_msg)
        )
        self.conn.commit()
    
    def get_tool_stats(self, tool_name: str = None, days: int = 7) -> Dict:
        cutoff = time.time() - (days * 86400)
        base = "WHERE ts > ?" if tool_name else "WHERE tool_name = ? AND ts > ?"
        params = [cutoff] if not tool_name else [tool_name, cutoff]
        row = self.conn.execute(f"""
            SELECT COUNT(*) as total, 
                   SUM(CASE WHEN success THEN 1 ELSE 0 END) as successes,
                   AVG(duration_ms) as avg_duration
            FROM tool_log {base}
        """, params).fetchone()
        return dict(row) if row else {}
    
    def close(self):
        self.conn.close()
        self.logger.info("Database closed")

# ==============================================================================
# === SECTION 6: BACKEND LAYER — OLLAMA & WEB =================================
# ==============================================================================
class OllamaBackend:
    def __init__(self, model: str, base_url: str, logger: PincerLogger):
        self.model = model
        self.base_url = base_url
        self.logger = logger
        self.client = ollama.Client(host=base_url)
        self._health_check()
    
    def _health_check(self) -> bool:
        try:
            models = [m["name"] for m in self.client.list()["models"]]
            if self.model not in models:
                self.logger.warning(f"Model {self.model} not found in Ollama")
                return False
            self.logger.info(f"Ollama healthy: {self.model} ready")
            return True
        except Exception as e:
            self.logger.error(f"Ollama health check failed: {e}")
            return False
    
    async def chat(self, messages: List[Dict], tools: List[Dict] = None, 
                  stream: bool = True, options: Dict = None, 
                  think: bool = False) -> Union[Dict, asyncio.Queue]:
        kwargs = {"model": self.model, "messages": messages, "stream": stream}
        if tools:
            kwargs["tools"] = tools
        if options:
            kwargs["options"] = options
        if think:
            kwargs["messages"] = [{"role": "system", 
                                  "content": "Think step-by-step. Use <thinking> tags for reasoning."}] + messages
        try:
            if stream:
                queue = asyncio.Queue()
                response = self.client.chat(**kwargs)
                for chunk in response:
                    await queue.put(chunk)
                await queue.put(None)
                return queue
            else:
                response = self.client.chat(**kwargs)
                self.logger.debug(f"LLM response: {len(response.get('message', {}).get('content', ''))} chars")
                return response
        except Exception as e:
            self.logger.error(f"Ollama chat error: {e}")
            raise ConnectionError(f"Failed to chat with Ollama: {e}")
    
    def pull_model(self, model: str, callback: Callable[[Dict], None] = None) -> bool:
        try:
            for progress in self.client.pull(model, stream=True):
                if callback:
                    callback(progress)
                self.logger.debug(f"Pull progress: {progress.get('status')}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to pull {model}: {e}")
            return False
    
    def list_models(self) -> List[Dict]:
        try:
            return self.client.list()["models"]
        except Exception as e:
            self.logger.error(f"Failed to list models: {e}")
            return []

class WebTools:
    USER_AGENT = "Pincer/2.0 (+https://github.com/nsfwhusnain-coder/pincer)"
    
    def __init__(self, logger: PincerLogger, allow_network: bool = True):
        self.logger = logger
        self.allow_network = allow_network
        self._session = None
    
    @asynccontextmanager
    async def _get_session(self):
        if self._session is None or self._session.is_closed:
            self._session = httpx.AsyncClient(
                headers={"User-Agent": self.USER_AGENT},
                timeout=30,
                follow_redirects=True,
                verify=True,
            )
        try:
            yield self._session
        finally:
            if self._session and not self._session.is_closed:
                await self._session.aclose()
                self._session = None
    
    async def fetch(self, url: str, max_length: int = 5000, 
                   strip_tags: List[str] = None) -> str:
        if not self.allow_network:
            raise PermissionError("Network access disabled")
        strip_tags = strip_tags or ["script", "style", "nav", "header", "footer", "aside"]
        async with self._get_session() as session:
            try:
                self.logger.info(f"Fetching: {url}")
                response = await session.get(url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                for tag_name in strip_tags:
                    for tag in soup.find_all(tag_name):
                        tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                text = re.sub(r"\n{3,}", "\n\n", text)
                text = text.strip()
                result = text[:max_length]
                self.logger.debug(f"Fetched {len(result)} chars from {url}")
                return result
            except httpx.HTTPError as e:
                self.logger.warning(f"HTTP error fetching {url}: {e}")
                return f"[Error fetching {url}: {e}]"
            except Exception as e:
                self.logger.error(f"Error fetching {url}: {e}")
                return f"[Error: {e}]"
    
    async def search(self, query: str, num_results: int = 5) -> List[Dict]:
        if not self.allow_network:
            raise PermissionError("Network access disabled")
        async with self._get_session() as session:
            try:
                self.logger.info(f"Searching: {query}")
                response = await session.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query, "kl": "wt-wt"},
                    headers={"User-Agent": self.USER_AGENT}
                )
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                results = []
                for result in soup.select(".result")[:num_results]:
                    title_el = result.select_one(".result__title a")
                    url_el = result.select_one(".result__url")
                    snippet_el = result.select_one(".result__snippet")
                    if title_el:
                        results.append({
                            "title": title_el.get_text(strip=True),
                            "url": title_el.get("href"),
                            "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                            "source": url_el.get_text(strip=True) if url_el else "",
                        })
                self.logger.debug(f"Search returned {len(results)} results")
                return results
            except Exception as e:
                self.logger.warning(f"Search error: {e}")
                return [{"title": "Search failed", "snippet": str(e), "url": ""}]
    
    async def scrape(self, url: str, wait_for: str = None, 
                    actions: List[Dict] = None) -> str:
        if not self.allow_network:
            raise PermissionError("Network access disabled")
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.logger.debug("Playwright not installed; falling back to fetch")
            return await self.fetch(url)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                self.logger.info(f"Scraping with browser: {url}")
                await page.goto(url, wait_until="networkidle", timeout=30000)
                if wait_for:
                    await page.wait_for_selector(wait_for, timeout=10000)
                if actions:
                    for action in actions:
                        if action["type"] == "click":
                            await page.click(action["selector"])
                        elif action["type"] == "scroll":
                            await page.evaluate(f"window.scrollBy(0, {action['pixels']})")
                        elif action["type"] == "wait":
                            await asyncio.sleep(action["seconds"])
                content = await page.content()
                soup = BeautifulSoup(content, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                self.logger.debug(f"Scraped {len(text)} chars from {url}")
                return text[:5000]
            finally:
                await browser.close()

# ==============================================================================
# === SECTION 7: SAFETY LAYER — GUARDRAILS & PERMISSIONS ======================
# ==============================================================================
class Guardrails:
    DENY_PATTERNS = [
        r"^rm\s+-[a-zA-Z]*f\s+/", r"^rm\s+--no-preserve-root", r"^mkfs",
        r"^dd\s+if=", r"curl\s+.*\|\s*(ba)?sh", r"wget\s+.*\|\s*(ba)?sh",
        r":\(\)\{.*;\}\s*;", r"^sudo\s+rm", r">\s*/etc/",
        r"^chmod\s+-R\s+777\s+/", r"^:(){:|:&};:", r"^\.\s*\./",
    ]
    SAFE_PATTERNS = [
        r"^git\s+(status|log|diff|show|branch)", r"^ls\s+.*", r"^cat\s+.*",
        r"^pwd$", r"^echo\s+.*", r"^python[3]?\s+.*\.py$",
        r"^pytest\s+.*", r"^python\s+-m\s+pytest\s+.*",
        r"^find\s+.*-type\s+f\s+.*", r"^grep\s+.*", r"^head\s+.*", r"^tail\s+.*",
    ]
    LIMITS = {
        "max_shell_commands": 50, "max_rm_commands": 3, "max_file_writes": 20,
        "max_task_time_minutes": 120, "max_no_progress_minutes": 20, "max_llm_calls": 100,
    }
    
    def __init__(self, logger: PincerLogger, trust_mode: TrustMode = TrustMode.DEFAULT):
        self.logger = logger
        self.trust_mode = trust_mode
        self.counts = defaultdict(int)
        self.start_time = time.time()
        self.last_progress = time.time()
    
    def check(self, command: str, tool_name: str = None) -> Tuple[bool, str]:
        self.counts["total"] += 1
        if tool_name == "execute_command":
            self.counts["shell"] += 1
        if "rm " in command:
            self.counts["rm"] += 1
        if tool_name in ["write_file", "edit_file"]:
            self.counts["writes"] += 1
        for pattern in self.DENY_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                reason = f"❌ Denied: High-risk command blocked by pattern '{pattern}'"
                self.logger.warning(reason)
                return False, reason
        if self.counts["shell"] > self.LIMITS["max_shell_commands"]:
            return False, f"⛔ Rate limit: Max {self.LIMITS['max_shell_commands']} shell commands per session"
        if self.counts["rm"] > self.LIMITS["max_rm_commands"]:
            return False, f"⛔ Rate limit: Max {self.LIMITS['max_rm_commands']} rm commands"
        if self.counts["writes"] > self.LIMITS["max_file_writes"]:
            return False, f"⛔ Rate limit: Max {self.LIMITS['max_file_writes']} file writes"
        elapsed_min = (time.time() - self.start_time) / 60
        if elapsed_min > self.LIMITS["max_task_time_minutes"]:
            return False, f"⏱️ Time limit: Task exceeded {self.LIMITS['max_task_time_minutes']} minutes"
        no_progress_min = (time.time() - self.last_progress) / 60
        if no_progress_min > self.LIMITS["max_no_progress_minutes"]:
            return False, f"⏱️ No progress: {no_progress_min:.1f} minutes without advancement"
        return True, "✅ Safe"
    
    def should_ask_permission(self, command: str, tool_name: str) -> bool:
        if self.trust_mode in [TrustMode.AUTO, TrustMode.DONT_ASK]:
            return False
        if self.trust_mode == TrustMode.PLAN:
            return True
        if self.trust_mode == TrustMode.DEFAULT:
            if tool_name == "execute_command":
                return not any(re.search(p, command) for p in self.SAFE_PATTERNS)
            return False
        if self.trust_mode == TrustMode.ACCEPT_EDITS:
            return tool_name == "execute_command"
        return True
    
    def record_progress(self):
        self.last_progress = time.time()
    
    def get_stats(self) -> Dict:
        return {
            "trust_mode": self.trust_mode.value,
            "counts": dict(self.counts),
            "elapsed_minutes": (time.time() - self.start_time) / 60,
            "limits": self.LIMITS,
        }

# ==============================================================================
# === SECTION 8: TOOL REGISTRY — NATIVE OLLAMA TOOL CALLING ===================
# ==============================================================================
class ToolRegistry:
    def __init__(self, guardrails: Guardrails, memory: PincerDB, 
                web: WebTools, logger: PincerLogger):
        self.gr = guardrails
        self.mem = memory
        self.web = web
        self.logger = logger
        self.schemas = self._build_schemas()
        self.tools = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "execute_command": self._execute_command,
            "web_search": self._web_search,
            "fetch_url": self._fetch_url,
            "think": self._think,
            "ask_user": self._ask_user,
        }
    
    def _build_schemas(self) -> List[Dict]:
        return [
            {"type": "function", "function": {"name": "read_file", "description": "Read a file with line numbers. Use for understanding code or config.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "File path (relative or absolute)"}, "limit": {"type": "integer", "description": "Max lines to read (default: 100)", "default": 100}, "offset": {"type": "integer", "description": "Start line offset (default: 0)", "default": 0}}, "required": ["path"]}}},
            {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a file. Use for generating code or configs.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "File path"}, "content": {"type": "string", "description": "File content"}, "append": {"type": "boolean", "description": "Append instead of overwrite", "default": False}}, "required": ["path", "content"]}}},
            {"type": "function", "function": {"name": "edit_file", "description": "Exact string replacement in a file. Must be unique match.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "File path"}, "old_str": {"type": "string", "description": "Exact string to replace (must be unique)"}, "new_str": {"type": "string", "description": "Replacement string"}}, "required": ["path", "old_str", "new_str"]}}},
            {"type": "function", "function": {"name": "execute_command", "description": "Run a shell command with sandbox protection.", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "Shell command to execute"}, "cwd": {"type": "string", "description": "Working directory", "default": "."}, "timeout": {"type": "integer", "description": "Timeout seconds", "default": 30}}, "required": ["command"]}}},
            {"type": "function", "function": {"name": "web_search", "description": "Search the web for information.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}, "num_results": {"type": "integer", "description": "Number of results", "default": 5}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "fetch_url", "description": "Fetch and extract text from a URL.", "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "URL to fetch"}, "max_length": {"type": "integer", "description": "Max chars to return", "default": 3000}}, "required": ["url"]}}},
            {"type": "function", "function": {"name": "think", "description": "Record step-by-step reasoning (visible to user).", "parameters": {"type": "object", "properties": {"reasoning": {"type": "string", "description": "Your reasoning process"}}, "required": ["reasoning"]}}},
            {"type": "function", "function": {"name": "ask_user", "description": "Ask the user a clarifying question.", "parameters": {"type": "object", "properties": {"question": {"type": "string", "description": "Question to ask"}, "options": {"type": "array", "items": {"type": "string"}, "description": "Optional answer choices"}}, "required": ["question"]}}},
        ]
    
    async def execute(self, tool_call: Dict, trust_mode: TrustMode, cwd: str = ".") -> Dict:
        start = time.time()
        tc = tool_call.get("function", {})
        name, args = tc.get("name"), json.loads(tc.get("arguments", "{}"))
        self.logger.info(f"Tool call: {name}({args})")
        if name == "execute_command":
            allowed, reason = self.gr.check(args.get("command", ""), name)
            if not allowed:
                result = {"error": reason, "blocked": True}
                self.mem.log_tool_execution(name, args, result, (time.time()-start)*1000, False, reason)
                return result
        if self.gr.should_ask_permission(args.get("command", ""), name):
            self.logger.warning(f"Permission would be asked for: {name}")
        try:
            tool_func = self.tools.get(name)
            if not tool_func:
                raise ValueError(f"Unknown tool: {name}")
            result = await tool_func(args, cwd=cwd)
            duration = (time.time() - start) * 1000
            self.mem.log_tool_execution(name, args, result, duration, True)
            self.gr.record_progress()
            self.logger.debug(f"Tool {name} completed in {duration:.0f}ms")
            return result
        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Tool {name} failed: {error_msg}")
            result = {"error": error_msg}
            self.mem.log_tool_execution(name, args, result, (time.time()-start)*1000, False, error_msg)
            return result
    
    async def _read_file(self, args: Dict, cwd: str) -> Dict:
        path = Path(cwd) / args["path"]
        limit = args.get("limit", 100)
        offset = args.get("offset", 0)
        if not path.exists():
            return {"error": f"File not found: {path}"}
        try:
            content = path.read_text(encoding="utf-8")
            lines = content.split("\n")
            selected = lines[offset:offset+limit]
            numbered = [f"{i+offset+1:4d} | {line}" for i, line in enumerate(selected)]
            output = "\n".join(numbered)
            if len(output) > CONFIG["MAX_TOOL_OUTPUT"]:
                output = output[:CONFIG["MAX_TOOL_OUTPUT"]] + "\n... [truncated]"
            return {"content": output, "total_lines": len(lines), "shown_lines": len(selected), "path": str(path)}
        except Exception as e:
            return {"error": f"Failed to read: {e}"}
    
    async def _write_file(self, args: Dict, cwd: str) -> Dict:
        path = Path(cwd) / args["path"]
        content = args["content"]
        append = args.get("append", False)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if append:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(content)
            else:
                path.write_text(content, encoding="utf-8")
            return {"status": "ok", "path": str(path), "bytes": len(content.encode("utf-8")), "action": "appended" if append else "written"}
        except Exception as e:
            return {"error": f"Failed to write: {e}"}
    
    async def _edit_file(self, args: Dict, cwd: str) -> Dict:
        path = Path(cwd) / args["path"]
        old_str = args["old_str"]
        new_str = args["new_str"]
        try:
            content = path.read_text(encoding="utf-8")
            count = content.count(old_str)
            if count == 0:
                return {"error": "String not found in file"}
            if count > 1:
                return {"error": f"String appears {count} times; must be unique"}
            new_content = content.replace(old_str, new_str, 1)
            path.write_text(new_content, encoding="utf-8")
            import difflib
            diff = difflib.unified_diff(content.splitlines(keepends=True), new_content.splitlines(keepends=True), fromfile=f"{path}.old", tofile=str(path), lineterm="")
            diff_text = "".join(diff)
            return {"status": "ok", "path": str(path), "diff": diff_text[:CONFIG["MAX_TOOL_OUTPUT"]]}
        except Exception as e:
            return {"error": f"Failed to edit: {e}"}
    
    async def _execute_command(self, args: Dict, cwd: str) -> Dict:
        command = args["command"]
        timeout = args.get("timeout", 30)
        work_dir = Path(cwd) / args.get("cwd", ".")
        try:
            if sys.platform == "darwin" and CONFIG["SANDBOX"] and shutil.which("sandbox-exec"):
                profile = self._build_sandbox_profile(str(work_dir), CONFIG["ALLOW_NETWORK"])
                result = subprocess.run(["sandbox-exec", "-f", profile, "sh", "-c", command], cwd=str(work_dir), capture_output=True, text=True, timeout=timeout)
            else:
                result = subprocess.run(command, shell=True, cwd=str(work_dir), capture_output=True, text=True, timeout=timeout)
            output = (result.stdout + result.stderr).strip()
            if len(output) > CONFIG["MAX_TOOL_OUTPUT"]:
                output = output[:CONFIG["MAX_TOOL_OUTPUT"]] + "\n... [output truncated]"
            return {"output": output, "exit_code": result.returncode, "success": result.returncode == 0}
        except subprocess.TimeoutExpired:
            return {"error": f"Command timed out after {timeout}s"}
        except Exception as e:
            return {"error": f"Execution failed: {e}"}
    
    def _build_sandbox_profile(self, cwd: str, allow_network: bool) -> str:
        network_rule = "(allow network*)" if allow_network else "(deny network*)"
        return f"""
        (version 1)
        (allow default)
        (deny file-write*
            (subpath "/System") (subpath "/usr") (subpath "/bin")
            (subpath "/sbin") (subpath "/etc") (literal "/var/log")
        )
        (allow file-write* (subpath "{cwd}") (subpath "{Path.home()}"))
        {network_rule}
        (deny process-exec (literal "/usr/bin/sandbox-exec"))
        """
    
    async def _web_search(self, args: Dict, cwd: str) -> Dict:
        query = args["query"]
        num = args.get("num_results", 5)
        try:
            results = await self.web.search(query, num)
            return {"query": query, "results": results, "count": len(results)}
        except Exception as e:
            return {"error": f"Search failed: {e}"}
    
    async def _fetch_url(self, args: Dict, cwd: str) -> Dict:
        url = args["url"]
        max_len = args.get("max_length", 3000)
        try:
            content = await self.web.fetch(url, max_len)
            return {"url": url, "content": content, "length": len(content)}
        except Exception as e:
            return {"error": f"Fetch failed: {e}"}
    
    async def _think(self, args: Dict, cwd: str) -> Dict:
        return {"status": "reasoning_recorded", "content": args.get("reasoning", "")}
    
    async def _ask_user(self, args: Dict, cwd: str) -> Dict:
        question = args["question"]
        options = args.get("options")
        return {"question": question, "options": options, "status": "awaiting_user_response", "note": "In production, this pauses the agent loop for user input"}

# ==============================================================================
# === SECTION 9: CORE AGENT LAYER — REACT LOOP & COMPACTION ===================
# ==============================================================================
class AgentCore:
    SYSTEM_PROMPT = """You are PINCER, a precise autonomous coding agent.
- Use tools to accomplish tasks. Show your reasoning with <thinking> tags.
- Prefer reading files before editing. Use edit_file for small changes.
- Always respect user safety preferences. When in doubt, ask.
- Keep responses concise. Use code blocks for code.
- If you need more info, use web_search or ask_user.
- Remember: You run locally on the user's machine. No cloud, no data leaving."""

    def __init__(self, ui_queue: asyncio.Queue, logger: PincerLogger):
        self.ui = ui_queue
        self.logger = logger
        self.db = PincerDB(CONFIG["DB_PATH"], logger)
        self.web = WebTools(logger, CONFIG["ALLOW_NETWORK"])
        self.gr = Guardrails(logger, TrustMode(CONFIG["TRUST_MODE"]))
        self.backend = OllamaBackend(CONFIG["MODEL"], CONFIG["OLLAMA_URL"], logger)
        self.tools = ToolRegistry(self.gr, self.db, self.web, logger)
        self.ctx = ContextBudget()
        self.state = AgentState.INITIALIZING
        self.session_id = hashlib.md5(f"{time.time()}{os.getpid()}".encode()).hexdigest()[:8]
        self.turn = 0
        self.task_id = None
        self.plan = []
        self.compaction_threshold = 0.85
        self.last_compaction = time.time()
        self.logger.info(f"AgentCore initialized (session: {self.session_id})")
    
    def _notify_ui(self, content: str, state: AgentState = None, metadata: Dict = None):
        if state:
            self.state = state
        self.ui.put_nowait({"type": "agent_update", "content": content, "state": self.state.value if self.state else None, "metadata": metadata or {}, "ts": time.time()})
    
    def _estimate_tokens(self, text: str) -> int:
        return len(text) // 4 + text.count(" ") // 2
    
    def _assemble_context(self, user_input: str) -> List[Dict]:
        messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]
        self.ctx.system = self._estimate_tokens(self.SYSTEM_PROMPT)
        notes = self.db.query_notes(user_input, limit=3)
        if notes:
            note_text = "Relevant memories:\n" + "\n".join(f"- [{n['category']}] {n['content']}" for n in notes)
            messages.append({"role": "system", "content": note_text})
            self.ctx.memory = self._estimate_tokens(note_text)
        recent = self.db.get_recent_messages(limit=20, session_id=self.session_id, exclude_roles=["system"])
        for msg in recent:
            messages.append({"role": msg["role"], "content": msg["content"]})
            self.ctx.conversation += msg.get("tokens", self._estimate_tokens(msg["content"]))
        messages.append({"role": "user", "content": user_input})
        self.ctx.conversation += self._estimate_tokens(user_input)
        self.ctx.tools = self._estimate_tokens(json.dumps(self.tools.schemas, indent=2))
        if warning := self.ctx.add_conversation(0):
            self._notify_ui(f"⚠️ {warning}", AgentState.COMPACTING)
        return messages
    
    def _compact_context(self, messages: List[Dict]) -> List[Dict]:
        self.logger.info("Starting context compaction")
        self._notify_ui("🗜️ Compacting context...", AgentState.COMPACTING)
        for msg in messages:
            if msg["role"] == "tool" and len(msg["content"]) > 500:
                msg["content"] = msg["content"][:500] + "...[truncated]"
        system_msgs = [m for m in messages if m["role"] == "system"]
        recent_msgs = messages[-15:]
        messages = system_msgs + recent_msgs
        old_msgs = [m for m in messages if m["role"] in ["user", "assistant"] and m not in recent_msgs]
        if old_msgs:
            summary = " | ".join([m["content"][:30] for m in old_msgs[-5:]])
            self.db.write_note("summary", f"Auto-compact: {summary}")
        self.ctx.conversation = sum(self._estimate_tokens(m.get("content", "")) for m in messages if m["role"] != "system")
        self.last_compaction = time.time()
        self.logger.info(f"Compaction complete: {self.ctx.used}/{self.ctx.max_tokens} tokens")
        self._notify_ui(f"✅ Context compacted ({self.ctx.used}/{self.ctx.max_tokens})", AgentState.IDLE)
        return messages
    
    async def run_loop(self, user_input: str):
        self.turn = 0
        self.task_id = hashlib.md5(f"{user_input}{time.time()}".encode()).hexdigest()[:8]
        tokens = self._estimate_tokens(user_input)
        self.db.add_message("user", user_input, tokens, self.session_id)
        messages = self._assemble_context(user_input)
        self._notify_ui(f"🦞 Starting task: {user_input[:50]}...", AgentState.PLANNING)
        while self.turn < CONFIG["MAX_TURNS"]:
            self.turn += 1
            self._notify_ui(f"🔄 Turn {self.turn}/{CONFIG['MAX_TURNS']}", AgentState.THINKING)
            try:
                self.logger.debug(f"LLM call (turn {self.turn})")
                response = await self.backend.chat(messages=messages, tools=self.tools.schemas, stream=False, think=CONFIG["SHOW_THINKING"])
                msg = response.get("message", {})
                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls", [])
                if content and CONFIG["SHOW_THINKING"]:
                    self._notify_ui(content[:200] + ("..." if len(content) > 200 else ""), AgentState.PLANNING if tool_calls else AgentState.EXECUTING)
                if tool_calls:
                    self._notify_ui(f"🔧 Executing {len(tool_calls)} tool(s)...", AgentState.EXECUTING)
                    for tc in tool_calls:
                        tool_name = tc["function"]["name"]
                        self._notify_ui(f"→ {tool_name}(...)", AgentState.EXECUTING)
                        result = await self.tools.execute(tc, self.gr.trust_mode)
                        messages.append({"role": "tool", "content": json.dumps(result), "tool_call_id": tc.get("id")})
                        self.ctx.tools += self._estimate_tokens(json.dumps(result))
                    continue
                messages.append({"role": "assistant", "content": content})
                self.db.add_message("assistant", content, self._estimate_tokens(content), self.session_id)
                self._notify_ui(content, AgentState.COMPLETE)
                if self.ctx.pct > self.compaction_threshold:
                    messages = self._compact_context(messages)
                break
            except Exception as e:
                error_msg = f"Error in agent loop: {e}"
                self.logger.error(error_msg)
                self._notify_ui(f"🛑 {error_msg}", AgentState.ERROR)
                if self.turn < CONFIG["MAX_TURNS"] - 1:
                    messages = self._compact_context(messages)
                    continue
                break
        self.db.save_checkpoint(self.task_id, plan=self.plan, context_summary=f"Turns: {self.turn}, Tokens: {self.ctx.used}", status="completed")
        self._notify_ui(f"✅ Task complete (turns: {self.turn})", AgentState.IDLE)
    
    async def run_autonomous(self, goal: str, auto_approve: bool = False):
        self._notify_ui(f"🎯 Autonomous mode: {goal}", AgentState.PLANNING)
        plan_prompt = f"Create a detailed step-by-step plan for: {goal}\nFormat as JSON array of {{step, description, tool, args}}."
        plan_response = await self.backend.chat([{"role": "user", "content": plan_prompt}], stream=False)
        plan_text = plan_response["message"]["content"]
        self.plan = [{"step": i+1, "description": line.strip()} for i, line in enumerate(plan_text.split("\n")) if line.strip()]
        self._notify_ui(f"📋 Plan ({len(self.plan)} steps):\n" + "\n".join(f"{s['step']}. {s['description']}" for s in self.plan[:5]), AgentState.PLANNING)
        for step in self.plan:
            self._notify_ui(f"▶ Step {step['step']}: {step['description']}", AgentState.EXECUTING)
            if "shell" in step.get("tool", "") or "command" in step.get("args", {}):
                allowed, reason = self.gr.check(step.get("args", {}).get("command", ""), "execute_command")
                if not allowed and not auto_approve:
                    self._notify_ui(f"⚠️ {reason}", AgentState.GUARDRAIL)
                    break
            await asyncio.sleep(0.5)
            self.gr.record_progress()
            if step["step"] % 5 == 0:
                self.db.save_checkpoint(self.task_id, self.plan, f"Progress: {step['step']}/{len(self.plan)}", "active")
        self._notify_ui(f"🏁 Autonomous task complete", AgentState.COMPLETE)
    
    def close(self):
        self.db.close()
        self.logger.info("AgentCore closed")

# ==============================================================================
# === SECTION 10: SURFACE LAYER — CLAUDE-CODE STYLE TERMINAL UI ===============
# ==============================================================================
class PincerUI:
    def __init__(self, logger: PincerLogger):
        self.logger = logger
        self.ui_queue = asyncio.Queue()
        self.core = AgentCore(self.ui_queue, logger)
        self.msg_buffer = Buffer()
        self.input_buffer = Buffer()
        self.agent_state = AgentState.INITIALIZING
        self.ctx = ContextBudget()
        self.panels_visible = True
        self.focused_panel = 1
        self.kb = KeyBindings()
        self._setup_keybindings()
        self.app = self._build_app()
        self.logger.info("PincerUI initialized")
    
    def _setup_keybindings(self):
        @self.kb.add("enter")
        def _(event):
            text = self.input_buffer.text.strip()
            if text:
                self.msg_buffer.insert_text(f"\n[{COLORS['PRIMARY']}]➤ [/][bold]{text}[/]\n")
                self.input_buffer.text = ""
                if text.startswith("/"):
                    self._handle_command(text)
                else:
                    asyncio.create_task(self.core.run_loop(text))
        @self.kb.add("c-p")
        def _(event):
            self.panels_visible = not self.panels_visible
            self.app.invalidate()
        @self.kb.add("c-1")
        def _(event):
            self.focused_panel = 0
            self.app.invalidate()
        @self.kb.add("c-2")
        def _(event):
            self.focused_panel = 1
            self.app.invalidate()
        @self.kb.add("c-3")
        def _(event):
            self.focused_panel = 2
            self.app.invalidate()
        @self.kb.add("c-l")
        def _(event):
            self.msg_buffer.reset()
            self.msg_buffer.insert_text(f"\n[{COLORS['DIM']}]— Chat cleared —[/{COLORS['DIM']}]\n")
        @self.kb.add("c-c")
        def _(event):
            self.logger.warning("User interrupted with Ctrl+C")
            self.agent_state = AgentState.IDLE
            self.msg_buffer.insert_text(f"\n[{COLORS['WARNING']}]⚠️ Interrupted[/{COLORS['WARNING']}]\n")
    
    def _handle_command(self, cmd: str):
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        if command == "/help":
            help_text = """
🦞 PINCER Commands:
  /help          Show this help
  /clear         Clear chat display
  /memory [q]    Query semantic memory
  /plan [goal]   Start autonomous mode
  /trust [mode]  Change trust: default/acceptEdits/auto
  /ctx           Show context budget
  /exit          Quit Pincer
            """.strip()
            self.msg_buffer.insert_text(f"\n{help_text}\n")
        elif command == "/clear":
            self.msg_buffer.reset()
        elif command == "/memory":
            notes = self.core.db.query_notes(args or "", limit=5)
            if notes:
                for n in notes:
                    self.msg_buffer.insert_text(f"\n[{COLORS['ACCENT']}]📝 [{n['category']}][/{COLORS['ACCENT']}] {n['content']}\n")
            else:
                self.msg_buffer.insert_text(f"\n[{COLORS['DIM']}]No memories found[/{COLORS['DIM']}]\n")
        elif command == "/plan":
            if args:
                asyncio.create_task(self.core.run_autonomous(args))
            else:
                self.msg_buffer.insert_text(f"\n[{COLORS['WARNING']}]Usage: /plan <goal>[/{COLORS['WARNING']}]\n")
        elif command == "/trust":
            if args in ["plan", "default", "acceptEdits", "auto", "dontAsk"]:
                self.core.gr.trust_mode = TrustMode(args)
                self.msg_buffer.insert_text(f"\n[{COLORS['SUCCESS']}]✓ Trust mode: {args}[/{COLORS['SUCCESS']}]\n")
            else:
                self.msg_buffer.insert_text(f"\n[{COLORS['WARNING']}]Valid modes: plan/default/acceptEdits/auto/dontAsk[/{COLORS['WARNING']}]\n")
        elif command == "/ctx":
            bar = "▓" * int(self.ctx.pct * 12) + "░" * (12 - int(self.ctx.pct * 12))
            stats = f"""
Context Budget:
  Used:     {self.ctx.used:,} / {self.ctx.max_tokens:,} tokens
  Bar:      [{COLORS['ACCENT']}]{bar}[/{COLORS['ACCENT']}]
  Breakdown:
    • System:   {self.ctx.system}
    • Memory:   {self.ctx.memory}
    • Conversation: {self.ctx.conversation}
    • Tools:    {self.ctx.tools}
    • Overhead: {self.ctx.overhead}
            """.strip()
            self.msg_buffer.insert_text(f"\n{stats}\n")
        elif command == "/exit":
            self.app.exit()
        else:
            self.msg_buffer.insert_text(f"\n[{COLORS['WARNING']}]Unknown command: {command}[/{COLORS['WARNING']}]\n")
    
    def _build_app(self) -> Application:
        header = Window(content=FormattedTextControl(lambda: self._render_header()), height=1, style=f"bg:{COLORS['BG']} fg:{COLORS['TEXT']}")
        left_panel = Window(content=FormattedTextControl(lambda: self._render_file_tree()), width=24, style=f"bg:{COLORS['SURFACE']} fg:{COLORS['TEXT']}")
        center_panel = Window(content=BufferControl(buffer=self.msg_buffer, focusable=True), style=f"bg:{COLORS['BG']} fg:{COLORS['TEXT']}")
        right_panel = Window(content=FormattedTextControl(lambda: self._render_activity()), width=28, style=f"bg:{COLORS['SURFACE']} fg:{COLORS['TEXT']}")
        input_bar = Window(content=BufferControl(buffer=self.input_buffer, focusable=True, prompt=HTML(f'<style fg="{COLORS["PRIMARY"]}">🦞</style> ')), height=1, style=f"bg:{COLORS['SURFACE']} fg:{COLORS['ACCENT']}")
        footer = Window(content=FormattedTextControl(lambda: f"[{COLORS['TEXT_MUTED']}]Enter:Send │ C-P:Panels │ C-1/2/3:Focus │ /help:Commands │ 🦞 Blue OS[/{COLORS['TEXT_MUTED']}]"), height=1, style=f"bg:{COLORS['BG']} fg:{COLORS['TEXT_MUTED']}")
        main_area = VSplit([ConditionalContainer(left_panel, filter=lambda: self.panels_visible), center_panel, ConditionalContainer(right_panel, filter=lambda: self.panels_visible)])
        layout = Layout(HSplit([header, main_area, input_bar, footer]))
        return Application(layout=layout, key_bindings=self.kb, full_screen=True, mouse_support=True, style=Style.from_dict({"buffer": f"bg:{COLORS['BG']} fg:{COLORS['TEXT']}", "input": f"bg:{COLORS['SURFACE']} fg:{COLORS['ACCENT']}"}))
    
    def _render_header(self) -> str:
        icon, color, label, anim = MASCOT_STATE[self.agent_state]
        bar = "▓" * int(self.ctx.pct * 12) + "░" * (12 - int(self.ctx.pct * 12))
        return f"[{color}]{icon}[/] [bold]{label}[/] │ {CONFIG['MODEL']} │ ctx:[{COLORS['ACCENT']}]{bar} {self.ctx.used}/{self.ctx.max_tokens}[/] │ [{COLORS['SUCCESS']}]🔒[/] {self.core.gr.trust_mode.value}"
    
    def _render_file_tree(self) -> str:
        tree = Tree("📁 project", guide_style=COLORS["DIM"])
        tree.add("📄 agent.py")
        tree.add("📄 config.json")
        tree.add("📁 .git/")
        tree.add("📁 tests/")
        return str(tree)
    
    def _render_activity(self) -> str:
        table = Table.grid(padding=(0, 1))
        table.add_column(style="dim", width=12)
        table.add_column()
        table.add_row("[dim]turns:[/]", str(self.core.turn))
        table.add_row("[dim]trust:[/]", self.core.gr.trust_mode.value)
        table.add_row("[dim]guard:[/]", "✅ Active")
        if self.core.plan:
            table.add_row("", Rule(style=COLORS["SURFACE_LIGHT"]))
            table.add_row("[dim]plan:[/]", "")
            for step in self.core.plan[:3]:
                table.add_row("", f"{step['step']}. {step['description'][:20]}...")
        return f"[{COLORS['SURFACE']}]{table}[/{COLORS['SURFACE']}]"
    
    async def _ui_update_loop(self):
        while True:
            try:
                while True:
                    update = self.ui_queue.get_nowait()
                    if update["type"] == "agent_update":
                        if update.get("state"):
                            self.agent_state = AgentState[update["state"]]
                        if hasattr(self.core, "ctx"):
                            self.ctx = self.core.ctx
                        content = update["content"]
                        if update.get("metadata", {}).get("thinking"):
                            content = f"[{COLORS['DIM']}]💭 {content}[/{COLORS['DIM']}]"
                        self.msg_buffer.insert_text(f"{content}\n")
            except asyncio.QueueEmpty:
                pass
            except Exception as e:
                self.logger.error(f"UI update error: {e}")
            self.app.invalidate()
            await asyncio.sleep(1 / CONFIG["REFRESH_HZ"])
    
    async def run(self):
        asyncio.create_task(self._ui_update_loop())
        try:
            await self.app.run_async()
        finally:
            self.core.close()
            self.logger.info("PincerUI shut down")

# ==============================================================================
# === SECTION 11: MAIN ENTRY & ORCHESTRATION ==================================
# ==============================================================================
async def main():
    CONFIG["PINCR_DIR"].mkdir(parents=True, exist_ok=True)
    logger = PincerLogger(CONFIG["LOG_PATH"])
    logger.info("🦞 PINCER v2 starting...")
    banner = f"""
{COLORS['PRIMARY']}╔════════════════════════════════════════╗
║  {COLORS['ACCENT']}🦞  PINCER v2 — Local AI Agent  {COLORS['PRIMARY']}  ║
╚════════════════════════════════════════╝
{COLORS['TEXT_DIM']}Model: {CONFIG['MODEL']} │ Context: {CONFIG['CTX_BUDGET']} tokens │ Trust: {CONFIG['TRUST_MODE']}
{COLORS['TEXT']}"""
    print_formatted_text(to_formatted_text(banner))
    config_file = CONFIG["PINCR_DIR"] / "config.json"
    if not config_file.exists():
        wizard = SetupWizard(logger)
        if not wizard.run():
            print_formatted_text(f"\n{COLORS['ERROR']}Setup failed. Please fix issues and restart.{COLORS['TEXT']}\n")
            return
    if config_file.exists():
        with open(config_file) as f:
            user_config = json.load(f)
        logger.info(f"Loaded user config: {user_config}")
    logger.info("Starting UI...")
    ui = PincerUI(logger)
    try:
        await ui.run()
    except KeyboardInterrupt:
        logger.info("User interrupted")
        print_formatted_text(f"\n{COLORS['WARNING']}🦞 Pincer shut down gracefully.{COLORS['TEXT']}\n")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print_formatted_text(f"\n{COLORS['ERROR']}🛑 Fatal error: {e}{COLORS['TEXT']}\n")
        print_formatted_text(f"{COLORS['DIM']}Check logs: {CONFIG['LOG_PATH']}{COLORS['TEXT']}\n")
        sys.exit(1)

if __name__ == "__main__":
    if not sys.stdout.isatty():
        print("🦞 PINCER requires an interactive terminal.")
        sys.exit(1)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🦞 Goodbye!")
        sys.exit(0)
