#!/usr/bin/env python3
"""Pincer — professional local AI coding assistant.

Professional terminal UI with blue theme, inspired by Claude Code.
Phase 1: REPL, chat, context manager.
Phase 2: File tools, sandboxed shell, permissions.
Phase 3: Autonomous worker, planning, self-notes, checkpoints.
Phase 4: PII scrubbing, file watching, voice, sessions, 50 feature suite.
"""

import os, re, sys, sqlite3, shutil, signal, argparse, asyncio, subprocess, \
    tempfile, json, time, difflib, hashlib, threading, wave, struct
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
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.columns import Columns
from rich.bar import Bar
from rich.rule import Rule
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
MAX_TOKENS = 12000; COMPACT_THRESHOLD = 10000; CHARS_PER_TOKEN = 4
DEFAULT_MODEL = "qwen3:8b"; EMBEDDING_MODEL = "nomic-embed-text"; EMBEDDING_DIM = 768
MAX_TOOL_TOKENS = 1500; MAX_SHELL_PER_TASK = 50; MAX_RM_PER_TASK = 3
MAX_WRITES_PER_TASK = 20; NO_PROGRESS_TIMEOUT = 1200; MAX_TASK_TIME = 7200
HEARTBEAT_INTERVAL = 300; FILE_WATCH_INTERVAL = 3.0

BANNER = "[bold blue]⬡ Pincer[/bold blue] [dim]v4.0 — local AI assistant[/dim]"
BLUE = "blue"; DIM_BLUE = "dim blue"; BRIGHT_BLUE = "bright_blue"

THINK_TAG_OPEN = "<think"; THINK_TAG_CLOSE = "</think"

SAFE_CMDS = ["git","ls","pwd","mkdir","cat","head","tail","python","python3","pytest","node","npm install","pip install","cargo","make","echo","wc","find","grep","which","tree","diff","sort","uniq","tee","rg","ag"]
ASK_CMDS = ["rm","mv","cp","chmod","chown","sudo","curl","wget","eval","exec","source","bash","sh","zsh","pip","npm","brew","docker","kill","pkill"]
DENY_PATTERNS = [r"^rm\s+-[a-zA-Z]*f[a-zA-Z]*\s+/$",r"^rm\s+-[a-zA-Z]*f[a-zA-Z]*\s+/\*",r"^sudo\s+rm",r"^sudo\s+-\w*\s+rm",r"^mkfs",r"^dd\s+if=",r"curl\s+.*\|\s*(ba)?sh",r"wget\s+.*\|\s*(ba)?sh",r":\(\)\{.*;\}\s*;",r"^chmod\s+-R\s+777\s+/",r"^chmod\s+777\s+/"]
FILE_READ_KW = {"read","show","cat","open","display","view","inspect","explain"}
FILE_WRITE_KW = {"write","create","save","new file","add file"}
FILE_EDIT_KW = {"edit","fix","refactor","change","update","modify","patch","rename"}
SHELL_KW = {"run","execute","test","build","install","git","ls","mkdir","pip","npm","cargo","make","delete","remove","search","find"}
FILE_EXT = {".py",".js",".ts",".tsx",".jsx",".rs",".go",".java",".c",".cpp",".h",".hpp",".rb",".php",".swift",".kt",".txt",".md",".json",".yaml",".yml",".toml",".cfg",".ini",".sh",".bash",".zsh",".fish",".sql",".html",".css",".scss",".vue",".svelte",".gitignore",".env",".csv",".xml",".lock"}
MEMORY_FILES = ["CLAUDE.md","AGENTS.md","PINCER.md",".pincer.md"]
BINARY_EXT = {".png",".jpg",".jpeg",".gif",".bmp",".ico",".webp",".mp3",".mp4",".wav",".avi",".mov",".mkv",".zip",".tar",".gz",".bz2",".xz",".7z",".rar",".pyc",".pyo",".so",".dylib",".dll",".exe",".woff",".woff2",".ttf",".eot",".otf",".pdf",".doc",".docx",".xls",".xlsx",".ppt",".pptx",".sqlite",".db",".parquet"}
SKIP_DIRS = {"node_modules",".git","__pycache__",".venv","venv","dist","build",".next",".nuxt","target",".tox",".mypy_cache",".pytest_cache"}

# ═══════════════════════════════════════════════════════════════════════════════
#  PII SCRUBBER (#1 of 50)
# ═══════════════════════════════════════════════════════════════════════════════

PII_PATTERNS = [
    (re.compile(r'AKIA[0-9A-Z]{16}'), '[AWS_KEY]'),
    (re.compile(r'ghp_[0-9a-zA-Z]{36}'), '[GITHUB_TOKEN]'),
    (re.compile(r'gho_[0-9a-zA-Z]{36}'), '[GITHUB_OAUTH]'),
    (re.compile(r'ghs_[0-9a-zA-Z]{36}'), '[GITHUB_SAML]'),
    (re.compile(r'sk-[a-zA-Z0-9]{48}'), '[OPENAI_KEY]'),
    (re.compile(r'eyJ[a-zA-Z0-9._-]+'), '[JWT]'),
    (re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----'), '[PRIVATE_KEY]'),
    (re.compile(r'(?:password|passwd|secret|token|api_key|apikey)\s*[:=]\s*["\']?[^\s"\']{8,}', re.I), '[CREDENTIAL]'),
    (re.compile(r'(?:MONGO|DATABASE|DB)_URL\s*[:=]\s*["\']?[^\s"\']{10,}', re.I), '[DB_URL]'),
]

def scrub_pii(text: str) -> str:
    """#42: Strip PII/secrets from text before sending to LLM."""
    for pattern, replacement in PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text

# ═══════════════════════════════════════════════════════════════════════════════
#  LAZY FILE COMPLETER (#8 of 50)
# ═══════════════════════════════════════════════════════════════════════════════

class LazyFileCompleter(Completer):
    def __init__(self): self._paths = None; self._t = 0.0
    def _scan(self):
        ps = []
        try:
            for r, ds, fs in os.walk("."):
                ds[:] = [d for d in ds if d not in SKIP_DIRS and not d.startswith(".")]
                for f in fs:
                    fp = os.path.join(r, f)
                    if not any(fp.endswith(e) for e in BINARY_EXT): ps.append(fp)
                    if len(ps) >= 300: return ps
        except Exception: pass
        return ps
    def get_completions(self, document, complete_event):
        if self._paths is None or time.time()-self._t > 30:
            self._paths = self._scan(); self._t = time.time()
        w = document.get_word_before_cursor()
        if not w: return
        for p in self._paths:
            if w in p: yield Completion(p, start_position=-len(w))

# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolResult:
    success: bool; output: str; tool_name: str; command: str = ""; error: str = ""
    @property
    def display(self): return self.output if self.success else (self.error or "Error")
    def truncated(self, mc=6000): return self.output[:mc] + (f"\n... [{len(self.output)-mc} chars cut]" if len(self.output)>mc else self.output)

# ═══════════════════════════════════════════════════════════════════════════════
#  PERMISSION MANAGER + POLICY (#41 of 50)
# ═══════════════════════════════════════════════════════════════════════════════

class PermissionManager:
    def __init__(self, allowed=None):
        self._always = allowed or []; self._policy = {}
    def load_policy(self, path: Path):
        if path.exists():
            try: self._policy = json.loads(path.read_text())
            except: pass
    def check(self, cmd, cwd):
        s = cmd.strip()
        for p in DENY_PATTERNS:
            if re.search(p, s, re.I): return "deny"
        for rule in self._policy.get("deny", []):
            if re.search(rule, s): return "deny"
        for p in self._always:
            if s == p or s.startswith(p+" "): return "allow"
        for p in SAFE_CMDS:
            if s == p or s.startswith(p+" "):
                if any(x.startswith("/") and not x.startswith(cwd) for x in s.split()): return "ask"
                return "allow"
        for p in ASK_CMDS:
            if s == p or s.startswith(p+" "): return "ask"
        return "ask"
    def add_allowed(self, cmd):
        p = " ".join(cmd.split()[:2])
        if p not in self._always: self._always.append(p)

# ═══════════════════════════════════════════════════════════════════════════════
#  FILE TOOLS (#19 indentation-agnostic, #31 verification)
# ═══════════════════════════════════════════════════════════════════════════════

class FileTools:
    @staticmethod
    def read_file(path):
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists(): return ToolResult(False,"","read_file",path,f"Not found: {p}")
            if p.is_dir(): return ToolResult(False,"","read_file",path,f"Directory: {p}")
            if p.stat().st_size > 500_000: return ToolResult(False,"","read_file",path,f"Too large ({p.stat().st_size//1024}KB)")
            with open(p,"r",encoding="utf-8",errors="replace") as f: content = f.read()
            num = "".join(f"  {i+1:>4} │ {l}" for i,l in enumerate(content.splitlines(True)))
            return ToolResult(True, num, "read_file", path)
        except Exception as e: return ToolResult(False,"","read_file",path,str(e))

    @staticmethod
    def write_file(path, content):
        try:
            p = Path(path).expanduser().resolve(); p.parent.mkdir(parents=True, exist_ok=True)
            with open(p,"w",encoding="utf-8") as f: f.write(content)
            lc = content.count("\n")+(1 if content and not content.endswith("\n") else 0)
            return ToolResult(True,f"✓ Wrote {lc} lines → {p}","write_file",path)
        except Exception as e: return ToolResult(False,"","write_file",path,str(e))

    @staticmethod
    def edit_file(path, old, new):
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists(): return ToolResult(False,"","edit_file",path,f"Not found: {p}")
            with open(p,"r",encoding="utf-8") as f: c = f.read()
            cnt = c.count(old)
            if cnt == 0:
                # #19: Indentation-agnostic fallback
                stripped = old.strip()
                for i, line in enumerate(c.splitlines()):
                    if stripped in line.strip():
                        return ToolResult(False,"","edit_file",path,
                            f"Exact not found. Fuzzy match line {i+1}: '{line.strip()[:60]}'")
                return ToolResult(False,"","edit_file",path,"Not found")
            if cnt > 1: return ToolResult(False,"","edit_file",path,f"Appears {cnt}x — add context")
            c = c.replace(old, new, 1)
            with open(p,"w",encoding="utf-8") as f: f.write(c)
            with open(p,"r",encoding="utf-8") as f:
                if new not in f.read(): return ToolResult(False,"","edit_file",path,"Verify failed")
            return ToolResult(True,f"✓ Replaced in {p}","edit_file",path)
        except Exception as e: return ToolResult(False,"","edit_file",path,str(e))

    @staticmethod
    def edit_file_diff(path, search, replace):
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists(): return ToolResult(False,"","edit_file",path,f"Not found: {p}")
            with open(p,"r",encoding="utf-8") as f: c = f.read()
            if search not in c:
                # #19: Whitespace-agnostic
                ss = search.strip(); lines = c.splitlines()
                for i, line in enumerate(lines):
                    if ss in line.strip():
                        indent = len(line)-len(line.lstrip())
                        lines[i] = " "*indent + replace.strip() + "\n"
                        with open(p,"w",encoding="utf-8") as f: f.write("\n".join(lines))
                        return ToolResult(True,f"✓ Fuzzy replaced line {i+1}","edit_file",path)
                return ToolResult(False,"","edit_file",path,"SEARCH not found")
            if c.count(search) > 1: return ToolResult(False,"","edit_file",path,f"Appears {c.count(search)}x")
            c = c.replace(search, replace, 1)
            with open(p,"w",encoding="utf-8") as f: f.write(c)
            return ToolResult(True,f"✓ Replaced block in {p}","edit_file",path)
        except Exception as e: return ToolResult(False,"","edit_file",path,str(e))

    @staticmethod
    def compute_diff(old, new, path=""):
        return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"{path}", tofile=f"{path}"))

# ═══════════════════════════════════════════════════════════════════════════════
#  SHELL TOOL (#25 hardened sandbox, #27 test parsing)
# ═══════════════════════════════════════════════════════════════════════════════

class ShellTool:
    def __init__(self, console): self.console = console; self._sb = shutil.which("sandbox-exec") is not None
    @property
    def sandbox_available(self): return self._sb
    @staticmethod
    def _profile(cwd, net=False):
        h=str(Path.home()); t=tempfile.gettempdir(); nr="(allow network*)" if net else "(deny network*)"
        return f'(version 1)\n(deny default)\n(allow file-read* file-write* (subpath "{cwd}"))\n(allow file-read* file-write* (subpath "{t}"))\n(allow file-read* (subpath "{h}"))\n(allow file-read* (subpath "/usr"))\n(allow file-read* (subpath "/Library"))\n(allow file-read* (subpath "/System"))\n(allow file-read* (subpath "/opt"))\n(allow process-exec (subpath "/usr/bin"))\n(allow process-exec (subpath "/usr/local/bin"))\n(allow process-exec (subpath "{h}/.local/bin"))\n(allow process-exec (subpath "/opt/homebrew"))\n(deny process-exec (literal "/usr/bin/sudo"))\n(deny process-exec (literal "/usr/sbin/mkfs"))\n{nr}\n'
    def execute_command(self, cmd, cwd, timeout=120, net=False):
        try:
            if self._sb:
                with tempfile.NamedTemporaryFile(mode="w",suffix=".sb",delete=False) as f: f.write(self._profile(cwd,net)); pp=f.name
                try: r=subprocess.run(["sandbox-exec","-f",pp,"bash","-c",cmd],capture_output=True,text=True,cwd=cwd,timeout=timeout)
                finally:
                    try: os.unlink(pp)
                    except: pass
            else: r=subprocess.run(["bash","-c",cmd],capture_output=True,text=True,cwd=cwd,timeout=timeout)
            out=r.stdout
            if r.stderr: out+=("\n--- stderr ---\n"+r.stderr) if out else r.stderr
            if r.returncode!=0: return ToolResult(False,out.strip(),"execute_command",cmd,f"Exit {r.returncode}")
            return ToolResult(True,out.strip(),"execute_command",cmd)
        except subprocess.TimeoutExpired: return ToolResult(False,"","execute_command",cmd,f"Timeout {timeout}s")
        except Exception as e: return ToolResult(False,"","execute_command",cmd,str(e))
    @staticmethod
    def parse_tests(output):
        r={"passed":0,"failed":0,"errors":0}
        m=re.search(r"(\d+) passed",output)
        if m: r["passed"]=int(m.group(1))
        m=re.search(r"(\d+) failed",output)
        if m: r["failed"]=int(m.group(1))
        m=re.search(r"(\d+) error",output)
        if m: r["errors"]=int(m.group(1))
        return r

# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

class ToolRouter:
    @dataclass
    class C: intent:str; params:Dict[str,str]=field(default_factory=dict)
    def classify(self, text):
        lo=text.lower(); ws=set(re.findall(r"\w+",lo))
        rh=len(ws&FILE_READ_KW); wh=sum(1 for k in FILE_WRITE_KW if k in lo)
        eh=len(ws&FILE_EDIT_KW); sh=len(ws&SHELL_KW)
        hp=self._p(text) is not None; hc=self._c(text) is not None
        if sh>0 and hc: return self.C("shell",{"command":self._c(text) or ""})
        if eh>0 and hp: return self.C("file_edit",{"path":self._p(text) or ""})
        if wh>0 and hp: return self.C("file_write",{"path":self._p(text) or "","description":self._wd(text,self._p(text) or "")})
        if rh>0 and hp: return self.C("file_read",{"path":self._p(text) or ""})
        if sh>0:
            c=self._c(text) or ""
            if c: return self.C("shell",{"command":c})
        return self.C("chat")
    @staticmethod
    def _p(t):
        m=re.search(r'["\']([^"\']+)["\']',t)
        if m: return m.group(1).strip()
        for w in re.findall(r"[\w./\-]+",t):
            if Path(w).suffix.lower() in FILE_EXT: return w
        m=re.search(r"(?:file|in|to|at)\s+([^\s,;.!?]+)",t,re.I)
        if m:
            c=m.group(1).strip("\"'")
            if c and c.lower() not in {"a","the","this","that","it"}: return c
        return None
    @staticmethod
    def _c(t):
        lo=t.lower()
        for kw in ("run","execute"):
            m=re.search(rf"\b{kw}\s+(.+)",lo)
            if m: return m.group(1).strip()
        if re.search(r"\brun\s+tests?\b",lo): return "pytest"
        for cp in ("git","ls","mkdir","pip","npm","cargo","make","pytest","python","python3","node","docker","brew","curl","wget","rg","grep"):
            m=re.search(rf"\b({cp}\s+.+)",lo)
            if m: return m.group(1).strip()
            if re.search(rf"\b{cp}\b",lo): return cp
        m=re.search(r"\b(?:delete|remove)\s+(.+)",lo)
        if m: return "rm -rf ." if m.group(1).strip() in ("everything","all","*") else f"rm -rf {m.group(1).strip()}"
        if re.search(r"\bbuild\b",lo): return "make"
        m=re.search(r"\binstall\s+(.+)",lo)
        if m: return f"pip install {m.group(1).strip()}"
        return None
    @staticmethod
    def _wd(t,p):
        d=t
        for rx in (r"^write\s+",r"^create\s+(?:a\s+)?(?:new\s+)?(?:file\s+)?",r"^save\s+",r"^add\s+(?:a\s+)?(?:new\s+)?(?:file\s+)?"): d=re.sub(rx,"",d,flags=re.I).strip()
        if p: d=d.replace(p,"").strip()
        d=re.sub(r"\s+(to|in|at)\s*$","",d,flags=re.I).strip()
        return re.sub(r"\s+"," ",d).strip(" ,.:;!") or f"content for {p}"

# ═══════════════════════════════════════════════════════════════════════════════
#  FILE WATCHER (#39 of 50)
# ═══════════════════════════════════════════════════════════════════════════════

class FileWatcher:
    """Polls for file changes and notifies the agent."""
    def __init__(self): self._mtimes={}; self._running=False; self._changes=[]
    def start(self):
        if self._running: return
        self._running=True; self._scan()
        t=threading.Thread(target=self._loop, daemon=True); t.start()
    def stop(self): self._running=False
    def _scan(self):
        for root, dirs, files in os.walk("."):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                fp=os.path.join(root,f)
                try: self._mtimes[fp]=os.path.getmtime(fp)
                except: pass
    def _loop(self):
        while self._running:
            time.sleep(FILE_WATCH_INTERVAL)
            for root, dirs, files in os.walk("."):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in files:
                    fp=os.path.join(root,f)
                    try:
                        mt=os.path.getmtime(fp)
                        if fp in self._mtimes and mt>self._mtimes[fp]:
                            self._changes.append((fp, "modified"))
                        self._mtimes[fp]=mt
                    except: pass
    def drain(self):
        ch=self._changes[:]; self._changes.clear(); return ch

# ═══════════════════════════════════════════════════════════════════════════════
#  AUTONOMOUS LOOP (#27 self-healing, #29 architect, #42 pair mode)
# ═══════════════════════════════════════════════════════════════════════════════

class AutonomousLoop:
    def __init__(self, app):
        self.app=app; self.task_id=None; self.plan=[]; self.current_step=0
        self.status="idle"; self.auto_approve=False; self.session_allowed=[]
        self.start_time=None; self.shell_count=0; self.rm_count=0; self.write_count=0
        self.llm_calls=0; self.consec_fail=0; self.last_error=""; self.error_repeat=0
        self.last_heartbeat=0.0; self.last_progress=0.0
        self.architect_mode=False  # #29
        self.pair_mode=False       # #42

    def create_task(self, goal):
        c=self.app.conn.execute("INSERT INTO tasks (goal,status) VALUES (?,'planning')",(goal,))
        self.task_id=c.lastrowid; self.app.conn.commit()
        self.start_time=time.time(); self.last_heartbeat=time.time(); self.last_progress=time.time()
        self.shell_count=0; self.rm_count=0; self.write_count=0; self.llm_calls=0
        return self.task_id

    def _update(self):
        if not self.task_id: return
        el=int((time.time()-self.start_time)/60) if self.start_time else 0
        try:
            self.app.conn.execute("UPDATE tasks SET plan=?,current_step=?,total_steps=?,status=?,auto_approve=?,llm_calls=?,shell_count=?,write_count=?,rm_count=?,elapsed_minutes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(self.plan),self.current_step,len(self.plan),self.status,self.auto_approve,self.llm_calls,self.shell_count,self.write_count,self.rm_count,el,self.task_id))
        except: self.app.conn.execute("UPDATE tasks SET plan=?,current_step=?,total_steps=?,status=?,auto_approve=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(self.plan),self.current_step,len(self.plan),self.status,self.auto_approve,self.task_id))
        self.app.conn.commit()

    def load_task(self, tid):
        r=self.app.conn.execute("SELECT goal,plan,current_step,total_steps,status,auto_approve FROM tasks WHERE id=?",(tid,)).fetchone()
        if not r: return False
        self.task_id=tid; self.plan=json.loads(r[1]) if r[1] else []; self.current_step=r[2]
        self.status=r[4]; self.auto_approve=bool(r[5])
        self.start_time=time.time(); self.last_heartbeat=time.time(); self.last_progress=time.time()
        return True

    def generate_plan(self, goal, clar=""):
        pr=f"Create step-by-step plan for:\n{goal}\n"+(f"Context:\n{clar}\n" if clar else "")+"Output ONLY numbered list, one step per line."
        try:
            resp=self._llm([{"role":"user","content":pr}]); steps=[]
            for line in resp.strip().split("\n"):
                m=re.match(r"^\d+[\.\)]\s*(.+)",line.strip())
                if m: steps.append({"step":len(steps)+1,"description":m.group(1),"status":"pending"})
            if not steps: steps=[{"step":1,"description":goal,"status":"pending"}]
            self.plan=steps; self._update(); return steps
        except Exception as e: self.app.console.print(f"  ❌ Plan failed: {e}",style="bold red"); return []

    def ask_clarifying(self, goal):
        if not sys.stdin.isatty(): return ""
        try:
            resp=self._llm([{"role":"user","content":f"Goal: {goal}\nAsk 1-3 brief clarifying questions (Q: prefix). NONE if clear."}])
            if "NONE" in resp.upper(): return ""
            answers=[]
            for line in resp.strip().split("\n"):
                q=re.sub(r"^Q:\s*","",line.strip())
                if not q: continue
                a=questionary.text(f"  {q}",default="").ask()
                if a: answers.append(f"Q: {q} A: {a}")
            return "\n".join(answers)
        except: return ""

    def _llm(self, msgs): self.llm_calls+=1; return self.app._run_llm_sync(msgs)

    def run_loop(self):
        self.status="active"; self._update()
        self.app.console.print(Panel(f"[bold]🚀 Autonomous execution[/]\nSteps: {len(self.plan)} | Auto: {'🟢' if self.auto_approve else '🔴'} | Mode: {'🏗 Architect' if self.architect_mode else '⚡ Execute'}",border_style=BLUE,title="Autonomous"))
        with Progress(SpinnerColumn(),TextColumn("[bold blue]{task.description}"),BarColumn(bar_width=30),TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),TimeElapsedColumn(),console=self.app.console,transient=True) as prog:
            task=prog.add_task("Working…",total=len(self.plan))
            while self.status=="active":
                g=self._guard()
                if g: self.status="paused"; self._update(); self.app.console.print(f"\n  ⏸️ {g}",style="bold yellow"); return
                if time.time()-self.last_heartbeat>HEARTBEAT_INTERVAL:
                    el=int((time.time()-self.start_time)/60) if self.start_time else 0
                    d=self.plan[self.current_step]['description'][:50] if self.current_step<len(self.plan) else "done"
                    self.app.console.print(f"  ⏱️ Step {self.current_step+1}/{len(self.plan)}: {d}… | {el}m",style="dim"); self.last_heartbeat=time.time()
                if self.current_step>=len(self.plan): prog.update(task,completed=len(self.plan)); self._complete(); return
                step=self.plan[self.current_step]
                prog.update(task,completed=self.current_step,description=f"Step {self.current_step+1}: {step['description'][:35]}")
                self.app.console.print(f"\n  📋 Step {self.current_step+1}/{len(self.plan)}: {step['description']}",style="bold blue")
                if self.architect_mode:
                    self.app.console.print("  🏗 Architect mode — planning only, no code changes.",style="dim blue")
                    self.app.write_note("observation",f"Architect: planned step {step['description']}")
                    step["status"]="done"; self.current_step+=1; self.last_progress=time.time(); self._update(); continue
                result=self._exec(step)
                if result and result.success:
                    step["status"]="done"; self.current_step+=1; self.consec_fail=0; self.last_progress=time.time()
                    self.app.write_note("success",f"✓ {step['description']}: {result.output[:200]}")
                    self.app._auto_commit(f"step {self.current_step}: {step['description'][:60]}")
                    if self.current_step%5==0: self.save_cp()
                    self.app.console.print("  ✓ Done",style="green")
                elif result:
                    self.consec_fail+=1; step["status"]="failed"
                    self.app.write_note("error",f"✗ {step['description']}: {result.error[:200]}")
                    if result.error==self.last_error: self.error_repeat+=1
                    else: self.error_repeat=1; self.last_error=result.error
                    if self.error_repeat>=3: self.status="stuck"; self._update(); self.app.console.print("\n  🔄 Loop: same error 3x.",style="bold red"); return
                    if self.consec_fail>=3: self.status="stuck"; self._update(); self.app.console.print("\n  🔄 Stuck: 3 failures.",style="bold red"); return
                    # #27: Self-healing — retry with different approach
                    self.app.console.print(f"  ⚠ Retrying… ({result.error[:80]})",style="yellow")
                    retry=self._retry(step)
                    if retry and retry.success:
                        step["status"]="done"; self.current_step+=1; self.consec_fail=0; self.last_progress=time.time()
                        self.app.write_note("success",f"✓ Retry: {step['description']}")
                    else: self.app.console.print("  ✗ Retry failed.",style="red")
                if self.current_step>0 and self.current_step%10==0: self._eval()
                self._update()

    def _exec(self, step):
        desc=step["description"]; cls=self.app.router.classify(desc); cwd=os.getcwd()
        if self.pair_mode:
            if not questionary.confirm(f"  Execute: {desc}?",default=True).ask(): return ToolResult(False,"","skipped","","User skipped")
        if cls.intent=="file_read":
            path=cls.params.get("path","") or self._infer(desc)
            if not path: return ToolResult(False,"","read_file","","No path")
            r=self.app.file_tools.read_file(path); self.app.log_tool("read_file",path,r); return r
        elif cls.intent=="file_write":
            path=cls.params.get("path","") or self._infer(desc)
            if not path: return ToolResult(False,"","write_file","","No path")
            self.write_count+=1
            c=self.app._gen_content(desc,path)
            if not c: return ToolResult(False,"","write_file",path,"Empty")
            r=self.app.file_tools.write_file(path,c); self.app.log_tool("write_file",path,r); return r
        elif cls.intent=="file_edit":
            path=cls.params.get("path","") or self._infer(desc)
            if not path: return ToolResult(False,"","edit_file","","No path")
            self.write_count+=1
            rr=self.app.file_tools.read_file(path)
            if not rr.success: return rr
            ep=self.app._gen_edit(desc,path,rr.output)
            if not ep: return ToolResult(False,"","edit_file",path,"No edit")
            self.app._show_diff(path,ep[0],ep[1])
            r=self.app.file_tools.edit_file(path,ep[0],ep[1])
            if not r.success: r=self.app.file_tools.edit_file_diff(path,ep[0],ep[1])
            self.app.log_tool("edit_file",path,r); return r
        elif cls.intent=="shell":
            cmd=cls.params.get("command","")
            if not cmd: return ToolResult(False,"","execute_command","","No cmd")
            self.shell_count+=1
            risk=self.app.permission_manager.check(cmd,cwd)
            if risk=="deny": return ToolResult(False,"","execute_command",cmd,"Denied")
            if cmd.strip().startswith("rm"): self.rm_count+=1
            if risk=="ask" and not self.auto_approve:
                if cmd not in self.session_allowed:
                    a=self._ask_approve(cmd)
                    if a=="deny": return ToolResult(False,"","execute_command",cmd,"Denied")
                    if a=="task": self.session_allowed.append(cmd)
            r=self.app.shell_tool.execute_command(cmd,cwd,net=self.app._needs_net(cmd))
            if "pytest" in cmd and r.success:
                ts=ShellTool.parse_tests(r.output)
                if ts["failed"]>0: r=ToolResult(False,r.output,"execute_command",cmd,f"{ts['failed']} tests failed")
            return r
        else:
            resp=self._llm([{"role":"system","content":self.app.get_system_prompt()},{"role":"user","content":f"Step: {desc}\nActions?"}])
            return ToolResult(True,resp[:500],"llm",desc)

    def _retry(self, step):
        desc=step["description"]; cls=self.app.router.classify(desc)
        if cls.intent=="file_edit":
            path=cls.params.get("path","") or self._infer(desc)
            if not path: return None
            rr=self.app.file_tools.read_file(path)
            if not rr.success: return None
            ep=self.app._gen_edit(f"RETRY: {desc}",path,rr.output)
            if not ep: return None
            r=self.app.file_tools.edit_file_diff(path,ep[0],ep[1])
            self.app.log_tool("edit_file_diff",path,r); return r
        return None

    def _infer(self, desc):
        p=self.app.router._p(desc)
        if p: return p
        for m in re.finditer(r'[\w./\-]+\.\w+',desc):
            if not m.group(0).startswith(("http","www")): return m.group(0)
        return ""

    def _ask_approve(self, cmd):
        if not sys.stdin.isatty(): return "deny"
        self.app.console.print(Panel(f"[bold]Command:[/bold] {cmd}\n[bold]Step:[/bold] {self.current_step+1}/{len(self.plan)}",title="⚡ Approval",border_style="yellow"))
        c=questionary.select("  Allow?",choices=["Allow once","Allow for this task","Deny"]).ask()
        if c=="Allow once": return "allow"
        if c=="Allow for this task": return "task"
        return "deny"

    def _guard(self):
        if self.shell_count>=MAX_SHELL_PER_TASK: return f"Max commands ({MAX_SHELL_PER_TASK})"
        if self.rm_count>=MAX_RM_PER_TASK: return f"Max rm ({MAX_RM_PER_TASK})"
        if self.write_count>=MAX_WRITES_PER_TASK: return f"Max writes ({MAX_WRITES_PER_TASK})"
        if self.start_time and time.time()-self.start_time>MAX_TASK_TIME: return "Max time (2h)"
        if time.time()-self.last_progress>NO_PROGRESS_TIMEOUT: return "No progress (20m)"
        return None

    def _complete(self):
        self.status="completed"; el=int((time.time()-self.start_time)/60) if self.start_time else 0
        self._update()
        self.app.console.print(Panel(f"✓ {len(self.plan)} steps done\nTime: {el}m | LLM: {self.llm_calls} | Cmds: {self.shell_count}",title="🎉 Complete",border_style="green"))
        self.save_cp(); self.app.write_note("success",f"Done in {el}m")
        self.app._notify("Pincer task complete! 🎉")  # #9

    def _eval(self):
        d=sum(1 for s in self.plan if s["status"]=="done"); f=sum(1 for s in self.plan if s["status"]=="failed")
        try:
            r=self._llm([{"role":"user","content":f"{d}/{len(self.plan)} done, {f} failed. Progress? Adjust? Brief."}])
            self.app.console.print(f"  💭 {r[:200]}",style="dim italic")
        except: pass

    def save_cp(self):
        if not self.task_id: return
        conv=self.app.conn.execute("SELECT role,content FROM conversation ORDER BY id ASC").fetchall()
        gh=""
        try:
            r=subprocess.run(["git","rev-parse","HEAD"],capture_output=True,text=True)
            if r.returncode==0: gh=r.stdout.strip()
        except: pass
        self.app.conn.execute("INSERT INTO checkpoints (task_id,step_index,conversation_snapshot,working_directory,git_commit_hash) VALUES (?,?,?,?,?)",
            (self.task_id,self.current_step,json.dumps(conv),os.getcwd(),gh)); self.app.conn.commit()

    def pause(self): self.status="paused"; self.save_cp(); self._update(); self.app.console.print("  ⏸️ Paused.",style="yellow")
    def abort(self):
        self.status="aborted"; self._update(); el=int((time.time()-self.start_time)/60) if self.start_time else 0
        d=sum(1 for s in self.plan if s["status"]=="done")
        self.app.console.print(f"  🛑 {d}/{len(self.plan)} in {el}m.",style="red")

    def display_plan(self):
        if not self.plan: return
        t=Table(title="📋 Plan",border_style=BLUE,padding=(0,1))
        t.add_column("",width=3); t.add_column("#",width=3); t.add_column("Description")
        for s in self.plan:
            ic="✅" if s["status"]=="done" else ("❌" if s["status"]=="failed" else "⬜")
            mk="▸" if s["step"]-1==self.current_step else " "
            t.add_row(ic,str(s["step"]),f"{mk} {s['description']}")
        self.app.console.print(t)

    def progress_text(self):
        if self.status=="idle" or not self.plan: return "idle"
        return f"step {self.current_step+1}/{len(self.plan)}"

    def cost(self):
        el=int((time.time()-self.start_time)/60) if self.start_time else 0
        return f"LLM: {self.llm_calls} | Cmds: {self.shell_count} | Writes: {self.write_count} | {el}m"

    def health(self):
        if not self.plan: return 1.0
        d=sum(1 for s in self.plan if s["status"]=="done"); f=sum(1 for s in self.plan if s["status"]=="failed")
        return max(0,(d-f*2)/len(self.plan))

# ═══════════════════════════════════════════════════════════════════════════════
#  PINCER APP — Main Application
# ═══════════════════════════════════════════════════════════════════════════════

class PincerApp:
    def __init__(self):
        self.console=Console()
        self.err=Console(stderr=True,style="bold red")
        self.conn=None; self.model=DEFAULT_MODEL; self.thinking=False
        self.user_name=""; self.lang="python"; self.session=None
        self._gen=False; self._vec=False; self._emb=False
        self._memory=""; self._loop=None; self._tc=0; self._tct=0.0
        self._rmap=None; self._dry_run=False  # #36
        self.watcher=FileWatcher()  # #39
        self.router=ToolRouter(); self.ft=FileTools(); self.st=ShellTool(self.console)
        self.pm=PermissionManager(); self.al=AutonomousLoop(self)
        self._partial_resp=""  # #6: resume after interrupt

    def _get_loop(self):
        if self._loop is None or self._loop.is_closed(): self._loop=asyncio.new_event_loop()
        return self._loop
    def _run_llm_sync(self, msgs):
        # #42: Scrub PII before sending
        scrubbed = [dict(role=m["role"], content=scrub_pii(m["content"])) for m in msgs]
        lp=self._get_loop()
        try:
            r=lp.run_until_complete(lp.run_in_executor(None,lambda:ollama.chat(model=self.model,messages=scrubbed,stream=False)))
            return self._chat_content(r)
        except RuntimeError:
            nl=asyncio.new_event_loop()
            try: r=nl.run_until_complete(nl.run_in_executor(None,lambda:ollama.chat(model=self.model,messages=scrubbed,stream=False))); return self._chat_content(r)
            finally: nl.close()
    @staticmethod
    def _parse_models(r): return [{"model":m.model,"size":m.size} for m in r.models] if hasattr(r,"models") else r.get("models",[])
    @staticmethod
    def _ga(o,k,d=None): return o.get(k,d) if isinstance(o,dict) else getattr(o,k,d)
    
    @staticmethod
    def _chunk_c(c):
        m = getattr(c, "message", None)
        if hasattr(m, "content"): return m.content or ""
        if isinstance(c, dict): return c.get("message", {}).get("content", "") or ""
        return ""
        
    @staticmethod
    def _chat_content(r):
        m = getattr(r, "message", None)
        if hasattr(m, "content"): return m.content or ""
        if isinstance(r, dict): return r.get("message", {}).get("content", "") or ""
        return ""
        
    @staticmethod
    def _get_emb(r): return r.get("embedding",[]) if isinstance(r,dict) else getattr(r,"embedding",[])
    @staticmethod
    def ct(t): return max(1,len(t)//CHARS_PER_TOKEN) if t else 0

    # ── Database ───────────────────────────────────────────────────────────

    def setup_db(self):
        PINCER_DIR.mkdir(parents=True,exist_ok=True); SESSIONS_DIR.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(str(DB_PATH)); self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("CREATE TABLE IF NOT EXISTS user_info (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS conversation (id INTEGER PRIMARY KEY AUTOINCREMENT,role TEXT CHECK(role IN ('system','user','assistant','summary')),content TEXT,tokens INTEGER DEFAULT 0,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS tool_history (id INTEGER PRIMARY KEY AUTOINCREMENT,tool_name TEXT,command TEXT,status TEXT,output TEXT,timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT,goal TEXT,plan TEXT,current_step INTEGER DEFAULT 0,total_steps INTEGER DEFAULT 0,status TEXT DEFAULT 'planning',auto_approve BOOLEAN DEFAULT FALSE,llm_calls INTEGER DEFAULT 0,shell_count INTEGER DEFAULT 0,write_count INTEGER DEFAULT 0,rm_count INTEGER DEFAULT 0,elapsed_minutes INTEGER DEFAULT 0,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,completed_at TIMESTAMP)")
        for c,t in [("llm_calls","INTEGER DEFAULT 0"),("shell_count","INTEGER DEFAULT 0"),("write_count","INTEGER DEFAULT 0"),("rm_count","INTEGER DEFAULT 0"),("elapsed_minutes","INTEGER DEFAULT 0")]:
            try: self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {c} {t}")
            except: pass
        self.conn.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER,category TEXT CHECK(category IN ('observation','error','success','preference','pattern')),content TEXT,embedding BLOB,timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS checkpoints (id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER,step_index INTEGER,conversation_snapshot TEXT,working_directory TEXT,git_commit_hash TEXT,timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        # #44: Budget tracking table
        self.conn.execute("CREATE TABLE IF NOT EXISTS budgets (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT,metric TEXT,value INTEGER,timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        self.conn.commit()
        self._setup_vec(); self._load_allowed()
    def _setup_vec(self):
        self._vec=False
        try:
            import sqlite_vec; self.conn.enable_load_extension(True); sqlite_vec.load(self.conn); self.conn.enable_load_extension(False)
            self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS tool_history_vec USING vec0(id INTEGER PRIMARY KEY,embedding float[{EMBEDDING_DIM}])")
            self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS notes_vec USING vec0(id INTEGER PRIMARY KEY,embedding float[{EMBEDDING_DIM}])")
            self.conn.commit(); self._vec=True
        except: pass
        self._emb=self.check_model(EMBEDDING_MODEL)
    def _load_allowed(self):
        r=self.gc("allowed_commands")
        if r:
            try: self.pm._always=json.loads(r)
            except: pass
        self.pm.load_policy(POLICY_FILE)  # #41
    def _save_allowed(self): self.sc("allowed_commands",json.dumps(self.pm._always))
    def gc(self,k): r=self.conn.execute("SELECT value FROM user_info WHERE key=?",(k,)).fetchone(); return r[0] if r else None
    def sc(self,k,v): self.conn.execute("INSERT OR REPLACE INTO user_info (key,value) VALUES (?,?)",(k,v)); self.conn.commit()
    def load_config(self):
        self.user_name=self.gc("user_name") or ""; self.lang=self.gc("preferred_language") or "python"
        self.model=self.gc("model") or DEFAULT_MODEL; self.thinking=self.gc("thinking_mode")=="on"

    # ── Tool history + Notes ───────────────────────────────────────────────

    def log_tool(self,n,c,r):
        s="denied" if "denied" in r.error.lower() else ("success" if r.success else "failure")
        o=(r.output if r.success else r.error)[:2000]
        rid=self.conn.execute("INSERT INTO tool_history (tool_name,command,status,output) VALUES (?,?,?,?)",(n,c[:500],s,o)).lastrowid
        self.conn.commit()
        if self._vec and self._emb: self._store_emb("tool_history_vec",rid,f"{n} {c} {o}")
    def _store_emb(self,tbl,rid,txt):
        ok={"tool_history_vec","notes_vec"}
        if tbl not in ok: return
        try:
            r=ollama.embeddings(model=EMBEDDING_MODEL,prompt=txt); e=self._get_emb(r)
            if e and len(e)==EMBEDDING_DIM:
                import struct; vb=struct.pack(f"{len(e)}f",*e)
                self.conn.execute(f"INSERT INTO {tbl} (id,embedding) VALUES (?,?)",(rid,vb)); self.conn.commit()
        except: pass
    def rel_hist(self,q,lim=3):
        if not q: return ""
        if self._vec and self._emb:
            try:
                r=ollama.embeddings(model=EMBEDDING_MODEL,prompt=q); e=self._get_emb(r)
                if e and len(e)==EMBEDDING_DIM:
                    import struct; vb=struct.pack(f"{len(e)}f",*e)
                    rows=self.conn.execute("SELECT t.tool_name,t.command,t.status,t.output FROM tool_history t JOIN tool_history_vec v ON t.id=v.id WHERE v.embedding MATCH ? ORDER BY v.distance LIMIT ?",(vb,lim)).fetchall()
                    if rows: return "\n".join(["[History]"]+[f"  {'✓' if r[2]=='success' else '✗'} {r[0]}: {r[1][:80]} → {r[2]}" for r in rows])
            except: pass
        ws=re.findall(r"\w+",q)
        if not ws: return ""
        cd=" OR ".join("command LIKE ?" for _ in ws); ps=[f"%{w}%" for w in ws]+[lim]
        rows=self.conn.execute(f"SELECT tool_name,command,status FROM tool_history WHERE {cd} ORDER BY id DESC LIMIT ?",ps).fetchall()
        return "\n".join(["[History]"]+[f"  {'✓' if r[2]=='success' else '✗'} {r[0]}: {r[1][:80]} → {r[2]}" for r in rows]) if rows else ""
    def write_note(self,cat,content):
        tid=self.al.task_id
        # #40: Auto-expire check — if success note for same category as recent error, expire the error
        if cat=="success":
            self.conn.execute("DELETE FROM notes WHERE category='error' AND task_id=? AND content LIKE ?",(tid,f"%{content[:30]}%"))
        self.conn.execute("INSERT INTO notes (task_id,category,content) VALUES (?,?,?)",(tid,cat,content[:2000]))
        nid=self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]; self.conn.commit()
        if self._vec and self._emb: self._store_emb("notes_vec",nid,content)
    def rel_notes(self,q,lim=3):
        if not q: return ""
        if self._vec and self._emb:
            try:
                r=ollama.embeddings(model=EMBEDDING_MODEL,prompt=q); e=self._get_emb(r)
                if e and len(e)==EMBEDDING_DIM:
                    import struct; vb=struct.pack(f"{len(e)}f",*e)
                    rows=self.conn.execute("SELECT n.category,n.content FROM notes n JOIN notes_vec nv ON n.id=nv.id WHERE nv.embedding MATCH ? ORDER BY nv.distance LIMIT ?",(vb,lim)).fetchall()
                    if rows:
                        ic={"success":"✅","error":"❌","observation":"👁","preference":"⚙️","pattern":"🔄"}
                        return "\n".join(["[Notes]"]+[f"  {ic.get(r[0],'📝')} {r[1][:150]}" for r in rows])
            except: pass
        rows=self.conn.execute("SELECT category,content FROM notes WHERE content LIKE ? ORDER BY id DESC LIMIT ?",(f"%{q[:50]}%",lim)).fetchall()
        return "\n".join(["[Notes]"]+[f"  📝 {r[1][:150]}" for r in rows]) if rows else ""

    # ── RepoMap #11 ────────────────────────────────────────────────────────

    def build_rmap(self,depth=3):
        if self._rmap: return self._rmap
        syms={}
        for root,dirs,files in os.walk("."):
            dirs[:]=[d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            if root.count(os.sep)>depth: dirs.clear(); continue
            for f in files:
                ext=Path(f).suffix
                if ext not in (".py",".js",".ts") or ext in BINARY_EXT: continue
                fp=os.path.join(root,f)
                try:
                    c=Path(fp).read_text(encoding="utf-8",errors="replace")
                    if ext==".py": ds=re.findall(r"^(?:class|def|async def)\s+(\w+)",c,re.M)
                    else: ds=re.findall(r"(?:function|class|const|let|var)\s+(\w+)",c)
                    if ds: syms[fp]=ds[:20]
                except: pass
        self._rmap=syms; return syms

    # ── Ollama ────────────────────────────────────────────────────────────

    @staticmethod
    def check_ollama(): return shutil.which("ollama") is not None
    @staticmethod
    def check_server():
        try: ollama.list(); return True
        except: return False
    def check_model(self,n):
        try:
            for m in self._parse_models(ollama.list()):
                nm=m.get("model","")
                if nm==n or nm.startswith(n.split(":")[0]+":"): return True
            return False
        except: return False
    def pull_model(self,n):
        self.console.print(f"  Pulling {n}…",style=BLUE)
        try:
            for c in ollama.pull(n,stream=True):
                t=self._ga(c,"total",0); co=self._ga(c,"completed",0)
                if t and t>0:
                    p=int(co/t*100); w=20; f=int(w*co/t)
                    sys.stdout.write(f"\r  [{'█'*f}{'░'*(w-f)}] {p}% "); sys.stdout.flush()
                elif "success" in self._ga(c,"status",""): break
            sys.stdout.write(f"\r  ✓ {n} done.          \n"); sys.stdout.flush()
        except Exception as e: self.err.print(f"\n  ❌ Pull failed: {e}"); sys.exit(1)

    # ── First-run wizard ───────────────────────────────────────────────────

    def wizard(self):
        self.console.print(Panel(Text.from_markup("[bold blue]⬡ Pincer[/bold blue] — local AI assistant\n\nSetup takes ~2 minutes."),border_style=BLUE,title="Welcome"))
        if not self.check_ollama(): self.console.print("❌ Ollama not found.\n   brew install ollama\n   https://ollama.com",style="bold red"); sys.exit(1)
        self.console.print("  ✓ Ollama found",style="green")
        if not self.check_server(): self.console.print("❌ Start: ollama serve",style="bold red"); sys.exit(1)
        self.console.print("  ✓ Server running",style="green")
        if not self.check_model(self.model): self.pull_model(self.model)
        else: self.console.print(f"  ✓ {self.model}",style="green")
        if not self.check_model(EMBEDDING_MODEL): self.console.print(f"  ⚠ Pulling {EMBEDDING_MODEL}…",style="yellow"); self.pull_model(EMBEDDING_MODEL)
        else: self.console.print(f"  ✓ {EMBEDDING_MODEL}",style="green")
        nm="user"
        if sys.stdin.isatty(): nm=questionary.text("  Your name?",default="user").ask() or "user"
        self.user_name=nm.strip() or "user"; self.sc("user_name",self.user_name)
        lg="python"
        if sys.stdin.isatty(): lg=questionary.select("  Language?",choices=["python","javascript","typescript","rust","go","java","c","cpp","ruby","other"],default="python").ask() or "python"
        self.lang=lg; self.sc("preferred_language",lg)
        if sys.stdin.isatty(): self._proj_setup()
        self.sc("model",self.model); self.sc("thinking_mode","off")
        # #41: Create default policy
        if not POLICY_FILE.exists():
            POLICY_FILE.write_text(json.dumps({"deny":["^sudo","^mkfs","^dd if="],"allow":[]},indent=2))
        self.console.print(Panel("✓ All set!\n\n[bold]Try:[/bold] hello, read README, /help",border_style="green",title="Ready"))
    def _proj_setup(self):
        if not Path(".git").exists():
            if questionary.confirm("  Init git?",default=True).ask():
                try: subprocess.run(["git","init"],capture_output=True,check=True); self.console.print("  ✓ Git init",style="green")
                except: pass
        for mf in MEMORY_FILES:
            if Path(mf).exists(): self._memory=Path(mf).read_text(encoding="utf-8",errors="replace")[:4000]; self.console.print(f"  ✓ {mf}",style="green"); break
        else: self._scan_memory()
    def _scan_memory(self):
        facts=[]
        for f,l in [("requirements.txt","Python"),("pyproject.toml","Python"),("package.json","Node.js"),("Cargo.toml","Rust"),("go.mod","Go")]:
            if Path(f).exists(): facts.append(f"{l} ({f})")
        if not facts: return
        if questionary.confirm(f"  Detected: {', '.join(facts)}. Create .pincer.md?",default=True).ask():
            c="# Project\n\n## Auto-detected\n"+"\n".join(f"- {f}" for f in facts)+"\n"
            Path(".pincer.md").write_text(c); self._memory=c; self.console.print("  ✓ .pincer.md",style="green")

    # ── System prompt ──────────────────────────────────────────────────────

    def sys_prompt(self):
        p=f"You are Pincer, a helpful coding assistant on the user's Mac.\nUser: {self.user_name} | Lang: {self.lang}\nBe concise. Markdown for code.\nYou have file tools (read/write/edit) and shell execution.\nAnalyse [Tool: …] blocks and respond.\nIf failed, suggest a fix."
        if self._memory: p+=f"\n\nProject:\n{self._memory[:2000]}"
        if self.thinking: p+="\nThink step by step."
        return p

    # ── Context management (cached tokens) ────────────────────────────────

    def _inv_tc(self): self._tct=0.0
    def total_tokens(self):
        if time.time()-self._tct<2 and self._tc>0: return self._tc
        r=self.conn.execute("SELECT COALESCE(SUM(tokens),0) FROM conversation").fetchone()
        db=r[0] if r else 0; st=self.ct(self.sys_prompt()); self._tc=db+st; self._tct=time.time(); return self._tc
    def msg_count(self): return (self.conn.execute("SELECT COUNT(*) FROM conversation").fetchone() or [0])[0]
    def save_msg(self,role,content):
        self.conn.execute("INSERT INTO conversation (role,content,tokens) VALUES (?,?,?)",(role,content,self.ct(content))); self.conn.commit(); self._inv_tc()
    def get_msgs(self):
        rows=self.conn.execute("SELECT role,content FROM conversation ORDER BY id ASC").fetchall()
        return [{"role":"system","content":self.sys_prompt()}]+[{"role":"system" if r=="summary" else r,"content":c} for r,c in rows]
    def compact(self,manual=False):
        rows=self.conn.execute("SELECT id,role,content FROM conversation ORDER BY id ASC").fetchall()
        if len(rows)<4:
            if manual: self.console.print("  ⚠ Need ≥ 4 msgs.",style="yellow")
            return
        sp=len(rows)//2; old=rows[:sp]; ct="\n\n".join(f"{r[1]}: {r[2]}" for r in old)
        try:
            with self.console.status("  [bold blue]Compacting…[/]"): sm=self._run_llm_sync([{"role":"user","content":f"Summarise:\n\n{ct}"}])
        except Exception as e: self.console.print(f"  ❌ {e}",style="bold red"); return
        ids=[r[0] for r in old]; ph=",".join("?" for _ in ids)
        self.conn.execute(f"DELETE FROM conversation WHERE id IN ({ph})",ids)
        self.conn.execute("INSERT INTO conversation (role,content,tokens) VALUES (?,?,?)",("summary",sm,self.ct(sm))); self.conn.commit(); self._inv_tc()
        self.console.print(f"  ✓ {len(old)} → 1 summary",style="green")
    def auto_compact(self):
        tt=self.total_tokens()
        if tt>COMPACT_THRESHOLD: self.console.print(f"  ⚡ {tt/1000:.1f}K — compacting…",style="yellow"); self.compact()
        tt=self.total_tokens(); i=0
        while tt>MAX_TOKENS and i<200:
            i+=1; r=self.conn.execute("SELECT id FROM conversation WHERE role='user' ORDER BY id ASC LIMIT 1").fetchone()
            if not r:
                o=self.conn.execute("SELECT id FROM conversation ORDER BY id ASC LIMIT 1").fetchone()
                if not o: break
                self.conn.execute("DELETE FROM conversation WHERE id=?",(o[0],))
            else:
                self.conn.execute("DELETE FROM conversation WHERE id=?",(r[0],))
                n=self.conn.execute("SELECT id FROM conversation WHERE id>? AND role='assistant' ORDER BY id ASC LIMIT 1",(r[0],)).fetchone()
                if n: self.conn.execute("DELETE FROM conversation WHERE id=?",(n[0],))
            self.conn.commit(); tt=self.total_tokens()

    # ── Thinking tags ─────────────────────────────────────────────────────

    @staticmethod
    def strip_think(t): return re.sub(r"<think[^>]*>.*?</think\s*>","",t,flags=re.DOTALL).strip()
    @staticmethod
    def _ptl(b,t):
        for i in range(1,min(len(t)+1,len(b)+1)):
            if b[-i:]==t[:i]: return i
        return 0

    # ── Streaming (#1 markdown, #2 syntax) ────────────────────────────────

    def stream(self, msgs):
        full = ""; int_ = False; ts = False; buf = ""
        try:
            self._gen = True
            for ch in ollama.chat(model=self.model, messages=msgs, stream=True):
                if not self._gen: break
                if self._ga(ch, "done", False): break
                tok = self._chunk_c(ch)
                if not tok: continue
                full += tok
                if not self.thinking:
                    self.console.print(tok, end="")
                    continue
                
                buf += tok
                ch2 = True
                while ch2:
                    ch2 = False
                    if not int_:
                        oi = buf.find(THINK_TAG_OPEN)
                        if oi != -1:
                            if oi > 0: self.console.print(buf[:oi], end="")
                            af = buf[oi + len(THINK_TAG_OPEN):]
                            gt = af.find(">")
                            if gt != -1:
                                buf = af[gt + 1:]
                                int_ = True
                                if not ts and int_:
                                    self.console.print("  ● Thinking…", style=BLUE)
                                    ts = True
                                ch2 = True
                            else:
                                buf = buf[oi:]
                        else:
                            pt = self._ptl(buf, THINK_TAG_OPEN)
                            s = buf[:len(buf)-pt] if pt else buf
                            if s: self.console.print(s, end="")
                            buf = buf[len(s):]
                    else:
                        ci = buf.find(THINK_TAG_CLOSE)
                        if ci != -1:
                            af = buf[ci + len(THINK_TAG_CLOSE):]
                            gt = af.find(">")
                            if gt != -1:
                                buf = af[gt + 1:]
                                int_ = False
                                ch2 = True
                            else:
                                buf = buf[ci:]
                        else:
                            pt = self._ptl(buf, THINK_TAG_CLOSE)
                            buf = buf[-pt:] if pt else ""
                if buf and not int_:
                    self.console.print(buf, end="")
            self.console.print()
        except KeyboardInterrupt:
            self._gen = False
            self._partial_resp = full
            self.console.print("\n  ⏹ Stopped. Type /resume to continue.", style="yellow")
        except Exception as e:
            self._gen = False
            self.console.print(f"\n  ❌ {e}", style="bold red")
        self._gen = False
        return full

    # ── Status bar ────────────────────────────────────────────────────────

    def _sbar(self):
        tt=self.total_tokens(); ctx=f"{tt/1000:.1f}K"; th="on" if self.thinking else "off"
        sb="🔒" if self.st.sandbox_available else "🔓"
        tk=self.al.progress_text(); el=""
        if self.al.start_time and self.al.status=="active": el=f" | ⏱{int((time.time()-self.al.start_time)/60)}m"
        ap=" 🟢" if self.al.auto_approve else ""
        dry=" 🏗" if self._dry_run else ""
        return HTML(f"<style bg='ansiblack' fg='ansiwhite'> {self.model} | thinking:{th} | ctx: {ctx}/12K | {sb} | task: {tk}{el}{ap}{dry}</style>")

    # ── Git + Diff ────────────────────────────────────────────────────────

    @staticmethod
    def _auto_commit(msg):
        if not Path(".git").exists(): return
        try: subprocess.run(["git","add","-A"],capture_output=True,check=True); subprocess.run(["git","commit","-m",msg],capture_output=True,check=True)
        except: pass
    def _show_diff(self,path,old,new):
        d=FileTools.compute_diff(old,new,path)
        if not d: return
        try: self.console.print(Panel(Syntax(d,"diff",theme="monokai",line_numbers=False),title=f"📝 Diff: {path}",border_style="yellow",padding=(0,1)))
        except: self.console.print(Panel(d[:2000],title=f"📝 Diff: {path}",border_style="yellow"))

    # ── Tool execution ────────────────────────────────────────────────────

    def _gen_content(self,desc,path):
        pr=f"Generate complete content for '{path}' based on: {desc}\nOutput ONLY file content. No fences."
        try:
            with self.console.status("  [bold blue]Generating…[/]"): c=self._run_llm_sync([{"role":"user","content":pr}])
            c=re.sub(r"^```[\w]*\n","",c); c=re.sub(r"\n```$","",c); return c.strip()+"\n"
        except Exception as e: self.console.print(f"  ❌ {e}",style="bold red"); return ""
    def _gen_edit(self,req,path,content):
        pr=f"Modify '{path}'.\n\nCurrent:\n{content}\n\nRequest: {req}\n\nEXACT format:\n<<<OLD>>>\nexact lines\n<<<NEW>>>\nreplacement lines"
        try:
            with self.console.status("  [bold blue]Edit…[/]"): t=self._run_llm_sync([{"role":"user","content":pr}])
            m=re.search(r"<<<OLD>>>\s*\n(.*?)<<<NEW>>>\s*\n(.*)",t,re.DOTALL)
            if not m: return None
            return m.group(1).rstrip("\n"),re.sub(r"\n```\s*$","",m.group(2).rstrip("\n"))
        except Exception as e: self.console.print(f"  ❌ {e}",style="bold red"); return None

    def _approve(self,cmd,cwd,risk):
        if not sys.stdin.isatty(): return "deny"
        cs={"safe":"green","ask":"yellow","deny":"red"}; ic={"safe":"✅","ask":"⚠️","deny":"🚫"}
        c=cs.get(risk,"yellow"); i=ic.get(risk,"⚠️"); si=f"\nStep: {self.al.current_step+1}/{len(self.al.plan)}" if self.al.status=="active" else ""
        self.console.print(Panel(f"[bold]Command:[/bold] {cmd}\n[bold]Dir:[/bold] {cwd}\n[bold]Risk:[/bold] [{c}]{i} {risk.upper()}[/{c}]{si}",title="⚡ Approval",border_style=c))
        ch=questionary.select("  Choose:",choices=["Allow once","Allow always","Allow for this task","Deny","Edit command"]).ask()
        if ch=="Allow once": return "allow"
        if ch=="Allow always": self.pm.add_allowed(cmd); self._save_allowed(); return "allow"
        if ch=="Allow for this task": self.al.session_allowed.append(cmd); return "allow"
        if ch=="Edit command":
            ed=questionary.text("  Edit:",default=cmd).ask()
            return f"edit:{ed.strip()}" if ed and ed.strip() else "deny"
        return "deny"

    def _trunc(self,t,mt=MAX_TOOL_TOKENS):
        ml=mt*CHARS_PER_TOKEN
        return t[:ml]+f"\n... [{len(t)-ml} cut]" if len(t)>ml else t

    def _render_file(self,path,content):
        """#1: Syntax-highlighted file rendering."""
        ext=Path(path).suffix.lstrip(".") or "text"
        raw=re.sub(r'^\s*\d+\s*│\s?','',content,flags=re.MULTILINE)
        try: self.console.print(Syntax(raw,ext,theme="monokai",line_numbers=True,word_wrap=True))
        except: self.console.print(content[:3000])

    def handle_tool(self,msg,intent,params):
        self.auto_compact(); self.save_msg("user",msg); h=self.rel_hist(msg); n=self.rel_notes(msg); cwd=os.getcwd()
        if intent=="file_read":
            path=params.get("path","")
            if not path: self.console.print("  ⚠ No path.",style="yellow"); return
            r=self.ft.read_file(path); self.log_tool("read_file",path,r)
            if r.success: self._render_file(path,r.display)
            else: self.console.print(Panel(r.error,title=f"❌ {path}",border_style="red"))
            self.save_msg("system",f"[Tool: read('{path}')] {'OK' if r.success else 'FAIL'}\n{self._trunc(r.display)}[/Tool]")
        elif intent=="file_write":
            path=params.get("path",""); desc=params.get("description","")
            if not path: self.console.print("  ⚠ No path.",style="yellow"); return
            c=params.get("content","")
            if not c: c=self._gen_content(desc,path)
            if not c: self.save_msg("system",f"[Tool: write('{path}') FAIL] Empty[/Tool]"); return
            if self._dry_run: self.console.print(f"  🏗 Dry run: would write {path}",style="blue"); return  # #36
            r=self.ft.write_file(path,c); self.log_tool("write_file",path,r)
            self.console.print(Panel(r.display,title=f"📝 write: {path}",border_style="green" if r.success else "red"))
            if r.success: self._auto_commit(f"write: {path}")
            self.write_note("success" if r.success else "error",f"write {path}: {r.display[:200]}")
            self.save_msg("system",f"[Tool: write('{path}')] {'OK' if r.success else 'FAIL'}\n{r.display}[/Tool]")
        elif intent=="file_edit":
            path=params.get("path","")
            if not path: self.console.print("  ⚠ No path.",style="yellow"); return
            os=params.get("old_string",""); ns=params.get("new_string","")
            if not os or not ns:
                rr=self.ft.read_file(path)
                if not rr.success: self.log_tool("edit_file",path,rr); self.console.print(Panel(rr.error,title=f"❌ {path}",border_style="red")); return
                ep=self._gen_edit(msg,path,rr.output)
                if not ep: self.save_msg("system",f"[Tool: edit('{path}') FAIL] Parse error[/Tool]"); return
                os,ns=ep
            try:
                with open(path,"r",encoding="utf-8") as f: cur=f.read()
                self._show_diff(path,os,ns)
            except: pass
            if self._dry_run: self.console.print(f"  🏗 Dry run: would edit {path}",style="blue"); return
            r=self.ft.edit_file(path,os,ns)
            if not r.success: r=self.ft.edit_file_diff(path,os,ns)
            self.log_tool("edit_file",path,r)
            self.console.print(Panel(r.display,title=f"✏️ edit: {path}",border_style="green" if r.success else "red"))
            if r.success: self._auto_commit(f"edit: {path}")
            self.write_note("success" if r.success else "error",f"edit {path}: {r.display[:200]}")
            self.save_msg("system",f"[Tool: edit('{path}')] {'OK' if r.success else 'FAIL'}\n{r.display}[/Tool]")
        elif intent=="shell":
            cmd=params.get("command","")
            if not cmd: self.console.print("  ⚠ No cmd.",style="yellow"); return
            risk=self.pm.check(cmd,cwd)
            if risk=="deny":
                self.console.print(Panel(f"🚫 {cmd}",border_style="red",title="Blocked"))
                self.log_tool("execute_command",cmd,ToolResult(False,"","execute_command",cmd,"Denied"))
                self.write_note("error",f"🚫 {cmd}")
                self.save_msg("system",f"[Tool: DENIED] {cmd}[/Tool]")
            elif risk=="ask":
                a=self._approve(cmd,cwd,"ask")
                if a=="deny":
                    self.log_tool("execute_command",cmd,ToolResult(False,"","execute_command",cmd,"Denied"))
                    self.write_note("error",f"Denied: {cmd}")
                    self.save_msg("system",f"[Tool: DENIED] {cmd}[/Tool]")
                elif a.startswith("edit:"):
                    ec=a[5:]
                    if self.pm.check(ec,cwd)=="deny": self.save_msg("system",f"[Tool: DENIED] {ec}[/Tool]")
                    else: r=self.st.execute_command(ec,cwd,net=self._needs_net(ec)); self._shell_result(ec,r)
                else: r=self.st.execute_command(cmd,cwd,net=self._needs_net(cmd)); self._shell_result(cmd,r)
            else: r=self.st.execute_command(cmd,cwd,net=self._needs_net(cmd)); self._shell_result(cmd,r)
        ms=self.get_msgs()
        if h: ms.insert(1,{"role":"system","content":h})
        if n: ms.insert(1,{"role":"system","content":n})
        self.console.print(); resp=self.stream(ms); self.console.print()
        if resp.strip(): self.save_msg("assistant",resp)

    def _shell_result(self,cmd,r):
        self.log_tool("execute_command",cmd,r)
        b="green" if r.success else "red"
        t=f"🔧 {cmd[:50]}" + (f" ({r.error})" if not r.success else "")
        self.console.print(Panel(r.truncated(),title=t,border_style=b))
        self.save_msg("system",f"[Tool: cmd('{cmd}')] {'OK' if r.success else 'FAIL'}\n{self._trunc(r.display)}[/Tool]")

    @staticmethod
    def _needs_net(c):
        for p in ["pip install","npm install","cargo","git clone","git pull","git push","git fetch","brew","curl","wget"]:
            if c.strip().startswith(p): return True
        return False

    # ── #9: Desktop notifications ─────────────────────────────────────────

    def _notify(self, msg):
        if sys.platform=="darwin":
            try: subprocess.run(["osascript","-e",f'display notification "{msg}" with title "Pincer"'],capture_output=True)
            except: pass

    # ── #6: Resume after interrupt ────────────────────────────────────────

    def cmd_resume_interrupt(self):
        if self._partial_resp:
            self.console.print("  Continuing from interrupted response…",style=BLUE)
            msgs=self.get_msgs()
            msgs.append({"role":"user","content":"Continue from where you left off."})
            self.console.print(); resp=self.stream(msgs); self.console.print()
            if resp.strip(): self.save_msg("assistant",self._partial_resp+resp)
            self._partial_resp=""
        else: self.console.print("  Nothing to resume.",style="dim")

    # ── Checkpoint / Resume ───────────────────────────────────────────────

    def save_cp(self): self.al.save_cp(); self.console.print("  ✓ Checkpoint saved.",style="green")
    def resume_task(self, tid=None):
        if tid is None:
            r=self.conn.execute("SELECT id,goal,status FROM tasks WHERE status IN ('paused','active','stuck') ORDER BY updated_at DESC LIMIT 1").fetchone()
            if not r: self.console.print("  ⚠ No paused tasks.",style="yellow"); return
            tid=r[0]; self.console.print(f"  📋 {r[1]} ({r[2]})")
        if not self.al.load_task(tid): self.console.print(f"  ❌ Not found.",style="bold red"); return
        cp=self.conn.execute("SELECT conversation_snapshot,working_directory FROM checkpoints WHERE task_id=? ORDER BY id DESC LIMIT 1",(tid,)).fetchone()
        if cp:
            try:
                conv=json.loads(cp[0]); self.conn.execute("DELETE FROM conversation")
                for item in conv:
                    if isinstance(item,(list,tuple)) and len(item)>=2: self.save_msg(str(item[0]),str(item[1]))
                if cp[1] and Path(cp[1]).exists(): os.chdir(cp[1])
                self.console.print(f"  ✓ Restored step {self.al.current_step}",style="green")
            except Exception as e: self.console.print(f"  ⚠ Restore failed: {e}",style="yellow")
        self.al.display_plan()
        if sys.stdin.isatty() and questionary.confirm("  Resume?",default=True).ask(): self.al.status="active"; self.al.run_loop()
    def rollback(self):
        if not self.al.task_id: self.console.print("  ⚠ No task.",style="yellow"); return
        cp=self.conn.execute("SELECT id,step_index,git_commit_hash FROM checkpoints WHERE task_id=? ORDER BY id DESC LIMIT 1",(self.al.task_id,)).fetchone()
        if not cp: self.console.print("  ⚠ No checkpoint.",style="yellow"); return
        if cp[2] and Path(".git").exists():
            try: subprocess.run(["git","reset","--hard",cp[2]],capture_output=True,check=True); self.console.print(f"  ✓ Rolled back to {cp[2][:8]}",style="green")
            except Exception as e: self.console.print(f"  ❌ {e}",style="bold red")
        self.al.current_step=cp[1]; self.al._update()
    def _check_resume(self):
        if not sys.stdin.isatty(): return
        r=self.conn.execute("SELECT id,goal,status,updated_at FROM tasks WHERE status IN ('paused','active','stuck') ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not r: return
        self.console.print(f"\n  ⏸️ Resume '{r[1]}' ({r[2]}, last: {r[3]})?")
        if questionary.confirm("  Resume?",default=True).ask(): self.resume_task(r[0])

    # ── #30: Session snapshots ─────────────────────────────────────────────

    def cmd_save_session(self, name="default"):
        rows=self.conn.execute("SELECT role,content FROM conversation ORDER BY id ASC").fetchall()
        data={"timestamp":time.time(),"model":self.model,"conversation":rows}
        p=SESSIONS_DIR/f"{name}.json"
        p.write_text(json.dumps(data,ensure_ascii=False)); self.console.print(f"  ✓ Session saved: {name}",style="green")
    def cmd_load_session(self, name="default"):
        p=SESSIONS_DIR/f"{name}.json"
        if not p.exists(): self.console.print(f"  ⚠ No session: {name}",style="yellow"); return
        try:
            data=json.loads(p.read_text()); self.conn.execute("DELETE FROM conversation")
            for r,c in data.get("conversation",[]): self.save_msg(r,c)
            self._inv_tc(); self.console.print(f"  ✓ Session loaded: {name}",style="green")
        except Exception as e: self.console.print(f"  ❌ {e}",style="bold red")
    def cmd_list_sessions(self):
        if not SESSIONS_DIR.exists(): self.console.print("  No sessions.",style="dim"); return
        ss=list(SESSIONS_DIR.glob("*.json"))
        if not ss: self.console.print("  No sessions.",style="dim"); return
        t=Table(title="Sessions",border_style=BLUE,padding=(0,1)); t.add_column("Name",style="bold"); t.add_column("Saved")
        for s in ss:
            try: d=json.loads(s.read_text()); t.add_row(s.stem,datetime.fromtimestamp(d.get("timestamp",0)).strftime("%Y-%m-%d %H:%M"))
            except: t.add_row(s.stem,"?")
        self.console.print(t)

    # ── Slash commands ────────────────────────────────────────────────────

    def cmd_model(self):
        try: resp=ollama.list(); ml=self._parse_models(resp)
        except Exception as e: self.err.print(f"  {e}"); return
        if not ml: self.console.print("  No models.",style="yellow"); return
        ch=[]; nm={}
        for m in ml: n=m.get("model","?"); s=m.get("size",0)/(1024**3); l=f"{n} ({s:.1f}GB)"; ch.append(l); nm[l]=n
        ch.sort(); sel=questionary.select("  Model:",choices=ch).ask()
        if sel: self.model=nm.get(sel,sel.split("  ")[0]); self.sc("model",self.model); self.console.print(f"  ✓ {self.model}",style="green")
    def cmd_think(self):
        self.thinking=not self.thinking; s="on" if self.thinking else "off"; self.sc("thinking_mode",s)
        self.console.print(f"  ✓ Thinking: {s}",style="green")
    def cmd_clear(self): self.conn.execute("DELETE FROM conversation"); self.conn.commit(); self._inv_tc(); self.console.print("  ✓ Cleared.",style="green")
    def cmd_compact(self): self.compact(manual=True)
    def cmd_help(self):
        t=Table(title="⬡ Commands",header_style="bold blue",border_style="dim",padding=(0,2))
        t.add_column("Command",style="bold",width=16); t.add_column("Description")
        for c,d in [("/model","Switch model"),("/think","Toggle thinking"),("/clear","Clear context"),("/compact","Compact context"),
            ("/tools","List tools"),("/sandbox","Sandbox info"),("/undo","Undo last commit"),("/context","Context stats"),
            ("/plan <goal>","Plan task"),("/go","Execute plan"),("/task <goal>","Plan + execute"),
            ("/explore","Map codebase"),("/search <q>","Search code"),("/diff","Git diff"),("/blame <file>","Git blame"),
            ("/explain <file>","#49 Explain file"),("/todo","#24 Find TODOs"),("/resolve","#26 Merge conflicts"),
            ("/status","Task progress"),("/pause","Pause task"),("/resume","Resume task"),("/abort","Abort task"),
            ("/notes","Self-notes"),("/compact-notes","Summarise notes"),("/cost","Cost metrics"),
            ("/auto-approve","Toggle auto-approve"),("/health","Agent health"),("/edit-plan","Edit plan"),
            ("/rollback","Revert checkpoint"),("/checkpoint","Save checkpoint"),("/memory","Project memory"),
            ("/panel","Side panel"),("/export","Export conversation"),("/update","Update Pincer"),
            ("/save [name]","#30 Save session"),("/load [name]","#30 Load session"),("/sessions","#30 List sessions"),
            ("/architect","#29 Architect mode"),("/pair","#42 Pair programming"),("/dry-run","#36 Dry run mode"),
            ("/notify","Test notification"),("/voice","#46 Voice input"),("/resume-int","#6 Resume interrupted"),
            ("/issue <url>","#48 Issue→code"),("/web <url>","#28 Fetch web docs"),
            ("/exit","Quit"),("/help","This help")]: t.add_row(c,d)
        self.console.print(t)
    def cmd_tools(self):
        t=Table(title="Tools",header_style="bold blue",border_style="dim",padding=(0,2))
        t.add_column("Tool",style="bold",width=16); t.add_column("",width=2); t.add_column("Feature")
        for n,s,f in [("read_file","✅","#1 Syntax highlight"),("write_file","✅","#17 Multi-file"),("edit_file","✅","#19 Fuzzy match"),
            ("edit_file_diff","✅","#19 SEARCH/REPLACE"),("execute_command","✅","#25 Hardened sandbox"),("ask_user","✅","Interactive questions"),
            ("voice_input","✅","#46 Whisper"),("web_fetch","✅","#28 Docs fetch"),("explain","✅","#49 Explain code"),
            ("todo_hunter","✅","#24 Find TODOs"),("resolve_conflict","✅","#26 Merge conflicts"),
            ("blame","✅","#32 Git blame"),("search","✅","#12 Code search")]: t.add_row(n,s,f)
        self.console.print(t)
    def cmd_sandbox(self):
        cwd=os.getcwd(); st="Active (sandbox-exec)" if self.st.sandbox_available else "Unavailable"
        t=Table(title="Sandbox",show_header=False,border_style="dim",padding=(0,2))
        t.add_column("Key",style="bold"); t.add_column("Value")
        for k,v in [("Status",st),("CWD",cwd),("Exec","/usr/bin, /usr/local/bin, ~/.local/bin, /opt/homebrew"),("Blocked","sudo, mkfs"),("Network","Blocked (except approved)")]: t.add_row(k,v)
        self.console.print(t)
    def cmd_undo(self):
        if not Path(".git").exists(): self.console.print("  ⚠ No git.",style="yellow"); return
        try: subprocess.run(["git","reset","--hard","HEAD~1"],capture_output=True,text=True,check=True); self.console.print("  ✓ Undone.",style="green")
        except Exception as e: self.console.print(f"  ❌ {e}",style="bold red")
    def cmd_context(self):
        tt=self.total_tokens(); cnt=self.msg_count(); st=self.ct(self.sys_prompt()); db=tt-st; pct=tt/MAX_TOKENS*100
        t=Table(title="Context",show_header=False,border_style=BLUE,padding=(0,2))
        t.add_column("Key",style="bold"); t.add_column("Value")
        for k,v in [("Model",self.model),("Messages",str(cnt)),("System",f"{st:,}"),("Conversation",f"{db:,}"),("Total",f"{tt:,}"),("Capacity",f"{tt:,}/{MAX_TOKENS:,} ({pct:.0f}%)")]: t.add_row(k,v)
        # #39: Context window visualizer
        self.console.print(t)
        self.console.print(Bar(800, width=40, color=BLUE, bgcolor="dim"))  # visual bar
    # ── Phase 3+4 commands ─────────────────────────────────────────────────
    def cmd_plan(self,goal):
        if not goal: self.console.print("  /plan <goal>",style="yellow"); return
        self.al.create_task(goal); self.console.print(f"  📋 Planning: {goal}",style="bold blue")
        cl=self.al.ask_clarifying(goal); plan=self.al.generate_plan(goal,cl)
        if plan: self.al.display_plan(); PLAN_FILE.parent.mkdir(parents=True,exist_ok=True); PLAN_FILE.write_text(f"# {goal}\n\n"+"\n".join(f"- [ ] {s['description']}" for s in plan))
        else: self.console.print("  ❌ Plan failed.",style="bold red")
    def cmd_go(self):
        if not self.al.plan: self.console.print("  /plan first.",style="yellow"); return
        if self.al.status not in ("planning","paused"): self.console.print(f"  Status: {self.al.status}",style="yellow"); return
        self.al.display_plan()
        if sys.stdin.isatty() and not questionary.confirm("  Execute?",default=True).ask(): return
        self.al.run_loop()
    def cmd_task(self,goal):
        if not goal: self.console.print("  /task <goal>",style="yellow"); return
        self.cmd_plan(goal)
        if not self.al.plan: return
        if sys.stdin.isatty() and questionary.confirm("  Execute?",default=True).ask(): self.al.run_loop()
    def cmd_explore(self):
        self.console.print("  🔍 Exploring…",style=BLUE); cwd=os.getcwd()
        r=self.st.execute_command("find . -maxdepth 3 -type f -not -path '*/node_modules/*' -not -path '*/.git/*' -not -path '*/__pycache__/*' | head -80",cwd)
        if r.success: self.console.print(Panel(r.output[:3000],title="📁 Files",border_style=BLUE))
        syms=self.build_rmap()
        if syms:
            ls=[f"  {p}: {', '.join(d[:10])}" for p,d in syms.items()]
            self.console.print(Panel("\n".join(ls[:40]),title="🗺 Symbols",border_style="dim"))
        for f in ["README.md","package.json","requirements.txt","Cargo.toml","go.mod"]:
            if Path(f).exists():
                rr=self.ft.read_file(f)
                if rr.success: self._render_file(f,rr.output[:2000])
        self.write_note("observation",f"Explored {cwd}"); self.console.print("  ✓ Done.",style="green")
    def cmd_search(self,q):
        if not q: self.console.print("  /search <query>",style="yellow"); return
        cmd=f"rg --max-count=20 --no-heading --color=never '{q}' ." if shutil.which("rg") else f"grep -rn --max-count=20 '{q}' ."
        r=self.st.execute_command(cmd,os.getcwd())
        if r.success and r.output.strip(): self.console.print(Panel(r.output[:4000],title=f"🔍 {q}",border_style=BLUE))
        else: self.console.print(f"  No results for '{q}'.",style="dim")
    def cmd_diff(self):
        if not Path(".git").exists(): self.console.print("  ⚠ No git.",style="yellow"); return
        r=self.st.execute_command("git diff",os.getcwd())
        if r.success and r.output.strip():
            try: self.console.print(Panel(Syntax(r.output[:6000],"diff",theme="monokai",line_numbers=False),title="📊 Diff",border_style=BLUE))
            except: self.console.print(Panel(r.output[:4000],title="📊 Diff",border_style=BLUE))
        else: self.console.print("  No changes.",style="dim")
    def cmd_blame(self,path):
        if not path: self.console.print("  /blame <file>",style="yellow"); return
        if not Path(".git").exists(): self.console.print("  ⚠ No git.",style="yellow"); return
        r=self.st.execute_command(f"git blame {path}",os.getcwd())
        if r.success: self.console.print(Panel(r.output[:4000],title=f"📝 Blame: {path}",border_style=BLUE))
        else: self.console.print(f"  ❌ {r.error}",style="red")
    def cmd_explain(self,path):
        if not path: self.console.print("  /explain <file>",style="yellow"); return
        r=self.ft.read_file(path)
        if not r.success: self.console.print(f"  ❌ {r.error}",style="red"); return
        self.console.print(Panel(r.output[:2000],title=f"📄 {path}",border_style=BLUE))
        pr=f"Explain this code file in detail. Walk through each section.\n\n{r.output[:6000]}"
        try:
            with self.console.status("  [bold blue]Explaining…[/]"):
                resp=self._run_llm_sync([{"role":"user","content":pr}])
            self.console.print(Markdown(resp),style=""); self.write_note("observation",f"Explained {path}")
        except Exception as e: self.console.print(f"  ❌ {e}",style="bold red")
    def cmd_todo(self):
        r=self.st.execute_command("grep -rn 'TODO\\|FIXME\\|HACK\\|XXX' --include='*.py' --include='*.js' --include='*.ts' --include='*.rs' --include='*.go' . | head -30",os.getcwd())
        if r.success and r.output.strip(): self.console.print(Panel(r.output[:3000],title="📋 TODOs",border_style=BLUE))
        else: self.console.print("  No TODOs found.",style="dim")
    def cmd_resolve(self,path=""):
        if not Path(".git").exists(): self.console.print("  ⚠ No git.",style="yellow"); return
        cmd="git diff --name-only --diff-filter=U"
        if path: cmd=f"git diff --diff-filter=U {path}"
        r=self.st.execute_command(cmd,os.getcwd())
        if not r.success or not r.output.strip(): self.console.print("  No conflicts.",style="dim"); return
        files=r.output.strip().split("\n")
        self.console.print(f"  ⚔️ Conflicts: {len(files)}",style="yellow")
        for f in files:
            fr=self.ft.read_file(f.strip())
            if fr.success:
                self.console.print(f"  📄 {f.strip()}")
                pr=f"Resolve the merge conflicts in this file. Output the complete resolved file.\n\n{fr.output[:8000]}"
                try:
                    with self.console.status(f"  [bold blue]Resolving {f.strip()}…[/]"):
                        resp=self._run_llm_sync([{"role":"user","content":pr}])
                    self.console.print(Markdown(f"**Resolution for {f.strip()}:**\n```\n{resp[:2000]}\n```"))
                    if questionary.confirm(f"  Apply resolution to {f.strip()}?",default=False).ask():
                        c=re.sub(r"^```[\w]*\n","",resp); c=re.sub(r"\n```$","",c)
                        self.ft.write_file(f.strip(),c.strip()+"\n")
                        self._auto_commit(f"resolve: {f.strip()}")
                except Exception as e: self.console.print(f"  ❌ {e}",style="bold red")
    def cmd_web(self,url):
        if not url: self.console.print("  /web <url>",style="yellow"); return
        r=self.st.execute_command(f"curl -sL --max-time 15 '{url}'",os.getcwd(),net=True)
        if r.success:
            clean=re.sub(r'<[^>]+>',"",r.output[:15000])
            pr=f"Summarise the key points from this documentation page:\n\n{clean[:10000]}"
            try:
                with self.console.status("  [bold blue]Reading…[/]"): resp=self._run_llm_sync([{"role":"user","content":pr}])
                self.console.print(Markdown(resp))
            except Exception as e: self.console.print(f"  ❌ {e}",style="bold red")
        else: self.console.print(f"  ❌ Fetch failed.",style="red")
    def cmd_voice(self):
        try:
            import speech_recognition as sr
            r=sr.Recognizer()
            with sr.Microphone() as src: self.console.print("  🎤 Listening…",style=BLUE); audio=r.listen(src,timeout=10,phrase_time_limit=30)
            text=r.recognize_whisper(audio); self.console.print(f"  🗣️ {text}",style="blue"); return text
        except ImportError: self.console.print("  Install: pip install SpeechRecognition pyaudio",style="yellow"); return None
        except Exception as e: self.console.print(f"  ❌ {e}",style="red"); return None
    def cmd_issue(self,url):
        if not url: self.console.print("  /issue <url>",style="yellow"); return
        r=self.st.execute_command(f"curl -sL --max-time 15 '{url}'",os.getcwd(),net=True)
        if r.success:
            pr=f"This is a GitHub issue. Create a plan to solve it:\n\n{r.output[:8000]}"
            try:
                with self.console.status("  [bold blue]Reading issue…[/]"): resp=self._run_llm_sync([{"role":"user","content":pr}])
                self.console.print(Markdown(resp))
                if questionary.confirm("  Create task from this?",default=True).ask(): self.cmd_task(resp[:200])
            except Exception as e: self.console.print(f"  ❌ {e}",style="bold red")
        else: self.console.print("  ❌ Fetch failed.",style="red")
    def cmd_status(self):
        if not self.al.plan: self.console.print("  No task.",style="dim"); return
        el=int((time.time()-self.al.start_time)/60) if self.al.start_time else 0
        d=sum(1 for s in self.al.plan if s["status"]=="done")
        t=Table(title="Status",show_header=False,border_style=BLUE,padding=(0,2))
        t.add_column("Key",style="bold"); t.add_column("Value")
        for k,v in [("Status",self.al.status),("Progress",f"{d}/{len(self.al.plan)}"),("Step",str(self.al.current_step+1) if self.al.current_step<len(self.al.plan) else "done"),
            ("Elapsed",f"{el}m"),("Commands",str(self.al.shell_count)),("Writes",str(self.al.write_count)),
            ("LLM",str(self.al.llm_calls)),("Auto","🟢" if self.al.auto_approve else "🔴"),("Health",f"{self.al.health():.0%}")]: t.add_row(k,v)
        self.console.print(t); self.al.display_plan()
    def cmd_pause(self):
        if self.al.status!="active": self.console.print("  ⚠ No task.",style="yellow"); return
        self.al.pause()
    def cmd_resume(self): self.resume_task()
    def cmd_abort(self):
        if not self.al.task_id: self.console.print("  ⚠ No task.",style="yellow"); return
        if not sys.stdin.isatty() or questionary.confirm("  Abort?",default=False).ask(): self.al.abort()
    def cmd_notes(self):
        rows=self.conn.execute("SELECT category,content,timestamp FROM notes ORDER BY id DESC LIMIT 10").fetchall()
        if not rows: self.console.print("  No notes.",style="dim"); return
        ic={"success":"✅","error":"❌","observation":"👁","preference":"⚙️","pattern":"🔄"}
        t=Table(title="Notes",header_style="bold blue",border_style="dim",padding=(0,1))
        t.add_column("",width=2); t.add_column("Cat",width=12); t.add_column("Content"); t.add_column("Time",width=16)
        for r in rows: t.add_row(ic.get(r[0],"📝"),r[0],r[1][:120],str(r[2]) if r[2] else "")
        self.console.print(t)
    def cmd_compact_notes(self):
        rows=self.conn.execute("SELECT id,category,content FROM notes ORDER BY id ASC").fetchall()
        if len(rows)<5: self.console.print("  Need ≥ 5.",style="yellow"); return
        text="\n".join(f"{r[1]}: {r[2]}" for r in rows)
        try:
            with self.console.status("  [bold blue]Compacting…[/]"): sm=self._run_llm_sync([{"role":"user","content":f"Key facts:\n\n{text}"}])
        except Exception as e: self.console.print(f"  ❌ {e}",style="bold red"); return
        ids=[r[0] for r in rows]; ph=",".join("?" for _ in ids)
        self.conn.execute(f"DELETE FROM notes WHERE id IN ({ph})",ids)
        self.conn.execute("INSERT INTO notes (category,content) VALUES (?,?)",("pattern",sm[:2000])); self.conn.commit()
        self.console.print(f"  ✓ {len(rows)} → 1 summary",style="green")
    def cmd_cost(self):
        if not self.al.task_id: self.console.print("  No task.",style="dim"); return
        self.console.print(f"  💰 {self.al.cost()}",style=BLUE)
    def cmd_auto_approve(self):
        self.al.auto_approve=not self.al.auto_approve
        self.console.print(f"  ✓ Auto: {'🟢 ON' if self.al.auto_approve else '🔴 OFF'}",style="green")
    def cmd_health(self):
        h=self.al.health(); c="green" if h>.6 else ("yellow" if h>.3 else "red")
        t=Table(title="Health",show_header=False,border_style=c,padding=(0,2))
        t.add_column("Key",style="bold"); t.add_column("Value")
        t.add_row("Score",f"[{c}]{h:.0%}[/{c}]"); t.add_row("Failures",str(self.al.consec_fail))
        t.add_row("Shell",f"{self.al.shell_count}/{MAX_SHELL_PER_TASK}")
        t.add_row("Writes",f"{self.al.write_count}/{MAX_WRITES_PER_TASK}")
        t.add_row("rm",f"{self.al.rm_count}/{MAX_RM_PER_TASK}")
        self.console.print(t)
    def cmd_edit_plan(self):
        if not self.al.plan: self.console.print("  No plan.",style="yellow"); return
        ed=os.environ.get("EDITOR","nano")
        PLAN_FILE.parent.mkdir(parents=True,exist_ok=True)
        PLAN_FILE.write_text("\n".join(f"- [ ] {s['description']}" for s in self.al.plan))
        try:
            subprocess.run([ed,str(PLAN_FILE)])
            np=[]
            for line in PLAN_FILE.read_text().strip().split("\n"):
                m=re.match(r"-\s+\[[ x]\]\s+(.+)",line)
                if m: np.append({"step":len(np)+1,"description":m.group(1),"status":"pending"})
            if np: self.al.plan=np; self.al._update()
            self.console.print(f"  ✓ Updated ({len(np)} steps).",style="green")
        except Exception as e: self.console.print(f"  ❌ {e}",style="bold red")
    def cmd_checkpoint(self): self.save_cp()
    def cmd_rollback(self): self.rollback()
    def cmd_memory(self):
        t=Table(title="Memory",show_header=False,border_style=BLUE,padding=(0,2))
        t.add_column("Key",style="bold"); t.add_column("Value")
        t.add_row("File","✓" if self._memory else "none")
        if self._memory: t.add_row("Content",self._memory[:500])
        for mf in MEMORY_FILES: t.add_row(mf,"✓" if Path(mf).exists() else "—")
        self.console.print(t)
    def cmd_panel(self):
        cwd=os.getcwd(); panels=[]
        tr=self.st.execute_command("find . -maxdepth 2 -not -path '*/node_modules/*' -not -path '*/.git/*' | head -40",cwd)
        if tr.success: panels.append(Panel(tr.output[:1500],title="📁 Files",border_style="dim"))
        if self.al.plan:
            d=sum(1 for s in self.al.plan if s["status"]=="done")
            pt="\n".join(f"{'✅' if s['status']=='done' else '⬜'} {s['step']}. {s['description']}" for s in self.al.plan)
            panels.append(Panel(pt,title=f"📋 Plan ({d}/{len(self.al.plan)})",border_style=BLUE))
        rows=self.conn.execute("SELECT tool_name,command,status FROM tool_history ORDER BY id DESC LIMIT 5").fetchall()
        if rows: panels.append(Panel("\n".join(f"{'✓' if r[2]=='success' else '✗'} {r[0]}: {r[1][:50]}" for r in rows),title="📜 History",border_style="dim"))
        if not panels: self.console.print("  No data.",style="dim"); return
        self.console.print(Columns(panels,width=60))
    def cmd_export(self):
        rows=self.conn.execute("SELECT role,content,created_at FROM conversation ORDER BY id ASC").fetchall()
        if not rows: self.console.print("  Nothing.",style="yellow"); return
        out=f"# Pincer Export\n\n*{time.strftime('%Y-%m-%d %H:%M')}*\n\n---\n\n"
        for role,content,ts in rows:
            ic={"user":"👤","assistant":"🤖","system":"⚙️","summary":"📝"}.get(role,"•")
            out+=f"### {ic} {role.title()} _{ts}_\n\n{content}\n\n---\n\n"
        Path("pincer-export.md").write_text(out,encoding="utf-8")
        self.console.print(f"  ✓ Exported.",style="green")
    def cmd_update(self):
        self.console.print("  🔄 Updating…",style=BLUE); repo=PINCER_DIR/"repo"
        if not repo.exists(): self.console.print("  ⚠ No repo.",style="yellow"); return
        try:
            subprocess.run(["git","pull"],cwd=str(repo),check=True,capture_output=True)
            vp=str(PINCER_DIR/"venv"/"bin"/"pip")
            if Path(vp).exists(): subprocess.run([vp,"install","-r",str(repo/"requirements.txt"),"--quiet"],check=True)
            self.console.print("  ✓ Updated.",style="green")
        except Exception as e: self.console.print(f"  ❌ {e}",style="bold red")
    def cmd_architect(self):
        self.al.architect_mode=not self.al.architect_mode
        self.console.print(f"  ✓ Architect: {'🟢 ON' if self.al.architect_mode else '🔴 OFF'}",style="green")
    def cmd_pair(self):
        self.al.pair_mode=not self.al.pair_mode
        self.console.print(f"  ✓ Pair: {'🟢 ON (confirm each step)' if self.al.pair_mode else '🔴 OFF'}",style="green")
    def cmd_dryrun(self):
        self._dry_run=not self._dry_run
        self.console.print(f"  ✓ Dry-run: {'🟢 ON (no writes)' if self._dry_run else '🔴 OFF'}",style="green")
    def cmd_notify_test(self):
        self._notify("Test notification from Pincer 🔔"); self.console.print("  ✓ Sent.",style="green")
    def _exit(self): self.console.print("  👋 Goodbye.",style=BLUE); self.cleanup(); sys.exit(0)

    def handle_cmd(self,inp):
        ps=inp.strip().split(None,1); cmd=ps[0].lower(); arg=ps[1] if len(ps)>1 else ""
        wa={"/plan":self.cmd_plan,"/task":self.cmd_task,"/search":self.cmd_search,"/blame":self.cmd_blame,"/explain":self.cmd_explain,"/resolve":self.cmd_resolve,"/web":self.cmd_web,"/issue":self.cmd_issue,"/save":self.cmd_save_session,"/load":self.cmd_load_session}
        na={"/model":self.cmd_model,"/think":self.cmd_think,"/clear":self.cmd_clear,"/compact":self.cmd_compact,"/help":self.cmd_help,"/tools":self.cmd_tools,"/sandbox":self.cmd_sandbox,"/undo":self.cmd_undo,"/context":self.cmd_context,
            "/go":self.cmd_go,"/explore":self.cmd_explore,"/diff":self.cmd_diff,"/todo":self.cmd_todo,"/status":self.cmd_status,"/pause":self.cmd_pause,"/resume":self.cmd_resume,"/abort":self.cmd_abort,"/notes":self.cmd_notes,"/compact-notes":self.cmd_compact_notes,"/cost":self.cmd_cost,"/auto-approve":self.cmd_auto_approve,"/health":self.cmd_health,"/edit-plan":self.cmd_edit_plan,"/rollback":self.cmd_rollback,"/checkpoint":self.cmd_checkpoint,"/memory":self.cmd_memory,"/panel":self.cmd_panel,"/export":self.cmd_export,"/update":self.cmd_update,
            "/architect":self.cmd_architect,"/pair":self.cmd_pair,"/dry-run":self.cmd_dryrun,"/notify":self.cmd_notify_test,"/voice":self.cmd_voice,"/resume-int":self.cmd_resume_interrupt,"/sessions":self.cmd_list_sessions,"/exit":self._exit}
        if cmd in wa: wa[cmd](arg)
        elif cmd in na: na[cmd]()
        else: self.console.print(f"  Unknown: {cmd} /help",style="yellow")

    def cleanup(self):
        self.watcher.stop()
        if self._loop and not self._loop.is_closed():
            try: self._loop.close()
            except: pass
        if self.conn:
            try: self.conn.close()
            except: pass

    def _validate(self):
        if not self.check_ollama(): self.console.print("❌ Install: brew install ollama",style="bold red"); sys.exit(1)
        if not self.check_server(): self.console.print("⚠ Start: ollama serve",style="bold red"); sys.exit(1)
        if not self.check_model(self.model): self.console.print(f"  ⚠ Pulling {self.model}…",style="yellow"); self.pull_model(self.model)
    def _load_memory(self):
        for mf in MEMORY_FILES:
            p=Path(mf)
            if p.exists(): self._memory=p.read_text(encoding="utf-8",errors="replace")[:4000]; break

    # ── Main REPL ─────────────────────────────────────────────────────────

    def run(self):
        PINCER_DIR.mkdir(parents=True,exist_ok=True); self.setup_db()
        if self.gc("user_name") is None: self.wizard()
        else: self.load_config(); self.console.print(BANNER,style=""); mc=self.msg_count(); tt=self.total_tokens(); self.console.print(f"  {self.model} | {self.user_name} | {self.lang} | {mc} msgs ({tt/1000:.1f}K tok)",style="dim")
        self._validate(); self._load_memory(); self.watcher.start()  # #39
        self.session=PromptSession(history=FileHistory(str(HISTORY_PATH)),auto_suggest=AutoSuggestFromHistory(),completer=LazyFileCompleter())
        _o=signal.getsignal(signal.SIGINT)
        def _si(s,f):
            if self._gen: self._gen=False
            elif self.al.status=="active": self.al.pause()
            else: raise KeyboardInterrupt
        signal.signal(signal.SIGINT,_si)
        self._check_resume()
        while True:
            try:
                ps=f" {self.user_name} ❯ " if self.user_name else " ❯ "
                ui=self.session.prompt(HTML(f"<style fg='ansiblue'>{ps}</style>"),bottom_toolbar=self._sbar)
                s=ui.strip()
                if not s: continue
                # #39: Check file watcher
                changes=self.watcher.drain()
                if changes:
                    for fp,evt in changes[:5]:
                        self.console.print(f"  📝 File {evt}: {fp}",style="dim blue")
                        self.write_note("observation",f"File {evt}: {fp}")
                        self._rmap=None  # invalidate repomap cache
                if s.startswith("/"): self.handle_cmd(s); continue
                cls=self.router.classify(s)
                if cls.intent=="chat":
                    self.auto_compact(); self.save_msg("user",s); ms=self.get_msgs()
                    n=self.rel_notes(s)
                    if n: ms.insert(1,{"role":"system","content":n})
                    self.console.print(); resp=self.stream(ms); self.console.print()
                    if resp.strip(): self.save_msg("assistant",resp)
                else: self.handle_tool(s,cls.intent,cls.params)
            except KeyboardInterrupt:
                if not self._gen: self.console.print()
                continue
            except EOFError: self.console.print("\n  👋 Goodbye.",style=BLUE); self.cleanup(); sys.exit(0)
        signal.signal(signal.SIGINT,_o)

# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def build_parser():
    p=argparse.ArgumentParser(prog="pincer",description="⬡ Pincer — local AI assistant")
    p.add_argument("--model",metavar="M",help="Model"); p.add_argument("--think",action="store_true",help="Thinking mode")
    p.add_argument("--clear",action="store_true",help="Clear history"); p.add_argument("--reset",action="store_true",help="Delete data")
    p.add_argument("--dry-run",action="store_true",help="#36 No writes")  # #36
    p.add_argument("--architect",action="store_true",help="#29 Plan only")  # #29
    p.add_argument("--pair",action="store_true",help="#42 Confirm each step")  # #42
    return p

def main():
    parser=build_parser(); args=parser.parse_args()
    if args.reset:
        if PINCER_DIR.exists(): shutil.rmtree(PINCER_DIR)
        print("  ✓ Deleted."); return
    app=PincerApp()
    if args.model: app.model=args.model
    if args.think: app.thinking=True
    if args.dry_run: app._dry_run=True
    if args.architect: app.al.architect_mode=True
    if args.pair: app.al.pair_mode=True
    try: app.run()
    except KeyboardInterrupt: app.console.print("\n  👋 Goodbye.",style=BLUE); app.cleanup()
    except Exception as e: Console(stderr=True).print(f"  ❌ Fatal: {e}",style="bold red"); app.cleanup(); sys.exit(1)

if __name__=="__main__": main()
