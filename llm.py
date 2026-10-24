"""One function, `ask(prompt, seed)`, for three backends.

  claude-*   Anthropic API. Key from ANTHROPIC_API_KEY, else the file named by ANTHROPIC_KEY_FILE.
  gemini-*   Vertex AI through google-genai. Key from TALP_KEY (the five hand-in runs only).
  fake       No network: a canned answer, for checking the pipeline.

Answers are cached on disk (CACHE_DIR, default .cache/) by model, seed and prompt: a rerun with the same seed
and prompts is free and instant; a changed persona or message only asks again where its prompts changed.
Keys are never printed or written. If SPEND_LOG is set, every real call appends one tab-separated line
(time, agent, model, input tokens, output tokens, usd, tag) to it.
"""
import hashlib
import json
import os
import random
import threading
import time
from datetime import datetime

PRICE = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0),   # $ per 1M tokens in, out
         # TALP Vertex models: organiser credit, price not published; flash-lite / flash class assumed
         "gemini-3.5-flash-lite": (0.10, 0.40), "gemini-3.1-flash-lite": (0.10, 0.40), "gemini-3.5-flash": (0.30, 2.50)}
RETRIES = 4


def _claude_client():
    from anthropic import Anthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key and os.environ.get("ANTHROPIC_KEY_FILE"):
        with open(os.path.expanduser(os.environ["ANTHROPIC_KEY_FILE"])) as f:
            key = f.read().strip()
    if not key:
        raise SystemExit("no Claude key: set ANTHROPIC_API_KEY or ANTHROPIC_KEY_FILE")
    return Anthropic(api_key=key, max_retries=RETRIES)


def _gemini_client():   # SDK retries off: ask() retries, so 429s are counted
    from google import genai
    from google.genai import types
    return genai.Client(vertexai=True, api_key=os.environ["TALP_KEY"],
                        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)))


_clients = {}
STATS = {"calls": 0, "seconds": 0.0, "usd": 0.0, "errors": 0, "429": 0}   # real calls in this process
_lock = threading.Lock()


def _log(model, tin, tout, tag):
    path = os.environ.get("SPEND_LOG")
    pin, pout = PRICE.get(model.split("/")[-1], (0, 0))
    usd = (tin * pin + tout * pout) / 1e6
    with _lock:
        STATS["usd"] += usd
    if not path:
        return
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(path, "a") as f:
        f.write(f"{stamp}\t{os.environ.get('SPEND_AGENT', 'talp-aisle-env')}\t{model}\t{tin}\t{tout}\t{usd:.6f}\t{tag}\n")


def _fake(prompt, seed):
    r = random.Random(f"{seed}|{prompt}")
    if "Answer with JSON" in prompt:
        return "{" + ", ".join(f'"{q}": {r.randint(1, 5)}' for q in ("1a", "1b", "2a", "2b", "3a", "3b", "4a", "4b")) + "}"
    options = prompt.rsplit("Status: one of ", 1)[1].split("\n", 1)[0].split(" | ")
    return f"(fake) I carry on.\nOrders: {r.randint(0, 2)}\nReturned: 0\nComplained: no\nStatus: {r.choice(options)}"


def ask(prompt, seed, model, max_tokens=400, tag=""):
    """Return the model's text for one prompt (cached; `tag` only labels the spend log)."""
    if model == "fake":
        return _fake(prompt, seed)
    key = hashlib.sha256(json.dumps([model, seed, max_tokens, prompt]).encode()).hexdigest()
    path = os.path.join(os.environ.get("CACHE_DIR", os.path.join(os.path.dirname(__file__), ".cache")), key[:2], key)
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    t0 = time.time()
    text = _ask(prompt, seed, model, max_tokens, tag)
    with _lock:
        STATS["calls"] += 1
        STATS["seconds"] += time.time() - t0
    if text.strip():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            f.write(text)
        os.replace(path + ".tmp", path)
    return text


def _ask(prompt, seed, model, max_tokens, tag):
    for attempt in range(RETRIES + 1):
        try:
            if model.startswith("claude"):
                c = _clients.get("c") or _clients.setdefault("c", _claude_client())
                # Haiku/Sonnet 5.5 reject a non-default temperature; the seed only labels the run.
                r = c.messages.create(model=model, max_tokens=max_tokens, thinking={"type": "disabled"},
                                      messages=[{"role": "user", "content": prompt}])
                _log(f"anthropic/{model}", r.usage.input_tokens, r.usage.output_tokens, tag)
                return "".join(b.text for b in r.content if b.type == "text")
            from google.genai import types
            c = _clients.get("g") or _clients.setdefault("g", _gemini_client())
            r = c.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(
                temperature=1.0, seed=seed, max_output_tokens=max_tokens,
                thinking_config=types.ThinkingConfig(thinking_budget=0)))
            u = r.usage_metadata
            _log(f"google/{model}", u.prompt_token_count or 0, u.candidates_token_count or 0, tag)
            return r.text or ""
        except SystemExit:
            raise
        except Exception as e:                      # network / overload: wait and try again
            with _lock:
                STATS["errors"] += 1
                STATS["429"] += "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)
            if attempt == RETRIES:
                raise
            print(f"retry {attempt + 1} ({type(e).__name__})", flush=True)
            time.sleep(5 * (attempt + 1))
