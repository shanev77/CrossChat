#!/usr/bin/env python3
import argparse, sys, time, re, datetime, os
from typing import List, Dict, Any

try:
    import requests
except ImportError:
    print("This script requires the 'requests' package. Install it with: pip install requests")
    sys.exit(1)

# ---- helpers ----
_PREFIX = re.compile(r"^\s*(thoughtful\s*question\s*:)\s*", re.I)
INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')  # Windows-invalid filename chars

def clean(text: str) -> str:
    return _PREFIX.sub("", text).strip()

def sanitize_filename(s: str) -> str:
    s = INVALID_CHARS.sub("_", s)
    s = s.strip().strip(".")
    return s

def fetch_models(base_url: str, timeout: int = 15) -> List[str]:
    url = base_url.rstrip("/") + "/api/tags"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    models = []
    for m in data.get("models", []):
        name = m.get("name") or m.get("model")
        if name:
            models.append(name)
    return sorted(models)

def choose_from_list(title: str, items: List[str]) -> str:
    print(f"\n{title}")
    print("-" * max(24, len(title)))
    if not items:
        print("No models found. Type a model name manually.")
        return input("Enter model name: ").strip()
    for i, item in enumerate(items, 1):
        print(f"{i:2d}) {item}")
    print(" M) Manual entry")
    while True:
        choice = input("Select a number (or 'M' to type a name): ").strip()
        if choice.lower() == "m":
            manual = input("Enter model name: ").strip()
            if manual:
                return manual
        elif choice.isdigit() and 1 <= int(choice) <= len(items):
            return items[int(choice) - 1]
        print("Invalid selection. Try again.")

def topic_prompt() -> str:
    default = "Discuss whether our universe could reside inside a black hole—pros, cons, and implications."
    t = input("\nTopic for the two bots to discuss\n(press Enter for default):\n> ").strip()
    return t if t else default

def turns_prompt(default_turns: int = 50) -> int:
    raw = input(f"\nHow many turns? (press Enter for {default_turns}):\n> ").strip()
    if not raw:
        return default_turns
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        print("Invalid number, using default.")
        return default_turns

def timestamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def default_logname(aihub_model: str, node01_model: str) -> str:
    ts = timestamp()
    safe_a = sanitize_filename(aihub_model.replace("/", "_"))
    safe_n = sanitize_filename(node01_model.replace("/", "_"))
    return f"crosschat_{safe_a}__{safe_n}_{ts}.txt"

def looks_like_dir_path(path: str) -> bool:
    return path.endswith(os.sep) or (os.name == "nt" and path.endswith(('/', '\\')))

def uniquify_log_path(path: str, aihub_model: str, node01_model: str) -> str:
    """
    Always return a unique logfile path for this run:
      - If 'path' empty -> auto name.
      - If 'path' is an existing directory OR ends with a separator -> create auto-named file inside it.
      - Otherwise treat as a filename and inject a timestamp before extension.
    """
    if not path:
        return default_logname(aihub_model, node01_model)
    if os.path.isdir(path) or looks_like_dir_path(path):
        return os.path.join(path.rstrip("/\\"), default_logname(aihub_model, node01_model))
    base, ext = os.path.splitext(path)
    return f"{base}_{timestamp()}{ext or '.txt'}"

def trim_history(history: List[Dict[str, str]], keep_pairs: int) -> List[Dict[str, str]]:
    """
    Keep the system message (index 0) and only the last N user/assistant pairs.
    Set keep_pairs to 0 to keep only the system message.
    """
    if keep_pairs is None:
        return history
    if keep_pairs <= 0:
        return history[:1]
    head = history[:1]
    tail = history[1:][-2 * keep_pairs:]
    return head + tail

def relay_with_wrap(from_name: str, last_message: str, remaining_turns: int) -> str:
    """
    Adds wrap-up cues in the final two turns:
      - remaining_turns == 2: start wrapping up, brief summary + one final short question.
      - remaining_turns == 1: final sign-off, thank you + goodbye, no new question.
    """
    cue = ""
    if remaining_turns == 2:
        cue = ("\n\n[Wrap-up cue: there are two messages left in total. "
               "Briefly summarise your view in 1–2 sentences and ask one short final question.]")
    elif remaining_turns == 1:
        cue = ("\n\n[Final-turn cue: this is the last message. "
               "Offer a quick thank-you and a clear goodbye. Do not ask another question.]")
    return f"From {from_name}: {last_message}{cue}"

# ---- chat core ----
def ollama_chat(base_url: str, model: str, messages: List[Dict[str, str]],
                temperature: float, timeout: int, retries: int, backoff: float,
                num_predict: int) -> str:
    url = base_url.rstrip('/') + "/api/chat"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict
        },
    }
    attempt = 0
    while True:
        attempt += 1
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            content = (data.get("message", {}) or {}).get("content", "").strip()
            reason = data.get("done_reason")
            if reason == "length":
                print("[INFO] Reply hit num_predict limit; consider raising --num-predict.")
            return content
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout) as e:
            if attempt <= retries:
                wait = backoff * attempt
                print(f"[WARN] Timeout calling {url} (attempt {attempt}/{retries}); retrying in {wait:.1f}s...")
                time.sleep(wait)
                continue
            raise SystemExit(f"[ERROR] Timeout talking to {url} after {retries} retries: {e}")
        except requests.exceptions.RequestException as e:
            raise SystemExit(f"[ERROR] HTTP error calling {url}: {e}")

def log_line(fp, who: str, model: str, text: str, turn: int):
    fp.write(f"Turn {turn} - {who} ({model})\n")
    fp.write("-" * 60 + "\n")
    fp.write(text.strip() + "\n\n")
    fp.flush()

# ---- main ----
def main():
    p = argparse.ArgumentParser(description="Cross-chat between Bob (AIHub) and Jane (NODE01) via Ollama HTTP API.")
    p.add_argument("--aihub-url", default="http://192.168.0.10:11434")
    p.add_argument("--node01-url", default="http://192.168.0.16:31135")
    p.add_argument("--turns", type=int, default=50)
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--delay", type=float, default=0.4)
    p.add_argument("--timeout", type=int, default=180, help="HTTP timeout per call (seconds)")
    p.add_argument("--retries", type=int, default=3, help="Retries on timeout/connect errors")
    p.add_argument("--retry-backoff", type=float, default=1.5, help="Backoff multiplier between retries (seconds)")
    p.add_argument("--num-predict", type=int, default=300, help="Max tokens to generate per reply")
    p.add_argument("--history-window", type=int, default=10, help="Keep only this many most-recent user/assistant pairs per side")
    p.add_argument("--logfile", default="", help="Transcript path or directory. A unique filename is always created.")
    args = p.parse_args()

    # 1) Let user pick models
    try:
        aihub_models = fetch_models(args.aihub_url)
    except Exception as e:
        print(f"[WARN] Could not list models on AIHub ({args.aihub_url}): {e}")
        aihub_models = []
    aihub_model = choose_from_list("AIHub models", aihub_models)

    try:
        node01_models = fetch_models(args.node01_url)
    except Exception as e:
        print(f"[WARN] Could not list models on NODE01 ({args.node01_url}): {e}")
        node01_models = []
    node01_model = choose_from_list("NODE01 models", node01_models)

    # 2) Topic + interactive turns (overrides --turns)
    topic = topic_prompt()
    args.turns = turns_prompt(default_turns=args.turns)

    # Keep seed simple—no model talk.
    seed = f"Start a friendly, curious conversation about: {topic}"

    # 3) Personas with explicit ban on model/AI talk
    system_aihub = (
        "You are Bob on AIHub. You're chatting with Jane on NODE01. "
        "Speak naturally and conversationally. Do NOT mention model names, training, providers, parameters, "
        "or that you are an AI/model/assistant. Avoid phrases like 'as a language model'. "
        "Reply clearly in <= 150 words and end with a single direct question if it helps the conversation flow."
    )
    system_node01 = (
        "You are Jane on NODE01. You're chatting with Bob on AIHub. "
        "Speak naturally and conversationally. Do NOT mention model names, training, providers, parameters, "
        "or that you are an AI/model/assistant. Avoid phrases like 'as a language model'. "
        "Reply clearly in <= 150 words and end with a single direct question if it helps the conversation flow."
    )

    history_aihub: List[Dict[str, str]] = [{"role": "system", "content": system_aihub}]
    history_node01: List[Dict[str, str]] = [{"role": "system", "content": system_node01}]
    last_message = seed
    speaker = "aihub"

    # 4) Transcript path (always unique per run)
    logfile = uniquify_log_path(args.logfile, aihub_model, node01_model)

    # Ensure the directory exists
    logdir = os.path.dirname(logfile)
    if logdir and not os.path.isdir(logdir):
        os.makedirs(logdir, exist_ok=True)

    print("\n=== Cross-chat starting ===")
    print(f"AIHub URL: {args.aihub_url}  model: {aihub_model}")
    print(f"NODE01 URL: {args.node01_url}  model: {node01_model}")
    print(f"Topic: {topic}")
    print(f"Transcript: {logfile}")
    print("-------------------------------------------\n")

    with open(logfile, "w", encoding="utf-8") as fp:
        fp.write("Cross-chat Transcript\n")
        fp.write("=" * 60 + "\n\n")
        fp.write(f"Started: {datetime.datetime.now().isoformat(timespec='seconds')}\n")
        fp.write(f"Bob (AIHub):  {args.aihub_url}  model={aihub_model}\n")
        fp.write(f"Jane (NODE01): {args.node01_url}  model={node01_model}\n")
        fp.write(f"Topic: {topic}\n\n")

        for turn in range(1, args.turns + 1):
            remaining = args.turns - turn + 1  # includes current reply

            if speaker == "aihub":
                history_aihub.append({
                    "role": "user",
                    "content": relay_with_wrap("Jane", last_message, remaining)
                })
                history_aihub = trim_history(history_aihub, args.history_window)
                reply = ollama_chat(args.aihub_url, aihub_model, history_aihub,
                                    args.temperature, args.timeout, args.retries, args.retry_backoff,
                                    args.num_predict)
                reply = clean(reply)
                history_aihub.append({"role": "assistant", "content": reply})
                print(f"[Bob / {aihub_model}]\n{reply}\n")
                log_line(fp, "Bob", aihub_model, reply, turn)
                last_message = reply
                speaker = "node01"
            else:
                history_node01.append({
                    "role": "user",
                    "content": relay_with_wrap("Bob", last_message, remaining)
                })
                history_node01 = trim_history(history_node01, args.history_window)
                reply = ollama_chat(args.node01_url, node01_model, history_node01,
                                    args.temperature, args.timeout, args.retries, args.retry_backoff,
                                    args.num_predict)
                reply = clean(reply)
                history_node01.append({"role": "assistant", "content": reply})
                print(f"[Jane / {node01_model}]\n{reply}\n")
                log_line(fp, "Jane", node01_model, reply, turn)
                last_message = reply
                speaker = "aihub"

            time.sleep(args.delay)

        fp.write("=== End of conversation ===\n")
        fp.write(f"Finished: {datetime.datetime.now().isoformat(timespec='seconds')}\n")

    print("=== Cross-chat complete ===\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
