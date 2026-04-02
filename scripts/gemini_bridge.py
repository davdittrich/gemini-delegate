#!/usr/bin/env python3
"""
Gemini Bridge Script — ACP Transport.

Communicates with Gemini CLI via the Agent Client Protocol (JSON-RPC 2.0 over stdio).
Replaces the previous stream-json transport with structured, typed communication.
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
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
# Session persistence
# ---------------------------------------------------------------------------
SESSIONS_DIR = Path.home() / ".cache" / "gemini-bridge"
SESSIONS_FILE = SESSIONS_DIR / "sessions.json"


def _load_sessions() -> Dict[str, str]:
    if not SESSIONS_FILE.exists():
        return {}
    try:
        return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_session(project_path: str, session_id: str) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SESSIONS_DIR, 0o700)
    sessions = _load_sessions()
    sessions[project_path] = session_id
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
    os.chmod(SESSIONS_FILE, 0o600)


def _get_persisted_session(project_path: str) -> Optional[str]:
    return _load_sessions().get(project_path)


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

    def __init__(self, cwd: str, approve_edits: bool = False):
        self._cwd = cwd
        self._approve_edits = approve_edits
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
        elif isinstance(update, AgentThoughtChunk):
            if isinstance(update.content, TextContentBlock):
                self._thoughts += update.content.text
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
async def _run_acp(args: argparse.Namespace) -> Dict[str, Any]:
    cd: Path = args.cd.resolve()
    project_path = cd.as_posix()

    # Determine prompt
    prompt_text: Optional[str] = None
    if args.prompt_file is not None:
        if not args.prompt_file.exists():
            return {"success": False, "error": f"Prompt file `{args.prompt_file}` does not exist."}
        prompt_text = args.prompt_file.read_text(encoding="utf-8")
    elif args.prompt:
        prompt_text = args.prompt
    if not prompt_text:
        return {"success": False, "error": "No prompt provided."}

    # Session resolution
    resume_id = ""
    if args.session_id:
        resume_id = args.session_id
    elif not args.new_session:
        persisted = _get_persisted_session(project_path)
        if persisted:
            resume_id = persisted

    # Build gemini flags
    extra_flags: List[str] = []
    if args.sandbox:
        extra_flags.append("--sandbox")
    if args.model:
        extra_flags.extend(["--model", args.model])

    client = BridgeClient(cwd=cd.as_posix(), approve_edits=args.approve_edits)
    result: Dict[str, Any] = {}
    session_id: Optional[str] = None

    try:
        async with spawn_agent_process(
            client,
            "gemini", "--acp", *extra_flags,
            env=os.environ.copy(),
        ) as (conn, proc):
            await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=CAPABILITIES,
            )

            # Session: resume or new
            if resume_id:
                try:
                    await conn.load_session(
                        cwd=cd.as_posix(),
                        session_id=resume_id,
                        mcp_servers=[],
                    )
                    session_id = resume_id
                except RequestError:
                    session = await conn.new_session(cwd=cd.as_posix(), mcp_servers=[])
                    session_id = session.session_id
                    _save_session(project_path, session_id)
            else:
                session = await conn.new_session(cwd=cd.as_posix(), mcp_servers=[])
                session_id = session.session_id
                _save_session(project_path, session_id)

            # Prompt with timeout
            timeout = args.timeout
            try:
                response = await asyncio.wait_for(
                    conn.prompt(
                        session_id=session_id,
                        prompt=[text_block(prompt_text)],
                    ),
                    timeout=timeout,
                )
                result["stop_reason"] = response.stop_reason
                result["success"] = True
            except asyncio.TimeoutError:
                try:
                    await conn.cancel(session_id=session_id)
                    await asyncio.sleep(5)
                except Exception:
                    pass
                if proc.returncode is None:
                    proc.kill()
                result["success"] = False
                result["stop_reason"] = "timeout"
                result["error"] = f"Gemini CLI timed out after {timeout:.0f} seconds."

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
    if client._thoughts:
        result["thoughts"] = client._thoughts
    if client._plan:
        result["plan"] = client._plan
    if args.return_all_messages:
        result["all_messages"] = [
            _serialize_update(u) for u in client._all_messages
        ]

    return result


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
    if hasattr(args, "output_file") and args.output_file:
        # Validate output-file path
        resolved = Path(args.output_file).resolve()
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

    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", "--PROMPT", dest="prompt",
                              help="Prompt text to send to Gemini.")
    prompt_group.add_argument("--prompt-file", type=Path, dest="prompt_file",
                              help="Read prompt from a file.")

    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument("--session-id", "--SESSION_ID", dest="session_id", default="",
                               help="Resume a specific session.")
    session_group.add_argument("--new-session", action="store_true",
                               help="Force a fresh session.")

    parser.add_argument("--cd", required=True, type=Path,
                        help="Workspace root directory.")
    parser.add_argument("--sandbox", action="store_true", default=False,
                        help="Run Gemini in sandbox mode.")
    parser.add_argument("--model", default="",
                        help="Override the Gemini model.")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="Max wall-clock seconds (default: 300).")
    parser.add_argument("--parse-json", action="store_true",
                        help="Extract JSON from agent_messages.")
    parser.add_argument("--output-file", type=Path,
                        help="Write result JSON to this file.")
    parser.add_argument("--return-all-messages", action="store_true",
                        help="Include raw ACP events in output JSON.")
    parser.add_argument("--approve-edits", action="store_true",
                        help="Allow Gemini to write files within --cd scope.")

    return parser.parse_args()


def main() -> None:
    if shutil.which("gemini") is None:
        print(json.dumps({"success": False, "error": "Gemini CLI not found in PATH."}))
        return

    args = _parse_args()

    if not args.cd.exists():
        print(json.dumps({"success": False, "error": f"Workspace `{args.cd.resolve()}` does not exist."}))
        return

    result = asyncio.run(_run_acp(args))
    _emit(result, args)


if __name__ == "__main__":
    main()
