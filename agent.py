#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║              🦞  P I N C E R  v2.2.0                        ║
║         Autonomous Coding Agent — Blue Lobster Edition       ║
║                                                              ║
║  Architecture: Single-file Python CLI agent                  ║
║  Primary Model: qwen3:8b (with tool calling)                ║
║  Target: Mac M4 Air 16GB                                    ║
║  Inspiration: Claude Code, OpenCode, MCP ecosystem           ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, sys, re, json, time, uuid, math, signal, shutil, sqlite3
import hashlib, textwrap, subprocess, difflib, threading
from pathlib import Path
from datetime import datetime
from collections import deque, OrderedDict
from typing import Any, Optional
from urllib.parse import quote as url_quote

# ──────────────────────────────────────────────────────────────
# SECTION 1: DEPENDENCY CHECK & AUTO-INSTALL
# ──────────────────────────────────────────────────────────────

REQUIRED_DEPS = {
    'ollama': 'ollama>=0.4.0',
    'prompt_toolkit': 'prompt_toolkit>=3.0',
    'rich': 'rich>=13.0',
    'httpx': 'httpx>=0.27',
    'bs4': 'beautifulsoup4>=4.12',
    'pygments': 'pygments>=2.17',
}

def ensure_dependencies():
    missing = []
    for mod, pkg in REQUIRED_DEPS.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"\033[38;5;39m🦞 Installing missing dependencies: {', '.join(missing)}\033[0m")
        try:
            subprocess.check_call(
                [sys.executable, '-m', 'pip', 'install', '--quiet'] + missing,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            print("\033[38;5;39m🦞 Dependencies installed!\033[0m")
        except subprocess.CalledProcessError:
            print(f"\033[38;5;196m🦞 Failed. Run: pip install {' '.join(missing)}\033[0m")
            sys.exit(1)

ensure_dependencies()

import ollama
import httpx
from bs4 import BeautifulSoup
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import FuzzyWordCompleter
from prompt_toolkit.formatted_text import FormattedText
from rich.console import Console
from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.rule import Rule
from rich.layout import Layout
from rich.box import ROUNDED
from rich.theme import Theme
from pygments.lexers import guess_lexer_for_filename, TextLexer


# ──────────────────────────────────────────────────────────────
# SECTION 2: CONSTANTS & DEEP OCEAN COLOR SYSTEM
# ──────────────────────────────────────────────────────────────

VERSION = "2.2.0"
APP_NAME = "Pincer"
MASCOT = "🦞"
PINCER_DIR = Path.home() / ".pincer"
DB_PATH = PINCER_DIR / "pincer.db"
HISTORY_PATH = PINCER_DIR / "history"
CONFIG_PATH = PINCER_DIR / "config.json"
PINCER_MD = Path.cwd() / "PINCER.md"

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_USER = "user"
MAX_TURNS = 25
MAX_CONTEXT_TOKENS = 12000
SYSTEM_PROMPT_TOKENS = 500
MEMORY_TOKENS = 1000
AGENT_LOOP_TIMEOUT = 300  # 5 minutes per turn

THINK_OPEN = "<think" + ">"
THINK_CLOSE = "</think" + ">"

C = {
    'bg':            '#0F1629',
    'bg_panel':      '#151D3B',
    'bg_input':      '#1A2550',
    'bg_hover':      '#1E3A6E',
    'primary':       '#2563EB',
    'primary_br':    '#3B82F6',
    'accent':        '#60A5FA',
    'accent_lt':     '#93C5FD',
    'text':          '#93C5FD',
    'text_dim':      '#64748B',
    'text_bright':   '#DBEAFE',
    'text_muted':    '#475569',
    'thinking':      '#A78BFA',
    'planning':      '#60A5FA',
    'working':       '#3B82F6',
    'searching':     '#06B6D4',
    'reading':       '#8B5CF6',
    'writing':       '#2563EB',
    'error':         '#EF4444',
    'success':       '#22C55E',
    'warning':       '#F59E0B',
    'info':          '#06B6D4',
    'border':        '#1E3A6E',
    'border_active': '#3B82F6',
    'diff_add':      '#4ADE80',
    'diff_add_bg':   '#052E16',
    'diff_del':      '#F87171',
    'diff_del_bg':   '#450A0A',
    'lobster':       '#2563EB',
}

DEEP_OCEAN = Theme({
    'primary': C['primary'], 'primary.br': C['primary_br'],
    'accent': C['accent'], 'thinking': C['thinking'],
    'planning': C['planning'], 'working': C['working'],
    'searching': C['searching'], 'reading': C['reading'],
    'writing': C['writing'], 'error': C['error'],
    'success': C['success'], 'warning': C['warning'],
    'info': C['info'], 'text': C['text'],
    'text.dim': C['text_dim'], 'text.bright': C['text_bright'],
    'text.muted': C['text_muted'], 'lobster': C['lobster'],
    'border': C['border'], 'border.active': C['border_active'],
})

ANSI = {
    'reset': '\033[0m', 'bold': '\033[1m', 'dim': '\033[2m',
    'italic': '\033[3m', 'underline': '\033[4m',
    'purple': '\033[38;2;167;139;250m',
    'blue': '\033[38;2;59;130;246m',
    'bright_blue': '\033[38;2;96;165;250m',
    'cyan': '\033[38;2;6;182;212m',
    'green': '\033[38;2;34;197;94m',
    'red': '\033[38;2;239;68;68m',
    'amber': '\033[38;2;245;158;11m',
    'dim_blue': '\033[38;2;100;116;139m',
    'light_blue': '\033[38;2;147;197;253m',
    'white': '\033[38;2;219;234;254m',
}

TRUST_LEVELS = ['plan', 'default', 'acceptEdits', 'auto', 'dontAsk']
LAYOUT_MODES = ['stream', 'panel', 'compact']

DENY_PATTERNS = [
    r'^rm\s+-[a-zA-Z]*f\s+/', r'^mkfs', r'^dd\s+if=',
    r'curl\s+.*\|\s*(ba)?sh', r':\(\)\{.*;\}\s*;',
    r'^sudo\s+rm', r'>\s*/etc/', r'^chmod\s+-R\s+777\s+/',
    r'^git\s+push\s+--force', r'^dropdb',
    r'^pip\s+uninstall\s+-y\s+(pip|setuptools)',
]

SAFE_PATTERNS = [
    r'^git\s+(status|log|diff|show|branch)', r'^ls\s+.*',
    r'^cat\s+.*', r'^pwd$', r'^python\s+.*\.py$',
    r'^pytest\s+.*', r'^pip\s+(list|show|freeze|install)',
    r'^echo\s+.*', r'^which\s+.*', r'^head\s+.*',
    r'^tail\s+.*', r'^wc\s+.*', r'^find\s+.*',
    r'^grep\s+.*', r'^du\s+.*', r'^mkdir\s+.*',
    r'^touch\s+.*', r'^cp\s+.*', r'^mv\s+.*',
]


# ──────────────────────────────────────────────────────────────
# SECTION 3: CONFIGURATION
# ──────────────────────────────────────────────────────────────

class PincerConfig:
    DEFAULTS = {
        'user_name': DEFAULT_USER, 'model': DEFAULT_MODEL,
        'language': 'python', 'experience': 'Intermediate',
        'trust_level': 'default', 'thinking_mode': 'auto',
        'theme': 'deep_ocean', 'layout_mode': 'stream',
        'auto_approve_reads': True, 'max_shell_timeout': 120,
        'max_file_size': 1_000_000, 'max_output_lines': 200,
        'web_search_enabled': True, 'sound_notifications': True,
        'first_run': True,
    }

    def __init__(self):
        PINCER_DIR.mkdir(parents=True, exist_ok=True)
        self.data = dict(self.DEFAULTS)
        self._load()

    def _load(self):
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH) as f:
                    self.data.update(json.load(f))
            except Exception:
                pass

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(self.data, f, indent=2)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    @property
    def user_name(self):
        return self.data.get('user_name', DEFAULT_USER)

    @property
    def model(self):
        return self.data.get('model', DEFAULT_MODEL)

    @property
    def trust_level(self):
        return self.data.get('trust_level', 'default')

    @property
    def thinking_mode(self):
        return self.data.get('thinking_mode', 'auto')

    @property
    def layout_mode(self):
        return self.data.get('layout_mode', 'stream')


# ──────────────────────────────────────────────────────────────
# SECTION 4: DATABASE / STATE LAYER
# ──────────────────────────────────────────────────────────────

class PincerDB:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, title TEXT DEFAULT '',
        created_at REAL, updated_at REAL
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT, role TEXT NOT NULL,
        content TEXT DEFAULT '', thinking TEXT DEFAULT '',
        tool_calls TEXT DEFAULT '', tool_name TEXT DEFAULT '',
        token_count INTEGER DEFAULT 0, created_at REAL,
        FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    );
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL, content TEXT NOT NULL,
        tokens TEXT DEFAULT '', created_at REAL
    );
    CREATE TABLE IF NOT EXISTS checkpoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT, step_number INTEGER DEFAULT 0,
        description TEXT DEFAULT '', state_json TEXT DEFAULT '{}',
        created_at REAL
    );
    CREATE TABLE IF NOT EXISTS tool_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT, tool_name TEXT NOT NULL,
        args TEXT DEFAULT '', result_preview TEXT DEFAULT '',
        duration REAL DEFAULT 0, success INTEGER DEFAULT 1,
        created_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_msgs_conv ON messages(conversation_id);
    CREATE INDEX IF NOT EXISTS idx_notes_cat ON notes(category);
    CREATE INDEX IF NOT EXISTS idx_thist_conv ON tool_history(conversation_id);
    """

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()

    def create_conversation(self, title=""):
        conv_id = str(uuid.uuid4())[:8]
        now = time.time()
        self.conn.execute(
            "INSERT INTO conversations (id,title,created_at,updated_at) VALUES (?,?,?,?)",
            (conv_id, title, now, now)
        )
        self.conn.commit()
        return conv_id

    def list_conversations(self):
        cur = self.conn.execute(
            "SELECT id,title,created_at,updated_at FROM conversations ORDER BY updated_at DESC LIMIT 20"
        )
        return cur.fetchall()

    def update_conversation(self, conv_id, title=None):
        now = time.time()
        if title:
            self.conn.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?", (title, now, conv_id))
        else:
            self.conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conv_id))
        self.conn.commit()

    def delete_conversation(self, conv_id):
        self.conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        self.conn.execute("DELETE FROM tool_history WHERE conversation_id=?", (conv_id,))
        self.conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
        self.conn.commit()

    def add_message(self, conv_id, role, content="", thinking="", tool_calls="", tool_name="", token_count=0):
        now = time.time()
        self.conn.execute(
            "INSERT INTO messages (conversation_id,role,content,thinking,tool_calls,tool_name,token_count,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (conv_id, role, content, thinking, tool_calls, tool_name, token_count, now)
        )
        self.conn.commit()

    def get_messages(self, conv_id, limit=200):
        cur = self.conn.execute(
            "SELECT role,content,thinking,tool_calls,tool_name FROM messages WHERE conversation_id=? ORDER BY id ASC LIMIT ?",
            (conv_id, limit)
        )
        return cur.fetchall()

    def count_messages(self, conv_id):
        cur = self.conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conv_id,))
        return cur.fetchone()[0]

    def add_note(self, category, content):
        now = time.time()
        tokens = ' '.join(re.findall(r'\b\w+\b', content.lower()))
        self.conn.execute(
            "INSERT INTO notes (category,content,tokens,created_at) VALUES (?,?,?,?)",
            (category, content, tokens, now)
        )
        self.conn.commit()

    def get_notes(self, category=None, limit=20):
        if category:
            cur = self.conn.execute(
                "SELECT category,content,created_at FROM notes WHERE category=? ORDER BY created_at DESC LIMIT ?",
                (category, limit)
            )
        else:
            cur = self.conn.execute(
                "SELECT category,content,created_at FROM notes ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        return cur.fetchall()

    def search_notes(self, query, limit=5):
        query_tokens = set(re.findall(r'\b\w+\b', query.lower()))
        if not query_tokens:
            return []
        all_notes = self.conn.execute(
            "SELECT id,category,content,tokens FROM notes ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        scored = []
        for nid, cat, content, tokens_str in all_notes:
            note_tokens = set(tokens_str.split())
            overlap = len(query_tokens & note_tokens)
            if overlap > 0:
                scored.append((overlap, cat, content))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(cat, content) for _, cat, content in scored[:limit]]

    def add_tool_history(self, conv_id, tool_name, args="", result_preview="", duration=0.0, success=True):
        now = time.time()
        self.conn.execute(
            "INSERT INTO tool_history (conversation_id,tool_name,args,result_preview,duration,success,created_at) VALUES (?,?,?,?,?,?,?)",
            (conv_id, tool_name, args, result_preview, duration, 1 if success else 0, now)
        )
        self.conn.commit()

    def get_tool_history(self, conv_id, limit=20):
        cur = self.conn.execute(
            "SELECT tool_name,args,result_preview,duration,success,created_at FROM tool_history WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conv_id, limit)
        )
        return cur.fetchall()

    def save_checkpoint(self, conv_id, step_number, description, state):
        now = time.time()
        self.conn.execute(
            "INSERT INTO checkpoints (conversation_id,step_number,description,state_json,created_at) VALUES (?,?,?,?,?)",
            (conv_id, step_number, description, json.dumps(state), now)
        )
        self.conn.commit()

    def get_latest_checkpoint(self, conv_id):
        cur = self.conn.execute(
            "SELECT step_number,description,state_json,created_at FROM checkpoints WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
            (conv_id,)
        )
        return cur.fetchone()

    def close(self):
        self.conn.close()


# ──────────────────────────────────────────────────────────────
# SECTION 5: MEMORY SYSTEM
# ──────────────────────────────────────────────────────────────

class MemorySystem:
    def __init__(self, db: PincerDB, config: PincerConfig):
        self.db = db
        self.config = config
        self.working = {
            'current_plan': None,
            'current_step': 0,
            'open_files': [],
            'recent_edits': [],
            'pending_approvals': [],
        }
        self._ensure_pincer_md()

    def _ensure_pincer_md(self):
        if not PINCER_MD.exists():
            PINCER_MD.write_text(
                f"# PINCER.md — Project & User Notes\n# Auto-managed by Pincer v{VERSION}\n\n"
                f"## User Preferences\n- Name: {self.config.user_name}\n- Language: {self.config.get('language', 'python')}\n\n"
                f"## Project Conventions\n(learned automatically)\n\n## Learned Facts\n(learned automatically)\n"
            )

    def read_pincer_md(self):
        return PINCER_MD.read_text() if PINCER_MD.exists() else ""

    def write_pincer_md(self, content: str):
        PINCER_MD.write_text(content)

    def add_note(self, category: str, content: str):
        self.db.add_note(category, content)
        md = self.read_pincer_md()
        section_map = {
            'preference': '## User Preferences', 'convention': '## Project Conventions',
            'fact': '## Learned Facts', 'error': '## Learned Facts',
            'success': '## Learned Facts', 'pattern': '## Project Conventions',
            'observation': '## Learned Facts',
        }
        section = section_map.get(category, '## Learned Facts')
        if section in md:
            md = md.replace(section, f"{section}\n- {content}")
            self.write_pincer_md(md)

    def get_notes_for_context(self, limit=5):
        notes = self.db.get_notes(limit=limit)
        if not notes:
            return ""
        return "\n".join(f"- [{cat}] {content}" for cat, content, _ in notes)

    def get_relevant_notes(self, query: str, limit=3):
        results = self.db.search_notes(query, limit=limit)
        if not results:
            return self.get_notes_for_context(limit)
        return "\n".join(f"- [{cat}] {content}" for cat, content in results)

    def get_procedural_memory(self):
        return self.read_pincer_md()[:1500]

    def set_working(self, key, value):
        self.working[key] = value

    def get_working(self, key, default=None):
        return self.working.get(key, default)

    def get_state(self):
        return dict(self.working)

    def restore_state(self, state: dict):
        if state:
            self.working.update(state)


# ──────────────────────────────────────────────────────────────
# SECTION 6: WEB CACHE
# ──────────────────────────────────────────────────────────────

class WebCache:
    """LRU cache for web search and fetch results with TTL."""

    def __init__(self, ttl=300, max_size=50):
        self.ttl = ttl
        self.max_size = max_size
        self._cache = OrderedDict()

    def get(self, key: str):
        if key in self._cache:
            result, timestamp = self._cache[key]
            if time.time() - timestamp < self.ttl:
                self._cache.move_to_end(key)
                return result
            del self._cache[key]
        return None

    def set(self, key: str, result: str):
        if key in self._cache:
            del self._cache[key]
        self._cache[key] = (result, time.time())
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)

    def clear(self):
        self._cache.clear()

    @property
    def size(self):
        return len(self._cache)


# ──────────────────────────────────────────────────────────────
# SECTION 7: OLLAMA BACKEND (with Retry & Backoff)
# ──────────────────────────────────────────────────────────────

class OllamaBackend:
    def __init__(self, config: PincerConfig):
        self.config = config
        self.model = config.model
        self.client = ollama.Client(host='http://localhost:11434')
        self._ensure_model()
        # FIX: Calculate actual tool schema tokens dynamically
        self._tool_schema_tokens = self.count_tokens_approx(json.dumps(TOOL_SCHEMAS))

    def _ensure_model(self):
        try:
            models = self.client.list()
            model_names = [m.get('name', '') or getattr(m, 'model', '') for m in models.get('models', [])]
            if not any(self.model in name for name in model_names):
                print(f"{ANSI['cyan']}{MASCOT} Pulling model {self.model}...{ANSI['reset']}")
                self.client.pull(self.model)
        except Exception as e:
            print(f"{ANSI['red']}{MASCOT} Cannot connect to Ollama: {e}{ANSI['reset']}")
            print(f"{ANSI['amber']}  Make sure Ollama is running: ollama serve{ANSI['reset']}")
            sys.exit(1)

    def health_check(self):
        try:
            self.client.list()
            return True
        except Exception:
            return False

    def list_models(self):
        try:
            models = self.client.list()
            return [m.get('name', '') or getattr(m, 'model', '') for m in models.get('models', [])]
        except Exception:
            return []

    @property
    def tool_schema_tokens(self):
        return self._tool_schema_tokens

    def chat(self, messages, tools=None, stream=False, think=False):
        """Chat with retry and exponential backoff."""
        kwargs = {'model': self.model, 'messages': messages, 'stream': stream}
        if tools:
            kwargs['tools'] = tools
        if think and self.config.thinking_mode != 'off':
            try:
                kwargs['think'] = True
            except Exception:
                pass
        return self._call_with_retry(kwargs)

    def stream_chat(self, messages, tools=None, think=False):
        """Streaming chat with retry (returns iterator)."""
        kwargs = {'model': self.model, 'messages': messages, 'stream': True}
        if tools:
            kwargs['tools'] = tools
        if think and self.config.thinking_mode != 'off':
            try:
                kwargs['think'] = True
            except Exception:
                pass
        try:
            return self.client.chat(**kwargs)
        except TypeError:
            kwargs.pop('think', None)
            return self.client.chat(**kwargs)
        except ollama.ResponseError as e:
            raise RuntimeError(f"Ollama error: {e.error}") from e
        except Exception as e:
            raise RuntimeError(f"Stream error: {e}") from e

    def _call_with_retry(self, kwargs, max_retries=3):
        """Non-streaming call with exponential backoff."""
        for attempt in range(max_retries):
            try:
                return self.client.chat(**kwargs)
            except TypeError:
                kwargs.pop('think', None)
                return self.client.chat(**kwargs)
            except (ollama.ResponseError, ConnectionError, OSError) as e:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Ollama error after {max_retries} retries: {e}") from e
                wait = min(2 ** attempt + (attempt * 0.5), 30)
                time.sleep(wait)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Connection error after {max_retries} retries: {e}") from e
                wait = min(2 ** attempt, 30)
                time.sleep(wait)

    def count_tokens_approx(self, text: str) -> int:
        return max(1, len(text) // 4)

    def set_model(self, model: str):
        self.model = model
        self.config.set('model', model)
        self._ensure_model()

    def should_think(self) -> bool:
        mode = self.config.thinking_mode
        if mode == 'on':
            return True
        if mode == 'off':
            return False
        return True  # auto


# ──────────────────────────────────────────────────────────────
# SECTION 8: TOOL DEFINITIONS
# ──────────────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {'type': 'function', 'function': {
        'name': 'read_file',
        'description': 'Read the contents of a file. Returns content with line numbers. Supports offset and limit for large files.',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'Path to the file to read'},
            'offset': {'type': 'integer', 'description': 'Starting line number (1-based, default 1)'},
            'limit': {'type': 'integer', 'description': 'Maximum number of lines to read (default 200)'},
        }, 'required': ['path']},
    }},
    {'type': 'function', 'function': {
        'name': 'write_file',
        'description': 'Create or overwrite a file with the given content. Creates parent directories if needed.',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'Path to the file to write'},
            'content': {'type': 'string', 'description': 'Content to write to the file'},
        }, 'required': ['path', 'content']},
    }},
    {'type': 'function', 'function': {
        'name': 'edit_file',
        'description': 'Edit a file by replacing an exact string match. The old_string must be unique in the file. Returns a diff of changes.',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'Path to the file to edit'},
            'old_string': {'type': 'string', 'description': 'Exact string to find and replace (must be unique)'},
            'new_string': {'type': 'string', 'description': 'String to replace the old_string with'},
        }, 'required': ['path', 'old_string', 'new_string']},
    }},
    {'type': 'function', 'function': {
        'name': 'search_files',
        'description': 'Search for files by name pattern or content. Supports glob patterns for filenames and regex for content.',
        'parameters': {'type': 'object', 'properties': {
            'pattern': {'type': 'string', 'description': 'File name pattern (glob) or content search pattern'},
            'search_type': {'type': 'string', 'enum': ['filename', 'content'], 'description': 'Search by filename or content (default: filename)'},
            'path': {'type': 'string', 'description': 'Directory to search in (default: current directory)'},
        }, 'required': ['pattern']},
    }},
    {'type': 'function', 'function': {
        'name': 'list_directory',
        'description': 'List the contents of a directory. Shows files and subdirectories with types.',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'Directory path to list (default: current directory)'},
            'recursive': {'type': 'boolean', 'description': 'List recursively (default: false)'},
        }, 'required': []},
    }},
    {'type': 'function', 'function': {
        'name': 'execute_command',
        'description': 'Execute a shell command. Runs in the current working directory with a timeout. Use for running tests, installing packages, git operations, etc.',
        'parameters': {'type': 'object', 'properties': {
            'command': {'type': 'string', 'description': 'Shell command to execute'},
            'timeout': {'type': 'integer', 'description': 'Timeout in seconds (default: 120)'},
        }, 'required': ['command']},
    }},
    {'type': 'function', 'function': {
        'name': 'web_search',
        'description': 'Search the web using DuckDuckGo. Returns a list of search results with titles, URLs, and descriptions.',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Search query'},
            'num_results': {'type': 'integer', 'description': 'Number of results to return (default: 5)'},
        }, 'required': ['query']},
    }},
    {'type': 'function', 'function': {
        'name': 'fetch_url',
        'description': 'Fetch a web page and convert it to readable text. Good for documentation, articles, and static pages.',
        'parameters': {'type': 'object', 'properties': {
            'url': {'type': 'string', 'description': 'URL to fetch'},
            'max_length': {'type': 'integer', 'description': 'Maximum text length to return (default: 5000)'},
        }, 'required': ['url']},
    }},
    {'type': 'function', 'function': {
        'name': 'scrape_page',
        'description': 'Scrape a JavaScript-heavy web page using a headless browser. Use when fetch_url returns incomplete content. Requires Playwright.',
        'parameters': {'type': 'object', 'properties': {
            'url': {'type': 'string', 'description': 'URL to scrape'},
            'wait_for': {'type': 'string', 'description': 'CSS selector to wait for before extracting content'},
            'max_length': {'type': 'integer', 'description': 'Maximum text length to return (default: 5000)'},
        }, 'required': ['url']},
    }},
    {'type': 'function', 'function': {
        'name': 'ask_user',
        'description': 'Ask the user a clarifying question and wait for their response. Use when you need more information to proceed.',
        'parameters': {'type': 'object', 'properties': {
            'question': {'type': 'string', 'description': 'Question to ask the user'},
            'options': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Optional list of suggested answers'},
        }, 'required': ['question']},
    }},
    {'type': 'function', 'function': {
        'name': 'write_note',
        'description': 'Write a self-note to remember important information for later. Categories: observation, error, success, preference, pattern, fact.',
        'parameters': {'type': 'object', 'properties': {
            'category': {'type': 'string', 'description': 'Category of the note', 'enum': ['observation', 'error', 'success', 'preference', 'pattern', 'fact']},
            'content': {'type': 'string', 'description': 'The note content to remember'},
        }, 'required': ['category', 'content']},
    }},
]

TOOL_NAMES = [t['function']['name'] for t in TOOL_SCHEMAS]


# ──────────────────────────────────────────────────────────────
# SECTION 9: TOOL IMPLEMENTATIONS (with Cache)
# ──────────────────────────────────────────────────────────────

class ToolExecutor:
    def __init__(self, config: PincerConfig, db: PincerDB, web_cache: WebCache):
        self.config = config
        self.db = db
        self.web_cache = web_cache
        self.cwd = str(Path.cwd())

    def execute(self, name: str, args: dict) -> str:
        handler = getattr(self, f'_tool_{name}', None)
        if not handler:
            return f"Error: Unknown tool '{name}'"
        try:
            cleaned = {k: v for k, v in args.items() if v is not None}
            return handler(**cleaned)
        except TypeError as e:
            return f"Error: Invalid arguments for {name}: {e}"
        except Exception as e:
            return f"Error executing {name}: {e}"

    def _tool_read_file(self, path: str, offset: int = 1, limit: int = 200) -> str:
        filepath = Path(path).expanduser().resolve()
        if not filepath.exists():
            return f"Error: File not found: {path}"
        if filepath.is_dir():
            return f"Error: Path is a directory, not a file: {path}"
        if filepath.stat().st_size > self.config.get('max_file_size', 1_000_000):
            return f"Error: File too large ({filepath.stat().st_size} bytes). Use offset/limit."
        try:
            lines = filepath.read_text(errors='replace').splitlines()
            start = max(0, offset - 1)
            end = min(len(lines), start + limit)
            result_lines = [f"{i+1:6d} | {lines[i]}" for i in range(start, end)]
            header = f"File: {path} ({len(lines)} lines total, showing {start+1}-{end})"
            return header + "\n" + "\n".join(result_lines)
        except PermissionError:
            return f"Error: Permission denied: {path}"
        except Exception as e:
            return f"Error reading file: {e}"

    def _tool_write_file(self, path: str, content: str) -> str:
        filepath = Path(path).expanduser().resolve()
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            existed = filepath.exists()
            old_content = filepath.read_text() if existed else ""
            filepath.write_text(content)
            line_count = content.count('\n') + 1
            action = "Updated" if existed else "Created"
            self.db.add_note('observation', f"{'Updated' if existed else 'Created'} {path} ({line_count} lines)")
            return json.dumps({
                'action': action, 'path': path, 'lines': line_count,
                'diff_available': existed, 'old_content_preview': old_content[:200] if existed else "",
            })
        except PermissionError:
            return f"Error: Permission denied: {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    def _tool_edit_file(self, path: str, old_string: str, new_string: str) -> str:
        filepath = Path(path).expanduser().resolve()
        if not filepath.exists():
            return f"Error: File not found: {path}"
        try:
            content = filepath.read_text()
        except Exception as e:
            return f"Error reading file: {e}"
        count = content.count(old_string)
        if count == 0:
            normalized_old = old_string.strip()
            if normalized_old in content.strip():
                return "Error: String not found exactly. Text exists but with different whitespace. Copy the exact text."
            return f"Error: String not found in {path}. The exact text must match."
        if count > 1:
            return f"Error: String found {count} times in {path}. Provide more context to make it unique."
        old_content = content
        new_content = content.replace(old_string, new_string, 1)
        try:
            filepath.write_text(new_content)
        except Exception as e:
            return f"Error writing file: {e}"
        diff_lines = list(difflib.unified_diff(
            old_content.splitlines(keepends=True), new_content.splitlines(keepends=True),
            fromfile=f"{path} (before)", tofile=f"{path} (after)", lineterm='',
        ))
        old_lines = old_string.splitlines()
        new_lines = new_string.splitlines()
        summary = f"Replaced {len(old_lines)} line(s) with {len(new_lines)} line(s) in {path}"
        self.db.add_note('observation', f"Edited {path}: {summary}")
        return json.dumps({'summary': summary, 'path': path, 'old_lines': len(old_lines),
                          'new_lines': len(new_lines), 'diff': '\n'.join(diff_lines)})

    def _tool_search_files(self, pattern: str, search_type: str = 'filename', path: str = None) -> str:
        search_dir = Path(path).expanduser().resolve() if path else Path.cwd()
        if not search_dir.exists():
            return f"Error: Directory not found: {path}"
        results = []
        try:
            if search_type == 'filename':
                for p in search_dir.rglob(pattern):
                    if len(results) >= 30:
                        results.append("... (more results)")
                        break
                    try:
                        results.append(str(p.relative_to(search_dir)))
                    except ValueError:
                        results.append(str(p))
            else:
                try:
                    regex = re.compile(pattern, re.IGNORECASE)
                except re.error:
                    return f"Error: Invalid regex pattern: {pattern}"
                skip_ext = {'.pyc', '.pyo', '.so', '.dylib', '.png', '.jpg', '.jpeg', '.gif',
                           '.zip', '.tar', '.gz', '.woff', '.ttf', '.ico', '.svg'}
                for p in search_dir.rglob('*'):
                    if not p.is_file() or any(part.startswith('.') for part in p.parts):
                        continue
                    if p.suffix in skip_ext or p.stat().st_size > 100_000 or len(results) >= 20:
                        continue
                    try:
                        text = p.read_text(errors='ignore')
                        for i, line in enumerate(text.splitlines()[:500], 1):
                            if regex.search(line):
                                try:
                                    rel = p.relative_to(search_dir)
                                except ValueError:
                                    rel = p
                                results.append(f"{rel}:{i}: {line.strip()[:120]}")
                                if len(results) >= 20:
                                    break
                    except Exception:
                        continue
        except Exception as e:
            return f"Error searching: {e}"
        return "\n".join(results) if results else f"No results found for '{pattern}'"

    def _tool_list_directory(self, path: str = None, recursive: bool = False) -> str:
        dirpath = Path(path).expanduser().resolve() if path else Path.cwd()
        if not dirpath.exists():
            return f"Error: Directory not found: {path}"
        if not dirpath.is_dir():
            return f"Error: Not a directory: {path}"
        results = []
        try:
            for p in sorted(dirpath.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                prefix = "📁" if p.is_dir() else "📄"
                size = ""
                if p.is_file():
                    try:
                        st = p.stat().st_size
                        size = f" ({st:,}B)" if st < 1024 else f" ({st//1024:,}KB)"
                    except Exception:
                        pass
                results.append(f"{prefix} {p.name}{size}")
        except PermissionError:
            return "Error: Permission denied"
        except Exception as e:
            return f"Error listing directory: {e}"
        return "\n".join(results)

    def _tool_execute_command(self, command: str, timeout: int = None) -> str:
        timeout = timeout or self.config.get('max_shell_timeout', 120)
        for pat in DENY_PATTERNS:
            if re.search(pat, command, re.IGNORECASE):
                return "BLOCKED: Command matches dangerous pattern. Run it directly in your terminal if you're sure."
        try:
            result = subprocess.run(
                command, shell=True, cwd=self.cwd, capture_output=True,
                text=True, timeout=timeout, env={**os.environ, 'TERM': 'dumb'},
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += ("\nSTDERR:\n" + result.stderr) if output else result.stderr
            if not output:
                output = f"(no output, exit code: {result.returncode})"
            max_lines = self.config.get('max_output_lines', 200)
            lines = output.splitlines()
            if len(lines) > max_lines:
                head = "\n".join(lines[:20])
                tail = "\n".join(lines[-20:])
                output = f"{head}\n\n... ({len(lines) - 40} lines truncated) ...\n\n{tail}"
            if result.returncode != 0:
                output += f"\nExit code: {result.returncode}"
            return output
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout}s"
        except Exception as e:
            return f"Error executing command: {e}"

    def _tool_web_search(self, query: str, num_results: int = 5) -> str:
        if not self.config.get('web_search_enabled', True):
            return "Error: Web search is disabled in config."
        # Check cache first
        cache_key = f"search:{query}:{num_results}"
        cached = self.web_cache.get(cache_key)
        if cached:
            return cached + "\n(cached result)"
        try:
            url = f"https://lite.duckduckgo.com/lite/?q={url_quote(query)}"
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                soup = BeautifulSoup(resp.text, 'html.parser')
            results = []
            for a in soup.find_all('a'):
                href = a.get('href', '')
                title = a.get_text(strip=True)
                if href.startswith('http') and 'duckduckgo' not in href and 'duck' not in href and title and len(title) > 5:
                    if len(results) < num_results:
                        results.append(f"{len(results)+1}. {title}\n   {href}")
            if not results:
                return f"No results found for '{query}'"
            result_text = "\n\n".join(results)
            self.web_cache.set(cache_key, result_text)
            return result_text
        except httpx.TimeoutException:
            return "Error: Search request timed out"
        except Exception as e:
            return f"Error searching: {e}"

    def _tool_fetch_url(self, url: str, max_length: int = 5000) -> str:
        cache_key = f"fetch:{url}:{max_length}"
        cached = self.web_cache.get(cache_key)
        if cached:
            return cached + "\n(cached result)"
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
                tag.decompose()
            text = soup.get_text(separator='\n', strip=True)
            text = re.sub(r'\n{3,}', '\n\n', text)
            if len(text) > max_length:
                text = text[:max_length] + f"\n\n... (truncated at {max_length} chars)"
            title = soup.title.string.strip() if soup.title and soup.title.string else url
            result = f"Title: {title}\nURL: {url}\n\n{text}"
            self.web_cache.set(cache_key, result)
            return result
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} for {url}"
        except httpx.TimeoutException:
            return "Error: Request timed out"
        except Exception as e:
            return f"Error fetching URL: {e}"

    def _tool_scrape_page(self, url: str, wait_for: str = '', max_length: int = 5000) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ("Error: Playwright not installed.\n"
                    "Run: pip install playwright && playwright install chromium\n"
                    "Falling back to fetch_url (may miss JS-rendered content).")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, timeout=30000, wait_until='domcontentloaded')
                if wait_for:
                    page.wait_for_selector(wait_for, timeout=10000)
                else:
                    page.wait_for_timeout(2000)
                content = page.content()
                browser.close()
            soup = BeautifulSoup(content, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
                tag.decompose()
            text = soup.get_text(separator='\n', strip=True)
            text = re.sub(r'\n{3,}', '\n\n', text)
            if len(text) > max_length:
                text = text[:max_length] + f"\n\n... (truncated at {max_length} chars)"
            title = soup.title.string.strip() if soup.title and soup.title.string else url
            return f"Title: {title}\nURL: {url}\n(Scraped with Playwright)\n\n{text}"
        except Exception as e:
            return f"Error scraping page: {e}"

    def _tool_ask_user(self, question: str, options: list = None) -> str:
        return f"ASK_USER:{json.dumps({'question': question, 'options': options or []})}"

    def _tool_write_note(self, category: str, content: str) -> str:
        self.db.add_note(category, content)
        return f"Note saved: [{category}] {content}"


# ──────────────────────────────────────────────────────────────
# SECTION 10: MCP CLIENT
# ──────────────────────────────────────────────────────────────

class MCPClient:
    """Basic MCP client for HTTP servers."""

    def __init__(self):
        self.servers = {}
        self.available_tools = {}

    def add_server(self, name: str, url: str, auth: str = None):
        self.servers[name] = {'url': url.rstrip('/'), 'auth': auth}
        self._discover_tools(name)

    def _discover_tools(self, server_name: str):
        server = self.servers.get(server_name)
        if not server:
            return
        try:
            headers = {}
            if server['auth']:
                headers['Authorization'] = f"Bearer {server['auth']}"
            with httpx.Client(timeout=10) as client:
                resp = client.post(f"{server['url']}/tools/list", json={}, headers=headers)
                data = resp.json()
                for tool in data.get('tools', []):
                    tool_name = tool.get('name', '')
                    if tool_name and tool_name not in TOOL_NAMES:  # Avoid conflicts
                        prefixed = f"mcp_{server_name}_{tool_name}"
                        self.available_tools[prefixed] = {
                            'server': server_name, 'name': tool_name,
                            'description': tool.get('description', ''),
                            'schema': tool.get('inputSchema', {}),
                        }
        except Exception:
            pass

    def call_tool(self, full_name: str, arguments: dict) -> str:
        tool_info = self.available_tools.get(full_name)
        if not tool_info:
            return f"Error: MCP tool '{full_name}' not found"
        server = self.servers.get(tool_info['server'])
        if not server:
            return f"Error: MCP server '{tool_info['server']}' not found"
        try:
            headers = {}
            if server['auth']:
                headers['Authorization'] = f"Bearer {server['auth']}"
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{server['url']}/tools/call",
                    json={'name': tool_info['name'], 'arguments': arguments},
                    headers=headers,
                )
                data = resp.json()
                contents = data.get('content', [])
                return contents[0].get('text', json.dumps(contents)) if contents else json.dumps(data)
        except Exception as e:
            return f"MCP error: {e}"

    def list_available(self):
        return list(self.available_tools.keys())


# ──────────────────────────────────────────────────────────────
# SECTION 11: PERMISSION SYSTEM (Consistent property access)
# ──────────────────────────────────────────────────────────────

class PermissionSystem:
    def __init__(self, config: PincerConfig, console: Console):
        self.config = config
        self.console = console
        self.session_approvals = set()

    @property
    def trust_level(self):
        return self.config.trust_level  # Consistent: always via property

    def check(self, tool_name: str, args: dict) -> bool:
        trust = self.trust_level  # Consistent property access
        if tool_name in ('read_file', 'list_directory', 'search_files') and trust != 'plan':
            return True
        if tool_name in ('write_note', 'ask_user'):
            return True
        if tool_name in ('web_search', 'fetch_url', 'scrape_page') and trust != 'plan':
            return True
        if tool_name in ('write_file', 'edit_file'):
            if trust in ('auto', 'dontAsk', 'acceptEdits') or 'file_edits' in self.session_approvals:
                return True
            if trust == 'plan':
                return self._ask_permission(tool_name, args)
        if tool_name == 'execute_command':
            command = args.get('command', '')
            for pat in DENY_PATTERNS:
                if re.search(pat, command, re.IGNORECASE):
                    self.console.print(f"\n[{C['error']}]🚨 Blocked dangerous command:[/{C['error']}] [{C['text_dim']}]{command}[/{C['text_dim']}]")
                    return False
            for pat in SAFE_PATTERNS:
                if re.search(pat, command, re.IGNORECASE) and trust != 'plan':
                    return True
            approval_key = f"cmd:{command.split()[0] if command.split() else command}"
            if approval_key in self.session_approvals:
                return True
            if trust == 'dontAsk':
                return True
            return self._ask_permission(tool_name, args)
        if trust == 'plan':
            return self._ask_permission(tool_name, args)
        return True

    def _ask_permission(self, tool_name: str, args: dict) -> bool:
        self.console.print()
        if tool_name == 'execute_command':
            cmd = args.get('command', '')
            self.console.print(Panel(
                f"[{C['text_bright']}]$ {cmd}[/{C['text_bright']}]",
                title=f"[{C['warning']}]⚠️  Allow this command?[/{C['warning']}]",
                border_style=C['warning'], padding=(1, 2),
            ))
            self.console.print(f"  [{C['text_dim']}]\\[Y] Allow once  \\[A] Allow all '{cmd.split()[0] if cmd.split() else cmd}'  \\[N] Deny  \\[E] Edit[/{C['text_dim']}]", highlight=False)
        elif tool_name in ('write_file', 'edit_file'):
            path = args.get('path', '?')
            action = "edit" if tool_name == 'edit_file' else "write"
            self.console.print(Panel(
                f"[{C['text_bright']}]{action}: {path}[/{C['text_bright']}]",
                title=f"[{C['warning']}]⚠️  Allow file {action}?[/{C['warning']}]",
                border_style=C['warning'], padding=(1, 2),
            ))
            self.console.print(f"  [{C['text_dim']}]\\[Y] Allow once  \\[A] Allow all file edits  \\[N] Deny[/{C['text_dim']}]", highlight=False)
        else:
            self.console.print(Panel(
                f"[{C['text_bright']}]{tool_name}({json.dumps(args, default=str)[:100]})[/{C['text_bright']}]",
                title=f"[{C['warning']}]⚠️  Allow?[/{C['warning']}]",
                border_style=C['warning'], padding=(1, 2),
            ))
            self.console.print(f"  [{C['text_dim']}]\\[Y] Allow  \\[N] Deny[/{C['text_dim']}]", highlight=False)
        try:
            choice = input(f"\n{ANSI['amber']}{MASCOT}❓ > {ANSI['reset']}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            self.console.print(f"\n[{C['text_dim']}]Denied.[/{C['text_dim']}]")
            return False
        if choice in ('y', 'yes', ''):
            return True
        elif choice == 'a':
            if tool_name == 'execute_command':
                cmd = args.get('command', '')
                self.session_approvals.add(f"cmd:{cmd.split()[0] if cmd.split() else cmd}")
            elif tool_name in ('write_file', 'edit_file'):
                self.session_approvals.add('file_edits')
            return True
        elif choice == 'e' and tool_name == 'execute_command':
            old_cmd = args.get('command', '')
            self.console.print(f"[{C['text_dim']}]Current: {old_cmd}[/{C['text_dim']}]")
            try:
                new_cmd = input(f"{ANSI['cyan']}Edit: {ANSI['reset']}").strip()
                if new_cmd:
                    args['command'] = new_cmd
                    return True
            except (EOFError, KeyboardInterrupt):
                pass
        self.console.print(f"[{C['text_dim']}]Denied.[/{C['text_dim']}]")
        return False


# ──────────────────────────────────────────────────────────────
# SECTION 12: CONTEXT MANAGER (Dynamic token counting)
# ──────────────────────────────────────────────────────────────

class ContextManager:
    SYSTEM_TEMPLATE = """You are Pincer, an autonomous coding agent running locally via Ollama.

## Your Identity
- You are Pinch 🦞, a helpful, thorough, and careful coding assistant.
- You are transparent: always show your reasoning before acting.

## Your Capabilities
- Read, write, and edit files
- Execute shell commands
- Search the web for information
- Fetch and scrape web pages
- Manage project structure
- Write and run tests
- Write self-notes to remember important findings

## Your Rules
1. ALWAYS use tools to accomplish tasks. Don't just describe what to do — do it.
2. Before writing code, read relevant files to understand the codebase.
3. After making changes, verify they work (run tests, check syntax).
4. Write self-notes (write_note tool) about important findings, user preferences, and patterns.
5. Ask the user for clarification (ask_user tool) if the request is ambiguous.
6. Show your thinking before taking action.
7. Be concise but thorough.
8. If something fails, diagnose and fix it before asking the user.
9. Never output harmful or malicious content.
10. Respect the user's code style and project conventions.

## Current Environment
- Working directory: {cwd}
- User: {user_name}
- Primary language: {language}

## Memory
{memory}

## User Notes
{notes}
"""

    def __init__(self, config: PincerConfig, memory: MemorySystem, backend: OllamaBackend):
        self.config = config
        self.memory = memory
        self.backend = backend

    def build_system_prompt(self) -> str:
        return self.SYSTEM_TEMPLATE.format(
            cwd=str(Path.cwd()), user_name=self.config.user_name,
            language=self.config.get('language', 'python'),
            memory=self.memory.get_procedural_memory()[:1000],
            notes=self.memory.get_notes_for_context(5),
        )

    def assemble_context(self, conversation_messages: list, user_input: str = None) -> list:
        messages = []
        system_prompt = self.build_system_prompt()
        messages.append({'role': 'system', 'content': system_prompt})
        # FIX: Use dynamically calculated tool schema tokens
        system_tokens = self.backend.count_tokens_approx(system_prompt)
        tool_tokens = self.backend.tool_schema_tokens
        budget = MAX_CONTEXT_TOKENS - system_tokens - tool_tokens - MEMORY_TOKENS - 500
        history_messages = []
        used_tokens = 0
        for msg in reversed(conversation_messages):
            msg_tokens = self.backend.count_tokens_approx(msg.get('content', ''))
            if used_tokens + msg_tokens > budget:
                break
            history_messages.insert(0, msg)
            used_tokens += msg_tokens
        messages.extend(history_messages)
        if user_input:
            messages.append({'role': 'user', 'content': user_input})
        return messages

    def compact_context(self, messages: list) -> list:
        if not messages:
            return messages
        # Layer 1: Trim long tool outputs
        compacted = []
        for msg in messages:
            content = msg.get('content', '')
            if len(content) > 2000:
                content = content[:500] + f"\n... (truncated from {len(content)} chars) ...\n" + content[-500:]
                msg = {**msg, 'content': content}
            compacted.append(msg)
        # Layer 2: Preserve complete turns (tool_call + tool_result pairs stay together)
        if len(compacted) > 15:
            turns = self._group_into_turns(compacted)
            if len(turns) > 8:
                essential_start = turns[:2]
                essential_end = turns[-6:]
                middle = turns[2:-6]
                if middle:
                    middle_text = self._concat_turn_summaries(middle)
                    summary_msg = {'role': 'system', 'content': f'[Earlier conversation summary: {middle_text}]'}
                    compacted = []
                    for turn in essential_start:
                        compacted.extend(turn)
                    compacted.append(summary_msg)
                    for turn in essential_end:
                        compacted.extend(turn)
        return compacted

    def _group_into_turns(self, messages: list) -> list:
        turns = []
        current_turn = []
        for msg in messages:
            role = msg.get('role', '')
            has_tool_calls = 'tool_calls' in msg and msg['tool_calls']
            if role == 'system' and current_turn:
                turns.append(current_turn)
                current_turn = [msg]
            elif role == 'user' and current_turn:
                turns.append(current_turn)
                current_turn = [msg]
            elif role == 'assistant' and has_tool_calls:
                if current_turn:
                    turns.append(current_turn)
                current_turn = [msg]
            elif role == 'tool':
                current_turn.append(msg)
            elif role == 'assistant' and current_turn:
                prev_roles = [m.get('role') for m in current_turn]
                if 'tool' in prev_roles:
                    turns.append(current_turn)
                    current_turn = [msg]
                else:
                    turns.append(current_turn)
                    current_turn = [msg]
            else:
                current_turn.append(msg)
        if current_turn:
            turns.append(current_turn)
        return turns

    def _concat_turn_summaries(self, turns: list) -> str:
        parts = []
        for turn in turns:
            for msg in turn:
                role = msg.get('role', '')
                content = msg.get('content', '')[:80]
                if content:
                    parts.append(f"{role}: {content}...")
        return " | ".join(parts[:8])[:600]

    def estimate_tokens(self, messages: list) -> int:
        return sum(self.backend.count_tokens_approx(msg.get('content', '')) for msg in messages)


# ──────────────────────────────────────────────────────────────
# SECTION 13: UI RENDERER (Syntax Highlighting + Layouts + History)
# ──────────────────────────────────────────────────────────────

class UIRenderer:
    def __init__(self, config: PincerConfig):
        self.config = config
        self.console = Console(theme=DEEP_OCEAN, highlight=False)
        self.user_name = config.user_name
        self.session_start = time.time()
        self.turn_count = 0
        self.llm_calls = 0
        self.commands_run = 0
        self.files_written = 0
        self.errors_count = 0
        self._layout_mode = config.layout_mode

    def show_banner(self):
        self.console.print()
        self.console.print(Panel(
            f"[{C['primary_br']}]🦞  P I N C E R  v{VERSION}[/{C['primary_br']}]\n"
            f"[{C['text_dim']}]Your Autonomous Coding Companion — Blue Lobster Edition[/{C['text_dim']}]",
            border_style=C['primary'], padding=(1, 4),
        ))
        self.console.print()

    def show_welcome(self):
        self.console.print(f"[{C['accent']}]Welcome to Pincer! 🦞[/{C['accent']}]")
        self.console.print(f"[{C['text_dim']}]Type your message, or /help for commands.[/{C['text_dim']}]")
        self.console.print()

    def show_thinking_start(self):
        self.console.print(f"[{C['thinking']}]🦞💭 Thinking...[/{C['thinking']}]")

    def show_thinking_block(self, thinking: str):
        if not thinking.strip():
            return
        display = thinking[:600] + f"\n... ({len(thinking)} chars) ..." + thinking[-400:] if len(thinking) > 1200 else thinking
        self.console.print(Panel(
            f"[{C['thinking']}]{display}[/{C['thinking']}]",
            title=f"[{C['thinking']}]🦞💭 Thinking[/{C['thinking']}]",
            border_style=C['thinking'], padding=(0, 1),
        ))

    def show_activity(self, activity: str):
        labels = {
            'thinking': ('🦞💭', C['thinking'], 'Thinking...'),
            'planning': ('🦞📋', C['planning'], 'Planning...'),
            'working':  ('🦞⚡', C['working'],  'Working...'),
            'searching':('🦞🔍', C['searching'],'Searching the web...'),
            'reading':  ('🦞📖', C['reading'],  'Reading files...'),
            'writing':  ('🦞✏️', C['writing'],  'Writing files...'),
            'fetching': ('🦞🌐', C['searching'],'Fetching URL...'),
            'scraping': ('🦞🌐', C['searching'],'Scraping page (Playwright)...'),
            'compacting':('🦞🔄',C['text_dim'], 'Compacting context...'),
        }
        icon, color, label = labels.get(activity, ('🦞', C['primary'], activity))
        self.console.print(f"[{color}]{icon} {label}[/{color}]")

    def show_tool_call(self, tool_name: str, args: dict):
        self.llm_calls += 1
        icon_map = {
            'read_file': '📖', 'write_file': '✏️', 'edit_file': '✏️',
            'search_files': '🔍', 'list_directory': '📁', 'execute_command': '⚡',
            'web_search': '🔍', 'fetch_url': '🌐', 'scrape_page': '🌐',
            'ask_user': '❓', 'write_note': '📝',
        }
        icon = icon_map.get(tool_name, '🔧')
        args_parts = []
        for k, v in args.items():
            v_str = str(v)
            if len(v_str) > 80:
                v_str = v_str[:77] + "..."
            args_parts.append(f"{k}: {v_str}")
        args_text = f"[{C['text_dim']}], [{C['text_dim']}]".join(args_parts)
        self.console.print(f"  [{C['accent']}]{icon} {tool_name}[/{C['accent']}]([{C['text_dim']}]{args_text}[/{C['text_dim']}])")
        if tool_name == 'execute_command':
            self.commands_run += 1
        elif tool_name in ('write_file', 'edit_file'):
            self.files_written += 1

    def show_tool_result(self, tool_name: str, result: str, duration: float = 0):
        is_error = result.startswith("Error:") or result.startswith("BLOCKED:")
        is_ask = result.startswith("ASK_USER:")
        if is_ask:
            return
        is_diff_result = False
        parsed = None
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and 'diff' in parsed:
                is_diff_result = True
        except (json.JSONDecodeError, TypeError):
            pass
        if is_error:
            self.errors_count += 1
            self.console.print(f"    [{C['error']}]✗[/{C['error']}] {result}")
        elif is_diff_result and parsed:
            self._show_diff_result(parsed, duration)
        elif tool_name == 'execute_command' and not is_error:
            self._show_command_result(result, duration)
        elif tool_name == 'read_file' and not is_error:
            self._show_file_result(result, duration)
        elif tool_name in ('write_file', 'edit_file') and not is_error:
            try:
                wp = json.loads(result)
                self.console.print(f"    [{C['success']}]✓[/{C['success']}] {wp.get('action','Wrote')} {wp.get('path','?')} ({wp.get('lines','?')} lines)")
            except (json.JSONDecodeError, TypeError):
                self.console.print(f"    [{C['success']}]✓[/{C['success']}] {result}")
        elif tool_name == 'web_search' and not is_error:
            self._show_search_result(result)
        elif tool_name in ('fetch_url', 'scrape_page') and not is_error:
            self._show_fetch_result(result)
        else:
            display = result[:500] if len(result) <= 500 else result[:250] + f"\n... ({len(result)} chars) ...\n" + result[-200:]
            self.console.print(f"    [{C['success']}]✓[/{C['success']}] {display}")
        self.console.print()

    def _show_diff_result(self, parsed: dict, duration: float = 0):
        diff_text = parsed.get('diff', '')
        summary = parsed.get('summary', '')
        path = parsed.get('path', '?')
        if not diff_text:
            self.console.print(f"    [{C['success']}]✓[/{C['success']}] {summary}")
            return
        diff_lines = diff_text.splitlines()
        table_lines = []
        for line in diff_lines:
            if line.startswith('---') or line.startswith('+++'):
                table_lines.append(f"[{C['text_dim']}]{line}[/{C['text_dim']}]")
            elif line.startswith('@@'):
                table_lines.append(f"[{C['info']}]{line}[/{C['info']}]")
            elif line.startswith('+'):
                table_lines.append(f"[{C['diff_add']}]{line}[/{C['diff_add']}]")
            elif line.startswith('-'):
                table_lines.append(f"[{C['diff_del']}]{line}[/{C['diff_del']}]")
            else:
                table_lines.append(f"[{C['text']}]{line}[/{C['text']}]")
        content = "\n".join(table_lines)
        dur_text = f" [{C['text_dim']}]{duration:.1f}s[/{C['text_dim']}]" if duration else ""
        self.console.print(Panel(content,
            title=f"[{C['writing']}]📝 Diff: {path}[/{C['writing']}]{dur_text}",
            border_style=C['border'], padding=(0, 1),
        ))
        self.console.print(f"    [{C['success']}]✓[/{C['success']}] {summary}")

    def _show_command_result(self, result: str, duration: float = 0):
        lines = result.splitlines()
        if len(lines) > 30:
            display = "\n".join(lines[:15]) + f"\n[{C['text_muted']}]... ({len(lines) - 25} lines hidden) ...[/{C['text_muted']}]\n" + "\n".join(lines[-10:])
        else:
            display = result
        dur_text = f" [{C['text_dim']}]{duration:.1f}s[/{C['text_dim']}]" if duration else ""
        self.console.print(Panel(f"[{C['text']}]{display}[/{C['text']}]",
            title=f"[{C['success']}]⚡ Output[/{C['success']}]{dur_text}",
            border_style=C['border'], padding=(0, 1),
        ))

    def _show_file_result(self, result: str, duration: float = 0):
        """Show file content with syntax highlighting — NEW: detect language."""
        lines = result.splitlines()
        # Try to extract path from header for syntax detection
        path_match = re.match(r'File: (.+?) \(', lines[0]) if lines else None
        file_path = path_match.group(1) if path_match else None

        if len(lines) > 40:
            display_text = "\n".join(lines[:20]) + f"\n[{C['text_muted']}]... ({len(lines) - 30} lines hidden) ...[/{C['text_muted']}]\n" + "\n".join(lines[-10:])
            self.console.print(Panel(f"[{C['text']}]{display_text}[/{C['text']}]",
                title=f"[{C['reading']}]📖 File[/{C['reading']}]",
                border_style=C['border'], padding=(0, 1),
            ))
        else:
            # Try syntax highlighting for smaller files
            if file_path and len(lines) > 2:
                try:
                    # Extract just the code (skip header line)
                    code_lines = []
                    for line in lines[1:]:
                        match = re.match(r'^\s*\d+\s*\|\s(.*)$', line)
                        if match:
                            code_lines.append(match.group(1))
                        else:
                            code_lines.append(line)
                    code = "\n".join(code_lines)
                    lexer = guess_lexer_for_filename(file_path, code)
                    if not isinstance(lexer, TextLexer):
                        syntax = Syntax(code, lexer.name, theme="monokai", line_numbers=True)
                        self.console.print(Panel(syntax,
                            title=f"[{C['reading']}]📖 {Path(file_path).name}[/{C['reading']}]",
                            border_style=C['border'], padding=(0, 1),
                        ))
                        return
                except Exception:
                    pass
            self.console.print(Panel(f"[{C['text']}]{result}[/{C['text']}]",
                title=f"[{C['reading']}]📖 File[/{C['reading']}]",
                border_style=C['border'], padding=(0, 1),
            ))

    def _show_search_result(self, result: str):
        self.console.print(Panel(f"[{C['text']}]{result}[/{C['text']}]",
            title=f"[{C['searching']}]🔍 Results[/{C['searching']}]",
            border_style=C['border'], padding=(0, 1),
        ))

    def _show_fetch_result(self, result: str):
        display = result[:1500] + f"\n... ({len(result)} chars) ..." + result[-1000:] if len(result) > 3000 else result
        self.console.print(Panel(f"[{C['text']}]{display}[/{C['text']}]",
            title=f"[{C['searching']}]🌐 Page[/{C['searching']}]",
            border_style=C['border'], padding=(0, 1),
        ))

    def show_ask_user(self, question: str, options: list = None):
        self.console.print()
        if options:
            opts_text = "\n".join(f"  [{C['accent']}]{i+1}.[/{C['accent']}] {opt}" for i, opt in enumerate(options))
            self.console.print(Panel(
                f"[{C['text_bright']}]{question}[/{C['text_bright']}]\n\n{opts_text}",
                title=f"[{C['warning']}]🦞❓ Question[/{C['warning']}]",
                border_style=C['warning'], padding=(1, 2),
            ))
        else:
            self.console.print(Panel(
                f"[{C['text_bright']}]{question}[/{C['text_bright']}]",
                title=f"[{C['warning']}]🦞❓ Question[/{C['warning']}]",
                border_style=C['warning'], padding=(1, 2),
            ))

    def show_error(self, message: str):
        self.console.print(f"\n[{C['error']}]🦞✗ {message}[/{C['error']}]")
        self.errors_count += 1

    def show_warning(self, message: str):
        self.console.print(f"[{C['warning']}]⚠️  {message}[/{C['warning']}]")

    def show_info(self, message: str):
        self.console.print(f"[{C['info']}]ℹ️  {message}[/{C['info']}]")

    def show_success(self, message: str):
        self.console.print(f"[{C['success']}]🦞✓ {message}[/{C['success']}]")

    def show_permission_denied(self, tool_name: str):
        self.console.print(f"[{C['text_dim']}]⊘ Permission denied for {tool_name}[/{C['text_dim']}]")

    def play_sound(self, sound_type: str = 'bell'):
        if self.config.get('sound_notifications', True):
            sys.stdout.write('\a')
            sys.stdout.flush()

    def show_status_bar(self, state: str = 'idle', context_tokens: int = 0):
        state_icons = {
            'idle': (f'{MASCOT}💤', C['text_dim']),
            'thinking': (f'{MASCOT}💭', C['thinking']),
            'planning': (f'{MASCOT}📋', C['planning']),
            'working':  (f'{MASCOT}⚡', C['working']),
            'searching':(f'{MASCOT}🔍', C['searching']),
            'reading':  (f'{MASCOT}📖', C['reading']),
            'writing':  (f'{MASCOT}✏️', C['writing']),
            'error':    (f'{MASCOT}✗', C['error']),
            'success':  (f'{MASCOT}✓', C['success']),
        }
        icon, color = state_icons.get(state, (MASCOT, C['primary']))
        elapsed = int(time.time() - self.session_start)
        ctx_ratio = context_tokens / MAX_CONTEXT_TOKENS if MAX_CONTEXT_TOKENS else 0
        ctx_color = C['success'] if ctx_ratio < 0.5 else C['warning'] if ctx_ratio < 0.75 else C['error']
        filled = int(ctx_ratio * 12)
        self.console.print(Rule(
            f"[{color}]{icon}[/{color}] │ "
            f"[{C['text_dim']}]{self.user_name}[/{C['text_dim']}] │ "
            f"[{ctx_color}]ctx {context_tokens:,}/{MAX_CONTEXT_TOKENS:,}[/{ctx_color}] │ "
            f"[{C['text_dim']}]{elapsed//60}m{elapsed%60:02d}s[/{C['text_dim']}] │ "
            f"[{C['text_dim']}]{self.config.model}[/{C['text_dim']}] │ "
            f"[{C['text_dim']}]{self.llm_calls} calls[/{C['text_dim']}]",
            style=C['border'], characters="─",
        ))

    # ── File Tree ──

    def show_file_tree(self, path: str = None):
        dirpath = Path(path) if path else Path.cwd()
        if not dirpath.exists():
            self.show_error(f"Directory not found: {dirpath}")
            return
        tree = Tree(f"📁 {dirpath.name}", guide_style=C['border'])
        self._build_tree(tree, dirpath, max_depth=3, current_depth=0)
        self.console.print(tree)

    def _build_tree(self, tree, path: Path, max_depth: int, current_depth: int):
        if current_depth >= max_depth:
            return
        try:
            items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        skip = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.tox',
                '.mypy_cache', '.pytest_cache', '.ruff_cache', 'dist', 'build', '.eggs'}
        for item in items:
            if item.name.startswith('.') and item.name not in ('.env', '.gitignore'):
                continue
            if item.name in skip:
                continue
            if item.is_dir():
                branch = tree.add(f"📁 {item.name}/")
                self._build_tree(branch, item, max_depth, current_depth + 1)
            else:
                ext_map = {"py": "🐍", "js": "📜", "ts": "📜", "md": "📄",
                           "json": "📋", "yaml": "📋", "yml": "📋", "toml": "📋",
                           "rs": "🦀", "go": "🔵", "rb": "💎"}
                icon = ext_map.get(item.suffix.lstrip('.'), "📄")
                try:
                    st = item.stat().st_size
                    size = f" [{C['text_muted']}]{st//1024}KB[/{C['text_muted']}]" if st > 1024 else f" [{C['text_muted']}]{st}B[/{C['text_muted']}]"
                except Exception:
                    size = ""
                tree.add(f"{icon} {item.name}{size}")

    # ── Sidebar for Panel Mode ──

    def show_sidebar(self, plan: list = None, current_step: int = 0):
        """Render sidebar layout for panel mode."""
        cwd = Path.cwd()
        tree = Tree(f"📁 {cwd.name}", guide_style=C['border'])
        self._build_tree(tree, cwd, max_depth=2, current_depth=0)

        layout = Layout()
        layout.split_column(
            Layout(name="files", ratio=3),
            Layout(name="plan", size=max(8, min(15, len(plan) + 4))) if plan else Layout(name="plan", size=4),
        )
        layout["files"].update(Panel(tree, title="📁 Files", border_style=C['border']))

        if plan:
            plan_lines = []
            for i, step in enumerate(plan[:10]):
                if i < current_step:
                    icon = f"[{C['success']}]✓[/{C['success']}]"
                elif i == current_step:
                    icon = f"[{C['primary_br']}]→[/{C['primary_br']}]"
                else:
                    icon = f"[{C['text_muted']}]○[/{C['text_muted']}]"
                plan_lines.append(f" {icon} {i+1}. {step[:40]}")
            layout["plan"].update(Panel("\n".join(plan_lines), title="📋 Plan", border_style=C['planning']))
        else:
            layout["plan"].update(Panel(f"[{C['text_muted']}]No active plan[/{C['text_muted']}]", title="📋 Plan", border_style=C['border']))

        self.console.print(layout)

    def show_plan(self, plan: list, current_step: int = 0):
        lines = []
        for i, step in enumerate(plan):
            if i < current_step:
                icon = f"[{C['success']}]✓[/{C['success']}]"
            elif i == current_step:
                icon = f"[{C['primary_br']}]→[/{C['primary_br']}]"
            else:
                icon = f"[{C['text_muted']}]○[/{C['text_muted']}]"
            lines.append(f"  {icon} {i+1}. {step}")
        self.console.print(Panel("\n".join(lines),
            title=f"[{C['planning']}]🦞📋 Plan ({len(plan)} steps)[/{C['planning']}]",
            border_style=C['planning'], padding=(0, 1),
        ))

    def show_task_summary(self, plan: list, current_step: int, elapsed: float, stats: dict):
        self.console.print()
        self.console.print(Panel(
            f"[{C['success']}]🦞✓ Task Complete![/{C['success']}]\n\n"
            f"  Steps: [{C['text_bright']}]{current_step}/{len(plan)}[/{C['text_bright']}]  "
            f"Time: [{C['text_bright']}]{elapsed/60:.1f}m[/{C['text_bright']}]  "
            f"LLM: [{C['text_bright']}]{stats.get('llm_calls',0)}[/{C['text_bright']}]  "
            f"Cmds: [{C['text_bright']}]{stats.get('commands',0)}[/{C['text_bright']}]  "
            f"Writes: [{C['text_bright']}]{stats.get('writes',0)}[/{C['text_bright']}]  "
            f"Errors: [{C['text_bright']}]{stats.get('errors',0)}[/{C['text_bright']}]",
            border_style=C['success'], padding=(1, 2),
        ))
        self.console.print()
        self.play_sound('bell')

    def show_tool_history(self, history: list):
        if not history:
            self.show_info("No tool calls in this session yet.")
            return
        table = Table(title="📜 Tool History", border_style=C['border'], title_style=C['accent'])
        table.add_column("#", style=C['text_dim'], width=4)
        table.add_column("Tool", style=C['accent'], width=15)
        table.add_column("Args", style=C['text'], width=40)
        table.add_column("Duration", style=C['text_dim'], width=8)
        table.add_column("OK", width=3)
        for i, (tool_name, args, result_preview, duration, success, created) in enumerate(reversed(history)):
            icon = f"[{C['success']}]✓[/{C['success']}]" if success else f"[{C['error']}]✗[/{C['error']}]"
            args_display = args[:40] if args else ""
            dur = f"{duration:.1f}s" if duration else "-"
            table.add_row(str(len(history) - i), tool_name, args_display, dur, icon)
        self.console.print(table)

    def show_help(self):
        table = Table(title="🦞 Pincer Commands", show_header=True, border_style=C['border'], title_style=C['primary_br'])
        table.add_column("Command", style=C['accent'], width=22)
        table.add_column("Description", style=C['text'], width=50)
        for cmd, desc in [
            ("/task <goal>", "Start autonomous task mode"),
            ("/plan <goal>", "Generate a plan without executing"),
            ("/search <query>", "Search the web"),
            ("/model [name]", "Show or switch model"),
            ("/think <on|off|auto>", "Toggle thinking mode"),
            ("/trust <level>", "Change trust level"),
            ("/layout <mode>", "Switch layout (stream|panel|compact)"),
            ("/compact", "Force context compaction"),
            ("/checkpoint", "Save a checkpoint"),
            ("/rollback", "Rollback to last checkpoint"),
            ("/notes [query]", "View/search agent self-notes"),
            ("/files", "Show file tree"),
            ("/history", "Show recent tool calls"),
            ("/sessions", "List conversation sessions"),
            ("/export [md|json]", "Export session"),
            ("/context", "Preview current context window"),
            ("/mcp <add|list>", "MCP server management"),
            ("/config", "Show configuration"),
            ("/doctor", "Run diagnostics"),
            ("/stats", "Show session statistics"),
            ("/help", "Show this help"),
            ("/exit", "Exit Pincer"),
        ]:
            table.add_row(cmd, desc)
        self.console.print(table)

    def show_keybindings(self):
        table = Table(title="⌨️  Key Bindings", show_header=True, border_style=C['border'], title_style=C['primary_br'])
        table.add_column("Key", style=C['accent'], width=25)
        table.add_column("Action", style=C['text'], width=45)
        for key, action in [("Enter", "Send message"), ("Up/Down", "Navigate input history"),
                            ("Ctrl+C", "Cancel current operation"), ("Tab", "Autocomplete commands")]:
            table.add_row(key, action)
        self.console.print(table)

    def show_stats(self):
        elapsed = int(time.time() - self.session_start)
        self.console.print(Panel(
            f"[{C['text_bright']}]Session Statistics[/{C['text_bright']}]\n\n"
            f"  Duration: [{C['accent']}]{elapsed//60}m {elapsed%60}s[/{C['accent']}]  "
            f"LLM: [{C['accent']}]{self.llm_calls}[/{C['accent']}]  "
            f"Cmds: [{C['accent']}]{self.commands_run}[/{C['accent']}]  "
            f"Files: [{C['accent']}]{self.files_written}[/{C['accent']}]  "
            f"Errors: [{C['accent']}]{self.errors_count}[/{C['accent']}]  "
            f"Turns: [{C['accent']}]{self.turn_count}[/{C['accent']}]",
            border_style=C['border'], padding=(1, 2),
        ))

    def show_context_preview(self, messages: list, total_tokens: int):
        sections = []
        for msg in messages[:15]:
            role = msg.get('role', '?')
            content = msg.get('content', '')[:80].replace('\n', ' ')
            has_tc = 'tool_calls' in msg and msg['tool_calls']
            token_est = max(1, len(msg.get('content', '')) // 4)
            icon = {'system': '📋', 'user': '👤', 'assistant': '🦞', 'tool': '🔧'}.get(role, '❓')
            tc_mark = " [+tools]" if has_tc else ""
            sections.append(f"  {icon} [{C['text_dim']}]{role}[/{C['text_dim']}] ({token_est}t{tc_mark}): {content}...")
        ctx_ratio = total_tokens / MAX_CONTEXT_TOKENS
        ctx_color = C['success'] if ctx_ratio < 0.5 else C['warning'] if ctx_ratio < 0.75 else C['error']
        extra = f"\n\n  ... {len(messages) - 15} more messages" if len(messages) > 15 else ""
        self.console.print(Panel("\n".join(sections) + extra,
            title=f"[{ctx_color}]📋 Context ({total_tokens:,}/{MAX_CONTEXT_TOKENS:,} tokens)[/{ctx_color}]",
            border_style=C['border'], padding=(0, 1),
        ))


# ──────────────────────────────────────────────────────────────
# SECTION 14: THINK PARSER
# ──────────────────────────────────────────────────────────────

class ThinkParser:
    def __init__(self):
        self.state = 'outside'
        self.tag_buf = ''
        self.content_buf = ''
        self.thinking = ''
        self.response = ''

    def feed(self, text: str) -> list:
        results = []
        for char in text:
            if self.state == 'outside':
                if char == '<':
                    self.state = 'in_tag'
                    self.tag_buf = '<'
                else:
                    self.content_buf += char
            elif self.state == 'in_tag':
                self.tag_buf += char
                if self.tag_buf == THINK_OPEN[:len(self.tag_buf)]:
                    if len(self.tag_buf) == len(THINK_OPEN):
                        if self.content_buf:
                            results.append(('response', self.content_buf))
                            self.response += self.content_buf
                            self.content_buf = ''
                        self.state = 'inside'
                        self.tag_buf = ''
                elif not THINK_OPEN.startswith(self.tag_buf):
                    self.content_buf += self.tag_buf
                    self.tag_buf = ''
                    self.state = 'outside'
            elif self.state == 'inside':
                if char == '<':
                    self.state = 'in_close_tag'
                    self.tag_buf = '<'
                else:
                    self.content_buf += char
            elif self.state == 'in_close_tag':
                self.tag_buf += char
                if self.tag_buf == THINK_CLOSE[:len(self.tag_buf)]:
                    if len(self.tag_buf) == len(THINK_CLOSE):
                        if self.content_buf:
                            results.append(('thinking', self.content_buf))
                            self.thinking += self.content_buf
                            self.content_buf = ''
                        self.state = 'outside'
                        self.tag_buf = ''
                elif not THINK_CLOSE.startswith(self.tag_buf):
                    self.content_buf += self.tag_buf
                    self.tag_buf = ''
                    self.state = 'inside'
        if self.state == 'outside' and self.content_buf:
            results.append(('response', self.content_buf))
            self.response += self.content_buf
            self.content_buf = ''
        return results

    def flush(self) -> list:
        results = []
        if self.content_buf:
            if self.state in ('inside', 'in_close_tag'):
                results.append(('thinking', self.content_buf))
                self.thinking += self.content_buf
            else:
                results.append(('response', self.content_buf))
                self.response += self.content_buf
            self.content_buf = ''
        if self.tag_buf:
            if self.state in ('inside', 'in_close_tag'):
                results.append(('thinking', self.tag_buf))
                self.thinking += self.tag_buf
            else:
                results.append(('response', self.tag_buf))
                self.response += self.tag_buf
            self.tag_buf = ''
        return results


# ──────────────────────────────────────────────────────────────
# SECTION 15: AGENT LOOP (with Timeout)
# ──────────────────────────────────────────────────────────────

class AgentLoop:
    def __init__(self, config, db, backend, tools, permissions, context, memory, ui):
        self.config = config
        self.db = db
        self.backend = backend
        self.tools = tools
        self.permissions = permissions
        self.context = context
        self.memory = memory
        self.ui = ui
        self.conversation_messages = []
        self.conv_id = None
        self._interrupted = False

    def new_conversation(self, title=""):
        self.conv_id = self.db.create_conversation(title)
        self.conversation_messages = []

    def load_conversation(self, conv_id):
        self.conv_id = conv_id
        raw_msgs = self.db.get_messages(conv_id)
        self.conversation_messages = []
        for role, content, thinking, tool_calls, tool_name in raw_msgs:
            msg = {'role': role, 'content': content}
            if thinking:
                msg['thinking'] = thinking
            if tool_calls:
                try:
                    msg['tool_calls'] = json.loads(tool_calls)
                except Exception:
                    pass
            self.conversation_messages.append(msg)

    def run(self, user_input: str) -> str:
        self._interrupted = False
        self.ui.turn_count += 1
        self.db.add_message(self.conv_id, 'user', user_input)
        self.conversation_messages.append({'role': 'user', 'content': user_input})
        final_response = ""
        turn_start_time = time.time()

        for turn in range(MAX_TURNS):
            if self._interrupted:
                final_response = "[Interrupted]"
                break

            # Timeout check: 5 minutes per outer loop
            if time.time() - turn_start_time > AGENT_LOOP_TIMEOUT:
                self.ui.show_warning(f"Agent loop timed out after {AGENT_LOOP_TIMEOUT}s")
                final_response = "[Timed out]"
                break

            messages = self.context.assemble_context(self.conversation_messages)
            token_est = self.context.estimate_tokens(messages)
            if token_est > MAX_CONTEXT_TOKENS * 0.9:
                self.ui.show_activity('compacting')
                self.conversation_messages = self.context.compact_context(self.conversation_messages)
                self.ui.show_info("Context auto-compacted")

            thinking_text = ""
            response_text = ""
            tool_calls_list = []
            should_think = self.backend.should_think()

            try:
                self.ui.show_activity('thinking')
                stream = self.backend.stream_chat(messages=messages, tools=TOOL_SCHEMAS, think=should_think)
                parser = ThinkParser()
                self.ui.console.print()
                thinking_displayed = False

                for chunk in stream:
                    if self._interrupted:
                        break
                    msg = chunk.message if hasattr(chunk, 'message') else (chunk.get('message', {}) if isinstance(chunk, dict) else None)
                    if msg is None:
                        continue
                    content = getattr(msg, 'content', None) or (msg.get('content', '') if isinstance(msg, dict) else '')
                    content = content or ''
                    tcs = []
                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        tcs = list(msg.tool_calls)
                    elif isinstance(msg, dict) and msg.get('tool_calls'):
                        tcs = list(msg['tool_calls'])
                    native_thinking = getattr(msg, 'thinking', '') or ''
                    if native_thinking:
                        if not thinking_displayed:
                            self.ui.show_thinking_start()
                            thinking_displayed = True
                        thinking_text += native_thinking
                        sys.stdout.write(f"{ANSI['purple']}{ANSI['italic']}{native_thinking}{ANSI['reset']}")
                        sys.stdout.flush()
                    if content:
                        parts = parser.feed(content)
                        for ptype, ptext in parts:
                            if ptype == 'thinking':
                                if not thinking_displayed:
                                    self.ui.show_thinking_start()
                                    thinking_displayed = True
                                thinking_text += ptext
                                sys.stdout.write(f"{ANSI['purple']}{ANSI['italic']}{ptext}{ANSI['reset']}")
                            else:
                                response_text += ptext
                                sys.stdout.write(f"{ANSI['light_blue']}{ptext}{ANSI['reset']}")
                            sys.stdout.flush()
                    for tc in tcs:
                        if hasattr(tc, 'function') and tc.function:
                            tc_name = getattr(tc.function, 'name', None) or (tc.function.get('name', '') if isinstance(tc.function, dict) else '')
                            raw_args = getattr(tc.function, 'arguments', None) or (tc.function.get('arguments', {}) if isinstance(tc.function, dict) else {})
                            # FIX #2: Parse string arguments
                            if isinstance(raw_args, str):
                                try:
                                    tc_args = json.loads(raw_args)
                                except (json.JSONDecodeError, TypeError):
                                    tc_args = {}
                            elif isinstance(raw_args, dict):
                                tc_args = raw_args
                            else:
                                tc_args = {}
                            if tc_name:
                                tool_calls_list.append({'name': tc_name, 'arguments': tc_args})

                remaining = parser.flush()
                for ptype, ptext in remaining:
                    if ptype == 'thinking':
                        thinking_text += ptext
                    else:
                        response_text += ptext
                        sys.stdout.write(f"{ANSI['light_blue']}{ptext}{ANSI['reset']}")
                        sys.stdout.flush()
                sys.stdout.write('\n')
                sys.stdout.flush()

            except KeyboardInterrupt:
                self._interrupted = True
                self.ui.console.print(f"\n[{C['warning']}]⚠️ Interrupted[/{C['warning']}]")
                break
            except RuntimeError as e:
                self.ui.show_error(f"LLM error: {e}")
                time.sleep(2)
                continue
            except Exception as e:
                self.ui.show_error(f"Unexpected: {e}")
                time.sleep(1)
                continue

            if thinking_text and not thinking_displayed:
                self.ui.show_thinking_block(thinking_text)

            if tool_calls_list:
                tc_serialized = [{'type': 'function', 'function': {'name': tc['name'], 'arguments': tc['arguments']}} for tc in tool_calls_list]
                self.db.add_message(self.conv_id, 'assistant', content=response_text, thinking=thinking_text, tool_calls=json.dumps(tc_serialized))
                self.conversation_messages.append({'role': 'assistant', 'content': response_text, 'tool_calls': tc_serialized})

                for tc in tool_calls_list:
                    tool_name = tc['name']
                    tool_args = tc['arguments'] if isinstance(tc['arguments'], dict) else {}
                    self.ui.show_tool_call(tool_name, tool_args)

                    if not self.permissions.check(tool_name, tool_args):
                        self.ui.show_permission_denied(tool_name)
                        self.db.add_message(self.conv_id, 'tool', "Permission denied by user", tool_name=tool_name)
                        self.conversation_messages.append({'role': 'tool', 'name': tool_name, 'content': "Permission denied by user"})
                        continue

                    duration = 0.0
                    if tool_name == 'ask_user':
                        question = tool_args.get('question', '')
                        options = tool_args.get('options', [])
                        self.ui.show_ask_user(question, options)
                        try:
                            answer = input(f"{ANSI['cyan']}{MASCOT}❓ Your answer: {ANSI['reset']}").strip()
                        except (EOFError, KeyboardInterrupt):
                            answer = "(no response)"
                        result = answer
                        self.ui.console.print()
                    else:
                        start_time = time.time()
                        result = self.tools.execute(tool_name, tool_args)
                        duration = time.time() - start_time

                    self.ui.show_tool_result(tool_name, result, duration)

                    # Track in tool history
                    is_success = not (result.startswith("Error:") or result.startswith("BLOCKED:"))
                    self.db.add_tool_history(
                        self.conv_id, tool_name,
                        args=json.dumps(tool_args, default=str)[:200],
                        result_preview=result[:200],
                        duration=duration,
                        success=is_success,
                    )

                    result_for_context = result
                    if len(result_for_context) > 3000:
                        result_for_context = result_for_context[:1500] + f"\n... (truncated from {len(result)} chars) ...\n" + result_for_context[-1000:]
                    self.db.add_message(self.conv_id, 'tool', result_for_context, tool_name=tool_name)
                    self.conversation_messages.append({'role': 'tool', 'name': tool_name, 'content': result_for_context})
                continue

            self.db.add_message(self.conv_id, 'assistant', content=response_text, thinking=thinking_text)
            self.conversation_messages.append({'role': 'assistant', 'content': response_text})
            final_response = response_text
            break

        self.db.update_conversation(self.conv_id)
        if self.db.count_messages(self.conv_id) <= 3 and user_input:
            self.db.update_conversation(self.conv_id, title=user_input[:60] + ("..." if len(user_input) > 60 else ""))
        return final_response

    def interrupt(self):
        self._interrupted = True

    def get_context_tokens(self):
        return self.context.estimate_tokens(self.conversation_messages)


# ──────────────────────────────────────────────────────────────
# SECTION 16: AUTONOMOUS MODE
# ──────────────────────────────────────────────────────────────

class AutonomousMode:
    GUARDRAILS = {'max_shell_commands': 50, 'max_rm_commands': 3, 'max_file_writes': 20,
                  'max_task_time_minutes': 120, 'max_no_progress_minutes': 20, 'max_llm_calls': 100}

    def __init__(self, agent, ui, db, config, memory):
        self.agent = agent
        self.ui = ui
        self.db = db
        self.config = config
        self.memory = memory
        self.plan = []
        self.current_step = 0
        self.stats = {'llm_calls': 0, 'commands': 0, 'writes': 0, 'errors': 0}
        self.start_time = 0
        self._cancelled = False

    def generate_plan(self, goal: str) -> list:
        self.ui.show_activity('planning')
        plan_prompt = f"Create a detailed, step-by-step plan for this task:\n\n{goal}\n\nThe plan should be a numbered list of specific, actionable steps. Output ONLY the numbered list."
        messages = self.agent.context.assemble_context(self.agent.conversation_messages, user_input=plan_prompt)
        try:
            should_think = self.agent.backend.should_think()
            response = self.agent.backend.chat(messages=messages, tools=None, think=should_think)
            content = response.message.content or ''
            thinking = getattr(response.message, 'thinking', '') or ''
            if thinking:
                self.ui.show_thinking_block(thinking)
            return self._parse_plan(content)
        except Exception as e:
            self.ui.show_error(f"Failed to generate plan: {e}")
            return []

    def _parse_plan(self, text: str) -> list:
        steps = []
        for line in text.splitlines():
            match = re.match(r'^\d+[\.\)]\s+(.+)', line.strip())
            if match:
                steps.append(match.group(1).strip())
        return steps if steps else [text[:200]]

    def execute_plan(self, goal: str, plan: list, auto_approve: bool = False):
        self.plan = plan
        self.current_step = 0
        self.start_time = time.time()
        self._cancelled = False
        self.stats = {'llm_calls': 0, 'commands': 0, 'writes': 0, 'errors': 0}
        self.memory.set_working('current_plan', plan)
        self.memory.set_working('current_step', 0)

        self.ui.console.print()
        self.ui.show_plan(plan, 0)

        if not auto_approve:
            self.ui.console.print(f"\n  [{C['text_dim']}]\\[Enter] Start  \\[E] Edit  \\[Q] Cancel[/{C['text_dim']}]")
            try:
                choice = input(f"\n{ANSI['cyan']}{MASCOT} > {ANSI['reset']}").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self.ui.show_info("Plan cancelled.")
                return
            if choice == 'q':
                self.ui.show_info("Plan cancelled.")
                return
            elif choice == 'e':
                self.ui.show_info("What should change?")
                try:
                    edit_input = input(f"{ANSI['cyan']}{MASCOT} > {ANSI['reset']}").strip()
                    if edit_input:
                        original = "\n".join(f"{i+1}. {s}" for i, s in enumerate(plan))
                        modified = self.generate_plan(f"Modify this plan: {goal}\nOriginal:\n{original}\n\nChange: {edit_input}")
                        if modified:
                            plan = modified
                            self.plan = plan
                            self.memory.set_working('current_plan', plan)
                            self.ui.show_plan(plan, 0)
                except (EOFError, KeyboardInterrupt):
                    pass

        for i, step in enumerate(plan):
            if self._cancelled:
                self.ui.show_warning("Task cancelled.")
                break
            self.current_step = i
            self.memory.set_working('current_step', i)
            self.ui.show_plan(plan, i)
            if not self._check_guardrails():
                break
            step_prompt = f"You are executing step {i+1} of {len(plan)} in a plan to: {goal}\n\nCurrent step: {step}\n\nExecute this step now. Use tools. After completing, briefly describe what you did."
            try:
                self.agent.run(step_prompt)
                self.stats['llm_calls'] += 1
            except KeyboardInterrupt:
                self._cancelled = True
                self.ui.show_warning("Task interrupted.")
                break
            except Exception as e:
                self.ui.show_error(f"Step {i+1} failed: {e}")
                self.stats['errors'] += 1
            self.stats['commands'] = self.ui.commands_run
            self.stats['writes'] = self.ui.files_written
            self.stats['errors'] = self.ui.errors_count
            if (i + 1) % 5 == 0:
                self._save_checkpoint(i + 1)
                self.ui.show_info(f"Checkpoint saved at step {i+1}")

        elapsed = time.time() - self.start_time
        final_step = self.current_step + 1 if not self._cancelled else self.current_step
        self.ui.show_task_summary(self.plan, final_step, elapsed, self.stats)

    def _check_guardrails(self):
        elapsed = time.time() - self.start_time
        if elapsed > self.GUARDRAILS['max_task_time_minutes'] * 60:
            self.ui.show_warning("Guardrail: Task exceeded maximum time.")
            return False
        if self.stats['errors'] > 10:
            self.ui.show_warning("Guardrail: Too many errors.")
            return False
        return True

    def _save_checkpoint(self, step_number: int):
        self.db.save_checkpoint(self.conv_id if hasattr(self, 'conv_id') else self.agent.conv_id,
            step_number, f"Step {step_number} of {len(self.plan)}",
            {'plan': self.plan, 'current_step': self.current_step, 'stats': self.stats,
             'message_count': len(self.agent.conversation_messages),
             'memory_working': self.memory.get_state()})

    def cancel(self):
        self._cancelled = True
        self.agent.interrupt()


# ──────────────────────────────────────────────────────────────
# SECTION 17: COMMAND HANDLER (with /history)
# ──────────────────────────────────────────────────────────────

class CommandHandler:
    def __init__(self, app):
        self.app = app

    def handle(self, user_input: str) -> bool:
        stripped = user_input.strip()
        if not stripped.startswith('/'):
            return False
        parts = stripped.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        handlers = {
            '/help': self._cmd_help, '/exit': self._cmd_exit, '/quit': self._cmd_exit,
            '/task': self._cmd_task, '/plan': self._cmd_plan, '/search': self._cmd_search,
            '/model': self._cmd_model, '/think': self._cmd_think, '/trust': self._cmd_trust,
            '/layout': self._cmd_layout, '/compact': self._cmd_compact,
            '/checkpoint': self._cmd_checkpoint, '/rollback': self._cmd_rollback,
            '/notes': self._cmd_notes, '/files': self._cmd_files,
            '/history': self._cmd_history, '/sessions': self._cmd_sessions,
            '/export': self._cmd_export, '/context': self._cmd_context,
            '/mcp': self._cmd_mcp, '/config': self._cmd_config,
            '/doctor': self._cmd_doctor, '/stats': self._cmd_stats,
            '/clear': self._cmd_clear, '/new': self._cmd_new,
        }
        handler = handlers.get(command)
        if handler:
            handler(args)
            return True
        matches = [k for k in handlers if k.startswith(command)]
        if len(matches) == 1:
            handlers[matches[0]](args)
            return True
        elif len(matches) > 1:
            self.app.ui.show_info(f"Ambiguous. Did you mean: {', '.join(matches)}?")
            return True
        self.app.ui.show_error(f"Unknown command: {command}. Type /help.")
        return True

    def _cmd_help(self, args):
        self.app.ui.show_help()
        self.app.ui.console.print()
        self.app.ui.show_keybindings()

    def _cmd_exit(self, args):
        self.app.ui.console.print(f"\n[{C['primary_br']}]🦞 See you later! Happy coding! 👋[/{C['primary_br']}]")
        sys.exit(0)

    def _cmd_task(self, args):
        if not args.strip():
            self.app.ui.show_error("Usage: /task <goal>")
            return
        plan = self.app.autonomous.generate_plan(args.strip())
        if plan:
            auto = self.app.config.trust_level in ('auto', 'dontAsk')
            self.app.autonomous.execute_plan(goal=args.strip(), plan=plan, auto_approve=auto)

    def _cmd_plan(self, args):
        if not args.strip():
            self.app.ui.show_error("Usage: /plan <goal>")
            return
        plan = self.app.autonomous.generate_plan(args.strip())
        if plan:
            self.app.ui.show_plan(plan)

    def _cmd_search(self, args):
        if not args.strip():
            self.app.ui.show_error("Usage: /search <query>")
            return
        self.app.ui.show_activity('searching')
        result = self.app.tool_executor._tool_web_search(args.strip())
        self.app.ui._show_search_result(result)

    def _cmd_model(self, args):
        if not args.strip():
            models = self.app.backend.list_models()
            self.app.ui.console.print(f"\n[{C['accent']}]Available models:[/{C['accent']}]")
            for m in models:
                current = " ← current" if m == self.app.config.model else ""
                self.app.ui.console.print(f"  • {m}{current}")
            self.app.ui.console.print()
        else:
            try:
                self.app.backend.set_model(args.strip())
                self.app.ui.show_success(f"Model switched to {args.strip()}")
            except Exception as e:
                self.app.ui.show_error(f"Failed: {e}")

    def _cmd_think(self, args):
        mode = args.strip().lower()
        if mode not in ('on', 'off', 'auto'):
            self.app.ui.show_info(f"Current: {self.app.config.thinking_mode}")
            self.app.ui.show_info("Usage: /think <on|off|auto>")
            return
        self.app.config.set('thinking_mode', mode)
        self.app.ui.show_success(f"Thinking mode: {mode}")

    def _cmd_trust(self, args):
        level = args.strip().lower()
        if level not in TRUST_LEVELS:
            self.app.ui.show_info(f"Current: {self.app.config.trust_level}")
            self.app.ui.show_info(f"Available: {', '.join(TRUST_LEVELS)}")
            return
        self.app.config.set('trust_level', level)
        self.app.permissions.session_approvals.clear()
        self.app.ui.show_success(f"Trust level: {level}")

    def _cmd_layout(self, args):
        mode = args.strip().lower()
        if mode not in LAYOUT_MODES:
            self.app.ui.show_info(f"Current: {self.app.config.layout_mode}")
            self.app.ui.show_info(f"Available: {', '.join(LAYOUT_MODES)}")
            return
        self.app.config.set('layout_mode', mode)
        self.app.ui._layout_mode = mode
        self.app.ui.show_success(f"Layout: {mode}")
        # In panel mode, show the sidebar immediately
        if mode == 'panel':
            plan = self.app.memory.get_working('current_plan')
            step = self.app.memory.get_working('current_step', 0)
            self.app.ui.show_sidebar(plan=plan, current_step=step)

    def _cmd_compact(self, args):
        self.app.ui.show_activity('compacting')
        self.app.agent.conversation_messages = self.app.context.compact_context(self.app.agent.conversation_messages)
        tokens = self.app.context.estimate_tokens(self.app.agent.conversation_messages)
        self.app.ui.show_success(f"Compacted. Tokens: {tokens:,}")

    def _cmd_checkpoint(self, args):
        desc = args.strip() or "Manual checkpoint"
        self.app.db.save_checkpoint(self.app.agent.conv_id, 0, desc,
            {'messages': len(self.app.agent.conversation_messages),
             'memory_working': self.app.memory.get_state()})
        self.app.ui.show_success(f"Checkpoint: {desc}")

    def _cmd_rollback(self, args):
        checkpoint = self.app.db.get_latest_checkpoint(self.app.agent.conv_id)
        if not checkpoint:
            self.app.ui.show_warning("No checkpoints found.")
            return
        step, desc, state_json, created = checkpoint
        state = json.loads(state_json) if state_json else {}
        msg_count = state.get('message_count', 0)
        self.app.ui.show_info(f"Latest: Step {step} — {desc} ({msg_count} msgs)")
        try:
            choice = input(f"{ANSI['amber']}Rollback? [y/N] {ANSI['reset']}").strip().lower()
            if choice in ('y', 'yes') and msg_count > 0:
                raw_msgs = self.app.db.get_messages(self.app.agent.conv_id, limit=msg_count)
                self.app.agent.conversation_messages = []
                for role, content, thinking, tool_calls, tool_name in raw_msgs:
                    msg = {'role': role, 'content': content}
                    if thinking:
                        msg['thinking'] = thinking
                    if tool_calls:
                        try:
                            msg['tool_calls'] = json.loads(tool_calls)
                        except Exception:
                            pass
                    self.app.agent.conversation_messages.append(msg)
                # FIX #7: Restore memory working state from checkpoint
                memory_state = state.get('memory_working', {})
                self.app.memory.restore_state(memory_state)
                self.app.ui.show_success(f"Rolled back ({len(self.app.agent.conversation_messages)} msgs, state restored)")
            else:
                self.app.ui.show_info("Rollback cancelled.")
        except (EOFError, KeyboardInterrupt):
            self.app.ui.show_info("Rollback cancelled.")

    def _cmd_notes(self, args):
        if args.strip():
            results = self.app.db.search_notes(args.strip(), limit=10)
            if results:
                table = Table(title=f"📝 Notes matching '{args.strip()}'", border_style=C['border'], title_style=C['thinking'])
                table.add_column("Category", style=C['accent'], width=12)
                table.add_column("Note", style=C['text'], width=60)
                for cat, content in results:
                    table.add_row(cat, content[:100])
                self.app.ui.console.print(table)
            else:
                self.app.ui.show_info(f"No notes matching '{args.strip()}'")
            return
        notes = self.app.db.get_notes(limit=15)
        if not notes:
            self.app.ui.show_info("No notes yet. Pinch will write notes as it learns!")
            return
        table = Table(title="📝 Agent Self-Notes", border_style=C['border'], title_style=C['thinking'])
        table.add_column("Category", style=C['accent'], width=12)
        table.add_column("Note", style=C['text'], width=60)
        for cat, content, _ in notes:
            table.add_row(cat, content[:100])
        self.app.ui.console.print(table)

    def _cmd_files(self, args):
        self.app.ui.show_file_tree(args.strip() or None)

    def _cmd_history(self, args):
        """NEW: Show recent tool call history."""
        history = self.app.db.get_tool_history(self.app.agent.conv_id, limit=15)
        self.app.ui.show_tool_history(history)

    def _cmd_sessions(self, args):
        sessions = self.app.db.list_conversations()
        if not sessions:
            self.app.ui.show_info("No previous sessions.")
            return
        for sid, title, created, updated in sessions:
            count = self.app.db.count_messages(sid)
            self.app.ui.console.print(f"  [{C['accent']}]{sid}[/{C['accent']}]  {title or '(untitled)'}  [{C['text_dim']}]{count} msgs[/{C['text_dim']}]")
        self.app.ui.console.print()

    def _cmd_export(self, args):
        fmt = args.strip().lower() or 'md'
        conv_id = self.app.agent.conv_id
        raw_msgs = self.app.db.get_messages(conv_id)
        if not raw_msgs:
            self.app.ui.show_warning("No messages to export.")
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_dir = Path.cwd() / "pincer_exports"
        export_dir.mkdir(exist_ok=True)
        if fmt == 'json':
            filepath = export_dir / f"pincer_{conv_id}_{timestamp}.json"
            export_data = [{'role': r, 'content': c, 'thinking': t, 'tool_calls': tc, 'tool_name': tn}
                          for r, c, t, tc, tn in raw_msgs]
            filepath.write_text(json.dumps(export_data, indent=2, ensure_ascii=False))
        else:
            filepath = export_dir / f"pincer_{conv_id}_{timestamp}.md"
            lines = [f"# Pincer Session Export\n", f"Date: {datetime.now().isoformat()}\n"]
            for role, content, thinking, tool_calls, tool_name in raw_msgs:
                if role == 'user':
                    lines.append(f"\n## 👤 User\n\n{content}\n")
                elif role == 'assistant':
                    if thinking:
                        lines.append(f"\n## 🦞💭 Thinking\n\n{thinking}\n")
                    lines.append(f"\n## 🦞 Assistant\n\n{content}\n")
                elif role == 'tool':
                    lines.append(f"\n## 🔧 Tool: {tool_name}\n\n```\n{content[:500]}\n```\n")
            filepath.write_text('\n'.join(lines))
        self.app.ui.show_success(f"Exported to: {filepath}")

    def _cmd_context(self, args):
        messages = self.app.context.assemble_context(self.app.agent.conversation_messages)
        tokens = self.app.context.estimate_tokens(messages)
        self.app.ui.show_context_preview(messages, tokens)

    def _cmd_mcp(self, args):
        parts = args.strip().split(maxsplit=1)
        if not parts or parts[0] == 'list':
            tools = self.app.mcp_client.list_available()
            if tools:
                self.app.ui.console.print(f"\n[{C['accent']}]MCP Tools:[/{C['accent']}]")
                for t in tools:
                    self.app.ui.console.print(f"  • {t}")
            else:
                self.app.ui.show_info("No MCP servers. Use /mcp add <name> <url>")
            return
        if parts[0] == 'add' and len(parts) > 1:
            add_parts = parts[1].split()
            if len(add_parts) >= 2:
                name, url = add_parts[0], add_parts[1]
                auth = add_parts[2] if len(add_parts) > 2 else None
                self.app.mcp_client.add_server(name, url, auth)
                count = len(self.app.mcp_client.list_available())
                self.app.ui.show_success(f"MCP '{name}' added ({count} tools)")
            else:
                self.app.ui.show_info("Usage: /mcp add <name> <url> [auth_token]")
        else:
            self.app.ui.show_info("MCP: /mcp list, /mcp add <name> <url>")

    def _cmd_config(self, args):
        table = Table(title="⚙️  Configuration", border_style=C['border'], title_style=C['accent'])
        table.add_column("Setting", style=C['accent'], width=20)
        table.add_column("Value", style=C['text'], width=40)
        for key, value in sorted(self.app.config.data.items()):
            table.add_row(key, str(value))
        self.app.ui.console.print(table)

    def _cmd_doctor(self, args):
        self.app.ui.console.print(f"\n[{C['accent']}]🦞 Running diagnostics...[/{C['accent']}]")
        checks = []
        checks.append(("Python", sys.version.split()[0], True))
        ollama_ok = self.app.backend.health_check()
        checks.append(("Ollama", "Running" if ollama_ok else "FAILED", ollama_ok))
        if ollama_ok:
            models = self.app.backend.list_models()
            model_ok = any(self.app.config.model in m for m in models)
            checks.append((f"Model {self.app.config.model}", "Available" if model_ok else "Not found", model_ok))
        checks.append(("Database", "OK" if self.app.db.conn else "FAILED", bool(self.app.db.conn)))
        checks.append(("Config", "OK" if CONFIG_PATH.exists() else "Missing", CONFIG_PATH.exists()))
        checks.append(("PINCER.md", "Found" if PINCER_MD.exists() else "Not found", PINCER_MD.exists()))
        try:
            usage = shutil.disk_usage(str(Path.home()))
            free_gb = usage.free / (1024**3)
            checks.append(("Disk", f"{free_gb:.1f} GB free", free_gb > 5))
        except Exception:
            checks.append(("Disk", "Unknown", True))
        try:
            from playwright.sync_api import sync_playwright
            checks.append(("Playwright", "Installed", True))
        except ImportError:
            checks.append(("Playwright", "Not installed (optional)", True))
        # Show tool schema token count
        schema_tokens = self.app.backend.tool_schema_tokens
        checks.append(("Tool Schema Tokens", f"{schema_tokens} (dynamic)", schema_tokens > 0))

        table = Table(border_style=C['border'], title_style=C['accent'])
        table.add_column("Check", style=C['accent'], width=25)
        table.add_column("Status", style=C['text'], width=25)
        table.add_column("OK", width=5)
        for name, status, ok in checks:
            icon = f"[{C['success']}]✓[/{C['success']}]" if ok else f"[{C['error']}]✗[/{C['error']}]"
            table.add_row(name, status, icon)
        self.app.ui.console.print(table)
        self.app.ui.console.print()

    def _cmd_stats(self, args):
        self.app.ui.show_stats()

    def _cmd_clear(self, args):
        os.system('clear' if os.name != 'nt' else 'cls')
        self.app.ui.show_banner()

    def _cmd_new(self, args):
        title = args.strip() or ""
        self.app.agent.new_conversation(title)
        self.app.ui.show_success(f"New session: {self.app.agent.conv_id}")


# ──────────────────────────────────────────────────────────────
# SECTION 18: ONBOARDING (Fixed Rich/input glitch)
# ──────────────────────────────────────────────────────────────

class Onboarding:
    def __init__(self, config: PincerConfig, ui: UIRenderer, backend: OllamaBackend):
        self.config = config
        self.ui = ui
        self.backend = backend

    def is_first_run(self):
        return self.config.get('first_run', True)

    def _safe_input(self, prompt: str, default: str = "") -> str:
        """Input that flushes Rich buffer first to prevent visual glitch."""
        sys.stdout.flush()
        try:
            result = input(prompt).strip()
            return result if result else default
        except (EOFError, KeyboardInterrupt):
            return default

    def run(self):
        self.ui.console.clear()
        self.ui.console.print()
        self.ui.console.print(Panel(
            f"[{C['primary_br']}]🦞  P I N C E R  v{VERSION}[/{C['primary_br']}]\n\n"
            f"[{C['text']}]Your Autonomous Coding Companion[/{C['text']}]\n"
            f"[{C['text_dim']}]Blue Lobster Edition 🦞[/{C['text_dim']}]",
            border_style=C['primary'], padding=(2, 6),
        ))
        self.ui.console.print()
        self._safe_input(f"  {ANSI['cyan']}Press Enter to begin setup...{ANSI['reset']}")

        # Step 1: Environment
        self.ui.console.print(f"\n[{C['accent']}]Step 1/4: Environment Check[/{C['accent']}]")
        checks = [("Python", sys.version.split()[0], True)]
        ollama_ok = self.backend.health_check()
        checks.append(("Ollama", "Running" if ollama_ok else "Not running!", ollama_ok))
        if ollama_ok:
            models = self.backend.list_models()
            model_ok = any(self.config.model in m for m in models)
            checks.append((f"Model {self.config.model}", "Available" if model_ok else "Not pulled", model_ok))
            if not model_ok:
                self.ui.console.print(f"  [{C['warning']}]Pulling {self.config.model}...[/{C['warning']}]")
                try:
                    self.backend.client.pull(self.config.model)
                    checks[-1] = (f"Model {self.config.model}", "Pulled!", True)
                except Exception:
                    checks[-1] = (f"Model {self.config.model}", "Pull failed", False)
        for name, status, ok in checks:
            self.ui.console.print(f"  [{C['success'] if ok else C['error']}]{'✓' if ok else '✗'}[/{C['success'] if ok else C['error']}] {name}: {status}")

        # Step 2: Personalization
        self.ui.console.print(f"\n[{C['accent']}]Step 2/4: Personalization[/{C['accent']}]")
        name = self._safe_input(f"  Your name? [{self.config.user_name}]: ", self.config.user_name)
        if name:
            self.config.set('user_name', name)
            self.ui.user_name = name
        self.ui.console.print("  Preferred language:")
        self.ui.console.print("  [1] Python  [2] JavaScript  [3] Rust  [4] Go  [5] TypeScript  [6] Other")
        lang_choice = self._safe_input("  Choice [1]: ", "1")
        lang_map = {'1': 'python', '2': 'javascript', '3': 'rust', '4': 'go', '5': 'typescript', '6': 'other'}
        self.config.set('language', lang_map.get(lang_choice, 'python'))
        self.ui.console.print("  Experience level:")
        self.ui.console.print("  [1] Beginner  [2] Intermediate  [3] Advanced")
        exp_choice = self._safe_input("  Choice [2]: ", "2")
        exp_map = {'1': 'Beginner', '2': 'Intermediate', '3': 'Advanced'}
        self.config.set('experience', exp_map.get(exp_choice, 'Intermediate'))

        # Step 3: Trust
        self.ui.console.print(f"\n[{C['accent']}]Step 3/4: Trust Level[/{C['accent']}]")
        self.ui.console.print("  How much should Pinch ask before acting?")
        self.ui.console.print(f"  [1] 🔒 plan       — Approve everything")
        self.ui.console.print(f"  [2] 🔒 default    — Ask before shell commands (recommended)")
        self.ui.console.print(f"  [3] 🔓 acceptEdits — Auto-approve file edits")
        self.ui.console.print(f"  [4] 🔓 auto       — Auto-approve safe commands")
        self.ui.console.print(f"  [5] 🔓 dontAsk    — Skip most prompts")
        trust_choice = self._safe_input("  Choice [2]: ", "2")
        trust_map = {'1': 'plan', '2': 'default', '3': 'acceptEdits', '4': 'auto', '5': 'dontAsk'}
        self.config.set('trust_level', trust_map.get(trust_choice, 'default'))

        # Step 4: Complete
        self.ui.console.print(f"\n[{C['accent']}]Step 4/4: All Set! 🎉[/{C['accent']}]")
        self.ui.console.print(f"  [{C['success']}]✓[/{C['success']}] Environment ready")
        self.ui.console.print(f"  [{C['success']}]✓[/{C['success']}] Personalization saved")
        self.ui.console.print(f"  [{C['success']}]✓[/{C['success']}] Trust: {self.config.trust_level}")
        self.ui.console.print(f"  [{C['success']}]✓[/{C['success']}] Theme: Deep Ocean (blue)")
        self.ui.console.print(f"\n  [{C['text_dim']}]Quick tips:[/{C['text_dim']}]")
        self.ui.console.print(f"  [{C['text_dim']}]• /help for all commands[/{C['text_dim']}]")
        self.ui.console.print(f"  [{C['text_dim']}]• /task for autonomous mode[/{C['text_dim']}]")
        self.ui.console.print(f"  [{C['text_dim']}]• /history to see tool calls[/{C['text_dim']}]")
        self.ui.console.print(f"  [{C['text_dim']}]• /layout panel for sidebar view[/{C['text_dim']}]")
        self.config.set('first_run', False)
        self.config.save()
        self._safe_input(f"\n  {ANSI['cyan']}Press Enter to start Pincer...{ANSI['reset']}")


# ──────────────────────────────────────────────────────────────
# SECTION 19: MAIN APPLICATION
# ──────────────────────────────────────────────────────────────

class PincerApp:
    def __init__(self):
        self.config = PincerConfig()
        self.db = PincerDB()
        self.backend = OllamaBackend(self.config)
        self.ui = UIRenderer(self.config)
        self.web_cache = WebCache(ttl=300, max_size=50)
        self.memory = MemorySystem(self.db, self.config)
        self.tool_executor = ToolExecutor(self.config, self.db, self.web_cache)
        self.permissions = PermissionSystem(self.config, self.ui.console)
        self.context = ContextManager(self.config, self.memory, self.backend)
        self.mcp_client = MCPClient()
        self.agent = AgentLoop(
            self.config, self.db, self.backend, self.tool_executor,
            self.permissions, self.context, self.memory, self.ui
        )
        self.autonomous = AutonomousMode(self.agent, self.ui, self.db, self.config, self.memory)
        self.commands = CommandHandler(self)
        self.onboarding = Onboarding(self.config, self.ui, self.backend)

        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        slash_commands = [
            '/help', '/exit', '/quit', '/task', '/plan', '/search', '/model',
            '/think', '/trust', '/compact', '/checkpoint', '/rollback',
            '/notes', '/files', '/history', '/sessions', '/export', '/context',
            '/mcp', '/config', '/doctor', '/stats', '/clear', '/new', '/layout',
        ]
        self.prompt_session = PromptSession(
            history=FileHistory(str(HISTORY_PATH)),
            completer=FuzzyWordCompleter(slash_commands, ignore_case=True),
            multiline=False,
        )
        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(self, sig, frame):
        self.agent.interrupt()
        self.autonomous.cancel()

    def _build_prompt(self) -> FormattedText:
        safe_name = self.config.user_name.replace('<', '&lt;').replace('>', '&gt;').replace('&', '&amp;')
        return FormattedText([
            (f'fg:{C["primary_br"]}', f'{MASCOT} '),
            (f'fg:{C["text_dim"]}', f'{safe_name}'),
            (f'fg:{C["accent"]}', ' > '),
        ])

    def _render_post_turn(self):
        """After each turn, render layout-appropriate UI."""
        mode = self.config.layout_mode
        if mode == 'panel':
            plan = self.memory.get_working('current_plan')
            step = self.memory.get_working('current_step', 0) or 0
            self.ui.show_sidebar(plan=plan, current_step=step)
        elif mode == 'compact':
            pass  # Minimal, no extra UI
        # stream mode: status bar already shown

    def run(self):
        if self.onboarding.is_first_run():
            self.onboarding.run()

        os.system('clear' if os.name != 'nt' else 'cls')
        self.ui.show_banner()
        self.ui.show_welcome()
        self.agent.new_conversation()
        ctx_tokens = self.agent.get_context_tokens()
        self.ui.show_status_bar('idle', ctx_tokens)
        self.ui.console.print(
            f"[{C['text_dim']}]Model: {self.config.model} | Trust: {self.config.trust_level} | "
            f"Think: {self.config.thinking_mode} | Layout: {self.config.layout_mode}[/{C['text_dim']}]"
        )
        self.ui.console.print()

        while True:
            try:
                prompt_formatted = self._build_prompt()
                user_input = self.prompt_session.prompt(prompt_formatted)
                if not user_input.strip():
                    continue
                if self.commands.handle(user_input):
                    continue
                self.ui.show_status_bar('thinking', self.agent.get_context_tokens())
                result = self.agent.run(user_input.strip())
                ctx_tokens = self.agent.get_context_tokens()
                self.ui.show_status_bar('idle' if result else 'error', ctx_tokens)
                self._render_post_turn()
            except KeyboardInterrupt:
                self.ui.console.print(f"\n[{C['text_dim']}]Use /exit to quit.[/{C['text_dim']}]")
                continue
            except EOFError:
                self.ui.console.print(f"\n[{C['primary_br']}]🦞 See you later! 👋[/{C['primary_br']}]")
                break
            except Exception as e:
                self.ui.show_error(f"Unexpected: {e}")
                if self.config.get('experience') == 'Advanced':
                    import traceback
                    self.ui.console.print(f"[{C['text_muted']}]{traceback.format_exc()}[/{C['text_muted']}]")
                continue

        self.db.close()


# ──────────────────────────────────────────────────────────────
# SECTION 20: ENTRY POINT
# ──────────────────────────────────────────────────────────────

def main():
    PINCER_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        client = ollama.Client(host='http://localhost:11434')
        client.list()
    except Exception:
        print(f"\n{ANSI['red']}{MASCOT} Cannot connect to Ollama!{ANSI['reset']}")
        print(f"{ANSI['amber']}  Make sure Ollama is running: ollama serve{ANSI['reset']}")
        print(f"{ANSI['dim_blue']}  Install: https://ollama.com{ANSI['reset']}\n")
        sys.exit(1)
    app = PincerApp()
    app.run()


if __name__ == "__main__":
    main()
