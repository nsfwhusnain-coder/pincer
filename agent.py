#!/usr/bin/env python3
"""Pincer — professional local AI coding assistant.

Professional terminal UI with blue theme, inspired by Claude Code.
Phase 1: REPL, chat, context manager.
Phase 2: File tools, sandboxed shell, permissions.
Phase 3: Autonomous worker, planning, self-notes, checkpoints.
Phase 4: PII scrubbing, file watching, voice, sessions, 50 feature suite.
"""

import os
import re
import sys
import sqlite3
import shutil
import signal
import argparse
import asyncio
import subprocess
import tempfile
import json
import time
import difflib
import threading
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.completion import Completer, Completion
from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn,
)
from rich.columns import Columns
import questionary
import ollama

# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

PINCER_DIR = Path.home() / ".pincer"
DB_PATH = PINCER_DIR / "pincer.db"
HISTORY_PATH = PINCER_DIR / "history"
SESSIONS_DIR = PINCER_DIR / "sessions"
PLAN_FILE = PINCER_DIR / "plan.md"
POLICY_FILE = PINCER_DIR / "policy.json"

MAX_TOKENS = 12000
COMPACT_THRESHOLD = 10000
CHARS_PER_TOKEN = 4
DEFAULT_MODEL = "qwen3:8b"
EMBEDDING_MODEL = "nomic-embed-text"
EMBEDDING_DIM = 768
MAX_TOOL_TOKENS = 1500
MAX_SHELL_PER_TASK = 50
MAX_RM_PER_TASK = 3
MAX_WRITES_PER_TASK = 20
NO_PROGRESS_TIMEOUT = 1200
MAX_TASK_TIME = 7200
HEARTBEAT_INTERVAL = 300
FILE_WATCH_INTERVAL = 3.0

BANNER = "[bold blue]⬡ Pincer[/bold blue] [dim]v4.0 — local AI assistant[/dim]"
BLUE = "blue"
DIM_BLUE = "dim blue"
BRIGHT_BLUE = "bright_blue"

THINK_TAG_OPEN = "<think"
THINK_TAG_CLOSE = "</think"

SAFE_CMDS = [
    "git", "ls", "pwd", "mkdir", "cat", "head", "tail",
    "python", "python3", "pytest", "node", "npm install",
    "pip install", "cargo", "make", "echo", "wc", "find",
    "grep", "which", "tree", "diff", "sort", "uniq", "tee", "rg", "ag",
]
ASK_CMDS = [
    "rm", "mv", "cp", "chmod", "chown", "sudo", "curl", "wget",
    "eval", "exec", "source", "bash", "sh", "zsh", "pip", "npm",
    "brew", "docker", "kill", "pkill",
]
DENY_PATTERNS = [
    r"^rm\s+-[a-zA-Z]*f[a-zA-Z]*\s+/$",
    r"^rm\s+-[a-zA-Z]*f[a-zA-Z]*\s+/\*",
    r"^sudo\s+rm",
    r"^sudo\s+-\w*\s+rm",
    r"^mkfs",
    r"^dd\s+if=",
    r"curl\s+.*\|\s*(ba)?sh",
    r"wget\s+.*\|\s*(ba)?sh",
    r":\(\)\{.*;\}\s*;",
    r"^chmod\s+-R\s+777\s+/",
    r"^chmod\s+777\s+/",
]
FILE_READ_KW = {"read", "show", "cat", "open", "display", "view", "inspect", "explain"}
FILE_WRITE_KW = {"write", "create", "save", "new file", "add file"}
FILE_EDIT_KW = {
    "edit", "fix", "refactor", "change", "update", "modify", "patch", "rename",
}
SHELL_KW = {
    "run", "execute", "test", "build", "install", "git", "ls", "mkdir",
    "pip", "npm", "cargo", "make", "delete", "remove", "search", "find",
}
FILE_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java",
    ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt",
    ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".cfg",
    ".ini", ".sh", ".bash", ".zsh", ".fish", ".sql", ".html",
    ".css", ".scss", ".vue", ".svelte", ".gitignore", ".env",
    ".csv", ".xml", ".lock",
}
MEMORY_FILES = ["CLAUDE.md", "AGENTS.md", "PINCER.md", ".pincer.md"]
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".sqlite", ".db", ".parquet",
}
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "dist",
    "build", ".next", ".nuxt", "target", ".tox", ".mypy_cache", ".pytest_cache",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  PII SCRUBBER (#42)
# ═══════════════════════════════════════════════════════════════════════════════

PII_PATTERNS = [
    (re.compile(r'AKIA[0-9A-Z]{16}'), '[AWS_KEY]'),
    (re.compile(r'ghp_[0-9a-zA-Z]{36}'), '[GITHUB_TOKEN]'),
    (re.compile(r'gho_[0-9a-zA-Z]{36}'), '[GITHUB_OAUTH]'),
    (re.compile(r'ghs_[0-9a-zA-Z]{36}'), '[GITHUB_SAML]'),
    (re.compile(r'sk-[a-zA-Z0-9]{48}'), '[OPENAI_KEY]'),
    (re.compile(r'eyJ[a-zA-Z0-9._-]+'), '[JWT]'),
    (re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----'), '[PRIVATE_KEY]'),
    (re.compile(
        r'(?:password|passwd|secret|token|api_key|apikey)'
        r'\s*[:=]\s*["\']?[^\s"\']{8,}', re.I
    ), '[CREDENTIAL]'),
    (re.compile(
        r'(?:MONGO|DATABASE|DB)_URL\s*[:=]\s*["\']?[^\s"\']{10,}', re.I
    ), '[DB_URL]'),
]


def scrub_pii(text: str) -> str:
    """#42: Strip PII/secrets from text before sending to LLM."""
    for pattern, replacement in PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ═══════════════════════════════════════════════════════════════════════════════
#  LAZY FILE COMPLETER (#8)
# ═══════════════════════════════════════════════════════════════════════════════

class LazyFileCompleter(Completer):
    """Scans directory on first Tab, caches 30 s, skips binary dirs."""

    def __init__(self) -> None:
        self._paths: Optional[List[str]] = None
        self._t: float = 0.0

    def _scan(self) -> List[str]:
        ps: List[str] = []
        try:
            for r, ds, fs in os.walk("."):
                ds[:] = [d for d in ds if d not in SKIP_DIRS and not d.startswith(".")]
                for f in fs:
                    fp = os.path.join(r, f)
                    if not any(fp.endswith(e) for e in BINARY_EXT):
                        ps.append(fp)
                    if len(ps) >= 300:
                        return ps
        except Exception:
            pass
        return ps

    def get_completions(self, document, complete_event):
        if self._paths is None or (time.time() - self._t > 30):
            self._paths = self._scan()
            self._t = time.time()
        w = document.get_word_before_cursor()
        if not w:
            return
        for p in self._paths:
            if w in p:
                yield Completion(p, start_position=-len(w))


# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolResult:
    success: bool
    output: str
    tool_name: str
    command: str = ""
    error: str = ""

    @property
    def display(self) -> str:
        return self.output if self.success else (self.error or "Error")

    def truncated(self, max_chars: int = 6000) -> str:
        if len(self.output) <= max_chars:
            return self.output
        return self.output[:max_chars] + (
            f"\n... [{len(self.output) - max_chars} chars cut]"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  PERMISSION MANAGER + POLICY (#41)
# ═══════════════════════════════════════════════════════════════════════════════

class PermissionManager:
    def __init__(self, allowed: Optional[List[str]] = None):
        self._always: List[str] = allowed or []
        self._policy: Dict[str, List[str]] = {}

    def load_policy(self, path: Path) -> None:
        if path.exists():
            try:
                self._policy = json.loads(path.read_text())
            except Exception:
                pass

    def check(self, cmd: str, cwd: str) -> str:
        s = cmd.strip()
        for p in DENY_PATTERNS:
            if re.search(p, s, re.I):
                return "deny"
        for rule in self._policy.get("deny", []):
            if re.search(rule, s):
                return "deny"
        for p in self._always:
            if s == p or s.startswith(p + " "):
                return "allow"
        for p in SAFE_CMDS:
            if s == p or s.startswith(p + " "):
                if any(x.startswith("/") and not x.startswith(cwd) for x in s.split()):
                    return "ask"
                return "allow"
        for p in ASK_CMDS:
            if s == p or s.startswith(p + " "):
                return "ask"
        return "ask"

    def add_allowed(self, cmd: str) -> None:
        p = " ".join(cmd.split()[:2])
        if p not in self._always:
            self._always.append(p)


# ═══════════════════════════════════════════════════════════════════════════════
#  FILE TOOLS (#19 indentation-agnostic, #31 verification)
# ═══════════════════════════════════════════════════════════════════════════════

class FileTools:
    @staticmethod
    def read_file(path: str) -> ToolResult:
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ToolResult(False, "", "read_file", path, f"Not found: {p}")
            if p.is_dir():
                return ToolResult(False, "", "read_file", path, f"Directory: {p}")
            if p.stat().st_size > 500_000:
                return ToolResult(
                    False, "", "read_file", path,
                    f"Too large ({p.stat().st_size // 1024}KB)",
                )
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            numbered = "".join(
                f"  {i+1:>4} │ {l}" for i, l in enumerate(content.splitlines(True))
            )
            return ToolResult(True, numbered, "read_file", path)
        except Exception as e:
            return ToolResult(False, "", "read_file", path, str(e))

    @staticmethod
    def write_file(path: str, content: str) -> ToolResult:
        try:
            p = Path(path).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            lc = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return ToolResult(True, f"✓ Wrote {lc} lines → {p}", "write_file", path)
        except Exception as e:
            return ToolResult(False, "", "write_file", path, str(e))

    @staticmethod
    def edit_file(path: str, old: str, new: str) -> ToolResult:
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ToolResult(False, "", "edit_file", path, f"Not found: {p}")
            with open(p, "r", encoding="utf-8") as f:
                c = f.read()
            cnt = c.count(old)
            if cnt == 0:
                # #19: Indentation-agnostic fallback
                stripped = old.strip()
                for i, line in enumerate(c.splitlines()):
                    if stripped in line.strip():
                        return ToolResult(
                            False, "", "edit_file", path,
                            f"Exact not found. Fuzzy match line {i+1}: '{line.strip()[:60]}'",
                        )
                return ToolResult(False, "", "edit_file", path, "Not found")
            if cnt > 1:
                return ToolResult(
                    False, "", "edit_file", path,
                    f"Appears {cnt}x — add context",
                )
            c = c.replace(old, new, 1)
            with open(p, "w", encoding="utf-8") as f:
                f.write(c)
            # #31: Verify write
            with open(p, "r", encoding="utf-8") as f:
                if new not in f.read():
                    return ToolResult(False, "", "edit_file", path, "Verify failed")
            return ToolResult(True, f"✓ Replaced in {p}", "edit_file", path)
        except Exception as e:
            return ToolResult(False, "", "edit_file", path, str(e))

    @staticmethod
    def edit_file_diff(path: str, search: str, replace: str) -> ToolResult:
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ToolResult(False, "", "edit_file", path, f"Not found: {p}")
            with open(p, "r", encoding="utf-8") as f:
                c = f.read()
            if search not in c:
                # #19: Whitespace-agnostic
                ss = search.strip()
                lines = c.splitlines()
                for i, line in enumerate(lines):
                    if ss in line.strip():
                        indent = len(line) - len(line.lstrip())
                        lines[i] = " " * indent + replace.strip() + "\n"
                        with open(p, "w", encoding="utf-8") as f:
                            f.write("\n".join(lines))
                        return ToolResult(
                            True, f"✓ Fuzzy replaced line {i+1}", "edit_file", path,
                        )
                return ToolResult(
                    False, "", "edit_file", path, "SEARCH not found",
                )
            if c.count(search) > 1:
                return ToolResult(
                    False, "", "edit_file", path, f"Appears {c.count(search)}x",
                )
            c = c.replace(search, replace, 1)
            with open(p, "w", encoding="utf-8") as f:
                f.write(c)
            return ToolResult(True, f"✓ Replaced block in {p}", "edit_file", path)
        except Exception as e:
            return ToolResult(False, "", "edit_file", path, str(e))

    @staticmethod
    def compute_diff(old: str, new: str, path: str = "") -> str:
        return "".join(difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"{path}",
            tofile=f"{path}",
        ))


# ═══════════════════════════════════════════════════════════════════════════════
#  SHELL TOOL (#25 hardened sandbox, #27 test parsing)
# ═══════════════════════════════════════════════════════════════════════════════

class ShellTool:
    def __init__(self, console: Console):
        self.console = console
        self._sb = shutil.which("sandbox-exec") is not None

    @property
    def sandbox_available(self) -> bool:
        return self._sb

    @staticmethod
    def _profile(cwd: str, net: bool = False) -> str:
        h = str(Path.home())
        t = tempfile.gettempdir()
        nr = "(allow network*)" if net else "(deny network*)"
        return (
            f'(version 1)\n'
            f'(deny default)\n'
            f'(allow file-read* file-write* (subpath "{cwd}"))\n'
            f'(allow file-read* file-write* (subpath "{t}"))\n'
            f'(allow file-read* (subpath "{h}"))\n'
            f'(allow file-read* (subpath "/usr"))\n'
            f'(allow file-read* (subpath "/Library"))\n'
            f'(allow file-read* (subpath "/System"))\n'
            f'(allow file-read* (subpath "/opt"))\n'
            f'(allow process-exec (subpath "/usr/bin"))\n'
            f'(allow process-exec (subpath "/usr/local/bin"))\n'
            f'(allow process-exec (subpath "{h}/.local/bin"))\n'
            f'(allow process-exec (subpath "/opt/homebrew"))\n'
            f'(deny process-exec (literal "/usr/bin/sudo"))\n'
            f'(deny process-exec (literal "/usr/sbin/mkfs"))\n'
            f'{nr}\n'
        )

    def execute_command(
        self,
        cmd: str,
        cwd: str,
        timeout: int = 120,
        net: bool = False,
    ) -> ToolResult:
        try:
            if self._sb:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".sb", delete=False
                ) as f:
                    f.write(self._profile(cwd, net))
                    pp = f.name
                try:
                    r = subprocess.run(
                        ["sandbox-exec", "-f", pp, "bash", "-c", cmd],
                        capture_output=True, text=True,
                        cwd=cwd, timeout=timeout,
                    )
                finally:
                    try:
                        os.unlink(pp)
                    except OSError:
                        pass
            else:
                r = subprocess.run(
                    ["bash", "-c", cmd],
                    capture_output=True, text=True,
                    cwd=cwd, timeout=timeout,
                )
            out = r.stdout
            if r.stderr:
                out += ("\n--- stderr ---\n" + r.stderr) if out else r.stderr
            if r.returncode != 0:
                return ToolResult(
                    False, out.strip(), "execute_command", cmd,
                    f"Exit {r.returncode}",
                )
            return ToolResult(True, out.strip(), "execute_command", cmd)
        except subprocess.TimeoutExpired:
            return ToolResult(
                False, "", "execute_command", cmd, f"Timeout {timeout}s",
            )
        except Exception as e:
            return ToolResult(False, "", "execute_command", cmd, str(e))

    @staticmethod
    def parse_tests(output: str) -> Dict[str, int]:
        r: Dict[str, int] = {"passed": 0, "failed": 0, "errors": 0}
        m = re.search(r"(\d+) passed", output)
        if m:
            r["passed"] = int(m.group(1))
        m = re.search(r"(\d+) failed", output)
        if m:
            r["failed"] = int(m.group(1))
        m = re.search(r"(\d+) error", output)
        if m:
            r["errors"] = int(m.group(1))
        return r


# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

class ToolRouter:
    @dataclass
    class C:
        intent: str
        params: Dict[str, str] = field(default_factory=dict)

    def classify(self, text: str) -> "ToolRouter.C":
        lo = text.lower()
        ws = set(re.findall(r"\w+", lo))
        rh = len(ws & FILE_READ_KW)
        wh = sum(1 for k in FILE_WRITE_KW if k in lo)
        eh = len(ws & FILE_EDIT_KW)
        sh = len(ws & SHELL_KW)
        hp = self._p(text) is not None
        hc = self._c(text) is not None

        if sh > 0 and hc:
            return self.C("shell", {"command": self._c(text) or ""})
        if eh > 0 and hp:
            return self.C("file_edit", {"path": self._p(text) or ""})
        if wh > 0 and hp:
            p = self._p(text) or ""
            return self.C(
                "file_write",
                {"path": p, "description": self._wd(text, p)},
            )
        if rh > 0 and hp:
            return self.C("file_read", {"path": self._p(text) or ""})
        if sh > 0:
            c = self._c(text) or ""
            if c:
                return self.C("shell", {"command": c})
        return self.C("chat")

    @staticmethod
    def _p(t: str) -> Optional[str]:
        m = re.search(r'["\']([^"\']+)["\']', t)
        if m:
            return m.group(1).strip()
        for w in re.findall(r"[\w./\-]+", t):
            if Path(w).suffix.lower() in FILE_EXT:
                return w
        m = re.search(r"(?:file|in|to|at)\s+([^\s,;.!?]+)", t, re.I)
        if m:
            c = m.group(1).strip("\"'")
            if c and c.lower() not in {"a", "the", "this", "that", "it"}:
                return c
        return None

    @staticmethod
    def _c(t: str) -> Optional[str]:
        lo = t.lower()
        for kw in ("run", "execute"):
            m = re.search(rf"\b{kw}\s+(.+)", lo)
            if m:
                return m.group(1).strip()
        if re.search(r"\brun\s+tests?\b", lo):
            return "pytest"
        for cp in (
            "git", "ls", "mkdir", "pip", "npm", "cargo", "make", "pytest",
            "python", "python3", "node", "docker", "brew", "curl", "wget",
            "rg", "grep",
        ):
            m = re.search(rf"\b({cp}\s+.+)", lo)
            if m:
                return m.group(1).strip()
            if re.search(rf"\b{cp}\b", lo):
                return cp
        m = re.search(r"\b(?:delete|remove)\s+(.+)", lo)
        if m:
            tgt = m.group(1).strip()
            return "rm -rf ." if tgt in ("everything", "all", "*") else f"rm -rf {tgt}"
        if re.search(r"\bbuild\b", lo):
            return "make"
        m = re.search(r"\binstall\s+(.+)", lo)
        if m:
            return f"pip install {m.group(1).strip()}"
        return None

    @staticmethod
    def _wd(t: str, p: str) -> str:
        d = t
        for rx in (
            r"^write\s+",
            r"^create\s+(?:a\s+)?(?:new\s+)?(?:file\s+)?",
            r"^save\s+",
            r"^add\s+(?:a\s+)?(?:new\s+)?(?:file\s+)?",
        ):
            d = re.sub(rx, "", d, flags=re.I).strip()
        if p:
            d = d.replace(p, "").strip()
        d = re.sub(r"\s+(to|in|at)\s*$", "", d, flags=re.I).strip()
        d = re.sub(r"\s+", " ", d).strip(" ,.:;!")
        return d or f"content for {p}"


# ═══════════════════════════════════════════════════════════════════════════════
#  FILE WATCHER (#39)
# ═══════════════════════════════════════════════════════════════════════════════

class FileWatcher:
    """Polls for file changes and notifies the agent."""

    def __init__(self) -> None:
        self._mtimes: Dict[str, float] = {}
        self._running = False
        self._changes: List[Tuple[str, str]] = []

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._scan()
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def stop(self) -> None:
        self._running = False

    def _scan(self) -> None:
        for root, dirs, files in os.walk("."):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                fp = os.path.join(root, f)
                try:
                    self._mtimes[fp] = os.path.getmtime(fp)
                except OSError:
                    pass

    def _loop(self) -> None:
        while self._running:
            time.sleep(FILE_WATCH_INTERVAL)
            for root, dirs, files in os.walk("."):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        mt = os.path.getmtime(fp)
                        if fp in self._mtimes and mt > self._mtimes[fp]:
                            self._changes.append((fp, "modified"))
                        self._mtimes[fp] = mt
                    except OSError:
                        pass

    def drain(self) -> List[Tuple[str, str]]:
        ch = self._changes[:]
        self._changes.clear()
        return ch


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTONOMOUS LOOP (#27 self-healing, #29 architect, #42 pair mode)
# ═══════════════════════════════════════════════════════════════════════════════

class AutonomousLoop:
    def __init__(self, app: "PincerApp") -> None:
        self.app = app
        self.task_id: Optional[int] = None
        self.plan: List[Dict] = []
        self.current_step: int = 0
        self.status: str = "idle"
        self.auto_approve: bool = False
        self.session_allowed: List[str] = []
        self.start_time: Optional[float] = None
        self.shell_count: int = 0
        self.rm_count: int = 0
        self.write_count: int = 0
        self.llm_calls: int = 0
        self.consec_fail: int = 0
        self.last_error: str = ""
        self.error_repeat: int = 0
        self.last_heartbeat: float = 0.0
        self.last_progress: float = 0.0
        self.architect_mode: bool = False   # #29
        self.pair_mode: bool = False         # #42

    # ── Task CRUD ─────────────────────────────────────────────────────────

    def create_task(self, goal: str) -> int:
        c = self.app.conn.execute(
            "INSERT INTO tasks (goal, status) VALUES (?, 'planning')", (goal,)
        )
        self.task_id = c.lastrowid
        self.app.conn.commit()
        self.start_time = time.time()
        self.last_heartbeat = time.time()
        self.last_progress = time.time()
        self.shell_count = 0
        self.rm_count = 0
        self.write_count = 0
        self.llm_calls = 0
        return self.task_id

    def _update(self) -> None:
        if not self.task_id:
            return
        el = int((time.time() - self.start_time) / 60) if self.start_time else 0
        try:
            self.app.conn.execute(
                "UPDATE tasks SET plan=?, current_step=?, total_steps=?, "
                "status=?, auto_approve=?, llm_calls=?, shell_count=?, "
                "write_count=?, rm_count=?, elapsed_minutes=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (
                    json.dumps(self.plan),
                    self.current_step,
                    len(self.plan),
                    self.status,
                    self.auto_approve,
                    self.llm_calls,
                    self.shell_count,
                    self.write_count,
                    self.rm_count,
                    el,
                    self.task_id,
                ),
            )
        except sqlite3.OperationalError:
            self.app.conn.execute(
                "UPDATE tasks SET plan=?, current_step=?, total_steps=?, "
                "status=?, auto_approve=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?",
                (
                    json.dumps(self.plan),
                    self.current_step,
                    len(self.plan),
                    self.status,
                    self.auto_approve,
                    self.task_id,
                ),
            )
        self.app.conn.commit()

    def load_task(self, tid: int) -> bool:
        r = self.app.conn.execute(
            "SELECT goal, plan, current_step, total_steps, status, "
            "auto_approve FROM tasks WHERE id=?",
            (tid,),
        ).fetchone()
        if not r:
            return False
        self.task_id = tid
        self.plan = json.loads(r[1]) if r[1] else []
        self.current_step = r[2]
        self.status = r[4]
        self.auto_approve = bool(r[5])
        self.start_time = time.time()
        self.last_heartbeat = time.time()
        self.last_progress = time.time()
        return True

    # ── Plan ───────────────────────────────────────────────────────────────

    def generate_plan(self, goal: str, clar: str = "") -> List[Dict]:
        pr = (
            f"Create step-by-step plan for:\n{goal}\n"
            + (f"Context:\n{clar}\n" if clar else "")
            + "Output ONLY numbered list, one step per line."
        )
        try:
            resp = self._llm([{"role": "user", "content": pr}])
            steps = []
            for line in resp.strip().split("\n"):
                m = re.match(r"^\d+[\.\)]\s*(.+)", line.strip())
                if m:
                    steps.append({
                        "step": len(steps) + 1,
                        "description": m.group(1),
                        "status": "pending",
                    })
            if not steps:
                steps = [{"step": 1, "description": goal, "status": "pending"}]
            self.plan = steps
            self._update()
            return steps
        except Exception as e:
            self.app.console.print(
                f"  ❌ Plan failed: {e}", style="bold red"
            )
            return []

    def ask_clarifying(self, goal: str) -> str:
        if not sys.stdin.isatty():
            return ""
        try:
            resp = self._llm([{
                "role": "user",
                "content": (
                    f"Goal: {goal}\n"
                    "Ask 1-3 brief clarifying questions (Q: prefix). "
                    "NONE if clear."
                ),
            }])
            if "NONE" in resp.upper():
                return ""
            answers = []
            for line in resp.strip().split("\n"):
                q = re.sub(r"^Q:\s*", "", line.strip())
                if not q:
                    continue
                a = questionary.text(f"  {q}", default="").ask()
                if a:
                    answers.append(f"Q: {q} A: {a}")
            return "\n".join(answers)
        except Exception:
            return ""

    def _llm(self, msgs: List[Dict]) -> str:
        self.llm_calls += 1
        return self.app._run_llm_sync(msgs)

    # ── Main loop ───────────────────────────────────────────────────────────

    def run_loop(self) -> None:
        self.status = "active"
        self._update()

        mode = '🏗 Architect' if self.architect_mode else '⚡ Execute'
        self.app.console.print(Panel(
            f"[bold]🚀 Autonomous execution[/bold]\n"
            f"Steps: {len(self.plan)} | "
            f"Auto: {'🟢' if self.auto_approve else '🔴'} | "
            f"Mode: {mode}",
            border_style=BLUE,
            title="Autonomous",
        ))

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self.app.console,
            transient=True,
        ) as prog:
            task = prog.add_task("Working…", total=len(self.plan))

            while self.status == "active":
                g = self._guard()
                if g:
                    self.status = "paused"
                    self._update()
                    self.app.console.print(
                        f"\n  ⏸️ {g}", style="bold yellow"
                    )
                    return

                if time.time() - self.last_heartbeat > HEARTBEAT_INTERVAL:
                    el = int((time.time() - self.start_time) / 60) if self.start_time else 0
                    d = (
                        self.plan[self.current_step]['description'][:50]
                        if self.current_step < len(self.plan)
                        else "done"
                    )
                    self.app.console.print(
                        f"  ⏱️ Step {self.current_step+1}/{len(self.plan)}: {d}… | {el}m",
                        style="dim",
                    )
                    self.last_heartbeat = time.time()

                if self.current_step >= len(self.plan):
                    prog.update(task, completed=len(self.plan))
                    self._complete()
                    return

                step = self.plan[self.current_step]
                prog.update(
                    task,
                    completed=self.current_step,
                    description=f"Step {self.current_step+1}: {step['description'][:35]}",
                )
                self.app.console.print(
                    f"\n  📋 Step {self.current_step+1}/{len(self.plan)}: "
                    f"{step['description']}",
                    style="bold blue",
                )

                # #29: Architect mode — plan only
                if self.architect_mode:
                    self.app.console.print(
                        "  🏗 Architect mode — planning only, no code changes.",
                        style="dim blue",
                    )
                    self.app.write_note(
                        "observation",
                        f"Architect: planned step {step['description']}",
                    )
                    step["status"] = "done"
                    self.current_step += 1
                    self.last_progress = time.time()
                    self._update()
                    continue

                result = self._exec(step)

                if result and result.success:
                    step["status"] = "done"
                    self.current_step += 1
                    self.consec_fail = 0
                    self.last_progress = time.time()
                    self.app.write_note(
                        "success",
                        f"✓ {step['description']}: {result.output[:200]}",
                    )
                    self.app._auto_commit(
                        f"step {self.current_step}: {step['description'][:60]}"
                    )
                    if self.current_step % 5 == 0:
                        self.save_cp()
                    self.app.console.print("  ✓ Done", style="green")
                elif result:
                    self.consec_fail += 1
                    step["status"]
