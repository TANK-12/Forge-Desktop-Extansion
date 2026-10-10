"""Jan for Forge apps: each app picks its own models, loads them when it needs
them, and gives the memory back when it doesn't.

the owner asked for this on 2026-09-16 — "make each app have its own setting for
the jan models and start them up when neede and close donw when not in use" —
and said why: "cause ram saving". Two idle models held about 9.8 GB on a laptop
with no swap.

How it works, and the rules it encodes:

* **Jan is shared and is never restarted.** It runs as a llama.cpp router
  (`--models-preset`) that loads a model on the first request naming it and can
  be asked to unload one; the next request loads it again (measured at 1.9 s
  for the 2B). So "start it up when needed" means: name the model in a request.
  Nothing here starts, stops or configures Jan itself.
* **A model is released only when nothing at all is using it.** Idleness is
  measured from the model process's own CPU time, which covers other apps and
  other Claude sessions, not just this one. An app with a request in flight
  never releases anything.
* **Each app has its own settings**, in ~/.config/forge/jan/<app>.json: which
  model it wants for each of its roles, and how long a model may sit idle
  before it is released. Empty model means "whatever Jan has", which is what
  every app did before this existed.
* **Stdlib only.** Every app has its own virtualenv; some have httpx, some
  requests, some neither. urllib works everywhere.

Mounting it on a FastAPI app gives that app /api/jan/settings and
/api/jan/models, and releases the models it loaded when its backend stops.
`shared/forge_jan.js` draws the settings; an app needs no Jan settings code of
its own.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("JAN_BASE_URL", "http://127.0.0.1:1337/v1")
SERVER = "llama-server"
DEFAULT_IDLE_MINUTES = 5.0
CHECK_EVERY = 30.0
MODELS_TTL = 60.0

# What every app's settings file holds. `models` maps the app's own role names
# ("chatter", "narrator", "chat") to a Jan model id; "" means let the app pick.
DEFAULTS = {"models": {}, "idle_minutes": DEFAULT_IDLE_MINUTES}


JAN_MODELS = Path(
    os.environ.get("JAN_MODELS_DIR")
    or Path.home() / ".var/app/ai.jan.Jan/data/Jan/data/llamacpp/models"
)
# the owner (2026-09-27): "tons of context for my AIs except for the 0.8", while
# his own Jan keeps his (jan-tune's) settings. So Forge apps get copies.
LONG_CONTEXT = 131072
LONG_SUFFIX = "-long"


def long_entries(
    bases: list[str], models_dir: Path | None = None, ctx: int = LONG_CONTEXT
) -> list[str]:
    """Forge's long-context copies of Jan models: a folder `<base>-long` whose
    model.yml is the base's with ctx_size raised and its own name, pointing at
    the same files (no disk used). Rebuilt from the base on every call, so
    tuning applied to the base (jan-tune) carries over. Jan lists a new copy
    after its next start. Returns the ids written."""
    root = Path(models_dir or os.environ.get("JAN_MODELS_DIR") or JAN_MODELS)
    written = []
    for base in bases:
        source = root / base / "model.yml"
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        out = [
            line
            for line in lines
            if not line.startswith(("ctx_size:", "name:", "size_bytes:"))
        ]
        label = next(
            (l.split(":", 1)[1].strip() for l in lines if l.startswith("name:")), base
        )
        out += [f"ctx_size: {ctx}", f"name: {label} (long context, Forge apps)"]
        target = root / f"{base}{LONG_SUFFIX}" / "model.yml"
        text = "\n".join(out) + "\n"
        try:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                target.parent.mkdir(exist_ok=True)
                target.write_text(text, encoding="utf-8")
        except OSError:
            continue
        written.append(target.parent.name)
    return written


def settings_dir() -> Path:
    return Path(
        os.environ.get("FORGE_JAN_SETTINGS_DIR")
        or Path.home() / ".config" / "forge" / "jan"
    )


def settings_path(app: str) -> Path:
    return settings_dir() / f"{app}.json"


def read_settings(app: str, roles: tuple[str, ...] = ()) -> dict:
    """This app's settings, with every role it declares present."""
    stored = {}
    try:
        stored = json.loads(settings_path(app).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    models = {role: "" for role in roles}
    saved = stored.get("models")
    if isinstance(saved, dict):
        for role, model in saved.items():
            models[str(role)] = str(model or "")
    try:
        idle = float(stored.get("idle_minutes", DEFAULT_IDLE_MINUTES))
    except (TypeError, ValueError):
        idle = DEFAULT_IDLE_MINUTES
    return {"models": models, "idle_minutes": max(0.0, idle)}


def write_settings(app: str, updates: dict, roles: tuple[str, ...] = ()) -> dict:
    """Merge `updates` into this app's settings and save them."""
    current = read_settings(app, roles)
    incoming = updates.get("models")
    if isinstance(incoming, dict):
        for role, model in incoming.items():
            current["models"][str(role)] = str(model or "")
    if "idle_minutes" in updates:
        try:
            current["idle_minutes"] = max(0.0, float(updates["idle_minutes"]))
        except (TypeError, ValueError):
            pass
    path = settings_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return current


# --- talking to Jan ---------------------------------------------------------------


class JanError(Exception):
    """Jan could not be reached, or answered with something unusable."""


# One lane per process. Two concurrent Jan requests once froze this laptop, and
# a lock here means an app cannot do it by accident from two threads.
LANE = threading.Lock()
LANE_WAIT = 30.0


def chat(
    model: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.8,
    max_tokens: int = 200,
    timeout: float = 120.0,
    seed: int | None = None,
    thinking: bool = False,
    base_url: str = BASE_URL,
    wait: float = LANE_WAIT,
    extra: dict | None = None,
) -> str:
    """One completion from Jan, or a JanError. Returns the message content.

    **Thinking is off by default, and that matters.** Qwen3.5 reasons unless
    told not to: measured on 2026-09-16, asking the 0.8B for one line of
    dialogue spent all 160 tokens in `reasoning_content` and returned EMPTY
    content in 5.7 s, where the same request with the flag came back in 0.9 s
    with the line. Any app asking a small model for a short answer wants this.
    Pass thinking=True for the rare case where the reasoning is the point.

    Requests are serialised through LANE, so an app with a worker thread and a
    request handler cannot have two in flight at once.
    """
    return chat_messages(
        model,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        seed=seed,
        thinking=thinking,
        base_url=base_url,
        wait=wait,
        extra=extra,
    )


def chat_messages(
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.8,
    max_tokens: int = 200,
    timeout: float = 120.0,
    seed: int | None = None,
    thinking: bool = False,
    base_url: str = BASE_URL,
    wait: float = LANE_WAIT,
    extra: dict | None = None,
    stats: dict | None = None,
) -> str:
    """chat() with a whole conversation: `messages` as the OpenAI API takes
    them (system, then user and assistant turns). Small models follow a real
    back-and-forth far better than a transcript pasted into one message.
    A `stats` dict is filled with llama.cpp's speed readings: gen_tps and
    prompt_tps (tokens per second writing and reading), prompt_tokens."""
    if not model:
        raise JanError("no model named")
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if not thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if seed is not None:
        body["seed"] = seed
    if extra:
        body.update(extra)

    if not LANE.acquire(timeout=wait):
        raise JanError("Jan is busy with another request")
    try:
        request = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise JanError(f"Jan said {exc.code}: {exc.read()[:200]!r}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise JanError(f"Jan is not answering: {exc}") from exc
        except ValueError as exc:
            raise JanError(f"Jan sent something that is not JSON: {exc}") from exc
    finally:
        LANE.release()

    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise JanError(f"Jan sent no choices: {str(payload)[:200]}") from exc
    if stats is not None:
        timings = payload.get("timings") or {}
        stats.update(
            gen_tps=timings.get("predicted_per_second"),
            prompt_tps=timings.get("prompt_per_second"),
            prompt_tokens=(payload.get("usage") or {}).get("prompt_tokens"),
        )
    content = (message.get("content") or "").strip()
    if not content:
        reasoning = len(message.get("reasoning_content") or "")
        raise JanError(f"empty reply (reasoning_content was {reasoning} characters)")
    return content


JAN_DATA = JAN_MODELS.parent
ROUTER_LOG = Path.home() / "Forge" / "outputs" / "jan-router.log"
ROUTER_BUILDS = (  # preferred first: the Vulkan builds use the iGPU
    "linux-vulkan-common_cpus-x64",
    "linux-vulkan-x64",
    "linux-avx-x64",
    "linux-common_cpus-x64",
)


def router_binary(data: Path | None = None) -> Path | None:
    """Jan's newest llama-server build, or None when Jan is not installed."""
    backends = (data or JAN_DATA) / "backends"
    try:
        builds = sorted(backends.iterdir(), reverse=True)
    except OSError:
        return None
    for build in builds:
        for flavour in ROUTER_BUILDS:
            exe = build / flavour / "build" / "bin" / SERVER
            if exe.exists():
                return exe
    return None


def router_alive(base_url: str | None = None, timeout: float = 2.0) -> bool:
    try:
        _get_json(f"{(base_url or BASE_URL).rstrip('/')}/models", timeout=timeout)
        return True
    except (OSError, ValueError):
        return False


def ensure_router(wait: float = 40.0, log=None, data: Path | None = None) -> bool:
    """True when a model router answers on Jan's port, starting one if none
    does (the owner, 2026-10-07: Relay's chat must work with Jan closed).

    Only ever *starts*: the router Jan itself writes the preset for
    (`router.preset.ini`, one section per downloaded model), in its own
    session so it outlives the caller. A running router, Jan's own or one
    another app started, is never touched. Same recipe as
    apps/panelforge/panelforge/janboot.py, shared here."""
    if router_alive():
        return True
    data = data or JAN_DATA
    exe = router_binary(data)
    preset = data / "router.preset.ini"
    if exe is None or not preset.exists():
        if log:
            log("Jan is not installed here (no llama-server build or preset)")
        return False
    port = BASE_URL.rsplit(":", 1)[-1].split("/", 1)[0]
    ROUTER_LOG.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, LD_LIBRARY_PATH=str(exe.parent))
    with open(ROUTER_LOG, "ab") as out:
        subprocess.Popen(
            [str(exe), "--models-preset", str(preset)]
            + ["--host", "127.0.0.1", "--port", port],
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(exe.parent),
            env=env,
            start_new_session=True,
        )
    if log:
        log(f"started Jan's model router ({exe.parent.parent.parent.name})")
    started = time.time()
    while time.time() - started < wait:
        if router_alive(timeout=1.0):
            _models_cache["checked"] = 0.0
            return True
        time.sleep(0.5)
    if log:
        log(f"Jan's router did not answer in {wait:.0f} s; see {ROUTER_LOG}")
    return False


def _get_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(
    url: str, payload: dict, timeout: float = 30.0, key: str | None = None
) -> int:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError):
        return 0  # nothing listening: Jan closed, or is closing with Forge


_models_cache: dict = {"ids": [], "checked": 0.0}


def models(refresh: bool = False, base_url: str | None = None) -> list[str]:
    """Every model id Jan can serve. Cached, because the room polls."""
    now = time.time()
    if (
        _models_cache["ids"]
        and not refresh
        and now - _models_cache["checked"] < MODELS_TTL
    ):
        return list(_models_cache["ids"])
    try:
        data = _get_json(f"{(base_url or BASE_URL).rstrip('/')}/models")
    except (OSError, ValueError):
        return list(_models_cache["ids"])
    ids = [str(m.get("id")) for m in data.get("data") or [] if m.get("id")]
    _models_cache.update(ids=ids, checked=now)
    return ids


def _servers(proc_dir: str = "/proc"):
    """(pid, argv) for every llama-server process, the router included."""
    try:
        entries = sorted(os.listdir(proc_dir))
    except OSError:
        return
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc_dir, name, "cmdline"), "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        argv = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        if argv and os.path.basename(argv[0]) == SERVER:
            yield int(name), argv


def _flag(argv: list[str], name: str) -> str | None:
    try:
        return argv[argv.index(name) + 1]
    except (ValueError, IndexError):
        return None


def router_url(proc_dir: str = "/proc") -> str | None:
    """The running router's base URL, read from its command line. None when Jan
    is not running — callers then leave Jan alone."""
    for _pid, argv in _servers(proc_dir):
        if "--models-preset" not in argv:
            continue
        port = _flag(argv, "--port")
        if port:
            return f"http://{_flag(argv, '--host') or '127.0.0.1'}:{port}"
    return None


def router_key(proc_dir: str = "/proc") -> str | None:
    """The router's API key. Jan starts the router with a fresh LLAMA_API_KEY
    in its environment on every launch (found 2026-09-22); chat needs no key
    but /models/unload does. The process is the user's own, so its environ
    is readable."""
    for pid, argv in _servers(proc_dir):
        if "--models-preset" not in argv:
            continue
        try:
            with open(os.path.join(proc_dir, str(pid), "environ"), "rb") as fh:
                for entry in fh.read().split(b"\0"):
                    if entry.startswith(b"LLAMA_API_KEY="):
                        return entry.split(b"=", 1)[1].decode(errors="replace")
        except OSError:
            return None
    return None


def context_size(model: str, proc_dir: str = "/proc") -> int | None:
    """The context window (tokens) the router loads `model` with, from its
    --models-preset file (the model's section, else the [*] default). None
    when Jan is not running or the file says nothing."""
    for _pid, argv in _servers(proc_dir):
        preset = _flag(argv, "--models-preset")
        if not preset:
            continue
        try:
            lines = Path(preset).read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        found: dict[str, int] = {}
        section = ""
        for line in lines:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
            elif section in (model, "*") and line.split("=")[0].strip() == "ctx-size":
                try:
                    found[section] = int(line.split("=", 1)[1])
                except ValueError:
                    pass
        size = found.get(model, found.get("*"))
        return size if size and size > 0 else None
    return None


def loaded_models(proc_dir: str = "/proc") -> list[str]:
    """Models with a process of their own right now (the router aside)."""
    found = []
    for _pid, argv in _servers(proc_dir):
        if "--models-preset" in argv:
            continue
        alias = _flag(argv, "--alias")
        if alias:
            found.append(alias)
    return found


def cpu_ticks(proc_dir: str = "/proc") -> dict[str, int]:
    """CPU time each loaded model has used so far, by model id."""
    out: dict[str, int] = {}
    for pid, argv in _servers(proc_dir):
        if "--models-preset" in argv:
            continue
        alias = _flag(argv, "--alias")
        if not alias:
            continue
        try:
            with open(os.path.join(proc_dir, str(pid), "stat"), encoding="utf-8") as fh:
                fields = fh.read().rsplit(")", 1)[1].split()
            out[alias] = int(fields[11]) + int(fields[12])
        except (OSError, IndexError, ValueError):
            continue
    return out


# Said once per process, not once per idle check: a broken unload would
# otherwise print every thirty seconds for as long as Forge is open.
_unload_complained = False


def release(model: str, proc_dir: str = "/proc") -> bool:
    """Ask the router to unload one model. The next request loads it again.

    A refusal is *said out loud*, once. It used to return False in silence,
    which meant the whole point of this module -- giving the memory back --
    could fail without anything anywhere noticing. Measured on 2026-09-16: this
    Jan build answers 401 "Invalid API Key" on /models/unload, while chat and
    /v1/models need no key at all, so idle models stay loaded. Fixed
    2026-09-22: the key is the router's own LLAMA_API_KEY (router_key), sent
    as a Bearer token; the log line below still reports any other refusal.
    """
    global _unload_complained
    base = router_url(proc_dir)
    if not base:
        return False
    status = _post_json(
        f"{base}/models/unload", {"model": model}, key=router_key(proc_dir)
    )
    if status == 0:
        return False  # Jan is gone, and its memory with it
    if status >= 300:
        if not _unload_complained:
            _unload_complained = True
            print(
                f"[forge_jan] could not unload {model}: the router answered {status}. "
                "Idle models will stay in memory until Jan is restarted."
            )
        return False
    return True


# --- one app's use of Jan ----------------------------------------------------------


def _print_now(message: str) -> None:
    """print with a flush: a backend started with its output sent to a file
    buffers plain prints, and the idle watcher's 'released' lines never
    reached the log while the question of who unloads a model was open."""
    print(message, flush=True)


class AppJan:
    """One app's own Jan: its model choices, its in-flight count, and the
    watcher that releases models once nothing is using them."""

    def __init__(
        self,
        app: str,
        roles: tuple[str, ...] = (),
        proc_dir: str = "/proc",
        log=_print_now,
    ):
        self.app = app
        self.roles = tuple(roles)
        self.proc_dir = proc_dir
        self.log = log
        self._lock = threading.Lock()
        self._in_flight = 0
        self._wanted: set[str] = set()  # models this app has asked Jan to load
        self._seen: dict[str, tuple[int, float]] = {}
        self._watcher: threading.Thread | None = None
        self._stop = threading.Event()

    # settings ---------------------------------------------------------------
    def settings(self) -> dict:
        return read_settings(self.app, self.roles)

    def update(self, changes: dict) -> dict:
        return write_settings(self.app, changes, self.roles)

    def model_for(self, role: str, fallback: str = "") -> str:
        """The model this app wants for a role: its own setting, else the
        caller's fallback, else whatever Jan has first."""
        chosen = self.settings()["models"].get(role) or ""
        if chosen:
            return chosen
        if fallback:
            return fallback
        available = models()
        return available[0] if available else ""

    # using it ---------------------------------------------------------------
    def using(self, model: str):
        """`with jan.using(model):` around a request. It marks the model as
        this app's and holds off any release while the request runs."""
        return _InFlight(self, model)

    def busy(self) -> bool:
        with self._lock:
            return self._in_flight > 0

    def release_mine(self) -> list[str]:
        """Release the models this app loaded and nobody is using. Called when
        the app's backend stops, and by the idle watcher."""
        released = []
        if self.busy():
            return released
        for model in sorted(self._wanted & set(loaded_models(self.proc_dir))):
            if release(model, self.proc_dir):
                released.append(model)
        return released

    # the idle watcher -------------------------------------------------------
    def idle_models(self, now: float, ticks: dict[str, int], idle_seconds: float):
        """Models that have used no CPU for the whole wait. Anything doing work
        for any app or session keeps its place."""
        for model in list(self._seen):
            if model not in ticks:
                del self._seen[model]
        idle = []
        for model, used in ticks.items():
            previous = self._seen.get(model)
            if self.busy() or previous is None or previous[0] != used:
                self._seen[model] = (used, now)
            elif now - previous[1] >= idle_seconds:
                idle.append(model)
        return idle

    def check_idle(self, now: float | None = None) -> list[str]:
        """One pass of the watcher, exposed so tests need no clock."""
        minutes = self.settings()["idle_minutes"]
        if minutes <= 0:
            return []
        ticks = cpu_ticks(self.proc_dir)
        mine = {model: used for model, used in ticks.items() if model in self._wanted}
        released = []
        stamp = time.monotonic() if now is None else now
        for model in self.idle_models(stamp, mine, minutes * 60.0):
            if not self.busy() and release(model, self.proc_dir):
                released.append(model)
                self._seen.pop(model, None)
                self.log(
                    f"[{self.app}] released idle Jan model {model} to free memory;"
                    " it loads again on the next request"
                )
        return released

    def watch(self, check_every: float = CHECK_EVERY) -> threading.Thread:
        if self._watcher:
            return self._watcher

        def loop():
            while not self._stop.wait(check_every):
                try:
                    self.check_idle()
                except Exception as exc:  # a broken check must not kill the app
                    self.log(
                        f"[{self.app}] Jan idle check: {type(exc).__name__}: {exc}"
                    )

        self._watcher = threading.Thread(target=loop, daemon=True, name="jan-idle")
        self._watcher.start()
        return self._watcher

    def stop(self) -> list[str]:
        self._stop.set()
        return self.release_mine()


class _InFlight:
    def __init__(self, owner: AppJan, model: str):
        self.owner = owner
        self.model = model

    def __enter__(self):
        with self.owner._lock:
            self.owner._in_flight += 1
        if self.model:
            self.owner._wanted.add(self.model)
        return self.owner

    def __exit__(self, *exc):
        with self.owner._lock:
            self.owner._in_flight = max(0, self.owner._in_flight - 1)
        return False


def mount(fastapi_app, app: str, roles: tuple[str, ...] = (), watch: bool = True):
    """Give an app the standard Jan settings surface and idle release.

        jan = forge_jan.mount(app, "dossier", roles=("triage", "reason"))
        ...
        with jan.using(model):
            ...call Jan...

    Adds GET/POST /api/jan/settings and GET /api/jan/models, starts the idle
    watcher, and releases this app's models when the backend stops.
    `shared/forge_jan.js` draws the settings from those routes.
    """
    owner = AppJan(app, roles)

    @fastapi_app.get("/api/jan/settings")
    def jan_settings():
        return {
            "app": app,
            "roles": list(roles),
            "settings": owner.settings(),
            "models": models(),
            "loaded": loaded_models(),
            "running": router_url() is not None,
        }

    @fastapi_app.post("/api/jan/settings")
    def set_jan_settings(changes: dict):
        return {"settings": owner.update(changes)}

    @fastapi_app.get("/api/jan/models")
    def jan_models(refresh: bool = False):
        return {"models": models(refresh=refresh), "loaded": loaded_models()}

    @fastapi_app.on_event("shutdown")
    def _release():
        owner.stop()

    if watch:
        owner.watch()
    return owner
