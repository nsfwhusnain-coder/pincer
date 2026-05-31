#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║              🦞  P I N C E R  v2.1.0                        ║
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
import hashlib, textwrap, subprocess, difflib, asyncio
from pathlib import Path
from datetime import datetime
from collections import deque
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
from prompt_toolkit.completion import WordCompleter, FuzzyWordCompleter
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
from rich.live import Live
from rich.box import ROUNDED
from rich.theme import Theme
from pygments.lexers import guess_lexer_for_filename, TextLexer


# ──────────────────────────────────────────────────────────────
# SECTION 2: CONSTANTS & DEEP OCEAN COLOR SYSTEM
# ──────────────────────────────────────────────────────────────

VERSION = "2.1.0"
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
TOOL_SCHEMA_TOKENS = 500

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
    'diff_hunk':     '#1E3A6E',
    'lobster':       '#2563EB',
}

DEEP_OCEAN = Theme({
    'primary':        C['primary'],
    'primary.br':     C['primary_br'],
    'accent':         C['accent'],
    'thinking':       C['thinking'],
    'planning':       C['planning'],
    'working':        C['working'],
    'searching':      C['searching'],
    'reading':        C['reading'],
    'writing':        C['writing'],
    'error':          C['error'],
    'success':        C['success'],
    'warning':        C['warning'],
    'info':           C['info'],
    'text':           C['text'],
    'text.dim':       C['text_dim'],
    'text.bright':    C['text_bright'],
    'text.muted':     C['text_muted'],
    'lobster':        C['lobster'],
    'border':         C['border'],
    'border.active':  C['border_active'],
})

ANSI = {
    'reset':      '\033[0m',
    'bold':       '\033[1m',
    'dim':        '\033[2m',
    'italic':     '\033[3m',
    'underline':  '\033[4m',
    'purple':     '\033[38;2;167;139;250m',
    'blue':       '\033[38;2;59;130;246m',
    'bright_blue':'\033[38;2;96;165;250m',
    'cyan':       '\033[38;2;6;182;212m',
    'green':      '\033[38;2;34;197;94m',
    'red':        '\033[38;2;239;68;68m',
    'amber':      '\033[38;2;245;158;11m',
    'dim_blue':   '\033[38;2;100;116;139m',
    'light_blue': '\033[38;2;147;197;253m',
    'white':      '\033[38;2;219;234;254m',
}

TRUST_LEVELS = ['plan', 'default', 'acceptEdits', 'auto', 'dontAsk']
LAYOUT_MODES = ['stream', 'panel', 'compact']

DENY_PATTERNS = [
    r'^rm\s+-[a-zA-Z]*f\s+/',
    r'^mkfs',
    r'^dd\s+if=',
    r'curl\s+.*\|\s*(ba)?sh',
    r':\(\)\{.*;\}\s*;',
    r'^sudo\s+rm',
    r'>\s*/etc/',
    r'^chmod\s+-R\s+777\s+/',
    r'^git\s+push\s+--force',
    r'^dropdb',
    r'^pip\s+uninstall\s+-y\s+(pip|setuptools)',
]

SAFE_PATTERNS = [
    r'^git\s+(status|log|diff|show|branch)',
    r'^ls\s+.*',
    r'^cat\s+.*',
    r'^pwd$',
    r'^python\s+.*\.py$',
    r'^pytest\s+.*',
    r'^pip\s+(list|show|freeze|install)',
    r'^echo\s+.*',
    r'^which\s+.*',
    r'^head\s+.*',
    r'^tail\s+.*',
    r'^wc\s+.*',
    r'^find\s+.*',
    r'^grep\s+.*',
    r'^du\s+.*',
    r'^mkdir\s+.*',
    r'^touch\s+.*',
    r'^cp\s+.*',
    r'^mv\s+.*',
]


# ──────────────────────────────────────────────────────────────
# SECTION 3: CONFIGURATION
# ──────────────────────────────────────────────────────────────

class PincerConfig:
    DEFAULTS = {
        'user_name': DEFAULT_USER,
        'model': DEFAULT_MODEL,
        'language': 'python',
        'experience': 'Intermediate',
        'trust_level': 'default',
        'thinking_mode': 'auto',
        'theme': 'deep_ocean',
        'layout_mode': 'stream',
        'auto_approve_reads': True,
        'max_shell_timeout': 120,
        'max_file_size': 1_000_000,
        'max_output_lines': 200,
        'web_search_enabled': True,
        'scrape_enabled': False,
        'sound_notifications': True,
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
    def user_name(self): return self.data.get('user_name', DEFAULT_USER)
    @property
    def model(self): return self.data.get('model', DEFAULT_MODEL)
    @property
    def trust_level(self): return self.data.get('trust_level', 'default')
    @property
    def thinking_mode(self): return self.data.get('thinking_mode', 'auto')
    @property
    def layout_mode(self): return self.data.get('layout_mode', 'stream')


# ──────────────────────────────────────────────────────────────
# SECTION 4: DATABASE / STATE LAYER
# ──────────────────────────────────────────────────────────────

class PincerDB:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        title TEXT DEFAULT '',
        created_at REAL,
        updated_at REAL
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT,
        role TEXT NOT NULL,
        content TEXT DEFAULT '',
        thinking TEXT DEFAULT '',
        tool_calls TEXT DEFAULT '',
        tool_name TEXT DEFAULT '',
        token_count INTEGER DEFAULT 0,
        created_at REAL,
        FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    );
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        content TEXT NOT NULL,
        tokens TEXT DEFAULT '',
        created_at REAL
    );
    CREATE TABLE IF NOT EXISTS checkpoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT,
        step_number INTEGER DEFAULT 0,
        description TEXT DEFAULT '',
        state_json TEXT DEFAULT '{}',
        created_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_msgs_conv ON messages(conversation_id);
    CREATE INDEX IF NOT EXISTS idx_notes_cat ON notes(category);
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
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (conv_id, title, now, now)
        )
        self.conn.commit()
        return conv_id

    def list_conversations(self):
        cur = self.conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC LIMIT 20"
        )
        return cur.fetchall()

    def update_conversation(self, conv_id, title=None):
        now = time.time()
        if title:
            self.conn.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?", (title, now, conv_id))
        else:
            self.conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conv_id))
        self.conn.commit()

    def delete_conversation(self, conv_id):
        self.conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
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

    def get_messages_after_id(self, conv_id, after_id=0, limit=200):
        cur = self.conn.execute(
            "SELECT role,content,thinking,tool_calls,tool_name,id FROM messages WHERE conversation_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (conv_id, after_id, limit)
        )
        return cur.fetchall()

    def count_messages(self, conv_id):
        cur = self.conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conv_id,))
        return cur.fetchone()[0]

    def get_last_message_id(self, conv_id):
        cur = self.conn.execute("SELECT MAX(id) FROM messages WHERE conversation_id=?", (conv_id,))
        return cur.fetchone()[0] or 0

    def add_note(self, category, content):
        now = time.time()
        tokens = ' '.join(re.findall(r'\b\w+\b', content.lower()))
        self.conn.execute(
            "INSERT INTO notes (category, content, tokens, created_at) VALUES (?,?,?,?)",
            (category, content, tokens, now)
        )
        self.conn.commit()

    def get_notes(self, category=None, limit=20):
        if category:
            cur = self.conn.execute(
                "SELECT category, content, created_at FROM notes WHERE category=? ORDER BY created_at DESC LIMIT ?",
                (category, limit)
            )
        else:
            cur = self.conn.execute(
                "SELECT category, content, created_at FROM notes ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        return cur.fetchall()

    def search_notes(self, query, limit=5):
        query_tokens = set(re.findall(r'\b\w+\b', query.lower()))
        if not query_tokens:
            return []
        all_notes = self.conn.execute(
            "SELECT id, category, content, tokens FROM notes ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        scored = []
        for nid, cat, content, tokens_str in all_notes:
            note_tokens = set(tokens_str.split())
            overlap = len(query_tokens & note_tokens)
            if overlap > 0:
                scored.append((overlap, cat, content))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(cat, content) for _, cat, content in scored[:limit]]

    def save_checkpoint(self, conv_id, step_number, description, state):
        now = time.time()
        self.conn.execute(
            "INSERT INTO checkpoints (conversation_id,step_number,description,state_json,created_at) VALUES (?,?,?,?,?)",
            (conv_id, step_number, description, json.dumps(state), now)
        )
        self.conn.commit()

    def get_latest_checkpoint(self, conv_id):
        cur = self.conn.execute(
            "SELECT step_number, description, state_json, created_at FROM checkpoints WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
            (conv_id,)
        )
        row = cur.fetchone()
        return row

    def get_checkpoints(self, conv_id):
        cur = self.conn.execute(
            "SELECT step_number, description, state_json, created_at FROM checkpoints WHERE conversation_id=? ORDER BY step_number DESC",
            (conv_id,)
        )
        return cur.fetchall()

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
            content = f"""# PINCER.md — Project & User Notes
# Auto-managed by Pincer v{VERSION}

## User Preferences
- Name: {self.config.user_name}
- Language: {self.config.get('language', 'python')}

## Project Conventions
(learned automatically)

## Learned Facts
(learned automatically)
"""
            PINCER_MD.write_text(content)

    def read_pincer_md(self):
        return PINCER_MD.read_text() if PINCER_MD.exists() else ""

    def write_pincer_md(self, content: str):
        PINCER_MD.write_text(content)

    def add_note(self, category: str, content: str):
        self.db.add_note(category, content)
        md = self.read_pincer_md()
        section_map = {
            'preference': '## User Preferences',
            'convention': '## Project Conventions',
            'fact': '## Learned Facts',
            'error': '## Learned Facts',
            'success': '## Learned Facts',
            'pattern': '## Project Conventions',
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


# ──────────────────────────────────────────────────────────────
# SECTION 6: OLLAMA BACKEND
# ──────────────────────────────────────────────────────────────

class OllamaBackend:
    def __init__(self, config: PincerConfig):
        self.config = config
        self.model = config.model
        self.client = ollama.Client(host='http://localhost:11434')
        self._ensure_model()

    def _ensure_model(self):
        try:
            models = self.client.list()
            model_names = []
            for m in models.get('models', []):
                name = m.get('name', '') or getattr(m, 'model', '')
                model_names.append(name)
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

    def chat(self, messages, tools=None, stream=False, think=False):
        kwargs = {
            'model': self.model,
            'messages': messages,
            'stream': stream,
        }
        if tools:
            kwargs['tools'] = tools
        # Wire thinking mode to Ollama API — BUG FIX #6
        if think and self.config.thinking_mode != 'off':
            try:
                kwargs['think'] = True
            except Exception:
                pass
        try:
            return self.client.chat(**kwargs)
        except TypeError:
            # Older SDK might not support 'think' parameter
            kwargs.pop('think', None)
            return self.client.chat(**kwargs)
        except ollama.ResponseError as e:
            raise RuntimeError(f"Ollama error: {e.error}") from e
        except Exception as e:
            raise RuntimeError(f"Connection error: {e}") from e

    def stream_chat(self, messages, tools=None, think=False):
        kwargs = {
            'model': self.model,
            'messages': messages,
            'stream': True,
        }
        if tools:
            kwargs['tools'] = tools
        # Wire thinking mode — BUG FIX #6
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
        except Exception as e:
            raise RuntimeError(f"Stream error: {e}") from e

    def count_tokens_approx(self, text: str) -> int:
        return max(1, len(text) // 4)

    def set_model(self, model: str):
        self.model = model
        self.config.set('model', model)
        self._ensure_model()

    def should_think(self) -> bool:
        """Determine if thinking mode should be active for this call."""
        mode = self.config.thinking_mode
        if mode == 'on':
            return True
        if mode == 'off':
            return False
        # 'auto' — let the model decide (pass think=True so API enables it)
        return True


# ──────────────────────────────────────────────────────────────
# SECTION 7: TOOL DEFINITIONS
# ──────────────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        'type': 'function',
        'function': {
            'name': 'read_file',
            'description': 'Read the contents of a file. Returns content with line numbers. Supports offset and limit for large files.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Path to the file to read'},
                    'offset': {'type': 'integer', 'description': 'Starting line number (1-based, default 1)'},
                    'limit': {'type': 'integer', 'description': 'Maximum number of lines to read (default 200)'},
                },
                'required': ['path'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'write_file',
            'description': 'Create or overwrite a file with the given content. Creates parent directories if needed.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Path to the file to write'},
                    'content': {'type': 'string', 'description': 'Content to write to the file'},
                },
                'required': ['path', 'content'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'edit_file',
            'description': 'Edit a file by replacing an exact string match. The old_string must be unique in the file. Returns a diff of changes.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Path to the file to edit'},
                    'old_string': {'type': 'string', 'description': 'Exact string to find and replace (must be unique)'},
                    'new_string': {'type': 'string', 'description': 'String to replace the old_string with'},
                },
                'required': ['path', 'old_string', 'new_string'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'search_files',
            'description': 'Search for files by name pattern or content. Supports glob patterns for filenames and regex for content.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'pattern': {'type': 'string', 'description': 'File name pattern (glob) or content search pattern'},
                    'search_type': {'type': 'string', 'enum': ['filename', 'content'], 'description': 'Search by filename or content (default: filename)'},
                    'path': {'type': 'string', 'description': 'Directory to search in (default: current directory)'},
                },
                'required': ['pattern'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'list_directory',
            'description': 'List the contents of a directory. Shows files and subdirectories with types.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Directory path to list (default: current directory)'},
                    'recursive': {'type': 'boolean', 'description': 'List recursively (default: false)'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'execute_command',
            'description': 'Execute a shell command. Runs in the current working directory with a timeout. Use for running tests, installing packages, git operations, etc.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'command': {'type': 'string', 'description': 'Shell command to execute'},
                    'timeout': {'type': 'integer', 'description': 'Timeout in seconds (default: 120)'},
                },
                'required': ['command'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'web_search',
            'description': 'Search the web using DuckDuckGo. Returns a list of search results with titles, URLs, and descriptions.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'Search query'},
                    'num_results': {'type': 'integer', 'description': 'Number of results to return (default: 5)'},
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'fetch_url',
            'description': 'Fetch a web page and convert it to readable text. Good for documentation, articles, and static pages.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'url': {'type': 'string', 'description': 'URL to fetch'},
                    'max_length': {'type': 'integer', 'description': 'Maximum text length to return (default: 5000)'},
                },
                'required': ['url'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'scrape_page',
            'description': 'Scrape a JavaScript-heavy web page using a headless browser. Use this when fetch_url returns incomplete content. Requires Playwright.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'url': {'type': 'string', 'description': 'URL to scrape'},
                    'wait_for': {'type': 'string', 'description': 'CSS selector to wait for before extracting content'},
                    'max_length': {'type': 'integer', 'description': 'Maximum text length to return (default: 5000)'},
                },
                'required': ['url'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'ask_user',
            'description': 'Ask the user a clarifying question and wait for their response. Use when you need more information to proceed.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'question': {'type': 'string', 'description': 'Question to ask the user'},
                    'options': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Optional list of suggested answers'},
                },
                'required': ['question'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'write_note',
            'description': 'Write a self-note to remember important information for later. Categories: observation, error, success, preference, pattern, fact.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'category': {'type': 'string', 'description': 'Category of the note', 'enum': ['observation', 'error', 'success', 'preference', 'pattern', 'fact']},
                    'content': {'type': 'string', 'description': 'The note content to remember'},
                },
                'required': ['category', 'content'],
            },
        },
    },
]

TOOL_NAMES = [t['function']['name'] for t in TOOL_SCHEMAS]


# ──────────────────────────────────────────────────────────────
# SECTION 8: TOOL IMPLEMENTATIONS
# ──────────────────────────────────────────────────────────────

class ToolExecutor:
    def __init__(self, config: PincerConfig, db: PincerDB):
        self.config = config
        self.db = db
        self.cwd = str(Path.cwd())
        self._last_file_read = ""  # For diff tracking

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

    # ── File Tools ──

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
            content = header + "\n" + "\n".join(result_lines)
            self._last_file_read = content
            return content
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
                'action': action,
                'path': path,
                'lines': line_count,
                'diff_available': existed,
                'old_content_preview': old_content[:200] if existed else "",
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
            # Try fuzzy matching for whitespace differences
            normalized_old = old_string.strip()
            normalized_content = content.strip()
            if normalized_old in normalized_content:
                return f"Error: String not found exactly. The text exists but with different whitespace. Copy the exact text from the file."
            return f"Error: String not found in {path}. The exact text must match."
        if count > 1:
            return f"Error: String found {count} times in {path}. Provide more context to make it unique."

        old_content = content
        new_content = content.replace(old_string, new_string, 1)
        try:
            filepath.write_text(new_content)
        except Exception as e:
            return f"Error writing file: {e}"

        # Generate actual unified diff
        diff_lines = list(difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
            lineterm='',
        ))

        old_lines = old_string.splitlines()
        new_lines = new_string.splitlines()
        summary = f"Replaced {len(old_lines)} line(s) with {len(new_lines)} line(s) in {path}"
        self.db.add_note('observation', f"Edited {path}: {summary}")

        return json.dumps({
            'summary': summary,
            'path': path,
            'old_lines': len(old_lines),
            'new_lines': len(new_lines),
            'diff': '\n'.join(diff_lines),
        })

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
                        rel = p.relative_to(search_dir)
                        results.append(str(rel))
                    except ValueError:
                        results.append(str(p))
            else:
                try:
                    regex = re.compile(pattern, re.IGNORECASE)
                except re.error:
                    return f"Error: Invalid regex pattern: {pattern}"
                skip_ext = {'.pyc', '.pyo', '.so', '.dylib', '.png', '.jpg', '.jpeg', '.gif',
                           '.zip', '.tar', '.gz', '.woff', '.ttf', '.eot', '.ico', '.svg', '.min.js', '.min.css'}
                for p in search_dir.rglob('*'):
                    if not p.is_file():
                        continue
                    if any(part.startswith('.') for part in p.parts):
                        continue
                    if p.suffix in skip_ext:
                        continue
                    if p.stat().st_size > 100_000:
                        continue
                    if len(results) >= 20:
                        results.append("... (more results)")
                        break
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
        if not results:
            return f"No results found for '{pattern}'"
        return "\n".join(results)

    def _tool_list_directory(self, path: str = None, recursive: bool = False) -> str:
        dirpath = Path(path).expanduser().resolve() if path else Path.cwd()
        if not dirpath.exists():
            return f"Error: Directory not found: {path}"
        if not dirpath.is_dir():
            return f"Error: Not a directory: {path}"
        results = []
        try:
            items = sorted(dirpath.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            for p in items:
                prefix = "📁" if p.is_dir() else "📄"
                size = ""
                if p.is_file():
                    try:
                        st = p.stat().st_size
                        size = f" ({st:,}B)" if st < 1024 else f" ({st//1024:,}KB)"
                    except Exception:
                        pass
                results.append(f"{prefix} {p.name}{size}")
                if recursive and p.is_dir() and not p.name.startswith('.'):
                    for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                        cprefix = "  📁" if child.is_dir() else "  📄"
                        results.append(f"{cprefix} {child.name}")
        except PermissionError:
            return "Error: Permission denied"
        except Exception as e:
            return f"Error listing directory: {e}"
        return "\n".join(results)

    # ── Shell Tool ──

    def _tool_execute_command(self, command: str, timeout: int = None) -> str:
        timeout = timeout or self.config.get('max_shell_timeout', 120)
        for pat in DENY_PATTERNS:
            if re.search(pat, command, re.IGNORECASE):
                return f"BLOCKED: Command matches dangerous pattern. If you're sure, run it directly in your terminal."
        try:
            result = subprocess.run(
                command, shell=True, cwd=self.cwd,
                capture_output=True, text=True, timeout=timeout,
                env={**os.environ, 'TERM': 'dumb'},
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

    # ── Web Tools ──

    def _tool_web_search(self, query: str, num_results: int = 5) -> str:
        """Search the web using DuckDuckGo — BUG FIX #1: use urllib.parse.quote"""
        if not self.config.get('web_search_enabled', True):
            return "Error: Web search is disabled in config."
        try:
            url = f"https://lite.duckduckgo.com/lite/?q={url_quote(query)}"
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                soup = BeautifulSoup(resp.text, 'html.parser')

            results = []
            # Parse DuckDuckGo Lite results
            for row in soup.find_all('tr'):
                link = row.find('a', class_='result-link')
                if link and len(results) < num_results:
                    title = link.get_text(strip=True)
                    href = link.get('href', '')
                    snippet_tag = row.find('td', class_='result-snippet')
                    snippet = snippet_tag.get_text(strip=True)[:200] if snippet_tag else ""
                    if title:
                        results.append(f"{len(results)+1}. {title}\n   {href}\n   {snippet}" if snippet else f"{len(results)+1}. {title}\n   {href}")

            if not results:
                # Fallback parser
                for a in soup.find_all('a'):
                    href = a.get('href', '')
                    if href.startswith('http') and 'duckduckgo' not in href and 'duck' not in href:
                        title = a.get_text(strip=True)
                        if title and len(title) > 5 and len(results) < num_results:
                            results.append(f"{len(results)+1}. {title}\n   {href}")

            if not results:
                return f"No results found for '{query}'"
            return "\n\n".join(results)
        except httpx.TimeoutException:
            return "Error: Search request timed out"
        except Exception as e:
            return f"Error searching: {e}"

    def _tool_fetch_url(self, url: str, max_length: int = 5000) -> str:
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
            return f"Title: {title}\nURL: {url}\n\n{text}"
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} for {url}"
        except httpx.TimeoutException:
            return "Error: Request timed out"
        except Exception as e:
            return f"Error fetching URL: {e}"

    def _tool_scrape_page(self, url: str, wait_for: str = '', max_length: int = 5000) -> str:
        """Scrape JS-heavy pages with Playwright headless browser."""
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

    # ── System Tools ──

    def _tool_ask_user(self, question: str, options: list = None) -> str:
        return f"ASK_USER:{json.dumps({'question': question, 'options': options or []})}"

    def _tool_write_note(self, category: str, content: str) -> str:
        self.db.add_note(category, content)
        return f"Note saved: [{category}] {content}"


# ──────────────────────────────────────────────────────────────
# SECTION 9: MCP CLIENT (Basic HTTP)
# ──────────────────────────────────────────────────────────────

class MCPClient:
    """Basic MCP client for connecting to MCP servers over HTTP/SSE."""

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
                tools = data.get('tools', [])
                for tool in tools:
                    tool_name = tool.get('name', '')
                    if tool_name:
                        self.available_tools[f"{server_name}:{tool_name}"] = {
                            'server': server_name,
                            'name': tool_name,
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
                if contents:
                    return contents[0].get('text', json.dumps(contents))
                return json.dumps(data)
        except Exception as e:
            return f"MCP error: {e}"

    def list_available(self) -> list:
        return list(self.available_tools.keys())

    def get_tool_schemas(self) -> list:
        """Convert MCP tools to Ollama tool schema format."""
        schemas = []
        for full_name, info in self.available_tools.items():
            schema = {
                'type': 'function',
                'function': {
                    'name': f"mcp_{full_name.replace(':', '_').replace('-', '_')}",
                    'description': info['description'] or f"MCP tool: {full_name}",
                    'parameters': info.get('schema', {'type': 'object', 'properties': {}}),
                }
            }
            schemas.append(schema)
        return schemas


# ──────────────────────────────────────────────────────────────
# SECTION 10: PERMISSION SYSTEM
# ──────────────────────────────────────────────────────────────

class PermissionSystem:
    def __init__(self, config: PincerConfig, console: Console):
        self.config = config
        self.console = console
        self.session_approvals = set()

    @property
    def trust_level(self):
        return self.config.trust_level

    def check(self, tool_name: str, args: dict) -> bool:
        trust = self.trust_level

        # Always allowed tools
        if tool_name in ('read_file', 'list_directory', 'search_files'):
            if trust != 'plan':
                return True
        if tool_name in ('write_note', 'ask_user'):
            return True
        if tool_name in ('web_search', 'fetch_url', 'scrape_page'):
            if trust in ('auto', 'dontAsk', 'default', 'acceptEdits'):
                return True

        # File write/edit
        if tool_name in ('write_file', 'edit_file'):
            if trust in ('auto', 'dontAsk', 'acceptEdits'):
                return True
            if 'file_edits' in self.session_approvals:
                return True
            if trust == 'plan':
                return self._ask_permission(tool_name, args)

        # Shell commands — most restricted
        if tool_name == 'execute_command':
            command = args.get('command', '')
            for pat in DENY_PATTERNS:
                if re.search(pat, command, re.IGNORECASE):
                    self.console.print(
                        f"\n[{C['error']}]🚨 Blocked dangerous command:[/{C['error']}] "
                        f"[{C['text_dim']}]{command}[/{C['text_dim']}]"
                    )
                    return False
            for pat in SAFE_PATTERNS:
                if re.search(pat, command, re.IGNORECASE):
                    if trust in ('auto', 'dontAsk', 'default', 'acceptEdits'):
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
            panel = Panel(
                f"[{C['text_bright']}]$ {cmd}[/{C['text_bright']}]",
                title=f"[{C['warning']}]⚠️  Allow this command?[/{C['warning']}]",
                border_style=C['warning'], padding=(1, 2),
            )
            self.console.print(panel)
            self.console.print(
                f"  [{C['text_dim']}]\\[Y]\\[/{C['text_dim']}] Allow once  "
                f"[{C['text_dim']}]\\[A]\\[/{C['text_dim']}] Allow all '{cmd.split()[0] if cmd.split() else cmd}'  "
                f"[{C['text_dim']}]\\[N]\\[/{C['text_dim']}] Deny  "
                f"[{C['text_dim']}]\\[E]\\[/{C['text_dim']}] Edit",
                highlight=False,
            )
        elif tool_name in ('write_file', 'edit_file'):
            path = args.get('path', '?')
            action = "edit" if tool_name == 'edit_file' else "write"
            panel = Panel(
                f"[{C['text_bright']}]{action}: {path}[/{C['text_bright']}]",
                title=f"[{C['warning']}]⚠️  Allow file {action}?[/{C['warning']}]",
                border_style=C['warning'], padding=(1, 2),
            )
            self.console.print(panel)
            self.console.print(
                f"  [{C['text_dim']}]\\[Y]\\[/{C['text_dim']}] Allow once  "
                f"[{C['text_dim']}]\\[A]\\[/{C['text_dim']}] Allow all file edits  "
                f"[{C['text_dim']}]\\[N]\\[/{C['text_dim']}] Deny",
                highlight=False,
            )
        else:
            panel = Panel(
                f"[{C['text_bright']}]{tool_name}({json.dumps(args, default=str)[:100]})[/{C['text_bright']}]",
                title=f"[{C['warning']}]⚠️  Allow this action?[/{C['warning']}]",
                border_style=C['warning'], padding=(1, 2),
            )
            self.console.print(panel)
            self.console.print(
                f"  [{C['text_dim']}]\\[Y]\\[/{C['text_dim']}] Allow  "
                f"[{C['text_dim']}]\\[N]\\[/{C['text_dim']}] Deny",
                highlight=False,
            )

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
            return False

        self.console.print(f"[{C['text_dim']}]Denied.[/{C['text_dim']}]")
        return False


# ──────────────────────────────────────────────────────────────
# SECTION 11: CONTEXT MANAGER (Improved Compaction)
# ──────────────────────────────────────────────────────────────

class ContextManager:
    """Assembles and compacts context — BUG FIX #3: preserve tool pairs."""

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
            cwd=str(Path.cwd()),
            user_name=self.config.user_name,
            language=self.config.get('language', 'python'),
            memory=self.memory.get_procedural_memory()[:1000],
            notes=self.memory.get_notes_for_context(5),
        )

    def assemble_context(self, conversation_messages: list, user_input: str = None) -> list:
        messages = []
        system_prompt = self.build_system_prompt()
        messages.append({'role': 'system', 'content': system_prompt})

        system_tokens = self.backend.count_tokens_approx(system_prompt)
        tool_tokens = TOOL_SCHEMA_TOKENS
        budget = MAX_CONTEXT_TOKENS - system_tokens - tool_tokens - MEMORY_TOKENS - 500

        # Add messages from newest to oldest until budget is exhausted
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
        """BUG FIX #3: Never split a tool_calls + tool result pair."""
        if not messages:
            return messages

        # Layer 1: Trim long tool outputs (but keep them paired)
        compacted = []
        for msg in messages:
            content = msg.get('content', '')
            if len(content) > 2000:
                content = content[:500] + f"\n... (truncated from {len(content)} chars) ...\n" + content[-500:]
                msg = {**msg, 'content': content}
            compacted.append(msg)

        # Layer 2: Preserve complete turns (tool_call + tool_result pairs)
        if len(compacted) > 15:
            # Group messages into "turns" that must stay together
            turns = self._group_into_turns(compacted)

            # Always keep the first 2 and last 6 turns
            if len(turns) > 8:
                essential_start = turns[:2]
                essential_end = turns[-6:]
                middle = turns[2:-6]

                # Summarize middle turns
                if middle:
                    middle_text = self._concat_turn_summaries(middle)
                    summary_msg = {
                        'role': 'system',
                        'content': f'[Earlier conversation summary: {middle_text}]'
                    }
                    compacted = []
                    for turn in essential_start:
                        compacted.extend(turn)
                    compacted.append(summary_msg)
                    for turn in essential_end:
                        compacted.extend(turn)

        return compacted

    def _group_into_turns(self, messages: list) -> list:
        """Group messages into turns, keeping tool_calls + tool_results together."""
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
                current_turn = [msg]  # Start new turn with tool-calling assistant
            elif role == 'tool':
                current_turn.append(msg)  # Keep tool result with its assistant
            elif role == 'assistant' and current_turn:
                # Check if previous was a tool chain
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
        """Concatenate brief summaries of each turn."""
        parts = []
        for turn in turns:
            for msg in turn:
                role = msg.get('role', '')
                content = msg.get('content', '')[:80]
                if content:
                    parts.append(f"{role}: {content}...")
        return " | ".join(parts[:8])[:600]

    def estimate_tokens(self, messages: list) -> int:
        total = 0
        for msg in messages:
            total += self.backend.count_tokens_approx(msg.get('content', ''))
            total += self.backend.count_tokens_approx(msg.get('thinking', ''))
        return total


# ──────────────────────────────────────────────────────────────
# SECTION 12: UI RENDERER (Full Diff Viewer + Layouts)
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

    # ── Banners ──

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

    # ── Activity Indicators ──

    def show_thinking_start(self):
        self.console.print(f"[{C['thinking']}]🦞💭 Thinking...[/{C['thinking']}]")

    def show_thinking_block(self, thinking: str):
        if not thinking.strip():
            return
        display = thinking
        if len(display) > 1200:
            display = display[:600] + f"\n... ({len(thinking)} chars) ...\n" + display[-400:]
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

    # ── Response Rendering ──

    def show_response(self, content: str):
        if not content.strip():
            return
        try:
            md = Markdown(content)
            self.console.print(md)
        except Exception:
            self.console.print(content)
        self.console.print()

    # ── Tool Call Display ──

    def show_tool_call(self, tool_name: str, args: dict):
        self.llm_calls += 1
        icon_map = {
            'read_file': '📖', 'write_file': '✏️', 'edit_file': '✏️',
            'search_files': '🔍', 'list_directory': '📁', 'execute_command': '⚡',
            'web_search': '🔍', 'fetch_url': '🌐', 'scrape_page': '🌐',
            'ask_user': '❓', 'write_note': '📝',
        }
        icon = icon_map.get(tool_name, '🔧')
        args_display = []
        for k, v in args.items():
            v_str = str(v)
            if len(v_str) > 80:
                v_str = v_str[:77] + "..."
            args_display.append(f"{k}: {v_str}")
        args_text = f"[{C['text_dim']}], [{C['text_dim']}]".join(args_display)
        self.console.print(
            f"  [{C['accent']}]{icon} {tool_name}[/{C['accent']}]"
            f"([{C['text_dim']}]{args_text}[/{C['text_dim']}])"
        )
        if tool_name == 'execute_command':
            self.commands_run += 1
        elif tool_name in ('write_file', 'edit_file'):
            self.files_written += 1

    def show_tool_result(self, tool_name: str, result: str, duration: float = 0):
        is_error = result.startswith("Error:") or result.startswith("BLOCKED:")
        is_ask = result.startswith("ASK_USER:")
        if is_ask:
            return

        # Check if result is JSON with diff (from edit_file)
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
            icon = "✗"
            color = C['error']
            self.console.print(f"    [{color}]{icon}[/{color}] {result}")
        elif is_diff_result and parsed:
            self._show_diff_result(parsed, duration)
        elif tool_name == 'execute_command' and not is_error:
            self._show_command_result(result, duration)
        elif tool_name == 'read_file' and not is_error:
            self._show_file_result(result, duration)
        elif tool_name in ('write_file', 'edit_file') and not is_error:
            # write_file now returns JSON too
            try:
                wp = json.loads(result)
                action = wp.get('action', 'Wrote')
                lines = wp.get('lines', '?')
                path = wp.get('path', '?')
                self.console.print(f"    [{C['success']}]✓[/{C['success']}] {action} {path} ({lines} lines)")
            except (json.JSONDecodeError, TypeError):
                self.console.print(f"    [{C['success']}]✓[/{C['success']}] {result}")
        elif tool_name == 'web_search' and not is_error:
            self._show_search_result(result)
        elif tool_name == 'fetch_url' and not is_error:
            self._show_fetch_result(result)
        elif tool_name == 'scrape_page' and not is_error:
            self._show_fetch_result(result)
        else:
            display = result
            if len(display) > 500:
                display = display[:250] + f"\n... ({len(result)} chars) ...\n" + display[-200:]
            icon = "✓"
            color = C['success']
            self.console.print(f"    [{color}]{icon}[/{color}] {display}")
        self.console.print()

    def _show_diff_result(self, parsed: dict, duration: float = 0):
        """Show a proper unified diff with red/green highlighting — NEW FEATURE."""
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

        self.console.print(Panel(
            content,
            title=f"[{C['writing']}]📝 Diff: {path}[/{C['writing']}]{dur_text}",
            border_style=C['border'], padding=(0, 1),
        ))
        self.console.print(f"    [{C['success']}]✓[/{C['success']}] {summary}")

    def _show_command_result(self, result: str, duration: float = 0):
        lines = result.splitlines()
        max_show = 30
        if len(lines) > max_show:
            head = "\n".join(lines[:15])
            tail = "\n".join(lines[-10:])
            display = f"{head}\n[{C['text_muted']}]... ({len(lines) - 25} lines hidden) ...[/{C['text_muted']}]\n{tail}"
        else:
            display = result
        dur_text = f" [{C['text_dim']}]{duration:.1f}s[/{C['text_dim']}]" if duration else ""
        self.console.print(Panel(
            f"[{C['text']}]{display}[/{C['text']}]",
            title=f"[{C['success']}]⚡ Output[/{C['success']}]{dur_text}",
            border_style=C['border'], padding=(0, 1),
        ))

    def _show_file_result(self, result: str, duration: float = 0):
        lines = result.splitlines()
        max_show = 40
        if len(lines) > max_show:
            display = "\n".join(lines[:20]) + f"\n[{C['text_muted']}]... ({len(lines) - 30} lines hidden) ...[/{C['text_muted']}]\n" + "\n".join(lines[-10:])
        else:
            display = result
        self.console.print(Panel(
            f"[{C['text']}]{display}[/{C['text']}]",
            title=f"[{C['reading']}]📖 File[/{C['reading']}]",
            border_style=C['border'], padding=(0, 1),
        ))

    def _show_search_result(self, result: str):
        self.console.print(Panel(
            f"[{C['text']}]{result}[/{C['text']}]",
            title=f"[{C['searching']}]🔍 Results[/{C['searching']}]",
            border_style=C['border'], padding=(0, 1),
        ))

    def _show_fetch_result(self, result: str):
        if len(result) > 3000:
            display = result[:1500] + f"\n... ({len(result)} chars) ...\n" + result[-1000:]
        else:
            display = result
        self.console.print(Panel(
            f"[{C['text']}]{display}[/{C['text']}]",
            title=f"[{C['searching']}]🌐 Page[/{C['searching']}]",
            border_style=C['border'], padding=(0, 1),
        ))

    def show_ask_user(self, question: str, options: list = None):
        self.console.print()
        if options:
            opts_text = "\n".join(
                f"  [{C['accent']}]{i+1}.[/{C['accent']}] {opt}" for i, opt in enumerate(options)
            )
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

    # ── Error & Status ──

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
        """Terminal bell notification for long-running tasks."""
        if self.config.get('sound_notifications', True):
            if sound_type == 'bell':
                sys.stdout.write('\a')
                sys.stdout.flush()

    # ── Status Bar ──

    def show_status_bar(self, state: str = 'idle', context_tokens: int = 0):
        state_icons = {
            'idle': (f'{MASCOT}💤', C['text_dim']),
            'thinking': (f'{MASCOT}💭', C['thinking']),
            'planning': (f'{MASCOT}📋', C['planning']),
            'working': (f'{MASCOT}⚡', C['working']),
            'searching': (f'{MASCOT}🔍', C['searching']),
            'reading': (f'{MASCOT}📖', C['reading']),
            'writing': (f'{MASCOT}✏️', C['writing']),
            'error': (f'{MASCOT}✗', C['error']),
            'success': (f'{MASCOT}✓', C['success']),
        }
        icon, color = state_icons.get(state, (MASCOT, C['primary']))
        elapsed = int(time.time() - self.session_start)
        ctx_ratio = context_tokens / MAX_CONTEXT_TOKENS if MAX_CONTEXT_TOKENS else 0
        if ctx_ratio < 0.5:
            ctx_color = C['success']
        elif ctx_ratio < 0.75:
            ctx_color = C['warning']
        else:
            ctx_color = C['error']
        filled = int(ctx_ratio * 12)
        ctx_bar = f"{'█' * filled}{'░' * (12 - filled)}"
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
            if item.name.startswith('.') and item.name not in ('.env', '.gitignore', '.pincer'):
                continue
            if item.name in skip:
                continue
            if item.is_dir():
                branch = tree.add(f"📁 {item.name}/")
                self._build_tree(branch, item, max_depth, current_depth + 1)
            else:
                icon = {"py": "🐍", "js": "📜", "ts": "📜", "md": "📄",
                        "json": "📋", "yaml": "📋", "yml": "📋", "toml": "📋",
                        "rs": "🦀", "go": "🔵", "rb": "💎"}.get(item.suffix.lstrip('.'), "📄")
                size = ""
                try:
                    st = item.stat().st_size
                    size = f" [{C['text_muted']}]{st//1024}KB[/{C['text_muted']}]" if st > 1024 else f" [{C['text_muted']}]{st}B[/{C['text_muted']}]"
                except Exception:
                    pass
                tree.add(f"{icon} {item.name}{size}")

    # ── Sidebar Layout ──

    def show_sidebar(self, plan: list = None, current_step: int = 0):
        """Show file tree + plan sidebar (for panel layout mode)."""
        layout = Layout()
        layout.split_column(
            Layout(name="files", ratio=1),
            Layout(name="plan", size=10),
        )

        # File tree
        cwd = Path.cwd()
        tree = Tree(f"📁 {cwd.name}", guide_style=C['border'])
        self._build_tree(tree, cwd, max_depth=2, current_depth=0)
        layout["files"].update(Panel(tree, title="📁 Files", border_style=C['border']))

        # Plan
        if plan:
            plan_lines = []
            for i, step in enumerate(plan):
                if i < current_step:
                    icon = f"[{C['success']}]✓[/{C['success']}]"
                elif i == current_step:
                    icon = f"[{C['primary_br']}]→[/{C['primary_br']}]"
                else:
                    icon = f"[{C['text_muted']}]○[/{C['text_muted']}]"
                plan_lines.append(f" {icon} {i+1}. {step[:40]}")
            plan_text = "\n".join(plan_lines)
            layout["plan"].update(Panel(plan_text, title="📋 Plan", border_style=C['planning']))
        else:
            layout["plan"].update(Panel("[dim]No active plan[/dim]", title="📋 Plan", border_style=C['border']))

        self.console.print(layout)

    # ── Plan Display ──

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
        self.console.print(Panel(
            "\n".join(lines),
            title=f"[{C['planning']}]🦞📋 Plan ({len(plan)} steps)[/{C['planning']}]",
            border_style=C['planning'], padding=(0, 1),
        ))

    # ── Task Summary ──

    def show_task_summary(self, plan: list, current_step: int, elapsed: float, stats: dict):
        completed = current_step
        total = len(plan)
        self.console.print()
        self.console.print(Panel(
            f"[{C['success']}]🦞✓ Task Complete![/{C['success']}]\n\n"
            f"  Steps completed: [{C['text_bright']}]{completed}/{total}[/{C['text_bright']}]\n"
            f"  Time elapsed:    [{C['text_bright']}]{elapsed/60:.1f} minutes[/{C['text_bright']}]\n"
            f"  LLM calls:       [{C['text_bright']}]{stats.get('llm_calls', 0)}[/{C['text_bright']}]\n"
            f"  Commands run:    [{C['text_bright']}]{stats.get('commands', 0)}[/{C['text_bright']}]\n"
            f"  Files written:   [{C['text_bright']}]{stats.get('writes', 0)}[/{C['text_bright']}]\n"
            f"  Errors:          [{C['text_bright']}]{stats.get('errors', 0)}[/{C['text_bright']}]",
            border_style=C['success'], padding=(1, 2),
        ))
        self.console.print()
        self.play_sound('bell')

    # ── Help ──

    def show_help(self):
        help_table = Table(
            title="🦞 Pincer Commands", show_header=True,
            border_style=C['border'], title_style=C['primary_br'],
        )
        help_table.add_column("Command", style=C['accent'], width=22)
        help_table.add_column("Description", style=C['text'], width=50)
        commands = [
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
            ("/notes", "View agent self-notes"),
            ("/files", "Show file tree"),
            ("/sessions", "List conversation sessions"),
            ("/export [fmt]", "Export session (md|json)"),
            ("/context", "Preview current context window"),
            ("/mcp <add|list|call>", "MCP server management"),
            ("/config", "Show current configuration"),
            ("/doctor", "Run diagnostics"),
            ("/stats", "Show session statistics"),
            ("/help", "Show this help"),
            ("/exit", "Exit Pincer"),
        ]
        for cmd, desc in commands:
            help_table.add_row(cmd, desc)
        self.console.print(help_table)

    def show_keybindings(self):
        kb_table = Table(
            title="⌨️  Key Bindings", show_header=True,
            border_style=C['border'], title_style=C['primary_br'],
        )
        kb_table.add_column("Key", style=C['accent'], width=25)
        kb_table.add_column("Action", style=C['text'], width=45)
        bindings = [
            ("Enter", "Send message"),
            ("Up/Down", "Navigate input history"),
            ("Ctrl+C", "Cancel current operation"),
            ("Tab", "Autocomplete commands"),
        ]
        for key, action in bindings:
            kb_table.add_row(key, action)
        self.console.print(kb_table)

    # ── Stats & Context Preview ──

    def show_stats(self):
        elapsed = int(time.time() - self.session_start)
        self.console.print(Panel(
            f"[{C['text_bright']}]Session Statistics[/{C['text_bright']}]\n\n"
            f"  Duration:     [{C['accent']}]{elapsed//60}m {elapsed%60}s[/{C['accent']}]\n"
            f"  LLM calls:   [{C['accent']}]{self.llm_calls}[/{C['accent']}]\n"
            f"  Commands:    [{C['accent']}]{self.commands_run}[/{C['accent']}]\n"
            f"  Files:       [{C['accent']}]{self.files_written}[/{C['accent']}]\n"
            f"  Errors:      [{C['accent']}]{self.errors_count}[/{C['accent']}]\n"
            f"  Turn count:  [{C['accent']}]{self.turn_count}[/{C['accent']}]",
            border_style=C['border'], padding=(1, 2),
        ))

    def show_context_preview(self, messages: list, total_tokens: int):
        """Show a preview of what's in the context window — NEW FEATURE."""
        sections = []
        for msg in messages:
            role = msg.get('role', '?')
            content = msg.get('content', '')
            has_tc = 'tool_calls' in msg and msg['tool_calls']
            token_est = max(1, len(content) // 4)
            if role == 'system':
                icon = "📋"
            elif role == 'user':
                icon = "👤"
            elif role == 'assistant':
                icon = "🦞"
            elif role == 'tool':
                icon = "🔧"
            else:
                icon = "❓"
            preview = content[:80].replace('\n', ' ')
            tc_mark = " [+tools]" if has_tc else ""
            sections.append(f"  {icon} [{C['text_dim']}]{role}[/{C['text_dim']}] ({token_est}t{tc_mark}): {preview}...")

        ctx_ratio = total_tokens / MAX_CONTEXT_TOKENS
        if ctx_ratio < 0.5:
            ctx_color = C['success']
        elif ctx_ratio < 0.75:
            ctx_color = C['warning']
        else:
            ctx_color = C['error']

        self.console.print(Panel(
            "\n".join(sections[:15]) + (f"\n\n  ... {len(messages) - 15} more messages" if len(messages) > 15 else ""),
            title=f"[{ctx_color}]📋 Context Preview ({total_tokens:,}/{MAX_CONTEXT_TOKENS:,} tokens)[/{ctx_color}]",
            border_style=C['border'], padding=(0, 1),
        ))


# ──────────────────────────────────────────────────────────────
# SECTION 13: THINKING TAG PARSER
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
# SECTION 14: AGENT LOOP (ReAct Pattern — All Bugs Fixed)
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
        """Run the agent loop — all 6 bugs fixed."""
        self._interrupted = False
        self.ui.turn_count += 1

        self.db.add_message(self.conv_id, 'user', user_input)
        self.conversation_messages.append({'role': 'user', 'content': user_input})

        final_response = ""

        for turn in range(MAX_TURNS):
            if self._interrupted:
                final_response = "[Interrupted]"
                break

            messages = self.context.assemble_context(self.conversation_messages)
            token_est = self.context.estimate_tokens(messages)
            if token_est >
