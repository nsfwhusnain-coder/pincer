#!/usr/bin/env python3
"""Pincer — local AI coding assistant for your terminal.

Phase 1: REPL, chat, context manager, slash commands.
Phase 2: File tools, sandboxed shell, permission system, tool history.
Phase 3: Autonomous worker, planning, self-notes, checkpoints, guardrails.
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
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.completion import WordCompleter, Completer, Completion
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
import questionary
import ollama

try:
    from rich.columns import Columns
    _HAS_COLUMNS = True
except ImportError:
    _HAS_COLUMNS = False

# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

PINCER_DIR = Path.home() / ".pincer"
DB_PATH = PINCER_DIR / "pincer.db"
HISTORY_PATH = PINCER_DIR / "history"
MAX_TOKENS = 12000
COMPACT_THRESHOLD = 10000
CHARS_PER_TOKEN = 4
DEFAULT_MODEL = "qwen3:8b"
EMBEDDING_MODEL = "nomic-embed-text"
EMBEDDING_DIM = 768
PLAN_FILE = PINCER_DIR / "plan.md"

BANNER = r"""
╔══════════════════════════════════╗
║  🤖 PINCER — local AI assistant  ║
╚══════════════════════════════════╝
"""

THINK_TAG_OPEN = "<think"
THINK_TAG_CLOSE = "</think"

SAFE_COMMAND_PREFIXES = [
    "git", "ls", "pwd", "mkdir", "cat", "head", "tail",
    "python", "python3", "pytest", "py.test", "node",
    "npm install", "pip install", "cargo", "make",
    "echo", "wc", "find", "grep", "which", "tree",
    "diff", "sort", "uniq", "tee",
]
ASK_COMMAND_PREFIXES = [
    "rm", "mv", "cp", "chmod", "chown", "sudo",
    "curl", "wget", "eval", "exec", "source",
    "bash", "sh", "zsh", "pip", "npm", "brew",
    "docker", "kill", "pkill",
]
DENY_PATTERNS = [
    r"^rm\s+-[a-zA-Z]*f[a-zA-Z]*\s+/$",
    r"^rm\s+-[a-zA-Z]*f[a-zA-Z]*\s+/\*",
    r"^sudo\s+rm\s+-[a-zA-Z]*f",
    r"^mkfs", r"^dd\s+if=",
    r"curl\s+.*\|\s*(ba)?sh",
    r"^sudo\s+rm\s",
    r":\(\)\{.*;\}\s*;",
]
FILE_READ_KEYWORDS = {"read", "show", "cat", "open", "display", "view"}
FILE_WRITE_KEYWORDS = {"write", "create", "save", "new file", "add file"}
FILE_EDIT_KEYWORDS = {
    "edit", "fix", "refactor", "change", "update",
    "modify", "patch", "rename",
}
SHELL_KEYWORDS = {
    "run", "execute", "test", "build", "install",
    "git", "ls", "mkdir", "pip", "npm", "cargo", "make",
    "delete", "remove",
}
FILE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java",
    ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt",
    ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".cfg",
    ".ini", ".sh", ".bash", ".zsh", ".fish", ".sql", ".html",
    ".css", ".scss", ".vue", ".svelte", ".dockerfile",
    ".gitignore", ".env", ".csv", ".xml", ".lock",
}
MEMORY_FILES = ["CLAUDE.md", "AGENTS.md", "PINCER.md", ".pincer.md"]
MAX_SHELL_PER_TASK = 50
MAX_RM_PER_TASK = 3
MAX_WRITES_PER_TASK = 20
NO_PROGRESS_TIMEOUT = 1200
MAX_TASK_TIME = 7200
HEARTBEAT_INTERVAL = 300


# ═══════════════════════════════════════════════════════════════════════════════
#  LAZY FILE COMPLETER
# ═══════════════════════════════════════════════════════════════════════════════

class LazyFileCompleter(Completer):
    """Scans the directory tree on first use, then caches the result."""

    def __init__(self) -> None:
        self._cached_paths: Optional[List[str]] = None
        self._cache_time: float = 0.0
        self._cache_ttl: float = 30.0  # Refresh every 30s

    def _scan(self) -> List[str]:
        paths: List[str] = []
        try:
            for p in Path(".").rglob("*"):
                if p.is_file() and not any(
                    skip in str(p) for skip in ("node_modules", ".git", "__pycache__", ".venv", "venv")
                ):
                    paths.append(str(p))
                    if len(paths) >= 300:
                        break
        except Exception:
            pass
        return paths

    def get_completions(self, document, complete_event):
        if self._cached_paths is None or (time.time() - self._cache_time > self._cache_ttl):
            self._cached_paths = self._scan()
            self._cache_time = time.time()

        word = document.get_word_before_cursor()
        if not word:
            return
        for p in self._cached_paths:
            if word in p:
                yield Completion(p, start_position=-len(word))


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
        return self.output if self.success else (self.error or "Unknown error")


# ═══════════════════════════════════════════════════════════════════════════════
#  PERMISSION MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class PermissionManager:
    def __init__(self, allowed: Optional[List[str]] = None):
        self._always_allowed: List[str] = allowed or []

    def check(self, command: str, cwd: str) -> str:
        stripped = command.strip()
        for p in DENY_PATTERNS:
            if re.search(p, stripped, re.IGNORECASE):
                return "deny"
        for prefix in self._always_allowed:
            if stripped == prefix or stripped.startswith(prefix + " "):
                return "allow"
        for prefix in SAFE_COMMAND_PREFIXES:
            if stripped == prefix or stripped.startswith(prefix + " "):
                if self._outside_cwd(stripped, cwd):
                    return "ask"
                return "allow"
        for prefix in ASK_COMMAND_PREFIXES:
            if stripped == prefix or stripped.startswith(prefix + " "):
                return "ask"
        return "ask"

    @staticmethod
    def _outside_cwd(cmd: str, cwd: str) -> bool:
        for part in cmd.split():
            if part.startswith("/") and not part.startswith(cwd):
                return True
            if part.startswith(".."):
                return True
        return False

    def add_allowed(self, command: str) -> None:
        prefix = " ".join(command.split()[:2])
        if prefix not in self._always_allowed:
            self._always_allowed.append(prefix)


# ═══════════════════════════════════════════════════════════════════════════════
#  FILE TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

class FileTools:
    @staticmethod
    def read_file(path: str) -> ToolResult:
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ToolResult(False, "", "read_file", path, f"File not found: {p}")
            if p.is_dir():
                return ToolResult(False, "", "read_file", path, f"Is a directory: {p}")
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            numbered = "".join(f"  {i+1:>4} | {l}" for i, l in enumerate(lines))
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
            return ToolResult(True, f"✓ Wrote {lc} lines to {p}", "write_file", path)
        except Exception as e:
            return ToolResult(False, "", "write_file", path, str(e))

    @staticmethod
    def edit_file(path: str, old: str, new: str) -> ToolResult:
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ToolResult(False, "", "edit_file", path, f"File not found: {p}")
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()
            cnt = content.count(old)
            if cnt == 0:
                return ToolResult(False, "", "edit_file", path, "Old string not found")
            if cnt > 1:
                return ToolResult(False, "", "edit_file", path, f"Old string appears {cnt}x — must be unique")
            content = content.replace(old, new, 1)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            with open(p, "r", encoding="utf-8") as f:
                if new not in f.read():
                    return ToolResult(False, "", "edit_file", path, "Verification failed")
            return ToolResult(True, f"✓ Replaced 1 occurrence in {p}", "edit_file", path)
        except Exception as e:
            return ToolResult(False, "", "edit_file", path, str(e))

    @staticmethod
    def edit_file_diff(path: str, search: str, replace: str) -> ToolResult:
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ToolResult(False, "", "edit_file", path, f"File not found: {p}")
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()
            if search not in content:
                return ToolResult(False, "", "edit_file", path, "SEARCH block not found")
            cnt = content.count(search)
            if cnt > 1:
                return ToolResult(False, "", "edit_file", path, f"SEARCH block appears {cnt}x")
            content = content.replace(search, replace, 1)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            with open(p, "r", encoding="utf-8") as f:
                if replace not in f.read():
                    return ToolResult(False, "", "edit_file", path, "Verification failed")
            return ToolResult(True, f"✓ Replaced SEARCH block in {p}", "edit_file", path)
        except Exception as e:
            return ToolResult(False, "", "edit_file", path, str(e))


# ═══════════════════════════════════════════════════════════════════════════════
#  SHELL TOOL
# ═══════════════════════════════════════════════════════════════════════════════

class ShellTool:
    def __init__(self, console: Console):
        self.console = console
        self._sandbox = shutil.which("sandbox-exec") is not None

    @property
    def sandbox_available(self) -> bool:
        return self._sandbox

    @staticmethod
    def _profile(cwd: str, net: bool = False) -> str:
        h = str(Path.home()); t = tempfile.gettempdir()
        nr = "(allow network*)" if net else "(deny network*)"
        return (
            f"(version 1)\n(deny default)\n"
            f"(allow file-read* file-write* (subpath \"{cwd}\"))\n"
            f"(allow file-read* file-write* (subpath \"{t}\"))\n"
            f"(allow file-read* (subpath \"{h}\"))\n"
            f"(allow file-read* (subpath \"/usr\"))\n"
            f"(allow file-read* (subpath \"/Library\"))\n"
            f"(allow file-read* (subpath \"/System\"))\n"
            f"(allow file-read* (subpath \"/opt\"))\n"
            f"(allow process-exec)\n{nr}\n"
        )

    def execute_command(self, command: str, cwd: str, timeout: int = 120, allow_network: bool = False) -> ToolResult:
        try:
            if self._sandbox:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".sb", delete=False) as pf:
                    pf.write(self._profile(cwd, allow_network)); pp = pf.name
                try:
                    r = subprocess.run(
                        ["sandbox-exec", "-f", pp, "bash", "-c", command],
                        capture_output=True, text=True, cwd=cwd, timeout=timeout)
                finally:
                    try: os.unlink(pp)
                    except OSError: pass
            else:
                r = subprocess.run(["bash", "-c", command], capture_output=True, text=True, cwd=cwd, timeout=timeout)
            out = r.stdout
            if r.stderr: out += ("\n--- stderr ---\n" + r.stderr) if out else r.stderr
            if r.returncode != 0:
                return ToolResult(False, out.strip(), "execute_command", command, f"Exit code {r.returncode}")
            return ToolResult(True, out.strip(), "execute_command", command)
        except subprocess.TimeoutExpired:
            return ToolResult(False, "", "execute_command", command, f"Timeout after {timeout}s")
        except Exception as e:
            return ToolResult(False, "", "execute_command", command, str(e))


# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

class ToolRouter:
    @dataclass
    class Classification:
        intent: str
        params: Dict[str, str] = field(default_factory=dict)

    def classify(self, text: str) -> "ToolRouter.Classification":
        low = text.lower(); words = set(re.findall(r"\w+", low))
        rh = len(words & FILE_READ_KEYWORDS)
        wh = sum(1 for k in FILE_WRITE_KEYWORDS if k in low)
        eh = len(words & FILE_EDIT_KEYWORDS)
        sh = len(words & SHELL_KEYWORDS)
        hp = self._path(text) is not None; hc = self._cmd(text) is not None
        if sh > 0 and hc: return self.Classification("shell", {"command": self._cmd(text) or ""})
        if eh > 0 and hp: return self.Classification("file_edit", {"path": self._path(text) or ""})
        if wh > 0 and hp:
            p = self._path(text) or ""
            return self.Classification("file_write", {"path": p, "description": self._wdesc(text, p)})
        if rh > 0 and hp: return self.Classification("file_read", {"path": self._path(text) or ""})
        if sh > 0:
            c = self._cmd(text) or ""
            if c: return self.Classification("shell", {"command": c})
        return self.Classification("chat")

    @staticmethod
    def _path(text: str) -> Optional[str]:
        m = re.search(r'["\']([^"\']+)["\']', text)
        if m: return m.group(1).strip()
        for w in re.findall(r"[\w./\-]+", text):
            if Path(w).suffix.lower() in FILE_EXTENSIONS: return w
        m = re.search(r"(?:file|in|to|at)\s+([^\s,;.!?]+)", text, re.I)
        if m:
            c = m.group(1).strip("\"'")
            if c and c.lower() not in {"a","the","this","that","it","my","our"}: return c
        return None

    @staticmethod
    def _cmd(text: str) -> Optional[str]:
        low = text.lower()
        for kw in ("run", "execute"):
            m = re.search(rf"\b{kw}\s+(.+)", low)
            if m: return m.group(1).strip()
        if re.search(r"\brun\s+tests?\b", low) or re.search(r"\btest\s+it\b", low): return "pytest"
        for cp in ("git","ls","mkdir","pip","npm","cargo","make","pytest","python","python3","node","docker","brew","curl","wget"):
            m = re.search(rf"\b({cp}\s+.+)", low)
            if m: return m.group(1).strip()
            if re.search(rf"\b{cp}\b", low): return cp
        m = re.search(r"\b(?:delete|remove)\s+(.+)", low)
        if m:
            t = m.group(1).strip()
            return "rm -rf ." if t in ("everything","all","*") else f"rm -rf {t}"
        if re.search(r"\bbuild\b", low): return "make"
        m = re.search(r"\binstall\s+(.+)", low)
        if m: return f"pip install {m.group(1).strip()}"
        return None

    @staticmethod
    def _wdesc(text: str, path: str) -> str:
        d = text
        for p in (r"^write\s+",r"^create\s+(?:a\s+)?(?:new\s+)?(?:file\s+)?",r"^save\s+",r"^add\s+(?:a\s+)?(?:new\s+)?(?:file\s+)?"):
            d = re.sub(p, "", d, flags=re.I).strip()
        if path: d = d.replace(path, "").strip()
        d = re.sub(r"\s+(to|in|at)\s*$", "", d, flags=re.I).strip()
        d = re.sub(r"\s+", " ", d).strip(" ,.:;!")
        return d or f"content for {path}"


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTONOMOUS LOOP
# ═══════════════════════════════════════════════════════════════════════════════

class AutonomousLoop:
    """Manages autonomous task execution with planning, guardrails, and checkpoints."""

    def __init__(self, app: "PincerApp"):
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
        self.consecutive_failures: int = 0
        self.last_error: str = ""
        self.error_repeat: int = 0
        self.last_heartbeat: float = 0.0
        self.last_progress_time: float = 0.0

    # ── Task CRUD ─────────────────────────────────────────────────────────

    def create_task(self, goal: str) -> int:
        cur = self.app.conn.execute(
            "INSERT INTO tasks (goal, status) VALUES (?, 'planning')", (goal,)
        )
        self.task_id = cur.lastrowid
        self.app.conn.commit()
        self.start_time = time.time()
        self.last_heartbeat = time.time()
        self.last_progress_time = time.time()
        return self.task_id

    def _update_task(self) -> None:
        if not self.task_id: return
        elapsed = int((time.time() - self.start_time) / 60) if self.start_time else 0
        try:
            self.app.conn.execute(
                "UPDATE tasks SET plan=?, current_step=?, total_steps=?, status=?, "
                "auto_approve=?, llm_calls=?, shell_count=?, write_count=?, "
                "rm_count=?, elapsed_minutes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(self.plan), self.current_step, len(self.plan),
                 self.status, self.auto_approve, self.llm_calls,
                 self.shell_count, self.write_count, self.rm_count,
                 elapsed, self.task_id),
            )
            self.app.conn.commit()
        except sqlite3.OperationalError:
            # Fallback for DBs without new cost columns
            self.app.conn.execute(
                "UPDATE tasks SET plan=?, current_step=?, total_steps=?, status=?, "
                "auto_approve=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(self.plan), self.current_step, len(self.plan),
                 self.status, self.auto_approve, self.task_id),
            )
            self.app.conn.commit()

    def load_task(self, task_id: int) -> bool:
        row = self.app.conn.execute(
            "SELECT goal, plan, current_step, total_steps, status, auto_approve "
            "FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if not row: return False
        self.task_id = task_id
        self.plan = json.loads(row[1]) if row[1] else []
        self.current_step = row[2]
        self.status = row[4]
        self.auto_approve = bool(row[5])
        self.start_time = time.time()
        self.last_heartbeat = time.time()
        self.last_progress_time = time.time()
        return True

    # ── Plan generation ───────────────────────────────────────────────────

    def generate_plan(self, goal: str, clarifications: str = "") -> List[Dict]:
        prompt = (
            f"Create a detailed step-by-step plan for this goal:\n{goal}\n\n"
            + (f"Additional context:\n{clarifications}\n\n" if clarifications else "")
            + "Output ONLY a numbered list, one step per line. No extra text.\n"
            + "Example:\n1. Install dependencies\n2. Create routes\n3. Add tests"
        )
        try:
            resp = self._llm_call([{"role": "user", "content": prompt}])
            steps = []
            for line in resp.strip().split("\n"):
                line = line.strip()
                m = re.match(r"^\d+[\.\)]\s*(.+)", line)
                if m:
                    steps.append({"step": len(steps)+1, "description": m.group(1), "status": "pending"})
            if not steps:
                steps = [{"step": 1, "description": goal, "status": "pending"}]
            self.plan = steps
            self._update_task()
            return steps
        except Exception as e:
            self.app.console.print(f"  ❌ Plan generation failed: {e}", style="bold red")
            return []

    def ask_clarifying_questions(self, goal: str) -> str:
        if not sys.stdin.isatty():
            return ""
        prompt = (
            f"The user wants to: {goal}\n\n"
            f"Ask 1-3 brief clarifying questions.\n"
            f"Output ONLY questions, one per line, prefixed with 'Q: '.\n"
            f"If no clarification needed, output 'NONE'."
        )
        try:
            resp = self._llm_call([{"role": "user", "content": prompt}])
            if "NONE" in resp.upper(): return ""
            answers = []
            for line in resp.strip().split("\n"):
                q = re.sub(r"^Q:\s*", "", line.strip())
                if not q: continue
                a = questionary.text(f"  {q}", default="").ask()
                if a: answers.append(f"Q: {q} A: {a}")
            return "\n".join(answers)
        except Exception:
            return ""

    def ask_user_during_execution(self, question: str) -> str:
        """ask_user tool: ask the user a question during autonomous execution."""
        if not sys.stdin.isatty():
            return ""
        self.app.console.print(f"\n  🤔 {question}", style="bold yellow")
        answer = questionary.text("  Your answer:", default="").ask()
        self.app.write_note("observation", f"User asked: {question} → {answer}")
        return answer or ""

    # ── LLM helper ────────────────────────────────────────────────────────

    def _llm_call(self, messages: List[Dict]) -> str:
        self.llm_calls += 1
        resp = self.app._run_llm_sync(messages)
        return resp

    # ── Main loop ─────────────────────────────────────────────────────────

    def run_loop(self) -> None:
        self.status = "active"
        self._update_task()
        self.app.console.print(Panel(
            f"[bold]🚀 Starting autonomous execution[/bold]\n"
            f"Steps: {len(self.plan)} | Auto-approve: {'🟢' if self.auto_approve else '🔴'}",
            border_style="cyan", title="Autonomous Mode"))
        while self.status == "active":
            # Guardrails
            guard = self._check_guardrails()
            if guard:
                self.status = "paused"
                self._update_task()
                self.app.console.print(f"\n  ⏸️ {guard}", style="bold yellow")
                return
            # Heartbeat — FIX: was HEARTBEN_INTERVAL
            if time.time() - self.last_heartbeat > HEARTBEAT_INTERVAL:
                elapsed = int((time.time() - self.start_time) / 60) if self.start_time else 0
                desc = self.plan[self.current_step]['description'][:50] if self.current_step < len(self.plan) else "done"
                self.app.console.print(
                    f"  ⏱️ Step {self.current_step+1}/{len(self.plan)}: {desc}… | "
                    f"{elapsed}m elapsed | shells: {self.shell_count}", style="dim")
                self.last_heartbeat = time.time()
            # Check completion
            if self.current_step >= len(self.plan):
                self._complete(); return
            step = self.plan[self.current_step]
            self.app.console.print(
                f"\n  📋 Step {self.current_step+1}/{len(self.plan)}: {step['description']}",
                style="bold cyan")
            # Execute step
            result = self._execute_step(step)
            # Verify
            if result and result.success:
                step["status"] = "done"
                self.current_step += 1
                self.consecutive_failures = 0
                self.last_progress_time = time.time()
                self.app.write_note("success", f"✓ {step['description']}: {result.output[:200]}")
                self.app._auto_commit(f"step {self.current_step}: {step['description'][:60]}")
                if self.current_step % 5 == 0:
                    self.save_checkpoint()
                self.app.console.print("  ✓ Step done", style="green")
            elif result:
                self.consecutive_failures += 1
                step["status"] = "failed"
                self.app.write_note("error", f"✗ {step['description']}: {result.error[:200]}")
                if result.error == self.last_error:
                    self.error_repeat += 1
                else:
                    self.error_repeat = 1; self.last_error = result.error
                if self.error_repeat >= 3:
                    self.status = "stuck"
                    self._update_task()
                    self.app.console.print("\n  🔄 Loop detected: same error 3 times. Pausing.", style="bold red")
                    self.app.write_note("error", f"🔄 Loop detected: {result.error[:200]}")
                    return
                if self.consecutive_failures >= 3:
                    self.status = "stuck"
                    self._update_task()
                    self.app.console.print("\n  🔄 Stuck after 3 consecutive failures. Pausing.", style="bold red")
                    return
                self.app.console.print(f"  ⚠ Step failed, retrying… ({result.error[:100]})", style="yellow")
                retry = self._retry_step(step)
                if retry and retry.success:
                    step["status"] = "done"
                    self.current_step += 1
                    self.consecutive_failures = 0
                    self.last_progress_time = time.time()
                    self.app.write_note("success", f"✓ Retry succeeded: {step['description']}")
                else:
                    self.app.console.print("  ✗ Retry also failed.", style="red")
            # Self-evaluation every 10 steps
            if self.current_step > 0 and self.current_step % 10 == 0:
                self._self_evaluate()
            self._update_task()

    def _execute_step(self, step: Dict) -> Optional[ToolResult]:
        desc = step["description"]
        cls = self.app.router.classify(desc)
        cwd = os.getcwd()
        if cls.intent == "file_read":
            path = cls.params.get("path", "") or self._infer_path(desc)
            if not path: return ToolResult(False, "", "read_file", "", "No path inferred")
            result = self.app.file_tools.read_file(path)
            self.app.log_tool_history("read_file", path, result)
            return result
        elif cls.intent == "file_write":
            path = cls.params.get("path", "") or self._infer_path(desc)
            if not path: return ToolResult(False, "", "write_file", "", "No path inferred")
            self.write_count += 1
            content = self.app._generate_file_content(desc, path)
            if not content: return ToolResult(False, "", "write_file", path, "Content generation empty")
            result = self.app.file_tools.write_file(path, content)
            self.app.log_tool_history("write_file", path, result)
            return result
        elif cls.intent == "file_edit":
            path = cls.params.get("path", "") or self._infer_path(desc)
            if not path: return ToolResult(False, "", "edit_file", "", "No path inferred")
            self.write_count += 1
            read_r = self.app.file_tools.read_file(path)
            if not read_r.success: return read_r
            edit = self.app._generate_edit(desc, path, read_r.output)
            if not edit: return ToolResult(False, "", "edit_file", path, "Could not generate edit")
            result = self.app.file_tools.edit_file(path, edit[0], edit[1])
            if not result.success:
                result = self.app.file_tools.edit_file_diff(path, edit[0], edit[1])
            self.app.log_tool_history("edit_file", path, result)
            return result
        elif cls.intent == "shell":
            cmd = cls.params.get("command", "")
            if not cmd: return ToolResult(False, "", "execute_command", "", "No command inferred")
            self.shell_count += 1
            if cmd.strip().startswith("rm"): self.rm_count += 1
            risk = self.app.permission_manager.check(cmd, cwd)
            if risk == "deny":
                self.app.write_note("error", f"🚫 Command denied: {cmd}")
                return ToolResult(False, "", "execute_command", cmd, "Denied by safety policy")
            if risk == "ask" and not self.auto_approve:
                if cmd not in self.session_allowed:
                    approval = self._auto_approval_prompt(cmd)
                    if approval == "deny":
                        return ToolResult(False, "", "execute_command", cmd, "User denied")
                    if approval == "task":
                        self.session_allowed.append(cmd)
            net = self.app._command_needs_network(cmd)
            return self.app.shell_tool.execute_command(cmd, cwd, allow_network=net)
        else:
            resp = self._llm_call([
                {"role": "system", "content": self.app.get_system_prompt()},
                {"role": "user", "content": f"Working on step: {desc}\nProvide specific actions to take."},
            ])
            return ToolResult(True, resp[:500], "llm_guidance", desc)

    def _retry_step(self, step: Dict) -> Optional[ToolResult]:
        desc = step["description"]
        cls = self.app.router.classify(desc)
        if cls.intent == "file_edit":
            path = cls.params.get("path", "") or self._infer_path(desc)
            if not path: return None
            read_r = self.app.file_tools.read_file(path)
            if not read_r.success: return None
            edit = self.app._generate_edit(f"RETRY: {desc}", path, read_r.output)
            if not edit: return None
            result = self.app.file_tools.edit_file_diff(path, edit[0], edit[1])
            self.app.log_tool_history("edit_file_diff", path, result)
            return result
        return None

    def _infer_path(self, desc: str) -> str:
        path = self.app.router._path(desc)
        if path: return path
        for ext in (".py", ".js", ".ts", ".md"):
            m = re.search(rf"(\w+{ext})", desc)
            if m: return m.group(1)
        return ""

    def _auto_approval_prompt(self, cmd: str) -> str:
        if not sys.stdin.isatty():
            return "deny"
        self.app.console.print(Panel(
            f"[bold]Command:[/bold] {cmd}\n[bold]Step:[/bold] {self.current_step+1}/{len(self.plan)}",
            title="⚡ Autonomous Approval", border_style="yellow"))
        choice = questionary.select(
            "  Allow?", choices=["Allow once", "Allow for this task", "Deny"]).ask()
        if choice == "Allow once": return "allow"
        if choice == "Allow for this task": return "task"
        return "deny"

    # ── Guardrails — FIX: rm count off-by-one ─────────────────────────────

    def _check_guardrails(self) -> Optional[str]:
        if self.shell_count >= MAX_SHELL_PER_TASK:
            return f"Max shell commands reached ({MAX_SHELL_PER_TASK})"
        # FIX: was >= MAX_RM_PER_TASK + 1, now >= MAX_RM_PER_TASK
        if self.rm_count >= MAX_RM_PER_TASK:
            return f"Max rm commands reached ({MAX_RM_PER_TASK})"
        if self.write_count >= MAX_WRITES_PER_TASK:
            return f"Max file writes reached ({MAX_WRITES_PER_TASK})"
        if self.start_time and time.time() - self.start_time > MAX_TASK_TIME:
            return "Max task time exceeded (2 hours)"
        if time.time() - self.last_progress_time > NO_PROGRESS_TIMEOUT:
            return "No progress for 20 minutes"
        return None

    # ── Completion ────────────────────────────────────────────────────────

    def _complete(self) -> None:
        self.status = "completed"
        elapsed = int((time.time() - self.start_time) / 60) if self.start_time else 0
        self._update_task()
        self.app.console.print(Panel(
            f"✓ All {len(self.plan)} steps completed\n"
            f"Time: {elapsed}m | LLM calls: {self.llm_calls} | "
            f"Commands: {self.shell_count} | Writes: {self.write_count}",
            title="🎉 Task Complete", border_style="green"))
        self.save_checkpoint()
        self.app.write_note("success", f"Task completed in {elapsed}m with {len(self.plan)} steps")

    # ── Self-evaluation ───────────────────────────────────────────────────

    def _self_evaluate(self) -> None:
        done = sum(1 for s in self.plan if s["status"] == "done")
        failed = sum(1 for s in self.plan if s["status"] == "failed")
        prompt = (
            f"Task progress: {done}/{len(self.plan)} done, {failed} failed. "
            f"Current step: {self.plan[self.current_step]['description']}\n"
            f"Am I making progress? Should I adjust? Answer briefly."
        )
        try:
            resp = self._llm_call([{"role": "user", "content": prompt}])
            self.app.console.print(f"  💭 Self-eval: {resp[:200]}", style="dim italic")
        except Exception:
            pass

    # ── Checkpoints ───────────────────────────────────────────────────────

    def save_checkpoint(self) -> None:
        if not self.task_id: return
        conv = self.app.conn.execute(
            "SELECT role, content FROM conversation ORDER BY id ASC").fetchall()
        git_hash = ""
        try:
            r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
            if r.returncode == 0: git_hash = r.stdout.strip()
        except Exception: pass
        self.app.conn.execute(
            "INSERT INTO checkpoints (task_id, step_index, conversation_snapshot, "
            "working_directory, git_commit_hash) VALUES (?, ?, ?, ?, ?)",
            (self.task_id, self.current_step, json.dumps(conv), os.getcwd(), git_hash))
        self.app.conn.commit()

    # ── Pause / Resume / Abort ────────────────────────────────────────────

    def pause(self) -> None:
        self.status = "paused"
        self.save_checkpoint()
        self._update_task()
        self.app.console.print("  ⏸️ Task paused. Checkpoint saved.", style="yellow")

    def abort(self) -> None:
        self.status = "aborted"
        self._update_task()
        elapsed = int((time.time() - self.start_time) / 60) if self.start_time else 0
        done = sum(1 for s in self.plan if s["status"] == "done")
        self.app.console.print(
            f"  🛑 Task aborted. {done}/{len(self.plan)} steps done in {elapsed}m.", style="red")

    # ── Display helpers ───────────────────────────────────────────────────

    def display_plan(self) -> None:
        if not self.plan: return
        t = Table(title="📋 Task Plan", border_style="cyan", padding=(0, 1))
        t.add_column("Status", width=3); t.add_column("Step", width=4); t.add_column("Description")
        for s in self.plan:
            icon = "✅" if s["status"] == "done" else ("❌" if s["status"] == "failed" else "⬜")
            marker = "▸ " if s["step"] - 1 == self.current_step else ""
            t.add_row(icon, str(s["step"]), f"{marker}{s['description']}")
        self.app.console.print(t)

    def progress_text(self) -> str:
        if self.status == "idle" or not self.plan: return "idle"
        return f"step {self.current_step+1}/{len(self.plan)}"

    def cost_report(self) -> str:
        elapsed = int((time.time() - self.start_time) / 60) if self.start_time else 0
        return (f"LLM calls: {self.llm_calls} | Commands: {self.shell_count} | "
                f"Writes: {self.write_count} | Time: {elapsed}m")

    def health_score(self) -> float:
        if not self.plan: return 1.0
        done = sum(1 for s in self.plan if s["status"] == "done")
        failed = sum(1 for s in self.plan if s["status"] == "failed")
        total = len(self.plan)
        if total == 0: return 1.0
        return max(0.0, (done - failed * 2) / total)


# ═══════════════════════════════════════════════════════════════════════════════
#  PINCER APP
# ═══════════════════════════════════════════════════════════════════════════════

class PincerApp:
    def __init__(self) -> None:
        self.console = Console()
        self.error_console = Console(stderr=True, style="bold red")
        self.conn: Optional[sqlite3.Connection] = None
        self.model: str = DEFAULT_MODEL
        self.thinking_mode: bool = False
        self.user_name: str = ""
        self.preferred_language: str = "python"
        self.session: Optional[PromptSession] = None
        self._generating: bool = False
        self._vec_available: bool = False
        self._embedding_available: bool = False
        self._project_memory: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None  # Cached event loop
        # Phase 2
        self.router = ToolRouter()
        self.file_tools = FileTools()
        self.shell_tool = ShellTool(self.console)
        self.permission_manager = PermissionManager()
        # Phase 3
        self.loop = AutonomousLoop(self)

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create the cached asyncio event loop."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run_llm_sync(self, messages: List[Dict]) -> str:
        """Run an LLM call synchronously using the cached event loop."""
        loop = self._get_loop()
        resp = loop.run_until_complete(
            loop.run_in_executor(
                None,
                lambda: ollama.chat(model=self.model, messages=messages, stream=False),
            )
        )
        return self._get_chat_response_content(resp)

    # ══════════════════════════════════════════════════════════════════════
    #  Ollama API helpers
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _parse_models(resp) -> List[Dict]:
        if hasattr(resp, "models"):
            return [{"model": m.model, "size": m.size} for m in resp.models]
        return resp.get("models", [])

    @staticmethod
    def _get_attr(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    @staticmethod
    def _get_chunk_content(chunk) -> str:
        if hasattr(chunk, "message"):
            m = chunk.message
            if hasattr(m, "content"): return m.content or ""
        if isinstance(chunk, dict):
            return chunk.get("message", {}).get("content", "") or ""
        return ""

    @staticmethod
    def _get_chat_response_content(response) -> str:
        if hasattr(response, "message"):
            m = response.message
            if hasattr(m, "content"): return m.content or ""
        if isinstance(response, dict):
            return response.get("message", {}).get("content", "") or ""
        return ""

    @staticmethod
    def _get_embedding(resp) -> List[float]:
        if isinstance(resp, dict): return resp.get("embedding", [])
        return getattr(resp, "embedding", [])

    # ══════════════════════════════════════════════════════════════════════
    #  Token counting
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def count_tokens(text: str) -> int:
        if not text: return 0
        return max(1, len(text) // CHARS_PER_TOKEN)

    # ══════════════════════════════════════════════════════════════════════
    #  Database
    # ══════════════════════════════════════════════════════════════════════

    def setup_db(self) -> None:
        PINCER_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.execute("PRAGMA journal_mode=WAL")
        # Phase 1 + 2
        self.conn.execute("CREATE TABLE IF NOT EXISTS user_info (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT CHECK(role IN ('system','user','assistant','summary')),
                content TEXT, tokens INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT, command TEXT, status TEXT, output TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
        """)
        # Phase 3 — tasks with cost columns
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT, plan TEXT,
                current_step INTEGER DEFAULT 0,
                total_steps INTEGER DEFAULT 0,
                status TEXT DEFAULT 'planning',
                auto_approve BOOLEAN DEFAULT FALSE,
                llm_calls INTEGER DEFAULT 0,
                shell_count INTEGER DEFAULT 0,
                write_count INTEGER DEFAULT 0,
                rm_count INTEGER DEFAULT 0,
                elapsed_minutes INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP)
        """)
        # Migrations: add cost columns if upgrading from Phase 2
        for col, typ in [("llm_calls","INTEGER DEFAULT 0"),("shell_count","INTEGER DEFAULT 0"),
                         ("write_count","INTEGER DEFAULT 0"),("rm_count","INTEGER DEFAULT 0"),
                         ("elapsed_minutes","INTEGER DEFAULT 0")]:
            try: self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError: pass

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                category TEXT CHECK(category IN ('observation','error','success','preference','pattern')),
                content TEXT, embedding BLOB,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER, step_index INTEGER,
                conversation_snapshot TEXT,
                working_directory TEXT,
                git_commit_hash TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
        """)
        self.conn.commit()
        self._setup_vec()
        self._load_allowed_commands()

    def _setup_vec(self) -> None:
        self._vec_available = False
        try:
            import sqlite_vec
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            self.conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS tool_history_vec
                USING vec0(id INTEGER PRIMARY KEY, embedding float[{EMBEDDING_DIM}])
            """)
            self.conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS notes_vec
                USING vec0(id INTEGER PRIMARY KEY, embedding float[{EMBEDDING_DIM}])
            """)
            self.conn.commit()
            self._vec_available = True
        except Exception:
            pass
        self._embedding_available = self.check_model_available(EMBEDDING_MODEL)

    def _load_allowed_commands(self) -> None:
        raw = self.get_config("allowed_commands")
        if raw:
            try: self.permission_manager._always_allowed = json.loads(raw)
            except json.JSONDecodeError: pass

    def _save_allowed_commands(self) -> None:
        self.set_config("allowed_commands", json.dumps(self.permission_manager._always_allowed))

    def get_config(self, key: str) -> Optional[str]:
        r = self.conn.execute("SELECT value FROM user_info WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def set_config(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO user_info (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def load_config(self) -> None:
        self.user_name = self.get_config("user_name") or ""
        self.preferred_language = self.get_config("preferred_language") or "python"
        self.model = self.get_config("model") or DEFAULT_MODEL
        self.thinking_mode = self.get_config("thinking_mode") == "on"

    # ══════════════════════════════════════════════════════════════════════
    #  Tool history
    # ══════════════════════════════════════════════════════════════════════

    def log_tool_history(self, name: str, cmd: str, result: ToolResult) -> None:
        status = "denied" if "denied" in result.error.lower() or "deny" in result.error.lower() else ("success" if result.success else "failure")
        output = (result.output if result.success else result.error)[:2000]
        cur = self.conn.execute(
            "INSERT INTO tool_history (tool_name, command, status, output) VALUES (?, ?, ?, ?)",
            (name, cmd[:500], status, output))
        rid = cur.lastrowid; self.conn.commit()
        if self._vec_available and self._embedding_available:
            self._store_embedding("tool_history_vec", rid, f"{name} {cmd} {output}")

    def _store_embedding(self, table: str, row_id: int, text: str) -> None:
        try:
            resp = ollama.embeddings(model=EMBEDDING_MODEL, prompt=text)
            emb = self._get_embedding(resp)
            if emb and len(emb) == EMBEDDING_DIM:
                import struct
                vb = struct.pack(f"{len(emb)}f", *emb)
                self.conn.execute(f"INSERT INTO {table} (id, embedding) VALUES (?, ?)", (row_id, vb))
                self.conn.commit()
        except Exception:
            pass

    def get_relevant_history(self, query: str, limit: int = 3) -> str:
        if not query: return ""
        if self._vec_available and self._embedding_available:
            results = self._semantic_tool_search(query, limit)
            if results: return self._fmt_history(results)
        results = self._kw_tool_search(query, limit)
        return self._fmt_history(results) if results else ""

    def _semantic_tool_search(self, q: str, limit: int) -> List[Dict]:
        try:
            resp = ollama.embeddings(model=EMBEDDING_MODEL, prompt=q)
            emb = self._get_embedding(resp)
            if not emb or len(emb) != EMBEDDING_DIM: return []
            import struct; vb = struct.pack(f"{len(emb)}f", *emb)
            rows = self.conn.execute("""
                SELECT th.tool_name, th.command, th.status, th.output
                FROM tool_history th JOIN tool_history_vec v ON th.id=v.id
                WHERE v.embedding MATCH ? ORDER BY v.distance LIMIT ?
            """, (vb, limit)).fetchall()
            return [{"tool_name":r[0],"command":r[1],"status":r[2],"output":r[3]} for r in rows]
        except Exception: return []

    def _kw_tool_search(self, q: str, limit: int) -> List[Dict]:
        words = re.findall(r"\w+", q)
        if not words: return []
        conds = " OR ".join("command LIKE ?" for _ in words)
        params = [f"%{w}%" for w in words] + [limit]
        rows = self.conn.execute(
            f"SELECT tool_name, command, status, output FROM tool_history "
            f"WHERE {conds} ORDER BY id DESC LIMIT ?", params).fetchall()
        return [{"tool_name":r[0],"command":r[1],"status":r[2],"output":r[3]} for r in rows]

    @staticmethod
    def _fmt_history(entries: List[Dict]) -> str:
        lines = ["[Relevant tool history]"]
        for e in entries:
            ic = "✓" if e["status"] == "success" else "✗"
            lines.append(f"  {ic} {e['tool_name']}: {e['command'][:80]} → {e['status']}")
        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════════
    #  Self-Notes
    # ══════════════════════════════════════════════════════════════════════

    def write_note(self, category: str, content: str) -> None:
        tid = self.loop.task_id
        self.conn.execute(
            "INSERT INTO notes (task_id, category, content) VALUES (?, ?, ?)",
            (tid, category, content[:2000]))
        nid = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.commit()
        if self._vec_available and self._embedding_available:
            self._store_embedding("notes_vec", nid, content)

    def get_relevant_notes(self, query: str, limit: int = 3) -> str:
        if not query: return ""
        if self._vec_available and self._embedding_available:
            try:
                resp = ollama.embeddings(model=EMBEDDING_MODEL, prompt=query)
                emb = self._get_embedding(resp)
                if emb and len(emb) == EMBEDDING_DIM:
                    import struct; vb = struct.pack(f"{len(emb)}f", *emb)
                    rows = self.conn.execute("""
                        SELECT n.category, n.content FROM notes n
                        JOIN notes_vec nv ON n.id=nv.id
                        WHERE nv.embedding MATCH ? ORDER BY nv.distance LIMIT ?
                    """, (vb, limit)).fetchall()
                    if rows:
                        icons = {"success":"✅","error":"❌","observation":"👁","preference":"⚙️","pattern":"🔄"}
                        lines = ["[Relevant notes]"]
                        for r in rows: lines.append(f"  {icons.get(r[0],'📝')} {r[1][:150]}")
                        return "\n".join(lines)
            except Exception: pass
        rows = self.conn.execute(
            "SELECT category, content FROM notes WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query[:50]}%", limit)).fetchall()
        if rows:
            lines = ["[Relevant notes]"]
            for r in rows: lines.append(f"  📝 {r[1][:150]}")
            return "\n".join(lines)
        return ""

    # ══════════════════════════════════════════════════════════════════════
    #  RepoMap — basic codebase indexing
    # ══════════════════════════════════════════════════════════════════════

    def build_repomap(self) -> Dict[str, List[str]]:
        """Build a simple symbol index of the codebase."""
        symbols: Dict[str, List[str]] = {}
        for ext in (".py", ".js", ".ts"):
            for path in Path(".").rglob(f"*{ext}"):
                sp = str(path)
                if any(s in sp for s in ("node_modules", ".git", "__pycache__", ".venv", "venv")):
                    continue
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    if ext == ".py":
                        defs = re.findall(r"^(?:class|def|async def)\s+(\w+)", content, re.MULTILINE)
                    elif ext in (".js", ".ts"):
                        defs = re.findall(r"(?:function|class|const|let|var)\s+(\w+)", content)
                    else:
                        defs = []
                    if defs: symbols[sp] = defs
                except Exception:
                    pass
        return symbols

    # ══════════════════════════════════════════════════════════════════════
    #  Ollama helpers
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def check_ollama_installed() -> bool:
        return shutil.which("ollama") is not None

    @staticmethod
    def check_ollama_server() -> bool:
        try: ollama.list(); return True
        except: return False

    def check_model_available(self, name: str) -> bool:
        try:
            resp = ollama.list()
            for m in self._parse_models(resp):
                n = m.get("model", "")
                if n == name or n.startswith(name.split(":")[0] + ":"): return True
            return False
        except: return False

    def pull_model(self, name: str) -> None:
        self.console.print(f"  Pulling {name}…", style="yellow")
        try:
            for chunk in ollama.pull(name, stream=True):
                total = self._get_attr(chunk, "total", 0)
                completed = self._get_attr(chunk, "completed", 0)
                if total and total > 0:
                    pct = int(completed / total * 100)
                    bw = 20; f = int(bw * completed / total)
                    sys.stdout.write(f"\r  [{'█'*f}{'░'*(bw-f)}] {pct}% "); sys.stdout.flush()
                elif "success" in self._get_attr(chunk, "status", ""): break
            sys.stdout.write(f"\r  ✓ {name} pulled.          \n"); sys.stdout.flush()
        except Exception as e:
            self.error_console.print(f"\n  ❌ Failed to pull {name}: {e}"); sys.exit(1)

    # ══════════════════════════════════════════════════════════════════════
    #  First-run wizard
    # ══════════════════════════════════════════════════════════════════════

    def first_run_wizard(self) -> None:
        self.console.print(Panel(
            Text.from_markup(
                "[bold cyan]🤖 PINCER[/bold cyan] — local AI coding assistant\n\n"
                "Let's get you set up. This takes about 2 minutes."),
            border_style="cyan", title="Welcome"))
        if not self.check_ollama_installed():
            self.console.print("❌ Ollama not found.\n   brew install ollama\n   https://ollama.com", style="bold red")
            sys.exit(1)
        self.console.print("  ✓ Ollama found", style="green")
        if not self.check_ollama_server():
            self.console.print("❌ Ollama server not running. Start: ollama serve", style="bold red")
            sys.exit(1)
        self.console.print("  ✓ Server running", style="green")
        if not self.check_model_available(self.model):
            self.pull_model(self.model)
        else:
            self.console.print(f"  ✓ {self.model} available", style="green")
        if not self.check_model_available(EMBEDDING_MODEL):
            self.console.print(f"  ⚠ Pulling {EMBEDDING_MODEL}…", style="yellow")
            self.pull_model(EMBEDDING_MODEL)
        else:
            self.console.print(f"  ✓ {EMBEDDING_MODEL} available", style="green")
        # User profile — FIX: sensible default, TTY check
        name = "user"
        if sys.stdin.isatty():
            name = questionary.text("  Your name?", default="user").ask() or "user"
        self.user_name = name.strip() or "user"
        self.set_config("user_name", self.user_name)
        lang = "python"
        if sys.stdin.isatty():
            lang = questionary.select("  Preferred language?", choices=[
                "python","javascript","typescript","rust","go","java","c","cpp","ruby","other"
            ], default="python").ask() or "python"
        self.preferred_language = lang
        self.set_config("preferred_language", lang)
        exp = "Intermediate"
        if sys.stdin.isatty():
            exp = questionary.select("  Experience level?", choices=[
                "Beginner", "Intermediate", "Advanced"
            ], default="Intermediate").ask() or "Intermediate"
        self.set_config("experience", exp)
        # Project setup — FIX: TTY check
        if sys.stdin.isatty():
            self._project_setup()
        self.set_config("model", self.model)
        self.set_config("thinking_mode", "off")
        self.console.print(Panel(
            "✓ All set!\n\nTry saying [bold]'hello'[/bold] or [bold]'read the README'[/bold]",
            border_style="green", title="Ready"))

    def _project_setup(self) -> None:
        if not Path(".git").exists():
            init = questionary.confirm("  Initialize git repo?", default=True).ask()
            if init:
                try:
                    subprocess.run(["git", "init"], capture_output=True, check=True)
                    self.console.print("  ✓ Git initialized", style="green")
                except Exception: pass
        for mf in MEMORY_FILES:
            if Path(mf).exists():
                self._project_memory = Path(mf).read_text(encoding="utf-8", errors="replace")[:4000]
                self.console.print(f"  ✓ Found {mf}", style="green"); break
        else:
            self._scan_and_offer_memory()

    def _scan_and_offer_memory(self) -> None:
        facts: List[str] = []
        if Path("requirements.txt").exists(): facts.append("Python project (requirements.txt)")
        if Path("package.json").exists(): facts.append("Node.js project (package.json)")
        if Path("Cargo.toml").exists(): facts.append("Rust project (Cargo.toml)")
        if Path("go.mod").exists(): facts.append("Go project (go.mod)")
        if not facts: return
        create = questionary.confirm(
            f"  Detected: {', '.join(facts)}. Create .pincer.md?", default=True).ask()
        if create:
            content = "# Project Context\n\n## Auto-detected\n" + "\n".join(f"- {f}" for f in facts) + "\n"
            Path(".pincer.md").write_text(content)
            self._project_memory = content
            self.console.print("  ✓ Created .pincer.md", style="green")

    # ══════════════════════════════════════════════════════════════════════
    #  System prompt
    # ══════════════════════════════════════════════════════════════════════

    def get_system_prompt(self) -> str:
        p = (
            f"You are Pincer, a helpful coding assistant running locally on the user's Mac.\n"
            f"User: {self.user_name} | Preferred language: {self.preferred_language}\n"
            f"Be concise. Use markdown for code blocks.\n"
            f"You have file tools (read, write, edit) and shell execution.\n"
            f"When tool results appear in [Tool: …] blocks, analyse them and respond.\n"
            f"If a tool failed, suggest a fix. If a test failed, explain why."
        )
        if self._project_memory: p += f"\n\nProject context:\n{self._project_memory[:2000]}"
        if self.thinking_mode: p += "\nThink step by step before responding."
        return p

    # ══════════════════════════════════════════════════════════════════════
    #  Context management
    # ══════════════════════════════════════════════════════════════════════

    def get_total_tokens(self) -> int:
        r = self.conn.execute("SELECT COALESCE(SUM(tokens), 0) FROM conversation").fetchone()
        return (r[0] if r else 0) + self.count_tokens(self.get_system_prompt())

    def get_message_count(self) -> int:
        r = self.conn.execute("SELECT COUNT(*) FROM conversation").fetchone()
        return r[0] if r else 0

    def save_message(self, role: str, content: str) -> None:
        self.conn.execute("INSERT INTO conversation (role, content, tokens) VALUES (?, ?, ?)",
                          (role, content, self.count_tokens(content)))
        self.conn.commit()

    def get_conversation_messages(self) -> List[Dict[str, str]]:
        rows = self.conn.execute("SELECT role, content FROM conversation ORDER BY id ASC").fetchall()
        msgs: List[Dict[str, str]] = [{"role": "system", "content": self.get_system_prompt()}]
        for role, content in rows:
            msgs.append({"role": "system" if role == "summary" else role, "content": content})
        return msgs

    def compact_context(self, manual: bool = False) -> None:
        rows = self.conn.execute("SELECT id, role, content FROM conversation ORDER BY id ASC").fetchall()
        if len(rows) < 4:
            if manual: self.console.print("  ⚠ Need ≥ 4 messages to compact.", style="yellow")
            return
        sp = len(rows) // 2; old = rows[:sp]
        ct = "\n\n".join(f"{r[1]}: {r[2]}" for r in old)
        try:
            with self.console.status("  [bold yellow]Compacting…[/]"):
                summary = self._run_llm_sync([{"role":"user","content":f"Summarise in one paragraph:\n\n{ct}"}])
        except Exception as e:
            self.console.print(f"  ❌ Compact failed: {e}", style="bold red"); return
        ids = [r[0] for r in old]; ph = ",".join("?" for _ in ids)
        self.conn.execute(f"DELETE FROM conversation WHERE id IN ({ph})", ids)
        self.conn.execute("INSERT INTO conversation (role, content, tokens) VALUES (?, ?, ?)",
                          ("summary", summary, self.count_tokens(summary)))
        self.conn.commit()
        self.console.print(f"  ✓ Compacted {len(old)} messages → 1 summary", style="green")

    def auto_compact_if_needed(self) -> None:
        tt = self.get_total_tokens()
        if tt > COMPACT_THRESHOLD:
            self.console.print(f"  ⚡ {tt/1000:.1f}K tokens — auto-compacting…", style="yellow")
            self.compact_context()
        tt = self.get_total_tokens(); i = 0
        while tt > MAX_TOKENS and i < 200:
            i += 1
            row = self.conn.execute("SELECT id FROM conversation WHERE role='user' ORDER BY id ASC LIMIT 1").fetchone()
            if not row:
                o = self.conn.execute("SELECT id FROM conversation ORDER BY id ASC LIMIT 1").fetchone()
                if not o: break
                self.conn.execute("DELETE FROM conversation WHERE id=?", (o[0],))
            else:
                self.conn.execute("DELETE FROM conversation WHERE id=?", (row[0],))
                na = self.conn.execute("SELECT id FROM conversation WHERE id>? AND role='assistant' ORDER BY id ASC LIMIT 1", (row[0],)).fetchone()
                if na: self.conn.execute("DELETE FROM conversation WHERE id=?", (na[0],))
            self.conn.commit(); tt = self.get_total_tokens()

    # ══════════════════════════════════════════════════════════════════════
    #  Thinking tags
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def strip_thinking_tags(text: str) -> str:
        return re.sub(r"<think[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL).strip()

    @staticmethod
    def _partial_tag_len(buf: str, tag: str) -> int:
        for i in range(1, min(len(tag)+1, len(buf)+1)):
            if buf[-i:] == tag[:i]: return i
        return 0

    # ══════════════════════════════════════════════════════════════════════
    #  Streaming response
    # ══════════════════════════════════════════════════════════════════════

    def stream_response(self, messages: List[Dict[str, str]]) -> str:
        full = ""; in_t = False; t_shown = False; buf = ""
        try:
            self._generating = True
            for chunk in ollama.chat(model=self.model, messages=messages, stream=True):
                if not self._generating: break
                if self._get_attr(chunk, "done", False): break
                tok = self._get_chunk_content(chunk)
                if not tok: continue
                full += tok
                if not self.thinking_mode:
                    self.console.print(tok, end=""); continue
                buf += tok; ch = True
                while ch:
                    ch = False
                    if not in_t:
                        oi = buf.find(THINK_TAG_OPEN)
                        if oi != -1:
                            if oi > 0: self.console.print(buf[:oi], end="")
                            af = buf[oi+len(THINK_TAG_OPEN):]; gt = af.find(">")
                            if gt != -1:
                                buf = af[gt+1:]; in_t = True
                                if not t_shown: self.console.print("  ● Thinking…", style="yellow"); t_shown = True
                                ch = True
                            else: buf = buf[oi:]
                        else:
                            pt = self._partial_tag_len(buf, THINK_TAG_OPEN)
                            s = buf[:len(buf)-pt] if pt else buf
                            if s: self.console.print(s, end="")
                            buf = buf[len(s):]
                    else:
                        ci = buf.find(THINK_TAG_CLOSE)
                        if ci != -1:
                            af = buf[ci+len(THINK_TAG_CLOSE):]; gt = af.find(">")
                            if gt != -1: buf = af[gt+1:]; in_t = False; ch = True
                            else: buf = buf[ci:]
                        else:
                            pt = self._partial_tag_len(buf, THINK_TAG_CLOSE)
                            buf = buf[-pt:] if pt else ""
                if buf and not in_t: self.console.print(buf, end="")
            self.console.print()
        except KeyboardInterrupt:
            self._generating = False; self.console.print("\n  ⏹ Stopped.", style="yellow")
        except Exception as e:
            self._generating = False; self.console.print(f"\n  ❌ Error: {e}", style="bold red")
        self._generating = False; return full

    # ══════════════════════════════════════════════════════════════════════
    #  Status bar
    # ══════════════════════════════════════════════════════════════════════

    def _status_bar(self) -> HTML:
        tt = self.get_total_tokens(); ctx = f"{tt/1000:.1f}K"
        th = "on" if self.thinking_mode else "off"
        sb = "🔒" if self.shell_tool.sandbox_available else "🔓"
        task = self.loop.progress_text()
        elapsed = ""
        if self.loop.start_time and self.loop.status == "active":
            elapsed = f" | ⏱️ {int((time.time()-self.loop.start_time)/60)}m"
        ap = " 🟢" if self.loop.auto_approve else ""
        return HTML(
            f"<style bg='ansiblack' fg='ansiwhite'>"
            f" {self.model} | thinking:{th} | ctx: {ctx}/12K | {sb} | "
            f"task: {task}{elapsed}{ap}</style>")

    # ══════════════════════════════════════════════════════════════════════
    #  Git auto-commit
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _auto_commit(msg: str) -> None:
        if not Path(".git").exists(): return
        try:
            subprocess.run(["git", "add", "-A"], capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", msg], capture_output=True, check=True)
        except Exception: pass

    # ══════════════════════════════════════════════════════════════════════
    #  Tool execution — uses cached event loop
    # ══════════════════════════════════════════════════════════════════════

    def _generate_file_content(self, desc: str, path: str) -> str:
        prompt = (
            f"Generate complete content for '{path}' based on: {desc}\n\n"
            f"Output ONLY file content. No markdown fences. Start with first line.")
        try:
            with self.console.status("  [bold yellow]Generating content…[/]"):
                c = self._run_llm_sync([{"role":"user","content":prompt}])
            c = re.sub(r"^```[\w]*\n", "", c); c = re.sub(r"\n```$", "", c)
            return c.strip() + "\n"
        except Exception as e:
            self.console.print(f"  ❌ Generation failed: {e}", style="bold red"); return ""

    def _generate_edit(self, req: str, path: str, content: str) -> Optional[Tuple[str, str]]:
        prompt = (
            f"Modify '{path}'.\n\nCurrent:\n{content}\n\nRequest: {req}\n\n"
            f"EXACT format — no extra text:\n<<<OLD>>>\nexact lines\n<<<NEW>>>\nreplacement lines")
        try:
            with self.console.status("  [bold yellow]Generating edit…[/]"):
                text = self._run_llm_sync([{"role":"user","content":prompt}])
            m = re.search(r"<<<OLD>>>\s*\n(.*?)<<<NEW>>>\s*\n(.*)", text, re.DOTALL)
            if not m: return None
            old = m.group(1).rstrip("\n"); new = re.sub(r"\n```\s*$", "", m.group(2).rstrip("\n"))
            return old, new
        except Exception as e:
            self.console.print(f"  ❌ Edit generation failed: {e}", style="bold red"); return None

    def _prompt_approval(self, cmd: str, cwd: str, risk: str) -> str:
        if not sys.stdin.isatty():
            return "deny"
        colors = {"safe":"green","ask":"yellow","deny":"red"}
        icons = {"safe":"✅","ask":"⚠️","deny":"🚫"}
        c = colors.get(risk,"yellow"); ic = icons.get(risk,"⚠️")
        step_info = ""
        if self.loop.status == "active":
            step_info = f"\n[bold]Autonomous Step:[/bold] {self.loop.current_step+1}/{len(self.loop.plan)}"
        self.console.print(Panel(
            f"[bold]Command:[/bold]  {cmd}\n[bold]Directory:[/bold] {cwd}\n"
            f"[bold]Risk:[/bold] [{c}]{ic} {risk.upper()}[/{c}]{step_info}",
            title="⚡ Approval", border_style=c))
        choices = ["Allow once", "Allow always", "Allow for this task", "Deny", "Edit command"]
        choice = questionary.select("  Choose:", choices=choices).ask()
        if choice == "Allow once": return "allow"
        if choice == "Allow always":
            self.permission_manager.add_allowed(cmd); self._save_allowed_commands(); return "allow"
        if choice == "Allow for this task":
            self.loop.session_allowed.append(cmd); return "allow"
        if choice == "Edit command":
            ed = questionary.text("  Edit:", default=cmd).ask()
            return f"edit:{ed.strip()}" if ed and ed.strip() else "deny"
        return "deny"

    def handle_tool_message(self, user_msg: str, intent: str, params: Dict[str, str]) -> None:
        self.auto_compact_if_needed()
        self.save_message("user", user_msg)
        hist = self.get_relevant_history(user_msg)
        notes = self.get_relevant_notes(user_msg)
        cwd = os.getcwd()

        if intent == "file_read":
            path = params.get("path", "")
            if not path: self.console.print("  ⚠ No path.", style="yellow"); return
            result = self.file_tools.read_file(path)
            self.log_tool_history("read_file", path, result)
            border = "green" if result.success else "red"
            self.console.print(Panel(result.display[:3000], title=f"📄 read_file: {path}", border_style=border))
            self.save_message("system", f"[Tool: read_file('{path}')] {'OK' if result.success else 'FAIL'}\n{result.display[:2000]}[/Tool]")
        elif intent == "file_write":
            path = params.get("path", ""); desc = params.get("description", "")
            if not path: self.console.print("  ⚠ No path.", style="yellow"); return
            content = params.get("content", "")
            if not content: content = self._generate_file_content(desc, path)
            if not content:
                self.save_message("system", f"[Tool: write_file('{path}') FAIL] Empty[/Tool]"); return
            result = self.file_tools.write_file(path, content)
            self.log_tool_history("write_file", path, result)
            border = "green" if result.success else "red"
            self.console.print(Panel(result.display, title=f"📝 write_file: {path}", border_style=border))
            if result.success: self._auto_commit(f"write: {path}")
            self.write_note("success" if result.success else "error", f"write_file {path}: {result.display[:200]}")
            self.save_message("system", f"[Tool: write_file('{path}')] {'OK' if result.success else 'FAIL'}\n{result.display}[/Tool]")
        elif intent == "file_edit":
            path = params.get("path", "")
            if not path: self.console.print("  ⚠ No path.", style="yellow"); return
            old_s = params.get("old_string", ""); new_s = params.get("new_string", "")
            if not old_s or not new_s:
                rr = self.file_tools.read_file(path)
                if not rr.success:
                    self.log_tool_history("edit_file", path, rr)
                    self.console.print(Panel(rr.error, title=f"❌ edit_file: {path}", border_style="red")); return
                ep = self._generate_edit(user_msg, path, rr.output)
                if not ep:
                    self.save_message("system", f"[Tool: edit_file('{path}') FAIL] Parse error[/Tool]"); return
                old_s, new_s = ep
            result = self.file_tools.edit_file(path, old_s, new_s)
            if not result.success: result = self.file_tools.edit_file_diff(path, old_s, new_s)
            self.log_tool_history("edit_file", path, result)
            border = "green" if result.success else "red"
            self.console.print(Panel(result.display, title=f"✏️ edit_file: {path}", border_style=border))
            if result.success: self._auto_commit(f"edit: {path}")
            self.write_note("success" if result.success else "error", f"edit_file {path}: {result.display[:200]}")
            self.save_message("system", f"[Tool: edit_file('{path}')] {'OK' if result.success else 'FAIL'}\n{result.display}[/Tool]")
        elif intent == "shell":
            cmd = params.get("command", "")
            if not cmd: self.console.print("  ⚠ No command.", style="yellow"); return
            risk = self.permission_manager.check(cmd, cwd)
            if risk == "deny":
                self.console.print(Panel(f"🚫 Denied: {cmd}", border_style="red", title="Blocked"))
                self.log_tool_history("execute_command", cmd, ToolResult(False,"","execute_command",cmd,"Denied"))
                self.write_note("error", f"🚫 Denied: {cmd}")
                self.save_message("system", f"[Tool: DENIED] {cmd}[/Tool]")
            elif risk == "ask":
                approval = self._prompt_approval(cmd, cwd, "ask")
                if approval == "deny":
                    self.log_tool_history("execute_command", cmd, ToolResult(False,"","execute_command",cmd,"Denied"))
                    self.write_note("error", f"User denied: {cmd}")
                    self.save_message("system", f"[Tool: DENIED] {cmd}[/Tool]")
                elif approval.startswith("edit:"):
                    ec = approval[5:]
                    nr = self.permission_manager.check(ec, cwd)
                    if nr == "deny":
                        self.save_message("system", f"[Tool: DENIED] {ec}[/Tool]")
                    else:
                        result = self.shell_tool.execute_command(ec, cwd, allow_network=self._command_needs_network(ec))
                        self._display_shell_result(ec, result)
                else:
                    result = self.shell_tool.execute_command(cmd, cwd, allow_network=self._command_needs_network(cmd))
                    self._display_shell_result(cmd, result)
            else:
                result = self.shell_tool.execute_command(cmd, cwd, allow_network=self._command_needs_network(cmd))
                self._display_shell_result(cmd, result)

        # LLM response
        messages = self.get_conversation_messages()
        if hist: messages.insert(1, {"role": "system", "content": hist})
        if notes: messages.insert(1, {"role": "system", "content": notes})
        self.console.print()
        resp = self.stream_response(messages)
        self.console.print()
        if resp.strip(): self.save_message("assistant", resp)

    def _display_shell_result(self, cmd: str, result: ToolResult) -> None:
        self.log_tool_history("execute_command", cmd, result)
        border = "green" if result.success else "red"
        title = f"🔧 Shell: {cmd[:40]}" + (f" ({result.error})" if not result.success else "")
        output = result.output[:3000] if result.success else f"{result.output[:2000]}\n{result.error}"
        self.console.print(Panel(output, title=title, border_style=border))
        self.save_message("system", f"[Tool: execute_command('{cmd}')] {'OK' if result.success else 'FAIL'}\n{result.display[:3000]}[/Tool]")

    @staticmethod
    def _command_needs_network(cmd: str) -> bool:
        for p in ["pip install","npm install","cargo","git clone","git pull","git push","git fetch","brew","curl","wget"]:
            if cmd.strip().startswith(p): return True
        return False

    # ══════════════════════════════════════════════════════════════════════
    #  Checkpoint / Resume
    # ══════════════════════════════════════════════════════════════════════

    def save_checkpoint_manual(self) -> None:
        self.loop.save_checkpoint()
        self.console.print("  ✓ Checkpoint saved.", style="green")

    def resume_task(self, task_id: Optional[int] = None) -> None:
        if task_id is None:
            row = self.conn.execute(
                "SELECT id, goal, status FROM tasks "
                "WHERE status IN ('paused','active','stuck') ORDER BY updated_at DESC LIMIT 1").fetchone()
            if not row:
                self.console.print("  ⚠ No paused tasks.", style="yellow"); return
            task_id = row[0]
            self.console.print(f"  📋 Found task: {row[1]} (status: {row[2]})")
        if not self.loop.load_task(task_id):
            self.console.print(f"  ❌ Task {task_id} not found.", style="bold red"); return
        cp = self.conn.execute(
            "SELECT conversation_snapshot, working_directory FROM checkpoints "
            "WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
        if cp:
            try:
                conv = json.loads(cp[0])
                self.conn.execute("DELETE FROM conversation")
                for item in conv:
                    # FIX: handle both lists and tuples from JSON
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        role, content = item[0], item[1]
                        self.save_message(role, content)
                if cp[1] and Path(cp[1]).exists():
                    os.chdir(cp[1])
                self.console.print(f"  ✓ Restored checkpoint (step {self.loop.current_step})", style="green")
            except Exception as e:
                self.console.print(f"  ⚠ Checkpoint restore failed: {e}", style="yellow")
        self.loop.display_plan()
        if sys.stdin.isatty():
            confirm = questionary.confirm("  Resume execution?", default=True).ask()
            if confirm:
                self.loop.status = "active"
                self.loop.run_loop()

    def rollback(self) -> None:
        if not self.loop.task_id:
            self.console.print("  ⚠ No active task.", style="yellow"); return
        cp = self.conn.execute(
            "SELECT id, step_index, git_commit_hash FROM checkpoints "
            "WHERE task_id=? ORDER BY id DESC LIMIT 1", (self.loop.task_id,)).fetchone()
        if not cp:
            self.console.print("  ⚠ No checkpoint.", style="yellow"); return
        if cp[2] and Path(".git").exists():
            try:
                subprocess.run(["git", "reset", "--hard", cp[2]], capture_output=True, check=True)
                self.console.print(f"  ✓ Rolled back to {cp[2][:8]}", style="green")
            except Exception as e:
                self.console.print(f"  ❌ Rollback failed: {e}", style="bold red")
        self.loop.current_step = cp[1]
        self.loop._update_task()

    def _check_resume_on_startup(self) -> None:
        if not sys.stdin.isatty():
            return
        row = self.conn.execute(
            "SELECT id, goal, status, updated_at FROM tasks "
            "WHERE status IN ('paused','active','stuck') ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not row: return
        self.console.print(f"\n  ⏸️ Resume task '{row[1]}' (status: {row[2]}, last: {row[3]})?")
        if questionary.confirm("  Resume?", default=True).ask():
            self.resume_task(row[0])

    # ══════════════════════════════════════════════════════════════════════
    #  Slash commands
    # ══════════════════════════════════════════════════════════════════════

    def cmd_model(self) -> None:
        try: resp = ollama.list(); ml = self._parse_models(resp)
        except Exception as e: self.error_console.print(f"  {e}"); return
        if not ml: self.console.print("  No models.", style="yellow"); return
        ch = []; nm = {}
        for m in ml:
            n = m.get("model","?"); s = m.get("size",0)/(1024**3)
            l = f"{n}  ({s:.1f}GB)"; ch.append(l); nm[l] = n
        ch.sort()
        sel = questionary.select("  Model:", choices=ch).ask()
        if sel:
            self.model = nm.get(sel, sel.split("  ")[0])
            self.set_config("model", self.model)
            self.console.print(f"  ✓ Switched to {self.model}", style="green")

    def cmd_think(self) -> None:
        self.thinking_mode = not self.thinking_mode
        s = "on" if self.thinking_mode else "off"
        self.set_config("thinking_mode", s)
        self.console.print(f"  ✓ Thinking: {s}", style="green")

    def cmd_clear(self) -> None:
        self.conn.execute("DELETE FROM conversation"); self.conn.commit()
        self.console.print("  ✓ Context cleared.", style="green")

    def cmd_compact(self) -> None:
        self.compact_context(manual=True)

    def cmd_help(self) -> None:
        t = Table(title="Pincer Commands", header_style="bold cyan", border_style="dim", padding=(0,2))
        t.add_column("Command", style="bold", width=14); t.add_column("Description")
        for c, d in [
            ("/model","Switch Ollama model"),("/think","Toggle thinking mode"),
            ("/clear","Clear conversation"),("/compact","Compact context"),
            ("/tools","List tools"),("/sandbox","Show sandbox config"),
            ("/undo","Undo last git commit"),("/context","Context window stats"),
            ("/plan <goal>","Plan a task"),("/go","Execute current plan"),
            ("/task <goal>","Plan + execute shortcut"),
            ("/explore","Map codebase (read-only)"),("/status","Task progress"),
            ("/pause","Pause current task"),("/resume","Resume paused task"),
            ("/abort","Abort current task"),("/notes","Show self-notes"),
            ("/compact-notes","Summarize old notes"),("/cost","Task cost metrics"),
            ("/auto-approve","Toggle auto-approve"),("/health","Agent health"),
            ("/edit-plan","Edit plan in $EDITOR"),("/rollback","Revert to checkpoint"),
            ("/checkpoint","Save checkpoint"),("/memory","Project memory"),
            ("/panel","Show side panel info"),("/update","Update Pincer"),
            ("/exit","Quit Pincer"),("/help","This help"),
        ]: t.add_row(c, d)
        self.console.print(t)

    def cmd_tools(self) -> None:
        t = Table(title="Tools", header_style="bold cyan", border_style="dim", padding=(0,2))
        t.add_column("Tool", style="bold", width=16); t.add_column("Status", width=6); t.add_column("Description")
        for n, s, d in [
            ("read_file","✅","Read file with line numbers"),
            ("write_file","✅","Write file (creates dirs)"),
            ("edit_file","✅","Exact string replacement"),
            ("edit_file_diff","✅","SEARCH/REPLACE block edit"),
            ("execute_command","✅","Sandboxed shell execution"),
            ("ask_user","✅","Ask user during autonomous execution"),
        ]: t.add_row(n, s, d)
        self.console.print(t)

    def cmd_sandbox(self) -> None:
        cwd = os.getcwd()
        st = "Active (sandbox-exec)" if self.shell_tool.sandbox_available else "Unavailable (unsandboxed)"
        t = Table(title="Sandbox", show_header=False, border_style="dim", padding=(0,2))
        t.add_column("Key", style="bold"); t.add_column("Value")
        t.add_row("Status", st); t.add_row("CWD", cwd)
        t.add_row("Reads", f"{cwd}, ~, /usr, /Library, /System, /opt")
        t.add_row("Writes", f"{cwd}, {tempfile.gettempdir()}")
        t.add_row("Network", "Blocked (except approved git/pip/npm)")
        t.add_row("Blocked", "sudo, rm -rf /, mkfs, dd, curl|bash")
        self.console.print(t)

    def cmd_undo(self) -> None:
        if not Path(".git").exists():
            self.console.print("  ⚠ Not in git repo.", style="yellow"); return
        try:
            subprocess.run(["git","reset","--hard","HEAD~1"], capture_output=True, text=True, check=True)
            self.console.print("  ✓ Undid last commit.", style="green")
        except Exception as e:
            self.console.print(f"  ❌ Undo failed: {e}", style="bold red")

    def cmd_context(self) -> None:
        tt = self.get_total_tokens(); cnt = self.get_message_count()
        st = self.count_tokens(self.get_system_prompt()); db = tt - st; pct = tt/MAX_TOKENS*100
        t = Table(title="Context", show_header=False, border_style="dim", padding=(0,2))
        t.add_column("Key", style="bold"); t.add_column("Value")
        t.add_row("Model", self.model); t.add_row("Messages", str(cnt))
        t.add_row("System tokens", f"{st:,}"); t.add_row("DB tokens", f"{db:,}")
        t.add_row("Total", f"{tt:,}"); t.add_row("Capacity", f"{tt:,}/{MAX_TOKENS:,} ({pct:.0f}%)")
        self.console.print(t)

    # ── Phase 3 commands ──────────────────────────────────────────────────

    def cmd_plan(self, goal: str) -> None:
        if not goal:
            self.console.print("  Usage: /plan <goal>", style="yellow"); return
        tid = self.loop.create_task(goal)
        self.console.print(f"  📋 Planning: {goal}", style="bold cyan")
        clarifications = self.loop.ask_clarifying_questions(goal)
        plan = self.loop.generate_plan(goal, clarifications)
        if plan:
            self.loop.display_plan()
            PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
            PLAN_FILE.write_text(f"# Plan: {goal}\n\n" + "\n".join(
                f"- [ ] {s['description']}" for s in plan))
            self.console.print("\n  Use /go to execute, /edit-plan to modify.", style="dim")
        else:
            self.console.print("  ❌ Plan generation failed.", style="bold red")

    def cmd_go(self) -> None:
        if not self.loop.plan:
            self.console.print("  ⚠ No plan. Use /plan <goal> first.", style="yellow"); return
        if self.loop.status not in ("planning", "paused"):
            self.console.print(f"  ⚠ Task status: {self.loop.status}", style="yellow"); return
        self.loop.display_plan()
        if sys.stdin.isatty():
            confirm = questionary.confirm("  Start execution?", default=True).ask()
            if not confirm: return
        self.loop.run_loop()

    def cmd_task(self, goal: str) -> None:
        if not goal:
            self.console.print("  Usage: /task <goal>", style="yellow"); return
        self.cmd_plan(goal)
        # FIX: check that plan was actually generated
        if not self.loop.plan:
            self.console.print("  ⚠ Plan failed. Use /plan to retry.", style="yellow"); return
        if sys.stdin.isatty():
            confirm = questionary.confirm("  Execute now?", default=True).ask()
            if confirm: self.loop.run_loop()

    def cmd_explore(self) -> None:
        self.console.print("  🔍 Exploring codebase…", style="cyan")
        cwd = os.getcwd()
        result = self.shell_tool.execute_command(
            "find . -type f -not -path '*/node_modules/*' -not -path '*/.git/*' "
            "-not -path '*/__pycache__/*' | head -100", cwd)
        if result.success:
            self.console.print(Panel(result.output[:3000], title="📁 File Tree", border_style="cyan"))
        # RepoMap
        symbols = self.build_repomap()
        if symbols:
            lines = []
            for path, defs in symbols.items():
                lines.append(f"  {path}: {', '.join(defs[:10])}")
            self.console.print(Panel("\n".join(lines[:40]), title="🗺️ Symbols", border_style="dim"))
        # Key files
        for f in ["README.md", "package.json", "requirements.txt", "Cargo.toml", "go.mod"]:
            if Path(f).exists():
                rr = self.file_tools.read_file(f)
                if rr.success:
                    self.console.print(Panel(rr.output[:1000], title=f"📄 {f}", border_style="dim"))
        self.write_note("observation", f"Explored codebase in {cwd}")
        self.console.print("  ✓ Exploration complete.", style="green")

    def cmd_status(self) -> None:
        if not self.loop.plan:
            self.console.print("  No active task.", style="dim"); return
        elapsed = int((time.time() - self.loop.start_time)/60) if self.loop.start_time else 0
        done = sum(1 for s in self.loop.plan if s["status"]=="done")
        t = Table(title="Task Status", show_header=False, border_style="cyan", padding=(0,2))
        t.add_column("Key", style="bold"); t.add_column("Value")
        t.add_row("Status", self.loop.status)
        t.add_row("Progress", f"{done}/{len(self.loop.plan)}")
        t.add_row("Current step", str(self.loop.current_step+1) if self.loop.current_step < len(self.loop.plan) else "done")
        t.add_row("Elapsed", f"{elapsed}m")
        t.add_row("Shell commands", str(self.loop.shell_count))
        t.add_row("Writes", str(self.loop.write_count))
        t.add_row("LLM calls", str(self.loop.llm_calls))
        t.add_row("Auto-approve", "🟢" if self.loop.auto_approve else "🔴")
        t.add_row("Health", f"{self.loop.health_score():.0%}")
        self.console.print(t)
        self.loop.display_plan()

    def cmd_pause(self) -> None:
        if self.loop.status != "active":
            self.console.print("  ⚠ No active task.", style="yellow"); return
        self.loop.pause()

    def cmd_resume(self) -> None:
        self.resume_task()

    def cmd_abort(self) -> None:
        if not self.loop.task_id:
            self.console.print("  ⚠ No task.", style="yellow"); return
        if not sys.stdin.isatty() or questionary.confirm("  Abort task?", default=False).ask():
            self.loop.abort()

    def cmd_notes(self) -> None:
        rows = self.conn.execute(
            "SELECT category, content, timestamp FROM notes ORDER BY id DESC LIMIT 10").fetchall()
        if not rows:
            self.console.print("  No notes yet.", style="dim"); return
        icons = {"success":"✅","error":"❌","observation":"👁","preference":"⚙️","pattern":"🔄"}
        t = Table(title="Recent Notes", header_style="bold cyan", border_style="dim", padding=(0,1))
        t.add_column("", width=2); t.add_column("Category", width=12)
        t.add_column("Content"); t.add_column("Time", width=16)
        for r in rows:
            t.add_row(icons.get(r[0],"📝"), r[0], r[1][:120], str(r[2]) if r[2] else "")
        self.console.print(t)

    def cmd_compact_notes(self) -> None:
        rows = self.conn.execute("SELECT id, category, content FROM notes ORDER BY id ASC").fetchall()
        if len(rows) < 5:
            self.console.print("  ⚠ Need ≥ 5 notes.", style="yellow"); return
        text = "\n".join(f"{r[1]}: {r[2]}" for r in rows)
        try:
            with self.console.status("  [bold yellow]Compacting notes…[/]"):
                summary = self._run_llm_sync([{"role":"user","content":f"Summarise into key facts:\n\n{text}"}])
        except Exception as e:
            self.console.print(f"  ❌ Failed: {e}", style="bold red"); return
        ids = [r[0] for r in rows]; ph = ",".join("?" for _ in ids)
        self.conn.execute(f"DELETE FROM notes WHERE id IN ({ph})", ids)
        self.conn.execute("INSERT INTO notes (category, content) VALUES (?, ?)", ("pattern", summary[:2000]))
        self.conn.commit()
        self.console.print(f"  ✓ Compacted {len(rows)} notes → 1 summary", style="green")

    def cmd_cost(self) -> None:
        if not self.loop.task_id:
            self.console.print("  No task tracked.", style="dim"); return
        self.console.print(f"  💰 {self.loop.cost_report()}", style="cyan")

    def cmd_auto_approve(self) -> None:
        self.loop.auto_approve = not self.loop.auto_approve
        s = "🟢 ON" if self.loop.auto_approve else "🔴 OFF"
        self.console.print(f"  ✓ Auto-approve: {s}", style="green")

    def cmd_health(self) -> None:
        h = self.loop.health_score()
        color = "green" if h > 0.6 else ("yellow" if h > 0.3 else "red")
        t = Table(title="Agent Health", show_header=False, border_style=color, padding=(0,2))
        t.add_column("Key", style="bold"); t.add_column("Value")
        t.add_row("Health score", f"[{color}]{h:.0%}[/{color}]")
        t.add_row("Consecutive failures", str(self.loop.consecutive_failures))
        t.add_row("Shell commands", f"{self.loop.shell_count}/{MAX_SHELL_PER_TASK}")
        t.add_row("Writes", f"{self.loop.write_count}/{MAX_WRITES_PER_TASK}")
        t.add_row("rm commands", f"{self.loop.rm_count}/{MAX_RM_PER_TASK}")
        if h < 0.3: t.add_row("Recommendation", "Break task into smaller steps")
        self.console.print(t)

    def cmd_edit_plan(self) -> None:
        if not self.loop.plan:
            self.console.print("  ⚠ No plan.", style="yellow"); return
        editor = os.environ.get("EDITOR", "nano")
        PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
        PLAN_FILE.write_text("\n".join(f"- [ ] {s['description']}" for s in self.loop.plan))
        try:
            subprocess.run([editor, str(PLAN_FILE)])
            new_plan = []
            for line in PLAN_FILE.read_text().strip().split("\n"):
                m = re.match(r"-\s+\[[ x]\]\s+(.+)", line)
                if m: new_plan.append({"step": len(new_plan)+1, "description": m.group(1), "status": "pending"})
            if new_plan:
                self.loop.plan = new_plan; self.loop._update_task()
                self.console.print(f"  ✓ Plan updated ({len(new_plan)} steps).", style="green")
        except Exception as e:
            self.console.print(f"  ❌ Editor failed: {e}", style="bold red")

    def cmd_checkpoint(self) -> None:
        self.save_checkpoint_manual()

    def cmd_rollback(self) -> None:
        self.rollback()

    def cmd_memory(self) -> None:
        t = Table(title="Project Memory", show_header=False, border_style="cyan", padding=(0,2))
        t.add_column("Key", style="bold"); t.add_column("Value")
        t.add_row("Project memory file", "✓ loaded" if self._project_memory else "none")
        if self._project_memory: t.add_row("Content", self._project_memory[:500])
        for mf in MEMORY_FILES:
            t.add_row(mf, "✓ exists" if Path(mf).exists() else "—")
        self.console.print(t)

    def cmd_panel(self) -> None:
        """Show side panel info: file tree, plan, history."""
        cwd = os.getcwd()
        panels = []
        tree_result = self.shell_tool.execute_command(
            "find . -maxdepth 2 -not -path '*/node_modules/*' -not -path '*/.git/*' | head -40", cwd)
        if tree_result.success:
            panels.append(Panel(tree_result.output[:1500], title="📁 Files", border_style="dim"))
        if self.loop.plan:
            done = sum(1 for s in self.loop.plan if s["status"]=="done")
            plan_text = "\n".join(
                f"{'✅' if s['status']=='done' else '⬜'} {s['step']}. {s['description']}"
                for s in self.loop.plan)
            panels.append(Panel(plan_text, title=f"📋 Plan ({done}/{len(self.loop.plan)})", border_style="cyan"))
        rows = self.conn.execute(
            "SELECT tool_name, command, status FROM tool_history ORDER BY id DESC LIMIT 5").fetchall()
        if rows:
            hist = "\n".join(f"{'✓' if r[2]=='success' else '✗'} {r[0]}: {r[1][:50]}" for r in rows)
            panels.append(Panel(hist, title="📜 History", border_style="dim"))
        # FIX: guard empty panels
        if not panels:
            self.console.print("  No panel data yet.", style="dim"); return
        if _HAS_COLUMNS:
            self.console.print(Columns(panels, width=60))
        else:
            for p in panels:
                self.console.print(p)

    def cmd_update(self) -> None:
        """Update Pincer to the latest version."""
        self.console.print("  🔄 Updating Pincer…", style="cyan")
        repo_dir = PINCER_DIR / "repo"
        if not repo_dir.exists():
            self.console.print("  ⚠ No repo found. Run install script.", style="yellow"); return
        try:
            subprocess.run(["git", "pull"], cwd=str(repo_dir), check=True, capture_output=True)
            venv = PINCER_DIR / "venv"
            venv_pip = str(venv / "bin" / "pip")
            if Path(venv_pip).exists():
                subprocess.run([venv_pip, "install", "-r", str(repo_dir / "requirements.txt"), "--quiet"], check=True)
            self.console.print("  ✓ Updated. Restart to use new version.", style="green")
        except Exception as e:
            self.console.print(f"  ❌ Update failed: {e}", style="bold red")

    def _exit(self) -> None:
        self.console.print("  👋 Goodbye.", style="cyan")
        self.cleanup(); sys.exit(0)

    def handle_command(self, user_input: str) -> None:
        parts = user_input.strip().split(None, 1)
        cmd = parts[0].lower(); arg = parts[1] if len(parts) > 1 else ""
        dispatch_with_arg = {"/plan": self.cmd_plan, "/task": self.cmd_task}
        dispatch_no_arg = {
            "/model": self.cmd_model, "/think": self.cmd_think,
            "/clear": self.cmd_clear, "/compact": self.cmd_compact,
            "/help": self.cmd_help, "/tools": self.cmd_tools,
            "/sandbox": self.cmd_sandbox, "/undo": self.cmd_undo,
            "/context": self.cmd_context, "/go": self.cmd_go,
            "/execute": self.cmd_go, "/explore": self.cmd_explore,
            "/status": self.cmd_status, "/pause": self.cmd_pause,
            "/resume": self.cmd_resume, "/abort": self.cmd_abort,
            "/notes": self.cmd_notes, "/compact-notes": self.cmd_compact_notes,
            "/cost": self.cmd_cost, "/auto-approve": self.cmd_auto_approve,
            "/health": self.cmd_health, "/edit-plan": self.cmd_edit_plan,
            "/rollback": self.cmd_rollback, "/checkpoint": self.cmd_checkpoint,
            "/memory": self.cmd_memory, "/panel": self.cmd_panel,
            "/update": self.cmd_update, "/exit": self._exit,
        }
        if cmd in dispatch_with_arg:
            dispatch_with_arg[cmd](arg)
        elif cmd in dispatch_no_arg:
            dispatch_no_arg[cmd]()
        else:
            self.console.print(f"  Unknown: {cmd}. /help for commands.", style="yellow")

    # ══════════════════════════════════════════════════════════════════════
    #  Cleanup
    # ══════════════════════════════════════════════════════════════════════

    def cleanup(self) -> None:
        if self._loop and not self._loop.is_closed():
            try: self._loop.close()
            except Exception: pass
        if self.conn:
            try: self.conn.close()
            except Exception: pass

    # ══════════════════════════════════════════════════════════════════════
    #  Startup validation
    # ══════════════════════════════════════════════════════════════════════

    def _validate_env(self) -> None:
        if not self.check_ollama_installed():
            self.console.print("❌ Ollama not found.\n   brew install ollama", style="bold red"); sys.exit(1)
        if not self.check_ollama_server():
            self.console.print("⚠ Ollama server not running. Start: ollama serve", style="bold red"); sys.exit(1)
        if not self.check_model_available(self.model):
            self.console.print(f"  ⚠ Pulling {self.model}…", style="yellow"); self.pull_model(self.model)

    def _load_project_memory(self) -> None:
        for mf in MEMORY_FILES:
            p = Path(mf)
            if p.exists():
                self._project_memory = p.read_text(encoding="utf-8", errors="replace")[:4000]
                break

    # ══════════════════════════════════════════════════════════════════════
    #  Main REPL
    # ══════════════════════════════════════════════════════════════════════

    def run(self) -> None:
        PINCER_DIR.mkdir(parents=True, exist_ok=True)
        self.setup_db()
        if self.get_config("user_name") is None:
            self.first_run_wizard()
        else:
            self.load_config()
            self.console.print(BANNER, style="bold cyan")
            mc = self.get_message_count(); tt = self.get_total_tokens()
            self.console.print(
                f"  Model: {self.model} | User: {self.user_name} | "
                f"Lang: {self.preferred_language} | Context: {mc} msgs ({tt/1000:.1f}K tok)",
                style="dim")
        self._validate_env()
        self._load_project_memory()
        # Lazy file completer — scans on first Tab, caches for 30s
        file_completer = LazyFileCompleter()
        self.session = PromptSession(
            history=FileHistory(str(HISTORY_PATH)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=file_completer,
        )
        _orig = signal.getsignal(signal.SIGINT)
        def _sigint(s, f):
            if self._generating: self._generating = False
            elif self.loop.status == "active": self.loop.pause()
            else: raise KeyboardInterrupt
        signal.signal(signal.SIGINT, _sigint)
        self._check_resume_on_startup()
        while True:
            try:
                ps = f" {self.user_name} ❯ " if self.user_name else " ❯ "
                user_input = self.session.prompt(
                    HTML(f"<ansicyan>{ps}</ansicyan>"), bottom_toolbar=self._status_bar)
                stripped = user_input.strip()
                if not stripped: continue
                if stripped.startswith("/"):
                    self.handle_command(stripped); continue
                cls = self.router.classify(stripped)
                if cls.intent == "chat":
                    self.auto_compact_if_needed()
                    self.save_message("user", stripped)
                    msgs = self.get_conversation_messages()
                    notes = self.get_relevant_notes(stripped)
                    if notes: msgs.insert(1, {"role": "system", "content": notes})
                    self.console.print()
                    resp = self.stream_response(msgs)
                    self.console.print()
                    if resp.strip(): self.save_message("assistant", resp)
                else:
                    self.handle_tool_message(stripped, cls.intent, cls.params)
            except KeyboardInterrupt:
                if not self._generating: self.console.print()
                continue
            except EOFError:
                self.console.print("\n  👋 Goodbye.", style="cyan")
                self.cleanup(); sys.exit(0)
        signal.signal(signal.SIGINT, _orig)


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI & ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pincer", description="Pincer — local AI assistant")
    p.add_argument("--model", metavar="M", help="Ollama model")
    p.add_argument("--think", action="store_true", help="Enable thinking")
    p.add_argument("--clear", action="store_true", help="Clear history")
    p.add_argument("--reset", action="store_true", help="Delete all data")
    return p

def main() -> None:
    parser = build_parser(); args = parser.parse_args()
    if args.reset:
        if PINCER_DIR.exists(): shutil.rmtree(PINCER_DIR)
        print("  ✓ Data deleted."); return
    app = PincerApp()
    if args.model: app.model = args.model
    if args.think: app.thinking_mode = True
    try: app.run()
    except KeyboardInterrupt: app.console.print("\n  👋 Goodbye.", style="cyan"); app.cleanup()
    except Exception as e:
        Console(stderr=True).print(f"  ❌ Fatal: {e}", style="bold red"); app.cleanup(); sys.exit(1)

if __name__ == "__main__":
    main()
