#!/usr/bin/env python3
# CrosschatGUI.py
import sys, os, re, time, datetime, threading, queue, traceback, json
from typing import List, Dict, Any, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText

# ---- deps ----
try:
    import requests
except ImportError:
    messagebox.showerror("Missing dependency",
                         "This app requires the 'requests' package.\n\nInstall with:\n    pip install requests")
    sys.exit(1)

# =========================
# Core chat logic (from your CLI version)
# =========================

_PREFIX = re.compile(r"^\s*(thoughtful\s*question\s*:)\s*", re.I)
INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')  # Windows-invalid filename chars
MANUAL_SENTINEL = "Manual model…"

def clean(text: str) -> str:
    return _PREFIX.sub("", text).strip()

def sanitize_filename(s: str) -> str:
    s = INVALID_CHARS.sub("_", s).strip().strip(".")
    return s

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
    if not path:
        return default_logname(aihub_model, node01_model)
    if os.path.isdir(path) or looks_like_dir_path(path):
        return os.path.join(path.rstrip("/\\"), default_logname(aihub_model, node01_model))
    base, ext = os.path.splitext(path)
    return f"{base}_{timestamp()}{ext or '.txt'}"

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

def trim_history(history: List[Dict[str, str]], keep_pairs: int) -> List[Dict[str, str]]:
    if keep_pairs is None:
        return history
    if keep_pairs <= 0:
        return history[:1]
    head = history[:1]
    tail = history[1:][-2 * keep_pairs:]
    return head + tail

def relay_with_wrap(from_name: str, last_message: str, remaining_turns: int) -> str:
    cue = ""
    if remaining_turns == 2:
        cue = ("\n\n[Wrap-up cue: two messages left total. "
               "Do NOT ask any questions. Provide a concise summary only.]")
    elif remaining_turns == 1:
        cue = ("\n\n[Final-turn cue: last message. Do NOT ask questions. "
               "Thank them and say goodbye. No new topics.]")
    return f"From {from_name}: {last_message}{cue}"

def enforce_wrap_rules(text: str, remaining: int) -> str:
    # For last two turns, strip questions; for final turn, ensure a thanks/goodbye.
    if remaining <= 2:
        text = re.sub(r'\?+', '.', text).strip()
        if remaining == 1 and not re.search(r'\b(thanks|thank you|cheers|goodbye|see you)\b', text, re.I):
            text = (text.rstrip('. ') + ". Thanks for the chat. Goodbye.").strip()
    return text

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
            return content
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout) as e:
            if attempt <= retries:
                wait = backoff * attempt
                time.sleep(wait)
                continue
            raise RuntimeError(f"Timeout calling {url} after {retries} retries: {e}") from e
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"HTTP error calling {url}: {e}") from e

def log_line(fp, who: str, model: str, text: str, turn: int):
    fp.write(f"Turn {turn} - {who} ({model})\n")
    fp.write("-" * 60 + "\n")
    fp.write(text.strip() + "\n\n")
    fp.flush()

# ---- Pull (download) models with streamed progress ----
def pull_model_stream(base_url: str, model: str, timeout: int = 3600):
    """
    Generator that yields progress strings while pulling a model via Ollama.
    Uses /api/pull which streams NDJSON lines like {"status":"pulling","completed":...,"total":...}
    """
    url = base_url.rstrip("/") + "/api/pull"
    try:
        r = requests.post(url, json={"name": model}, stream=True, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        yield f"[ERROR] Pull request failed: {e}"
        return

    for raw in r.iter_lines(decode_unicode=True):
        if not raw:
            continue
        try:
            data = json.loads(raw)
            status = data.get("status", "")
            completed = data.get("completed")
            total = data.get("total")
            if completed is not None and total:
                pct = int((completed / total) * 100) if total else 0
                yield f"{status} {pct}% ({completed}/{total})"
            else:
                if status:
                    yield status
        except Exception:
            yield raw  # fallback: raw line

# =========================
# GUI
# =========================

APP_TITLE = "AI Cross-Chat (Bob ↔ Jane)"
DEFAULT_TOPIC = "Discuss whether our universe could reside inside a black hole—pros, cons, and implications."

class CrossChatGUI(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.pack(fill="both", expand=True)
        self.master.title(APP_TITLE)
        self.master.minsize(1020, 720)

        # state
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.ui_queue = queue.Queue()

        # --- styles
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Status.TLabel", foreground="#006400")
        style.configure("Warn.TLabel", foreground="#8B0000")
        style.configure("Accent.TButton")

        # --- build UI
        self._build_top_form()
        self._build_run_area()
        self._build_console()

        self._set_status("Ready.")

        # defaults
        self.aihub_url_var.set("http://192.168.0.10:11434")
        self.node01_url_var.set("http://192.168.0.16:31135")
        self.temperature_var.set("0.7")
        self.delay_var.set("0.9")
        self.timeout_var.set("400")
        self.retries_var.set("6")
        self.backoff_var.set("2.5")
        self.num_predict_var.set("200")
        self.history_window_var.set("8")
        self.turns_var.set("50")
        self.topic_text.insert("1.0", DEFAULT_TOPIC)

        # disable Start until ready
        self._update_start_state()

        # periodic UI updates from worker threads
        self.after(100, self._poll_ui_queue)

    # ---------- UI builders ----------
    def _build_top_form(self):
        frm = ttk.Frame(self)
        frm.pack(fill="x", padx=12, pady=(12, 6))

        ttk.Label(frm, text="Connections", style="Header.TLabel").grid(row=0, column=0, sticky="w", pady=(0,6))

        # URLs + fetch/pull buttons
        ttk.Label(frm, text="AIHub URL").grid(row=1, column=0, sticky="w")
        ttk.Label(frm, text="NODE01 URL").grid(row=2, column=0, sticky="w")

        self.aihub_url_var = tk.StringVar()
        self.node01_url_var = tk.StringVar()
        aihub_entry = ttk.Entry(frm, textvariable=self.aihub_url_var, width=46)
        node_entry = ttk.Entry(frm, textvariable=self.node01_url_var, width=46)
        aihub_entry.grid(row=1, column=1, sticky="we", padx=(6,6))
        node_entry.grid(row=2, column=1, sticky="we", padx=(6,6))

        self.fetch_aihub_btn = ttk.Button(frm, text="Fetch AIHub Models", command=self._fetch_aihub_models)
        self.fetch_node_btn  = ttk.Button(frm, text="Fetch NODE01 Models", command=self._fetch_node_models)
        self.fetch_aihub_btn.grid(row=1, column=2, padx=(6,0))
        self.fetch_node_btn.grid(row=2, column=2, padx=(6,0))

        # model pickers + pull buttons
        ttk.Label(frm, text="Bob Model (AIHub)").grid(row=1, column=3, padx=(18,0), sticky="w")
        ttk.Label(frm, text="Jane Model (NODE01)").grid(row=2, column=3, padx=(18,0), sticky="w")

        self.aihub_model_var = tk.StringVar()
        self.node01_model_var = tk.StringVar()
        self.aihub_model_cb = ttk.Combobox(frm, textvariable=self.aihub_model_var, width=32, state="readonly")
        self.node01_model_cb = ttk.Combobox(frm, textvariable=self.node01_model_var, width=32, state="readonly")
        self.aihub_model_cb.grid(row=1, column=4, sticky="we", padx=(6,0))
        self.node01_model_cb.grid(row=2, column=4, sticky="we", padx=(6,0))

        self.aihub_model_var.set("Select a model…")
        self.node01_model_var.set("Select a model…")

        self.aihub_model_cb.bind("<<ComboboxSelected>>", lambda e: self._handle_model_select("aihub"))
        self.node01_model_cb.bind("<<ComboboxSelected>>", lambda e: self._handle_model_select("node01"))

        # Pull (download) buttons
        self.pull_aihub_btn = ttk.Button(frm, text="Download Model", command=self._pull_aihub_model)
        self.pull_node_btn  = ttk.Button(frm, text="Download Model", command=self._pull_node_model)
        self.pull_aihub_btn.grid(row=1, column=5, padx=(6,0))
        self.pull_node_btn.grid(row=2, column=5, padx=(6,0))

        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(4, weight=1)

        # Topic
        ttk.Label(self, text="Topic", style="Header.TLabel").pack(anchor="w", padx=12, pady=(10,4))
        self.topic_text = ScrolledText(self, height=4, wrap="word")
        self.topic_text.pack(fill="x", padx=12)

        # parameters grid
        pfrm = ttk.Frame(self)
        pfrm.pack(fill="x", padx=12, pady=(10,6))

        def add_labeled(row, col, text, var, width=8):
            ttk.Label(pfrm, text=text).grid(row=row, column=col, sticky="w")
            e = ttk.Entry(pfrm, textvariable=var, width=width)
            e.grid(row=row, column=col+1, sticky="w", padx=(6,18))

        self.turns_var = tk.StringVar()
        self.temperature_var = tk.StringVar()
        self.delay_var = tk.StringVar()
        self.timeout_var = tk.StringVar()
        self.retries_var = tk.StringVar()
        self.backoff_var = tk.StringVar()
        self.num_predict_var = tk.StringVar()
        self.history_window_var = tk.StringVar()

        add_labeled(0,0,"Turns", self.turns_var)
        add_labeled(0,2,"Temperature", self.temperature_var)
        add_labeled(0,4,"Delay (s)", self.delay_var)
        add_labeled(0,6,"Timeout (s)", self.timeout_var)
        add_labeled(1,0,"Retries", self.retries_var)
        add_labeled(1,2,"Backoff", self.backoff_var)
        add_labeled(1,4,"Num Predict", self.num_predict_var)
        add_labeled(1,6,"History Window", self.history_window_var)

        # transcript folder (changed)
        tfrm = ttk.Frame(self)
        tfrm.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Label(tfrm, text="Transcript Folder").grid(row=0, column=0, sticky="w")
        self.log_path_var = tk.StringVar()
        ttk.Entry(tfrm, textvariable=self.log_path_var).grid(row=0, column=1, sticky="we", padx=(6,6))
        ttk.Button(tfrm, text="Choose folder…", command=self._choose_log_path).grid(row=0, column=2, sticky="w")
        tfrm.columnconfigure(1, weight=1)

    def _build_run_area(self):
        rfrm = ttk.Frame(self)
        rfrm.pack(fill="x", padx=12, pady=(10,6))
        self.start_btn = ttk.Button(rfrm, text="Start", command=self._start_chat, style="Accent.TButton")
        self.stop_btn  = ttk.Button(rfrm, text="Stop", command=self._stop_chat, state="disabled")
        self.start_btn.pack(side="left")
        self.stop_btn.pack(side="left", padx=(6,0))

        # live turns-left indicator
        self.turns_left_var = tk.StringVar(value="")
        ttk.Label(rfrm, textvariable=self.turns_left_var).pack(side="right", padx=(0,12))

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(rfrm, textvariable=self.status_var, style="Status.TLabel").pack(side="right")

    def _build_console(self):
        ttk.Label(self, text="Conversation", style="Header.TLabel").pack(anchor="w", padx=12, pady=(6,0))
        self.console = ScrolledText(self, wrap="word", height=20)
        self.console.pack(fill="both", expand=True, padx=12, pady=(4,12))
        self.console.configure(state="disabled")

    # ---------- helpers ----------
    def _append_console(self, text: str):
        self.console.configure(state="normal")
        self.console.insert("end", text + "\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    def _set_status(self, msg: str):
        self.status_var.set(msg)

    def _choose_log_path(self):
        # Folder only
        folder = filedialog.askdirectory(title="Choose transcript folder")
        if folder:
            self.log_path_var.set(folder)
        self._update_start_state()

    # ---------- start/stop ----------
    def _models_ready(self) -> bool:
        return (
            self.aihub_model_var.get().strip() not in ("", "Select a model…", MANUAL_SENTINEL) and
            self.node01_model_var.get().strip() not in ("", "Select a model…", MANUAL_SENTINEL)
        )

    def _update_start_state(self):
        can_start = all([
            bool(self.aihub_url_var.get().strip()),
            bool(self.node01_url_var.get().strip()),
            self._models_ready()
        ])
        self.start_btn.configure(state=("normal" if can_start else "disabled"))

    # ---------- model fetch/pull ----------
    def _fetch_aihub_models(self):
        self._fetch_models(self.aihub_url_var.get(), self.aihub_model_cb, "AIHub")

    def _fetch_node_models(self):
        self._fetch_models(self.node01_url_var.get(), self.node01_model_cb, "NODE01")

    def _fetch_models(self, url: str, target_cb: ttk.Combobox, label: str):
        def work():
            try:
                models = fetch_models(url, timeout=15)
                models = [MANUAL_SENTINEL] + models
                self.ui_queue.put(("models", (target_cb, models, label)))
            except Exception as e:
                self.ui_queue.put(("error", f"Failed to fetch {label} models: {e}"))
        threading.Thread(target=work, daemon=True).start()
        self._set_status(f"Fetching {label} models...")

    def _handle_model_select(self, side: str):
        if side == "aihub":
            if self.aihub_model_var.get() == MANUAL_SENTINEL:
                tag = simpledialog.askstring("Manual model", "Enter model tag for Bob (AIHub), e.g. granite3.1-moe:1b")
                if tag:
                    self.aihub_model_var.set(tag.strip())
        else:
            if self.node01_model_var.get() == MANUAL_SENTINEL:
                tag = simpledialog.askstring("Manual model", "Enter model tag for Jane (NODE01), e.g. llama3.2:1b")
                if tag:
                    self.node01_model_var.set(tag.strip())
        self._update_start_state()

    def _pull_aihub_model(self):
        self._pull_model_generic("AIHub", self.aihub_url_var.get(), self.aihub_model_var, self.aihub_model_cb)

    def _pull_node_model(self):
        self._pull_model_generic("NODE01", self.node01_url_var.get(), self.node01_model_var, self.node01_model_cb)

    def _pull_model_generic(self, label: str, base_url: str, model_var: tk.StringVar, cb: ttk.Combobox):
        model = simpledialog.askstring(f"Download to {label}",
                                       f"Enter model tag to download to {label} (e.g. llama3.2:1b):")
        if not model:
            return
        model = model.strip()
        self._append_console(f"Starting download on {label}: {model}")
        self._set_status(f"Downloading {model} to {label}…")

        def work():
            try:
                for msg in pull_model_stream(base_url, model):
                    self.ui_queue.put(("progress", f"[{label}] {msg}"))
                models = fetch_models(base_url, timeout=15)
                models = [MANUAL_SENTINEL] + models
                self.ui_queue.put(("models-select", (cb, models, label, model)))
                self.ui_queue.put(("info", f"[{label}] Download finished: {model}"))
            except Exception as e:
                self.ui_queue.put(("error", f"Download failed on {label}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    # ---------- run/stop ----------
    def _start_chat(self):
        try:
            aihub_url = self.aihub_url_var.get().strip()
            node01_url = self.node01_url_var.get().strip()
            aihub_model = self.aihub_model_var.get().strip()
            node01_model = self.node01_model_var.get().strip()
            topic = self.topic_text.get("1.0", "end").strip()
            turns = int(self.turns_var.get().strip() or "50")
            temperature = float(self.temperature_var.get().strip() or "0.7")
            delay = float(self.delay_var.get().strip() or "0.4")
            timeout = int(self.timeout_var.get().strip() or "180")
            retries = int(self.retries_var.get().strip() or "3")
            backoff = float(self.backoff_var.get().strip() or "1.5")
            num_predict = int(self.num_predict_var.get().strip() or "300")
            history_window = int(self.history_window_var.get().strip() or "10")
            log_dir = self.log_path_var.get().strip() or "."

            if not aihub_url or not node01_url:
                messagebox.showerror("Missing URLs", "Please provide both AIHub URL and NODE01 URL.")
                return
            if not aihub_model or not node01_model or aihub_model == MANUAL_SENTINEL or node01_model == MANUAL_SENTINEL:
                messagebox.showerror("Missing models", "Please select a model for Bob and Jane.")
                return
            if not topic:
                topic = DEFAULT_TOPIC

            os.makedirs(log_dir, exist_ok=True)
            logfile = os.path.join(log_dir, default_logname(aihub_model, node01_model))

            for w in (self.start_btn, self.fetch_aihub_btn, self.fetch_node_btn,
                      self.aihub_model_cb, self.node01_model_cb,
                      self.pull_aihub_btn, self.pull_node_btn):
                w.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self._set_status("Running…")
            self.turns_left_var.set(f"Turns left: {turns}")
            self._append_console(
                f"=== Cross-chat starting ===\n"
                f"Bob (AIHub):  {aihub_url}  model={aihub_model}\n"
                f"Jane (NODE01): {node01_url}  model={node01_model}\n"
                f"Topic: {topic}\nTranscript: {logfile}\n-------------------------------------------"
            )

            self.stop_event.clear()
            args = dict(
                aihub_url=aihub_url, node01_url=node01_url,
                aihub_model=aihub_model, node01_model=node01_model,
                topic=topic, turns=turns, temperature=temperature, delay=delay,
                timeout=timeout, retries=retries, backoff=backoff,
                num_predict=num_predict, history_window=history_window,
                logfile=logfile
            )
            self.worker_thread = threading.Thread(target=self._run_chat_worker, args=(args,), daemon=True)
            self.worker_thread.start()
        except Exception as e:
            messagebox.showerror("Invalid input", str(e))

    def _stop_chat(self):
        self.stop_event.set()
        self._set_status("Stopping…")

    def _run_chat_worker(self, cfg: Dict[str, Any]):
        try:
            seed = f"Start a friendly, curious conversation about: {cfg['topic']}"

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

            with open(cfg["logfile"], "w", encoding="utf-8") as fp:
                fp.write("Cross-chat Transcript\n")
                fp.write("=" * 60 + "\n\n")
                fp.write(f"Started: {datetime.datetime.now().isoformat(timespec='seconds')}\n")
                fp.write(f"Bob (AIHub):  {cfg['aihub_url']}  model={cfg['aihub_model']}\n")
                fp.write(f"Jane (NODE01): {cfg['node01_url']}  model={cfg['node01_model']}\n")
                fp.write(f"Topic: {cfg['topic']}\n\n")

                for turn in range(1, cfg["turns"] + 1):
                    remaining = cfg["turns"] - turn + 1
                    self.ui_queue.put(("turns_left", f"Turns left: {remaining}"))

                    if self.stop_event.is_set():
                        self.ui_queue.put(("info", "Stopped by user."))
                        break

                    if speaker == "aihub":
                        history_aihub.append({"role": "user",
                                              "content": relay_with_wrap("Jane", last_message, remaining)})
                        history_aihub = trim_history(history_aihub, cfg["history_window"])
                        reply = ollama_chat(cfg["aihub_url"], cfg["aihub_model"], history_aihub,
                                            cfg["temperature"], cfg["timeout"], cfg["retries"], cfg["backoff"],
                                            cfg["num_predict"])
                        reply = clean(reply)
                        reply = enforce_wrap_rules(reply, remaining)
                        history_aihub.append({"role": "assistant", "content": reply})
                        self.ui_queue.put(("say", ("Bob", cfg["aihub_model"], reply, turn)))
                        log_line(fp, "Bob", cfg["aihub_model"], reply, turn)
                        last_message = reply
                        speaker = "node01"
                    else:
                        history_node01.append({"role": "user",
                                               "content": relay_with_wrap("Bob", last_message, remaining)})
                        history_node01 = trim_history(history_node01, cfg["history_window"])
                        reply = ollama_chat(cfg["node01_url"], cfg["node01_model"], history_node01,
                                            cfg["temperature"], cfg["timeout"], cfg["retries"], cfg["backoff"],
                                            cfg["num_predict"])
                        reply = clean(reply)
                        reply = enforce_wrap_rules(reply, remaining)
                        history_node01.append({"role": "assistant", "content": reply})
                        self.ui_queue.put(("say", ("Jane", cfg["node01_model"], reply, turn)))
                        log_line(fp, "Jane", cfg["node01_model"], reply, turn)
                        last_message = reply
                        speaker = "aihub"

                    time.sleep(cfg["delay"])

                fp.write("=== End of conversation ===\n")
                fp.write(f"Finished: {datetime.datetime.now().isoformat(timespec='seconds')}\n")

            self.ui_queue.put(("turns_left", "Turns left: 0"))
            self.ui_queue.put(("done", "Conversation complete."))
        except Exception as e:
            err = "".join(traceback.format_exception(e))
            self.ui_queue.put(("error", err))

    # ---------- UI queue poll ----------
    def _poll_ui_queue(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "models":
                    cb, models, label = payload
                    cb.configure(state="readonly", values=models)
                    if len(models) > 1:
                        cb.set(models[1])
                    else:
                        cb.set(MANUAL_SENTINEL)
                    self._update_start_state()
                    self._set_status(f"{label} models loaded ({len(models)-1}). Selected: {cb.get()}")
                elif kind == "models-select":
                    cb, models, label, chosen = payload
                    cb.configure(state="readonly", values=models)
                    if chosen in models:
                        cb.set(chosen)
                    else:
                        models = [models[0], chosen] + [m for m in models[1:] if m != chosen]
                        cb.configure(values=models)
                        cb.set(chosen)
                    self._update_start_state()
                    self._set_status(f"{label} selected: {chosen}")
                elif kind == "progress":
                    self._append_console(payload)
                elif kind == "say":
                    who, model, text, turn = payload
                    self._append_console(f"\n[{who} / {model}]\n{text}\n")
                elif kind == "info":
                    self._append_console(payload)
                elif kind == "done":
                    self._append_console(payload)
                    self._finish_run()
                elif kind == "error":
                    self._append_console("ERROR:\n" + payload)
                    messagebox.showerror("Error", payload)
                    self._finish_run()
                elif kind == "turns_left":
                    self.turns_left_var.set(payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_ui_queue)

    def _finish_run(self):
        self.stop_event.clear()
        for w in (self.start_btn, self.fetch_aihub_btn, self.fetch_node_btn,
                  self.aihub_model_cb, self.node01_model_cb,
                  self.pull_aihub_btn, self.pull_node_btn):
            w.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self._set_status("Ready.")

# =========================
# main
# =========================

def main():
    root = tk.Tk()
    CrossChatGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
