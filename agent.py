#!/usr/bin/env python3
"""Pincer — local AI coding assistant for your terminal."""

import os
import re
import sys
import sqlite3
import shutil
import signal
import argparse
import asyncio
from pathlib import Path
from typing import Optional, List, Dict

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.table import Table
import questionary
import ollama

PINCER_DIR = Path.home() / ".pincer"
DB_PATH = PINCER_DIR / "pincer.db"
HISTORY_PATH = PINCER_DIR / "history"
MAX_TOKENS = 12000
COMPACT_THRESHOLD = 10000
CHARS_PER_TOKEN = 4
DEFAULT_MODEL = "qwen3:8b"

BANNER = r"""
╔══════════════════════════════════╗
║  🤖 PINCER — local AI assistant  ║
╚══════════════════════════════════╝
"""

THINK_TAG_OPEN = "<think"
THINK_TAG_CLOSE = "</think"


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
    def count_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // CHARS_PER_TOKEN)

    def setup_db(self) -> None:
        PINCER_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.execute("PRAGMA journal_mode=WAL")
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
        self.conn.commit()

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
                if name == model_name or name.startswith(model_name.split(":")[0] + ":"):
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

        if not self.check_model_available(self.model):
            self.pull_model(self.model)
        else:
            self.console.print(f"  ✓ {self.model} available.", style="green")

        name = questionary.text("  Your name?", default="").ask()
        if name is None:
            name = ""
        self.user_name = name.strip() or "user"
        self.set_config("user_name", self.user_name)

        lang = questionary.select(
            "  Preferred coding language?",
            choices=[
                "python", "javascript", "typescript", "rust", "go",
                "java", "c", "cpp", "ruby", "other",
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

    def get_system_prompt(self) -> str:
        prompt = (
            f"You are Pincer, a helpful coding assistant running locally on the user's Mac.\n"
            f"User: {self.user_name} | Preferred language: {self.preferred_language}\n"
            f"Be concise. Use markdown for code blocks."
        )
        if self.thinking_mode:
            prompt += "\nThink step by step before responding."
        return prompt

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

    def compact_context(self, manual: bool = False) -> None:
        rows = self.conn.execute(
            "SELECT id, role, content FROM conversation ORDER BY id ASC"
        ).fetchall()

        if len(rows) < 4:
            if manual:
                self.console.print("  ⚠ Not enough messages to compact (need ≥ 4).", style="yellow")
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
        self.conn.execute(f"DELETE FROM conversation WHERE id IN ({placeholders})", old_ids)

        summary_tokens = self.count_tokens(summary)
        self.conn.execute(
            "INSERT INTO conversation (role, content, tokens) VALUES (?, ?, ?)",
            ("summary", summary, summary_tokens),
        )
        self.conn.commit()

        self.console.print(f"  ✓ Compacted {len(old_rows)} messages → 1 summary", style="green")

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
                "SELECT id FROM conversation WHERE role = 'user' ORDER BY id ASC LIMIT 1"
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
                    "SELECT id FROM conversation WHERE id > ? AND role = 'assistant' ORDER BY id ASC LIMIT 1",
                    (user_id,),
                ).fetchone()
                if next_asst:
                    self.conn.execute("DELETE FROM conversation WHERE id = ?", (next_asst[0],))

            self.conn.commit()
            total_tokens = self.get_total_tokens()

    @staticmethod
    def _partial_tag_len(buffer: str, tag: str) -> int:
        for i in range(1, min(len(tag) + 1, len(buffer) + 1)):
            if buffer[-i:] == tag[:i]:
                return i
        return 0

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
            self.console.print(f"\n  ❌ Error generating response: {exc}", style="bold red")

        self._generating = False
        return full_response

    def get_status_bar_text(self) -> str:
        total = self.get_total_tokens()
        ctx = f"{total / 1000:.1f}K"
        think = "on" if self.thinking_mode else "off"
        return f" {self.model} | thinking:{think} | ctx: {ctx}/12K"

    def _status_bar(self) -> HTML:
        return HTML(
            f"<style bg='ansiblack' fg='ansiwhite'>"
            f"{self.get_status_bar_text()}</style>"
        )

    def cmd_model(self) -> None:
        try:
            resp = ollama.list()
            model_list = self._parse_models(resp)
        except Exception as exc:
            self.error_console.print(f"  Could not list models: {exc}")
            return

        if not model_list:
            self.console.print("  No models found. Pull one with:  ollama pull <model>", style="yellow")
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

        selection = questionary.select("  Choose a model:", choices=choices).ask()
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
            title="Context Window",
            show_header=False,
            border_style="dim",
            title_style="bold",
            padding=(0, 2),
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

    def handle_command(self, user_input: str) -> None:
        parts = user_input.strip().split()
        cmd = parts[0].lower()

        dispatch = {
            "/model": self.cmd_model,
            "/think": self.cmd_think,
            "/clear": self.cmd_clear,
            "/compact": self.cmd_compact,
            "/help": self.cmd_help,
            "/context": self.cmd_context,
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
        self.console.print("  👋 Goodbye.", style="cyan")
        self.cleanup()
        sys.exit(0)

    def cleanup(self) -> None:
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass

    def _validate_env(self) -> None:
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

        if not self.check_model_available(self.model):
            self.console.print(f"  ⚠ Model {self.model} not found locally. Pulling…", style="yellow")
            self.pull_model(self.model)

    def run(self) -> None:
        PINCER_DIR.mkdir(parents=True, exist_ok=True)
        self.setup_db()

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

        self._validate_env()

        self.session = PromptSession(
            history=FileHistory(str(HISTORY_PATH)),
            auto_suggest=AutoSuggestFromHistory(),
        )

        _orig_sigint = signal.getsignal(signal.SIGINT)

        def _sigint_handler(signum, frame):
            if self._generating:
                self._generating = False
            else:
                raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _sigint_handler)

        while True:
            try:
                prompt_str = f" {self.user_name} ❯ " if self.user_name else " ❯ "

                user_input = self.session.prompt(
                    HTML(f"<ansicyan>{prompt_str}</ansicyan>"),
                    bottom_toolbar=self._status_bar,
                )

                stripped = user_input.strip()
                if not stripped:
                    continue

                if stripped.startswith("/"):
                    self.handle_command(stripped)
                    continue

                self.auto_compact_if_needed()
                self.save_message("user", stripped)

                messages = self.get_conversation_messages()

                self.console.print()
                response = self.stream_response(messages)
                self.console.print()

                if response.strip():
                    self.save_message("assistant", response)

            except KeyboardInterrupt:
                if not self._generating:
                    self.console.print()
                continue

            except EOFError:
                self.console.print("\n  👋 Goodbye.", style="cyan")
                self.cleanup()
                sys.exit(0)

        signal.signal(signal.SIGINT, _orig_sigint)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pincer",
        description="Pincer — local AI coding assistant for your terminal",
    )
    p.add_argument("--model", metavar="MODEL", help=f"Ollama model (default: {DEFAULT_MODEL})")
    p.add_argument("--think", action="store_true", help="Enable thinking mode on startup")
    p.add_argument("--clear", action="store_true", help="Clear conversation history before starting")
    p.add_argument("--reset", action="store_true", help="Delete all Pincer data and re-run the setup wizard")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.reset:
        import shutil as _shutil
        if PINCER_DIR.exists():
            _shutil.rmtree(PINCER_DIR)
        print("  ✓ Pincer data deleted. Run again to start fresh.")
        return

    app = PincerApp()

    if args.model:
        app.model = args.model
    if args.think:
        app.thinking_mode = True
    if args.clear:
        app.setup_db()
        app.conn.execute("DELETE FROM conversation")
        app.conn.commit()

    try:
        app.run()
    except KeyboardInterrupt:
        app.console.print("\n  👋 Goodbye.", style="cyan")
        app.cleanup()
    except Exception as exc:
        Console(stderr=True).print(f"  ❌ Fatal error: {exc}", style="bold red")
        app.cleanup()
        sys.exit(1)


if __name__ == "__main__":
    main()
