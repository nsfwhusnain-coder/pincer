#!/usr/bin/env python3
"""Pincer v2.2.0 — Autonomous Coding Agent — Blue Lobster Edition"""

import os, sys, re, json, time, uuid, signal, shutil, sqlite3
import subprocess, difflib
from pathlib import Path
from datetime import datetime
from collections import OrderedDict
from urllib.parse import quote as url_quote

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
        print(f"\033[38;5;39m🦞 Installing: {', '.join(missing)}\033[0m")
        try:
            subprocess.check_call(
                [sys.executable, '-m', 'pip', 'install', '--quiet'] + missing,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
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
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.rule import Rule
from rich.layout import Layout
from rich.theme import Theme
from pygments.lexers import guess_lexer_for_filename, TextLexer

VERSION = "2.2.0"
MASCOT = "\U0001f99e"
PINCER_DIR = Path.home() / ".pincer"
DB_PATH = PINCER_DIR / "pincer.db"
HISTORY_PATH = PINCER_DIR / "history"
CONFIG_PATH = PINCER_DIR / "config.json"
PINCER_MD = Path.cwd() / "PINCER.md"
DEFAULT_MODEL = "qwen3:8b"
MAX_TURNS = 25
MAX_CONTEXT_TOKENS = 12000
AGENT_LOOP_TIMEOUT = 300
THINK_OPEN = "<think" + ">"
THINK_CLOSE = "</think" + ">"

C = {
    'primary': '#2563EB', 'primary_br': '#3B82F6', 'accent': '#60A5FA',
    'text': '#93C5FD', 'text_dim': '#64748B', 'text_bright': '#DBEAFE',
    'text_muted': '#475569', 'thinking': '#A78BFA', 'planning': '#60A5FA',
    'working': '#3B82F6', 'searching': '#06B6D4', 'reading': '#8B5CF6',
    'writing': '#2563EB', 'error': '#EF4444', 'success': '#22C55E',
    'warning': '#F59E0B', 'info': '#06B6D4', 'border': '#1E3A6E',
    'diff_add': '#4ADE80', 'diff_del': '#F87171',
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
    'text.muted': C['text_muted'], 'border': C['border'],
})

ANSI = {
    'reset': '\033[0m', 'bold': '\033[1m', 'italic': '\033[3m',
    'purple': '\033[38;2;167;139;250m', 'blue': '\033[38;2;59;130;246m',
    'cyan': '\033[38;2;6;182;212m', 'amber': '\033[38;2;245;158;11m',
    'dim_blue': '\033[38;2;100;116;139m', 'light_blue': '\033[38;2;147;197;253m',
    'red': '\033[38;2;239;68;68m', 'green': '\033[38;2;34;197;94m',
}

TRUST_LEVELS = ['plan', 'default', 'acceptEdits', 'auto', 'dontAsk']
LAYOUT_MODES = ['stream', 'panel', 'compact']

DENY_PATTERNS = [
    r'^rm\s+-[a-zA-Z]*f\s+/', r'^mkfs', r'^dd\s+if=',
    r'curl\s+.*\|\s*(ba)?sh', r'^sudo\s+rm', r'>\s*/etc/',
    r'^chmod\s+-R\s+777\s+/', r'^git\s+push\s+--force',
]

SAFE_PATTERNS = [
    r'^git\s+(status|log|diff|show|branch)', r'^ls\s+.*',
    r'^cat\s+.*', r'^pwd$', r'^python\s+.*\.py$',
    r'^pytest\s+.*', r'^pip\s+(list|show|freeze|install)',
    r'^echo\s+.*', r'^which\s+.*', r'^head\s+.*',
    r'^tail\s+.*', r'^wc\s+.*', r'^find\s+.*',
    r'^grep\s+.*', r'^du\s+.*', r'^mkdir\s+.*',
]

TOOL_SCHEMAS = [
    {'type': 'function', 'function': {
        'name': 'read_file',
        'description': 'Read file contents with line numbers.',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'File path'},
            'offset': {'type': 'integer', 'description': 'Start line (default 1)'},
            'limit': {'type': 'integer', 'description': 'Max lines (default 200)'},
        }, 'required': ['path']},
    }},
    {'type': 'function', 'function': {
        'name': 'write_file',
        'description': 'Create or overwrite a file.',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'File path'},
            'content': {'type': 'string', 'description': 'File content'},
        }, 'required': ['path', 'content']},
    }},
    {'type': 'function', 'function': {
        'name': 'edit_file',
        'description': 'Edit file by replacing exact unique string match.',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'File path'},
            'old_string': {'type': 'string', 'description': 'Exact string to replace'},
            'new_string': {'type': 'string', 'description': 'Replacement string'},
        }, 'required': ['path', 'old_string', 'new_string']},
    }},
    {'type': 'function', 'function': {
        'name': 'search_files',
        'description': 'Search files by name or content.',
        'parameters': {'type': 'object', 'properties': {
            'pattern': {'type': 'string', 'description': 'Search pattern'},
            'search_type': {'type': 'string', 'enum': ['filename', 'content']},
            'path': {'type': 'string', 'description': 'Directory to search'},
        }, 'required': ['pattern']},
    }},
    {'type': 'function', 'function': {
        'name': 'list_directory',
        'description': 'List directory contents.',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'Directory path'},
            'recursive': {'type': 'boolean', 'description': 'Recursive listing'},
        }, 'required': []},
    }},
    {'type': 'function', 'function': {
        'name': 'execute_command',
        'description': 'Execute a shell command with timeout.',
        'parameters': {'type': 'object', 'properties': {
            'command': {'type': 'string', 'description': 'Shell command'},
            'timeout': {'type': 'integer', 'description': 'Timeout in seconds'},
        }, 'required': ['command']},
    }},
    {'type': 'function', 'function': {
        'name': 'web_search',
        'description': 'Search the web using DuckDuckGo.',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Search query'},
            'num_results': {'type': 'integer', 'description': 'Max results (default 5)'},
        }, 'required': ['query']},
    }},
    {'type': 'function', 'function': {
        'name': 'fetch_url',
        'description': 'Fetch a web page as text.',
        'parameters': {'type': 'object', 'properties': {
            'url': {'type': 'string', 'description': 'URL to fetch'},
            'max_length': {'type': 'integer', 'description': 'Max text length'},
        }, 'required': ['url']},
    }},
    {'type': 'function', 'function': {
        'name': 'ask_user',
        'description': 'Ask user a clarifying question.',
        'parameters': {'type': 'object', 'properties': {
            'question': {'type': 'string', 'description': 'Question'},
            'options': {'type': 'array', 'items': {'type': 'string'}},
        }, 'required': ['question']},
    }},
    {'type': 'function', 'function': {
        'name': 'write_note',
        'description': 'Write a self-note. Categories: observation, error, success, preference, pattern, fact.',
        'parameters': {'type': 'object', 'properties': {
            'category': {'type': 'string', 'description': 'Note category'},
            'content': {'type': 'string', 'description': 'Note content'},
        }, 'required': ['category', 'content']},
    }},
]


class PincerConfig:
    DEFAULTS = {
        'user_name': 'user', 'model': DEFAULT_MODEL,
        'language': 'python', 'experience': 'Intermediate',
        'trust_level': 'default', 'thinking_mode': 'auto',
        'layout_mode': 'stream', 'max_shell_timeout': 120,
        'max_file_size': 1000000, 'max_output_lines': 200,
        'web_search_enabled': True, 'sound_notifications': True,
        'first_run': True, 'schema_version': 0,
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
        with open(CONFIG_PATH, 'w') as f:
            json.dump(self.data, f, indent=2)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    @property
    def user_name(self):
        return self.data.get('user_name', 'user')

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


SCHEMA_VERSION = 3

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, title TEXT DEFAULT '',
    created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
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
CREATE TABLE IF NOT EXISTS tool_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    tool_name TEXT NOT NULL,
    args TEXT DEFAULT '',
    result_preview TEXT DEFAULT '',
    duration REAL DEFAULT 0,
    success INTEGER DEFAULT 1,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_msgs_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_notes_cat ON notes(category);
CREATE INDEX IF NOT EXISTS idx_thist_conv ON tool_history(conversation_id);
"""


class PincerDB:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        need_reset = False
        if DB_PATH.exists():
            try:
                conn = sqlite3.connect(str(DB_PATH))
                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
                if cur.fetchone():
                    cur = conn.execute("PRAGMA table_info(messages)")
                    cols = {row[1] for row in cur.fetchall()}
                    required = {'conversation_id', 'role', 'content', 'thinking', 'tool_calls', 'tool_name', 'token_count'}
                    if not required.issubset(cols):
                        need_reset = True
                conn.close()
            except Exception:
                need_reset = True
        if need_reset:
            backup = DB_PATH.with_suffix('.db.v1.bak')
            if DB_PATH.exists():
                shutil.copy2(str(DB_PATH), str(backup))
            DB_PATH.unlink(missing_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(DB_SCHEMA)
        self.conn.commit()

    def create_conversation(self, title=""):
        cid = str(uuid.uuid4())[:8]
        now = time.time()
        self.conn.execute("INSERT INTO conversations (id,title,created_at,updated_at) VALUES (?,?,?,?)", (cid, title, now, now))
        self.conn.commit()
        return cid

    def list_conversations(self):
        return self.conn.execute("SELECT id,title,created_at,updated_at FROM conversations ORDER BY updated_at DESC LIMIT 20").fetchall()

    def update_conversation(self, cid, title=None):
        now = time.time()
        if title:
            self.conn.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?", (title, now, cid))
        else:
            self.conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
        self.conn.commit()

    def add_message(self, cid, role, content="", thinking="", tool_calls="", tool_name="", token_count=0):
        self.conn.execute("INSERT INTO messages (conversation_id,role,content,thinking,tool_calls,tool_name,token_count,created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (cid, role, content, thinking, tool_calls, tool_name, token_count, time.time()))
        self.conn.commit()

    def get_messages(self, cid, limit=200):
        return self.conn.execute("SELECT role,content,thinking,tool_calls,tool_name FROM messages WHERE conversation_id=? ORDER BY id ASC LIMIT ?", (cid, limit)).fetchall()

    def count_messages(self, cid):
        return self.conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (cid,)).fetchone()[0]

    def add_note(self, category, content):
        tokens = ' '.join(re.findall(r'\b\w+\b', content.lower()))
        self.conn.execute("INSERT INTO notes (category,content,tokens,created_at) VALUES (?,?,?,?)", (category, content, tokens, time.time()))
        self.conn.commit()

    def get_notes(self, category=None, limit=20):
        if category:
            return self.conn.execute("SELECT category,content,created_at FROM notes WHERE category=? ORDER BY created_at DESC LIMIT ?", (category, limit)).fetchall()
        return self.conn.execute("SELECT category,content,created_at FROM notes ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    def search_notes(self, query, limit=5):
        qtoks = set(re.findall(r'\b\w+\b', query.lower()))
        if not qtoks:
            return []
        rows = self.conn.execute("SELECT id,category,content,tokens FROM notes ORDER BY created_at DESC LIMIT 100").fetchall()
        scored = []
        for nid, cat, content, toks in rows:
            overlap = len(qtoks & set(toks.split()))
            if overlap > 0:
                scored.append((overlap, cat, content))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(c, t) for _, c, t in scored[:limit]]

    def add_tool_history(self, cid, tool_name, args="", result_preview="", duration=0.0, success=True):
        self.conn.execute("INSERT INTO tool_history (conversation_id,tool_name,args,result_preview,duration,success,created_at) VALUES (?,?,?,?,?,?,?)",
                          (cid, tool_name, args, result_preview, duration, 1 if success else 0, time.time()))
        self.conn.commit()

    def get_tool_history(self, cid, limit=20):
        return self.conn.execute("SELECT tool_name,args,result_preview,duration,success,created_at FROM tool_history WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (cid, limit)).fetchall()

    def save_checkpoint(self, cid, step, desc, state):
        self.conn.execute("INSERT INTO checkpoints (conversation_id,step_number,description,state_json,created_at) VALUES (?,?,?,?,?)", (cid, step, desc, json.dumps(state), time.time()))
        self.conn.commit()

    def get_latest_checkpoint(self, cid):
        return self.conn.execute("SELECT step_number,description,state_json,created_at FROM checkpoints WHERE conversation_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()

    def close(self):
        self.conn.close()


class MemorySystem:
    def __init__(self, db, config):
        self.db = db
        self.config = config
        self.working = {'current_plan': None, 'current_step': 0, 'open_files': [], 'recent_edits': []}
        self._ensure_pincer_md()

    def _ensure_pincer_md(self):
        if not PINCER_MD.exists():
            PINCER_MD.write_text(f"# PINCER.md\n# Auto-managed by Pincer v{VERSION}\n\n## User Preferences\n- Name: {self.config.user_name}\n\n## Learned Facts\n(learned automatically)\n")

    def read_pincer_md(self):
        return PINCER_MD.read_text() if PINCER_MD.exists() else ""

    def add_note(self, category, content):
        self.db.add_note(category, content)
        md = self.read_pincer_md()
        section_map = {'preference': '## User Preferences', 'fact': '## Learned Facts',
                       'error': '## Learned Facts', 'success': '## Learned Facts',
                       'pattern': '## Learned Facts', 'observation': '## Learned Facts'}
        section = section_map.get(category, '## Learned Facts')
        if section in md:
            md = md.replace(section, f"{section}\n- {content}")
            PINCER_MD.write_text(md)

    def get_notes_for_context(self, limit=5):
        notes = self.db.get_notes(limit=limit)
        return "\n".join(f"- [{c}] {t}" for c, t, _ in notes) if notes else ""

    def get_procedural_memory(self):
        return self.read_pincer_md()[:1500]

    def set_working(self, key, value):
        self.working[key] = value

    def get_working(self, key, default=None):
        return self.working.get(key, default)

    def get_state(self):
        return dict(self.working)

    def restore_state(self, state):
        if state:
            self.working.update(state)


class WebCache:
    def __init__(self, ttl=300, max_size=50):
        self.ttl = ttl
        self.max_size = max_size
        self._cache = OrderedDict()

    def get(self, key):
        if key in self._cache:
            result, ts = self._cache[key]
            if time.time() - ts < self.ttl:
                self._cache.move_to_end(key)
                return result
            del self._cache[key]
        return None

    def set(self, key, result):
        if key in self._cache:
            del self._cache[key]
        self._cache[key] = (result, time.time())
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)


class OllamaBackend:
    def __init__(self, config):
        self.config = config
        self.model = config.model
        self.client = ollama.Client(host='http://localhost:11434')
        self._ensure_model()
        self._tool_schema_tokens = self.count_tokens_approx(json.dumps(TOOL_SCHEMAS))

    def _ensure_model(self):
        try:
            models = self.client.list()
            names = [m.get('name', '') or getattr(m, 'model', '') for m in models.get('models', [])]
            if not any(self.model in n for n in names):
                print(f"{ANSI['cyan']}{MASCOT} Pulling {self.model}...{ANSI['reset']}")
                self.client.pull(self.model)
        except Exception as e:
            print(f"{ANSI['red']}{MASCOT} Cannot connect to Ollama: {e}{ANSI['reset']}")
            print(f"{ANSI['amber']}  Run: ollama serve{ANSI['reset']}")
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
        kwargs = {'model': self.model, 'messages': messages, 'stream': stream}
        if tools:
            kwargs['tools'] = tools
        if think and self.config.thinking_mode != 'off':
            try:
                kwargs['think'] = True
            except Exception:
                pass
        for attempt in range(3):
            try:
                return self.client.chat(**kwargs)
            except TypeError:
                kwargs.pop('think', None)
                return self.client.chat(**kwargs)
            except (ollama.ResponseError, ConnectionError, OSError) as e:
                if attempt == 2:
                    raise RuntimeError(f"Ollama error: {e}") from e
                time.sleep(min(2 ** attempt, 30))
            except Exception as e:
                if attempt == 2:
                    raise RuntimeError(f"Error: {e}") from e
                time.sleep(min(2 ** attempt, 30))

    def stream_chat(self, messages, tools=None, think=False):
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
        except Exception as e:
            raise RuntimeError(f"Stream error: {e}") from e

    def count_tokens_approx(self, text):
        return max(1, len(text) // 4)

    def set_model(self, model):
        self.model = model
        self.config.set('model', model)
        self._ensure_model()

    def should_think(self):
        m = self.config.thinking_mode
        return m != 'off'


class ToolExecutor:
    def __init__(self, config, db, web_cache):
        self.config = config
        self.db = db
        self.web_cache = web_cache
        self.cwd = str(Path.cwd())

    def execute(self, name, args):
        handler = getattr(self, f'_tool_{name}', None)
        if not handler:
            return f"Error: Unknown tool '{name}'"
        try:
            return handler(**{k: v for k, v in args.items() if v is not None})
        except TypeError as e:
            return f"Error: Invalid args for {name}: {e}"
        except Exception as e:
            return f"Error in {name}: {e}"

    def _tool_read_file(self, path, offset=1, limit=200):
        fp = Path(path).expanduser().resolve()
        if not fp.exists():
            return f"Error: File not found: {path}"
        if fp.is_dir():
            return f"Error: Is a directory: {path}"
        if fp.stat().st_size > self.config.get('max_file_size', 1000000):
            return f"Error: File too large. Use offset/limit."
        try:
            lines = fp.read_text(errors='replace').splitlines()
            s, e = max(0, offset - 1), min(len(lines), offset - 1 + limit)
            result = [f"{i+1:6d} | {lines[i]}" for i in range(s, e)]
            return f"File: {path} ({len(lines)} lines, showing {s+1}-{e})\n" + "\n".join(result)
        except PermissionError:
            return f"Error: Permission denied: {path}"
        except Exception as e:
            return f"Error: {e}"

    def _tool_write_file(self, path, content):
        fp = Path(path).expanduser().resolve()
        try:
            fp.parent.mkdir(parents=True, exist_ok=True)
            existed = fp.exists()
            fp.write_text(content)
            lc = content.count('\n') + 1
            return json.dumps({'action': 'Updated' if existed else 'Created', 'path': path, 'lines': lc})
        except Exception as e:
            return f"Error: {e}"

    def _tool_edit_file(self, path, old_string, new_string):
        fp = Path(path).expanduser().resolve()
        if not fp.exists():
            return f"Error: File not found: {path}"
        try:
            content = fp.read_text()
        except Exception as e:
            return f"Error: {e}"
        count = content.count(old_string)
        if count == 0:
            return f"Error: String not found in {path}"
        if count > 1:
            return f"Error: String found {count} times. Provide more context."
        old_content = content
        new_content = content.replace(old_string, new_string, 1)
        try:
            fp.write_text(new_content)
        except Exception as e:
            return f"Error: {e}"
        diff_lines = list(difflib.unified_diff(
            old_content.splitlines(keepends=True), new_content.splitlines(keepends=True),
            fromfile=f"{path} (before)", tofile=f"{path} (after)", lineterm=''))
        old_n, new_n = len(old_string.splitlines()), len(new_string.splitlines())
        summary = f"Replaced {old_n} line(s) with {new_n} line(s) in {path}"
        return json.dumps({'summary': summary, 'path': path, 'diff': '\n'.join(diff_lines)})

    def _tool_search_files(self, pattern, search_type='filename', path=None):
        sd = Path(path).expanduser().resolve() if path else Path.cwd()
        if not sd.exists():
            return f"Error: Directory not found: {path}"
        results = []
        try:
            if search_type == 'filename':
                for p in sd.rglob(pattern):
                    if len(results) >= 30:
                        break
                    try:
                        results.append(str(p.relative_to(sd)))
                    except ValueError:
                        results.append(str(p))
            else:
                try:
                    regex = re.compile(pattern, re.IGNORECASE)
                except re.error:
                    return f"Error: Invalid regex"
                for p in sd.rglob('*'):
                    if not p.is_file() or p.stat().st_size > 100000 or len(results) >= 20:
                        continue
                    try:
                        for i, line in enumerate(p.read_text(errors='ignore').splitlines()[:500], 1):
                            if regex.search(line):
                                results.append(f"{p.relative_to(sd)}:{i}: {line.strip()[:120]}")
                                if len(results) >= 20:
                                    break
                    except Exception:
                        continue
        except Exception as e:
            return f"Error: {e}"
        return "\n".join(results) if results else f"No results for '{pattern}'"

    def _tool_list_directory(self, path=None, recursive=False):
        dp = Path(path).expanduser().resolve() if path else Path.cwd()
        if not dp.exists():
            return f"Error: Not found: {path}"
        results = []
        try:
            for p in sorted(dp.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                pre = "\U0001f4c1" if p.is_dir() else "\U0001f4c4"
                results.append(f"{pre} {p.name}")
        except PermissionError:
            return "Error: Permission denied"
        return "\n".join(results)

    def _tool_execute_command(self, command, timeout=None):
        timeout = timeout or self.config.get('max_shell_timeout', 120)
        for pat in DENY_PATTERNS:
            if re.search(pat, command, re.IGNORECASE):
                return "BLOCKED: Dangerous pattern. Run directly in terminal if sure."
        try:
            r = subprocess.run(command, shell=True, cwd=self.cwd, capture_output=True,
                               text=True, timeout=timeout, env={**os.environ, 'TERM': 'dumb'})
            out = (r.stdout or '') + (('\nSTDERR:\n' + r.stderr) if r.stderr else '')
            if not out:
                out = f"(no output, exit code: {r.returncode})"
            lines = out.splitlines()
            if len(lines) > 200:
                out = "\n".join(lines[:20]) + f"\n... ({len(lines)-40} lines truncated) ...\n" + "\n".join(lines[-20:])
            if r.returncode != 0:
                out += f"\nExit code: {r.returncode}"
            return out
        except subprocess.TimeoutExpired:
            return f"Error: Timeout after {timeout}s"
        except Exception as e:
            return f"Error: {e}"

    def _tool_web_search(self, query, num_results=5):
        if not self.config.get('web_search_enabled', True):
            return "Error: Web search disabled"
        ck = f"search:{query}"
        cached = self.web_cache.get(ck)
        if cached:
            return cached + "\n(cached)"
        try:
            url = f"https://lite.duckduckgo.com/lite/?q={url_quote(query)}"
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(url, headers={'User-Agent': 'Mozilla/5.0'})
                soup = BeautifulSoup(resp.text, 'html.parser')
            results = []
            for a in soup.find_all('a'):
                href = a.get('href', '')
                title = a.get_text(strip=True)
                if href.startswith('http') and 'duck' not in href and title and len(title) > 5 and len(results) < num_results:
                    results.append(f"{len(results)+1}. {title}\n   {href}")
            if not results:
                return f"No results for '{query}'"
            text = "\n\n".join(results)
            self.web_cache.set(ck, text)
            return text
        except Exception as e:
            return f"Error: {e}"

    def _tool_fetch_url(self, url, max_length=5000):
        ck = f"fetch:{url}"
        cached = self.web_cache.get(ck)
        if cached:
            return cached + "\n(cached)"
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                resp = client.get(url, headers={'User-Agent': 'Mozilla/5.0'})
                resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
                tag.decompose()
            text = re.sub(r'\n{3,}', '\n\n', soup.get_text(separator='\n', strip=True))
            if len(text) > max_length:
                text = text[:max_length] + f"\n... (truncated at {max_length} chars)"
            title = soup.title.string.strip() if soup.title and soup.title.string else url
            result = f"Title: {title}\nURL: {url}\n\n{text}"
            self.web_cache.set(ck, result)
            return result
        except Exception as e:
            return f"Error: {e}"

    def _tool_ask_user(self, question, options=None):
        return f"ASK_USER:{json.dumps({'question': question, 'options': options or []})}"

    def _tool_write_note(self, category, content):
        self.db.add_note(category, content)
        return f"Note saved: [{category}] {content}"


class PermissionSystem:
    def __init__(self, config, console):
        self.config = config
        self.console = console
        self.session_approvals = set()

    @property
    def trust_level(self):
        return self.config.trust_level

    def check(self, tool_name, args):
        trust = self.trust_level
        if tool_name in ('read_file', 'list_directory', 'search_files') and trust != 'plan':
            return True
        if tool_name in ('write_note', 'ask_user'):
            return True
        if tool_name in ('web_search', 'fetch_url') and trust != 'plan':
            return True
        if tool_name in ('write_file', 'edit_file'):
            if trust in ('auto', 'dontAsk', 'acceptEdits') or 'file_edits' in self.session_approvals:
                return True
            return self._ask(tool_name, args)
        if tool_name == 'execute_command':
            cmd = args.get('command', '')
            for pat in DENY_PATTERNS:
                if re.search(pat, cmd, re.IGNORECASE):
                    self.console.print(f"[{C['error']}]Blocked dangerous command[/{C['error']}]")
                    return False
            for pat in SAFE_PATTERNS:
                if re.search(pat, cmd, re.IGNORECASE) and trust != 'plan':
                    return True
            ak = f"cmd:{cmd.split()[0] if cmd.split() else cmd}"
            if ak in self.session_approvals or trust == 'dontAsk':
                return True
            return self._ask(tool_name, args)
        if trust == 'plan':
            return self._ask(tool_name, args)
        return True

    def _ask(self, tool_name, args):
        self.console.print()
        if tool_name == 'execute_command':
            self.console.print(Panel(f"[{C['text_bright']}]$ {args.get('command','')}[/{C['text_bright']}]",
                title=f"[{C['warning']}]Allow command?[/{C['warning']}]", border_style=C['warning'], padding=(1,2)))
        elif tool_name in ('write_file', 'edit_file'):
            self.console.print(Panel(f"[{C['text_bright']}]{tool_name}: {args.get('path','')}[/{C['text_bright']}]",
                title=f"[{C['warning']}]Allow?[/{C['warning']}]", border_style=C['warning'], padding=(1,2)))
        else:
            self.console.print(Panel(f"[{C['text_bright']}]{tool_name}[/{C['text_bright']}]",
                title=f"[{C['warning']}]Allow?[/{C['warning']}]", border_style=C['warning'], padding=(1,2)))
        self.console.print(f"  [{C['text_dim']}]\\[Y] Yes  \\[A] Allow all  \\[N] No[/{C['text_dim']}]", highlight=False)
        try:
            choice = input(f"{ANSI['amber']}{MASCOT}❓ > {ANSI['reset']}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if choice in ('y', 'yes', ''):
            return True
        if choice == 'a':
            if tool_name == 'execute_command':
                self.session_approvals.add(f"cmd:{args.get('command','').split()[0]}")
            elif tool_name in ('write_file', 'edit_file'):
                self.session_approvals.add('file_edits')
            return True
        return False


class ContextManager:
    SYSTEM_TEMPLATE = """You are Pincer, an autonomous coding agent running locally via Ollama.

## Identity
- You are Pinch, a helpful coding assistant. Show reasoning before acting.

## Capabilities
- Read, write, edit files; execute commands; search web; fetch URLs; write notes.

## Rules
1. Use tools to accomplish tasks — don't just describe.
2. Read files before writing code.
3. Verify changes work.
4. Write notes about findings and preferences.
5. Ask if ambiguous.
6. If something fails, diagnose and fix before asking.

## Environment
- Working directory: {cwd}
- User: {user_name}
- Language: {language}

## Memory
{memory}

## Notes
{notes}
"""

    def __init__(self, config, memory, backend):
        self.config = config
        self.memory = memory
        self.backend = backend

    def build_system_prompt(self):
        return self.SYSTEM_TEMPLATE.format(
            cwd=str(Path.cwd()), user_name=self.config.user_name,
            language=self.config.get('language', 'python'),
            memory=self.memory.get_procedural_memory()[:1000],
            notes=self.memory.get_notes_for_context(5))

    def assemble_context(self, conv_msgs, user_input=None):
        msgs = [{'role': 'system', 'content': self.build_system_prompt()}]
        sys_tok = self.backend.count_tokens_approx(msgs[0]['content'])
        budget = MAX_CONTEXT_TOKENS - sys_tok - self.backend.tool_schema_tokens - 1500
        history, used = [], 0
        for m in reversed(conv_msgs):
            t = self.backend.count_tokens_approx(m.get('content', ''))
            if used + t > budget:
                break
            history.insert(0, m)
            used += t
        msgs.extend(history)
        if user_input:
            msgs.append({'role': 'user', 'content': user_input})
        return msgs

    def compact_context(self, messages):
        if not messages:
            return messages
        compacted = []
        for m in messages:
            c = m.get('content', '')
            if len(c) > 2000:
                c = c[:500] + f"\n... (truncated {len(c)} chars) ...\n" + c[-500:]
                m = {**m, 'content': c}
            compacted.append(m)
        if len(compacted) > 15:
            turns = self._group_turns(compacted)
            if len(turns) > 8:
                mid_text = " | ".join(
                    f"{m.get('role','?')}: {m.get('content','')[:60]}..."
                    for t in turns[2:-6] for m in t)[:600]
                compacted = []
                for t in turns[:2]:
                    compacted.extend(t)
                compacted.append({'role': 'system', 'content': f'[Earlier summary: {mid_text}]'})
                for t in turns[-6:]:
                    compacted.extend(t)
        return compacted

    def _group_turns(self, msgs):
        turns, cur = [], []
        for m in msgs:
            role = m.get('role', '')
            has_tc = 'tool_calls' in m and m['tool_calls']
            if role in ('system', 'user') and cur:
                turns.append(cur)
                cur = [m]
            elif role == 'assistant' and has_tc and cur:
                turns.append(cur)
                cur = [m]
            elif role == 'tool':
                cur.append(m)
            elif role == 'assistant' and cur:
                turns.append(cur)
                cur = [m]
            else:
                cur.append(m)
        if cur:
            turns.append(cur)
        return turns

    def estimate_tokens(self, messages):
        return sum(self.backend.count_tokens_approx(m.get('content', '')) for m in messages)


class UIRenderer:
    def __init__(self, config):
        self.config = config
        self.console = Console(theme=DEEP_OCEAN, highlight=False)
        self.user_name = config.user_name
        self.session_start = time.time()
        self.turn_count = 0
        self.llm_calls = 0
        self.commands_run = 0
        self.files_written = 0
        self.errors_count = 0

    def show_banner(self):
        self.console.print()
        self.console.print(Panel(f"[{C['primary_br']}]{MASCOT}  P I N C E R  v{VERSION}[/{C['primary_br']}]\n[{C['text_dim']}]Autonomous Coding Agent — Blue Lobster Edition[/{C['text_dim']}]",
            border_style=C['primary'], padding=(1, 4)))
        self.console.print()

    def show_welcome(self):
        self.console.print(f"[{C['accent']}]Welcome! {MASCOT}[/{C['accent']}]  [{C['text_dim']}]Type /help for commands[/{C['text_dim']}]")
        self.console.print()

    def show_activity(self, activity):
        labels = {'thinking': ('thinking', 'Thinking...'), 'planning': ('planning', 'Planning...'),
                  'working': ('working', 'Working...'), 'searching': ('searching', 'Searching...'),
                  'reading': ('reading', 'Reading...'), 'writing': ('writing', 'Writing...'),
                  'fetching': ('searching', 'Fetching...'), 'compacting': ('text_dim', 'Compacting...')}
        color, label = labels.get(activity, ('primary', activity))
        self.console.print(f"[{C.get(color, C['primary'])}]{MASCOT} {label}[/{C.get(color, C['primary'])}]")

    def show_thinking_block(self, text):
        if not text.strip():
            return
        d = text[:600] + f"\n... ({len(text)} chars) ..." + text[-400:] if len(text) > 1200 else text
        self.console.print(Panel(f"[{C['thinking']}]{d}[/{C['thinking']}]",
            title=f"[{C['thinking']}]{MASCOT} Thinking[/{C['thinking']}]", border_style=C['thinking'], padding=(0,1)))

    def show_tool_call(self, name, args):
        self.llm_calls += 1
        icons = {'read_file': '📖', 'write_file': '✏️', 'edit_file': '✏️', 'search_files': '🔍',
                 'list_directory': '📁', 'execute_command': '⚡', 'web_search': '🔍',
                 'fetch_url': '🌐', 'ask_user': '❓', 'write_note': '📝'}
        icon = icons.get(name, '🔧')
        args_str = ", ".join(f"{k}: {str(v)[:60]}" for k, v in args.items())
        self.console.print(f"  [{C['accent']}]{icon} {name}[/{C['accent']}]([{C['text_dim']}]{args_str}[/{C['text_dim']}])")
        if name == 'execute_command':
            self.commands_run += 1
        elif name in ('write_file', 'edit_file'):
            self.files_written += 1

    def show_tool_result(self, name, result, duration=0):
        is_err = result.startswith("Error:") or result.startswith("BLOCKED:")
        if result.startswith("ASK_USER:"):
            return
        parsed = None
        try:
            parsed = json.loads(result)
        except Exception:
            pass
        if is_err:
            self.errors_count += 1
            self.console.print(f"    [{C['error']}]✗[/{C['error']}] {result}")
        elif isinstance(parsed, dict) and 'diff' in parsed:
            self._show_diff(parsed, duration)
        elif name == 'execute_command' and not is_err:
            self._show_cmd_result(result, duration)
        elif name == 'read_file' and not is_err:
            self._show_file_result(result, duration)
        elif name in ('write_file', 'edit_file') and not is_err and parsed:
            self.console.print(f"    [{C['success']}]✓[/{C['success']}] {parsed.get('action','Done')} {parsed.get('path','')} ({parsed.get('lines','?')} lines)")
        elif name == 'web_search' and not is_err:
            self.console.print(Panel(f"[{C['text']}]{result}[/{C['text']}]", title=f"[{C['searching']}]🔍 Results[/{C['searching']}]", border_style=C['border'], padding=(0,1)))
        elif name == 'fetch_url' and not is_err:
            d = result[:1500] + f"\n... ({len(result)} chars)" + result[-1000:] if len(result) > 3000 else result
            self.console.print(Panel(f"[{C['text']}]{d}[/{C['text']}]", title=f"[{C['searching']}]🌐 Page[/{C['searching']}]", border_style=C['border'], padding=(0,1)))
        else:
            self.console.print(f"    [{C['success']}]✓[/{C['success']}] {result[:200]}")
        self.console.print()

    def _show_diff(self, parsed, duration=0):
        diff_text = parsed.get('diff', '')
        summary = parsed.get('summary', '')
        path = parsed.get('path', '?')
        if not diff_text:
            self.console.print(f"    [{C['success']}]✓[/{C['success']}] {summary}")
            return
        lines = []
        for line in diff_text.splitlines():
            if line.startswith('---') or line.startswith('+++'):
                lines.append(f"[{C['text_dim']}]{line}[/{C['text_dim']}]")
            elif line.startswith('@@'):
                lines.append(f"[{C['info']}]{line}[/{C['info']}]")
            elif line.startswith('+'):
                lines.append(f"[{C['diff_add']}]{line}[/{C['diff_add']}]")
            elif line.startswith('-'):
                lines.append(f"[{C['diff_del']}]{line}[/{C['diff_del']}]")
            else:
                lines.append(f"[{C['text']}]{line}[/{C['text']}]")
        dur = f" [{C['text_dim']}]{duration:.1f}s[/{C['text_dim']}]" if duration else ""
        self.console.print(Panel("\n".join(lines), title=f"[{C['writing']}]📝 {path}[/{C['writing']}]{dur}", border_style=C['border'], padding=(0,1)))
        self.console.print(f"    [{C['success']}]✓[/{C['success']}] {summary}")

    def _show_cmd_result(self, result, duration=0):
        lns = result.splitlines()
        if len(lns) > 30:
            d = "\n".join(lns[:15]) + f"\n[{C['text_muted']}]... {len(lns)-25} lines hidden ...[/{C['text_muted']}]\n" + "\n".join(lns[-10:])
        else:
            d = result
        dur = f" [{C['text_dim']}]{duration:.1f}s[/{C['text_dim']}]" if duration else ""
        self.console.print(Panel(f"[{C['text']}]{d}[/{C['text']}]", title=f"[{C['success']}]⚡ Output[/{C['success']}]{dur}", border_style=C['border'], padding=(0,1)))

    def _show_file_result(self, result, duration=0):
        lns = result.splitlines()
        path_match = re.match(r'File: (.+?) \(', lns[0]) if lns else None
        fp = path_match.group(1) if path_match else None
        if fp and len(lns) > 3 and len(lns) <= 40:
            try:
                code_lines = []
                for line in lns[1:]:
                    m = re.match(r'^\s*\d+\s*\|\s(.*)$', line)
                    code_lines.append(m.group(1) if m else line)
                code = "\n".join(code_lines)
                lexer = guess_lexer_for_filename(fp, code)
                if not isinstance(lexer, TextLexer):
                    syn = Syntax(code, lexer.name, theme="monokai", line_numbers=True)
                    self.console.print(Panel(syn, title=f"[{C['reading']}]📖 {Path(fp).name}[/{C['reading']}]", border_style=C['border'], padding=(0,1)))
                    return
            except Exception:
                pass
        if len(lns) > 40:
            d = "\n".join(lns[:20]) + f"\n[{C['text_muted']}]... {len(lns)-30} lines hidden ...[/{C['text_muted']}]\n" + "\n".join(lns[-10:])
        else:
            d = result
        self.console.print(Panel(f"[{C['text']}]{d}[/{C['text']}]", title=f"[{C['reading']}]📖 File[/{C['reading']}]", border_style=C['border'], padding=(0,1)))

    def show_ask_user(self, question, options=None):
        self.console.print()
        if options:
            opts = "\n".join(f"  [{C['accent']}]{i+1}.[/{C['accent']}] {o}" for i, o in enumerate(options))
            self.console.print(Panel(f"[{C['text_bright']}]{question}[/{C['text_bright']}]\n\n{opts}",
                title=f"[{C['warning']}]{MASCOT}❓[/{C['warning']}]", border_style=C['warning'], padding=(1,2)))
        else:
            self.console.print(Panel(f"[{C['text_bright']}]{question}[/{C['text_bright']}]",
                title=f"[{C['warning']}]{MASCOT}❓[/{C['warning']}]", border_style=C['warning'], padding=(1,2)))

    def show_error(self, msg):
        self.console.print(f"[{C['error']}]{MASCOT}✗ {msg}[/{C['error']}]")
        self.errors_count += 1

    def show_warning(self, msg):
        self.console.print(f"[{C['warning']}]⚠️  {msg}[/{C['warning']}]")

    def show_info(self, msg):
        self.console.print(f"[{C['info']}]ℹ️  {msg}[/{C['info']}]")

    def show_success(self, msg):
        self.console.print(f"[{C['success']}]{MASCOT}✓ {msg}[/{C['success']}]")

    def show_permission_denied(self, name):
        self.console.print(f"[{C['text_dim']}]⊘ Denied: {name}[/{C['text_dim']}]")

    def show_status_bar(self, state='idle', ctx_tokens=0):
        icons = {'idle': (f'{MASCOT}💤', C['text_dim']), 'thinking': (f'{MASCOT}💭', C['thinking']),
                 'working': (f'{MASCOT}⚡', C['working']), 'searching': (f'{MASCOT}🔍', C['searching']),
                 'reading': (f'{MASCOT}📖', C['reading']), 'error': (f'{MASCOT}✗', C['error']),
                 'success': (f'{MASCOT}✓', C['success'])}
        icon, color = icons.get(state, (MASCOT, C['primary']))
        el = int(time.time() - self.session_start)
        ratio = ctx_tokens / MAX_CONTEXT_TOKENS if MAX_CONTEXT_TOKENS else 0
        cc = C['success'] if ratio < 0.5 else C['warning'] if ratio < 0.75 else C['error']
        self.console.print(Rule(
            f"[{color}]{icon}[/{color}] │ [{C['text_dim']}]{self.user_name}[/{C['text_dim']}] │ "
            f"[{cc}]ctx {ctx_tokens:,}/{MAX_CONTEXT_TOKENS:,}[/{cc}] │ "
            f"[{C['text_dim']}]{el//60}m{el%60:02d}s[/{C['text_dim']}] │ "
            f"[{C['text_dim']}]{self.config.model}[/{C['text_dim']}] │ "
            f"[{C['text_dim']}]{self.llm_calls} calls[/{C['text_dim']}]",
            style=C['border'], characters="─"))

    def show_file_tree(self, path=None):
        dp = Path(path) if path else Path.cwd()
        if not dp.exists():
            self.show_error(f"Not found: {dp}")
            return
        tree = Tree(f"\U0001f4c1 {dp.name}", guide_style=C['border'])
        self._build_tree(tree, dp, 3, 0)
        self.console.print(tree)

    def _build_tree(self, tree, path, max_d, d):
        if d >= max_d:
            return
        try:
            items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        skip = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.tox', '.mypy_cache', '.pytest_cache', 'dist', 'build'}
        for item in items:
            if item.name.startswith('.') and item.name not in ('.env', '.gitignore'):
                continue
            if item.name in skip:
                continue
            if item.is_dir():
                self._build_tree(tree.add(f"\U0001f4c1 {item.name}/"), item, max_d, d+1)
            else:
                ext_map = {'py': '\U0001f40d', 'js': '\U0001f4dc', 'md': '\U0001f4c4', 'json': '\U0001f4cb'}
                icon = ext_map.get(item.suffix.lstrip('.'), '\U0001f4c4')
                tree.add(f"{icon} {item.name}")

    def show_sidebar(self, plan=None, step=0):
        cwd = Path.cwd()
        tree = Tree(f"\U0001f4c1 {cwd.name}", guide_style=C['border'])
        self._build_tree(tree, cwd, 2, 0)
        layout = Layout()
        layout.split_column(Layout(name="files", ratio=3), Layout(name="plan", size=8))
        layout["files"].update(Panel(tree, title="\U0001f4c1 Files", border_style=C['border']))
        if plan:
            pl = []
            for i, s in enumerate(plan[:10]):
                ic = f"[{C['success']}]✓[/{C['success']}]" if i < step else f"[{C['primary_br']}]→[/{C['primary_br']}]" if i == step else f"[{C['text_muted']}]○[/{C['text_muted']}]"
                pl.append(f" {ic} {i+1}. {s[:40]}")
            layout["plan"].update(Panel("\n".join(pl), title="\U0001f4cb Plan", border_style=C['planning']))
        else:
            layout["plan"].update(Panel(f"[{C['text_muted']}]No active plan[/{C['text_muted']}]", title="\U0001f4cb Plan", border_style=C['border']))
        self.console.print(layout)

    def show_plan(self, plan, step=0):
        lines = []
        for i, s in enumerate(plan):
            ic = f"[{C['success']}]✓[/{C['success']}]" if i < step else f"[{C['primary_br']}]→[/{C['primary_br']}]" if i == step else f"[{C['text_muted']}]○[/{C['text_muted']}]"
            lines.append(f"  {ic} {i+1}. {s}")
        self.console.print(Panel("\n".join(lines), title=f"[{C['planning']}]{MASCOT} Plan ({len(plan)} steps)[/{C['planning']}]",
            border_style=C['planning'], padding=(0,1)))

    def show_task_summary(self, plan, step, elapsed, stats):
        self.console.print()
        self.console.print(Panel(
            f"[{C['success']}]{MASCOT}✓ Task Complete![/{C['success']}]\n\n"
            f"  Steps: [{C['text_bright']}]{step}/{len(plan)}[/{C['text_bright']}]  "
            f"Time: [{C['text_bright']}]{elapsed/60:.1f}m[/{C['text_bright']}]  "
            f"LLM: [{C['text_bright']}]{stats.get('llm_calls',0)}[/{C['text_bright']}]  "
            f"Errors: [{C['text_bright']}]{stats.get('errors',0)}[/{C['text_bright']}]",
            border_style=C['success'], padding=(1,2)))
        self.console.print()
        if self.config.get('sound_notifications', True):
            sys.stdout.write('\a')
            sys.stdout.flush()

    def show_tool_history(self, history):
        if not history:
            self.show_info("No tool calls yet.")
            return
        table = Table(title="\U0001f4dc Tool History", border_style=C['border'], title_style=C['accent'])
        table.add_column("#", style=C['text_dim'], width=4)
        table.add_column("Tool", style=C['accent'], width=15)
        table.add_column("Args", style=C['text'], width=40)
        table.add_column("Dur", style=C['text_dim'], width=8)
        table.add_column("OK", width=3)
        for i, (tn, args, rp, dur, suc, cr) in enumerate(reversed(history)):
            ic = f"[{C['success']}]✓[/{C['success']}]" if suc else f"[{C['error']}]✗[/{C['error']}]"
            table.add_row(str(len(history)-i), tn, (args or '')[:40], f"{dur:.1f}s" if dur else "-", ic)
        self.console.print(table)

    def show_help(self):
        table = Table(title=f"{MASCOT} Commands", show_header=True, border_style=C['border'], title_style=C['primary_br'])
        table.add_column("Command", style=C['accent'], width=22)
        table.add_column("Description", style=C['text'], width=50)
        for cmd, desc in [("/task <goal>", "Start autonomous task"), ("/plan <goal>", "Generate plan only"),
                          ("/search <query>", "Search the web"), ("/model [name]", "Show/switch model"),
                          ("/think <on|off|auto>", "Toggle thinking"), ("/trust <level>", "Change trust level"),
                          ("/layout <mode>", "Switch layout (stream|panel|compact)"),
                          ("/compact", "Force context compaction"), ("/checkpoint", "Save checkpoint"),
                          ("/rollback", "Rollback to checkpoint"), ("/notes [query]", "View/search notes"),
                          ("/files", "Show file tree"), ("/history", "Show tool call history"),
                          ("/sessions", "List sessions"), ("/export [md|json]", "Export session"),
                          ("/context", "Preview context window"), ("/config", "Show configuration"),
                          ("/doctor", "Run diagnostics"), ("/stats", "Session statistics"),
                          ("/help", "Show help"), ("/exit", "Exit Pincer")]:
            table.add_row(cmd, desc)
        self.console.print(table)

    def show_context_preview(self, messages, tokens):
        sections = []
        for m in messages[:15]:
            role = m.get('role', '?')
            content = m.get('content', '')[:80].replace('\n', ' ')
            has_tc = 'tool_calls' in m and m['tool_calls']
            icon = {'system': '\U0001f4cb', 'user': '\U0001f464', 'assistant': MASCOT, 'tool': '\U0001f527'}.get(role, '?')
            tc = " [+tools]" if has_tc else ""
            sections.append(f"  {icon} [{C['text_dim']}]{role}[/{C['text_dim']}] ({len(m.get('content',''))//4}t{tc}): {content}...")
        ratio = tokens / MAX_CONTEXT_TOKENS
        cc = C['success'] if ratio < 0.5 else C['warning'] if ratio < 0.75 else C['error']
        extra = f"\n  ... {len(messages)-15} more" if len(messages) > 15 else ""
        self.console.print(Panel("\n".join(sections) + extra,
            title=f"[{cc}]\U0001f4cb Context ({tokens:,}/{MAX_CONTEXT_TOKENS:,}t)[/{cc}]", border_style=C['border'], padding=(0,1)))

    def show_stats(self):
        el = int(time.time() - self.session_start)
        self.console.print(Panel(
            f"[{C['text_bright']}]Stats[/{C['text_bright']}]  "
            f"Duration: [{C['accent']}]{el//60}m{el%60}s[/{C['accent']}]  "
            f"LLM: [{C['accent']}]{self.llm_calls}[/{C['accent']}]  "
            f"Cmds: [{C['accent']}]{self.commands_run}[/{C['accent']}]  "
            f"Files: [{C['accent']}]{self.files_written}[/{C['accent']}]  "
            f"Errors: [{C['accent']}]{self.errors_count}[/{C['accent']}]  "
            f"Turns: [{C['accent']}]{self.turn_count}[/{C['accent']}]",
            border_style=C['border'], padding=(1,2)))


class ThinkParser:
    def __init__(self):
        self.state = 'outside'
        self.tag_buf = ''
        self.content_buf = ''
        self.thinking = ''
        self.response = ''

    def feed(self, text):
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
                    self.state = 'in_close'
                    self.tag_buf = '<'
                else:
                    self.content_buf += char
            elif self.state == 'in_close':
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

    def flush(self):
        results = []
        if self.content_buf:
            if self.state in ('inside', 'in_close'):
                results.append(('thinking', self.content_buf))
                self.thinking += self.content_buf
            else:
                results.append(('response', self.content_buf))
                self.response += self.content_buf
            self.content_buf = ''
        return results


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

    def run(self, user_input):
        self._interrupted = False
        self.ui.turn_count += 1
        self.db.add_message(self.conv_id, 'user', user_input)
        self.conversation_messages.append({'role': 'user', 'content': user_input})
        final_response = ""
        turn_start = time.time()

        for turn in range(MAX_TURNS):
            if self._interrupted:
                final_response = "[Interrupted]"
                break
            if time.time() - turn_start > AGENT_LOOP_TIMEOUT:
                self.ui.show_warning(f"Timeout after {AGENT_LOOP_TIMEOUT}s")
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
                    msg = getattr(chunk, 'message', None) or (chunk.get('message', {}) if isinstance(chunk, dict) else None)
                    if msg is None:
                        continue
                    content = getattr(msg, 'content', None) or (msg.get('content', '') if isinstance(msg, dict) else '') or ''
                    tcs = []
                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        tcs = list(msg.tool_calls)
                    elif isinstance(msg, dict) and msg.get('tool_calls'):
                        tcs = list(msg['tool_calls'])
                    native_thinking = getattr(msg, 'thinking', '') or ''
                    if native_thinking:
                        if not thinking_displayed:
                            self.ui.show_activity('thinking')
                            thinking_displayed = True
                        thinking_text += native_thinking
                        sys.stdout.write(f"{ANSI['purple']}{ANSI['italic']}{native_thinking}{ANSI['reset']}")
                        sys.stdout.flush()
                    if content:
                        parts = parser.feed(content)
                        for ptype, ptext in parts:
                            if ptype == 'thinking':
                                if not thinking_displayed:
                                    self.ui.show_activity('thinking')
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

                for ptype, ptext in parser.flush():
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
                self.ui.console.print(f"\n[{C['warning']}]Interrupted[/{C['warning']}]")
                break
            except RuntimeError as e:
                self.ui.show_error(str(e))
                time.sleep(2)
                continue
            except Exception as e:
                self.ui.show_error(str(e))
                time.sleep(1)
                continue

            if thinking_text and not thinking_displayed:
                self.ui.show_thinking_block(thinking_text)

            if tool_calls_list:
                tc_ser = [{'type': 'function', 'function': {'name': tc['name'], 'arguments': tc['arguments']}} for tc in tool_calls_list]
                self.db.add_message(self.conv_id, 'assistant', content=response_text, thinking=thinking_text, tool_calls=json.dumps(tc_ser))
                self.conversation_messages.append({'role': 'assistant', 'content': response_text, 'tool_calls': tc_ser})

                for tc in tool_calls_list:
                    tool_name = tc['name']
                    tool_args = tc['arguments'] if isinstance(tc['arguments'], dict) else {}
                    self.ui.show_tool_call(tool_name, tool_args)
                    if not self.permissions.check(tool_name, tool_args):
                        self.ui.show_permission_denied(tool_name)
                        self.db.add_message(self.conv_id, 'tool', "Permission denied", tool_name=tool_name)
                        self.conversation_messages.append({'role': 'tool', 'name': tool_name, 'content': "Permission denied"})
                        continue

                    duration = 0.0
                    if tool_name == 'ask_user':
                        q = tool_args.get('question', '')
                        opts = tool_args.get('options', [])
                        self.ui.show_ask_user(q, opts)
                        try:
                            answer = input(f"{ANSI['cyan']}{MASCOT}❓ > {ANSI['reset']}").strip()
                        except (EOFError, KeyboardInterrupt):
                            answer = "(no response)"
                        result = answer
                        self.ui.console.print()
                    else:
                        start = time.time()
                        result = self.tools.execute(tool_name, tool_args)
                        duration = time.time() - start

                    self.ui.show_tool_result(tool_name, result, duration)
                    is_ok = not (result.startswith("Error:") or result.startswith("BLOCKED:"))
                    self.db.add_tool_history(self.conv_id, tool_name, json.dumps(tool_args, default=str)[:200], result[:200], duration, is_ok)
                    rfc = result if len(result) <= 3000 else result[:1500] + f"\n... (truncated {len(result)} chars) ...\n" + result[-1000:]
                    self.db.add_message(self.conv_id, 'tool', rfc, tool_name=tool_name)
                    self.conversation_messages.append({'role': 'tool', 'name': tool_name, 'content': rfc})
                continue

            self.db.add_message(self.conv_id, 'assistant', content=response_text, thinking=thinking_text)
            self.conversation_messages.append({'role': 'assistant', 'content': response_text})
            final_response = response_text
            break

        self.db.update_conversation(self.conv_id)
        if self.db.count_messages(self.conv_id) <= 3 and user_input:
            self.db.update_conversation(self.conv_id, title=user_input[:60])
        return final_response

    def interrupt(self):
        self._interrupted = True

    def get_context_tokens(self):
        return self.context.estimate_tokens(self.conversation_messages)


class AutonomousMode:
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

    def generate_plan(self, goal):
        self.ui.show_activity('planning')
        prompt = f"Create a step-by-step plan for: {goal}\n\nOutput ONLY a numbered list of actionable steps."
        messages = self.agent.context.assemble_context(self.agent.conversation_messages, user_input=prompt)
        try:
            r = self.agent.backend.chat(messages=messages, tools=None, think=self.agent.backend.should_think())
            content = r.message.content or ''
            thinking = getattr(r.message, 'thinking', '') or ''
            if thinking:
                self.ui.show_thinking_block(thinking)
            steps = []
            for line in content.splitlines():
                m = re.match(r'^\d+[\.\)]\s+(.+)', line.strip())
                if m:
                    steps.append(m.group(1).strip())
            return steps if steps else [content[:200]]
        except Exception as e:
            self.ui.show_error(f"Plan failed: {e}")
            return []

    def execute_plan(self, goal, plan, auto_approve=False):
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
            self.ui.console.print(f"  [{C['text_dim']}]\\[Enter] Start  \\[Q] Cancel[/{C['text_dim']}]")
            try:
                c = input(f"{ANSI['cyan']}{MASCOT} > {ANSI['reset']}").strip().lower()
                if c == 'q':
                    self.ui.show_info("Cancelled")
                    return
            except (EOFError, KeyboardInterrupt):
                self.ui.show_info("Cancelled")
                return
        for i, step in enumerate(plan):
            if self._cancelled:
                self.ui.show_warning("Cancelled")
                break
            self.current_step = i
            self.memory.set_working('current_step', i)
            self.ui.show_plan(plan, i)
            if time.time() - self.start_time > 120 * 60:
                self.ui.show_warning("Time limit exceeded")
                break
            if self.ui.errors_count > 10:
                self.ui.show_warning("Too many errors")
                break
            sp = f"Execute step {i+1}/{len(plan)}: {step}\nGoal: {goal}\nUse tools. Describe what you did."
            try:
                self.agent.run(sp)
            except KeyboardInterrupt:
                self._cancelled = True
                break
            except Exception as e:
                self.ui.show_error(f"Step {i+1} failed: {e}")
            self.stats['llm_calls'] = self.ui.llm_calls
            self.stats['commands'] = self.ui.commands_run
            self.stats['writes'] = self.ui.files_written
            self.stats['errors'] = self.ui.errors_count
            if (i + 1) % 5 == 0:
                self.db.save_checkpoint(self.agent.conv_id, i+1, f"Step {i+1}",
                    {'plan': plan, 'current_step': i, 'stats': self.stats,
                     'message_count': len(self.agent.conversation_messages),
                     'memory_working': self.memory.get_state()})
                self.ui.show_info(f"Checkpoint saved at step {i+1}")
        elapsed = time.time() - self.start_time
        self.ui.show_task_summary(self.plan, self.current_step + (1 if not self._cancelled else 0), elapsed, self.stats)

    def cancel(self):
        self._cancelled = True
        self.agent.interrupt()


class CommandHandler:
    def __init__(self, app):
        self.app = app

    def handle(self, user_input):
        s = user_input.strip()
        if not s.startswith('/'):
            return False
        parts = s.split(maxsplit=1)
        cmd, args = parts[0].lower(), parts[1] if len(parts) > 1 else ""
        handlers = {
            '/help': self._help, '/exit': self._exit, '/quit': self._exit,
            '/task': self._task, '/plan': self._plan, '/search': self._search,
            '/model': self._model, '/think': self._think, '/trust': self._trust,
            '/layout': self._layout, '/compact': self._compact,
            '/checkpoint': self._checkpoint, '/rollback': self._rollback,
            '/notes': self._notes, '/files': self._files, '/history': self._history,
            '/sessions': self._sessions, '/export': self._export,
            '/context': self._context, '/config': self._config,
            '/doctor': self._doctor, '/stats': self._stats,
            '/clear': self._clear, '/new': self._new,
        }
        h = handlers.get(cmd)
        if h:
            h(args)
            return True
        matches = [k for k in handlers if k.startswith(cmd)]
        if len(matches) == 1:
            handlers[matches[0]](args)
            return True
        self.app.ui.show_error(f"Unknown: {cmd}. /help for list")
        return True

    def _help(self, a):
        self.app.ui.show_help()
    def _exit(self, a):
        self.app.ui.console.print(f"\n[{C['primary_br']}]{MASCOT} See you! \U0001f44b[/{C['primary_br']}]")
        sys.exit(0)
    def _task(self, a):
        if not a.strip():
            self.app.ui.show_error("Usage: /task <goal>")
            return
        plan = self.app.autonomous.generate_plan(a.strip())
        if plan:
            self.app.autonomous.execute_plan(a.strip(), plan, self.app.config.trust_level in ('auto', 'dontAsk'))
    def _plan(self, a):
        if not a.strip():
            self.app.ui.show_error("Usage: /plan <goal>")
            return
        plan = self.app.autonomous.generate_plan(a.strip())
        if plan:
            self.app.ui.show_plan(plan)
    def _search(self, a):
        if not a.strip():
            self.app.ui.show_error("Usage: /search <query>")
            return
        self.app.ui.show_activity('searching')
        r = self.app.tool_executor._tool_web_search(a.strip())
        self.app.ui.console.print(Panel(f"[{C['text']}]{r}[/{C['text']}]", title=f"[{C['searching']}]🔍 Results[/{C['searching']}]", border_style=C['border'], padding=(0,1)))
    def _model(self, a):
        if not a.strip():
            models = self.app.backend.list_models()
            self.app.ui.console.print(f"\n[{C['accent']}]Models:[/{C['accent']}]")
            for m in models:
                cur = " ← current" if m == self.app.config.model else ""
                self.app.ui.console.print(f"  • {m}{cur}")
            self.app.ui.console.print()
        else:
            try:
                self.app.backend.set_model(a.strip())
                self.app.ui.show_success(f"Model: {a.strip()}")
            except Exception as e:
                self.app.ui.show_error(str(e))
    def _think(self, a):
        m = a.strip().lower()
        if m not in ('on', 'off', 'auto'):
            self.app.ui.show_info(f"Current: {self.app.config.thinking_mode}. Usage: /think <on|off|auto>")
            return
        self.app.config.set('thinking_mode', m)
        self.app.ui.show_success(f"Thinking: {m}")
    def _trust(self, a):
        l = a.strip().lower()
        if l not in TRUST_LEVELS:
            self.app.ui.show_info(f"Current: {self.app.config.trust_level}. Levels: {', '.join(TRUST_LEVELS)}")
            return
        self.app.config.set('trust_level', l)
        self.app.permissions.session_approvals.clear()
        self.app.ui.show_success(f"Trust: {l}")
    def _layout(self, a):
        m = a.strip().lower()
        if m not in LAYOUT_MODES:
            self.app.ui.show_info(f"Current: {self.app.config.layout_mode}. Modes: {', '.join(LAYOUT_MODES)}")
            return
        self.app.config.set('layout_mode', m)
        self.app.ui.show_success(f"Layout: {m}")
        if m == 'panel':
            self.app.ui.show_sidebar(self.app.memory.get_working('current_plan'), self.app.memory.get_working('current_step', 0))
    def _compact(self, a):
        self.app.ui.show_activity('compacting')
        self.app.agent.conversation_messages = self.app.context.compact_context(self.app.agent.conversation_messages)
        t = self.app.context.estimate_tokens(self.app.agent.conversation_messages)
        self.app.ui.show_success(f"Compacted: {t:,} tokens")
    def _checkpoint(self, a):
        desc = a.strip() or "Manual"
        self.app.db.save_checkpoint(self.app.agent.conv_id, 0, desc,
            {'messages': len(self.app.agent.conversation_messages), 'memory_working': self.app.memory.get_state()})
        self.app.ui.show_success(f"Checkpoint: {desc}")
    def _rollback(self, a):
        cp = self.app.db.get_latest_checkpoint(self.app.agent.conv_id)
        if not cp:
            self.app.ui.show_warning("No checkpoints")
            return
        step, desc, state_json, created = cp
        state = json.loads(state_json) if state_json else {}
        mc = state.get('message_count', 0)
        self.app.ui.show_info(f"Latest: {desc} ({mc} msgs)")
        try:
            c = input(f"{ANSI['amber']}Rollback? [y/N] {ANSI['reset']}").strip().lower()
            if c in ('y', 'yes') and mc > 0:
                raw = self.app.db.get_messages(self.app.agent.conv_id, limit=mc)
                self.app.agent.conversation_messages = []
                for role, content, thinking, tool_calls, tool_name in raw:
                    msg = {'role': role, 'content': content}
                    if thinking:
                        msg['thinking'] = thinking
                    if tool_calls:
                        try:
                            msg['tool_calls'] = json.loads(tool_calls)
                        except Exception:
                            pass
                    self.app.agent.conversation_messages.append(msg)
                self.app.memory.restore_state(state.get('memory_working', {}))
                self.app.ui.show_success(f"Rolled back ({len(self.app.agent.conversation_messages)} msgs)")
            else:
                self.app.ui.show_info("Cancelled")
        except (EOFError, KeyboardInterrupt):
            self.app.ui.show_info("Cancelled")
    def _notes(self, a):
        if a.strip():
            r = self.app.db.search_notes(a.strip(), 10)
            if r:
                t = Table(border_style=C['border'], title_style=C['thinking'])
                t.add_column("Cat", style=C['accent'], width=12)
                t.add_column("Note", style=C['text'], width=60)
                for cat, content in r:
                    t.add_row(cat, content[:100])
                self.app.ui.console.print(t)
            else:
                self.app.ui.show_info(f"No notes for '{a.strip()}'")
            return
        notes = self.app.db.get_notes(limit=15)
        if not notes:
            self.app.ui.show_info("No notes yet")
            return
        t = Table(border_style=C['border'], title_style=C['thinking'])
        t.add_column("Cat", style=C['accent'], width=12)
        t.add_column("Note", style=C['text'], width=60)
        for cat, content, _ in notes:
            t.add_row(cat, content[:100])
        self.app.ui.console.print(t)
    def _files(self, a):
        self.app.ui.show_file_tree(a.strip() or None)
    def _history(self, a):
        h = self.app.db.get_tool_history(self.app.agent.conv_id, 15)
        self.app.ui.show_tool_history(h)
    def _sessions(self, a):
        sessions = self.app.db.list_conversations()
        if not sessions:
            self.app.ui.show_info("No sessions")
            return
        for sid, title, cr, up in sessions:
            cnt = self.app.db.count_messages(sid)
            self.app.ui.console.print(f"  [{C['accent']}]{sid}[/{C['accent']}]  {title or '(untitled)'}  [{C['text_dim']}]{cnt} msgs[/{C['text_dim']}]")
        self.app.ui.console.print()
    def _export(self, a):
        fmt = a.strip().lower() or 'md'
        raw = self.app.db.get_messages(self.app.agent.conv_id)
        if not raw:
            self.app.ui.show_warning("Nothing to export")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ed = Path.cwd() / "pincer_exports"
        ed.mkdir(exist_ok=True)
        if fmt == 'json':
            fp = ed / f"pincer_{self.app.agent.conv_id}_{ts}.json"
            fp.write_text(json.dumps([{'role': r, 'content': c} for r, c, *_ in raw], indent=2))
        else:
            fp = ed / f"pincer_{self.app.agent.conv_id}_{ts}.md"
            lines = [f"# Pincer Export\n", f"Date: {datetime.now().isoformat()}\n"]
            for role, content, thinking, tool_calls, tool_name in raw:
                if role == 'user':
                    lines.append(f"\n## User\n\n{content}\n")
                elif role == 'assistant':
                    if thinking:
                        lines.append(f"\n## Thinking\n\n{thinking}\n")
                    lines.append(f"\n## Assistant\n\n{content}\n")
                elif role == 'tool':
                    lines.append(f"\n## Tool: {tool_name}\n\n```\n{content[:500]}\n```\n")
            fp.write_text('\n'.join(lines))
        self.app.ui.show_success(f"Exported: {fp}")
    def _context(self, a):
        msgs = self.app.context.assemble_context(self.app.agent.conversation_messages)
        t = self.app.context.estimate_tokens(msgs)
        self.app.ui.show_context_preview(msgs, t)
    def _config(self, a):
        t = Table(border_style=C['border'], title_style=C['accent'])
        t.add_column("Setting", style=C['accent'], width=20)
        t.add_column("Value", style=C['text'], width=40)
        for k, v in sorted(self.app.config.data.items()):
            t.add_row(k, str(v))
        self.app.ui.console.print(t)
    def _doctor(self, a):
        self.app.ui.console.print(f"\n[{C['accent']}]{MASCOT} Diagnostics...[/{C['accent']}]")
        checks = [("Python", sys.version.split()[0], True)]
        ok = self.app.backend.health_check()
        checks.append(("Ollama", "Running" if ok else "FAILED", ok))
        if ok:
            models = self.app.backend.list_models()
            mok = any(self.app.config.model in m for m in models)
            checks.append((f"Model {self.app.config.model}", "Available" if mok else "Missing", mok))
        checks.append(("Database", "OK", bool(self.app.db.conn)))
        checks.append(("Schema Tokens", str(self.app.backend.tool_schema_tokens), True))
        try:
            from playwright.sync_api import sync_playwright
            checks.append(("Playwright", "Installed", True))
        except ImportError:
            checks.append(("Playwright", "Optional", True))
        t = Table(border_style=C['border'])
        t.add_column("Check", style=C['accent'], width=20)
        t.add_column("Status", style=C['text'], width=20)
        t.add_column("OK", width=3)
        for n, s, o in checks:
            ic = f"[{C['success']}]✓[/{C['success']}]" if o else f"[{C['error']}]✗[/{C['error']}]"
            t.add_row(n, s, ic)
        self.app.ui.console.print(t)
        self.app.ui.console.print()
    def _stats(self, a):
        self.app.ui.show_stats()
    def _clear(self, a):
        os.system('clear' if os.name != 'nt' else 'cls')
        self.app.ui.show_banner()
    def _new(self, a):
        self.app.agent.new_conversation(a.strip())
        self.app.ui.show_success(f"New session: {self.app.agent.conv_id}")


class Onboarding:
    def __init__(self, config, ui, backend):
        self.config = config
        self.ui = ui
        self.backend = backend

    def is_first_run(self):
        return self.config.get('first_run', True)

    def _input(self, prompt, default=""):
        sys.stdout.flush()
        try:
            r = input(prompt).strip()
            return r if r else default
        except (EOFError, KeyboardInterrupt):
            return default

    def run(self):
        self.ui.console.clear()
        self.ui.console.print()
        self.ui.console.print(Panel(
            f"[{C['primary_br']}]{MASCOT}  P I N C E R  v{VERSION}[/{C['primary_br']}]\n"
            f"[{C['text']}]Autonomous Coding Agent[/{C['text']}]\n"
            f"[{C['text_dim']}]Blue Lobster Edition {MASCOT}[/{C['text_dim']}]",
            border_style=C['primary'], padding=(2, 6)))
        self.ui.console.print()
        self._input(f"  {ANSI['cyan']}Press Enter to start...{ANSI['reset']}")

        self.ui.console.print(f"\n[{C['accent']}]Step 1/3: Environment[/{C['accent']}]")
        ok = self.backend.health_check()
        self.ui.console.print(f"  [{'✓' if ok else '✗'}] Ollama: {'Running' if ok else 'Not running!'}")
        if ok:
            models = self.backend.list_models()
            mok = any(self.config.model in m for m in models)
            self.ui.console.print(f"  [{'✓' if mok else '✗'}] Model {self.config.model}: {'Available' if mok else 'Missing'}")
            if not mok:
                self.ui.console.print(f"  [{C['warning']}]Pulling {self.config.model}...[/{C['warning']}]")
                try:
                    self.backend.client.pull(self.config.model)
                    self.ui.console.print(f"  [{C['success']}]✓ Pulled![/{C['success']}]")
                except Exception:
                    self.ui.console.print(f"  [{C['error']}]Failed. Run: ollama pull {self.config.model}[/{C['error']}]")

        self.ui.console.print(f"\n[{C['accent']}]Step 2/3: Setup[/{C['accent']}]")
        name = self._input(f"  Name [{self.config.user_name}]: ", self.config.user_name)
        if name:
            self.config.set('user_name', name)
            self.ui.user_name = name
        self.ui.console.print("  Language: [1]Python [2]JS [3]Rust [4]Go [5]Other")
        lc = self._input("  Choice [1]: ", "1")
        self.config.set('language', {'1': 'python', '2': 'javascript', '3': 'rust', '4': 'go', '5': 'other'}.get(lc, 'python'))

        self.ui.console.print(f"\n[{C['accent']}]Step 3/3: Trust[/{C['accent']}]")
        self.ui.console.print("  [1]🔒 plan  [2]🔒 default  [3]🔓 acceptEdits  [4]🔓 auto  [5]🔓 dontAsk")
        tc = self._input("  Choice [2]: ", "2")
        self.config.set('trust_level', {'1': 'plan', '2': 'default', '3': 'acceptEdits', '4': 'auto', '5': 'dontAsk'}.get(tc, 'default'))

        self.ui.console.print(f"\n[{C['success']}]✓ Ready!{MASCOT}[/{C['success']}]")
        self.ui.console.print(f"  [{C['text_dim']}]/help for commands • /task for autonomous mode • /layout panel for sidebar[/{C['text_dim']}]")
        self.config.set('first_run', False)
        self.config.save()
        self._input(f"\n  {ANSI['cyan']}Press Enter to start Pincer...{ANSI['reset']}")


class PincerApp:
    def __init__(self):
        self.config = PincerConfig()
        self.db = PincerDB()
        self.backend = OllamaBackend(self.config)
        self.ui = UIRenderer(self.config)
        self.web_cache = WebCache()
        self.memory = MemorySystem(self.db, self.config)
        self.tool_executor = ToolExecutor(self.config, self.db, self.web_cache)
        self.permissions = PermissionSystem(self.config, self.ui.console)
        self.context = ContextManager(self.config, self.memory, self.backend)
        self.agent = AgentLoop(self.config, self.db, self.backend, self.tool_executor, self.permissions, self.context, self.memory, self.ui)
        self.autonomous = AutonomousMode(self.agent, self.ui, self.db, self.config, self.memory)
        self.commands = CommandHandler(self)
        self.onboarding = Onboarding(self.config, self.ui, self.backend)
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        cmds = ['/help', '/exit', '/quit', '/task', '/plan', '/search', '/model', '/think', '/trust',
                '/layout', '/compact', '/checkpoint', '/rollback', '/notes', '/files', '/history',
                '/sessions', '/export', '/context', '/config', '/doctor', '/stats', '/clear', '/new']
        self.prompt_session = PromptSession(
            history=FileHistory(str(HISTORY_PATH)),
            completer=FuzzyWordCompleter(cmds, ignore_case=True),
            multiline=False)
        signal.signal(signal.SIGINT, self._sigint)

    def _sigint(self, sig, frame):
        self.agent.interrupt()
        self.autonomous.cancel()

    def _build_prompt(self):
        safe = self.config.user_name.replace('<', '&lt;').replace('>', '&gt;')
        return FormattedText([
            (f'fg:{C["primary_br"]}', f'{MASCOT} '),
            (f'fg:{C["text_dim"]}', f'{safe}'),
            (f'fg:{C["accent"]}', ' > ')])

    def _post_turn(self):
        if self.config.layout_mode == 'panel':
            self.ui.show_sidebar(self.memory.get_working('current_plan'), self.memory.get_working('current_step', 0))

    def run(self):
        if self.onboarding.is_first_run():
            self.onboarding.run()
        os.system('clear' if os.name != 'nt' else 'cls')
        self.ui.show_banner()
        self.ui.show_welcome()
        self.agent.new_conversation()
        self.ui.show_status_bar('idle', self.agent.get_context_tokens())
        self.ui.console.print(
            f"[{C['text_dim']}]Model: {self.config.model} | Trust: {self.config.trust_level} | "
            f"Think: {self.config.thinking_mode} | Layout: {self.config.layout_mode}[/{C['text_dim']}]")
        self.ui.console.print()

        while True:
            try:
                user_input = self.prompt_session.prompt(self._build_prompt())
                if not user_input.strip():
                    continue
                if self.commands.handle(user_input):
                    continue
                self.ui.show_status_bar('thinking', self.agent.get_context_tokens())
                result = self.agent.run(user_input.strip())
                self.ui.show_status_bar('idle' if result else 'error', self.agent.get_context_tokens())
                self._post_turn()
            except KeyboardInterrupt:
                self.ui.console.print(f"\n[{C['text_dim']}]Use /exit to quit[/{C['text_dim']}]")
                continue
            except EOFError:
                self.ui.console.print(f"\n[{C['primary_br']}]{MASCOT} Bye! \U0001f44b[/{C['primary_br']}]")
                break
            except Exception as e:
                self.ui.show_error(f"Error: {e}")
                continue
        self.db.close()


def main():
    PINCER_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        client = ollama.Client(host='http://localhost:11434')
        client.list()
    except Exception:
        print(f"\n{ANSI['red']}{MASCOT} Cannot connect to Ollama!{ANSI['reset']}")
        print(f"{ANSI['amber']}  Run: ollama serve{ANSI['reset']}")
        print(f"{ANSI['dim_blue']}  Install: https://ollama.com{ANSI['reset']}\n")
        sys.exit(1)
    app = PincerApp()
    app.run()


if __name__ == "__main__":
    main()
