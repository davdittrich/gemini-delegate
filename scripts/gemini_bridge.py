#!/usr/bin/env python3
"""
Gemini Bridge Script — ACP Transport.

Communicates with Gemini CLI via the Agent Client Protocol (JSON-RPC 2.0 over stdio).
Replaces the previous stream-json transport with structured, typed communication.
"""

import argparse
import asyncio
import copy
import datetime as _datetime
import enum
import hashlib
import json
import os
import re
import shutil
import subprocess as _subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from acp import spawn_agent_process, text_block, PROTOCOL_VERSION, RequestError
from acp.schema import (
    ClientCapabilities,
    FileSystemCapabilities,
    RequestPermissionResponse,
    AllowedOutcome,
    DeniedOutcome,
    ReadTextFileResponse,
    WriteTextFileResponse,
    AgentMessageChunk,
    AgentThoughtChunk,
    AgentPlanUpdate,
    ToolCallStart,
    ToolCallUpdate,
    TextContentBlock,
)

# ---------------------------------------------------------------------------
# Timeouts and Watchdog
# ---------------------------------------------------------------------------
class TimeoutType(enum.Enum):
    CONNECT = "Connect Timeout"
    INITIAL_IDLE = "Initial Idle Timeout"
    SUBSEQUENT_IDLE = "Subsequent Idle Timeout"
    TOTAL = "Total Timeout"


class BridgeTimeoutError(Exception):
    def __init__(self, timeout_type: TimeoutType, threshold: float, elapsed: float):
        self.timeout_type = timeout_type
        self.threshold = threshold
        self.elapsed = elapsed
        msg = f"{timeout_type.value}: {elapsed:.1f}s elapsed (Threshold: {threshold:.1f}s)"
        super().__init__(msg)


class HeartbeatWatchdog:
    """Monitors activity and raises BridgeTimeoutError if thresholds are exceeded."""

    def __init__(self, total_limit: float, initial_idle: float, subsequent_idle: float, verbose: bool = False):
        self.total_limit = total_limit
        self.initial_idle = initial_idle
        self.subsequent_idle = subsequent_idle
        self.verbose = verbose
        
        self.start_time = time.monotonic()
        self.last_activity = self.start_time
        self.has_had_activity = False
        self.last_log_time = 0.0

    def activity(self, label: str = "activity"):
        now = time.monotonic()
        self.last_activity = now
        self.has_had_activity = True
        
        if self.verbose and now - self.last_log_time > 0.5:
            elapsed = now - self.start_time
            print(f"[bridge] Heartbeat reset @ {elapsed:.1f}s: {label}", file=sys.stderr)
            self.last_log_time = now

    async def monitor(self):
        """Async loop that checks for timeout conditions."""
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            elapsed = now - self.start_time
            
            # 1. Check Total Limit
            if elapsed > self.total_limit:
                raise BridgeTimeoutError(TimeoutType.TOTAL, self.total_limit, elapsed)
            
            # 2. Check Idle Limits
            if not self.has_had_activity:
                if now - self.start_time > self.initial_idle:
                    raise BridgeTimeoutError(TimeoutType.INITIAL_IDLE, self.initial_idle, elapsed)
            else:
                idle_time = now - self.last_activity
                if idle_time > self.subsequent_idle:
                    raise BridgeTimeoutError(TimeoutType.SUBSEQUENT_IDLE, self.subsequent_idle, elapsed)
            
            if self.verbose and int(elapsed) % 30 == 0:
                print(f"[bridge] Progress: {elapsed:.0f}s elapsed...", file=sys.stderr)


# ---------------------------------------------------------------------------
# Session persistence (Shredded/Hashed approach)
# ---------------------------------------------------------------------------
DEFAULT_SESSIONS_DIR = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "gemini-bridge" / "sessions"


# ---------------------------------------------------------------------------
# Result Caching
# ---------------------------------------------------------------------------
DEFAULT_CACHE_DIR = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "gemini-bridge" / "result-cache"
DEFAULT_CACHE_TTL = 86400  # 24 hours


def _ensure_dir(path: Path, mode: int = 0o700) -> Path:
    """Create directory with correct permissions, handling umask race."""
    os.makedirs(path, mode=mode, exist_ok=True)
    os.chmod(path, mode)  # Override umask
    return path


def _cache_key(prompt: str, cwd: str, model: str) -> str:
    """Content-addressed key: hash of (git HEAD + dirty state + model + prompt).

    Auto-invalidates when:
    - The prompt changes (different task)
    - Any commit is made (git HEAD changes)
    - Any tracked file is modified without committing (dirty tree)
    - A different model is requested (Flash vs Pro produce different results)
    """
    try:
        head = _subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd, text=True, stderr=_subprocess.DEVNULL
        ).strip()
    except (_subprocess.CalledProcessError, FileNotFoundError):
        head = "no-git"
    # Include dirty-tree signal so uncommitted edits bust the cache
    try:
        dirty = _subprocess.check_output(
            ["git", "diff", "--stat"],
            cwd=cwd, text=True, stderr=_subprocess.DEVNULL
        ).strip()
    except (_subprocess.CalledProcessError, FileNotFoundError):
        dirty = ""
    dirty_hash = hashlib.sha256(dirty.encode("utf-8")).hexdigest()[:8] if dirty else "clean"
    composite = f"{head}\n{dirty_hash}\n{model}\n{prompt}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()[:32]


def _cache_lookup(cache_dir: Path, key: str, cache_ttl: int) -> Optional[Dict[str, Any]]:
    """Return cached result if it exists and is not expired.

    Args:
        cache_ttl: Max age in seconds. Passed as parameter to avoid global state.
    """
    cache_file = cache_dir / f"{key}.json"
    if not cache_file.exists():
        return None
    # Wall-clock age check (timezone-immune)
    file_age = time.time() - cache_file.stat().st_mtime
    if file_age > cache_ttl:
        cache_file.unlink(missing_ok=True)
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        data["_cache_hit"] = True
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _cache_store(cache_dir: Path, key: str, result: Dict[str, Any]) -> None:
    """Store result in cache. Atomic write with 0600 permissions.

    Non-fatal: logs to stderr and continues on failure (e.g., disk full).
    """
    try:
        _ensure_dir(cache_dir)
        cache_file = cache_dir / f"{key}.json"
        # Strip internal fields before caching
        to_cache = {k: v for k, v in result.items() if not k.startswith("_")}
        fd = tempfile.mkstemp(dir=cache_dir, prefix=".tmp-cache-", suffix=".json")
        temp_path = fd[1]
        try:
            with os.fdopen(fd[0], "w", encoding="utf-8") as f:
                json.dump(to_cache, f, indent=2)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, cache_file)
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
    except Exception as e:
        print(f"[bridge] Cache store failed (non-fatal): {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Token Estimation
# ---------------------------------------------------------------------------
# Rough heuristic: 1 token ~ 4 characters for English text.
# This is an estimate, not a billing-accurate count.

def _estimate_tokens(text: str) -> int:
    """Rough token count estimate. Not billing-accurate."""
    return max(1, len(text) // 4)


# Pricing per 1M tokens (USD) -- update when prices change.
# Source: https://ai.google.dev/pricing as of 2026-04.
_MODEL_PRICING = {
    "gemini-3-flash-preview": {"input": 0.15, "output": 0.60},
    "gemini-3.1-pro-preview":   {"input": 1.25, "output": 10.00},
    # Fallback for unknown models
    "default":          {"input": 1.25, "output": 10.00},
}


def _estimate_cost(input_tokens: int, output_tokens: int, model: str) -> dict:
    """Estimate USD cost based on token counts and model."""
    pricing = _MODEL_PRICING.get(model, _MODEL_PRICING["default"])
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": model or "default",
        "estimated_cost_usd": round(input_cost + output_cost, 6),
        "note": "Estimate only. Actual billing may differ.",
    }


# ---------------------------------------------------------------------------
# Feedback Logging
# ---------------------------------------------------------------------------


def _sanitize_log_field(value: str) -> str:
    """Strip newlines and carriage returns to prevent log injection."""
    return value.replace("\n", " ").replace("\r", " ").replace("|", "-")


def _write_feedback(cd: Path, feedback_str: str, model: str) -> None:
    """Append a feedback entry to .gemini-bridge/feedback.log.

    Format: VERDICT|TASK_TYPE|EST_TOKENS|NOTE
    All fields are sanitized to prevent newline injection.
    """
    parts = feedback_str.split("|", 3)
    if len(parts) != 4:
        print(json.dumps({
            "success": False,
            "error": "Format: VERDICT|TASK_TYPE|EST_TOKENS|NOTE  "
                     "(e.g., 'accepted|review|1.2k|clean review')"
        }))
        return

    verdict, task_type, est_tokens, note = [_sanitize_log_field(p.strip()) for p in parts]
    model = _sanitize_log_field(model)  # Sanitize caller-supplied model too
    log_dir = _ensure_dir(cd.resolve() / ".gemini-bridge")
    log_file = log_dir / "feedback.log"

    timestamp = _datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"{timestamp} | {model:<20} | {task_type:<12} | {verdict:<8} | {est_tokens:<6} | {note}\n"

    # Open with restricted permissions
    fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, entry.encode("utf-8"))
    finally:
        os.close(fd)

    print(json.dumps({"success": True, "logged": entry.strip()}))


# ---------------------------------------------------------------------------
# Parallel Execution (constant; full implementation added in Phase 3)
# ---------------------------------------------------------------------------
_MAX_PARALLEL_MODELS = 5


def _get_sessions_dir(args: argparse.Namespace) -> Path:
    """Resolve sessions directory with precedence: Flag > Env > Default."""
    env_dir = os.getenv("GEMINI_BRIDGE_SESSIONS_DIR")
    if args.sessions_dir:
        path = Path(args.sessions_dir)
    elif env_dir:
        path = Path(env_dir)
    else:
        path = DEFAULT_SESSIONS_DIR

    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    return path


def _get_session_path(sessions_dir: Path, project_path: str) -> Path:
    """Generate a debuggable hashed filename for a project."""
    resolved_path = Path(project_path).resolve().as_posix()
    path_hash = hashlib.sha256(resolved_path.encode("utf-8")).hexdigest()[:16]
    basename = Path(project_path).name or "root"
    # Ensure basename is safe for filenames
    safe_basename = re.sub(r"[^a-zA-Z0-9_\-]", "_", basename)
    return sessions_dir / f"{safe_basename}_{path_hash}.json"


def _load_session(session_path: Path, project_path: str) -> Optional[str]:
    """Load session ID from a hashed project file, with legacy fallback."""
    # 1. Check for shredded session file
    if session_path.exists():
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
            return data.get("session_id")
        except (json.JSONDecodeError, OSError):
            pass

    # 2. Legacy fallback (copy-on-demand)
    legacy_file = session_path.parent.parent / "sessions.json"
    if legacy_file.exists():
        try:
            legacy_data = json.loads(legacy_file.read_text(encoding="utf-8"))
            resolved_project = Path(project_path).resolve().as_posix()
            if resolved_project in legacy_data:
                session_id = legacy_data[resolved_project]
                # Migration: save to the new shredded format
                _save_session(session_path, project_path, session_id)
                return session_id
        except (json.JSONDecodeError, OSError):
            pass

    return None


def _save_session(session_path: Path, project_path: str, session_id: str) -> None:
    """Atomic save of session ID to a hashed project file."""
    session_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(session_path.parent, 0o700)

    data = {
        "project_path": Path(project_path).resolve().as_posix(),
        "session_id": session_id,
    }

    # Atomic write via tempfile
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=session_path.parent,
        delete=False,
        encoding="utf-8",
        prefix=".tmp-sess-"
    ) as tf:
        json.dump(data, tf, indent=2)
        temp_name = tf.name

    try:
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, session_path)
    except Exception:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


# ---------------------------------------------------------------------------
# JSON extraction from markdown-fenced responses
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL)


def extract_json(text: str) -> Tuple[Optional[Any], Optional[str]]:
    m = _FENCE_RE.search(text)
    candidate = m.group(1).strip() if m else text.strip()
    try:
        return json.loads(candidate), None
    except json.JSONDecodeError as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# ACP Client
# ---------------------------------------------------------------------------
class BridgeClient:
    """Implements the ACP Client protocol (12 methods)."""

    def __init__(self, cwd: str, approve_edits: bool = False, watchdog: Optional[HeartbeatWatchdog] = None):
        self._cwd = cwd
        self._approve_edits = approve_edits
        self._watchdog = watchdog
        self._conn = None
        self._agent_messages = ""
        self._thoughts = ""
        self._tool_calls: List[Dict[str, Any]] = []
        self._plan: List[Dict[str, Any]] = []
        self._all_messages: List[Any] = []

    def _check_path_containment(self, path: str) -> Path:
        resolved = Path(path).resolve()
        root = Path(self._cwd).resolve()
        if not resolved.is_relative_to(root):
            raise RequestError.invalid_params({
                "path": str(resolved),
                "reason": f"Outside workspace root {root}",
            })
        return resolved

    # --- Required methods (non-optional) ---

    async def read_text_file(self, path: str, session_id: str, limit: Optional[int] = None, line: Optional[int] = None, **kwargs) -> ReadTextFileResponse:
        resolved = self._check_path_containment(path)
        content = resolved.read_text(encoding="utf-8")
        return ReadTextFileResponse(content=content)

    async def write_text_file(self, content: str, path: str, session_id: str, **kwargs) -> WriteTextFileResponse:
        resolved = self._check_path_containment(path)
        if not self._approve_edits:
            raise RequestError.invalid_params({
                "path": str(resolved),
                "reason": "Write denied: --approve-edits not set",
            })
        resolved.write_text(content, encoding="utf-8")
        self._tool_calls.append({
            "id": f"write-{len(self._tool_calls)}",
            "title": f"Write {path}",
            "type": "write_file",
            "status": "completed",
            "path": path,
        })
        return WriteTextFileResponse()

    async def request_permission(self, options, session_id: str, tool_call=None, **kwargs) -> RequestPermissionResponse:
        # Determine if this is a write operation from tool_call context
        is_write = False
        if tool_call is not None:
            kind = getattr(tool_call, "kind", None)
            if kind in ("edit", "write"):
                is_write = True

        if is_write and not self._approve_edits:
            return RequestPermissionResponse(
                outcome=DeniedOutcome(outcome="cancelled"),
            )

        # Allow reads and approved writes
        first_option = options[0] if options else None
        if first_option is None:
            return RequestPermissionResponse(
                outcome=DeniedOutcome(outcome="cancelled"),
            )
        return RequestPermissionResponse(
            outcome=AllowedOutcome(
                option_id=first_option.option_id,
                outcome="selected",
            ),
        )

    # --- Streaming / notifications ---

    async def session_update(self, session_id: str, update, **kw) -> None:
        self._all_messages.append(update)

        if isinstance(update, AgentMessageChunk):
            if isinstance(update.content, TextContentBlock):
                self._agent_messages += update.content.text
                if self._watchdog: self._watchdog.activity("AgentMessageChunk")
        elif isinstance(update, AgentThoughtChunk):
            if isinstance(update.content, TextContentBlock):
                self._thoughts += update.content.text
                if self._watchdog: self._watchdog.activity("AgentThoughtChunk")
        elif isinstance(update, ToolCallStart):
            path = None
            if update.locations:
                path = update.locations[0].path
            self._tool_calls.append({
                "id": update.tool_call_id,
                "title": update.title or "",
                "type": self._classify_tool_call(update),
                "status": getattr(update, "status", "pending"),
                "path": path,
            })
            if self._watchdog: self._watchdog.activity("ToolCallStart")
        elif isinstance(update, ToolCallUpdate):
            for tc in self._tool_calls:
                if tc["id"] == update.tool_call_id:
                    if update.status:
                        tc["status"] = update.status
                    break
        elif isinstance(update, AgentPlanUpdate):
            self._plan = [
                {"content": e.content, "status": e.status}
                for e in (update.entries or [])
            ]

    def _classify_tool_call(self, tc: ToolCallStart) -> str:
        title = (tc.title or "").lower()
        if "read" in title and "file" in title:
            return "read_file"
        if "write" in title or "edit" in title:
            return "write_file"
        return "unknown"

    # --- Terminal methods (all rejected/no-op) ---

    async def create_terminal(self, command: str, session_id: str, args: Optional[List[str]] = None, cwd: Optional[str] = None, env=None, output_byte_limit: Optional[int] = None, **kwargs):
        raise RequestError(-32601, "Terminal execution not permitted by bridge policy")

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs):
        raise RequestError(-32601, "Terminal not available")

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs):
        return None

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs):
        raise RequestError(-32601, "Terminal not available")

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs):
        return None

    # --- Extension methods ---

    async def ext_method(self, method: str, params=None):
        raise RequestError(-32601, f"Unknown extension method: {method}")

    async def ext_notification(self, method: str, params=None) -> None:
        pass  # silently ignore

    # --- Connection lifecycle ---

    def on_connect(self, conn) -> None:
        self._conn = conn


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
CAPABILITIES = ClientCapabilities(
    fs=FileSystemCapabilities(read_text_file=True, write_text_file=True),
    terminal=False,
)


# ---------------------------------------------------------------------------
# ACP transport
# ---------------------------------------------------------------------------
async def _run_acp(args: argparse.Namespace, prompt_text: str) -> Dict[str, Any]:
    cd: Path = args.cd.resolve()
    project_path = cd.as_posix()
    model_name = args.model or "default"

    # --- Cache check (before any ACP work) ---
    cache_d: Optional[Path] = None
    cache_key_str: Optional[str] = None
    if args.cache:
        cache_ttl = args.cache_ttl
        if cache_ttl < 1 or cache_ttl > 2592000:
            cache_ttl = DEFAULT_CACHE_TTL
        cache_d = _ensure_dir(DEFAULT_CACHE_DIR)
        cache_key_str = _cache_key(prompt_text, project_path, model_name)
        cached = _cache_lookup(cache_d, cache_key_str, cache_ttl)
        if cached is not None:
            return cached

    # Session resolution
    sessions_dir = _get_sessions_dir(args)
    session_path = _get_session_path(sessions_dir, project_path)

    resume_id = ""
    if args.session_id:
        resume_id = args.session_id
    elif not args.new_session:
        persisted = _load_session(session_path, project_path)
        if persisted:
            resume_id = persisted

    # Build gemini flags
    extra_flags: List[str] = []
    if args.sandbox:
        extra_flags.append("--sandbox")
    if args.model:
        extra_flags.extend(["--model", args.model])

    watchdog = HeartbeatWatchdog(
        total_limit=args.timeout,
        initial_idle=args.first_chunk_timeout,
        subsequent_idle=args.idle_timeout,
        verbose=args.verbose
    )
    
    client = BridgeClient(cwd=cd.as_posix(), approve_edits=args.approve_edits, watchdog=watchdog)
    result: Dict[str, Any] = {}
    session_id: Optional[str] = None

    try:
        async with spawn_agent_process(
            client,
            "gemini", "--acp", *extra_flags,
            env=os.environ.copy(),
        ) as (conn, proc):
            # 1. Connect Phase (Isolated)
            try:
                await asyncio.wait_for(
                    conn.initialize(
                        protocol_version=PROTOCOL_VERSION,
                        client_capabilities=CAPABILITIES,
                    ),
                    timeout=60.0
                )
                
                # Session: resume or new
                if resume_id:
                    try:
                        await asyncio.wait_for(
                            conn.load_session(
                                cwd=cd.as_posix(),
                                session_id=resume_id,
                                mcp_servers=[],
                            ),
                            timeout=60.0
                        )
                        session_id = resume_id
                    except (RequestError, asyncio.TimeoutError):
                        session = await asyncio.wait_for(
                            conn.new_session(cwd=cd.as_posix(), mcp_servers=[]),
                            timeout=60.0
                        )
                        session_id = session.session_id
                        _save_session(session_path, project_path, session_id)
                else:
                    session = await asyncio.wait_for(
                        conn.new_session(cwd=cd.as_posix(), mcp_servers=[]),
                        timeout=60.0
                    )
                    session_id = session.session_id
                    _save_session(session_path, project_path, session_id)
            except asyncio.TimeoutError:
                raise BridgeTimeoutError(TimeoutType.CONNECT, 60.0, 60.0)

            # 2. Progress Race (Prompt Phase)
            watchdog_task = asyncio.create_task(watchdog.monitor())
            prompt_task = asyncio.create_task(conn.prompt(
                session_id=session_id,
                prompt=[text_block(prompt_text)],
            ))
            
            done, pending = await asyncio.wait(
                [prompt_task, watchdog_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Cleanup pending
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            
            if prompt_task in done:
                response = await prompt_task
                result["stop_reason"] = response.stop_reason
                result["success"] = True
            else:
                # Watchdog won the race
                await watchdog_task # Re-raises BridgeTimeoutError

    except BridgeTimeoutError as e:
        result["success"] = False
        result["stop_reason"] = "timeout"
        result["error"] = str(e)
        # Attempt cleanup (cancel -> kill -> wait)
        if session_id and 'conn' in locals():
            try:
                await asyncio.wait_for(conn.cancel(session_id=session_id), timeout=2.0)
            except Exception:
                pass
        if 'proc' in locals():
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
                
    except (BrokenPipeError, EOFError, ConnectionResetError, asyncio.IncompleteReadError) as e:
        result["success"] = False
        result["stop_reason"] = "crash"
        result["error"] = f"Gemini process exited unexpectedly: {e}"
    except RequestError as e:
        result["success"] = False
        result["stop_reason"] = "error"
        result["error"] = f"ACP protocol error: {e}"
    except Exception as e:
        result["success"] = False
        result["stop_reason"] = "error"
        result["error"] = f"Unexpected error: {e}"

    # Assemble output
    if session_id:
        result["SESSION_ID"] = session_id
    result["agent_messages"] = client._agent_messages
    result["tool_calls"] = client._tool_calls
    # Token estimation
    input_tokens = _estimate_tokens(prompt_text)
    output_tokens = _estimate_tokens(client._agent_messages)
    result["token_estimate"] = _estimate_cost(input_tokens, output_tokens, model_name)
    if client._thoughts:
        result["thoughts"] = client._thoughts
    if client._plan:
        result["plan"] = client._plan
    if args.return_all_messages:
        result["all_messages"] = [
            _serialize_update(u) for u in client._all_messages
        ]

    # --- Cache store (only on success, non-fatal on failure) ---
    if cache_d and cache_key_str and result.get("success"):
        _cache_store(cache_d, cache_key_str, result)

    return result


# ---------------------------------------------------------------------------
# Parallel Execution
# ---------------------------------------------------------------------------
async def _run_parallel(args: argparse.Namespace, prompt_text: str) -> Dict[str, Any]:
    """Run N parallel ACP sessions, potentially with different models.

    --parallel-models: comma-separated list of models.
    Example: --parallel-models gemini-3-flash-preview,gemini-3.1-pro-preview
    Each model gets its own ACP session with the same prompt.
    Temp session directories are cleaned up in a finally block.
    """
    models = [m.strip() for m in args.parallel_models.split(",")]
    if len(models) > _MAX_PARALLEL_MODELS:
        return {
            "success": False,
            "error": f"Max {_MAX_PARALLEL_MODELS} parallel models allowed, got {len(models)}.",
            "mode": "parallel",
            "results": [],
        }

    temp_dirs: List[Path] = []
    tasks: List[asyncio.Task] = []

    try:
        for i, model in enumerate(models):
            model_args = copy.copy(args)
            model_args.model = model
            model_args.new_session = True  # Force fresh sessions for isolation
            model_args.cache = False  # Disable cache in parallel to avoid racing writes
            # Isolated session dir per model
            model_dir = Path(tempfile.mkdtemp(prefix=f"gemini-parallel-{i}-"))
            temp_dirs.append(model_dir)
            model_args.sessions_dir = model_dir
            # create_task() schedules immediately and returns a cancellable Task
            tasks.append(asyncio.create_task(_run_acp(model_args, prompt_text)))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Assemble parallel output
        parallel_results = []
        for model, res in zip(models, results):
            if isinstance(res, BaseException):
                parallel_results.append({
                    "model": model,
                    "success": False,
                    "error": str(res),
                })
            elif isinstance(res, dict):
                res["model_used"] = model
                parallel_results.append(res)

        return {
            "success": all(r.get("success", False) for r in parallel_results),
            "mode": "parallel",
            "results": parallel_results,
        }
    finally:
        # Cancel any tasks that were created but never gathered (mid-loop exception)
        for t in tasks:
            if not t.done():
                t.cancel()
        # Guaranteed cleanup of all temp session directories
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)


def _serialize_update(update: Any) -> Any:
    """Convert ACP update objects to JSON-serializable dicts."""
    if hasattr(update, "model_dump"):
        return update.model_dump(mode="json", by_alias=True)
    return str(update)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def _emit(result: Dict[str, Any], args: argparse.Namespace) -> None:
    # Optional JSON extraction from agent_messages
    if args.parse_json and result.get("agent_messages"):
        parsed, err = extract_json(result["agent_messages"])
        if parsed is not None:
            result["parsed_json"] = parsed
        else:
            result["json_parse_error"] = err

    output = json.dumps(result, indent=2, ensure_ascii=False)
    print(output)
    
    output_file = getattr(args, "output_file", None)
    if output_file:
        is_auto = str(output_file).upper() == "AUTO"
        
        if is_auto:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                delete=False,
                encoding="utf-8",
                prefix="gemini_bridge_res_"
            ) as f:
                f.write(output)
                resolved = Path(f.name).resolve()
            
            print(f"[bridge] Result saved to {resolved} (0600 permissions). Manual cleanup recommended.", file=sys.stderr)
            result["AUTO_OUTPUT_FILE"] = str(resolved)
        else:
            # Validate output-file path
            resolved = Path(output_file).resolve()
            cwd_root = Path(args.cd).resolve()
            tmp_root = Path(tempfile.gettempdir()).resolve()
            
            if not (resolved.is_relative_to(cwd_root) or resolved.is_relative_to(tmp_root)):
                print(json.dumps({
                    "success": False,
                    "error": "--output-file must be within the workspace or system temp directory",
                }), file=sys.stderr)
                return
            resolved.write_text(output, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gemini Bridge (ACP)")

    prompt_group = parser.add_mutually_exclusive_group(required=False)
    prompt_group.add_argument("--prompt", "--PROMPT", dest="prompt",
                              help="Prompt text to send to Gemini.")
    prompt_group.add_argument("--prompt-file", type=Path, dest="prompt_file",
                              help="Read prompt from a file.")
    prompt_group.add_argument("--prompt-stdin", action="store_true",
                              help="Read prompt from stdin.")

    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument("--session-id", "--SESSION_ID", dest="session_id", default="",
                               help="Resume a specific session.")
    session_group.add_argument("--new-session", action="store_true",
                               help="Force a fresh session.")

    parser.add_argument("--cd", type=Path, default=Path("."),
                        help="Workspace root directory (default: current directory).")
    parser.add_argument("--sandbox", action="store_true", default=False,
                        help="Run Gemini in sandbox mode.")
    parser.add_argument("--model", default="",
                        help="Override the Gemini model.")
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="Total max wall-clock seconds (default: 600).")
    parser.add_argument("--idle-timeout", type=float, default=120.0,
                        help="Max seconds between message chunks (default: 120).")
    parser.add_argument("--first-chunk-timeout", type=float, default=300.0,
                        help="Max seconds for the first response chunk (default: 300).")
    parser.add_argument("--verbose", action="store_true",
                        help="Print heartbeat and progress markers to stderr.")
    parser.add_argument("--parse-json", action="store_true",
                        help="Extract JSON from agent_messages.")
    parser.add_argument("--output-file",
                        help="Write result JSON to this file (or 'AUTO' for unique temp file).")
    parser.add_argument("--sessions-dir", type=Path,
                        help="Override default session storage directory.")
    parser.add_argument("--return-all-messages", action="store_true",
                        help="Include raw ACP events in output JSON.")
    parser.add_argument("--approve-edits", action="store_true",
                        help="Allow Gemini to write files within --cd scope.")
    parser.add_argument("--cache", action="store_true", default=False,
                        help="Enable result caching (skip Gemini if cache hit). "
                             "Opt-in only. Omit for fresh results every time.")
    parser.add_argument("--cache-ttl", type=int, default=86400,
                        help="Cache time-to-live in seconds (default: 86400 = 24h). "
                             "Must be between 1 and 2592000 (30 days).")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Clear the result cache and exit.")
    parser.add_argument("--parallel-models",
                        help="Comma-separated models for parallel execution (e.g., 'gemini-3-flash-preview,gemini-3.1-pro-preview').")
    parser.add_argument("--log-feedback",
                        help="Append a feedback entry. Format: 'VERDICT|TASK_TYPE|EST_TOKENS|NOTE'. "
                             "Example: 'accepted|review|1.2k|clean review'. "
                             "Writes to .gemini-bridge/feedback.log in --cd directory.")

    return parser.parse_args()


def main() -> None:
    if shutil.which("gemini") is None:
        print(json.dumps({"success": False, "error": "Gemini CLI not found in PATH."}))
        return

    args = _parse_args()

    # --- Canonical argument validation (covers all early-exit flags) ---
    has_prompt = bool(args.prompt or args.prompt_file or args.prompt_stdin)
    has_early_exit = (
        args.clear_cache
        or args.log_feedback
    )
    if not has_prompt and not has_early_exit:
        print(json.dumps({
            "success": False,
            "error": "A prompt source (--prompt, --prompt-file, or --prompt-stdin) "
                     "is required unless using --clear-cache or --log-feedback.",
        }))
        return

    if args.clear_cache:
        cache_path = DEFAULT_CACHE_DIR
        if cache_path.exists():
            count = len(list(cache_path.glob("*.json")))
            shutil.rmtree(cache_path)
            print(json.dumps({"success": True, "cleared": count, "path": str(cache_path)}))
        else:
            print(json.dumps({"success": True, "cleared": 0, "path": str(cache_path)}))
        return

    if args.log_feedback:
        _write_feedback(args.cd, args.log_feedback, args.model or "unknown")
        return

    if not args.cd.exists():
        print(json.dumps({"success": False, "error": f"Workspace `{args.cd.resolve()}` does not exist."}))
        return

    # Pre-read prompt to avoid deadlocks
    prompt_text: Optional[str] = None
    if args.prompt_stdin:
        prompt_text = sys.stdin.read()
    elif args.prompt_file:
        prompt_text = args.prompt_file.read_text(encoding="utf-8")
    else:
        prompt_text = args.prompt

    if prompt_text is None:
        print(json.dumps({"success": False, "error": "No prompt text provided."}))
        return

    if args.parallel_models:
        result = asyncio.run(_run_parallel(args, prompt_text))
    else:
        result = asyncio.run(_run_acp(args, prompt_text))
    _emit(result, args)


if __name__ == "__main__":
    main()
