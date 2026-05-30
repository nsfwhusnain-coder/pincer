#!/usr/bin/env python3
"""Pincer — local AI coding assistant for your terminal.

Runs entirely on your Mac using Ollama. No API keys needed.
Phase 1: REPL, chat, context manager, slash commands.
Phase 2: File tools, sandboxed shell, permission system, tool history.
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
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
import questionary
import ollama

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

BANNER = r"""
╔══════════════════════════════════╗
║  🤖 PINCER — local AI assistant  ║
╚══════════════════════════════════╝
"""

THINK_TAG_OPEN = "<think"
THINK_TAG_CLOSE = "</think"

# ── Permission constants ──────────────────────────────────────────────────────

SAFE_COMMAND_PREFIXES = [
    "git", "ls", "pwd", "mkdir", "cat", "head", "tail",
    "python", "python3", "pytest", "py.test",
    "node", "npm install", "pip install", "cargo",
    "make", "echo", "wc", "find", "grep", "which",
    "tree", "diff", "sort", "uniq", "tee",
]

ASK_COMMAND_PREFIXES = [
    "rm", "mv", "cp", "chmod", "chown",
    "sudo", "curl", "wget",
    "eval", "exec", "source",
    "bash", "sh", "zsh",
    "pip", "npm", "brew",
    "docker", "kill", "pkill",
]

DENY_PATTERNS = [
    r"^rm\s+-[a-zA-Z]*f[a-zA-Z]*\s+/$",
    r"^rm\s+-[a-zA-Z]*f[a-zA-Z]*\s+/\*",
    r"^sudo\s+rm\s+-[a-zA-Z]*f",
    r"^mkfs",
    r"^dd\s+if=",
    r"curl\s+.*\|\s*(ba)?sh",
    r"^sudo\s+rm\s",
    r":\(\)\{.*;\}\s*;",  # fork bomb
]

# ── Tool-routing keywords ────────────────────────────────────────────────────

FILE_READ_KEYWORDS = {"read", "show", "cat", "open", "display", "view"}
FILE_WRITE_KEYWORDS = {"write", "create", "save", "new file", "add file"}
FILE_EDIT_KEYWORDS = {"edit", "fix", "refactor", "change", "update", "modify", "patch", "rename"}
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


# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolResult:
    """Standardised result from any tool execution."""
    success: bool
    output: str
    tool_name: str
    command: str = ""
    error: str = ""

    @property
    def display(self) -> str:
        if self.success:
            return self.output
        return self.error or "Unknown error"


# ═══════════════════════════════════════════════════════════════════════════════
#  PERMISSION MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class PermissionManager:
    """Classifies shell commands into safe / ask / deny tiers."""

    def __init__(self, allowed_commands: Optional[List[str]] = None):
        self._always_allowed: List[str] = allowed_commands or []

    # ──────────────────────────────────────────────────────────────────────

    def check(self, command: str, cwd: str) -> str:
        """Return 'allow', 'ask', or 'deny'."""
        stripped = command.strip()

        # 1. Deny tier — catastrophic patterns
        for pattern in DENY_PATTERNS:
            if re.search(pattern, stripped, re.IGNORECASE):
                return "deny"

        # 2. Check always-allowed list
        for prefix in self._always_allowed:
            if stripped == prefix or stripped.startswith(prefix + " "):
                return "allow"

        # 3. Safe tier — auto-allow
        for prefix in SAFE_COMMAND_PREFIXES:
            if stripped == prefix or stripped.startswith(prefix + " "):
                # Extra check: safe command outside cwd needs ask
                if self._writes_outside_cwd(stripped, cwd):
                    return "ask"
                return "allow"

        # 4. Ask tier — needs approval
        for prefix in ASK_COMMAND_PREFIXES:
            if stripped == prefix or stripped.startswith(prefix + " "):
                return "ask"

        # 5. Unknown command → ask
        return "ask"

    @staticmethod
    def _writes_outside_cwd(command: str, cwd: str) -> bool:
        """Heuristic: does the command reference paths outside cwd?"""
        parts = command.split()
        for part in parts:
            if part.startswith("/") and not part.startswith(cwd):
                return True
            if part.startswith(".."):
                return True
        return False

    def add_allowed(self, command: str) -> None:
        """Store a command prefix as always-allowed."""
        tokens = command.split()[:2]
        prefix = " ".join(tokens)
        if prefix not in self._always_allowed:
            self._always_allowed.append(prefix)


# ═══════════════════════════════════════════════════════════════════════════════
#  FILE TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

class FileTools:
    """read_file, write_file, edit_file, edit_file_diff."""

    @staticmethod
    def read_file(path: str) -> ToolResult:
        """Return file contents with line numbers."""
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ToolResult(False, "", "read_file", path, f"File not found: {p}")
            if p.is_dir():
                return ToolResult(False, "", "read_file", path, f"Path is a directory: {p}")
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            numbered = "".join(
                f"  {i + 1:>4} | {line}" for i, line in enumerate(lines)
            )
            return ToolResult(True, numbered, "read_file", path)
        except Exception as exc:
            return ToolResult(False, "", "read_file", path, str(exc))

    @staticmethod
    def write_file(path: str, content: str) -> ToolResult:
        """Write content to a file, creating parent directories as needed."""
        try:
            p = Path(path).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return ToolResult(
                True,
                f"✓ Wrote {line_count} lines to {p}",
                "write_file",
                path,
            )
        except Exception as exc:
            return ToolResult(False, "", "write_file", path, str(exc))

    @staticmethod
    def edit_file(path: str, old_string: str, new_string: str) -> ToolResult:
        """Exact string replacement in a file."""
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ToolResult(False, "", "edit_file", path, f"File not found: {p}")

            with open(p, "r", encoding="utf-8") as f:
                content = f.read()

            count = content.count(old_string)
            if count == 0:
                return ToolResult(
                    False, "", "edit_file", path,
                    f"Old string not found in {p}",
                )
            if count > 1:
                return ToolResult(
                    False, "", "edit_file", path,
                    f"Old string appears {count} times — must be unique. Add more context.",
                )

            new_content = content.replace(old_string, new_string, 1)

            with open(p, "w", encoding="utf-8") as f:
                f.write(new_content)

            # Verify
            with open(p, "r", encoding="utf-8") as f:
                verify = f.read()
            if new_string not in verify:
                return ToolResult(
                    False, "", "edit_file", path,
                    "Edit applied but verification failed — file may be corrupted.",
                )

            return ToolResult(
                True,
                f"✓ Replaced 1 occurrence in {p}",
                "edit_file",
                path,
            )
        except Exception as exc:
            return ToolResult(False, "", "edit_file", path, str(exc))

    @staticmethod
    def edit_file_diff(path: str, search: str, replace: str) -> ToolResult:
        """Aider-style SEARCH/REPLACE block edit."""
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ToolResult(False, "", "edit_file", path, f"File not found: {p}")
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()
            if search not in content:
                return ToolResult(
                    False, "", "edit_file", path,
                    "SEARCH block not found in file",
                )
            count = content.count(search)
            if count > 1:
                return ToolResult(
                    False, "", "edit_file", path,
                    f"SEARCH block appears {count} times — must be unique",
                )
            new_content = content.replace(search, replace, 1)
            with open(p, "w", encoding="utf-8") as f:
                f.write(new_content)
            # Verify
            with open(p, "r", encoding="utf-8") as f:
                verify = f.read()
            if replace not in verify:
                return ToolResult(
                    False, "", "edit_file", path,
                    "Edit applied but verification failed",
                )
            return ToolResult(
                True,
                f"✓ Replaced SEARCH block in {p}",
                "edit_file",
                path,
            )
        except Exception as exc:
            return ToolResult(False, "", "edit_file", path, str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
#  SHELL TOOL
# ═══════════════════════════════════════════════════════════════════════════════

class ShellTool:
    """Sandboxed shell command execution via sandbox-exec on macOS."""

    def __init__(self, console: Console):
        self.console = console
        self._sandbox_available = shutil.which("sandbox-exec") is not None

    @property
    def sandbox_available(self) -> bool:
        return self._sandbox_available

    @staticmethod
    def _generate_sandbox_profile(cwd: str, allow_network: bool = False) -> str:
        """Build a Seatbelt profile for sandbox-exec."""
        home = str(Path.home())
        tmp_dir = tempfile.gettempdir()
        net_rule = "(allow network*)" if allow_network else "(deny network*)"
        return (
            f"(version 1)\n"
            f"(deny default)\n"
            f"(allow file-read* file-write* (subpath \"{cwd}\"))\n"
            f"(allow file-read* file-write* (subpath \"{tmp_dir}\"))\n"
            f"(allow file-read* (subpath \"{home}\"))\n"
            f"(allow file-read* (subpath \"/usr\"))\n"
            f"(allow file-read* (subpath \"/Library\"))\n"
            f"(allow file-read* (subpath \"/System\"))\n"
            f"(allow file-read* (subpath \"/opt\"))\n"
            f"(allow process-exec)\n"
            f"{net_rule}\n"
        )

    def execute_command(
        self,
        command: str,
        cwd: str,
        timeout: int = 120,
        allow_network: bool = False,
    ) -> ToolResult:
        """Run a shell command, optionally inside a macOS sandbox."""
        try:
            if self._sandbox_available:
                profile = self._generate_sandbox_profile(cwd, allow_network)
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".sb", delete=False
                ) as pf:
                    pf.write(profile)
                    profile_path = pf.name

                try:
                    result = subprocess.run(
                        ["sandbox-exec", "-f", profile_path, "bash", "-c", command],
                        capture_output=True,
                        text=True,
                        cwd=cwd,
                        timeout=timeout,
                    )
                finally:
                    try:
                        os.unlink(profile_path)
                    except OSError:
                        pass
            else:
                result = subprocess.run(
                    ["bash", "-c", command],
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    timeout=timeout,
                )

            output = result.stdout
            if result.stderr:
                output += ("\n--- stderr ---\n" + result.stderr) if output else result.stderr

            if result.returncode != 0:
                return ToolResult(
                    False,
                    output.strip(),
                    "execute_command",
                    command,
                    f"Exit code {result.returncode}",
                )
            return ToolResult(True, output.strip(), "execute_command", command)

        except subprocess.TimeoutExpired:
            return ToolResult(
                False, "", "execute_command", command,
                f"Command timed out after {timeout}s",
            )
        except Exception as exc:
            return ToolResult(False, "", "execute_command", command, str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

class ToolRouter:
    """Rule-based intent classifier — decides *which* tool to use, never asks the LLM."""

    @dataclass
    class Classification:
        intent: str  # "chat" | "file_read" | "file_write" | "file_edit" | "shell"
        params: Dict[str, str] = field(default_factory=dict)

    # ──────────────────────────────────────────────────────────────────────

    def classify(self, text: str) -> "ToolRouter.Classification":
        """Scan *text* for keywords and return a routing decision."""
        lower = text.lower()
        words = set(re.findall(r"\w+", lower))

        # Count keyword hits per category
        read_hits = len(words & FILE_READ_KEYWORDS)
        write_hits = sum(1 for kw in FILE_WRITE_KEYWORDS if kw in lower)
        edit_hits = len(words & FILE_EDIT_KEYWORDS)
        shell_hits = len(words & SHELL_KEYWORDS)

        has_path = self._extract_file_path(text) is not None
        has_cmd = self._extract_command(text) is not None

        # ── Shell intent ──
        if shell_hits > 0 and has_cmd:
            cmd = self._extract_command(text) or ""
            return self.Classification("shell", {"command": cmd})

        # ── File-edit intent ──
        if edit_hits > 0 and has_path:
            path = self._extract_file_path(text) or ""
            return self.Classification("file_edit", {"path": path})

        # ── File-write intent ──
        if write_hits > 0 and has_path:
            path = self._extract_file_path(text) or ""
            description = self._extract_write_description(text, path)
            return self.Classification(
                "file_write", {"path": path, "description": description}
            )

        # ── File-read intent ──
        if read_hits > 0 and has_path:
            path = self._extract_file_path(text) or ""
            return self.Classification("file_read", {"path": path})

        # ── Shell with keyword but no explicit command ──
        if shell_hits > 0:
            cmd = self._extract_command(text) or ""
            if cmd:
                return self.Classification("shell", {"command": cmd})

        # ── Fallback: chat ──
        return self.Classification("chat")

    # ── Parameter extraction helpers ──────────────────────────────────────

    @staticmethod
    def _extract_file_path(text: str) -> Optional[str]:
        """Heuristically extract a file path from natural language."""
        # 1. Quoted strings
        match = re.search(r'["\']([^"\']+)["\']', text)
        if match:
            return match.group(1).strip()

        # 2. Words with recognised extensions
        for word in re.findall(r"[\w./\-\u0021-\u002F]+", text):
            if Path(word).suffix.lower() in FILE_EXTENSIONS:
                return word

        # 3. After prepositions: "file X", "in X", "to X"
        match = re.search(
            r"(?:file|in|to|at)\s+([^\s,;.!?]+)", text, re.IGNORECASE
        )
        if match:
            candidate = match.group(1).strip("\"'")
            if candidate and candidate.lower() not in {
                "a", "the", "this", "that", "it", "my", "our",
            }:
                return candidate

        return None

    @staticmethod
    def _extract_command(text: str) -> Optional[str]:
        """Extract a shell command from natural language."""
        lower = text.lower()

        # "run <cmd>", "execute <cmd>"
        for kw in ("run", "execute"):
            match = re.search(rf"\b{kw}\s+(.+)", lower)
            if match:
                return match.group(1).strip()

        # "test" → "pytest"
        if re.search(r"\brun\s+tests?\b", lower) or re.search(
            r"\btest\s+it\b", lower
        ):
            return "pytest"

        # Direct command patterns
        for cmd_prefix in (
            "git", "ls", "mkdir", "pip", "npm", "cargo",
            "make", "pytest", "python", "python3", "node",
            "docker", "brew", "curl", "wget",
        ):
            match = re.search(rf"\b({cmd_prefix}\s+.+)", lower)
            if match:
                return match.group(1).strip()
            if re.search(rf"\b{cmd_prefix}\b", lower):
                return cmd_prefix

        # "delete/remove X" → "rm X"
        match = re.search(r"\b(?:delete|remove)\s+(.+)", lower)
        if match:
            target = match.group(1).strip()
            if target in ("everything", "all", "*"):
                return "rm -rf ."
            return f"rm -rf {target}"

        # "build" → "make" or "npm run build"
        if re.search(r"\bbuild\b", lower):
            return "make"

        # "install X" → "pip install X" (heuristic)
        match = re.search(r"\binstall\s+(.+)", lower)
        if match:
            pkg = match.group(1).strip()
            return f"pip install {pkg}"

        return None

    @staticmethod
    def _extract_write_description(text: str, path: str) -> str:
        """Extract the description of what to write, minus the path itself."""
        desc = text
        for pattern in (
            r"^write\s+",
            r"^create\s+(?:a\s+)?(?:new\s+)?(?:file\s+)?",
            r"^save\s+",
            r"^add\s+(?:a\s+)?(?:new\s+)?(?:file\s+)?",
        ):
            desc = re.sub(pattern, "", desc, flags=re.IGNORECASE).strip()
        if path:
            desc = desc.replace(path, "").strip()
        desc = re.sub(r"\s+(to|in|at)\s*$", "", desc, flags=re.IGNORECASE).strip()
        desc = re.sub(r"\s+", " ", desc).strip(" ,.:;!")
        return desc or f"content for {path}"


# ═══════════════════════════════════════════════════════════════════════════════
#  APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════


class PincerApp:
    """Main Pincer application — owns DB, config, REPL, tools, and Ollama calls."""

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

        # Phase 2 components
        self.router = ToolRouter()
        self.file_tools = FileTools()
        self.shell_tool = ShellTool(self.console)
        self.permission_manager = PermissionManager()
        self._vec_available: bool = False
        self._embedding_model_available: bool = False

    # ══════════════════════════════════════════════════════════════════════
    #  Ollama API response helpers
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _parse_models(resp) -> List[Dict]:
        if hasattr(resp, "models"):
            return [{"model": m.model, "size": m.size} for m in resp.models]
        return resp.get("models", [])

    @staticmethod
    def _get_attr(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _get_chunk_content(chunk) -> str:
        if hasattr(chunk, "message"):
            msg = chunk.message
            if hasattr(msg, "content"):
                return msg.content or ""
        if isinstance(chunk, dict):
            return chunk.get("message", {}).get("content", "") or ""
        return ""

    @staticmethod
    def _get_chat_response_content(response) -> str:
        if hasattr(response, "message"):
            msg = response.message
            if hasattr(msg, "content"):
                return msg.content or ""
        if isinstance(response, dict):
            return response.get("message", {}).get("content", "") or ""
        return ""

    @staticmethod
    def _get_embedding(resp) -> List[float]:
        """Extract embedding vector from ollama.embeddings response.

        Handles both dict and object-style responses from different
        ollama library versions.
        """
        if isinstance(resp, dict):
            return resp.get("embedding", [])
        return getattr(resp, "embedding", [])

    # ══════════════════════════════════════════════════════════════════════
    #  Token counting
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def count_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // CHARS_PER_TOKEN)

    # ══════════════════════════════════════════════════════════════════════
    #  Database
    # ══════════════════════════════════════════════════════════════════════

    def setup_db(self) -> None:
        PINCER_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.execute("PRAGMA journal_mode=WAL")

        # Phase 1 tables
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS user_info (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT CHECK(role IN ('system', 'user', 'assistant', 'summary')),
                content TEXT,
                tokens INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Phase 2: tool history
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT,
                command TEXT,
                status TEXT,
                output TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.commit()

        # Try sqlite-vec for semantic search
        self._setup_vec()

        # Load always-allowed commands from DB
        self._load_allowed_commands()

    # ── sqlite-vec setup ──────────────────────────────────────────────────

    def _setup_vec(self) -> None:
        """Try to initialise sqlite-vec for semantic tool-history search."""
        self._vec_available = False
        try:
            import sqlite_vec
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            self.conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS tool_history_vec
                USING vec0(
                    id INTEGER PRIMARY KEY,
                    embedding float[{EMBEDDING_DIM}]
                )
            """)
            self.conn.commit()
            self._vec_available = True
        except Exception:
            self._vec_available = False

        # Check if embedding model is available
        self._embedding_model_available = self.check_model_available(
            EMBEDDING_MODEL
        )

    # ── Allowed-commands persistence ──────────────────────────────────────

    def _load_allowed_commands(self) -> None:
        raw = self.get_config("allowed_commands")
        if raw:
            try:
                self.permission_manager._always_allowed = json.loads(raw)
            except json.JSONDecodeError:
                pass

    def _save_allowed_commands(self) -> None:
        self.set_config(
            "allowed_commands",
            json.dumps(self.permission_manager._always_allowed),
        )

    # ── Config helpers ────────────────────────────────────────────────────

    def get_config(self, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM user_info WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_config(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO user_info (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    def load_config(self) -> None:
        self.user_name = self.get_config("user_name") or ""
        self.preferred_language = self.get_config("preferred_language") or "python"
        self.model = self.get_config("model") or DEFAULT_MODEL
        self.thinking_mode = self.get_config("thinking_mode") == "on"

    # ══════════════════════════════════════════════════════════════════════
    #  Tool history
    # ══════════════════════════════════════════════════════════════════════

    def log_tool_history(
        self, tool_name: str, command: str, result: ToolResult
    ) -> None:
        """Record a tool execution in the database."""
        status = (
            "denied"
            if "denied" in result.error.lower() or "deny" in result.error.lower()
            else ("success" if result.success else "failure")
        )
        output = result.output[:2000] if result.success else result.error[:2000]

        cursor = self.conn.execute(
            "INSERT INTO tool_history (tool_name, command, status, output) VALUES (?, ?, ?, ?)",
            (tool_name, command[:500], status, output),
        )
        row_id = cursor.lastrowid
        self.conn.commit()

        # Try to store embedding
        if self._vec_available and self._embedding_model_available:
            self._store_embedding(row_id, f"{tool_name} {command} {output}")

    def _store_embedding(self, row_id: int, text: str) -> None:
        """Generate and store an embedding for a tool-history entry."""
        try:
            resp = ollama.embeddings(model=EMBEDDING_MODEL, prompt=text)
            embedding = self._get_embedding(resp)
            if embedding and len(embedding) == EMBEDDING_DIM:
                import struct
                vec_bytes = struct.pack(f"{len(embedding)}f", *embedding)
                self.conn.execute(
                    "INSERT INTO tool_history_vec (id, embedding) VALUES (?, ?)",
                    (row_id, vec_bytes),
                )
                self.conn.commit()
        except Exception:
            pass

    def get_relevant_tool_history(self, query: str, limit: int = 3) -> str:
        """Return a condensed string of relevant past tool executions."""
        if not query:
            return ""

        # Try semantic search first
        if self._vec_available and self._embedding_model_available:
            results = self._semantic_search(query, limit)
            if results:
                return self._format_history(results)

        # Fallback: keyword search
        results = self._keyword_search(query, limit)
        if results:
            return self._format_history(results)

        return ""

    def _semantic_search(self, query: str, limit: int) -> List[Dict]:
        try:
            resp = ollama.embeddings(model=EMBEDDING_MODEL, prompt=query)
            embedding = self._get_embedding(resp)
            if not embedding or len(embedding) != EMBEDDING_DIM:
                return []
            import struct
            vec_bytes = struct.pack(f"{len(embedding)}f", *embedding)
            rows = self.conn.execute("""
                SELECT th.tool_name, th.command, th.status, th.output
                FROM tool_history th
                JOIN tool_history_vec vth ON th.id = vth.id
                WHERE vth.embedding MATCH ?
                ORDER BY vth.distance
                LIMIT ?
            """, (vec_bytes, limit)).fetchall()
            return [
                {"tool_name": r[0], "command": r[1], "status": r[2], "output": r[3]}
                for r in rows
            ]
        except Exception:
            return []

    def _keyword_search(self, query: str, limit: int) -> List[Dict]:
        """Simple LIKE search on tool_history."""
        words = re.findall(r"\w+", query)
        if not words:
            return []
        conditions = " OR ".join("command LIKE ?" for _ in words)
        params = [f"%{w}%" for w in words] + [limit]
        rows = self.conn.execute(f"""
            SELECT tool_name, command, status, output
            FROM tool_history
            WHERE {conditions}
            ORDER BY id DESC
            LIMIT ?
        """, params).fetchall()
        return [
            {"tool_name": r[0], "command": r[1], "status": r[2], "output": r[3]}
            for r in rows
        ]

    @staticmethod
    def _format_history(entries: List[Dict]) -> str:
        lines = ["[Relevant tool history]"]
        for e in entries:
            status_icon = "✓" if e["status"] == "success" else "✗"
            output_snippet = (e["output"] or "")[:200]
            lines.append(
                f"  {status_icon} {e['tool_name']}: {e['command']} "
                f"→ {e['status']} | {output_snippet}"
            )
        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════════
    #  Ollama helpers
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def check_ollama_installed() -> bool:
        return shutil.which("ollama") is not None

    @staticmethod
    def check_ollama_server() -> bool:
        try:
            ollama.list()
            return True
        except Exception:
            return False

    def check_model_available(self, model_name: str) -> bool:
        try:
            resp = ollama.list()
            models = self._parse_models(resp)
            for m in models:
                name = m.get("model", "")
                if name == model_name or name.startswith(
                    model_name.split(":")[0] + ":"
                ):
                    return True
            return False
        except Exception:
            return False

    def pull_model(self, model_name: str) -> None:
        self.console.print(f"  Pulling {model_name}…", style="yellow")
        try:
            stream = ollama.pull(model_name, stream=True)
            for chunk in stream:
                status = self._get_attr(chunk, "status", "")
                total = self._get_attr(chunk, "total", 0)
                completed = self._get_attr(chunk, "completed", 0)
                if total and total > 0:
                    pct = int(completed / total * 100)
                    bar_w = 20
                    filled = int(bar_w * completed / total)
                    bar = "█" * filled + "░" * (bar_w - filled)
                    sys.stdout.write(f"\r  [{bar}] {pct}% ")
                    sys.stdout.flush()
                elif "success" in status:
                    break
            sys.stdout.write(f"\r  ✓ {model_name} pulled.          \n")
            sys.stdout.flush()
        except Exception as exc:
            self.error_console.print(f"\n  ❌ Failed to pull {model_name}: {exc}")
            sys.exit(1)

    # ══════════════════════════════════════════════════════════════════════
    #  First-run wizard
    # ══════════════════════════════════════════════════════════════════════

    def first_run_wizard(self) -> None:
        self.console.print(BANNER, style="bold cyan")

        if not self.check_ollama_installed():
            self.console.print(
                "❌ Ollama not found. Install it first:\n"
                "   brew install ollama\n"
                "   # or download from https://ollama.com",
                style="bold red",
            )
            sys.exit(1)
        self.console.print("  ✓ Ollama found.", style="green")

        if not self.check_ollama_server():
            self.console.print(
                "❌ Ollama server is not running. Start it first:\n"
                "   ollama serve   # in a separate terminal\n"
                "   # or open the Ollama app",
                style="bold red",
            )
            sys.exit(1)
        self.console.print("  ✓ Ollama server running.", style="green")

        # Pull chat model
        if not self.check_model_available(self.model):
            self.pull_model(self.model)
        else:
            self.console.print(f"  ✓ {self.model} available.", style="green")

        # Pull embedding model
        if not self.check_model_available(EMBEDDING_MODEL):
            self.console.print(
                f"  ⚠ Pulling embedding model {EMBEDDING_MODEL}…", style="yellow"
            )
            self.pull_model(EMBEDDING_MODEL)
        else:
            self.console.print(
                f"  ✓ {EMBEDDING_MODEL} available.", style="green"
            )

        # --- User name (with sensible default) ---
        name = questionary.text("  Your name?", default="user").ask()
        if name is None or not name.strip():
            name = "user"
        self.user_name = name.strip()
        self.set_config("user_name", self.user_name)

        # --- Preferred language ---
        lang = questionary.select(
            "  Preferred coding language?",
            choices=[
                "python", "javascript", "typescript", "rust",
                "go", "java", "c", "cpp", "ruby", "other",
            ],
            default="python",
        ).ask()
        if lang is None:
            lang = "python"
        self.preferred_language = lang
        self.set_config("preferred_language", self.preferred_language)

        self.set_config("model", self.model)
        self.set_config("thinking_mode", "off")

        self.console.print(
            "\n  ✓ Ready. Type /help for commands or just start chatting.\n",
            style="bold green",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  System prompt
    # ══════════════════════════════════════════════════════════════════════

    def get_system_prompt(self) -> str:
        prompt = (
            f"You are Pincer, a helpful coding assistant running locally on the user's Mac.\n"
            f"User: {self.user_name} | Preferred language: {self.preferred_language}\n"
            f"Be concise. Use markdown for code blocks.\n"
            f"You have access to file tools (read, write, edit) and shell execution.\n"
            f"When tool results are shown in [Tool: …] blocks, analyse them and respond helpfully.\n"
            f"If a tool failed, suggest a fix. If a test failed, explain why."
        )
        if self.thinking_mode:
            prompt += "\nThink step by step before responding."
        return prompt

    # ══════════════════════════════════════════════════════════════════════
    #  Context window management
    # ══════════════════════════════════════════════════════════════════════

    def get_total_tokens(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM conversation"
        ).fetchone()
        db_tokens = row[0] if row else 0
        system_tokens = self.count_tokens(self.get_system_prompt())
        return db_tokens + system_tokens

    def get_message_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM conversation").fetchone()
        return row[0] if row else 0

    def save_message(self, role: str, content: str) -> None:
        tokens = self.count_tokens(content)
        self.conn.execute(
            "INSERT INTO conversation (role, content, tokens) VALUES (?, ?, ?)",
            (role, content, tokens),
        )
        self.conn.commit()

    def get_conversation_messages(self) -> List[Dict[str, str]]:
        rows = self.conn.execute(
            "SELECT role, content FROM conversation ORDER BY id ASC"
        ).fetchall()
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self.get_system_prompt()}
        ]
        for role, content in rows:
            api_role = "system" if role == "summary" else role
            messages.append({"role": api_role, "content": content})
        return messages

    # ── Compaction ────────────────────────────────────────────────────────

    def compact_context(self, manual: bool = False) -> None:
        rows = self.conn.execute(
            "SELECT id, role, content FROM conversation ORDER BY id ASC"
        ).fetchall()
        if len(rows) < 4:
            if manual:
                self.console.print(
                    "  ⚠ Not enough messages to compact (need ≥ 4).",
                    style="yellow",
                )
            return

        split_point = len(rows) // 2
        old_rows = rows[:split_point]
        conv_text = ""
        for _, role, content in old_rows:
            conv_text += f"{role}: {content}\n\n"

        summary_prompt = (
            "Summarise the following conversation into a single concise paragraph "
            "that preserves all important context, decisions, code snippets, and key "
            "details. Be factual and thorough.\n\n" + conv_text
        )

        try:
            with self.console.status("  [bold yellow]Compacting context…[/]"):
                loop = asyncio.new_event_loop()
                try:
                    response = loop.run_until_complete(
                        loop.run_in_executor(
                            None,
                            lambda: ollama.chat(
                                model=self.model,
                                messages=[{"role": "user", "content": summary_prompt}],
                                stream=False,
                            ),
                        )
                    )
                finally:
                    loop.close()
            summary = self._get_chat_response_content(response)
        except Exception as exc:
            self.console.print(f"  ❌ Compaction failed: {exc}", style="bold red")
            return

        old_ids = [r[0] for r in old_rows]
        placeholders = ",".join("?" for _ in old_ids)
        self.conn.execute(
            f"DELETE FROM conversation WHERE id IN ({placeholders})", old_ids
        )
        summary_tokens = self.count_tokens(summary)
        self.conn.execute(
            "INSERT INTO conversation (role, content, tokens) VALUES (?, ?, ?)",
            ("summary", summary, summary_tokens),
        )
        self.conn.commit()
        self.console.print(
            f"  ✓ Compacted {len(old_rows)} messages → 1 summary", style="green"
        )

    def auto_compact_if_needed(self) -> None:
        total_tokens = self.get_total_tokens()
        if total_tokens > COMPACT_THRESHOLD:
            self.console.print(
                f"  ⚡ Context at {total_tokens / 1000:.1f}K tokens — auto-compacting…",
                style="yellow",
            )
            self.compact_context()
        total_tokens = self.get_total_tokens()
        iterations = 0
        while total_tokens > MAX_TOKENS:
            iterations += 1
            if iterations > 200:
                break
            row = self.conn.execute(
                "SELECT id FROM conversation WHERE role = 'user' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if not row:
                oldest = self.conn.execute(
                    "SELECT id FROM conversation ORDER BY id ASC LIMIT 1"
                ).fetchone()
                if not oldest:
                    break
                self.conn.execute("DELETE FROM conversation WHERE id = ?", (oldest[0],))
            else:
                user_id = row[0]
                self.conn.execute("DELETE FROM conversation WHERE id = ?", (user_id,))
                next_asst = self.conn.execute(
                    "SELECT id FROM conversation "
                    "WHERE id > ? AND role = 'assistant' "
                    "ORDER BY id ASC LIMIT 1",
                    (user_id,),
                ).fetchone()
                if next_asst:
                    self.conn.execute(
                        "DELETE FROM conversation WHERE id = ?", (next_asst[0],)
                    )
            self.conn.commit()
            total_tokens = self.get_total_tokens()

    # ══════════════════════════════════════════════════════════════════════
    #  Thinking-tag handling
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def strip_thinking_tags(text: str) -> str:
        return re.sub(r"<think[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL).strip()

    @staticmethod
    def _partial_tag_len(buffer: str, tag: str) -> int:
        for i in range(1, min(len(tag) + 1, len(buffer) + 1)):
            if buffer[-i:] == tag[:i]:
                return i
        return 0

    # ══════════════════════════════════════════════════════════════════════
    #  Streaming response
    # ══════════════════════════════════════════════════════════════════════

    def stream_response(self, messages: List[Dict[str, str]]) -> str:
        full_response = ""
        in_think = False
        thinking_displayed = False
        buf = ""

        try:
            self._generating = True
            stream = ollama.chat(model=self.model, messages=messages, stream=True)

            for chunk in stream:
                if not self._generating:
                    break
                if self._get_attr(chunk, "done", False):
                    break
                token = self._get_chunk_content(chunk)
                if not token:
                    continue

                full_response += token

                if not self.thinking_mode:
                    self.console.print(token, end="")
                    continue

                buf += token
                changed = True
                while changed:
                    changed = False
                    if not in_think:
                        open_idx = buf.find(THINK_TAG_OPEN)
                        if open_idx != -1:
                            if open_idx > 0:
                                self.console.print(buf[:open_idx], end="")
                            after = buf[open_idx + len(THINK_TAG_OPEN):]
                            gt = after.find(">")
                            if gt != -1:
                                buf = after[gt + 1:]
                                in_think = True
                                if not thinking_displayed:
                                    self.console.print("  ● Thinking…", style="yellow")
                                    thinking_displayed = True
                                changed = True
                            else:
                                buf = buf[open_idx:]
                        else:
                            partial = self._partial_tag_len(buf, THINK_TAG_OPEN)
                            safe = buf[: len(buf) - partial] if partial else buf
                            if safe:
                                self.console.print(safe, end="")
                            buf = buf[len(safe):]
                    else:
                        close_idx = buf.find(THINK_TAG_CLOSE)
                        if close_idx != -1:
                            after = buf[close_idx + len(THINK_TAG_CLOSE):]
                            gt = after.find(">")
                            if gt != -1:
                                buf = after[gt + 1:]
                                in_think = False
                                changed = True
                            else:
                                buf = buf[close_idx:]
                        else:
                            partial = self._partial_tag_len(buf, THINK_TAG_CLOSE)
                            if partial:
                                buf = buf[-partial:]
                            else:
                                buf = ""

            if buf and not in_think:
                self.console.print(buf, end="")
            self.console.print()

        except KeyboardInterrupt:
            self._generating = False
            self.console.print("\n  ⏹ Generation stopped.", style="yellow")
        except Exception as exc:
            self._generating = False
            self.console.print(
                f"\n  ❌ Error generating response: {exc}", style="bold red"
            )

        self._generating = False
        return full_response

    # ══════════════════════════════════════════════════════════════════════
    #  Status bar
    # ══════════════════════════════════════════════════════════════════════

    def get_status_bar_text(self) -> str:
        total = self.get_total_tokens()
        ctx = f"{total / 1000:.1f}K"
        think = "on" if self.thinking_mode else "off"
        sandbox = "🔒" if self.shell_tool.sandbox_available else "🔓"
        return f" {self.model} | thinking:{think} | ctx: {ctx}/12K | {sandbox} sandbox"

    def _status_bar(self) -> HTML:
        return HTML(
            f"<style bg='ansiblack' fg='ansiwhite'>"
            f"{self.get_status_bar_text()}</style>"
        )

    # ══════════════════════════════════════════════════════════════════════
    #  Git auto-commits
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _auto_commit(message: str) -> None:
        """Auto-commit changes to Git if in a repo."""
        if not Path(".git").exists():
            return
        try:
            subprocess.run(
                ["git", "add", "-A"], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "commit", "-m", message],
                check=True,
                capture_output=True,
            )
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    #  TOOL EXECUTION FLOW (Phase 2)
    # ══════════════════════════════════════════════════════════════════════

    def _generate_file_content(self, description: str, path: str) -> str:
        """Ask the LLM to produce file content (content generation only)."""
        prompt = (
            f"Generate the complete content for the file '{path}' "
            f"based on this request: {description}\n\n"
            f"Output ONLY the file content. "
            f"Do NOT wrap it in markdown code fences. "
            f"Start with the actual first line of the file."
        )
        try:
            with self.console.status("  [bold yellow]Generating file content…[/]"):
                loop = asyncio.new_event_loop()
                try:
                    response = loop.run_until_complete(
                        loop.run_in_executor(
                            None,
                            lambda: ollama.chat(
                                model=self.model,
                                messages=[{"role": "user", "content": prompt}],
                                stream=False,
                            ),
                        )
                    )
                finally:
                    loop.close()
            content = self._get_chat_response_content(response)
            # Strip markdown fences that 8B models love to add
            content = re.sub(r"^```[\w]*\n", "", content)
            content = re.sub(r"\n```$", "", content)
            return content.strip() + "\n"
        except Exception as exc:
            self.console.print(
                f"  ❌ Content generation failed: {exc}", style="bold red"
            )
            return ""

    def _generate_edit(
        self, user_request: str, path: str, current_content: str
    ) -> Optional[Tuple[str, str]]:
        """Ask the LLM for an old_string / new_string pair (content generation only)."""
        prompt = (
            f"The user wants to modify the file '{path}'.\n\n"
            f"Current content:\n{current_content}\n\n"
            f"User request: {user_request}\n\n"
            f"Provide the edit in this EXACT format — no extra text:\n"
            f"<<<OLD>>>\n"
            f"exact lines from the file to replace\n"
            f"<<<NEW>>>\n"
            f"replacement lines\n"
        )
        try:
            with self.console.status("  [bold yellow]Generating edit…[/]"):
                loop = asyncio.new_event_loop()
                try:
                    response = loop.run_until_complete(
                        loop.run_in_executor(
                            None,
                            lambda: ollama.chat(
                                model=self.model,
                                messages=[{"role": "user", "content": prompt}],
                                stream=False,
                            ),
                        )
                    )
                finally:
                    loop.close()
            text = self._get_chat_response_content(response)
            return self._parse_edit_response(text)
        except Exception as exc:
            self.console.print(
                f"  ❌ Edit generation failed: {exc}", style="bold red"
            )
            return None

    @staticmethod
    def _parse_edit_response(text: str) -> Optional[Tuple[str, str]]:
        """Parse <<<OLD>>>…<<<NEW>>>… from LLM output."""
        match = re.search(
            r"<<<OLD>>>\s*\n(.*?)<<<NEW>>>\s*\n(.*)",
            text,
            re.DOTALL,
        )
        if not match:
            return None
        old = match.group(1).rstrip("\n")
        new = match.group(2).rstrip("\n")
        # Strip trailing fence if model added one
        new = re.sub(r"\n```\s*$", "", new)
        return old, new

    def _prompt_approval(self, command: str, cwd: str, risk: str) -> str:
        """Interactive approval prompt. Returns 'allow', 'deny', or 'edit:…'."""
        risk_colors = {"safe": "green", "ask": "yellow", "deny": "red"}
        risk_icons = {"safe": "✅", "ask": "⚠️", "deny": "🚫"}
        color = risk_colors.get(risk, "yellow")
        icon = risk_icons.get(risk, "⚠️")

        self.console.print(
            Panel(
                f"[bold]Command:[/bold]  {command}\n"
                f"[bold]Directory:[/bold] {cwd}\n"
                f"[bold]Risk level:[/bold] [{color}]{icon} {risk.upper()}[/{color}]",
                title="⚡ Command Approval",
                border_style=color,
            )
        )

        choice = questionary.select(
            "  Choose:",
            choices=["Allow once", "Allow always", "Deny", "Edit command"],
        ).ask()

        if choice is None or choice == "Deny":
            return "deny"
        if choice == "Allow once":
            return "allow"
        if choice == "Allow always":
            self.permission_manager.add_allowed(command)
            self._save_allowed_commands()
            return "allow"
        if choice == "Edit command":
            edited = questionary.text("  Edit command:", default=command).ask()
            if edited and edited.strip():
                return f"edit:{edited.strip()}"
            return "deny"
        return "deny"

    # ── Main tool dispatcher ──────────────────────────────────────────────

    def handle_tool_message(
        self, user_msg: str, intent: str, params: Dict[str, str]
    ) -> None:
        """Execute a tool, log it, inject the result, and let the LLM respond."""
        self.auto_compact_if_needed()
        self.save_message("user", user_msg)

        # Retrieve relevant tool history
        history_ctx = self.get_relevant_tool_history(user_msg)

        cwd = os.getcwd()

        # ── file_read ─────────────────────────────────────────────────────
        if intent == "file_read":
            path = params.get("path", "")
            if not path:
                self.console.print("  ⚠ No file path detected.", style="yellow")
                return

            result = self.file_tools.read_file(path)
            self.log_tool_history("read_file", path, result)

            if result.success:
                tool_msg = (
                    f"[Tool: read_file('{path}')]\n{result.output}\n[/Tool: success]"
                )
            else:
                tool_msg = (
                    f"[Tool: read_file('{path}') FAILED]\n{result.error}\n"
                    f"[/Tool: failure]"
                )

            self.save_message("system", tool_msg)

        # ── file_write ────────────────────────────────────────────────────
        elif intent == "file_write":
            path = params.get("path", "")
            description = params.get("description", "")
            if not path:
                self.console.print("  ⚠ No file path detected.", style="yellow")
                return

            # Generate content via LLM if not provided
            content = params.get("content", "")
            if not content:
                content = self._generate_file_content(description, path)
                if not content:
                    self.save_message(
                        "system",
                        f"[Tool: write_file('{path}') FAILED]\n"
                        f"Content generation returned empty.\n[/Tool: failure]",
                    )
                else:
                    result = self.file_tools.write_file(path, content)
                    self.log_tool_history("write_file", path, result)
                    if result.success:
                        self._auto_commit(f"write: {path}")
                        tool_msg = (
                            f"[Tool: write_file('{path}')]\n{result.output}\n"
                            f"[/Tool: success]"
                        )
                    else:
                        tool_msg = (
                            f"[Tool: write_file('{path}') FAILED]\n{result.error}\n"
                            f"[/Tool: failure]"
                        )
                    self.save_message("system", tool_msg)
            else:
                result = self.file_tools.write_file(path, content)
                self.log_tool_history("write_file", path, result)
                if result.success:
                    self._auto_commit(f"write: {path}")
                    tool_msg = (
                        f"[Tool: write_file('{path}')]\n{result.output}\n"
                        f"[/Tool: success]"
                    )
                else:
                    tool_msg = (
                        f"[Tool: write_file('{path}') FAILED]\n{result.error}\n"
                        f"[/Tool: failure]"
                    )
                self.save_message("system", tool_msg)

        # ── file_edit ─────────────────────────────────────────────────────
        elif intent == "file_edit":
            path = params.get("path", "")
            if not path:
                self.console.print("  ⚠ No file path detected.", style="yellow")
                return

            old_string = params.get("old_string", "")
            new_string = params.get("new_string", "")

            # If no explicit edit provided, use LLM to generate it
            if not old_string or not new_string:
                read_result = self.file_tools.read_file(path)
                if not read_result.success:
                    self.log_tool_history("edit_file", path, read_result)
                    self.save_message(
                        "system",
                        f"[Tool: edit_file('{path}') FAILED]\n"
                        f"Cannot read file: {read_result.error}\n[/Tool: failure]",
                    )
                else:
                    edit_pair = self._generate_edit(
                        user_msg, path, read_result.output
                    )
                    if edit_pair:
                        old_string, new_string = edit_pair
                        result = self.file_tools.edit_file(
                            path, old_string, new_string
                        )
                        self.log_tool_history("edit_file", path, result)
                        if result.success:
                            self._auto_commit(f"edit: {path}")
                            tool_msg = (
                                f"[Tool: edit_file('{path}')]\n{result.output}\n"
                                f"[/Tool: success]"
                            )
                        else:
                            tool_msg = (
                                f"[Tool: edit_file('{path}') FAILED]\n"
                                f"{result.error}\n[/Tool: failure]"
                            )
                        self.save_message("system", tool_msg)
                    else:
                        self.save_message(
                            "system",
                            f"[Tool: edit_file('{path}') FAILED]\n"
                            f"Could not parse edit from LLM response.\n"
                            f"[/Tool: failure]",
                        )
            else:
                result = self.file_tools.edit_file(path, old_string, new_string)
                self.log_tool_history("edit_file", path, result)
                if result.success:
                    self._auto_commit(f"edit: {path}")
                    tool_msg = (
                        f"[Tool: edit_file('{path}')]\n{result.output}\n"
                        f"[/Tool: success]"
                    )
                else:
                    tool_msg = (
                        f"[Tool: edit_file('{path}') FAILED]\n{result.error}\n"
                        f"[/Tool: failure]"
                    )
                self.save_message("system", tool_msg)

        # ── shell ─────────────────────────────────────────────────────────
        elif intent == "shell":
            command = params.get("command", "")
            if not command:
                self.console.print("  ⚠ No command detected.", style="yellow")
                return

            risk = self.permission_manager.check(command, cwd)

            if risk == "deny":
                self.console.print(
                    f"  🚫 Command denied for safety: {command}", style="bold red"
                )
                self.log_tool_history(
                    "execute_command",
                    command,
                    ToolResult(
                        False, "", "execute_command", command, "Denied for safety"
                    ),
                )
                self.save_message(
                    "system",
                    f"[Tool: execute_command DENIED]\n"
                    f"Command: {command}\n"
                    f"Reason: Blocked by safety policy.\n[/Tool: denied]",
                )

            elif risk == "ask":
                approval = self._prompt_approval(command, cwd, "ask")

                if approval == "deny":
                    self.log_tool_history(
                        "execute_command",
                        command,
                        ToolResult(
                            False, "", "execute_command", command, "User denied"
                        ),
                    )
                    self.save_message(
                        "system",
                        f"[Tool: execute_command DENIED]\n"
                        f"Command: {command}\n"
                        f"Reason: User denied.\n[/Tool: denied]",
                    )

                elif approval.startswith("edit:"):
                    edited_cmd = approval[5:]
                    # Re-check the edited command
                    new_risk = self.permission_manager.check(edited_cmd, cwd)
                    if new_risk == "deny":
                        self.console.print(
                            f"  🚫 Edited command still denied: {edited_cmd}",
                            style="bold red",
                        )
                        self.save_message(
                            "system",
                            f"[Tool: execute_command DENIED]\n"
                            f"Command: {edited_cmd}\n[/Tool: denied]",
                        )
                    else:
                        allow_net = self._command_needs_network(edited_cmd)
                        result = self.shell_tool.execute_command(
                            edited_cmd, cwd, allow_network=allow_net
                        )
                        self.log_tool_history("execute_command", edited_cmd, result)
                        tool_msg = self._format_shell_result(edited_cmd, result)
                        self.save_message("system", tool_msg)

                else:  # allow
                    allow_net = self._command_needs_network(command)
                    result = self.shell_tool.execute_command(
                        command, cwd, allow_network=allow_net
                    )
                    self.log_tool_history("execute_command", command, result)
                    tool_msg = self._format_shell_result(command, result)
                    self.save_message("system", tool_msg)

            else:  # allow (safe)
                allow_net = self._command_needs_network(command)
                result = self.shell_tool.execute_command(
                    command, cwd, allow_network=allow_net
                )
                self.log_tool_history("execute_command", command, result)
                tool_msg = self._format_shell_result(command, result)
                self.save_message("system", tool_msg)

        # ── Let the LLM respond to the tool result ───────────────────────
        messages = self.get_conversation_messages()

        # Inject tool history context
        if history_ctx:
            messages.insert(1, {"role": "system", "content": history_ctx})

        self.console.print()
        response = self.stream_response(messages)
        self.console.print()

        if response.strip():
            self.save_message("assistant", response)

    # ── Shell helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _command_needs_network(command: str) -> bool:
        """Heuristic: does this command need network access?"""
        net_prefixes = [
            "pip install", "npm install", "cargo", "git clone",
            "git pull", "git push", "git fetch", "brew", "curl", "wget",
        ]
        for prefix in net_prefixes:
            if command.strip().startswith(prefix):
                return True
        return False

    @staticmethod
    def _format_shell_result(command: str, result: ToolResult) -> str:
        if result.success:
            output = result.output[:4000]
            return (
                f"[Tool: execute_command('{command}')]\n{output}\n[/Tool: success]"
            )
        else:
            error = result.error[:1000]
            output = result.output[:2000]
            parts = [f"[Tool: execute_command('{command}') FAILED]"]
            if output:
                parts.append(output)
            parts.append(f"Error: {error}")
            parts.append("[/Tool: failure]")
            return "\n".join(parts)

    # ══════════════════════════════════════════════════════════════════════
    #  Slash commands
    # ══════════════════════════════════════════════════════════════════════

    def cmd_model(self) -> None:
        try:
            resp = ollama.list()
            model_list = self._parse_models(resp)
        except Exception as exc:
            self.error_console.print(f"  Could not list models: {exc}")
            return
        if not model_list:
            self.console.print(
                "  No models found. Pull one with:  ollama pull <model>",
                style="yellow",
            )
            return
        choices: List[str] = []
        name_map: Dict[str, str] = {}
        for m in model_list:
            name = m.get("model", "unknown")
            size = m.get("size", 0)
            size_gb = size / (1024**3)
            label = f"{name}  ({size_gb:.1f} GB)"
            choices.append(label)
            name_map[label] = name
        choices.sort()
        selection = questionary.select(
            "  Choose a model:", choices=choices
        ).ask()
        if selection is None:
            return
        chosen = name_map.get(selection, selection.split("  ")[0])
        self.model = chosen
        self.set_config("model", chosen)
        self.console.print(f"  ✓ Switched to {chosen}", style="green")

    def cmd_think(self) -> None:
        self.thinking_mode = not self.thinking_mode
        state = "on" if self.thinking_mode else "off"
        self.set_config("thinking_mode", state)
        self.console.print(f"  ✓ Thinking mode: {state}", style="green")

    def cmd_clear(self) -> None:
        self.conn.execute("DELETE FROM conversation")
        self.conn.commit()
        self.console.print("  ✓ Context cleared.", style="green")

    def cmd_compact(self) -> None:
        self.compact_context(manual=True)

    def cmd_help(self) -> None:
        table = Table(
            title="Pincer Commands",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
            title_style="bold",
            padding=(0, 2),
        )
        table.add_column("Command", style="bold", width=12)
        table.add_column("Description")
        table.add_row("/model", "Switch Ollama model (arrow-key picker)")
        table.add_row("/think", "Toggle extended thinking mode")
        table.add_row("/clear", "Clear all conversation history")
        table.add_row("/compact", "Summarise old context to free space")
        table.add_row("/tools", "List available tools and their status")
        table.add_row("/sandbox", "Show sandbox configuration and allowed paths")
        table.add_row("/undo", "Undo the last Git commit (reset --hard HEAD~1)")
        table.add_row("/context", "Show context window stats")
        table.add_row("/exit", "Quit Pincer")
        table.add_row("/help", "Show this help table")
        self.console.print(table)

    def cmd_context(self) -> None:
        total = self.get_total_tokens()
        count = self.get_message_count()
        system_t = self.count_tokens(self.get_system_prompt())
        db_t = total - system_t
        pct = (total / MAX_TOKENS) * 100
        table = Table(
            title="Context Window", show_header=False,
            border_style="dim", title_style="bold", padding=(0, 2),
        )
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("Model", self.model)
        table.add_row("Messages in DB", str(count))
        table.add_row("System prompt tokens", f"{system_t:,}")
        table.add_row("Conversation tokens", f"{db_t:,}")
        table.add_row("Total tokens", f"{total:,}")
        table.add_row("Capacity", f"{total:,} / {MAX_TOKENS:,} ({pct:.0f}%)")
        table.add_row("Thinking mode", "on" if self.thinking_mode else "off")
        self.console.print(table)

    def cmd_tools(self) -> None:
        """List all available tools and their status."""
        table = Table(
            title="Available Tools",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
            title_style="bold",
            padding=(0, 2),
        )
        table.add_column("Tool", style="bold", width=18)
        table.add_column("Status", width=6)
        table.add_column("Description")

        tools = [
            ("read_file", "✅", "Read file contents with line numbers"),
            ("write_file", "✅", "Write content to file (creates parent dirs)"),
            ("edit_file", "✅", "Exact string replacement in a file"),
            ("edit_file_diff", "✅", "Aider-style SEARCH/REPLACE block edit"),
            ("execute_command", "✅", "Run shell commands (sandboxed)"),
        ]
        for name, status, desc in tools:
            table.add_row(name, status, desc)

        self.console.print(table)

        # Show recent tool history
        rows = self.conn.execute(
            "SELECT tool_name, command, status, timestamp FROM tool_history "
            "ORDER BY id DESC LIMIT 5"
        ).fetchall()
        if rows:
            self.console.print("\n  Recent tool activity:", style="dim")
            for r in rows:
                icon = "✓" if r[2] == "success" else "✗"
                self.console.print(
                    f"    {icon} {r[0]}: {r[1][:60]}  ({r[2]})  {r[3]}",
                    style="dim",
                )

    def cmd_sandbox(self) -> None:
        """Show current sandbox restrictions and allowed paths."""
        cwd = os.getcwd()
        sandbox_status = (
            "Active (sandbox-exec)"
            if self.shell_tool.sandbox_available
            else "Unavailable (commands run unsandboxed)"
        )
        table = Table(
            title="Sandbox Configuration",
            show_header=False,
            border_style="dim",
            title_style="bold",
            padding=(0, 2),
        )
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("Status", sandbox_status)
        table.add_row("Working directory", cwd)
        table.add_row(
            "Allowed reads",
            f"{cwd}, {Path.home()}, /usr, /Library, /System, /opt",
        )
        table.add_row(
            "Allowed writes", f"{cwd}, {tempfile.gettempdir()}"
        )
        table.add_row(
            "Network",
            "Blocked (except for git/pip/npm/cargo/brew/curl/wget with approval)",
        )
        table.add_row(
            "Blocked",
            "sudo, rm -rf /, mkfs, dd, curl | bash, fork bombs",
        )
        self.console.print(table)

    def cmd_undo(self) -> None:
        """Undo the last Git commit."""
        if not Path(".git").exists():
            self.console.print("  ⚠ Not in a Git repository.", style="yellow")
            return
        try:
            subprocess.run(
                ["git", "reset", "--hard", "HEAD~1"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.console.print("  ✓ Undid last change.", style="green")
        except Exception as exc:
            self.console.print(f"  ❌ Undo failed: {exc}", style="bold red")

    def handle_command(self, user_input: str) -> None:
        """Route a slash command to its handler."""
        parts = user_input.strip().split()
        cmd = parts[0].lower()

        dispatch = {
            "/model": self.cmd_model,
            "/think": self.cmd_think,
            "/clear": self.cmd_clear,
            "/compact": self.cmd_compact,
            "/help": self.cmd_help,
            "/context": self.cmd_context,
            "/tools": self.cmd_tools,
            "/sandbox": self.cmd_sandbox,
            "/undo": self.cmd_undo,
            "/exit": self._exit,
        }

        handler = dispatch.get(cmd)
        if handler:
            handler()
        else:
            self.console.print(
                f"  Unknown command: {cmd}  — type /help for available commands.",
                style="yellow",
            )

    def _exit(self) -> None:
        """Clean up and exit."""
        self.console.print("  👋 Goodbye.", style="cyan")
        self.cleanup()
        sys.exit(0)

    # ══════════════════════════════════════════════════════════════════════
    #  Cleanup
    # ══════════════════════════════════════════════════════════════════════

    def cleanup(self) -> None:
        """Close the database connection."""
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════════
    #  Startup validation
    # ══════════════════════════════════════════════════════════════════════

    def _validate_env(self) -> None:
        """Check that Ollama is still installed and the server is up."""
        if not self.check_ollama_installed():
            self.console.print(
                "❌ Ollama not found. Install it first:\n"
                "   brew install ollama\n"
                "   # or download from https://ollama.com",
                style="bold red",
            )
            sys.exit(1)

        if not self.check_ollama_server():
            self.console.print(
                "⚠  Ollama server is not running. Start it in a separate terminal:\n"
                "   ollama serve\n"
                "   or open the Ollama app. Then restart Pincer.",
                style="bold red",
            )
            sys.exit(1)

        # Verify the configured model is available
        if not self.check_model_available(self.model):
            self.console.print(
                f"  ⚠ Model {self.model} not found locally. Pulling…",
                style="yellow",
            )
            self.pull_model(self.model)

    # ══════════════════════════════════════════════════════════════════════
    #  Main REPL
    # ══════════════════════════════════════════════════════════════════════

    def run(self) -> None:
        """Entry point — setup, wizard if needed, then the REPL loop."""
        # Data directory
        PINCER_DIR.mkdir(parents=True, exist_ok=True)

        # Database
        self.setup_db()

        # First-run or returning user
        if self.get_config("user_name") is None:
            self.first_run_wizard()
        else:
            self.load_config()
            self.console.print(BANNER, style="bold cyan")
            msg_count = self.get_message_count()
            total_tok = self.get_total_tokens()
            self.console.print(
                f"  Model: {self.model}  |  User: {self.user_name}  "
                f"|  Lang: {self.preferred_language}  "
                f"|  Context: {msg_count} msgs ({total_tok / 1000:.1f}K tok)",
                style="dim",
            )

        # Validate environment
        self._validate_env()

        # prompt_toolkit session
        self.session = PromptSession(
            history=FileHistory(str(HISTORY_PATH)),
            auto_suggest=AutoSuggestFromHistory(),
        )

        # SIGINT handler — stops generation instead of killing the process
        _orig_sigint = signal.getsignal(signal.SIGINT)

        def _sigint_handler(signum, frame):
            if self._generating:
                self._generating = False
            else:
                raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _sigint_handler)

        # ── Main loop ────────────────────────────────────────────────────
        while True:
            try:
                prompt_str = (
                    f" {self.user_name} ❯ " if self.user_name else " ❯ "
                )

                user_input = self.session.prompt(
                    HTML(f"<ansicyan>{prompt_str}</ansicyan>"),
                    bottom_toolbar=self._status_bar,
                )

                stripped = user_input.strip()
                if not stripped:
                    continue

                # ── Slash commands ──
                if stripped.startswith("/"):
                    self.handle_command(stripped)
                    continue

                # ── Route to tool or chat ──
                classification = self.router.classify(stripped)

                if classification.intent == "chat":
                    # Pure chat — same as Phase 1
                    self.auto_compact_if_needed()
                    self.save_message("user", stripped)
                    messages = self.get_conversation_messages()
                    self.console.print()
                    response = self.stream_response(messages)
                    self.console.print()
                    if response.strip():
                        self.save_message("assistant", response)
                else:
                    # Tool intent — dispatch through tool handler
                    self.handle_tool_message(
                        stripped, classification.intent, classification.params
                    )

            except KeyboardInterrupt:
                if not self._generating:
                    self.console.print()
                continue

            except EOFError:
                self.console.print("\n  👋 Goodbye.", style="cyan")
                self.cleanup()
                sys.exit(0)

        # Restore (unreachable but correct)
        signal.signal(signal.SIGINT, _orig_sigint)


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI argument parser
# ═══════════════════════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for CLI flags."""
    p = argparse.ArgumentParser(
        prog="pincer",
        description="Pincer — local AI coding assistant for your terminal",
    )
    p.add_argument(
        "--model",
        metavar="MODEL",
        help=f"Ollama model to use (default: {DEFAULT_MODEL})",
    )
    p.add_argument(
        "--think",
        action="store_true",
        help="Enable thinking mode on startup",
    )
    p.add_argument(
        "--clear",
        action="store_true",
        help="Clear conversation history before starting",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Delete all Pincer data and re-run the setup wizard",
    )
    return p


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    # --reset: wipe everything and start fresh
    if args.reset:
        if PINCER_DIR.exists():
            shutil.rmtree(PINCER_DIR)
        print("  ✓ Pincer data deleted. Run again to start fresh.")

    app = PincerApp()

    # Apply CLI flags
    if args.model:
        app.model = args.model
    if args.think:
        app.thinking_mode = True
    if args.clear:
        # Will be applied after DB init
        pass

    try:
        app.run()
    except KeyboardInterrupt:
        app.console.print("\n  👋 Goodbye.", style="cyan")
        app.cleanup()
    except Exception as exc:
        Console(stderr=True).print(
            f"  ❌ Fatal error: {exc}", style="bold red"
        )
        app.cleanup()
        sys.exit(1)


if __name__ == "__main__":
    main()
PINCEOF
