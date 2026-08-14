"""Protocol for persistent Python sessions inside sandbox containers.

The coding agent's biggest observed inefficiency was chaining `python3 -c`
one-liners through run_command: every call is a fresh process, so expensive
work (loading images, parsing data) was redone from scratch each step. A
persistent interpreter fixes that -- variables survive between calls, like a
notebook.

This module holds the pure protocol pieces: the driver script that runs inside
the container, and the parsing of its output stream. The socket plumbing lives
in the sandbox manager.
"""

import json
from typing import Optional, Tuple

#: Prefix marking the driver's response line, so it cannot be confused with
#: output the executed code (or a subprocess it spawned) writes to the terminal.
#: NUL bytes rather than \x1e-style separators: those are Unicode line
#: boundaries, so str.splitlines() would cut the sentinel itself in half.
RESPONSE_SENTINEL = "\x00\x00KAYAK_REPL\x00\x00"

#: The interpreter loop that runs inside the container. Requests arrive as one
#: JSON line each on stdin; every request is answered with exactly one
#: sentinel-prefixed JSON line. All code shares one globals dict -- that is the
#: whole point. BaseException is caught so sys.exit() in user code cannot kill
#: the session.
PYTHON_REPL_DRIVER = r'''
import io, json, sys, traceback

SENTINEL = "\x00\x00KAYAK_REPL\x00\x00"
GLOBALS = {"__name__": "__main__"}

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        code = json.loads(line)["code"]
    except (ValueError, KeyError, TypeError):
        continue

    buffer = io.StringIO()
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buffer
    failed = False
    try:
        try:
            # Expression? Echo its value like a REPL would.
            result = eval(compile(code, "<session>", "eval"), GLOBALS)
            if result is not None:
                print(repr(result))
        except SyntaxError:
            exec(compile(code, "<session>", "exec"), GLOBALS)
    except BaseException:
        failed = True
        traceback.print_exc()
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr

    response = json.dumps({"output": buffer.getvalue(), "failed": failed})
    sys.stdout.write(SENTINEL + response + "\n")
    sys.stdout.flush()
'''


def demux_docker_frames(buffer: bytes) -> Tuple[bytes, bytes]:
    """Strips Docker's stream-multiplexing headers from raw exec output.

    A non-TTY exec stream is framed as an 8-byte header (stream id + big-endian
    payload size) followed by the payload. Frames can arrive split.

    Returns:
        Tuple[bytes, bytes]: (payload so far, unconsumed trailing bytes).
    """
    payload = b""
    while len(buffer) >= 8:
        size = int.from_bytes(buffer[4:8], "big")
        if len(buffer) < 8 + size:
            break
        payload += buffer[8 : 8 + size]
        buffer = buffer[8 + size :]
    return payload, buffer


def scan_for_response(buffer: str) -> Tuple[Optional[dict], str, str]:
    """Looks for the driver's response line in accumulated output.

    Anything before the response that is not the response itself is "leaked"
    output: text written straight to the terminal, bypassing the driver's
    capture -- typically a subprocess started by the executed code. It belongs
    to the user, so it is preserved rather than discarded.

    Returns:
        Tuple: (response dict or None, leaked output, remaining buffer).
    """
    leaked_parts = []
    while "\n" in buffer:
        line, buffer = buffer.split("\n", 1)
        if line.startswith(RESPONSE_SENTINEL):
            try:
                response = json.loads(line[len(RESPONSE_SENTINEL):])
            except ValueError:
                # A corrupted response line cannot be answered meaningfully;
                # treat it as leaked output and keep waiting.
                leaked_parts.append(line + "\n")
                continue
            return response, "".join(leaked_parts), buffer
        leaked_parts.append(line + "\n")
    return None, "".join(leaked_parts), buffer


def format_execution_result(leaked: str, response: dict) -> str:
    """Combines captured and leaked output into the tool result string.

    A failure is prefixed with `Error:` because that is how the engine marks a
    tool result as failed for both the model and the UI.
    """
    output = (leaked + response.get("output", "")).rstrip("\n")
    if response.get("failed"):
        return f"Error: the code raised an exception.\n{output}" if output else (
            "Error: the code raised an exception."
        )
    return output if output else "Executed with no output."
