#!/usr/bin/env python3
"""
Personal AI Agent with self-spawning assistants.

মূল এজেন্ট (orchestrator) কাজ দেখে সিদ্ধান্ত নেয়:
  - সহজ কাজ হলে নিজেই করে
  - জটিল বা আলাদা করা যায় এমন কাজ হলে `spawn_assistant` টুল দিয়ে
    নির্দিষ্ট ভূমিকার সহকারী (researcher, writer, coder, reviewer...) বানায়,
    কাজ বুঝিয়ে দেয়, ফলাফল নিয়ে চূড়ান্ত উত্তর তৈরি করে।

একসঙ্গে একাধিক সহকারী বানালে তারা সমান্তরালে (parallel) চলে।
সহকারীরা নিজে আর নতুন সহকারী বানাতে পারে না (অসীম লুপ ঠেকাতে)।
"""

import ast
import datetime
import json
import operator
import os
import subprocess
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic

try:  # .env ফাইল থাকলে পড়বে (ঐচ্ছিক)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ------------------------------------------------------------------ কনফিগ
MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-5-5")
MAX_STEPS = int(os.getenv("MAX_STEPS", "12"))            # প্রতিটি এজেন্টের সর্বোচ্চ ধাপ
MAX_ASSISTANTS = int(os.getenv("MAX_ASSISTANTS", "8"))   # প্রতিটি অনুরোধে সর্বোচ্চ সহকারী
MAX_TOKENS = 4000
WORKSPACE = Path(os.getenv("AGENT_WORKSPACE", "workspace")).resolve()
WORKSPACE.mkdir(exist_ok=True)
NEEDS_APPROVAL = {"write_file", "run_python"}            # এগুলো চালানোর আগে আপনার "y" লাগবে

client = anthropic.Anthropic()
IO_LOCK = threading.Lock()        # প্রিন্ট ও অনুমোদনের প্রশ্ন যেন গুলিয়ে না যায়
COUNT_LOCK = threading.Lock()
assistants_spawned = 0


def log(label: str, msg: str):
    with IO_LOCK:
        print(f"  [{label}] {msg}")


# ------------------------------------------------------------------ বেস টুল
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg,
}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


def calculator(expression: str) -> str:
    return str(_eval(ast.parse(expression, mode="eval").body))


def get_time() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def fetch_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "Error: only http/https URLs allowed"
    req = urllib.request.Request(url, headers={"User-Agent": "personal-agent/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read(300_000).decode("utf-8", errors="replace")
    return text[:10000]


def _safe_path(name: str) -> Path:
    p = (WORKSPACE / name).resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents:
        raise ValueError("path outside workspace")
    return p


def list_files() -> str:
    files = [str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob("*") if p.is_file()]
    return "\n".join(files) or "(workspace is empty)"


def read_file(path: str) -> str:
    return _safe_path(path).read_text(encoding="utf-8")[:10000]


def write_file(path: str, content: str) -> str:
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} chars to {path}"


def run_python(code: str) -> str:
    """workspace ফোল্ডারে পাইথন কোড চালায় (৩০ সেকেন্ড টাইমআউট)।"""
    try:
        r = subprocess.run(
            [sys.executable, "-c", code],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "Error: timed out after 30s"
    out = (r.stdout + ("\n[stderr]\n" + r.stderr if r.stderr else "")).strip()
    return out[:6000] or "(no output)"


BASE_FUNCS = {
    "calculator": calculator, "get_time": get_time, "fetch_url": fetch_url,
    "list_files": list_files, "read_file": read_file,
    "write_file": write_file, "run_python": run_python,
}


def _schema(props: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


BASE_TOOLS = [
    {"name": "calculator",
     "description": "Evaluate arithmetic like '12*(3+4)/5'. Supports + - * / ** %.",
     "input_schema": _schema({"expression": {"type": "string"}}, ["expression"])},
    {"name": "get_time", "description": "Current local date and time.",
     "input_schema": _schema({})},
    {"name": "fetch_url",
     "description": "Download a web page or API response as text (first ~10000 chars).",
     "input_schema": _schema({"url": {"type": "string"}}, ["url"])},
    {"name": "list_files", "description": "List files in the workspace folder.",
     "input_schema": _schema({})},
    {"name": "read_file", "description": "Read a text file from the workspace.",
     "input_schema": _schema({"path": {"type": "string"}}, ["path"])},
    {"name": "write_file",
     "description": "Create/overwrite a text file in the workspace. Requires user approval.",
     "input_schema": _schema({"path": {"type": "string"}, "content": {"type": "string"}},
                             ["path", "content"])},
    {"name": "run_python",
     "description": "Run Python code in the workspace folder and return stdout/stderr. "
                    "Requires user approval.",
     "input_schema": _schema({"code": {"type": "string"}}, ["code"])},
]

SPAWN_TOOL = {
    "name": "spawn_assistant",
    "description": (
        "Create a specialist sub-assistant to complete a self-contained sub-task and return "
        "its result. The assistant does NOT see this conversation, so 'task' must contain all "
        "needed context. Use for research, drafting, coding, review, or any sub-task that can be "
        "split off. Call this several times in ONE turn to run assistants in parallel."
    ),
    "input_schema": _schema({
        "role": {"type": "string",
                 "description": "Short role name, e.g. 'researcher', 'python coder', 'editor'."},
        "instructions": {"type": "string",
                         "description": "System-prompt style guidance: expertise, style, constraints."},
        "task": {"type": "string",
                 "description": "The complete, self-contained task and the expected output format."},
        "tools": {"type": "array",
                  "items": {"type": "string", "enum": list(BASE_FUNCS)},
                  "description": "Tools this assistant may use (give the minimum needed)."},
    }, ["role", "instructions", "task"]),
}


# ------------------------------------------------------------------ টুল চালানো
def approve(label: str, name: str, args: dict) -> bool:
    with IO_LOCK:
        print(f"\n[অনুমোদন দরকার | {label}] {name}")
        for k, v in args.items():
            print(f"   {k}: {str(v)[:500]}")
        return input("চালাবো? (y/n): ").strip().lower() == "y"


def run_tool(name: str, args: dict, label: str, allowed: set) -> tuple[str, bool]:
    if name not in allowed:
        return f"Tool not available to you: {name}", True
    if name == "spawn_assistant":
        return spawn_assistant(**args)
    if name in NEEDS_APPROVAL and not approve(label, name, args):
        return "User denied this action.", True
    try:
        return str(BASE_FUNCS[name](**args)), False
    except Exception as e:
        return f"Error: {e}", True


# ------------------------------------------------------------------ কোর লুপ (মূল এজেন্ট ও সহকারী দুজনেই ব্যবহার করে)
def agent_loop(system: str, messages: list, tools: list, label: str) -> str:
    allowed = {t["name"] for t in tools}
    for step in range(1, MAX_STEPS + 1):
        resp = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=system,
            tools=tools, messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")

        calls = [b for b in resp.content if b.type == "tool_use"]
        for c in calls:
            log(label, f"ধাপ {step}: {c.name} {json.dumps(c.input, ensure_ascii=False)[:110]}")

        def execute(c):
            out, err = run_tool(c.name, c.input, label, allowed)
            return {"type": "tool_result", "tool_use_id": c.id, "content": out, "is_error": err}

        if len(calls) > 1:  # একাধিক টুল (বিশেষত একাধিক সহকারী) সমান্তরালে
            with ThreadPoolExecutor(max_workers=len(calls)) as pool:
                results = list(pool.map(execute, calls))
        else:
            results = [execute(calls[0])]
        messages.append({"role": "user", "content": results})

    return f"(থামানো হয়েছে: {MAX_STEPS} ধাপে কাজ শেষ হয়নি)"


# ------------------------------------------------------------------ সহকারী তৈরি
def spawn_assistant(role: str, instructions: str, task: str, tools: list | None = None):
    global assistants_spawned
    with COUNT_LOCK:
        if assistants_spawned >= MAX_ASSISTANTS:
            return f"Error: assistant limit reached ({MAX_ASSISTANTS}). Finish the work yourself.", True
        assistants_spawned += 1
        n = assistants_spawned

    label = f"সহকারী#{n}:{role[:18]}"
    log(label, "তৈরি হলো")
    chosen = [t for t in BASE_TOOLS if t["name"] in (tools or [])]   # spawn_assistant কখনো দেওয়া হয় না
    system = (
        f"You are a specialist assistant with the role: {role}.\n{instructions}\n\n"
        "You were created by a coordinator agent to complete exactly one task. "
        "Work independently, use tools only if needed, and finish with a clear, complete "
        "result in your final message (that message is returned to the coordinator)."
    )
    try:
        result = agent_loop(system, [{"role": "user", "content": task}], chosen, label)
    except Exception as e:
        return f"Assistant failed: {e}", True
    log(label, "কাজ শেষ")
    return result, False


# ------------------------------------------------------------------ মূল এজেন্ট
ORCHESTRATOR_PROMPT = f"""You are a capable personal assistant agent (the coordinator).

How to work:
- Simple or quick tasks: do them yourself, directly.
- Complex tasks, or ones with independent parts (research several things, draft + review, \
write code + test it): create specialist assistants with spawn_assistant. Make each task \
self-contained (they can't see this chat), give the minimum tools, and spawn independent \
assistants in the same turn so they run in parallel.
- Check the assistants' results for quality and consistency, then write ONE final answer for the user. \
Don't just paste raw outputs.
- You may create at most {MAX_ASSISTANTS} assistants per request; don't create more than needed.
- Files live only in the workspace folder. Never claim to have done something you didn't.
- Reply in the same language the user writes in."""


def main():
    global assistants_spawned
    print(f"Personal AI Agent চালু | মডেল: {MODEL} | workspace: {WORKSPACE}")
    print("কাজ লিখুন। বের হতে 'exit' লিখুন।")
    tools = BASE_TOOLS + [SPAWN_TOOL]
    messages = []   # কথোপকথনের মেমোরি
    while True:
        try:
            user = input("\nআপনি: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user.lower() in {"exit", "quit"}:
            break
        if not user:
            continue
        assistants_spawned = 0   # প্রতিটি নতুন অনুরোধে গণনা শূন্য
        messages.append({"role": "user", "content": user})
        try:
            answer = agent_loop(ORCHESTRATOR_PROMPT, messages, tools, "মূল")
        except anthropic.APIError as e:
            print(f"\nAPI error: {e}")
            messages.pop()
            continue
        print(f"\nAgent: {answer}")


if __name__ == "__main__":
    main()
