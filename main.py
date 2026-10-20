import os, threading

# ==========================================
# 🌟 AUTO INSTALLER: missing library thakle bot nijei pip diye install kore
# ==========================================
def _auto_install():
    import sys, subprocess, importlib, importlib.util, site
    # (import naam, pip package naam)
    needed = [
        ("requests", "requests"),
        ("firebase_admin", "firebase-admin"),
        ("pyotp", "pyotp"),
        ("bs4", "beautifulsoup4"),
        ("neonize", "neonize"),   # WS Checker WhatsApp engine
        # WhatsApp bridge er extra library lagle ekhane add korun, jemon: ("qrcode", "qrcode[pil]")
    ]
    missing = [(mod, pkg) for mod, pkg in needed if importlib.util.find_spec(mod) is None]
    if not missing:
        return
    print(f"[auto-install] missing: {[p for _, p in missing]}", flush=True)
    for mod, pkg in missing:
        done = False
        for extra in ([], ["--break-system-packages"], ["--user"], ["--user", "--break-system-packages"]):
            cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--quiet", pkg] + extra
            try:
                if subprocess.run(cmd, timeout=600).returncode == 0:
                    done = True
                    break
            except Exception as e:
                print(f"[auto-install] {pkg} error: {e}", flush=True)
        print(f"[auto-install] {pkg}: {'OK' if done else 'FAILED'}", flush=True)
    try:
        site.main()
        user_site = site.getusersitepackages()
        if user_site not in sys.path:
            sys.path.append(user_site)
    except Exception:
        pass
    importlib.invalidate_caches()

_auto_install()

os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")
os.environ.setdefault("GRPC_DNS_RESOLVER", "native")
import faulthandler, sys
faulthandler.dump_traceback_later(60, exit=False, file=sys.stdout)  # 60s এর মধ্যে start না হলে কোথায় আটকেছে দেখাবে
try: threading.stack_size(1024 * 1024)   # প্রতি thread এ কম memory (can't start new thread fix)
except Exception: pass
import requests
import time
import json
import os
import firebase_admin
from firebase_admin import credentials, firestore
import uuid
import threading
import random
import re
import html
import pyotp
from collections import Counter 
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from datetime import datetime 
from urllib.parse import urljoin
# ==========================================
# 🌟 WS CHECKER (embedded module, alada file lagbe na)
# ==========================================
import types as _types
_WS_SRC = r'''# ws_checker.py
# ------------------------------------------------------------------
# WS Checker (WhatsApp link + auto Fresh / In Use check) for main.py
# - User nijer WhatsApp pairing code diye link kore
# - Number assign hole auto check: 🟢 Fresh (WhatsApp nai) / 🔴 In Use (WhatsApp ache)
# Requirement: WhatsApp bridge file ei folder e thakte hobe
# ------------------------------------------------------------------
import os
import sys
import types
import re
import json
import time
import copy
import asyncio
import threading
import shutil
import logging
from functools import partial
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("ws_checker")

# ───────────────────────── built-in WhatsApp engine (neonize) ─────────────────────────
SHOW_ERRORS = True   # link fail hole user ke karon dekhabe (test sesh hole False korun)


def _diag():
    """Host er limit gulo log e dekhay (failed to map segment error er karon khujte)."""
    out = [f"py={sys.version.split()[0]}"]
    try:
        import resource
        for nm in ("RLIMIT_AS", "RLIMIT_DATA", "RLIMIT_NPROC"):
            out.append(f"{nm}={resource.getrlimit(getattr(resource, nm))}")
    except Exception:
        pass
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current",
              "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            out.append(f"{os.path.basename(p)}={open(p).read().strip()}")
        except Exception:
            pass
    try:
        for line in open("/proc/meminfo"):
            if line.startswith(("MemTotal", "MemAvailable")):
                out.append(line.strip().replace("  ", ""))
    except Exception:
        pass
    try:
        import importlib.util
        sp = importlib.util.find_spec("neonize")
        path = os.path.dirname(sp.origin) if sp and sp.origin else ""
        out.append(f"neonize_dir={path}")
        best = ""
        for line in open("/proc/mounts"):
            parts = line.split()
            if len(parts) > 3 and path.startswith(parts[1]) and len(parts[1]) >= len(best):
                best = parts[1]
                opts = parts[3]
        if best:
            out.append(f"mount={best} noexec={'noexec' in opts}")
    except Exception:
        pass
    print("[ws-diag] " + " | ".join(out), flush=True)


def _reason(msg):
    if not SHOW_ERRORS or not msg:
        return ""
    low = str(msg).lower()
    if any(x in low for x in ("schedinit", "malloc.go", "failed to map segment", "memoryerror",
                              "cannot allocate memory", "out of memory")):
        return ("\n\n<i>Reason: this server's memory limit is too low to run the WhatsApp engine. "
                "The bot must run on a server without a memory cap.</i>")
    safe = str(msg)[:180].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"\n\n<i>Reason: {safe}</i>"


# Engine alada process e chole (bot er memory limit er baire thakar jonno).
# Child ar parent JSON-lines diye kotha bole. Ei string er vitore triple-quote deya jabe na.
_CHILD_SRC = r"""
import sys, os, json, threading, time, types, re

SDIR = sys.argv[1]
MODE = sys.argv[2]
PHONE = sys.argv[3] if len(sys.argv) > 3 else ""

_out = os.fdopen(os.dup(1), "w", buffering=1)
os.dup2(2, 1)
_lk = threading.Lock()


def emit(**kw):
    with _lk:
        try:
            _out.write(json.dumps(kw) + "\n")
            _out.flush()
        except Exception:
            os._exit(1)


STUBBABLE = ("cv2", "magic", "moviepy", "vidgear", "imageio")


class Stub(types.ModuleType):
    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: "application/octet-stream"


def culprit(exc):
    found = []
    tb = exc.__traceback__
    while tb:
        fn = tb.tb_frame.f_code.co_filename.replace("\\", "/")
        for n in STUBBABLE:
            if "/" + n + "/" in fn or fn.endswith("/" + n + ".py"):
                found.append(n)
        tb = tb.tb_next
    if found:
        return found[-1]
    msg = str(exc).lower()
    if "magic" in msg:
        return "magic"
    if any(x in msg for x in ("libxau", "libxcb", "libx11", "libgl", "opencv", "cv2")):
        return "cv2"
    return None


def load():
    stubbed = []
    for _ in range(5):
        try:
            from neonize.client import NewClient
            import neonize.events as ev
            if stubbed:
                sys.stderr.write("neonize loaded with stubs: %s\n" % stubbed)
            return NewClient, ev
        except BaseException as e:
            name = culprit(e)
            sys.stderr.write("import failed: %r culprit=%s\n" % (e, name))
            if not name or name in stubbed:
                raise
            sys.modules[name] = Stub(name)
            stubbed.append(name)
            for k in [k for k in list(sys.modules) if k == "neonize" or k.startswith("neonize.")]:
                sys.modules.pop(k, None)
    raise ImportError("neonize import failed")


try:
    NewClient, ev = load()
    os.makedirs(SDIR, exist_ok=True)
    client = NewClient(os.path.join(SDIR, "session.sqlite3"))
except BaseException as e:
    emit(ev="fatal", msg=repr(e)[:300])
    sys.exit(1)

state = {"connected": False, "stop": False}


def reg(name, fn):
    E = getattr(ev, name, None)
    if E is not None:
        client.event(E)(fn)


def h_connected(_c, _e):
    state["connected"] = True
    emit(ev="connected")


def h_disconnected(_c, _e):
    was = state["connected"]
    state["connected"] = False
    if not state["stop"]:
        emit(ev="disconnected", was=was)


def h_logged_out(_c, _e):
    was = state["connected"]
    state["connected"] = False
    if not state["stop"]:
        emit(ev="logged_out", was=was)


def h_qr(_c, _e):
    if MODE != "pair":
        emit(ev="qr")


reg("ConnectedEv", h_connected)
reg("DisconnectedEv", h_disconnected)
reg("LoggedOutEv", h_logged_out)
reg("QREv", h_qr)


def runner():
    try:
        client.connect()
    except BaseException as e:
        if not state["stop"]:
            emit(ev="fatal", msg=repr(e)[:300])
            time.sleep(0.5)
            os._exit(1)


threading.Thread(target=runner, daemon=True).start()


def pair_worker():
    last = None
    for attempt in range(5):
        time.sleep(3 if attempt == 0 else 4)
        if state["stop"]:
            return
        try:
            try:
                code = client.PairPhone(PHONE, show_push_notification=True)
            except TypeError:
                code = client.PairPhone(PHONE, True)
            code = getattr(code, "code", code)
            emit(ev="pair_code", code=str(code))
            return
        except BaseException as e:
            last = e
    emit(ev="pair_error", msg=repr(last)[:300])


if MODE == "pair" and PHONE:
    threading.Thread(target=pair_worker, daemon=True).start()


def do_check(i, number):
    n = "+" + re.sub(r"\D", "", str(number))
    try:
        try:
            res = client.is_on_whatsapp([n])
        except Exception:
            res = client.is_on_whatsapp(n)
        if isinstance(res, (list, tuple)):
            res = res[0] if len(res) == 1 else None
        val = getattr(res, "IsIn", None) if res is not None else None
        emit(ev="check", id=i, res=(None if val is None else bool(val)))
    except BaseException as e:
        emit(ev="check", id=i, res=None, err=repr(e)[:200])


def shutdown(do_logout):
    state["stop"] = True
    if do_logout:
        try:
            client.logout()
        except BaseException:
            pass
    try:
        client.disconnect()
    except BaseException:
        pass
    emit(ev="bye")
    os._exit(0)


emit(ev="ready")
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except Exception:
        continue
    cmd = msg.get("cmd")
    if cmd == "check":
        threading.Thread(target=do_check, args=(msg["id"], msg["number"]), daemon=True).start()
    elif cmd == "logout":
        shutdown(True)
    elif cmd == "stop":
        shutdown(False)
state["stop"] = True
os._exit(0)
"""


def _tail(path, n=400):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - n))
            return f.read().decode("utf-8", "ignore").strip().replace("\n", " | ")[-n:]
    except Exception:
        return ""


class _SubprocBridge:
    """neonize ke alada Python process e chalay. Parent (bot) shudhu JSON message pathay."""

    def __init__(self, session_dir):
        self.dir = session_dir
        self.proc = None
        self.connected = False
        self._stopping = False
        self._seq = 0
        self._pending = {}
        self._task = None

    def _send(self, obj):
        try:
            self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        except Exception as e:
            log.error("bridge send error: %s", e)

    async def start(self, use_pairing_code, phone_number, on_qr, on_pairing_code,
                    on_pairing_error, on_connected, on_disconnected):
        os.makedirs(self.dir, exist_ok=True)
        self._logpath = os.path.join(self.dir, "child.log")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
        logf = open(self._logpath, "wb")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-u", "-c", _CHILD_SRC, self.dir,
                "pair" if use_pairing_code else "restore", phone_number or "",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=logf, env=env)
        finally:
            logf.close()
        print(f"[ws] engine process started pid={self.proc.pid}", flush=True)
        self._task = asyncio.ensure_future(self._read_loop(
            use_pairing_code, on_qr, on_pairing_code, on_pairing_error, on_connected, on_disconnected))

    async def _read_loop(self, pairing, on_qr, on_pairing_code, on_pairing_error, on_connected, on_disconnected):
        fatal = ""
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                e = msg.get("ev")
                if e == "connected":
                    self.connected = True
                    await on_connected()
                elif e in ("disconnected", "logged_out"):
                    was = self.connected
                    self.connected = False
                    if not self._stopping:
                        await on_disconnected("logged_out" if e == "logged_out" else "disconnected", was)
                elif e == "qr":
                    if not pairing and not self._stopping:
                        await on_qr("")
                elif e == "pair_code":
                    await on_pairing_code(msg.get("code", ""))
                elif e == "pair_error":
                    fatal = msg.get("msg", "")
                    if not self._stopping:
                        await on_pairing_error(fatal)
                elif e == "fatal":
                    fatal = msg.get("msg", "")
                elif e == "check":
                    fut = self._pending.pop(msg.get("id"), None)
                    if fut and not fut.done():
                        fut.set_result(msg.get("res"))
        except Exception as ex:
            log.error("bridge read loop error: %s", ex)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_result(None)
            self._pending.clear()
            was = self.connected
            self.connected = False
            if not self._stopping:
                detail = fatal or _tail(self._logpath)
                print(f"[ws] engine process exited: {detail}", flush=True)
                try:
                    if pairing and not was:
                        await on_pairing_error(detail or "engine process exited")
                    else:
                        await on_disconnected("error: " + (detail or "engine exited"), was)
                except Exception as ex:
                    log.error("bridge exit callback error: %s", ex)

    async def check_number(self, number):
        """True = WhatsApp ache (In Use), False = nai (Fresh), None = jana gelo na"""
        if not self.proc or self.proc.returncode is not None or not self.connected:
            return None
        self._seq += 1
        i = self._seq
        fut = asyncio.get_running_loop().create_future()
        self._pending[i] = fut
        self._send({"cmd": "check", "id": i, "number": re.sub(r"\D", "", str(number))})
        try:
            return await asyncio.wait_for(fut, 30)
        except asyncio.TimeoutError:
            self._pending.pop(i, None)
            return None

    async def _end(self, cmd, wait):
        self._stopping = True
        self.connected = False
        p = self.proc
        if p and p.returncode is None:
            self._send({"cmd": cmd})
            try:
                await asyncio.wait_for(p.wait(), wait)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        if self._task and self._task is not asyncio.current_task():
            self._task.cancel()

    async def disconnect(self):
        await self._end("stop", 6)

    async def logout(self):
        """WhatsApp er Linked Devices theke o remove kore."""
        await self._end("logout", 15)


def _resolve_bridge():
    try:   # alada whatsapp_bridge.py thakle seta agey
        return __import__("whatsapp_" + "bridge").WhatsAppBridge
    except Exception:
        pass
    try:
        import importlib.util
        if importlib.util.find_spec("neonize") is not None:
            return _SubprocBridge
    except Exception:
        pass
    return None


WhatsAppBridge = _resolve_bridge()
_IMPORT_ERR = None if WhatsAppBridge else "neonize not installed (see [auto-install] log)"

BASE_DIR = os.environ.get("WS_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
os.makedirs(BASE_DIR, exist_ok=True)
SESSION_ROOT = os.path.join(BASE_DIR, "ws_sessions")
DATA_FILE = os.path.join(BASE_DIR, "ws_data.json")

MAX_SESSIONS = 5         # low thread limit host e besi session dilo na
PAIR_EXPIRE_SEC = 300    # pairing code 5 minute
CHECK_TIMEOUT = 25       # ekta number check er max time
CHECK_DELAY = 1.0        # multiple number e gap

_send = _edit = _render = _cancel_kb = None
_user_states = _temp_data = _active_sessions = None
_loop = None
_pool = None
_bridges = {}      # uid -> bridge
_attempt = {}      # uid -> token (stale event ignore korar jonno)
_connecting = {}   # uid -> phone (pairing cholche)
_restarts = {}     # uid -> koybar engine abar chalu kora hoyeche
_data = {}         # str(uid) -> {phone, linked, connected, since, fresh, used}
_lock = threading.RLock()


# ───────────────────────── setup ─────────────────────────
def init(send_message, edit_message, user_states, temp_data, active_sessions,
         render=None, cancel_kb=None):
    global _send, _edit, _render, _cancel_kb, _user_states, _temp_data
    global _active_sessions, _loop, _pool
    _send, _edit = send_message, edit_message
    _user_states, _temp_data, _active_sessions = user_states, temp_data, active_sessions
    _render = render or (lambda t: t)
    _cancel_kb = cancel_kb or (lambda: {"inline_keyboard": [[{"text": "Cancel", "callback_data": "cancel_state"}]]})

    os.makedirs(SESSION_ROOT, exist_ok=True)
    _load()
    try:
        _diag()
    except Exception:
        pass
    _pool = ThreadPoolExecutor(max_workers=2)
    _loop = asyncio.new_event_loop()

    def _runner():
        asyncio.set_event_loop(_loop)
        _loop.run_forever()

    threading.Thread(target=_runner, daemon=True, name="ws-loop").start()
    if WhatsAppBridge is not None:
        asyncio.run_coroutine_threadsafe(_restore_all(), _loop)
    else:
        log.error("whatsapp_bridge import failed: %s", _IMPORT_ERR)


def _load():
    global _data
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            _data = json.load(f)
    except Exception:
        _data = {}


def _save():
    with _lock:
        try:
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, DATA_FILE)
        except Exception as e:
            log.error("ws save error: %s", e)


def _rec(uid):
    return _data.setdefault(str(uid), {})


def _dir(uid):
    return os.path.join(SESSION_ROOT, f"user_{uid}")


def is_connected(uid) -> bool:
    return bool(_data.get(str(uid), {}).get("connected")) and uid in _bridges


def _mark_unlinked(uid):
    r = _rec(uid)
    r["linked"] = False
    r["connected"] = False
    _save()


# ───────────────────────── messaging ─────────────────────────
LINK_KB = [[{"text": "🔗 Link WhatsApp Account", "callback_data": "ws_link"}]]


async def _say(uid, text, kb=None):
    fn = partial(_send, uid, _render(text), reply_markup=({"inline_keyboard": kb} if kb else None))
    await asyncio.get_running_loop().run_in_executor(_pool, fn)


def _bd_time(ts):
    return time.strftime("%d %b %Y, %I:%M %p", time.gmtime(int(ts) + 6 * 3600)) + " (BD)"


def _panel(uid):
    d = _data.get(str(uid), {})
    if WhatsAppBridge is None:
        return ("⚠️ <b>WS Checker engine is not installed on the server.</b>\nPlease contact admin.", None)
    if is_connected(uid):
        text = (
            "📱 <b>WHATSAPP NUMBER CHECKER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>Status:</b> Connected &amp; Active 🟢\n"
            f"<b>Linked Account:</b> +{d.get('phone', '')}\n\n"
            "💡 <b>Auto-Check Active:</b> Whenever you assign or request numbers from the bot, "
            "they will be automatically verified for WhatsApp status (🟢 Fresh / 🔴 In Use)."
        )
        kb = [
            [{"text": "My Session Details", "callback_data": "ws_details", "style": "primary"}],
            [{"text": "Unlink WhatsApp Account", "callback_data": "ws_unlink", "style": "danger"}],
        ]
        return text, kb
    if uid in _connecting:
        text = (
            "📱 <b>WHATSAPP NUMBER CHECKER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>Status:</b> Connecting... ⏳\n\n"
            "💡 Enter the pairing code in WhatsApp (Settings → Linked Devices) to finish linking."
        )
        kb = [
            [{"text": "Get New Code", "callback_data": "ws_agree"}],
            [{"text": "Cancel Linking", "callback_data": "ws_cancel", "style": "danger"}],
        ]
        return text, kb
    text = (
        "📱 <b>WHATSAPP NUMBER CHECKER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ <b>Status:</b> Not Connected\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Link a secondary WhatsApp number to verify active accounts automatically before taking OTPs.</i>"
    )
    return text, LINK_KB


def show_panel(uid, msg_id=None):
    text, kb = _panel(uid)
    markup = {"inline_keyboard": kb} if kb else None
    if msg_id:
        r = _edit(uid, msg_id, _render(text), reply_markup=markup)
        if r and r.get("ok") is False and "not modified" not in str(r.get("description", "")):
            _send(uid, _render(text), reply_markup=markup)
    else:
        _send(uid, _render(text), reply_markup=markup)


# ───────────────────────── link flow ─────────────────────────
def _ask_phone(uid, msg_id):
    _user_states[uid] = "wait_for_ws_phone"
    _temp_data[uid] = {"msg_id": msg_id}
    text = (
        "💬 <b>ENTER YOUR WHATSAPP NUMBER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Please type your full WhatsApp phone number including country code (without + or spaces).</b>\n\n"
        "<b>Example:</b>\n"
        "<b>• 8801712345678 (Bangladesh)</b>\n"
        "<b>• 14155552671 (USA)</b>\n"
        "<b>• 919876543210 (India)</b>\n\n"
        "💡 <i>Send your number in the chat now:</i>"
    )
    r = _edit(uid, msg_id, _render(text))
    if r and r.get("ok") is False:
        _send(uid, _render(text))


def handle_state(uid, text) -> bool:
    """main.py er user_states block theke call hobe. True mane handled."""
    if _user_states.get(uid) != "wait_for_ws_phone":
        return False
    digits = re.sub(r"\D", "", text or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if not (8 <= len(digits) <= 15):
        _send(uid, _render(
            "☑️ <b>Invalid Phone Number Format</b>\n\n"
            "<b>Please make sure to include the country code without + or symbols.</b>\n"
            "<b>Example: 8801712345678</b>"))
        return True
    _user_states.pop(uid, None)
    _temp_data.pop(uid, None)
    if WhatsAppBridge is None:
        show_panel(uid)
        return True
    if is_connected(uid):
        show_panel(uid)
        return True
    if uid not in _bridges and len(_bridges) >= MAX_SESSIONS:
        _send(uid, _render("⚠️ <b>Server is full right now.</b> Please try again later or contact admin."))
        return True
    _connecting[uid] = digits
    _send(uid, _render("⏳ <i>Requesting pairing code... please wait.</i>"))
    fut = asyncio.run_coroutine_threadsafe(_start_bridge(uid, digits, pairing=True), _loop)
    fut.add_done_callback(partial(_on_start_done, uid))
    return True


def _on_start_done(uid, fut):
    try:
        exc = fut.exception()
    except BaseException:
        return
    if exc:
        import traceback
        print("[ws] start failed uid=%s:\n%s" % (
            uid, "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))), flush=True)
        asyncio.run_coroutine_threadsafe(_fail_start(uid, str(exc)), _loop)


async def _fail_start(uid, err):
    await _drop(uid)
    _mark_unlinked(uid)
    await _say(uid, "❌ <b>Could not start WhatsApp linking.</b>\nPlease try again." + _reason(err), LINK_KB)


async def _drop(uid, wipe=True):
    b = _bridges.pop(uid, None)
    _attempt.pop(uid, None)
    _connecting.pop(uid, None)
    if b:
        try:
            if wipe and hasattr(b, "logout"):
                await asyncio.wait_for(b.logout(), 20)
            else:
                await asyncio.wait_for(b.disconnect(), 15)
        except Exception:
            pass
    if wipe:
        shutil.rmtree(_dir(uid), ignore_errors=True)


async def _expire(uid, token):
    await asyncio.sleep(PAIR_EXPIRE_SEC)
    if _attempt.get(uid) == token and not is_connected(uid):
        await _drop(uid)
        await _say(uid, "⌛ <b>Pairing code expired.</b>\nTap the button below to get a new code.", LINK_KB)


async def _start_bridge(uid, phone, pairing, silent=False):
    if uid in _bridges:
        await _drop(uid, wipe=pairing)
    os.makedirs(_dir(uid), exist_ok=True)
    bridge = WhatsAppBridge(_dir(uid))
    token = time.time()
    _bridges[uid] = bridge
    _attempt[uid] = token
    if pairing:
        _connecting[uid] = phone

    def alive():
        return _attempt.get(uid) == token

    async def on_qr(_qr):
        if pairing or not alive():
            return
        # restore korar somoy QR chaile mane session expired
        await _drop(uid)
        _mark_unlinked(uid)
        await _say(uid, "⚠️ <b>Your WhatsApp session expired.</b>\nPlease link again.", LINK_KB)

    async def on_pairing_code(code):
        if not alive():
            return
        code = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
        text = (
            "🔑 <b>YOUR PAIRING CODE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Code:</b> <code>{code}</code>\n\n"
            "<b>How to link:</b>\n"
            "1. Open WhatsApp on your phone\n"
            "2. Go to Settings → Linked Devices\n"
            "3. Tap Link a Device\n"
            "4. Tap \"Link with phone number instead\"\n"
            "5. Enter the 8-digit code shown above (or tap button below)\n\n"
            "💡 <i>Code expires in 5 minutes. You will get a notification when verified!</i>"
        )
        kb = [[{"text": f"Copy Code: {code}", "copy_text": {"text": code}, "style": "primary"}]]
        await _say(uid, text, kb)
        asyncio.ensure_future(_expire(uid, token))

    async def on_pairing_error(_msg):
        print(f"[ws] pairing error uid={uid}: {_msg}", flush=True)
        if not alive():
            return
        await _drop(uid)
        _mark_unlinked(uid)
        await _say(uid, (
            "❌ <b>Could not send the link request.</b>\n\n"
            "Possible reasons:\n"
            "• This number has no WhatsApp\n"
            "• Wrong number format (use country code)\n"
            "• Temporary server problem\n\n"
            "Please try again." + _reason(_msg)), LINK_KB)

    async def on_connected():
        if not alive():
            return
        r = _rec(uid)
        was_pairing = uid in _connecting
        if phone:
            r["phone"] = phone
        if not r.get("since") or was_pairing:
            r["since"] = int(time.time())
        r.setdefault("fresh", 0)
        r.setdefault("used", 0)
        r["linked"] = True
        r["connected"] = True
        _restarts.pop(uid, None)
        _connecting.pop(uid, None)
        _save()
        if was_pairing and not silent:
            await _say(uid, "✅ <b>WhatsApp Session Connected Successfully!</b>\n\n"
                            "You can now check numbers from the WS Checker menu.")

    async def on_disconnected(reason, was_connected):
        if not alive():
            return
        _rec(uid)["connected"] = False
        _save()
        if "logged_out" in (reason or "").lower():
            await _drop(uid)
            _mark_unlinked(uid)
            await _say(uid, "❌ <b>WhatsApp has been logged out.</b>\nLink again from the WS Checker menu.", LINK_KB)
        elif (reason or "").startswith("error") and _rec(uid).get("linked") and _restarts.get(uid, 0) < 3:
            n = _restarts[uid] = _restarts.get(uid, 0) + 1
            await asyncio.sleep(5 * n)
            if alive():
                try:
                    await _start_bridge(uid, _rec(uid).get("phone"), pairing=False, silent=True)
                except Exception as ex:
                    print(f"[ws] restart failed uid={uid}: {ex!r}", flush=True)
        # normal disconnect: WhatsApp nijei abar connect kore, user ke spam korbo na

    await bridge.start(
        use_pairing_code=pairing,
        phone_number=phone if pairing else None,
        on_qr=on_qr,
        on_pairing_code=on_pairing_code,
        on_pairing_error=on_pairing_error,
        on_connected=on_connected,
        on_disconnected=on_disconnected,
    )


async def _restore_all():
    """Bot restart hole ager linked session gulo silently abar connect kore."""
    for uid_s, d in list(_data.items()):
        if not d.get("linked"):
            continue
        if len(_bridges) >= MAX_SESSIONS:
            break
        uid = int(uid_s)
        d["connected"] = False
        fut = asyncio.ensure_future(_start_bridge(uid, d.get("phone"), pairing=False, silent=True))
        fut.add_done_callback(lambda f: f.exception() and log.error("ws restore error: %s", f.exception()))
        await asyncio.sleep(2)  # ek sathe onek thread na khulte


# ───────────────────────── callbacks ─────────────────────────
def handle_callback(call) -> bool:
    data = call.get("data", "")
    if not data.startswith("ws_"):
        return False
    uid = call["message"]["chat"]["id"]
    msg_id = call["message"]["message_id"]
    try:
        if data == "ws_link":
            if WhatsAppBridge is None or is_connected(uid):
                show_panel(uid, msg_id)
            else:
                text = (
                    "❗ <b>IMPORTANT SAFETY NOTICE</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    "🔒 <b>Account Guidelines:</b>\n"
                    "<b>• Please use a spare / secondary WhatsApp number.</b>\n"
                    "<b>• Do NOT link your main personal or business account.</b>\n"
                    "<b>• Automated checking carries a risk of account ban by WhatsApp.</b>\n\n"
                    "💡 <i>By continuing, you acknowledge and accept this safety advice.</i>"
                )
                kb = [[{"text": "✅ I Agree & Continue", "callback_data": "ws_agree"},
                       {"text": "☑️ Cancel", "callback_data": "ws_notice_cancel"}]]
                _edit(uid, msg_id, _render(text), reply_markup={"inline_keyboard": kb})

        elif data == "ws_agree":
            if WhatsAppBridge is None or is_connected(uid):
                show_panel(uid, msg_id)
            else:
                _ask_phone(uid, msg_id)

        elif data == "ws_notice_cancel":
            show_panel(uid, msg_id)

        elif data == "ws_panel":
            show_panel(uid, msg_id)

        elif data == "ws_details":
            d = _data.get(str(uid), {})
            text = (
                "📋 <b>MY SESSION DETAILS</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Account:</b> +{d.get('phone', '')}\n"
                f"<b>Status:</b> {'Connected &amp; Active 🟢' if is_connected(uid) else 'Disconnected 🔴'}\n"
                f"<b>Linked Since:</b> {_bd_time(d['since']) if d.get('since') else '-'}\n"
                f"<b>Auto-Checked:</b> 🟢 Fresh {d.get('fresh', 0)} | 🔴 In Use {d.get('used', 0)}"
            )
            kb = [[{"text": "Back", "callback_data": "ws_panel", "style": "primary"}]]
            _edit(uid, msg_id, _render(text), reply_markup={"inline_keyboard": kb})

        elif data == "ws_unlink":
            text = (
                "⚠️ <b>Are you sure you want to unlink your WhatsApp account?</b>\n\n"
                "This will disconnect your session and cancel any ongoing checks."
            )
            kb = [[
                {"text": "Yes, Unlink", "callback_data": "ws_unlink_yes", "style": "danger"},
                {"text": "Keep Linked", "callback_data": "ws_keep", "style": "success"},
            ]]
            _edit(uid, msg_id, _render(text), reply_markup={"inline_keyboard": kb})

        elif data == "ws_keep":
            _edit(uid, msg_id, _render("✅ <b>Action cancelled.</b>"),
                  reply_markup={"inline_keyboard": [[{"text": "Back", "callback_data": "ws_panel", "style": "primary"}]]})

        elif data in ("ws_unlink_yes", "ws_cancel"):
            try:
                asyncio.run_coroutine_threadsafe(_drop(uid), _loop).result(25)
            except Exception:
                pass
            _mark_unlinked(uid)
            msg = ("✅ <b>WhatsApp account unlinked successfully.</b>" if data == "ws_unlink_yes"
                   else "✅ <b>Linking cancelled.</b>")
            _edit(uid, msg_id, _render(msg), reply_markup={"inline_keyboard": LINK_KB})
    except Exception as e:
        log.error("ws callback error: %s", e)
    return True


# ───────────────────────── auto check on assign ─────────────────────────
def decorate(chat_id, nums, kb, text):
    """Number assign hoye message pathano hoye gele eta call korun.
    User er WhatsApp linked thakle background e check kore button/message e 🟢/🔴 boshay."""
    try:
        if _loop is None or not nums or not is_connected(chat_id):
            print(f"[ws] decorate skipped uid={chat_id}: loop={_loop is not None} nums={bool(nums)} "
                  f"connected_flag={bool(_data.get(str(chat_id), {}).get('connected'))} bridge={chat_id in _bridges}", flush=True)
            return
        msg_id = (_active_sessions.get(chat_id) or {}).get("msg_id")
        if not msg_id:
            print(f"[ws] decorate skipped uid={chat_id}: no active msg_id", flush=True)
            return
        asyncio.run_coroutine_threadsafe(
            _decorate(chat_id, msg_id, list(nums), copy.deepcopy(kb), text), _loop)
    except Exception as e:
        log.error("ws decorate error: %s", e)


async def _check_one(uid, num):
    b = _bridges.get(uid)
    if not b or not is_connected(uid):
        return None
    for attempt in range(2):          # result na ele (None) ekbar abar try, jate sob country er number e dot ashe
        try:
            res = await asyncio.wait_for(b.check_number(num), CHECK_TIMEOUT)
        except Exception as e:
            print(f"[ws] check error uid={uid}: {e!r}", flush=True)
            res = None
        if res is not None:
            return res
        await asyncio.sleep(1.0)
    return None


async def _decorate(uid, msg_id, nums, kb, text):
    results = []
    for i, n in enumerate(nums):
        results.append(await _check_one(uid, re.sub(r"\D", "", str(n))))
        if i < len(nums) - 1:
            await asyncio.sleep(CHECK_DELAY)

    sess = _active_sessions.get(uid) or {}
    if sess.get("msg_id") != msg_id:      # user Change Number korle purono message e edit korbo na
        return

    fresh = sum(1 for r in results if r is False)
    used = sum(1 for r in results if r is True)
    unk = len(results) - fresh - used
    r = _rec(uid)
    r["fresh"] = r.get("fresh", 0) + fresh
    r["used"] = r.get("used", 0) + used
    _save()

    j = 0
    for row in kb:
        if row and "copy_text" in row[0] and j < len(results):
            res = results[j]
            icon = "🟢" if res is False else ("🔴" if res is True else "⚠️")
            row[0]["text"] = f"{row[0]['text']} {icon}"   # number er shesh e dot (video moto)
            j += 1

    if len(results) == 1:
        line = ("🟢 <b>WA STATUS: FRESH</b>" if fresh else
                "🔴 <b>WA STATUS: IN USE</b>" if used else
                "⚠️ <b>WA STATUS: UNKNOWN</b>")
    else:
        line = f"<b>WA STATUS:</b> 🟢 Fresh {fresh} | 🔴 In Use {used}"
        if unk:
            line += f" | ⚠️ {unk}"

    new_text = text + "\n" + _render(line)
    fn = partial(_edit, uid, msg_id, new_text, reply_markup={"inline_keyboard": kb})
    r = await asyncio.get_running_loop().run_in_executor(_pool, fn)
    print(f"[ws] decorate uid={uid} results={results} edit_ok={(r or {}).get('ok')} {(r or {}).get('description', '')}", flush=True)
'''
ws_checker = _types.ModuleType("ws_checker")
ws_checker.__file__ = os.path.abspath(__file__)
sys.modules["ws_checker"] = ws_checker
exec(compile(_WS_SRC, "<ws_checker>", "exec"), ws_checker.__dict__)

# ==========================================
# Configuration (Token & Owner ID)
# ==========================================
TOKEN = "8877308560:AAEugZrQJq4ERHTln4nNBIvZukjeCeHolWs"
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"
FILE_URL = f"https://api.telegram.org/file/bot{TOKEN}/"

OWNER_ID = 8763551849
BOT_USERNAME = ""
DB_FILE = "bot_data.json"

# ==========================================
# Premium Emoji Database
# ==========================================
PEM = {
    "ok": '<tg-emoji emoji-id="6215041273309434461">✅</tg-emoji>',
    "no": '<tg-emoji emoji-id="6206110936789423908">❌</tg-emoji>',
    "warn": '<tg-emoji emoji-id="6206077285720659346">⚠️</tg-emoji>',
    "admin": '<tg-emoji emoji-id="6224537252727364146">📊</tg-emoji>',
    "user": '<tg-emoji emoji-id="6307589808424228455">👤</tg-emoji>',
    "file": '<tg-emoji emoji-id="5800835941543186089">📁</tg-emoji>',
    "rocket": '<tg-emoji emoji-id="5188481279963715781">🚀</tg-emoji>',
    "graph": '<tg-emoji emoji-id="6206343625232619150">📊</tg-emoji>',
    "money": '<tg-emoji emoji-id="6170375743734817047">💸</tg-emoji>',
    "gift": '<tg-emoji emoji-id="6169973554407285878">🎁</tg-emoji>',
    "msg": '<tg-emoji emoji-id="6170018561369583643">💬</tg-emoji>',
    "gear": '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>',
    "link": '<tg-emoji emoji-id="6170144133328414061">🔗</tg-emoji>',
    "trash": '<tg-emoji emoji-id="5775903905498010383">🗑</tg-emoji>',
    "upload": '<tg-emoji emoji-id="6046627337821231012">📤</tg-emoji>',
    "world": '<tg-emoji emoji-id="5375177250553487549">🌐</tg-emoji>',
    "lock": '<tg-emoji emoji-id="5352888345972187597">🔐</tg-emoji>',
    "phone": '<tg-emoji emoji-id="5197269100878907942">📱</tg-emoji>',
    "num": '<tg-emoji emoji-id="5190806721286657692">🔢</tg-emoji>',
    "pin": '<tg-emoji emoji-id="6170452357361442411">📍</tg-emoji>',
    "star": '<tg-emoji emoji-id="6224060236479600711">✨</tg-emoji>',
    "hi": '<tg-emoji emoji-id="5199885118214255386">👋</tg-emoji>'
}

GLOBAL_BODY_EMOJIS = {
    "➖": "6170414278181397227", "🚫": "6170473406996160824", "😒": "6170118028517187553",
    "🖥": "6170247775184232577", "🌐": "6170462480599358632", "🌟": "6170016890627302393",
    "🕓": "6170346993223739212", "⌛": "6170088779789901724", "💬": "6170018561369583643",
    "🔐": "6170445197650960977", "🍏": "6169961648757941769", "❔": "6170116838811247135",
    "⚠️": "6170363752186128772", "🔥": "6170233558842482964", "💸": "6170375743734817047",
    "🥚": "6170305125882536870", "👨‍⚖": "6170162455658897329", "🐁": "6170247775184232577",
    "🧻": "6170300053526158329", "⚗": "6170247775184232577", "🛴": "6170247775184232577",
    "📊": "6170307874661606527", "🔢": "6170237755025530933", "👤": "6170037192937710571",
    "📁": "6170029178528735972", "🚀": "6170041032638473025", "💎": "6170391794027602592",
    "📍": "6169989518800724866", "👋": "6170260350848474739", "✅": "6170351859421684453",
    "1️⃣": "6170095973860123461", "2️⃣": "6169981298233319856", "3️⃣": "6170275924399889537",
    "4️⃣": "6170380322169953911", "5️⃣": "6170310563311134546", "6️⃣": "6169948368719061246",
    "7️⃣": "6170004684330247691", "8️⃣": "6170004684330247691", "9️⃣": "6169967197855687258",
    "🔤": "6170121370001745635", "📣": "6170002150299542446", "📤": "6170480364843181323",
    "✨": "6170118831676072322", "🔹": "6170305125882536870", "🎙": "6169949171877945461",
    "💴": "6170034920900011245", "📅": "6170076062391738761", "📴": "6170407629572023922",
    "✏️": "6170073176173716185", "📱": "6170032945215054536", "🔗": "6170330762542325503",
    "❌": "6170209558565233298", "⚙️": "6169946238415282157", "🫂": "6170331093254809380",
    "➕": "6170076281435071205", "🗑": "6170207484096029598", "🎁": "6169973554407285878",
    "➤": "6170006952072979935", "🏢": "6169992602587242222", "💳": "6170218006765905352",
    "📝": "6170399752602002753", "🛡": "6170094522161177006", "🤝": "6170278020343931445",
    "💰": "6170444261348090306", "👀": "6170183874660802880", "🕹": "6170089058962776765",
    "🟢": "6170021069630480745", "🧪": "6170074013692338587", "🎨": "6170038678996399869",
    "📂": "6170029178528735972", "🌍": "6170356979022700944", "📌": "6169989518800724866",
    "📢": "6170002150299542446", "🆔": "6169941093044461843", "📈": "6170343544364998890",
    "🔔": "6170056872477860782", "🏦": "6170361909645156512", "🧾": "6170065458117485547",
    "👨‍⚖️": "6170162455658897329", "🔍": "6170439820351907260", "↘️": "6170077896342773779",
    "🔑": "6170443982175216273", "🔽": "6170053934720229383", "🤕": "6170452894232355345",
    "💣": "5388632425314140043", "🎟️": "6170032945215054536", "🍍": "6170385123943391899",
}

DEFAULT_CUSTOM_MESSAGES = {
    "start": {"text": "╔═══════════╗\n       📊 NUMBER BOT\n╚═══════════╝\n🚀 Welcome to Number & OTP Service\n━━━━━━━━━━━━\n✅ Choose an option below\nto continue using the bot.\n━━━━━━━━━━━━\n💎 Premium OTP Service", "buttons": []},
    "get_number": {"text": f"{PEM['pin']} Select a service 🔽", "buttons": []},
    "select_country": {"text": f"🤕 Select a country for {{service}} 🔽", "buttons": []}, 
    "search_number": {"text": "╔═══════════╗\n     🔍 <b>SEARCH NUMBER</b>\n╚═══════════╝\n✅ Enter 3 to 9 digits  \nto search for a number.\n━━━━━━━━━━━━━\n📝 Example:\n➥ 880\n➥ 9227373\n━━━━━━━━━━━━━\n🔍 Fast Number Lookup System", "buttons": []},
    "traffic": {"text": f"{PEM['graph']} <b>Traffic Overview</b>\n\n{PEM['ok']} Available Numbers: {{avail}}\n{PEM['rocket']} Assigned Numbers: {{assigned}}", "buttons": []},
    "refer": {"text": f"➖➖➖➖➖➖➖\n« {PEM['gift']} REFER & EARN »\n➖➖➖➖➖➖➖\n{PEM['link']} YOUR LINK:\n<code>{{ref_link}}</code>\n➖➖➖➖➖➖➖\n{PEM['user']} TOTAL REFERS: <b>{{total_ref}}</b>\n➖➖➖➖➖➖➖\n{PEM['money']} PER REFER: <b>{{ref_reward}} TK</b>\n➖➖➖➖➖➖➖", "buttons": []},
    "withdrawal": {"text": "➖➖➖➖➖➖➖\n《 😒 WITHDRAWAL 》\n➖➖➖➖➖➖➖\n👋 Total Otp: {total_otp}\n➖➖➖➖➖➖➖\n🫂 Total Reffer :{total_ref}\n➖➖➖➖➖➖➖\n📅 BALANCE: {bal}৳\n➖➖➖➖➖➖➖\n🔐 MINIMUM: {min_w} ৳\n➖➖➖➖➖➖➖\nSELECT METHOD:", "buttons": []},
    "support": {"text": f"{PEM['msg']} Contact us for any help:", "buttons": []}
}

# ==========================================
# Firebase Setup
# ==========================================
firebase_credentials_json = r"""
{
  "type": "service_account",
  "project_id": "newo-58a6e",
  "private_key_id": "46b8ffef401269d6d1ad0fc4413fb9e9a31debff",
  "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCo/bYBFlF98e33\nfhcHznGKP8jcgCtYlsPLYbzTkOUmYRg5tExvXVKpvHzdwy9dqI+XR5kZvTMZkcsS\nvYo097ffcFLsmmXku1lPeFCu3tWQyAnwZSfMUK7Fo0/G6A0VjbTPHMZ5bs2McKqZ\nxdSR0xaB0fnnbzh8YcdymcsoshkeX9YZyfPlbxedmcYTlF5q2FAQx7Gpby5M3G7V\n12AHk2IyKChEXfPXHxoJWwqfj4gk50kP0Nnp0QZ8otO+gCilud+5iMiSWhvgc8nU\niwvn5Z4XK/27T86wNyW7xDDQ8v8WY/tjv41g055V2U90+oDPjEQqTF8I5UvDobYq\nMc+bu44/AgMBAAECggEALqhiDHnLhTD7SWZkWHDs00VaIVs33I0/wNMa7fTm98o+\nBIydv++tx5R1i0r9NGg6QQPxJvSge7JPtbxkQMyWVVgvZQkaEeWSOolXCXkX0RIS\nAHAFZXlvT70P5TSjjCxkW/yvZc6sbNacm7lNv/dekvhU/cus9laO2TrSJ0nI+qP7\nPJXKem7K4rmOHWPBeWlUTy/yFPiN7vTXOnaES5q2JsXJoXvFpOA9uERc0sGwoDqc\nCZJ7aqoc193AkdLtalvQnW7xl4TPZfNOjxAywIUmc1UO9EvuHTK0A15MHet6z7nV\n4eMGpdXpLh8nC8u9SGhi9dVttVj/7el1rgO5dc372QKBgQDaakA1h5K/iTux/coC\n+yTurObXsxn1aRl/0uIfalgh+8wAytyJbhbyBo7zFqwq9uVc/q3jdU558VswUMmn\nRfIb0BgCJiPVn/K3uYiAyE0g+1vp4jlZgyFDduXlwmeXguji42cKEycITqUsMx4K\na1kwSb2rfSCDqrp/idOEAtM7ZwKBgQDGEjSX410thbUgPv25JJPwyVKTa/icctmT\nJtc/fqoqx0c7EqFPdqMBE17RqdTZkAHKgWSm21DKYpFZn+5bqnMuoiXwbbEdeVHG\nJaNzXa2gS3ojjBUEEYyRZ8nDxWX3Qn0lR+dg3LkkYmHpOR7r3BY5iv/SFmT187eE\n9PejXuinaQKBgCjXm4AnBOL6J4aGHaNnESXsgRIutJj2eUU0ywHy4d4g9qnjRVAR\nnQrO/SYpxDIYjxVPrnaNNgqJfnLPeJXl93sJ86NbPHIzAbEnaYYjboDPYAWhPvYl\nFjBGaIEJVFO5sBDVUyxIHpFoG2zjdieeoUPtViFeOZlFcLButnXGDXfbAoGAFcpm\n8tbWp21wUc3cSAQYKoNbjFByl3OjMCczww6CVhZ6ZatVB9L7d+DTq6lNe2n2aHr3\nwcubBcj2SK/pnzgPV/AK0Hqg46c3Md9uLgD+WwYyt7pvjXUIy+5pKhqLbzpnXjSc\n5fEihTg1oVzhY/1gjJAYONqx3MUlSYFRxS1p1xECgYEAmZZBt6SzO4oK9SWQAfgw\nPxT8y1Tku26wTKyA8iMC+PiW+Qc6WQX/vnZPzVyusF/3EKlX7tUsrojruwKpb2Od\ng6Nj8HYUnRZicOMU0o2s5SDUwL3xH4HmoT/1NEjf719zg3KkXFFvek3GrkstbrP+\nbv/R0LB/x7Vb1SN6IpzHGpg=\n-----END PRIVATE KEY-----\n",
  "client_email": "firebase-adminsdk-fbsvc@newo-58a6e.iam.gserviceaccount.com",
  "client_id": "106307346001575802129",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk-fbsvc%40newo-58a6e.iam.gserviceaccount.com",
  "universe_domain": "googleapis.com"
}
"""

def _load_firebase_credentials():
    # 1) serviceAccountKey.json file  2) FIREBASE_CREDENTIALS env  3) উপরের inline JSON
    path = os.environ.get("FIREBASE_KEY_FILE", "serviceAccountKey.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    env = os.environ.get("FIREBASE_CREDENTIALS")
    if env:
        return json.loads(env)
    return json.loads(firebase_credentials_json)

try:
    cred = credentials.Certificate(_load_firebase_credentials())
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    # দ্রুত test read: key invalid হলে bot আটকে না থেকে সাথে সাথে জানিয়ে দেবে
    db.collection('settings').document('bot_config').get(timeout=10, retry=None)
    print("✅ Firebase Connected! (Full Sync Enabled)")
except Exception as e:
    print(f"❌ Firebase Error (bot চলবে কিন্তু Firestore ছাড়া): {e}")
    print("👉 Firebase Console > Project settings > Service accounts > Generate new private key করে serviceAccountKey.json আপলোড করো")
    db = None

bot_settings = {
    "admins": [OWNER_ID],
    "panels": [], 
    "fw_groups": [], 
    "otp_link": "https://t.me/your_otp_group",
    "withdraw_on": True,
    "min_withdraw": 30.0,
    "otp_reward": 0.1,
    "refer_reward": 0.2,
    "cooldown": 10,
    "num_req": 3,
    "num_share": 1, 
    "support_link": "https://t.me/your_support",
    "w_methods": ["bKash", "Nagad"],
    "w_group": "", 
    
    "fj_on": False,
    "ws_on": True,
    "btn_color": "original",
    "fj_channels": [], 
    "stex_keys": [], 
    "voltx_keys": [],
    "zenex_keys": [],
    "zebra_keys": [],
    "scarface_keys": [],
    "search_countries": [],
    "voltx_search_countries": [],
    "zenex_search_countries": [],
    "zebra_search_countries": [],
    "scarface_search_countries": [],
    "stex_services": {},
    "voltx_services": {},
    "zenex_services": {},
    "zebra_services": {},
    "scarface_services": {},
    "premium_flags": {
        "1": {"char": "🇺🇸", "iso": "US", "name": "United States", "id": "5913463998522592692"},
        "880": {"char": "🇧🇩", "iso": "BD", "name": "Bangladesh", "id": "5911365056594973179"},
        "91": {"char": "🇮🇳", "iso": "IN", "name": "India", "id": "5913754823643107921"},
        "92": {"char": "🇵🇰", "iso": "PK", "name": "Pakistan", "id": "5913705895375672082"},
        "44": {"char": "🇬🇧", "iso": "GB", "name": "United Kingdom", "id": "5913443365499703513"}
    },
    "premium_apps": {
        "FACEBOOK": {"char": "🚫", "id": "5389064576333527180", "name": "Facebook"},
        "WHATSAPP": {"char": "🚫", "id": "5233354831984353090", "name": "WhatsApp"}
    },
    "custom_messages": DEFAULT_CUSTOM_MESSAGES.copy()
}

FS_KEYS = [
    "admins", "panels", "fw_groups", "otp_link", "withdraw_on", 
    "min_withdraw", "otp_reward", "refer_reward", "cooldown", 
    "num_req", "num_share", "support_link", "w_methods", "w_group", 
    "stex_keys", "voltx_keys", "zenex_keys", "zebra_keys", "scarface_keys",
    "search_countries", "voltx_search_countries", "zenex_search_countries", "zebra_search_countries", "scarface_search_countries",
    "stex_services", "voltx_services", "zenex_services", "zebra_services", "scarface_services",
    "fj_on", "fj_channels", "ws_on", "btn_color",
    "premium_flags", "premium_apps", "custom_messages"
]

number_batches = {}
used_numbers_list = []
stex_assigned_numbers = {} 
voltx_assigned_numbers = {}
zenex_assigned_numbers = {}
zebra_assigned_numbers = {}
scarface_assigned_numbers = {}
assigned_number_metadata = {}
STEX_BASE_URL = "https://api.2oo9.cloud/MXS47FLFX0U/tness/@public/api"
VOLTX_BASE_URL = "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
ZENEX_BASE_URL = "https://api.zenexnetwork.com"
ZEBRA_BASE_URL = "https://api.zebrasms.com"
SCARFACE_BASE_URL = "https://nexaotpservice.com/api/v1"
total_uploaded_stats = 0
total_assigned_stats = 0
processed_otps = set() 
recent_traffic = []
user_banned_cache = {}

# Active HTTP sessions for Auto Captcha Panels
panel_sessions = {}

# 🌟 sAjaxSource (AJAX/DataTable) এবং Fallback HTML Parser Helper Function
def fetch_cpt_panel_cdrs(p, session, check_url):
    res = session.get(check_url, timeout=15)
    html_text = res.text
    
    # সেশন শেষ হয়েছে বা লগইন পেজে রিডাইরেক্ট করেছে কি না তা চেক করা
    if "login" in html_text.lower() or "signin" in html_text.lower() or any(x in html_text for x in ["Sign in to your account", "Please sign in", "Welcome back!"]):
        raise Exception("Session expired")
        
    soup = BeautifulSoup(html_text, 'html.parser')
    s_ajax_source = ""
    for script in soup.find_all("script"):
        script_text = script.string or ""
        match = re.search(r'sAjaxSource":\s*"([^"]+)"', script_text)
        if match:
            s_ajax_source = match.group(1)
            break
            
    results = []
    
    n_col_name = p.get("num_col_name", "number").lower()
    m_col_name = p.get("msg_col_name", "message").lower()
    n_idx = int(p.get("num_col_idx", 1)) - 1 if p.get("num_col_idx") else 1
    m_idx = int(p.get("msg_col_idx", 2)) - 1 if p.get("msg_col_idx") else 2

    # ৫.১ যদি sAjaxSource AJAX লিংক পাওয়া যায়
    if s_ajax_source:
        baseUrl = p.get("login_url", "").split("/client")[0].split("/login")[0].strip()
        if not baseUrl.startswith("http"):
            baseUrl = "http://" + baseUrl
            
        full_ajax_url = ""
        if s_ajax_source.startswith("http"):
            full_ajax_url = s_ajax_source
        elif s_ajax_source.startswith("/"):
            full_ajax_url = f"{baseUrl}{s_ajax_source}"
        else:
            last_slash_idx = check_url.rfind("/")
            current_dir = check_url[:last_slash_idx]
            full_ajax_url = f"{current_dir}/{s_ajax_source}"

        if "iDisplayLength" not in full_ajax_url:
            # 250 এর জায়গায় 10000 করে দেওয়া হলো যাতে সব মেসেজ ফেচ করতে পারে
            query_params = "sEcho=1&iColumns=7&iDisplayStart=0&iDisplayLength=10000&sSearch=&iSortingCols=1&iSortCol_0=0&sSortDir_0=desc"
            divider = "&" if "?" in full_ajax_url else "?"
            full_ajax_url += f"{divider}{query_params}"

        ajax_headers = {
            "Referer": check_url,
            "X-Requested-With": "XMLHttpRequest"
        }
        
        ajax_res = session.get(full_ajax_url, headers=ajax_headers, timeout=15)
        data_dict = ajax_res.json()
        rows = data_dict.get("aaData", [])
        for row_val in rows:
            if not isinstance(row_val, list):
                continue
                
            if len(row_val) < max(n_idx, m_idx) + 1:
                continue
                
            num_val = row_val[n_idx] if (0 <= n_idx < len(row_val)) else row_val[2]
            msg_val = row_val[m_idx] if (0 <= m_idx < len(row_val)) else row_val[4]
            
            clean_num = re.sub(r'\D', '', str(num_val))
            if clean_num and 5 <= len(clean_num) <= 18:
                otp = extract_otp_code(msg_val)
                if otp and len(msg_val) > 4:
                    results.append({"number": clean_num, "message": msg_val, "otp": otp})
                    
    else:
        # ৫.২ ডাইরেক্ট HTML টেবিল থেকে রিড করার ব্যাকআপ লজিক
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            if not rows: continue
            
            final_n_idx = n_idx
            final_m_idx = m_idx
            
            header_cells = rows[0].find_all(['th', 'td'])
            for i, cell in enumerate(header_cells):
                c_text = cell.get_text(strip=True).lower()
                if n_col_name in c_text: final_n_idx = i
                if m_col_name in c_text: final_m_idx = i

            for row in rows:
                cols = row.find_all(['td', 'th'])
                if all(c.name == 'th' for c in cols): continue
                
                if len(cols) > max(final_n_idx, final_m_idx):
                    num_text = cols[final_n_idx].get_text(separator=" ", strip=True)
                    msg_text = cols[final_m_idx].get_text(separator=" ", strip=True)
                    
                    clean_num = re.sub(r'\D', '', num_text)
                    if clean_num and 5 <= len(clean_num) <= 18:
                        otp = extract_otp_code(msg_text)
                        if otp and len(msg_text) > 4:
                            results.append({"number": clean_num, "message": msg_text, "otp": otp})
                            
    return results, html_text

# Track active number sessions to expire them automatically
class _SessionDict(dict):
    def __setitem__(self, uid, val):
        super().__setitem__(uid, val)
        try: _aux_pool.submit(_on_numbers_assigned, uid, dict(val))
        except Exception: pass
user_active_sessions = _SessionDict()

def load_db():
    global bot_settings, number_batches, used_numbers_list, total_uploaded_stats, total_assigned_stats, recent_traffic, processed_otps, stex_assigned_numbers, voltx_assigned_numbers, assigned_number_metadata
    if db:
        try:
            doc = db.collection('settings').document('bot_config').get()
            if doc.exists:
                fs_data = doc.to_dict()
                for k in FS_KEYS:
                    if k in fs_data:
                        bot_settings[k] = fs_data[k]
                print("✅ Config Loaded from Firestore!")
                
                # 🌟 ফায়ারবেস থেকে আপলোড করা নাম্বারগুলো রিস্টোর করা হচ্ছে
                try:
                    extra_doc = db.collection('settings').document('bot_extra_data').get()
                    if extra_doc.exists:
                        e_data = extra_doc.to_dict()
                        global stex_assigned_numbers, voltx_assigned_numbers, assigned_number_metadata
                        
                        for k, v in e_data.get("number_batches", {}).items():
                            number_batches[k] = v
                            
                        used_numbers_list.extend(e_data.get("used_numbers_list", []))
                        total_uploaded_stats = e_data.get("total_uploaded_stats", 0)
                        total_assigned_stats = e_data.get("total_assigned_stats", 0)
                        stex_assigned_numbers = e_data.get("stex_assigned_numbers", {})
                        voltx_assigned_numbers = e_data.get("voltx_assigned_numbers", {})
                        assigned_number_metadata = e_data.get("assigned_number_metadata", {})
                        
                        if "processed_otps" in e_data:
                            processed_otps.update(e_data["processed_otps"])
                        print("✅ Numbers & Extra Data Loaded from Firestore!")
                except Exception as e:
                    pass
            else:
                fs_data = {k: bot_settings[k] for k in FS_KEYS}
                db.collection('settings').document('bot_config').set(fs_data)
                print("✅ Firestore Config Initialized!")
        except Exception as e:
            print(f"❌ Error loading from Firestore: {e}")

    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding='utf-8') as f:
                data = json.load(f)
                saved_settings = data.get("bot_settings", {})
                for key, val in saved_settings.items():
                    if key not in FS_KEYS:
                        if key == "custom_messages":
                            for m_key, m_val in val.items():
                                bot_settings["custom_messages"][m_key] = m_val
                        else:
                            bot_settings[key] = val
                        
                for m_key, m_val in DEFAULT_CUSTOM_MESSAGES.items():
                    if m_key not in bot_settings["custom_messages"]:
                        bot_settings["custom_messages"][m_key] = m_val
                        
                number_batches = data.get("number_batches", {})
                used_numbers_list = data.get("used_numbers_list", [])
                total_uploaded_stats = data.get("total_uploaded_stats", 0)
                total_assigned_stats = data.get("total_assigned_stats", 0)
                recent_traffic = data.get("recent_traffic", [])
                stex_assigned_numbers = data.get("stex_assigned_numbers", {})
                voltx_assigned_numbers = data.get("voltx_assigned_numbers", {})
                assigned_number_metadata = data.get("assigned_number_metadata", {})
                
                # 🌟 বট রিস্টার্ট দিলে যাতে পুরনো OTP আবার না যায় তার জন্য মেমোরি লোড
                loaded_otps = data.get("processed_otps", [])
                processed_otps.update(loaded_otps)
            print("✅ Local Stock/UI DB Loaded Successfully!")
        except Exception as e:
            print(f"❌ Error loading local DB: {e}")

def save_local_db():
    local_data = {
        "bot_settings": {k: v for k, v in bot_settings.items() if k not in FS_KEYS},
        "number_batches": number_batches,
        "used_numbers_list": used_numbers_list,
        "total_uploaded_stats": total_uploaded_stats,
        "total_assigned_stats": total_assigned_stats,
        "recent_traffic": recent_traffic,
        "stex_assigned_numbers": stex_assigned_numbers,
        "voltx_assigned_numbers": voltx_assigned_numbers,
        "assigned_number_metadata": globals().get("assigned_number_metadata", {}),
        "processed_otps": list(processed_otps)
    }
    try:
        with open(DB_FILE, "w", encoding='utf-8') as f:
            json.dump(local_data, f, indent=4)
    except Exception as e:
        pass

def _sync_fs():
    if not db: return
    fs_data = {k: bot_settings[k] for k in FS_KEYS if k in bot_settings}
    try:
        db.collection('settings').document('bot_config').set(fs_data)
        
        # 🌟 আপলোড করা নাম্বার ও এক্সট্রা ডেটা ফায়ারবেসে সেভ করা হচ্ছে
        db.collection('settings').document('bot_extra_data').set({
            "number_batches": number_batches,
            "used_numbers_list": used_numbers_list,
            "total_uploaded_stats": total_uploaded_stats,
            "total_assigned_stats": total_assigned_stats,
            "stex_assigned_numbers": stex_assigned_numbers,
            "voltx_assigned_numbers": voltx_assigned_numbers,
            "assigned_number_metadata": globals().get("assigned_number_metadata", {}),
            "processed_otps": list(processed_otps) if 'processed_otps' in globals() else []
        })
    except: pass

_fs_dirty = threading.Event()
_fs_lock = threading.Lock()
_fs_worker_started = False

def _fs_worker():
    while True:
        _fs_dirty.wait()
        _fs_dirty.clear()
        try: _sync_fs()
        except Exception: pass
        time.sleep(1)

def save_db():
    global _fs_worker_started
    save_local_db()
    # 🌟 একটাই permanent worker thread: প্রতিবার নতুন thread বানানো হবে না
    with _fs_lock:
        if not _fs_worker_started:
            try:
                threading.Thread(target=_fs_worker, daemon=True).start()
                _fs_worker_started = True
            except RuntimeError:
                _sync_fs()
                return
    _fs_dirty.set()

load_db()

user_states = {}
temp_data = {}
user_cooldowns = {}
pending_withdrawals = {}

# ==========================================
# Telegram API & Helpers
# ==========================================
_aux_pool = ThreadPoolExecutor(max_workers=8)
tg_session = requests.Session() # 🌟 Keep-Alive Connection (Makes bot 10x faster)

BTN_COLOR_MODES = {
    "original": "Original (default)",
    "blue": "Blue",
    "green": "Green",
    "red": "Red",
    "rainbow": "Rainbow",
    "plain": "No Color",
}
_BTN_STYLE = {"blue": "primary", "green": "success", "red": "danger"}


def apply_btn_color(markup):
    """Admin je color choose korse seta bot er SOB button e boshay (inline + reply keyboard)."""
    try:
        mode = bot_settings.get("btn_color", "original")
        if mode == "original" or mode not in BTN_COLOR_MODES:
            return markup
        was_str = isinstance(markup, str)
        data = json.loads(markup) if was_str else markup
        if not isinstance(data, dict):
            return markup
        import copy as _copy
        data = _copy.deepcopy(data)
        rows = data.get("inline_keyboard") or data.get("keyboard") or []
        i = 0
        for row in rows:
            for btn in row:
                if not isinstance(btn, dict):
                    continue
                if mode == "plain":
                    btn.pop("style", None)
                elif mode == "rainbow":
                    btn["style"] = ("primary", "success", "danger")[i % 3]
                else:
                    btn["style"] = _BTN_STYLE[mode]
                i += 1
        return json.dumps(data) if was_str else data
    except Exception:
        return markup


def btn_color_keyboard():
    cur = bot_settings.get("btn_color", "original")
    def b(mode):
        mark = "✅ " if cur == mode else ""
        return {"text": mark + BTN_COLOR_MODES[mode], "callback_data": f"btnclr_{mode}"}
    return {"inline_keyboard": [
        [b("original"), b("plain")],
        [b("blue"), b("green"), b("red")],
        [b("rainbow")],
        [{"text": "Back", "callback_data": "back_to_admin"}],
    ]}


def api_call(method, payload=None):
    url = f"{BASE_URL}/{method}"
    if payload and payload.get("reply_markup"):
        payload = dict(payload)
        payload["reply_markup"] = apply_btn_color(payload["reply_markup"])
    if payload:
        payload = dict(payload)
        if payload.get("parse_mode") == "HTML":
            for _k in ("text", "caption"):
                if isinstance(payload.get(_k), str): payload[_k] = _emoji_sub(payload[_k])
        if payload.get("reply_markup"):
            payload["reply_markup"] = _strip_btn_icons(payload["reply_markup"])
    try:
        # 🌟 Added timeout to prevent hanging!
        res = tg_session.post(url, json=payload, timeout=15)
        return res.json()
    except Exception as e:
        return {}

def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True}
    if reply_markup: payload["reply_markup"] = reply_markup
    return api_call("sendMessage", payload)

def send_photo(chat_id, photo_url_or_file_id, caption="", reply_markup=None, parse_mode="HTML"):
    payload = {"chat_id": chat_id, "photo": photo_url_or_file_id, "caption": caption, "parse_mode": parse_mode}
    if reply_markup: payload["reply_markup"] = reply_markup
    return api_call("sendPhoto", payload)

def edit_message(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True}
    if reply_markup: payload["reply_markup"] = reply_markup
    return api_call("editMessageText", payload)

def delete_message(chat_id, message_id):
    return api_call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

def answer_callback(callback_id, text="", show_alert=False):
    api_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text, "show_alert": show_alert})

def send_document(chat_id, filename, text_content):
    url = f"{BASE_URL}/sendDocument"
    files = {'document': (filename, text_content)}
    data = {'chat_id': chat_id}
    try: requests.post(url, data=data, files=files)
    except: pass

# 🌟 Local User List to completely remove Firebase Read Costs!
all_known_users = set()

def sync_users_list():
    global all_known_users
    try:
        if os.path.exists("users_list.json"):
            with open("users_list.json", "r") as f:
                all_known_users = set(json.load(f))
        if not all_known_users and db:
            for doc in db.collection('users').select([]).stream():
                all_known_users.add(doc.id)
            with open("users_list.json", "w") as f:
                json.dump(list(all_known_users), f)
    except: pass

try:
    threading.Thread(target=sync_users_list, daemon=True).start()
except RuntimeError:
    print("⚠️ sync_users_list thread failed, skipping (users_list.json will be used)")

def _save_users_list():
    try:
        with open("users_list.json", "w") as f:
            json.dump(list(all_known_users), f)
    except: pass

def register_user_local(uid):
    uid_str = str(uid)
    if uid_str not in all_known_users:
        all_known_users.add(uid_str)
        # 🌟 Non-blocking background save (Prevents lag)
        _save_users_list()

def broadcast_copymessage(from_chat_id, msg_id):
    success = 0
    failed = 0
    users = list(all_known_users)
    
    # 🌟 Dedicated Connection Pool for Broadcast (Fixes Port Exhaustion & Network Lag)
    b_session = requests.Session()
    url = f"{BASE_URL}/copyMessage"
    
    for user_id in users:
        payload = {"chat_id": user_id, "from_chat_id": from_chat_id, "message_id": msg_id}
        try:
            res = b_session.post(url, json=payload, timeout=5).json()
            if res.get("ok"): success += 1
            else: failed += 1
        except:
            failed += 1
        time.sleep(0.035) # Safe speed (28 msgs/sec) to prevent Telegram Ban
        
    send_message(from_chat_id, render_body_text(f"📢 <b>Broadcast Completed!</b>\n✅ Success: {success}\n❌ Failed: {failed}\n👥 Total Sent: {len(users)}"))

def render_body_text(text):
    if not text: return str(text)
    return _emoji_sub(text)

def get_real_service(num, fallback_srv):
    clean_n = str(num).replace("+", "").strip()
    if 'assigned_number_metadata' in globals() and clean_n in assigned_number_metadata:
        return assigned_number_metadata[clean_n]["service"]
    return fallback_srv

def get_real_owner(num):
    clean_n = str(num).replace("+", "").strip()
    
    # ১. পার্মানেন্ট মেমোরি চেক (Exact + Endswith)
    if 'assigned_number_metadata' in globals():
        for stored_n, data in assigned_number_metadata.items():
            stored_clean = str(stored_n).replace("+", "").strip()
            if clean_n == stored_clean or (len(stored_clean) >= 6 and clean_n.endswith(stored_clean[-8:])) or (len(clean_n) >= 8 and stored_clean.endswith(clean_n[-8:])):
                if "owner" in data: return data["owner"]
                
    # ২. অ্যাকটিভ সেশন চেক
    for uid, sess in user_active_sessions.items():
        for n in sess.get("nums", []):
            stored_clean = str(n).replace("+", "").strip()
            if clean_n == stored_clean or (len(stored_clean) >= 6 and clean_n.endswith(stored_clean[-8:])) or (len(clean_n) >= 8 and stored_clean.endswith(clean_n[-8:])):
                return uid
                
    # ৩. StexSMS চেক
    for n, uid in stex_assigned_numbers.items():
        stored_clean = str(n).replace("+", "").strip()
        if clean_n == stored_clean or (len(stored_clean) >= 6 and clean_n.endswith(stored_clean[-8:])) or (len(clean_n) >= 8 and stored_clean.endswith(clean_n[-8:])):
            return uid
            
    # ৪. Voltx চেক
    for n, uid in voltx_assigned_numbers.items():
        stored_clean = str(n).replace("+", "").strip()
        if clean_n == stored_clean or (len(stored_clean) >= 6 and clean_n.endswith(stored_clean[-8:])) or (len(clean_n) >= 8 and stored_clean.endswith(clean_n[-8:])):
            return uid
            
    return None

def get_display_number(num_str, remove_cc=False):
    clean_num = str(num_str).lstrip("+")
    if remove_cc:
        sorted_codes = sorted(bot_settings.get("premium_flags", {}).keys(), key=len, reverse=True)
        for code in sorted_codes:
            if clean_num.startswith(code):
                return clean_num[len(code):]
        return clean_num
    return f"+{clean_num}"

def get_custom_inbox_msg(num, msg_text, app_name, otp, owner_id=None):
    char, iso = get_flag_and_code(num)
    c_name = iso
    for code, fdata in bot_settings.get("premium_flags", {}).items():
        if fdata.get("iso") == iso:
            c_name = fdata.get("name", iso)
            break
            
    clean_raw_number = str(num).replace("+", "").strip()
    actual_service = get_real_service(num, app_name)
    
    app_full_name, prem_app_html = get_service_info_html(actual_service, msg_text)
    srv_name_upper = html.escape(app_full_name.upper())
    
    remove_cc = False
    if owner_id:
        u_data = get_user(owner_id)
        remove_cc = u_data.get("remove_cc", False)
        
    if remove_cc:
        sorted_codes = sorted(bot_settings.get("premium_flags", {}).keys(), key=len, reverse=True)
        for code in sorted_codes:
            if clean_raw_number.startswith(code):
                clean_raw_number = clean_raw_number[len(code):] 
                break
    else:
        clean_raw_number = f"+{clean_raw_number}"
        
    reward = assigned_number_rewards.get(num, bot_settings.get("otp_reward", 0.0)) if 'assigned_number_rewards' in globals() else bot_settings.get("otp_reward", 0.0)
    if 'assigned_number_metadata' in globals() and clean_raw_number in assigned_number_metadata:
        reward = assigned_number_metadata[clean_raw_number]["reward"]
    
    text = (
        f"<blockquote><tg-emoji emoji-id=\"6170462480599358632\">🌍</tg-emoji> <b>Country:</b> {c_name}</blockquote>\n"
        f"<blockquote><tg-emoji emoji-id=\"6170457257919129220\">📂</tg-emoji> <b>Service:</b> {prem_app_html} {srv_name_upper}</blockquote>\n"
        f"<blockquote><tg-emoji emoji-id=\"6170032945215054536\">📱</tg-emoji> <b>Number:</b> <code>{clean_raw_number}</code></blockquote>\n"
        f"<blockquote><tg-emoji emoji-id=\"6170445197650960977\">🔐</tg-emoji> <b>OTP:</b> <code>{otp}</code></blockquote>\n"
        f"<blockquote><tg-emoji emoji-id=\"6170277719696221481\">📝</tg-emoji> <b>Full SMS:</b> {html.escape(msg_text)}</blockquote>"
    )
    return render_body_text(text)

def extract_premium_html(msg):
    text = msg.get("text", msg.get("caption", ""))
    entities = msg.get("entities", msg.get("caption_entities", []))
    if not entities: return text
    try:
        b_text = text.encode('utf-16-le')
        c_entities = [e for e in entities if e.get("type") == "custom_emoji"]
        c_entities.sort(key=lambda x: x["offset"], reverse=True)
        for ent in c_entities:
            offset = ent["offset"] * 2
            length = ent["length"] * 2
            eid = ent["custom_emoji_id"]
            emoji_char = b_text[offset:offset+length].decode('utf-16-le')
            html_tag = f'<tg-emoji emoji-id="{eid}">{emoji_char}</tg-emoji>'
            replacement = html_tag.encode('utf-16-le')
            b_text = b_text[:offset] + replacement + b_text[offset+length:]
        return b_text.decode('utf-16-le')
    except Exception as e:
        return text 

def get_flag_info_from_num(num):
    clean = num.replace("+", "").replace(" ", "")
    sorted_codes = sorted(bot_settings.get("premium_flags", {}).keys(), key=len, reverse=True)
    for code in sorted_codes:
        if clean.startswith(code):
            data = bot_settings["premium_flags"][code]
            return data["char"], data.get("iso", "XX"), data.get("id")
    return "🌍", "XX", None

def get_flag_and_code(num):
    char, iso, _ = get_flag_info_from_num(num)
    return char, iso

def get_flag_info_html(num_or_iso, return_full_name=False):
    if len(num_or_iso) == 2:
        for code, data in bot_settings.get("premium_flags", {}).items():
            if data.get("iso") == num_or_iso:
                eid = data.get("id")
                char = data.get("char")
                name = data.get("name", num_or_iso)
                if return_full_name: return name
                if eid: return f'<tg-emoji emoji-id="{eid}">{char}</tg-emoji>'
                return char
        if return_full_name: return num_or_iso
        return "🌍"
        
    char, _, eid = get_flag_info_from_num(num_or_iso)
    if return_full_name:
        for code, data in bot_settings.get("premium_flags", {}).items():
            clean = num_or_iso.replace("+", "").replace(" ", "")
            if clean.startswith(code): return data.get("name", num_or_iso)
        return num_or_iso
        
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{char}</tg-emoji>'
    return char

def mask_number(num):
    clean = num.replace("+", "").replace(" ", "")
    if len(clean) >= 8: return f"{clean[:4]}𓊈𝗥+𝗦𓊉{clean[-4:]}"
    elif len(clean) > 2: return f"{clean[:1]}𓊈𝗥+𝗦𓊉{clean[-1:]}"
    return clean

def get_group_otp_msg(num, app_full_name, prem_app_html, iso, lang_name, otp):
    display_num = f"+{num}" if not str(num).startswith("+") else str(num)
    masked = mask_number(display_num)
    prem_flag_html = get_flag_info_html(iso)
    
    text = (
        f"╔════ {prem_flag_html} {iso} • {app_full_name.upper()} {prem_app_html} ════╗\n"
        f"│\n"
        f"│<tg-emoji emoji-id=\"6170032945215054536\">📞</tg-emoji> 𝗡𝗨𝗠𝗕𝗘𝗥 :  {masked}\n"
        f"│\n"
        f"│ <tg-emoji emoji-id=\"6170356979022700944\">🌐</tg-emoji>  𝗟𝗔𝗡𝗚𝗨𝗔𝗚𝗘 :  {lang_name}\n"
        f"╠══════════════════════╣\n"
        f"│ <tg-emoji emoji-id=\"6170445197650960977\">🔐</tg-emoji> 𝗬𝗢𝗨𝗥 𝗢𝗧𝗣\n"
        f"│\n"
        f"│      <tg-emoji emoji-id=\"6170450776813478108\">🛡</tg-emoji> <code>{otp}</code> <tg-emoji emoji-id=\"6170439794582102391\">👀</tg-emoji>\n"
        f"╚══════════════════════╝"
    )
    return render_body_text(text)

LANG_MAP = {
    "#EN": "English", "#BN": "Bengali", "#AR": "Arabic", "#HI": "Hindi", 
    "#PA": "Punjabi", "#GU": "Gujarati", "#OR": "Odia", "#TA": "Tamil", 
    "#TE": "Telugu", "#KN": "Kannada", "#ML": "Malayalam", "#SI": "Sinhala", 
    "#TH": "Thai", "#LO": "Lao", "#BO": "Tibetan", "#MY": "Burmese", 
    "#AM": "Amharic", "#KM": "Khmer", "#KA": "Georgian", "#HY": "Armenian", 
    "#HE": "Hebrew", "#EL": "Greek", "#RU": "Russian", "#ZH": "Chinese", 
    "#JA": "Japanese", "#KO": "Korean", "#ID": "Indonesian", "#MS": "Malay", 
    "#VN": "Vietnamese", "#TL": "Filipino", "#ES": "Spanish", "#PT": "Portuguese", 
    "#FR": "French", "#DE": "German", "#IT": "Italian", "#PL": "Polish", 
    "#TR": "Turkish", "#NL": "Dutch", "#SV": "Swedish", "#DA": "Danish", 
    "#NO": "Norwegian", "#FI": "Finnish", "#CS": "Czech", "#SK": "Slovak", 
    "#HU": "Hungarian", "#RO": "Romanian", "#HR": "Croatian", "#BG": "Bulgarian", 
    "#UK": "Ukrainian", "#SW": "Swahili", "#AF": "Afrikaans"
}

# ==========================================
# 🌟 ADVANCED SERVICE & LANGUAGE DETECTION
# ==========================================

SERVICE_SMS_KEYWORDS = {
    # 🟢 Social Media & Chat (Added Arabic Keywords)
    "whatsapp": ["whatsapp", "whatsa", "whatsap", "whats", "whatsapp business", "whatsapp me", "whatsapp code", "whatsap", "واتساب", "واتساپ", "واٹس ایپ", "व्हाट्सएप", "वाट्सएप", "वॉट्सऐप", "व्हाट्सप्प", "হোয়াটসঅ্যাপ", "হোটসঅ্যাপ", "ватсап", "уотсап", "вотсап", "ватс апп", "వాట్సాప్", "വാട്‌സ്ആപ്പ്", "வாட்ஸ்அப்", "ವಾಟ್ಸಾಪ್", "વોટ્સએપ", "ਵਟਸਐਪ", "ହ୍ଵାଟସ୍ ଆପ୍", "වට්ස්ඇප්", "วอตส์แอปป์", "วอทส์แอพ", "ဝက်စ်အက်ပ်", "វ៉តសាប់", "ວອດແອັບ", "ワッツアップ", "왓츠앱", "whatsapp的", "whatsapp验证码", "וואטסאפ", "γουάτσαπ", "ዋትስአፕ", "ვოთსאფი", "վոթսափ"],
    "facebook": ["facebook", "fb", "meta", "fbook", "fb code", "facebook code", "فيسبوك", "فيس بوك"],
    "instagram": ["instagram", "insta", "ig", "ig code", "instagram code", "انستغرام", "انستقرام"],
    "telegram": ["telegram", "tg", "tele", "telegram code", "tg code", "t.me", "تيليجرام", "تليجرام"],
    "tiktok": ["tiktok", "tik tok", "tikvideo", "tiktok code", "tik code", "تيك توك"],
    "snapchat": ["snapchat", "snap", "snap code", "سناب شات"],
    "twitter": ["twitter", "x.com", "x code", "twitter code", "تويتر"],
    "discord": ["discord", "discord code", "ديسكورد"],
    "viber": ["viber", "viber code", "فايبر"],
    "line": ["line", "line code", "line verification", "لاين"],
    "wechat": ["wechat", "we chat", "wechat code", "وي تشات"],
    "signal": ["signal", "signal code", "سيجنال"],
    "linkedin": ["linkedin", "linked in", "لينكد إن"],
    "imo": ["imo", "imo code", "imo verification", "ايمو"],
    "kakaotalk": ["kakao", "kakaotalk", "كاكاو"],
    "qq": ["qq", "tencent qq"],
    "vk": ["vk", "vkontakte"],

    # 🔵 Tech & Mail
    "google": ["google", "gmail", "youtube", "g-", "google voice", "جوجل", "غوغل"],
    "microsoft": ["microsoft", "ms", "outlook", "live.com", "hotmail"],
    "apple": ["apple", "icloud", "itunes", "apple id"],
    "yahoo": ["yahoo", "yahoo code", "ymail"],
    "protonmail": ["proton", "protonmail"],
    
    # 💰 Crypto & Trading
    "binance": ["binance", "bnb", "binances"],
    "coinbase": ["coinbase"],
    "okx": ["okx", "okex"],
    "kucoin": ["kucoin"],
    "bybit": ["bybit"],
    "huobi": ["huobi", "htx"],
    "mexc": ["mexc"],
    "trustwallet": ["trust wallet", "trustwallet"],

    # 💳 Finance & Wallets
    "bkash": ["bkash", "b-kash", "bkash code"],
    "nagad": ["nagad", "nagad code"],
    "rocket": ["rocket", "dutch bangla"],
    "upay": ["upay", "upay code"],
    "paypal": ["paypal", "pay pal"],
    "paytm": ["paytm"],
    "cashapp": ["cash app", "cashapp"],
    "wise": ["wise", "transferwise"],

    # 🛒 E-commerce & Delivery
    "amazon": ["amazon", "amzn", "amazon code"],
    "ebay": ["ebay"],
    "aliexpress": ["aliexpress", "ali express"],
    "alibaba": ["alibaba"],
    "daraz": ["daraz", "daraz code"],
    "foodpanda": ["foodpanda", "food panda"],
    "uber": ["uber", "uber code", "uber verification", "uber eats"],
    "pathao": ["pathao", "pathao ride"],

    # 🎮 Gaming & Entertainment
    "netflix": ["netflix", "netflix code"],
    "spotify": ["spotify", "spotify code"],
    "steam": ["steam", "steam guard"],
    "epicgames": ["epic games", "epicgames"],
    "roblox": ["roblox", "roblox code"],
    "riotgames": ["riot", "riot games", "valorant", "league of legends"],
    "garena": ["garena", "free fire", "freefire"],
    "playstation": ["playstation", "psn"],

    # 🎲 Betting & Casino
    "1xbet": ["1xbet", "1x bet"],
    "melbet": ["melbet", "melbet code"],
    "linebet": ["linebet"],
    "bet365": ["bet365"],
    "megapari": ["megapari"],

    # ❤️ Dating
    "tinder": ["tinder", "tinder code"],
    "bumble": ["bumble"],
    "badoo": ["badoo"]
}

def detect_service(text):
    text_lower = str(text).lower()
    for service_key, keywords in SERVICE_SMS_KEYWORDS.items():
        for kw in keywords:
            # \b ব্যবহার করা হয়েছে যাতে শব্দের মাঝখানের অক্ষর ম্যাচ না করে (যেমন: codigo তে ig)
            if re.search(r'\b' + re.escape(kw) + r'\b', text_lower):
                return service_key.upper()
    return None

def get_service_info_html(service_text, msg_text=""):
    s = str(service_text).upper().strip()
    m = str(msg_text).lower().strip()
    apps = bot_settings.get("premium_apps", {})
    
    detected_service = s
    # যদি ইউজার আগে থেকেই সঠিক সার্ভিস সিলেক্ট করে থাকে, তাহলে বট আর জোর করে নাম পাল্টাবে না
    is_known = any(app_name == s or app_name in s or s in app_name for app_name in apps.keys())
    
    if m and not is_known:
        for service_key, keywords in SERVICE_SMS_KEYWORDS.items():
            for kw in keywords:
                if re.search(r'\b' + re.escape(kw) + r'\b', m):
                    detected_service = service_key.upper()
                    break
            if detected_service != s: break

    clean_s = re.sub(r'[^\w\s]', '', detected_service).strip()
    
    for app_name, data in apps.items():
        if app_name == detected_service or app_name == clean_s or app_name in detected_service or detected_service in app_name:
            full_name = data.get("name", app_name.title())
            char = data.get("char", "📱")
            eid = data.get("id")
            if eid: return full_name, f'<tg-emoji emoji-id="{eid}">{char}</tg-emoji>'
            return full_name, char
            
    if len(detected_service) > 20:
        return "Message", "💬"
        
    return detected_service.title(), "📱"

def detect_language(text):
    if not text: return "#EN"
    text_str = str(text)

    # ১. Unicode Block দিয়ে নিখুঁত বর্ণমালা শনাক্তকরণ (100% Accurate for scripts)
    if any('\u0600' <= c <= '\u06ff' for c in text_str): return "#AR" # Arabic / Persian / Urdu
    if any('\u0980' <= c <= '\u09ff' for c in text_str): return "#BN" # Bengali
    if any('\u0900' <= c <= '\u097f' for c in text_str): return "#HI" # Hindi / Marathi / Nepali
    if any('\u0a00' <= c <= '\u0a7f' for c in text_str): return "#PA" # Punjabi (Gurmukhi)
    if any('\u0a80' <= c <= '\u0aff' for c in text_str): return "#GU" # Gujarati
    if any('\u0b00' <= c <= '\u0b7f' for c in text_str): return "#OR" # Odia
    if any('\u0b80' <= c <= '\u0bff' for c in text_str): return "#TA" # Tamil
    if any('\u0c00' <= c <= '\u0c7f' for c in text_str): return "#TE" # Telugu
    if any('\u0c80' <= c <= '\u0cff' for c in text_str): return "#KN" # Kannada
    if any('\u0d00' <= c <= '\u0d7f' for c in text_str): return "#ML" # Malayalam
    if any('\u0d80' <= c <= '\u0dff' for c in text_str): return "#SI" # Sinhala
    if any('\u0e00' <= c <= '\u0e7f' for c in text_str): return "#TH" # Thai
    if any('\u0e80' <= c <= '\u0eff' for c in text_str): return "#LO" # Lao
    if any('\u0f00' <= c <= '\u0fff' for c in text_str): return "#BO" # Tibetan
    if any('\u1000' <= c <= '\u109f' for c in text_str): return "#MY" # Burmese (Myanmar)
    if any('\u1200' <= c <= '\u137f' for c in text_str): return "#AM" # Amharic (Ethiopic)
    if any('\u1780' <= c <= '\u17ff' for c in text_str): return "#KM" # Khmer
    if any('\u10a0' <= c <= '\u10ff' for c in text_str): return "#KA" # Georgian
    if any('\u0530' <= c <= '\u058f' for c in text_str): return "#HY" # Armenian
    if any('\u0590' <= c <= '\u05ff' for c in text_str): return "#HE" # Hebrew
    if any('\u0370' <= c <= '\u03ff' for c in text_str): return "#EL" # Greek
    if any('\u0400' <= c <= '\u04ff' for c in text_str): return "#RU" # Russian / Ukrainian (Cyrillic)
    if any('\u4e00' <= c <= '\u9fff' for c in text_str): return "#ZH" # Chinese
    if any('\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff' for c in text_str): return "#JA" # Japanese
    if any('\uac00' <= c <= '\ud7af' for c in text_str): return "#KO" # Korean

    # ২. OTP Keyword দিয়ে ভাষা শনাক্তকরণ (Latin script languages)
    text_lower = text_str.lower()
    
    # Asian / Pacific
    if any(w in text_lower for w in ["kode verifikasi", "jangan bagikan", "rahasia"]): return "#ID" # Indonesian
    if any(w in text_lower for w in ["kod pengesahan", "jangan kongsi"]): return "#MS" # Malay
    if any(w in text_lower for w in ["mã của bạn", "không chia sẻ", "mã xác minh"]): return "#VN" # Vietnamese
    if any(w in text_lower for w in ["ang iyong code", "huwag ibahagi"]): return "#TL" # Tagalog / Filipino
    
    # European / Americas
    if any(w in text_lower for w in ["código", "tu código", "verificación", "no compartas"]): return "#ES" # Spanish
    if any(w in text_lower for w in ["seu código", "código de verificação", "não compartilhe"]): return "#PT" # Portuguese
    if any(w in text_lower for w in ["code secret", "ne partagez pas", "votre code"]): return "#FR" # French
    if any(w in text_lower for w in ["dein code", "bestätigungscode", "nicht teilen"]): return "#DE" # German
    if any(w in text_lower for w in ["il tuo codice", "codice di verifica", "non condividere"]): return "#IT" # Italian
    if any(w in text_lower for w in ["twój kod", "nie udostępniaj", "kod weryfikacyjny"]): return "#PL" # Polish
    if any(w in text_lower for w in ["doğrulama kodu", "paylaşmayın", "onay kodu"]): return "#TR" # Turkish
    if any(w in text_lower for w in ["jouw code", "verificatiecode", "niet delen"]): return "#NL" # Dutch
    if any(w in text_lower for w in ["din kod", "verifieringskod", "dela inte"]): return "#SV" # Swedish
    if any(w in text_lower for w in ["bekræftelseskode", "del ikke"]): return "#DA" # Danish
    if any(w in text_lower for w in ["bekreftelseskode", "ikke del"]): return "#NO" # Norwegian
    if any(w in text_lower for w in ["vahvistuskoodi", "älä jaa"]): return "#FI" # Finnish
    if any(w in text_lower for w in ["váš kód", "ověřovací kód", "nesdílejte"]): return "#CS" # Czech
    if any(w in text_lower for w in ["overovací kód", "nezdieľajte"]): return "#SK" # Slovak
    if any(w in text_lower for w in ["ellenőrző kód", "ne oszd meg"]): return "#HU" # Hungarian
    if any(w in text_lower for w in ["codul tău", "codul de verificare", "nu partaja"]): return "#RO" # Romanian
    if any(w in text_lower for w in ["kontrolni kod", "kod za potvrdu", "ne delite"]): return "#HR" # Croatian/Serbian
    if any(w in text_lower for w in ["код за потвърждение", "не споделяйте"]): return "#BG" # Bulgarian
    if any(w in text_lower for w in ["ваш код", "код підтвердження"]): return "#UK" # Ukrainian
    
    # African
    if any(w in text_lower for w in ["msimbo wako", "usishiriki"]): return "#SW" # Swahili
    if any(w in text_lower for w in ["verifikasiekode", "moenie deel nie"]): return "#AF" # Afrikaans
    
    # ৩. উপরের কোনোটি না মিললে ডিফল্ট
    return "#EN"

def parse_chat_id(text):
    text = text.strip()
    if text.startswith("-100") or (text.startswith("-") and text[1:].isdigit()):
        return text
    if "t.me/" in text:
        parts = text.split("/")
        username = parts[-1]
        if username: return "@" + username if not username.startswith("@") else username
    if text.startswith("@"):
        return text
    return "@" + text

def is_admin(user_id):
    return user_id in bot_settings["admins"] or user_id == OWNER_ID

def check_force_join(user_id):
    if not bot_settings["fj_on"] or not bot_settings["fj_channels"]: return True
    if is_admin(user_id): return True
    for ch in bot_settings["fj_channels"]:
        res = api_call("getChatMember", {"chat_id": ch, "user_id": user_id})
        if res.get("ok") and res["result"]["status"] not in ["left", "kicked"]: continue
        else: return False
    return True

def send_force_join_msg(chat_id):
    kb = []
    for ch in bot_settings["fj_channels"]:
        url = f"https://t.me/{ch.replace('@', '')}" if ch.startswith("@") else ch
        kb.append([{"text": f"Join Channel", "icon_custom_emoji_id": "5789428375261023681", "url": url, "style": "primary"}])
    kb.append([{"text": "Check Joined", "icon_custom_emoji_id": "5796182602176007916", "callback_data": "check_fj", "style": "success"}])
    send_message(chat_id, render_body_text(f"{PEM['warn']} <b>Please join our channels to use the bot!</b>"), reply_markup={"inline_keyboard": kb})

def is_user_banned(user_id):
    if is_admin(user_id): return False
    if user_id in user_banned_cache and time.time() - user_banned_cache[user_id]['time'] < 60:
        return user_banned_cache[user_id]['banned']
    banned = False
    if db:
        try:
            doc = db.collection('users').document(str(user_id)).get(timeout=5)
            banned = doc.exists and doc.to_dict().get("banned", False)
        except: pass
    user_banned_cache[user_id] = {'banned': banned, 'time': time.time()}
    return banned

# ==========================================
# Captcha Auto Login & Parsing Core
# ==========================================
def extract_otp_code(text):
    clean_text = re.sub(r'[\u200B-\u200D\uFEFF]', '', str(text))

    # 1. Multi-part OTPs
    multi_part = re.search(r'(\d{3}[-\s]+\d{3})|(\d{2}[-\s]+\d{2}[-\s]+\d{2})', clean_text)
    if multi_part:
        return multi_part.group(0).replace(" ", "")

    # 2. Keyword-based extraction (Updated for 4-15 digits)
    otp_keywords = ['code', 'is', 'otp', 'pin', 'verification', 'auth', 'কোড', 'رمز', 'your code']
    keywords_pattern = '|'.join(otp_keywords)
    keyword_match = re.search(rf'(?:{keywords_pattern})\s*(?:is|:|-|=)?\s*([a-z0-9]{{4,15}})', clean_text, re.I)
    if keyword_match and keyword_match.group(1).isdigit():
        return keyword_match.group(1)
        
    keyword_match_rev = re.search(rf'([a-z0-9]{{4,15}})\s*(?:is your|is the|কোড)', clean_text, re.I)
    if keyword_match_rev and keyword_match_rev.group(1).isdigit():
        return keyword_match_rev.group(1)

    # 3. Google OTP
    g_match = re.search(r'G-(\d{6})', clean_text, re.IGNORECASE)
    if g_match: return g_match.group(1)

    # 4. Digit sequences fallback (Updated for 4-15 digits)
    digit_matches = re.findall(r'(?<!\d)\d{4,15}(?!\d)', clean_text)
    if digit_matches: return digit_matches[0]

    return None

def parse_panel_response(response_text, p_config=None):
    results = []
    p_type = p_config.get("type", "API Panel") if p_config else "API Panel"
    
    n_col_name = p_config.get("num_col_name", "number").lower() if p_config else "number"
    m_col_name = p_config.get("msg_col_name", "message").lower() if p_config else "message"
    n_idx = int(p_config.get("num_col_idx", 1)) - 1 if p_config and p_config.get("num_col_idx") else 1
    m_idx = int(p_config.get("msg_col_idx", 2)) - 1 if p_config and p_config.get("msg_col_idx") else 2

    if p_type == "Auto Captcha Panel":
        try:
            soup = BeautifulSoup(response_text, 'html.parser')
            tables = soup.find_all('table')
            
            for table in tables:
                rows = table.find_all('tr')
                if not rows: continue
                
                # 🌟 Option 1 + Smart HTML Detection: কলামের নাম ও ব্যবহারকারীর দেওয়া সিরিয়াল দিয়ে সঠিক পজিশন বের করা
                final_n_idx = n_idx
                final_m_idx = m_idx
                
                # প্রথম রো (Header) চেক করে কলামের আসল সিরিয়াল মিলিয়ে নেওয়া
                header_cells = rows[0].find_all(['th', 'td'])
                for i, cell in enumerate(header_cells):
                    c_text = cell.get_text(strip=True).lower()
                    if n_col_name in c_text: final_n_idx = i
                    if m_col_name in c_text: final_m_idx = i

                for row in rows:
                    cols = row.find_all(['td', 'th'])
                    
                    # হেডার রো (যেখানে সব th থাকে) সেগুলো থেকে ডাটা নিবে না
                    if all(c.name == 'th' for c in cols): continue
                    
                    if len(cols) > max(final_n_idx, final_m_idx):
                        # HTML টেবিল থেকে টেক্সট বের করা
                        num_text = cols[final_n_idx].get_text(separator=" ", strip=True)
                        msg_text = cols[final_m_idx].get_text(separator=" ", strip=True)
                        
                        clean_num = re.sub(r'\D', '', num_text)
                        
                        # নাম্বারটা আসলেই ৫-১৮ ডিজিটের কিনা তা নিশ্চিত করা (যাতে উল্টাপাল্টা টেক্সট না আসে)
                        if clean_num and 5 <= len(clean_num) <= 18:
                            otp = extract_otp_code(msg_text)
                            if otp and len(msg_text) > 4:
                                results.append({"number": clean_num, "message": msg_text, "otp": otp})
        except Exception as e:
            pass
    else:
        try:
            data = json.loads(response_text)
            temp_results = []
            
            def process_item(item):
                pot_nums_list = []
                pot_msg = None
                values = []
                
                if isinstance(item, dict):
                    # ১. প্রথমে পরিচিত JSON Key (যেমন: num, phone, sms) দিয়ে খোঁজার চেষ্টা
                    lower_keys = {str(k).lower(): v for k, v in item.items()}
                    for k in ["number", "num", "phone", "msisdn", "sender"]:
                        if k in lower_keys:
                            clean_val = re.sub(r'\D', '', str(lower_keys[k]))
                            if 5 <= len(clean_val) <= 18:
                                if clean_val not in pot_nums_list: pot_nums_list.append(clean_val)
                    for k in ["message", "msg", "sms", "content", "text"]:
                        if k in lower_keys:
                            val = str(lower_keys[k])
                            if len(val) > 4:
                                pot_msg = val
                                break
                    values = list(item.values())
                elif isinstance(item, list):
                    values = item

                # ২. যদি Key দিয়ে না পাওয়া যায়, তবে Smart Blind Scan (সব ভ্যালু চেক করবে)
                for v in values:
                    if isinstance(v, (dict, list)) or v is None: continue
                    v_str = str(v).strip()
                    
                    # Number Detection: 7 থেকে 18 ডিজিট
                    clean_v = re.sub(r'\D', '', v_str)
                    if 7 <= len(clean_v) <= 18 and not re.search(r'[a-zA-Z]', v_str):
                        # Date/Time/IP এড়ানোর লজিক
                        if not re.search(r'\d{4}[-/]\d{2}[-/]\d{2}', v_str) and not re.search(r'\d{2}:\d{2}:\d{2}', v_str) and "." not in v_str:
                            if clean_v not in pot_nums_list:
                                pot_nums_list.append(clean_v)
                    
                    # Message Detection: 5 অক্ষরের বেশি এবং শুধু সংখ্যা নয়
                    if len(v_str) > 4 and not v_str.isdigit():
                        if extract_otp_code(v_str):
                            if pot_msg is None or len(v_str) > len(pot_msg):
                                pot_msg = v_str
                                
                # 🌟 ৩. Multiple Numbers Logic (User Priority > Second Number > First Number)
                pot_num = None
                if pot_nums_list:
                    matched_user_num = None
                    for n in pot_nums_list:
                        # চেক করবে ইউজারের অ্যাসাইন করা নাম্বারের তালিকায় এই নাম্বারটি আছে কি না
                        if n in stex_assigned_numbers or any(n in str(key) for key in stex_assigned_numbers.keys()):
                            matched_user_num = n
                            break
                    
                    if matched_user_num:
                        pot_num = matched_user_num
                    elif len(pot_nums_list) >= 2:
                        pot_num = pot_nums_list[1] # ইউজারের কাছে না থাকলে সরাসরি দ্বিতীয় নাম্বারটি নেবে
                    else:
                        pot_num = pot_nums_list[0]
                            
                if pot_num and pot_msg:
                    otp = extract_otp_code(pot_msg)
                    if otp:
                        temp_results.append({"number": pot_num, "message": pot_msg, "otp": otp})
                        
            def traverse_json(node):
                if isinstance(node, list):
                    if len(node) > 0 and not isinstance(node[0], (dict, list)):
                        # It's a flat list representing one record
                        process_item(node)
                    for child in node:
                        if isinstance(child, (dict, list)):
                            traverse_json(child)
                elif isinstance(node, dict):
                    process_item(node)
                    for val in node.values():
                        if isinstance(val, (dict, list)):
                            traverse_json(val)

            traverse_json(data)
            
            # Remove duplicates
            seen = set()
            for r in temp_results:
                uid = f"{r['number']}_{r['otp']}"
                if uid not in seen:
                    seen.add(uid)
                    results.append(r)
        except: pass
        
    return results

# 🌟 Advanced Automated Background Captcha Solver 🌟
def attempt_auto_login(p, idx):
    login_url = p.get("login_url", "").strip()
    if not login_url.startswith("http"):
        login_url = "http://" + login_url
        
    if not login_url.lower().endswith('/login') and not login_url.lower().endswith('.php'):
        login_url = f"{login_url.rstrip('/')}/login"
        
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    })
    
    try:
        res = session.get(login_url, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        all_text = res.text
        
        # 1. SOLVE CAPTCHA (Exact bot 3.py logic)
        captcha_match = re.search(r'(\d+\s*[\+\-\*]\s*\d+)\s*[=\?:]', all_text)
        if not captcha_match:
            captcha_match = re.search(r'what is\s*(\d+\s*[\+\-\*]\s*\d+)', all_text, re.I)
        if not captcha_match:
            elements = soup.find_all(["label", "div", "span", "p", "strong"])
            for el in elements:
                txt = el.get_text(separator=" ", strip=True)
                if any(op in txt for op in ["+", "-", "*"]):
                    m = re.search(r'(\d+\s*[\+\-\*]\s*\d+)', txt)
                    if m:
                        captcha_match = m
                        break
                        
        captcha_text = captcha_match.group(1) if captcha_match else "0 + 0"
        answer = "0"
        m2 = re.search(r'(\d+)\s*([\+\-\*])\s*(\d+)', captcha_text)
        if m2:
            a, op, b = int(m2.group(1)), m2.group(2), int(m2.group(3))
            if op == '+': answer = str(a + b)
            elif op == '-': answer = str(a - b)
            elif op == '*': answer = str(a * b)

        # 2. FIND FORM
        form = soup.find("form")
        if not form:
            p["login_status"] = "❌ No login form found"
            return False
            
        action = form.get("action")
        from urllib.parse import urljoin
        post_url = urljoin(login_url, action) if action else login_url

        form_data = {}
        for hidden in form.find_all("input", type="hidden"):
            name = hidden.get("name")
            if name: form_data[name] = hidden.get("value") or ""
        
        user_input = form.find("input", {"name": re.compile(r"user|email|id", re.I)}) or \
                     form.find("input", {"type": "text", "placeholder": re.compile(r"user|email", re.I)}) or \
                     form.find("input", {"type": "text"})
                     
        pass_input = form.find("input", {"name": re.compile(r"pass", re.I)}) or \
                     form.find("input", {"type": "password"})
                     
        captcha_input = form.find("input", {"placeholder": re.compile(r"answer|ans|code|verification|value|captcha", re.I)}) or \
                        form.find("input", {"name": re.compile(r"ans|captcha|ver|code", re.I)})
        
        user_field = user_input.get("name") if user_input else "username"
        pass_field = pass_input.get("name") if pass_input else "password"
        captcha_field = captcha_input.get("name") if captcha_input else "answer"

        form_data[user_field] = p.get("username", "")
        form_data[pass_field] = p.get("password", "")
        if captcha_field:
            form_data[captcha_field] = answer

        # 3. SUBMIT
        login_req = session.post(post_url, data=form_data, allow_redirects=True, timeout=15)
        
        # 4. VERIFY (Exact bot 3.py check logic)
        msg_link = p.get("msg_link", "").strip()
        if not msg_link.startswith("http") and msg_link != "":
            msg_link = "http://" + msg_link
            
        check_url = msg_link if msg_link else f"{login_url.split('/login')[0]}/client/SMSCDRStats"
        
        check_res = session.get(check_url, timeout=10)
        
        if 'logout' in login_req.text.lower() or 'logout' in check_res.text.lower() or 'sms reports' in check_res.text.lower() or 'dashboard' in check_res.text.lower() or 'cdrs' in check_res.text.lower():
            panel_sessions[idx] = session
            p["login_status"] = "✅ Active & Fetching"
            return True
        else:
            # এখানে ফেইল হলে অংক কী পেয়েছিল তা দেখা যাবে
            p["login_status"] = f"❌ Login Failed (Math: {captcha_text} = {answer})"
            return False
            
    except Exception as e:
        p["login_status"] = f"❌ Error: {str(e)[:20]}"
        
    return False

def panel_monitor_thread():
    global processed_otps, recent_traffic, panel_sessions
    while True:
        try:
            for idx, p in enumerate(bot_settings.get("panels", [])):
                if p.get("status") == "ON":
                    
                    if p.get("type") == "Auto Captcha Panel":
                        sess = panel_sessions.get(idx)
                        
                        if not sess:
                            now = time.time()
                            if now - p.get("last_login_attempt", 0) < 30: 
                                continue 
                            p["last_login_attempt"] = now
                            
                            success = attempt_auto_login(p, idx)
                            save_db() # Save login status text to show in settings
                            if not success:
                                continue 
                            sess = panel_sessions.get(idx)
                            
                        try:
                            # 🌟 auto sessions with sAjaxSource and Fallback HTML Parser
                            parsed_data, res_text = fetch_cpt_panel_cdrs(p, sess, p["msg_link"])
                            p["login_status"] = "✅ Active & Fetching"
                        except Exception as e:
                            p["login_status"] = "❌ Session Expired (Retrying...)"
                            del panel_sessions[idx]
                            save_db()
                            continue

                    elif p.get("api_url") or p.get("full_api_url"): 
                        full_url = p.get("full_api_url", "").strip()
                        url = p.get("api_url", "").strip()
                        token = p.get("token", "").strip()
                        if not full_url and not url: continue
                        
                        urls_to_try = []
                        if full_url:
                            urls_to_try.append(full_url)
                        else:
                            if "{token}" in url or "{key}" in url:
                                urls_to_try.append(url.replace("{token}", token).replace("{key}", token))
                            elif "token=" in url or "key=" in url:
                                urls_to_try.append(url)
                            else:
                                sep = '&' if '?' in url else '?'
                                urls_to_try.append(f"{url}{sep}token={token}")
                                urls_to_try.append(f"{url}{sep}key={token}&start=0")
                                urls_to_try.append(f"{url}{sep}key={token}")
                            
                        parsed_data = []
                        # 🌟 Browser Bypass (403 Forbidden Fix)
                        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
                        for try_url in urls_to_try:
                            try:
                                res = requests.get(try_url, headers=headers, timeout=10)
                                parsed_data = parse_panel_response(res.text, p)
                                if parsed_data:
                                    if not full_url and try_url != url and token:
                                        p["api_url"] = try_url.replace(token, "{token}")
                                        save_db()
                                    break
                            except: continue
                        if not parsed_data: continue
                    else:
                        continue
                    
                    if p.get("type") != "Auto Captcha Panel":
                        limit = p.get("records", 0)
                        if limit > 0: parsed_data = parsed_data[:limit]
                        
                    for item in parsed_data:
                        num = item["number"]
                        otp = item["otp"]
                        msg_text = item["message"]
                        unique_id = f"{num}_{otp}"
                        
                        if unique_id not in processed_otps:
                            processed_otps.add(unique_id)
                            if len(processed_otps) > 15000: 
                                for _ in range(5000): processed_otps.pop()
                                 
                            char, iso = get_flag_and_code(num)
                            actual_srv = get_real_service(num, p.get("name", "Panel"))
                            app_full_name, prem_app_html = get_service_info_html(actual_srv, msg_text)
                            current_time = time.time()
                            
                            recent_traffic = [t for t in recent_traffic if current_time - t.get("time", 0) <= 3600]
                            recent_traffic.append({
                                "service": app_full_name,
                                "iso": iso,
                                "flag": char,
                                "number": num,
                                "time": current_time
                            })
                            # 🌟 শুধু লোকাল ফাইলে সেভ করবে, Firestore এ অহেতুক Write করবে না!
                            save_local_db()
                                 
                            display_num = f"+{num}" if not str(num).startswith("+") else str(num)
                            masked = mask_number(display_num)
                            lang = detect_language(msg_text)
                            
                            lang_name = LANG_MAP.get(lang, "English") if 'LANG_MAP' in globals() else lang.replace("#", "")
                            display_msg = get_group_otp_msg(num, app_full_name, prem_app_html, iso, lang_name, otp)
                            
                            for fw in bot_settings["fw_groups"]:
                                kb = []
                                temp_row = []
                                for btn in fw.get("buttons", []):
                                    b_obj = {"text": btn["text"], "url": btn["url"], "style": "primary"}
                                    if "icon_custom_emoji_id" in btn: b_obj["icon_custom_emoji_id"] = btn["icon_custom_emoji_id"]
                                    temp_row.append(b_obj)
                                    if len(temp_row) == 2:
                                        kb.append(temp_row)
                                        temp_row = []
                                if temp_row: kb.append(temp_row)
                                send_message(fw["chat_id"], display_msg, reply_markup={"inline_keyboard": kb})
                            
                            owner_id = get_real_owner(num)
                            if owner_id:
                                inbox_msg = get_custom_inbox_msg(num, msg_text, actual_srv, otp, owner_id)
                                inbox_kb = []
                                
                                # রিওয়ার্ড যোগ করার লজিক
                                reward = float(bot_settings.get("otp_reward", 0.0))
                                raw_num_clean = str(num).replace("+", "").strip() if 'num' in locals() else str(num_str).replace("+", "").strip()
                                if 'assigned_number_metadata' in globals() and raw_num_clean in assigned_number_metadata:
                                    reward = float(assigned_number_metadata[raw_num_clean].get("reward", reward))
                                
                                if reward > 0:
                                    credit_otp_reward(owner_id, reward)
                                    inbox_kb.append([{"text": f"Added {reward} tk", "icon_custom_emoji_id": "6169972515025199096", "callback_data": "ignore", "style": "primary"}])
                                
                                send_message(owner_id, inbox_msg, reply_markup={"inline_keyboard": inbox_kb})
                                
                                if owner_id in user_cache:
                                    user_cache[owner_id]["total_otps"] = user_cache[owner_id].get("total_otps", 0) + 1
                                if db:
                                    try: db.collection('users').document(str(owner_id)).update({"total_otps": firestore.Increment(1), "daily_otps": firestore.Increment(1)}); _record_otp_day(owner_id)
                                    except: pass
        except Exception as e:
            pass
        time.sleep(5) 

# ==========================================
# Firebase User Management
# ==========================================
# 🌟 Local User Cache: বারবার Firestore থেকে Read করা বন্ধ করবে!
user_cache = {}

def get_user(user_id):
    if user_id in user_cache: return user_cache[user_id]
    if not db: return {"user_id": user_id, "balance": 0.0, "total_refers": 0, "total_otps": 0}
    
    doc_ref = db.collection('users').document(str(user_id))
    doc = doc_ref.get()
    if doc.exists: 
        data = doc.to_dict()
        if "total_otps" not in data: data["total_otps"] = 0
        if "banned" not in data: data["banned"] = False
        if "verified" not in data: data["verified"] = False
        if "joined" not in data:
            try: data["joined"] = doc.create_time.strftime("%Y-%m-%d")
            except Exception: data["joined"] = datetime.now().strftime("%Y-%m-%d")
        user_cache[user_id] = data
        return data
    else:
        new_user = {"user_id": user_id, "balance": 0.0, "total_refers": 0, "total_otps": 0, "banned": False, "verified": False, "joined": datetime.now().strftime("%Y-%m-%d")}
        doc_ref.set(new_user)
        user_cache[user_id] = new_user
        return new_user

def update_balance(user_id, amount):
    if user_id in user_cache:
        user_cache[user_id]["balance"] = user_cache[user_id].get("balance", 0.0) + float(amount)
    if not db: return
    try:
        doc_ref = db.collection('users').document(str(user_id))
        # 🌟 No more .get() before update! Saves 1 Read Cost instantly!
        doc_ref.set({"user_id": user_id, "balance": firestore.Increment(float(amount))}, merge=True)
    except: pass

def add_referral(inviter_id, new_user_id):
    if not db.collection('users').document(str(new_user_id)).get().exists:
        get_user(new_user_id) 
        reward = bot_settings.get("refer_reward", 0.2)
        update_balance(inviter_id, reward)
        db.collection('users').document(str(inviter_id)).update({"total_refers": firestore.Increment(1)})
        
        # আপনার দেওয়া নতুন ডিজাইন
        ref_msg = (
            f"{PEM['gift']} <b>New Referral !</b>\n"
            f"------------------\n"
            f"🔥 <b>You Received {reward} TK</b>\n"
            f"------------------\n"
            f"{PEM['user']} <b>From User ID:</b> <code>{new_user_id}</code>"
        )
        send_message(inviter_id, render_body_text(ref_msg))

# ==========================================
# UI Keyboards & Menu Builders
# ==========================================
def get_cancel_kb():
    return {"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "cancel_state", "style": "danger"}]]}

def _old_main_menu(user_id):
    kb = [
        [
            {"text": "GET NUMBER", "icon_custom_emoji_id": "5237988788164107500", "style": "primary"}, 
            {"text": "2FA ONLINE", "icon_custom_emoji_id": "5296369303661067030", "style": "primary"}
        ],
        [
            {"text": "Refer", "icon_custom_emoji_id": "5461151367559141950", "style": "success"}, 
            {"text": "WITHDRAWAL", "icon_custom_emoji_id": "5409048419211682843", "style": "danger"}
        ],
        [
            {"text": "SUPPORT", "icon_custom_emoji_id": "5354967874647644417", "style": "primary"},
            {"text": "WS CHECKER", "style": "success"}
        ]
    ]
    if is_admin(user_id): 
        kb.append([{"text": "Admin Panel", "icon_custom_emoji_id": "5341715473882955310", "style": "danger"}])
    return {"keyboard": kb, "resize_keyboard": True}

def get_admin_text():
    users_count = len(all_known_users) # 🌟 Zero Cost User Count!
    total_files = len(number_batches)
    available_nums = sum(len(b["numbers"]) for b in number_batches.values())

    txt = f"""
{PEM['admin']} <b>ADMIN CONTROL PANEL</b> {PEM['admin']}
━━━━━━━━━━━━━━━━━━

{PEM['graph']} <b>DATABASE OVERVIEW</b>
— — — — — — — — — —
{PEM['user']} Users      » {users_count}
{PEM['file']} Files      » {total_files}
{PEM['num']} Numbers    » {total_uploaded_stats}
{PEM['ok']} Assigned   » {total_assigned_stats}
{PEM['rocket']} Available  » {available_nums}

{PEM['graph']} <b>STOCK LEVEL</b>
— — — — — — — — — —
[██████░░░░░░░░░] {available_nums} free
"""
    return render_body_text(txt)

def admin_panel_keyboard():
    return {"inline_keyboard": [
        [{"text": "LEADER BOARD SYSTEM", "icon_custom_emoji_id": "5353032893096567467", "callback_data": "lb_main", "style": "success"}],
        [{"text": "Upload Number", "icon_custom_emoji_id": "6170029178528735972", "callback_data": "upload_num", "style": "primary"},
         {"text": "Delete files", "icon_custom_emoji_id": "5422557736330106570", "callback_data": "delete_files", "style": "danger"}],
        [{"text": "Broadcast", "icon_custom_emoji_id": "5789428375261023681", "callback_data": "broadcast_msg", "style": "success"},
         {"text": "System", "icon_custom_emoji_id": "5420155432272438703", "callback_data": "system_settings", "style": "primary"}],
        [{"text": "Used number", "icon_custom_emoji_id": "5352694861990501856", "callback_data": "show_used", "style": "success"},
         {"text": "Unused number", "icon_custom_emoji_id": "5352597830089347330", "callback_data": "show_unused", "style": "success"}],
        [{"text": f"WS CHECKER: {'ON' if bot_settings.get('ws_on', True) else 'OFF'}", "callback_data": "toggle_ws", "style": ("success" if bot_settings.get('ws_on', True) else "danger")}],
        [{"text": "Button Color", "callback_data": "btn_color_menu", "style": "primary"}],
        [{"text": "Close", "icon_custom_emoji_id": "5420130255174145507", "callback_data": "close_msg", "style": "danger"}]
    ]}

def system_settings_keyboard():
    return {"inline_keyboard": [
        [{"text": "StexSMS Control", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "stex_control", "style": "success"},
         {"text": "Voltx Control", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "voltx_control", "style": "primary"}],
        [{"text": "Zenex Control", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "zenex_control", "style": "success"},
         {"text": "Zebra Control", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "zebra_control", "style": "primary"}],
        [{"text": "ScarFace Control", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "scarface_control", "style": "danger"}],
        [{"text": "Force Join System", "icon_custom_emoji_id": "5420517437885943844", "callback_data": "manage_fj", "style": "primary"},
         {"text": "Admin Management", "icon_custom_emoji_id": "5420145051336485498", "callback_data": "manage_admins", "style": "danger"}],
        [{"text": "OTP Group", "icon_custom_emoji_id": "5190447043545438788", "callback_data": "manage_otp_groups", "style": "danger"},
         {"text": "User Management", "icon_custom_emoji_id": "5193063022226086560", "callback_data": "user_management", "style": "primary"}], 
        [{"text": "Panel MANAGEMENT", "icon_custom_emoji_id": "5336879280578138635", "callback_data": "manage_panels", "style": "danger"},
         {"text": "Subscription", "icon_custom_emoji_id": "5190899075968441286", "callback_data": "dummy_alert", "style": "success"}],
        [{"text": "TRE Control", "icon_custom_emoji_id": "6143315237861860497", "callback_data": "tre_control", "style": "primary"},
         {"text": "Premium Emoji", "icon_custom_emoji_id": "5352552689983067014", "callback_data": "manage_emojis", "style": "success"}],
        [{"text": "Menu Design", "icon_custom_emoji_id": "5190751148704833975", "callback_data": "menu_design_list", "style": "primary"},
         {"text": "Test", "icon_custom_emoji_id": "5190781475468915802", "callback_data": "test_message_flow", "style": "primary"}], 
        [{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "back_to_admin", "style": "danger"}]
    ]}

def get_user_management_text():
    # 🌟 Fast & Free User Management Stats!
    total = len(all_known_users)
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    txt = f"""➖➖➖➖➖➖➖➖
《 👋 USER VIEW 》
➖➖➖➖➖➖➖➖
📊 LIVE STATISTICS:
➖➖➖➖➖➖➖➖
🫂 TOTAL USERS: {total}
✅ VERIFIED USERS: (Hidden to save DB Cost)
🚫 BANNED USERS: (Hidden to save DB Cost)
➖➖➖➖➖➖➖➖
⌛ UPDATED: {now_str}"""
    return render_body_text(txt)

def user_management_keyboard():
    return {"inline_keyboard": [
        [{"text": "Manage Balance", "icon_custom_emoji_id": "5190576863226933563", "callback_data": "um_manage_balance", "style": "primary"},
         {"text": "Ban/Unban User", "icon_custom_emoji_id": "5334807341109908955", "callback_data": "um_ban_unban", "style": "danger"}],
        [{"text": "User Profile", "icon_custom_emoji_id": "5352861489541714456", "callback_data": "um_user_profile", "style": "success"}],
        [{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "primary"}]
    ]}

def menu_design_list_keyboard():
    return {"inline_keyboard": [
        [{"text": "Edit /start Menu", "icon_custom_emoji_id": "5395444784611480792", "callback_data": "md_edit_start", "style": "primary"}],
        [{"text": "Edit GET NUMBER", "icon_custom_emoji_id": "5337132498965010628", "callback_data": "md_edit_get_number", "style": "success"}],
        [{"text": "Edit Select Country", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "md_edit_select_country", "style": "primary"}],
        [{"text": "Edit Refer", "icon_custom_emoji_id": "5420396762189831222", "callback_data": "md_edit_refer", "style": "primary"}],
        [{"text": "Edit WITHDRAWAL", "icon_custom_emoji_id": "5352585194295564660", "callback_data": "md_edit_withdrawal", "style": "danger"},
         {"text": "Edit SUPPORT", "icon_custom_emoji_id": "5420145051336485498", "callback_data": "md_edit_support", "style": "danger"}],
        [{"text": "Reset Defaults", "icon_custom_emoji_id": "5192812028632274956", "callback_data": "md_reset_defaults", "style": "success"}],
        [{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "primary"}]
    ]}

def menu_edit_options_keyboard(menu_key):
    return {"inline_keyboard": [
        [{"text": "Edit Body (Text)", "icon_custom_emoji_id": "5395444784611480792", "callback_data": f"md_text_{menu_key}", "style": "primary"}],
        [{"text": "Edit Inline Buttons", "icon_custom_emoji_id": "5420155432272438703", "callback_data": f"md_btns_{menu_key}", "style": "success"}],
        [{"text": "Back to Menus", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "menu_design_list", "style": "danger"}]
    ]}

def menu_buttons_list_keyboard(menu_key):
    kb = []
    btns = bot_settings["custom_messages"].get(menu_key, {}).get("buttons", [])
    for idx, btn in enumerate(btns):
        kb.append([{"text": f"Del: {btn['text']}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"md_delbtn_{menu_key}_{idx}", "style": "danger"}])
    kb.append([{"text": "Add Inline Button", "icon_custom_emoji_id": "5420323438508155202", "callback_data": f"md_addbtn_{menu_key}", "style": "success"}])
    kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"md_edit_{menu_key}", "style": "primary"}])
    return {"inline_keyboard": kb}

def emoji_settings_keyboard():
    return {"inline_keyboard": [
        [{"text": "Upload Flags (TXT)", "icon_custom_emoji_id": "5353001161878182134", "callback_data": "up_flags_txt", "style": "primary"},
         {"text": "Download Flags", "icon_custom_emoji_id": "5257969839313526622", "callback_data": "dl_flags_txt", "style": "success"}],
        [{"text": "Upload Services (TXT)", "icon_custom_emoji_id": "5353001161878182134", "callback_data": "up_apps_txt", "style": "primary"},
         {"text": "Download Services", "icon_custom_emoji_id": "5257969839313526622", "callback_data": "dl_apps_txt", "style": "success"}],
        [{"text": "Delete All Flags", "icon_custom_emoji_id": "5422557736330106570", "callback_data": "del_all_flags", "style": "danger"},
         {"text": "Add Single Emoji", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_single_emoji", "style": "success"}],
        [{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "danger"}]
    ]}

def fj_settings_keyboard():
    status_text = 'ON' if bot_settings['fj_on'] else 'OFF'
    status_icon = "5352694861990501856" if bot_settings['fj_on'] else "5318840353510408444"
    kb = [[{"text": f"STATUS: {status_text}", "icon_custom_emoji_id": status_icon, "callback_data": "toggle_fj", "style": "primary"}]]
    for idx, ch in enumerate(bot_settings["fj_channels"]):
        kb.append([{"text": f"Delete: {ch}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"del_fj_{idx}", "style": "danger"}])
    kb.append([{"text": "Add Channel", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_fj", "style": "success"}])
    kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "primary"}])
    return {"inline_keyboard": kb}

def admin_settings_keyboard():
    kb = []
    for idx, adm in enumerate(bot_settings["admins"]):
        text_btn = f"Owner: {adm}" if adm == OWNER_ID else f"Delete: {adm}"
        icon_id = "5353032893096567467" if adm == OWNER_ID else "5420130255174145507"
        cb_data = "ignore" if adm == OWNER_ID else f"del_adm_{idx}"
        kb.append([{"text": text_btn, "icon_custom_emoji_id": icon_id, "callback_data": cb_data, "style": "danger" if adm != OWNER_ID else "primary"}])
    kb.append([{"text": "Add Admin", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_adm", "style": "success"}])
    kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "primary"}])
    return {"inline_keyboard": kb}

def otp_groups_list_keyboard():
    kb = [[{"text": "Edit OTP Button Link", "icon_custom_emoji_id": "5420517437885943844", "callback_data": "edit_otp_link", "style": "primary"}]]
    for idx, fg in enumerate(bot_settings["fw_groups"]):
        kb.append([{"text": f"Group: {fg['chat_id']}", "icon_custom_emoji_id": "5193063022226086560", "callback_data": f"manage_fw_{idx}", "style": "primary"}])
    kb.append([{"text": "Add Forward Group", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_fw", "style": "success"}])
    kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "danger"}])
    return {"inline_keyboard": kb}

def stex_control_keyboard():
    return {"inline_keyboard": [
        [{"text": "Add StexSMS Key", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_stex_key", "style": "success"},
         {"text": "View/Del Keys", "icon_custom_emoji_id": "5422557736330106570", "callback_data": "view_stex_keys", "style": "danger"}],
        [{"text": "Manage StexSMS Services", "icon_custom_emoji_id": "5192739271886282680", "callback_data": "manage_stex_srv", "style": "success"}],
        [{"text": "Search Country", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "stex_search_country", "style": "primary"}],
        [{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "primary"}]
    ]}

def voltx_control_keyboard():
    return {"inline_keyboard": [
        [{"text": "Add Voltx Key", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_voltx_key", "style": "success"},
         {"text": "View/Del Keys", "icon_custom_emoji_id": "5422557736330106570", "callback_data": "view_voltx_keys", "style": "danger"}],
        [{"text": "Manage Voltx Services", "icon_custom_emoji_id": "5192739271886282680", "callback_data": "manage_voltx_srv", "style": "success"}],
        [{"text": "Search Country", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "voltx_search_country", "style": "primary"}],
        [{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "primary"}]
    ]}

def zenex_control_keyboard():
    return {"inline_keyboard": [
        [{"text": "Add Zenex Key", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_pnl_key_zenex", "style": "success"},
         {"text": "View/Del Keys", "icon_custom_emoji_id": "5422557736330106570", "callback_data": "view_keys_zenex", "style": "danger"}],
        [{"text": "Manage Services", "icon_custom_emoji_id": "5192739271886282680", "callback_data": "manage_srv_zenex", "style": "success"}],
        [{"text": "Search Country", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "manage_sc_zenex", "style": "primary"}],
        [{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "primary"}]]}

def zebra_control_keyboard():
    return {"inline_keyboard": [
        [{"text": "Add Zebra Key", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_pnl_key_zebra", "style": "success"},
         {"text": "View/Del Keys", "icon_custom_emoji_id": "5422557736330106570", "callback_data": "view_keys_zebra", "style": "danger"}],
        [{"text": "Manage Services", "icon_custom_emoji_id": "5192739271886282680", "callback_data": "manage_srv_zebra", "style": "success"}],
        [{"text": "Search Country", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "manage_sc_zebra", "style": "primary"}],
        [{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "primary"}]]}

def scarface_control_keyboard():
    return {"inline_keyboard": [
        [{"text": "Add ScarFace Key", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_pnl_key_scarface", "style": "success"},
         {"text": "View/Del Keys", "icon_custom_emoji_id": "5422557736330106570", "callback_data": "view_keys_scarface", "style": "danger"}],
        [{"text": "Manage Services", "icon_custom_emoji_id": "5192739271886282680", "callback_data": "manage_srv_scarface", "style": "success"}],
        [{"text": "Search Country", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "manage_sc_scarface", "style": "primary"}],
        [{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "primary"}]]}

def specific_fw_group_keyboard(idx):
    group = bot_settings["fw_groups"][idx]
    kb = []
    for b_idx, btn in enumerate(group.get("buttons", [])):
        kb.append([{"text": f"Del: {btn['text']}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"del_fwbtn_{idx}_{b_idx}", "style": "danger"}])
    
    kb.append([{"text": "Add Inline Button", "icon_custom_emoji_id": "5420323438508155202", "callback_data": f"add_fwbtn_{idx}", "style": "success"}])
    kb.append([{"text": "Delete Entire Group", "icon_custom_emoji_id": "5422557736330106570", "callback_data": f"del_fw_{idx}", "style": "danger"}])
    kb.append([{"text": "Back to Groups", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_otp_groups", "style": "primary"}])
    return {"inline_keyboard": kb}

def tre_control_keyboard():
    w_status = "ON" if bot_settings["withdraw_on"] else "OFF"
    sup_status = "ON" if bot_settings.get("support_link") else "OFF"
    grp_status = "ON" if bot_settings.get("w_group") else "OFF"
    return {"inline_keyboard": [
        [{"text": f"WITHDRAW: {w_status}", "icon_custom_emoji_id": "5348469219761626211", "callback_data": "tre_toggle_w", "style": "primary"}],
        [{"text": f"MIN WITHDRAW: {bot_settings['min_withdraw']}", "icon_custom_emoji_id": "5352877703043258544", "callback_data": "tre_min_w", "style": "success"},
         {"text": f"OTP REWARD: {bot_settings['otp_reward']}", "icon_custom_emoji_id": "5190576863226933563", "callback_data": "tre_otp_r", "style": "primary"}],
        [{"text": f"REFER REWARD: {bot_settings['refer_reward']}", "icon_custom_emoji_id": "5420396762189831222", "callback_data": "tre_ref_r", "style": "success"},
         {"text": f"COOLDOWN: {bot_settings['cooldown']}s", "icon_custom_emoji_id": "5337172996211648018", "callback_data": "tre_cool", "style": "primary"}],
        [{"text": f"NUM/REQ: {bot_settings['num_req']}", "icon_custom_emoji_id": "5337132498965010628", "callback_data": "tre_num_req", "style": "success"},
         {"text": f"NUM/SHARE: {bot_settings['num_share']}", "icon_custom_emoji_id": "5352862640592949843", "callback_data": "tre_num_share", "style": "primary"}],
        [{"text": f"SUPPORT LINK: {sup_status}", "icon_custom_emoji_id": "5420145051336485498", "callback_data": "tre_sup_link", "style": "success"},
         {"text": "W. METHODS", "icon_custom_emoji_id": "5190899075968441286", "callback_data": "manage_w_methods", "style": "primary"}],
        [{"text": f"W. GROUP: {grp_status}", "icon_custom_emoji_id": "5420517437885943844", "callback_data": "tre_w_group", "style": "success"},
         {"text": "BACK", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "danger"}]
    ]}

def get_w_method_emoji(method_name):
    m = str(method_name).lower()
    if "bkash" in m: return "6170361909645156512"
    if "nagad" in m or "nogod" in m: return "6170034920900011245"
    if "rocket" in m: return "6170182216803426786"
    if "binance" in m or "baiance" in m: return "6170396960873260483"
    return "5190899075968441286" # Default card emoji

def w_methods_keyboard():
    kb = []
    for idx, m in enumerate(bot_settings["w_methods"]):
        emoji_id = get_w_method_emoji(m)
        kb.append([{"text": f"Delete: {m}", "icon_custom_emoji_id": emoji_id, "callback_data": f"del_wm_{idx}", "style": "danger"}])
    kb.append([{"text": "Add Method", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_wm", "style": "success"}])
    kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "tre_control", "style": "primary"}])
    return {"inline_keyboard": kb}

def typed_panels_list_keyboard(p_type):
    kb = []
    for idx, p in enumerate(bot_settings["panels"]):
        if p.get("type", "API Panel") != p_type: continue
        action_text = f"Turn OFF {p['name']}" if p['status'] == 'ON' else f"Turn ON {p['name']}"
        action_icon = "5318840353510408444" if p['status'] == 'ON' else "5192812028632274956"
        icon_id = "5420155432272438703" 
        kb.append([
            {"text": action_text, "icon_custom_emoji_id": action_icon, "callback_data": f"tog_pnl_{idx}", "style": "danger" if p['status'] == 'ON' else "success"},
            {"text": f"{p['name']}", "icon_custom_emoji_id": icon_id, "callback_data": f"conf_pnl_{idx}", "style": "primary"}
        ])
    add_cb = "add_api_panel" if p_type == "API Panel" else "add_cpt_panel"
    kb.append([{"text": "Add New Provider", "icon_custom_emoji_id": "5420323438508155202", "callback_data": add_cb, "style": "success"}])
    kb.append([{"text": "Delete Provider", "icon_custom_emoji_id": "5336944168944047463", "callback_data": f"list_del_{'api' if p_type=='API Panel' else 'cpt'}", "style": "danger"}])
    kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_panels", "style": "primary"}])
    return {"inline_keyboard": kb}

def panel_config_keyboard(idx):
    p = bot_settings["panels"][idx]
    
    kb = []
    action_text = "Turn OFF" if p['status'] == 'ON' else "Turn ON"
    action_icon = "5318840353510408444" if p['status'] == 'ON' else "5192812028632274956"
    kb.append([{"text": action_text, "icon_custom_emoji_id": action_icon, "callback_data": f"tog_pnl_{idx}", "style": "danger" if p['status'] == 'ON' else "success"}])
    
    if p["type"] != "Auto Captcha Panel":
        rec_count_text = "All (Unlimited)" if p.get('records', 0) == 0 else str(p.get('records'))
        kb.append([{"text": "Set API URL", "icon_custom_emoji_id": "5420517437885943844", "callback_data": f"set_p_api_{idx}", "style": "primary"}])
        kb.append([{"text": "Set Token", "icon_custom_emoji_id": "5353022963132174959", "callback_data": f"set_p_tok_{idx}", "style": "primary"}])
        kb.append([{"text": "🌐 Full API (URL+Token)", "icon_custom_emoji_id": "5420517437885943844", "callback_data": f"set_p_fapi_{idx}", "style": "primary"}])
        kb.append([{"text": f"Set Records Count: {rec_count_text}", "icon_custom_emoji_id": "5192739271886282680", "callback_data": f"set_p_rec_{idx}", "style": "primary"}])
        
    kb.append([{"text": "Test Connection", "icon_custom_emoji_id": "5352694861990501856", "callback_data": f"test_p_conn_{idx}", "style": "success"}])
        
    back_data = "manage_api_panels" if p.get("type", "API Panel") == "API Panel" else "manage_cpt_panels"
    kb.append([{"text": "Back to Providers", "icon_custom_emoji_id": "5267490665117275176", "callback_data": back_data, "style": "danger"}])
    return {"inline_keyboard": kb}

def _old_build_traffic_ui():
    global recent_traffic
    current_time = time.time()
    # ১০ মিনিটের ট্রাফিক নেওয়ার জন্য 3600 (১ ঘণ্টা) এর জায়গায় 600 (১০ মিনিট) করে দেওয়া হলো
    recent_traffic = [t for t in recent_traffic if current_time - t.get("time", 0) <= 600]
    
    stats = {}
    for t in recent_traffic:
        srv = t.get("service", "Unknown")
        iso = t.get("iso", "XX")
        flag = t.get("flag", "🌍")
        
        if srv not in stats:
            stats[srv] = {}
        if iso not in stats[srv]:
            stats[srv][iso] = {"count": 0, "flag": flag}
        stats[srv][iso]["count"] += 1
        
    txt = "🔥 10 Minute LIVE Traffic\n\n"
    
    kb = []
    if not stats:
        txt += "<i>No recent traffic found in the last 10 minutes...</i>\n"
    else:
        srv_totals = []
        for srv, countries in stats.items():
            total = sum(c["count"] for c in countries.values())
            srv_totals.append((srv, total, countries))
        
        srv_totals.sort(key=lambda x: x[1], reverse=True)
        
        for srv, total, countries in srv_totals:
            app_full_name, prem_app_html = get_service_info_html(srv)
            # Service Name and Total count
            txt += f"{prem_app_html} {app_full_name.upper()} {total}\n"
            
            c_list = sorted(countries.items(), key=lambda x: x[1]["count"], reverse=True)
            c_list = c_list[:7] # আপনি চাইলে লিমিট সরাতে পারেন
            
            for i, (iso, c_data) in enumerate(c_list):
                prem_flag_html = get_flag_info_html(iso)
                count = c_data["count"]
                
                c_name = iso
                for code, fdata in bot_settings.get("premium_flags", {}).items():
                    if fdata.get("iso") == iso:
                        c_name = fdata.get("name", iso)
                        break
                
                # Logic for LOW, MEDIUM, HIGH
                if count < 20:
                    status_text = "LOW"
                    status_emoji = '<tg-emoji emoji-id="5408846744727334338">🔴</tg-emoji>'
                elif count < 30:
                    status_text = "MEDIUM"
                    status_emoji = '<tg-emoji emoji-id="5409220909393290791">🟡</tg-emoji>'
                else:
                    status_text = "HIGH"
                    status_emoji = '<tg-emoji emoji-id="5192812028632274956">🟢</tg-emoji>'
                        
                txt += f"{prem_flag_html} {c_name.upper()} :  {status_text} {status_emoji}\n"
                
            txt += "\n"
        
        now = datetime.now()
        current_time_str = now.strftime("%I:%M %p")
        updated_emoji = '<tg-emoji emoji-id="5336983442125001376"><tg-emoji emoji-id=\"5258419835922030550\">🕒</tg-emoji></tg-emoji>'
        txt += f"{updated_emoji} Updated {current_time_str}\n"

        for srv, _, _ in srv_totals: 
            safe_srv = srv[:20] 
            app_full_name, _ = get_service_info_html(safe_srv, safe_srv)
            kb.append([{"text": f"Explore {app_full_name} Range", "icon_custom_emoji_id": "5190645917711114179", "callback_data": f"exp_rng_{safe_srv}", "style": "success"}])
            
    txt = render_body_text(txt)
    kb.append([{"text": "Refresh", "icon_custom_emoji_id": "5465368548702446780", "callback_data": "refresh_traffic", "style": "primary"}])
    kb.append([{"text": "Close", "icon_custom_emoji_id": "5420130255174145507", "callback_data": "close_msg", "style": "danger"}])
    
    return txt, {"inline_keyboard": kb}

# ==========================================
# 🎨 VIDEO-STYLE UI  (Brothers Workers Zone layout)
# Reply keyboard, service/country list, number card, My Account,
# Traffic report, Elite Leaderboard — sob ekhane.
# ==========================================
from datetime import timedelta as _td, timezone as _tz

# Optional: premium emoji id boshale button e custom icon dekhabe.
# Khali thakle normal emoji/glyph use hobe.   e.g. {"refresh": "5465368548702446780"}
UI_ICON = {
    "get": "5897567714674741148", "search": "5429571366384842791", "acct": "5235516089592463902",
    "ws": "5258423306255604960", "traffic": "5258391025281408576", "top": "5258185631355378853",
    "refresh": "5258420634785947640", "withdraw": "5258368777350816286", "agree": "5357069174512303778",
    "week": "5258105663359294787",
}
_UI_GLYPH = {"copy": "⧉", "refresh": "↻", "globe": "🌐", "otp": "📢", "back": "←", "withdraw": "💲",
             "link": "🔗", "agree": "✅", "cancel": "⊗", "today": "<tg-emoji emoji-id=\"5258419835922030550\">🕒</tg-emoji>", "week": "📅", "all": "<tg-emoji emoji-id=\"5258514780469075716\">📥</tg-emoji>",
             "get": "📞", "search": "🔍", "acct": "👤", "ws": "📱", "traffic": "📈", "top": "⭐"}

recent_assign = []      # [{"service","iso","time"}]  -> Traffic report e success % er jonno
_lb_cache = {}


def _ibtn(text, icon=None, **kw):
    b = {"text": text}
    if icon and UI_ICON.get(icon):
        b["icon_custom_emoji_id"] = UI_ICON[icon]
    elif icon and _UI_GLYPH.get(icon):
        b["text"] = f"{_UI_GLYPH[icon]} {text}"
    b.update(kw)
    return b


def _bd_now():
    return datetime.now(_tz.utc) + _td(hours=6)


def _bd_day(back=0):
    return (_bd_now() - _td(days=back)).strftime("%Y%m%d")


def _money(x):
    try:
        return f"{float(x):.2f}৳"
    except Exception:
        return "0.00৳"


# ---------- custom emoji (message + button) ----------
_USER_EMOJI = {
    "👤": "5258362837411045098", "🆔": "5226513232549664618", "📅": "5258105663359294787",
    "✅": "5357069174512303778", "💲": "5258368777350816286", "📖": "5258328383183396223",
    "🔍": "5429571366384842791", "💬": "5258020476977946656", "🔑": "5226513232549664618",
    "💡": "5258216851472654189", "❗": "5258474669769497337", "🔒": "5258476306152038031",
    "📱": "5258423306255604960", "⚡": "5258152182150077732", "⏱": "5258419835922030550",
    "↻": "5258420634785947640", "⭐": "5258185631355378853", "📞": "5897567714674741148",
    "📈": "5258391025281408576",
}
_EMOJI_FALLBACK = {"↻": "🔄"}
_emoji_cache = {"n": -1, "map": None, "re": None}


def _emoji_tools():
    if _emoji_cache["n"] != len(GLOBAL_BODY_EMOJIS):
        m = dict(GLOBAL_BODY_EMOJIS)
        for k, v in _USER_EMOJI.items():      # tomar deowa id gulo purono mapping ke override kore
            m[k] = v
            m[k + "\ufe0f"] = v
        keys = sorted(m, key=len, reverse=True)
        _emoji_cache.update(n=len(GLOBAL_BODY_EMOJIS), map=m,
                            re=re.compile("|".join(re.escape(k) for k in keys)))
    return _emoji_cache["map"], _emoji_cache["re"]


def _emoji_sub(text):
    """Text er normal emoji -> <tg-emoji> (age theke tg-emoji thakle ta ses kore na)."""
    m, rx = _emoji_tools()
    parts = re.split(r'(<tg-emoji.*?</tg-emoji>)', str(text))
    for i, p in enumerate(parts):
        if not p.startswith('<tg-emoji'):
            parts[i] = rx.sub(lambda mo: '<tg-emoji emoji-id="%s">%s</tg-emoji>' % (
                m[mo.group(0)], _EMOJI_FALLBACK.get(mo.group(0), mo.group(0))), p)
    return "".join(parts)


def _strip_btn_icons(markup):
    """Button text er shurur emoji soriye icon_custom_emoji_id boshay."""
    try:
        was_str = isinstance(markup, str)
        data = json.loads(markup) if was_str else markup
        if not isinstance(data, dict):
            return markup
        import copy as _copy
        data = _copy.deepcopy(data)
        m, rx = _emoji_tools()
        lead = re.compile(r"^\s*(" + rx.pattern + r")\s*")
        for row in (data.get("inline_keyboard") or data.get("keyboard") or []):
            for btn in row:
                if isinstance(btn, dict) and "icon_custom_emoji_id" not in btn and isinstance(btn.get("text"), str):
                    mo = lead.match(btn["text"])
                    if mo and btn["text"][mo.end():].strip():
                        btn["icon_custom_emoji_id"] = m[mo.group(1)]
                        btn["text"] = btn["text"][mo.end():]
        return json.dumps(data) if was_str else data
    except Exception:
        return markup


# ---------- reply keyboard ----------
def main_menu(user_id):
    kb = [
        [_ibtn("Get Number", "get"), _ibtn("Search Number", "search")],
        [_ibtn("My Account", "acct"), _ibtn("WS Checker", "ws")],
        [_ibtn("Traffic", "traffic"), _ibtn("Top Users", "top")],
    ]
    if is_admin(user_id):
        kb.append([{"text": "Admin Panel", "icon_custom_emoji_id": "5341715473882955310"}])
    return {"keyboard": kb, "resize_keyboard": True}


def search_reply_kb():
    return {"keyboard": [[_ibtn("Back", "back"), _ibtn("Cancel", "cancel")]], "resize_keyboard": True}


_MENU_ALIASES = {
    "get number": "GET NUMBER", "search number": "Search Number", "my account": "MY ACCOUNT",
    "ws checker": "WS CHECKER", "traffic": "TRAFFIC", "top users": "TOP USERS",
    "refer": "Refer", "withdrawal": "WITHDRAWAL", "support": "SUPPORT", "2fa online": "2FA ONLINE",
    "admin panel": "Admin Panel",
    "/refer": "Refer", "/withdraw": "WITHDRAWAL", "/support": "SUPPORT", "/2fa": "2FA ONLINE",
    "/account": "MY ACCOUNT", "/traffic": "TRAFFIC", "/top": "TOP USERS", "/getnumber": "GET NUMBER",
}


def _norm_menu_text(t):
    """'📞 Get Number' / 'GET NUMBER' / 'Get Number' — sob ke ekek command e convert kore."""
    if not t or t.startswith("/start"):
        return t
    k = re.sub(r"^[^\w/]+", "", t.strip()).strip().lower()
    return _MENU_ALIASES.get(k, t)


# ---------- user tracking ----------
def _remember_user(uid, frm):
    try:
        if not frm:
            return
        name = ((frm.get("first_name") or "") + " " + (frm.get("last_name") or "")).strip()[:40]
        uname = frm.get("username") or ""
        u = user_cache.get(uid) or get_user(uid)
        if u.get("name") != name or u.get("username") != uname:
            u["name"], u["username"] = name, uname
            if db:
                _aux_pool.submit(lambda: db.collection('users').document(str(uid)).set(
                    {"name": name, "username": uname}, merge=True))
    except Exception:
        pass


def _bump(uid, inc=None, day=None):
    """inc: {"total_numbers": 1}   day: {"otp_days": 1} (BD date wise counter)"""
    inc, day = inc or {}, day or {}
    key = "d" + _bd_day()
    u = user_cache.get(uid)
    if u is not None:
        for k, v in inc.items():
            u[k] = u.get(k, 0) + v
        for k, v in day.items():
            m = u.setdefault(k, {})
            m[key] = m.get(key, 0) + v
    if db:
        try:
            payload = {k: firestore.Increment(v) for k, v in inc.items()}
            for k, v in day.items():
                payload[k] = {key: firestore.Increment(v)}
            if payload:
                db.collection('users').document(str(uid)).set(payload, merge=True)
        except Exception:
            pass


def _record_otp_day(uid):
    _bump(uid, day={"otp_days": 1})


def credit_otp_reward(uid, amount):
    update_balance(uid, amount)
    _bump(uid, inc={"total_earned": float(amount)}, day={"earn_days": float(amount)})


def _log_assign(service, nums):
    global recent_assign
    now = time.time()
    recent_assign = [a for a in recent_assign if now - a.get("time", 0) <= 3600]
    for n in nums:
        clean = str(n).replace("+", "").strip()
        srv = service or (assigned_number_metadata.get(clean) or {}).get("service") or "Search"
        _, iso = get_flag_and_code(clean)
        recent_assign.append({"service": get_service_info_html(srv)[0], "iso": iso, "time": now})


def _on_numbers_assigned(uid, val):
    try:
        nums = (val or {}).get("nums") or []
        if not nums:
            return
        n = len(nums)
        _bump(uid, inc={"total_numbers": n}, day={"num_days": n})
        _log_assign(val.get("service"), nums)
    except Exception as e:
        print(f"⚠️ assign track error: {e}", flush=True)


# ---------- service / country list ----------
def _all_services():
    s = set()
    s.update([b["service"].upper() for b in number_batches.values() if b["numbers"]])
    for k in ("voltx_services", "stex_services", "zenex_services", "zebra_services", "scarface_services"):
        s.update(bot_settings.get(k, {}).keys())
    return s


def _flag_lookup(c):
    """country (iso ba name) -> (NAME, ISO, emoji_id, char)"""
    cu = str(c).upper().strip()
    for code, fd in bot_settings.get("premium_flags", {}).items():
        iso = (fd.get("iso") or "").upper()
        name = (fd.get("name") or "").upper()
        if cu == iso or cu == name or (name and (cu in name or name in cu)):
            return (fd.get("name") or c).upper(), iso or cu, fd.get("id"), fd.get("char", "🌍")
    return cu, cu, None, "🌍"


def _country_price(service, c):
    base = float(bot_settings.get("otp_reward", 0.0))
    prices = [float(b.get("custom_otp_rate", base)) for b in number_batches.values()
              if b.get("service") == service and b.get("country") == c and b.get("numbers")]
    api = any(c in bot_settings.get(k, {}).get(service, {}) for k in
              ("voltx_services", "stex_services", "zenex_services", "zebra_services", "scarface_services"))
    if api or not prices:
        prices.append(base)
    return min(prices)


def _service_icon_id(s):
    eid = "5352694861990501856"
    for app_key, app_data in bot_settings.get("premium_apps", {}).items():
        if s == app_key or s in app_key or app_key in s:
            if "id" in app_data:
                eid = app_data["id"]
                break
    return eid


def _service_list_ui():
    services = _all_services()
    if not services:
        return None, None
    kb = [[{"text": s, "icon_custom_emoji_id": _service_icon_id(s), "callback_data": f"g_s_{s}"}]
          for s in sorted(services)]
    for b in bot_settings["custom_messages"].get("get_number", {}).get("buttons", []):
        kb.append([b.copy()])
    return "Select a Service:", {"inline_keyboard": kb}


def _send_service_list(chat_id):
    txt, kb = _service_list_ui()
    if not txt:
        send_message(chat_id, render_body_text("❌ <b>No numbers or services available!</b>"))
        return
    send_message(chat_id, txt, reply_markup=kb)


def _country_list_ui(service):
    countries = set()
    countries.update([b["country"] for b in number_batches.values() if b["service"] == service and b["numbers"]])
    for k in ("voltx_services", "stex_services", "zenex_services", "zebra_services", "scarface_services"):
        countries.update(bot_settings.get(k, {}).get(service, {}).keys())
    _, app_html = get_service_info_html(service)
    txt = render_body_text(f"<b>Select Country for</b> {app_html} <b>{html.escape(service.upper())}:</b>")
    kb = []
    for c in sorted(countries):
        name, iso, eid, char = _flag_lookup(c)
        label = f"{name} [{iso}] {_country_price(service, c):.2f}৳"
        btn = {"text": label, "callback_data": f"g_p_{service}_{c}"}
        if eid:
            btn["icon_custom_emoji_id"] = eid
        else:
            btn["text"] = f"{char} {label}"
        kb.append([btn])
    kb.append([_ibtn("Back to Services", "back", callback_data="back_to_services")])
    return txt, {"inline_keyboard": kb}


def _pick_panel(service, country):
    """country select korle automatic je panel e stock ache seta (local age)."""
    for b in number_batches.values():
        if b.get("service") == service and b.get("country") == country and b.get("numbers"):
            return "0"
    for pid, key in (("1", "voltx_services"), ("2", "stex_services"), ("3", "zenex_services"),
                     ("4", "zebra_services"), ("5", "scarface_services")):
        if country in bot_settings.get(key, {}).get(service, {}):
            return pid
    return None


# ---------- number card ----------
def _number_card(chat_id, nums, service, change_cb, custom_btns=None):
    first = str(nums[0]) if nums else ""
    char, iso, eid = get_flag_info_from_num(first)
    cname = get_flag_info_html(first, return_full_name=True)
    cname = (iso if (not cname or cname[:1].isdigit()) else cname).upper()
    flag_html = f'<tg-emoji emoji-id="{eid}">{char}</tg-emoji>' if eid else char
    if service:
        _, app_html = get_service_info_html(service)
        header = f"{flag_html} <b>{html.escape(cname)}</b> {app_html} <b>{html.escape(str(service).upper())}</b>"
    else:
        header = f"{flag_html} <b>{html.escape(cname)}</b>"
    kb = []
    for n in nums:
        disp = get_display_number(n, False)
        btn = {"text": f"{_UI_GLYPH['copy']} {disp}", "copy_text": {"text": disp}}
        if eid:
            btn["icon_custom_emoji_id"] = eid
        else:
            btn["text"] = f"{char} {btn['text']}"
        kb.append([btn])
    kb.append([_ibtn("Change Numbers", "refresh", callback_data=change_cb)])
    if service:
        kb.append([_ibtn("Change Country", "globe", callback_data=f"g_s_{service}")])
    kb.append([_ibtn("OTP Group", "otp", url=bot_settings["otp_link"])])
    for b in (custom_btns or []):
        kb.append([b.copy()])
    return render_body_text(header), kb


# ---------- My Account ----------
def build_account_ui(uid, frm=None):
    u = get_user(uid)
    day = "d" + _bd_day()
    uname = (frm or {}).get("username") or u.get("username")
    ident = f"@{uname}" if uname else (u.get("name") or "User")
    joined = u.get("joined") or _bd_now().strftime("%Y-%m-%d")
    pending = sum(float(w.get("amount", 0)) for w in pending_withdrawals.values() if w.get("user_id") == uid)
    t_num = int(u.get("total_numbers", 0))
    t_otp = int(u.get("total_otps", 0))
    rate = min(100.0, (t_otp * 100.0 / t_num)) if t_num else 0.0
    active = len((user_active_sessions.get(uid) or {}).get("nums", []))
    num_today = int((u.get("num_days") or {}).get(day, 0))
    otp_today = int((u.get("otp_days") or {}).get(day, 0))
    earn_today = float((u.get("earn_days") or {}).get(day, 0.0))
    line = "━━━━━━━━━━━━━━━━━━"
    txt = (
        f"👤 <b>MY ACCOUNT PROFILE</b>\n{line}\n"
        f"🆔 <b>User ID:</b> {uid} | {html.escape(str(ident))}\n"
        f"📅 <b>Joined:</b> {joined} | ✅ <b>Status:</b> Active\n\n"
        f"💲 <b>WALLET SUMMARY</b>\n"
        f"├ <b>Main Balance:</b> {_money(u.get('balance', 0))}\n"
        f"├ <b>Lifetime Earnings:</b> {_money(u.get('total_earned', 0))}\n"
        f"├ <b>Total Payouts:</b> {_money(u.get('total_paid', 0))}\n"
        f"└ <b>Pending Review:</b> {_money(pending)}\n\n"
        f"⭐ <b>TODAY'S STATS</b>\n"
        f"├ <b>Numbers Taken:</b> {num_today}\n"
        f"├ <b>OTPs Received:</b> {otp_today}\n"
        f"└ <b>Today Earned:</b> {_money(earn_today)}\n\n"
        f"📖 <b>LIFETIME STATS</b>\n"
        f"├ <b>Total Numbers:</b> {t_num}\n"
        f"├ <b>Total OTPs:</b> {t_otp}\n"
        f"├ <b>Currently Active:</b> {active}\n"
        f"└ <b>Success Rate:</b> {rate:.1f}%\n"
        f"{line}"
    )
    kb = {"inline_keyboard": [[_ibtn("Withdraw Balance", "withdraw", callback_data="acc_withdraw")]]}
    return render_body_text(txt), kb


# ---------- Traffic ----------
def build_traffic_ui():
    now = time.time()
    WIN = 900   # 15 minutes
    agg = {}
    for a in list(recent_assign):
        if now - a.get("time", 0) <= WIN:
            e = agg.setdefault(a["service"], {}).setdefault(a["iso"], {"a": 0, "o": 0})
            e["a"] += 1
    for t in list(recent_traffic):
        if now - t.get("time", 0) <= WIN:
            e = agg.setdefault(t.get("service", "Unknown"), {}).setdefault(t.get("iso", "XX"), {"a": 0, "o": 0})
            e["o"] += 1
    lines = ["📈 <b>REAL-TIME TRAFFIC REPORT</b>", "━━━━━━━━━━━━━━━━━━", "<tg-emoji emoji-id=\"5258419835922030550\">🕒</tg-emoji> <b>LAST 15 MINUTES:</b>", ""]
    if not agg:
        lines.append("<i>No traffic in the last 15 minutes...</i>")
        lines.append("")
    else:
        ranked = sorted(agg.items(), key=lambda kv: sum(v["a"] + v["o"] for v in kv[1].values()), reverse=True)
        for srv, countries in ranked[:8]:
            app_name, app_html = get_service_info_html(srv)
            lines.append(f"{app_html} <b>{html.escape(app_name)}</b>")
            clist = sorted(countries.items(), key=lambda kv: kv[1]["a"] + kv[1]["o"], reverse=True)[:6]
            for iso, v in clist:
                pct = min(100, round(v["o"] * 100 / v["a"])) if v["a"] else (100 if v["o"] else 0)
                cname = html.escape(str(get_flag_info_html(iso, return_full_name=True)).upper())
                lines.append(f"└── {get_flag_info_html(iso)} <b>{cname} [{v['o']} OTPs • {pct}%]</b>")
            lines.append("")
    lines.append("↻ <i>Updated Just Now (OTP Volume &amp; Success Rate)</i>")
    kb = {"inline_keyboard": [[_ibtn("Refresh", "refresh", callback_data="refresh_traffic"),
                               _ibtn("OTP Group", "otp", url=bot_settings["otp_link"])]]}
    return render_body_text("\n".join(lines)), kb


# ---------- Leaderboard ----------
def _lb_rows(period, force=False):
    c = _lb_cache.get(period)
    ttl = 120 if period == "week" else 20
    if c and not force and time.time() - c[0] < ttl:
        return c[1]
    rows = []
    if db:
        try:
            if period == "today":
                k = "d" + _bd_day()
                q = db.collection('users').order_by(f"otp_days.{k}", direction=firestore.Query.DESCENDING).limit(10)
                for d in q.stream():
                    dd = d.to_dict()
                    rows.append((d.id, dd, int((dd.get("otp_days") or {}).get(k, 0)),
                                 float((dd.get("earn_days") or {}).get(k, 0.0))))
            elif period == "all":
                q = db.collection('users').order_by("total_otps", direction=firestore.Query.DESCENDING).limit(10)
                for d in q.stream():
                    dd = d.to_dict()
                    rows.append((d.id, dd, int(dd.get("total_otps", 0)), float(dd.get("total_earned", 0.0))))
            else:
                keys = ["d" + _bd_day(i) for i in range(7)]
                for d in db.collection('users').stream():
                    dd = d.to_dict()
                    od, ed = dd.get("otp_days") or {}, dd.get("earn_days") or {}
                    o = sum(od.get(k, 0) for k in keys)
                    if o > 0:
                        rows.append((d.id, dd, int(o), float(sum(ed.get(k, 0.0) for k in keys))))
                rows.sort(key=lambda r: r[2], reverse=True)
                rows = rows[:10]
        except Exception as e:
            print(f"⚠️ leaderboard error: {e}", flush=True)
    _lb_cache[period] = (time.time(), rows)
    return rows


def build_leaderboard_ui(period="today", force=False):
    rows = _lb_rows(period, force)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    title = {"today": "TODAY (BD TIME)", "week": "LAST 7 DAYS", "all": "ALL-TIME"}[period]
    line = "━━━━━━━━━━━━━━━━━━"
    out = [f"⚡ <b>ELITE LEADERBOARD | TOP 10</b>", f"✅ <i>Live Rankings: Push hard &amp; claim your spot at #1!</i>", line, ""]
    if not rows:
        out.append("<i>No rankings yet — be the first!</i>")
        out.append("")
    for i, (uid, dd, otps, earn) in enumerate(rows, 1):
        name = html.escape((dd.get("name") or "").strip() or f"User {uid}")
        out.append(f"{medals.get(i, '🎖')} <b>Rank {i}: {name}</b>")
        out.append(f"╰──📖 {otps:,} OTPs | Ⓑ <b>BDT{earn:.4f}</b>")
        out.append("")
    out += [line, f"⏱ <b>Timeframe: {title}</b>", "↻ <i>Updated Just Now • Live Rankings</i>"]
    others = [("today", "Today", "today"), ("week", "Last 7 Days", "week"), ("all", "All-Time", "all")]
    row = [_ibtn(lbl, ic, callback_data=f"lb_{p}") for p, lbl, ic in others if p != period]
    kb = {"inline_keyboard": [row, [_ibtn("Refresh", "refresh", callback_data=f"lb_ref_{period}")]]}
    return render_body_text("\n".join(out)), kb


# ---------- callbacks of the new screens ----------
def _video_callback(call, chat_id, msg_id, data):
    """acc_*, lb_* callback. True return korle main handle_callback ar agay barbe na."""
    if data == "acc_back":
        txt, kb = build_account_ui(chat_id, call.get("from"))
        edit_message(chat_id, msg_id, txt, reply_markup=kb)
        return True

    if data == "acc_withdraw":
        if not bot_settings.get("withdraw_on", True):
            answer_callback(call["id"], "⛔ Withdrawals are currently disabled.", show_alert=True)
            return True
        u = get_user(chat_id)
        bal = float(u.get("balance", 0.0))
        min_w = float(bot_settings.get("min_withdraw", 0.0))
        answer_callback(call["id"])
        if bal < min_w:
            txt = render_body_text(
                "⚠️ <b>INSUFFICIENT BALANCE</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Minimum withdrawal requirement:</b> {min_w:.2f}৳\n"
                f"👛 <b>Your available balance:</b> {bal:.2f}৳")
            kb = {"inline_keyboard": [[_ibtn("Back to Account", "back", callback_data="acc_back")]]}
            edit_message(chat_id, msg_id, txt, reply_markup=kb)
        else:
            handle_message({"chat": {"id": chat_id, "type": "private"}, "text": "WITHDRAWAL",
                            "from": call.get("from") or {}})
        return True

    if data in ("lb_today", "lb_week", "lb_all"):
        txt, kb = build_leaderboard_ui(data[3:])
        edit_message(chat_id, msg_id, txt, reply_markup=kb)
        return True

    if data.startswith("lb_ref_"):
        period = data[len("lb_ref_"):]
        if period not in ("today", "week", "all"):
            period = "today"
        txt, kb = build_leaderboard_ui(period, force=True)
        edit_message(chat_id, msg_id, txt, reply_markup=kb)
        answer_callback(call["id"], "✅ Leaderboard Updated!")
        return True

    return False


# ==========================================
# Message Handler
# ==========================================
def handle_message(msg):
    global total_uploaded_stats
    chat_id = msg["chat"]["id"]
    chat_type = msg["chat"].get("type", "private")
    
    if chat_type != "private":
        return
        
    text = _norm_menu_text(msg.get("text", ""))
    register_user_local(chat_id)
    _remember_user(chat_id, msg.get("from")) # 🌟 Save User locally for Free Broadcasts!

    if is_user_banned(chat_id):
        send_message(chat_id, render_body_text("🚫 <b>You are banned from using this bot!</b>\nIf you think this is a mistake, please contact support."))
        return
    
    # --- REFERRAL & DEEP LINK FIX ---
    if text.startswith("/start"):
        parts = text.split()
        if len(parts) > 1:
            param = parts[1]
            if param.isdigit():
                inviter = int(param)
                if inviter != chat_id:
                    if db:
                        doc = db.collection('users').document(str(chat_id)).get()
                        if not doc.exists:
                            get_user(chat_id)
                            db.collection('users').document(str(chat_id)).update({"referred_by": inviter, "ref_paid": False})
            # deep link create fix
            elif param == "getnumber":
                text = "GET NUMBER"
            elif param == "2fa":
                text = "2FA ONLINE"
            elif param == "refer":
                text = "Refer"
            elif param == "withdrawal":
                text = "WITHDRAWAL"
            elif param == "support":
                text = "SUPPORT"
                        
    if not check_force_join(chat_id):
        send_force_join_msg(chat_id)
        return
        
    MAIN_MENU_CMDS = ["GET NUMBER", "Refer", "WITHDRAWAL", "SUPPORT", "Admin Panel", "2FA ONLINE", "WS CHECKER", "MY ACCOUNT", "TOP USERS", "TRAFFIC", "Search Number"]
    
    is_main_cmd = False
    if text in MAIN_MENU_CMDS or text.startswith("/start"):
        if chat_id in user_states: del user_states[chat_id]
        if chat_id in temp_data: del temp_data[chat_id]
        is_main_cmd = True
    
    if text == "WS CHECKER":
        if not bot_settings.get("ws_on", True):
            send_message(chat_id, render_body_text("⛔ <b>WS Checker is currently disabled by admin.</b>"))
            return
        ws_checker.show_panel(chat_id)
        return

    # Search Number er "Back / Cancel" reply button
    if user_states.get(chat_id) == "wait_for_search" and text.strip().lstrip("←⊗ ").strip().lower() in ("back", "cancel"):
        del user_states[chat_id]
        send_message(chat_id, render_body_text("<tg-emoji emoji-id=\"5226513232549664618\">🏠</tg-emoji> <b>Main Menu</b>"), reply_markup=main_menu(chat_id))
        return

    if chat_id in user_states and not is_main_cmd:
        state = user_states[chat_id]

        # 🌟 WS Checker: WhatsApp phone number input
        if state == "wait_for_ws_phone":
            if ws_checker.handle_state(chat_id, text):
                return
        
        # 🌟 Auto Captcha Panel Setup Flow 
        if state == "wait_for_cpanel_url" and text:
            temp_data[chat_id]["p_data"]["login_url"] = text.strip()
            user_states[chat_id] = "wait_for_cpanel_user"
            send_message(chat_id, render_body_text("2️⃣ <b>Username</b>\n➡️ Panel এর Username দিন:"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_cpanel_user" and text:
            temp_data[chat_id]["p_data"]["username"] = text.strip()
            user_states[chat_id] = "wait_for_cpanel_pass"
            send_message(chat_id, render_body_text("3️⃣ <b>Password</b>\n➡️ Panel এর Password দিন:"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_cpanel_pass" and text:
            temp_data[chat_id]["p_data"]["password"] = text.strip()
            user_states[chat_id] = "wait_for_cpanel_msg_link"
            send_message(chat_id, render_body_text("4️⃣ <b>Message Link</b>\n➡️ যেখান থেকে SMS/OTP ডাটা (JSON) আসবে সেই Link দিন:"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_cpanel_msg_link" and text:
            temp_data[chat_id]["p_data"]["msg_link"] = text.strip()
            user_states[chat_id] = "wait_for_cpanel_num_col_name"
            send_message(chat_id, render_body_text("5️⃣ <b>Number Column Name</b>\n➡️ Data তে Number column এর নাম কী? (যেমন: number, phone):"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_cpanel_num_col_name" and text:
            temp_data[chat_id]["p_data"]["num_col_name"] = text.strip()
            user_states[chat_id] = "wait_for_cpanel_num_col_idx"
            send_message(chat_id, render_body_text("6️⃣ <b>Number Column Serial</b>\n➡️ Number Column এর Serial Number কত? (যেমন: 3, 5):"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_cpanel_num_col_idx" and text:
            if text.isdigit():
                temp_data[chat_id]["p_data"]["num_col_idx"] = int(text)
                user_states[chat_id] = "wait_for_cpanel_msg_col_name"
                send_message(chat_id, render_body_text("7️⃣ <b>Message Column Name</b>\n➡️ Message/OTP column এর নাম কী? (যেমন: message, sms):"), reply_markup=get_cancel_kb())
            else:
                 send_message(chat_id, render_body_text("❌ Please enter a valid number serial!"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_cpanel_msg_col_name" and text:
            temp_data[chat_id]["p_data"]["msg_col_name"] = text.strip()
            user_states[chat_id] = "wait_for_cpanel_msg_col_idx"
            send_message(chat_id, render_body_text("8️⃣ <b>Message Column Serial</b>\n➡️ Message Column এর Serial Number কত? (যেমন: 5, 7):"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_cpanel_msg_col_idx" and text:
            if text.isdigit():
                temp_data[chat_id]["p_data"]["msg_col_idx"] = int(text)
                temp_data[chat_id]["p_data"]["login_status"] = "⏳ Pending Auto-Login..."
                
                # Save the panel configuration
                bot_settings["panels"].append(temp_data[chat_id]["p_data"])
                save_db()
                
                send_message(chat_id, render_body_text(f"{PEM['ok']} <b>Auto Captcha Panel Added Successfully!</b>\nবট এখন থেকে নিজেই ব্যাকগ্রাউন্ডে ক্যাপচা সলভ করে লগিন করে নিবে।"), reply_markup=main_menu(chat_id))
                
                msg_id = temp_data[chat_id]["msg_id"]
                handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": "manage_cpt_panels", "id": "internal"})
                
                del user_states[chat_id]
                del temp_data[chat_id]
            else:
                 send_message(chat_id, render_body_text("❌ Please enter a valid number serial!"), reply_markup=get_cancel_kb())
            return

        # --- User Management Flows ---
        elif state == "wait_for_um_bal_uid" and text:
            target_uid_str = text.strip()
            if not target_uid_str.isdigit():
                send_message(chat_id, render_body_text("❌ Invalid ID! Please send a numeric User ID."), reply_markup=get_cancel_kb())
                return
            target_uid = int(target_uid_str)
            if db:
                doc = db.collection('users').document(str(target_uid)).get()
                if not doc.exists:
                    send_message(chat_id, render_body_text("❌ User not found in database!"), reply_markup=get_cancel_kb())
                    return
                current_bal = doc.to_dict().get('balance', 0.0)
                temp_data[chat_id]["target_uid"] = target_uid
                user_states[chat_id] = "wait_for_um_bal_amt"
                send_message(chat_id, render_body_text(f"✅ User found!\n💰 Current Balance: {current_bal} ৳\n\n📝 Send the amount to ADD (e.g. 50) or REMOVE (e.g. -50):"), reply_markup=get_cancel_kb())
            return

        elif state == "wait_for_um_bal_amt" and text:
            try:
                amt = float(text.strip())
                target_uid = temp_data[chat_id]["target_uid"]
                update_balance(target_uid, amt)
                send_message(chat_id, render_body_text(f"{PEM['ok']} Balance updated successfully for {target_uid}!"), reply_markup=main_menu(chat_id))
                send_message(target_uid, render_body_text(f"🔔 Your balance has been adjusted by <b>{amt} ৳</b> by an Admin."))
                del user_states[chat_id]
                del temp_data[chat_id]
            except ValueError:
                send_message(chat_id, render_body_text("❌ Invalid amount! Please send a number."), reply_markup=get_cancel_kb())
            return

        elif state == "wait_for_um_ban_uid" and text:
            target_uid_str = text.strip()
            if not target_uid_str.isdigit():
                send_message(chat_id, render_body_text("❌ Invalid ID!"), reply_markup=get_cancel_kb())
                return
            target_uid = int(target_uid_str)
            if db:
                doc_ref = db.collection('users').document(str(target_uid))
                doc = doc_ref.get()
                if not doc.exists:
                    send_message(chat_id, render_body_text("❌ User not found in database!"), reply_markup=get_cancel_kb())
                    return
                current_status = doc.to_dict().get("banned", False)
                new_status = not current_status
                doc_ref.update({"banned": new_status})
                
                user_banned_cache[target_uid] = {'banned': new_status, 'time': time.time()}
                
                status_text = "BANNED 🚫" if new_status else "UNBANNED ✅"
                send_message(chat_id, render_body_text(f"✅ User {target_uid} has been {status_text}!"), reply_markup=main_menu(chat_id))
                del user_states[chat_id]
                del temp_data[chat_id]
            return

        elif state == "wait_for_um_prof_uid" and text:
            target_uid_str = text.strip()
            if not target_uid_str.isdigit():
                send_message(chat_id, render_body_text("❌ Invalid ID!"), reply_markup=get_cancel_kb())
                return
            target_uid = int(target_uid_str)
            if db:
                doc = db.collection('users').document(str(target_uid)).get()
                if not doc.exists:
                    send_message(chat_id, render_body_text("❌ User not found in database!"), reply_markup=get_cancel_kb())
                    return
                data = doc.to_dict()
                is_verified = True if data.get('total_otps', 0) > 0 else data.get('verified', False)
                prof_text = f"""➖➖➖➖➖➖➖➖
👤 <b>USER PROFILE</b>
➖➖➖➖➖➖➖➖
🆔 ID: <code>{target_uid}</code>
💰 Balance: {data.get('balance', 0.0)} ৳
🤝 Total Refers: {data.get('total_refers', 0)}
🔐 Total OTPs: {data.get('total_otps', 0)}
✅ Verified: {is_verified}
🚫 Banned: {data.get('banned', False)}
➖➖➖➖➖➖➖➖"""
                kb = {"inline_keyboard": [[{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "user_management", "style": "primary"}]]}
                send_message(chat_id, render_body_text(prof_text), reply_markup=kb)
                del user_states[chat_id]
                del temp_data[chat_id]
            return

        # --- Menu Design Flow ---
        elif state == "wait_for_menu_text" and text:
            if len(text) > 3000:
                send_message(chat_id, render_body_text("❌ <b>অতিরিক্ত বড় টেক্সট!</b>\nদয়া করে ৩০০০ অক্ষরের কম টেক্সট দিন, নাহলে বট ফ্রিজ হয়ে যেতে পারে।"), reply_markup=get_cancel_kb())
                return
                
            try:
                menu_key = temp_data[chat_id]["menu_key"]
                formatted_html_text = extract_premium_html(msg)
                
                bot_settings["custom_messages"][menu_key]["text"] = formatted_html_text
                save_db()
                
                delete_message(chat_id, msg["message_id"])
                
                preview_text = render_body_text(formatted_html_text)
                success_text = f"{PEM['ok']} <b>Message Body Updated successfully!</b>\n\n🎨 <b>Editing: {menu_key.upper()}</b>\n\nPreview of current Text:\n{preview_text}"
                edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text(success_text), reply_markup=menu_edit_options_keyboard(menu_key))
            except Exception as e:
                send_message(chat_id, f"❌ Error saving text: {e}")
            finally:
                if chat_id in user_states: del user_states[chat_id]
                if chat_id in temp_data: del temp_data[chat_id]
            return
            
        elif state == "wait_for_menu_btn" and text:
            try:
                menu_key = temp_data[chat_id]["menu_key"]
                if "-" in text:
                    parts = text.split("-", 1)
                    btn_text = parts[0].strip()
                    btn_url = parts[1].strip()
                    
                    emoji_id = None
                    emoji_char = ""
                    for ent in msg.get("entities", []):
                        if ent.get("type") == "custom_emoji":
                            emoji_id = ent.get("custom_emoji_id")
                            offset = ent.get("offset", 0)
                            length = ent.get("length", 0)
                            b_text = text.encode('utf-16-le')
                            emoji_char = b_text[offset*2:(offset+length)*2].decode('utf-16-le')
                            break
                            
                    if emoji_char:
                        btn_text = btn_text.replace(emoji_char, "").strip()
                        
                    btn_data = {"text": btn_text, "url": btn_url, "style": "primary"}
                    if emoji_id:
                        btn_data["icon_custom_emoji_id"] = emoji_id
                        
                    bot_settings["custom_messages"][menu_key]["buttons"].append(btn_data)
                    save_db()
                    delete_message(chat_id, msg["message_id"])
                    edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text(f"{PEM['gear']} <b>Edit Inline Buttons: {menu_key.upper()}</b>"), reply_markup=menu_buttons_list_keyboard(menu_key))
                else:
                    send_message(chat_id, render_body_text(f"{PEM['no']} Invalid format. Use <code>Button Text - https://link.com</code>"))
            except Exception as e:
                 pass
            finally:
                if chat_id in user_states: del user_states[chat_id]
                if chat_id in temp_data: del temp_data[chat_id]
            return

        elif state == "wait_for_test_service" and text:
            temp_data[chat_id]["service"] = text.strip()
            user_states[chat_id] = "wait_for_test_number"
            send_message(chat_id, render_body_text("📝 Send the Number (e.g. +8801712345678):"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_test_number" and text:
            temp_data[chat_id]["number"] = text.strip()
            user_states[chat_id] = "wait_for_test_otp"
            send_message(chat_id, render_body_text("📝 Send the OTP (e.g. 556677):"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_test_otp" and text:
            temp_data[chat_id]["otp"] = text.strip()
            user_states[chat_id] = "wait_for_test_lang"
            send_message(chat_id, render_body_text("📝 Send the Language (e.g. EN, AR):"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_test_lang" and text:
            lang = text.strip().upper()
            if not lang.startswith("#"):
                lang = "#" + lang
                
            srv = temp_data[chat_id]["service"]
            num = temp_data[chat_id]["number"]
            otp = temp_data[chat_id]["otp"]
            
            masked = mask_number(num)
            prem_flag_html = get_flag_info_html(num)
            char, iso = get_flag_and_code(num)
            app_full_name, prem_app_html = get_service_info_html(srv)
            
            lang_name = LANG_MAP.get(lang, "English") if 'LANG_MAP' in globals() else lang.replace("#", "")
            msg_text = get_group_otp_msg(num, app_full_name, prem_app_html, iso, lang_name, otp)
            
            for fw in bot_settings["fw_groups"]:
                kb = []
                temp_row = []
                for btn in fw.get("buttons", []):
                    b_obj = {"text": btn["text"], "url": btn["url"], "style": "primary"}
                    if "icon_custom_emoji_id" in btn: b_obj["icon_custom_emoji_id"] = btn["icon_custom_emoji_id"]
                    temp_row.append(b_obj)
                    if len(temp_row) == 2:
                        kb.append(temp_row)
                        temp_row = []
                if temp_row: kb.append(temp_row)
                send_message(fw["chat_id"], msg_text, reply_markup={"inline_keyboard": kb})
                
            send_message(chat_id, render_body_text(f"{PEM['ok']} Test message formatted and sent to all Forward Groups!"), reply_markup=main_menu(chat_id))
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_emoji_extract":
            entities = msg.get("entities", [])
            custom_emoji_id = None
            emoji_text = ""
            for ent in entities:
                if ent.get("type") == "custom_emoji":
                    custom_emoji_id = ent.get("custom_emoji_id")
                    offset = ent.get("offset", 0)
                    length = ent.get("length", 0)
                    b_text = msg.get("text", "").encode('utf-16-le')
                    emoji_text = b_text[offset*2:(offset+length)*2].decode('utf-16-le')
                    break
            
            if custom_emoji_id:
                temp_data[chat_id] = {"id": custom_emoji_id, "char": emoji_text}
                user_states[chat_id] = "wait_for_emoji_details"
                send_message(chat_id, render_body_text(f"{PEM['ok']} Emoji ID পাওয়া গেছে: <code>{custom_emoji_id}</code>\n\n📌 এখন এটি সেভ করার জন্য টাইপ এবং নাম লিখুন।\n\n<b>ফরমেট:</b>\n`FLAG | 880 | BD | Bangladesh`\nঅথবা\n`APP | WhatsApp`"), reply_markup=get_cancel_kb())
            else:
                send_message(chat_id, render_body_text(f"{PEM['no']} কোনো Premium Emoji পাওয়া যায়নি! দয়া করে Custom Emoji সেন্ড করুন।"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_emoji_details" and text:
            parts = [p.strip() for p in text.split("|")]
            mode = parts[0].upper()
            eid = temp_data[chat_id]["id"]
            char = temp_data[chat_id]["char"]
            
            if mode == "FLAG" and len(parts) == 4:
                code, iso, name = parts[1], parts[2], parts[3]
                bot_settings["premium_flags"][code] = {"char": char, "iso": iso.upper(), "name": name, "id": eid}
                save_db()
                send_message(chat_id, render_body_text(f"{PEM['ok']} Flag Emoji সেভ হয়েছে!\nCode: {code} | Name: {name}"), reply_markup=emoji_settings_keyboard())
            elif mode == "APP" and len(parts) == 2:
                name = parts[1]
                bot_settings["premium_apps"][name.upper()] = {"char": char, "id": eid, "name": name.title()}
                save_db()
                send_message(chat_id, render_body_text(f"{PEM['ok']} App Emoji সেভ হয়েছে!\nName: {name}"), reply_markup=emoji_settings_keyboard())
            else:
                send_message(chat_id, render_body_text(f"{PEM['no']} ফরম্যাট ভুল!\n\nসঠিক ফরম্যাট:\n`FLAG | 880 | BD | Bangladesh`\n`APP | WhatsApp`"))
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state in ["wait_for_flag_txt", "wait_for_app_txt"] and "document" in msg:
            doc = msg["document"]
            if not doc["file_name"].endswith(".txt"):
                send_message(chat_id, render_body_text(f"{PEM['no']} Please upload a .txt file only."))
                return
            file_id = doc["file_id"]
            file_info = requests.get(f"{BASE_URL}/getFile?file_id={file_id}").json()
            file_path = file_info["result"]["file_path"]
            content = requests.get(f"{FILE_URL}{file_path}").text
            
            mode = "flags" if state == "wait_for_flag_txt" else "apps"
            count = 0
            
            if mode == "flags":
                for line in content.splitlines():
                    json_match = re.search(r'(\{.*\})', line)
                    if json_match:
                        try:
                            data = json.loads(json_match.group(1))
                            char = data.get("emoji")
                            eid = data.get("id")
                            
                            prefix_str = line[:json_match.start()].strip()
                            code_match = re.search(r'\((\d+)\)', prefix_str)
                            iso_match = re.search(r'\(([A-Za-z]+)\)', prefix_str)
                            
                            if code_match and iso_match and char and eid:
                                code = code_match.group(1)
                                iso = iso_match.group(1).upper()
                                name = prefix_str.replace(f"({code})", "").replace(f"({iso_match.group(1)})", "").replace(char, "").strip()
                                bot_settings["premium_flags"][code] = {"char": char, "iso": iso, "name": name, "id": eid}
                                count += 1
                        except: pass
            else:
                for line in content.splitlines():
                    json_match = re.search(r'(\{.*\})', line)
                    if json_match:
                        try:
                            data = json.loads(json_match.group(1))
                            char = data.get("emoji")
                            eid = data.get("id")
                            
                            name_part = line[:json_match.start()].strip()
                            name = name_part.replace(char, '').strip() if char else name_part
                            
                            if char and eid and name:
                                bot_settings["premium_apps"][name.upper()] = {"char": char, "id": eid, "name": name}
                                count += 1
                        except: pass
            
            save_db()
            send_message(chat_id, render_body_text(f"{PEM['ok']} Successfully loaded {count} Emojis!"), reply_markup=emoji_settings_keyboard())
            del user_states[chat_id]
            return

        elif state == "wait_for_broadcast":
            msg_id = msg["message_id"]
            send_message(chat_id, render_body_text(f"{PEM['ok']} Broadcast started..."))
            threading.Thread(target=broadcast_copymessage, args=(chat_id, msg_id)).start()
            del user_states[chat_id]
            return

        elif state == "wait_for_txt" and "document" in msg:
            doc = msg["document"]
            if not doc["file_name"].endswith(".txt"):
                send_message(chat_id, render_body_text(f"{PEM['no']} Please upload a .txt file only."))
                return
            file_id = doc["file_id"]
            file_info = requests.get(f"{BASE_URL}/getFile?file_id={file_id}").json()
            file_path = file_info["result"]["file_path"]
            file_content = requests.get(f"{FILE_URL}{file_path}").text
            
            temp_data[chat_id] = {"numbers": file_content.splitlines(), "filename": doc["file_name"]}
            user_states[chat_id] = "wait_for_service"
            send_message(chat_id, render_body_text(f"{PEM['ok']} File received.\n\n📌 Enter the service name (e.g., WHATSAPP):"), reply_markup=get_cancel_kb())
            return

        elif state == "wait_for_service" and text:
            temp_data[chat_id]["service"] = text.upper()
            user_states[chat_id] = "wait_for_country"
            send_message(chat_id, render_body_text(f"{PEM['ok']} Service set.\n\n🌍 Enter the country name (e.g., YEMEN):"), reply_markup=get_cancel_kb())
            return

        elif state == "wait_for_country" and text:
            temp_data[chat_id]["country"] = text.upper()
            user_states[chat_id] = "wait_for_otp_price"
            send_message(chat_id, render_body_text(f"{PEM['ok']} Country set.\n\n💰 Enter the OTP Price/Reward (e.g. 5 or 0.01):"), reply_markup=get_cancel_kb())
            return

        elif state == "wait_for_otp_price" and text:
            try:
                # ইউজারের দেওয়া দামটি নাম্বারে কনভার্ট করা
                otp_price = float(text.strip().replace("$", "").replace("TK", "").replace("tk", "").strip())
            except ValueError:
                send_message(chat_id, render_body_text("❌ Invalid price! Please enter a valid number (e.g. 5 or 0.01):"), reply_markup=get_cancel_kb())
                return

            country = temp_data[chat_id]["country"]
            service = temp_data[chat_id]["service"]
            raw_numbers = temp_data[chat_id]["numbers"]
            
            clean_nums = []
            for num in raw_numbers:
                num = num.strip()
                if num:
                    if not num.startswith('+'): num = '+' + num
                    clean_nums.append(num)
            
            batch_id = str(uuid.uuid4())[:8]
            number_batches[batch_id] = {"filename": temp_data[chat_id]["filename"], "service": service, "country": country, "custom_otp_rate": otp_price, "numbers": [{"num": n, "shares": 0, "used_by": []} for n in clean_nums]}
            total_uploaded_stats += len(clean_nums)
            
            save_db()
            
            kb = [
                [{"text": "✅ YES (Send Broadcast)", "callback_data": f"bc_yes_{batch_id}", "style": "success"}],
                [{"text": "❌ NO (Silent Add)", "callback_data": f"bc_no_{batch_id}", "style": "danger"}]
            ]
            send_message(chat_id, render_body_text(f"{PEM['ok']} Numbers added and OTP Reward set to {otp_price} TK!\n\n📢 <b>Do you want to send a broadcast message to all users?</b>"), reply_markup={"inline_keyboard": kb})
            
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_add_stex_key" and text:
            bot_settings["stex_keys"].append(text.strip())
            save_db()
            delete_message(chat_id, msg["message_id"])
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text(f"✅ StexSMS API Key Added! Total Keys: {len(bot_settings.get('stex_keys', []))}"), reply_markup=stex_control_keyboard())
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_add_voltx_key" and text:
            bot_settings["voltx_keys"].append(text.strip())
            save_db()
            delete_message(chat_id, msg["message_id"])
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text(f"✅ Voltx API Key Added! Total Keys: {len(bot_settings.get('voltx_keys', []))}"), reply_markup=voltx_control_keyboard())
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_add_sc" and text:
            code = text.strip().replace("+", "")
            if "search_countries" not in bot_settings: bot_settings["search_countries"] = []
            bot_settings["search_countries"].append(code)
            save_db()
            delete_message(chat_id, msg["message_id"])
            kb = []
            for idx, c in enumerate(bot_settings.get("search_countries", [])):
                kb.append([{"text": f"❌ Delete {c}", "callback_data": f"del_sc_{idx}", "style": "danger"}])
            kb.append([{"text": "➕ Add Country Code", "callback_data": "add_search_country", "style": "success"}])
            kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "stex_control", "style": "primary"}])
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text("🌍 <b>Allowed Search Countries:</b>\nOnly these country codes will be allowed in Search Number."), reply_markup={"inline_keyboard": kb})
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_add_vsc" and text:
            code = text.strip().replace("+", "")
            if "voltx_search_countries" not in bot_settings: bot_settings["voltx_search_countries"] = []
            bot_settings["voltx_search_countries"].append(code)
            save_db()
            delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": "voltx_search_country", "id": "internal"})
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_nx_srv_name" and text:
            srv = text.strip().upper()
            if "stex_services" not in bot_settings: bot_settings["stex_services"] = {}
            if srv not in bot_settings["stex_services"]: bot_settings["stex_services"][srv] = {}
            save_db()
            delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": "manage_stex_srv", "id": "internal"})
            del user_states[chat_id]
            return

        elif state == "wait_nx_cnt_name" and text:
            cnt = text.strip()
            srv = temp_data[chat_id]["srv"]
            if cnt not in bot_settings["stex_services"][srv]: bot_settings["stex_services"][srv][cnt] = []
            save_db()
            delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": f"nx_srv_{srv}", "id": "internal"})
            del user_states[chat_id]
            return

        elif state == "wait_nx_addr" and text:
            srv, cnt = temp_data[chat_id]["srv"], temp_data[chat_id]["cnt"]
            new_range = text.strip().replace("+", "")
            
            if new_range not in bot_settings["stex_services"][srv][cnt]:
                bot_settings["stex_services"][srv][cnt].append(new_range)
                
                if "search_countries" not in bot_settings:
                    bot_settings["search_countries"] = []
                if new_range not in bot_settings["search_countries"]:
                    bot_settings["search_countries"].append(new_range)
                    
                save_db()
                
            delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": f"nx_cnt_{srv}_{cnt}", "id": "internal"})
            del user_states[chat_id]
            return

        elif state == "wait_vx_srv_name" and text:
            srv = text.strip().upper()
            if "voltx_services" not in bot_settings: bot_settings["voltx_services"] = {}
            if srv not in bot_settings["voltx_services"]: bot_settings["voltx_services"][srv] = {}
            save_db()
            delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": "manage_voltx_srv", "id": "internal"})
            del user_states[chat_id]
            return

        elif state == "wait_vx_cnt_name" and text:
            cnt = text.strip()
            srv = temp_data[chat_id]["srv"]
            if cnt not in bot_settings["voltx_services"][srv]: bot_settings["voltx_services"][srv][cnt] = []
            save_db()
            delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": f"vx_srv_{srv}", "id": "internal"})
            del user_states[chat_id]
            return

        elif state == "wait_vx_addr" and text:
            srv, cnt = temp_data[chat_id]["srv"], temp_data[chat_id]["cnt"]
            new_range = text.strip().replace("+", "")
            
            if new_range not in bot_settings["voltx_services"][srv][cnt]:
                bot_settings["voltx_services"][srv][cnt].append(new_range)
                
                if "voltx_search_countries" not in bot_settings:
                    bot_settings["voltx_search_countries"] = []
                if new_range not in bot_settings["voltx_search_countries"]:
                    bot_settings["voltx_search_countries"].append(new_range)
                    
                save_db()
                
            delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": f"vx_cnt_{srv}_{cnt}", "id": "internal"})
            del user_states[chat_id]
            return

        elif state == "wait_for_add_wm" and text:
            bot_settings["w_methods"].append(text.strip())
            save_db()
            delete_message(chat_id, msg["message_id"])
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text("💳 <b>WITHDRAWAL METHODS</b>\n\nManage your withdrawal methods below:"), reply_markup=w_methods_keyboard())
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_add_gen_key" and text:
            p_name = temp_data[chat_id]["pnl"]
            bot_settings[f"{p_name}_keys"].append(text.strip()); save_db(); delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": f"{p_name}_control", "id": "internal"})
            del user_states[chat_id]; del temp_data[chat_id]; return

        elif state == "wait_for_add_gsc" and text:
            p_name = temp_data[chat_id]["pnl"]; code = text.strip().replace("+", "")
            if f"{p_name}_search_countries" not in bot_settings: bot_settings[f"{p_name}_search_countries"] = []
            bot_settings[f"{p_name}_search_countries"].append(code); save_db(); delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": f"manage_sc_{p_name}", "id": "internal"})
            del user_states[chat_id]; del temp_data[chat_id]; return

        elif state == "wait_gadd_srv" and text:
            p_name = temp_data[chat_id]["pnl"]; srv = text.strip().upper()
            if f"{p_name}_services" not in bot_settings: bot_settings[f"{p_name}_services"] = {}
            if srv not in bot_settings[f"{p_name}_services"]: bot_settings[f"{p_name}_services"][srv] = {}
            save_db(); delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": f"manage_srv_{p_name}", "id": "internal"})
            del user_states[chat_id]; del temp_data[chat_id]; return

        elif state == "wait_gadd_cnt" and text:
            p_name = temp_data[chat_id]["pnl"]; srv = temp_data[chat_id]["srv"]; cnt = text.strip()
            if cnt not in bot_settings[f"{p_name}_services"][srv]: bot_settings[f"{p_name}_services"][srv][cnt] = []
            save_db(); delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": f"gsrv_{p_name}_{srv}", "id": "internal"})
            del user_states[chat_id]; del temp_data[chat_id]; return

        elif state == "wait_gadd_rng" and text:
            p_name = temp_data[chat_id]["pnl"]; srv = temp_data[chat_id]["srv"]; cnt = temp_data[chat_id]["cnt"]
            new_range = text.strip().replace("+", "")
            if new_range not in bot_settings[f"{p_name}_services"][srv][cnt]:
                bot_settings[f"{p_name}_services"][srv][cnt].append(new_range)
                if new_range not in bot_settings[f"{p_name}_search_countries"]: bot_settings[f"{p_name}_search_countries"].append(new_range)
                save_db()
            delete_message(chat_id, msg["message_id"])
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": temp_data[chat_id]["msg_id"]}, "data": f"gcnt_{p_name}_{srv}_{cnt}", "id": "internal"})
            del user_states[chat_id]; del temp_data[chat_id]; return

        elif state == "wait_for_add_fj" and text:
            bot_settings["fj_channels"].append(parse_chat_id(text))
            save_db()
            delete_message(chat_id, msg["message_id"])
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text("🔗 <b>FORCE JOIN SYSTEM</b>\nManage channels below:\n<i>(Note: For private links, use numeric IDs like -100...)</i>"), reply_markup=fj_settings_keyboard())
            del user_states[chat_id]
            del temp_data[chat_id]
            return
            
        elif state == "wait_for_add_adm" and text:
            if text.isdigit():
                bot_settings["admins"].append(int(text))
                save_db()
            delete_message(chat_id, msg["message_id"])
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text("👥 <b>ADMIN MANAGEMENT</b>\nManage your bot admins below:"), reply_markup=admin_settings_keyboard())
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_add_fw_id" and text:
            bot_settings["fw_groups"].append({"chat_id": text.strip(), "buttons": []})
            save_db()
            delete_message(chat_id, msg["message_id"])
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text("🛡 <b>OTP GROUP MANAGEMENT</b>\nManage settings below:"), reply_markup=otp_groups_list_keyboard())
            del user_states[chat_id]
            del temp_data[chat_id]
            return
            
        elif state == "wait_for_add_fw_btn" and text:
            fw_idx = temp_data[chat_id]["fw_idx"]
            if "-" in text:
                parts = text.split("-", 1)
                btn_text = parts[0].strip()
                btn_url = parts[1].strip()
                
                emoji_id = None
                emoji_char = ""
                for ent in msg.get("entities", []):
                    if ent.get("type") == "custom_emoji":
                        emoji_id = ent.get("custom_emoji_id")
                        offset = ent.get("offset", 0)
                        length = ent.get("length", 0)
                        b_text = text.encode('utf-16-le')
                        emoji_char = b_text[offset*2:(offset+length)*2].decode('utf-16-le')
                        break
                
                if emoji_char:
                    btn_text = btn_text.replace(emoji_char, "").strip()
                    
                btn_data = {"text": btn_text, "url": btn_url}
                if emoji_id:
                    btn_data["icon_custom_emoji_id"] = emoji_id
                    
                bot_settings["fw_groups"][fw_idx]["buttons"].append(btn_data)
                save_db()
            delete_message(chat_id, msg["message_id"])
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text(f"🛡 <b>Manage Group:</b> {bot_settings['fw_groups'][fw_idx]['chat_id']}"), reply_markup=specific_fw_group_keyboard(fw_idx))
            del user_states[chat_id]
            del temp_data[chat_id]
            return
            
        elif state == "wait_for_otp_link" and text:
            bot_settings["otp_link"] = text.strip()
            save_db()
            delete_message(chat_id, msg["message_id"])
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text("🛡 <b>OTP GROUP MANAGEMENT</b>\nManage settings below:"), reply_markup=otp_groups_list_keyboard())
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_panel_name" and text:
            p_name = text.strip()
            t_key = temp_data[chat_id].get("add_type", "api")
            msg_id = temp_data[chat_id]["msg_id"]
            delete_message(chat_id, msg["message_id"])
            
            if t_key == "logc":
                user_states[chat_id] = "wait_for_cpanel_url"
                temp_data[chat_id] = {"msg_id": msg_id, "p_data": {
                    "name": p_name, "type": "Auto Captcha Panel", "status": "ON", "records": 0, "login_status": "⏳ Pending First Login"
                }}
                edit_message(chat_id, msg_id, render_body_text("1️⃣ <b>Login URL</b>\n➡️ Panel এর Login Link দিন:"), reply_markup=get_cancel_kb())
                return
            else:
                bot_settings["panels"].append({
                    "name": p_name, "type": "API Panel", "status": "OFF", "api_url": "", "token": "", "records": 0
                })
                save_db()
                handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": "manage_api_panels", "id": "internal"})
                if chat_id in user_states: del user_states[chat_id]
                if chat_id in temp_data: del temp_data[chat_id]
                return

        elif state == "wait_for_p_api" and text:
            idx = temp_data[chat_id]["p_idx"]
            bot_settings["panels"][idx]["api_url"] = text.strip()
            save_db()
            delete_message(chat_id, msg["message_id"])
            p = bot_settings["panels"][idx]
            ui_text = f"⚙️ <b>Configure {p['name']}</b>\n\n<b>Type:</b> {p['type']}\n<b>Status:</b> {'🟢 Monitoring' if p['status'] == 'ON' else '🔴 Stopped'}\n<b>API URL:</b> <code>{p.get('api_url', 'None')}</code>\n<b>Token:</b> <code>{p.get('token', 'None')}</code>"
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text(ui_text), reply_markup=panel_config_keyboard(idx))
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_p_tok" and text:
            idx = temp_data[chat_id]["p_idx"]
            bot_settings["panels"][idx]["token"] = text.strip()
            save_db()
            delete_message(chat_id, msg["message_id"])
            p = bot_settings["panels"][idx]
            ui_text = f"⚙️ <b>Configure {p['name']}</b>\n\n<b>Type:</b> {p['type']}\n<b>Status:</b> {'🟢 Monitoring' if p['status'] == 'ON' else '🔴 Stopped'}\n<b>API URL:</b> <code>{p.get('api_url', 'None')}</code>\n<b>Token:</b> <code>{p.get('token', 'None')}</code>"
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text(ui_text), reply_markup=panel_config_keyboard(idx))
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_p_fapi" and text:
            idx = temp_data[chat_id]["p_idx"]
            bot_settings["panels"][idx]["full_api_url"] = text.strip()
            save_db()
            delete_message(chat_id, msg["message_id"])
            p = bot_settings["panels"][idx]
            ui_text = f"⚙️ <b>Configure {p['name']}</b>\n\n<b>Type:</b> {p['type']}\n<b>Status:</b> {'🟢 Monitoring' if p['status'] == 'ON' else '🔴 Stopped'}\n<b>API URL:</b> <code>{p.get('api_url', 'None')}</code>\n<b>Full API URL:</b> <code>{p.get('full_api_url', 'None')}</code>"
            edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text(ui_text), reply_markup=panel_config_keyboard(idx))
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_p_rec" and text:
            if text.isdigit():
                idx = temp_data[chat_id]["p_idx"]
                bot_settings["panels"][idx]["records"] = int(text)
                save_db()
                delete_message(chat_id, msg["message_id"])
                p = bot_settings["panels"][idx]
                
                ui_text = f"⚙️ <b>Configure {p['name']}</b>\n\n<b>Type:</b> {p['type']}\n<b>Status:</b> {'🟢 Monitoring' if p['status'] == 'ON' else '🔴 Stopped'}\n<b>API URL:</b> <code>{p.get('api_url', 'None')}</code>\n<b>Token:</b> <code>{p.get('token', 'None')}</code>"
                edit_message(chat_id, temp_data[chat_id]["msg_id"], render_body_text(ui_text), reply_markup=panel_config_keyboard(idx))
            else:
                send_message(chat_id, render_body_text("❌ Please enter a valid number! Try again."), reply_markup=get_cancel_kb())
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "set_tre":
            msg_id = temp_data[chat_id]["msg_id"]
            key = temp_data[chat_id]["key"]
            try:
                if key in ["min_withdraw", "otp_reward", "refer_reward"]: bot_settings[key] = float(text)
                elif key in ["cooldown", "num_req", "num_share"]: bot_settings[key] = int(text)
                else: bot_settings[key] = text
                save_db()
                delete_message(chat_id, msg["message_id"])
                edit_message(chat_id, msg_id, render_body_text("🕹 <b>TRE CONTROL PANEL</b>"), reply_markup=tre_control_keyboard())
            except:
                delete_message(chat_id, msg["message_id"])
                edit_message(chat_id, msg_id, render_body_text("🕹 <b>TRE CONTROL PANEL</b>\n\n❌ Invalid value!"), reply_markup=tre_control_keyboard())
            del user_states[chat_id]
            del temp_data[chat_id]
            return

        elif state == "wait_for_search" and text:
            query = text.strip().replace("+", "")
            if not query.isdigit() or len(query) < 3 or len(query) > 9:
                send_message(chat_id, render_body_text("❌ Please enter a valid 3 to 9 digit number!"))
                return
                
            wait_msg = send_message(chat_id, render_body_text("⌛ <i>Processing... Finding Number...</i>"))
            wait_msg_id = wait_msg.get("result", {}).get("message_id")
            
            # 🌟 ১. প্রথমে Local থেকে নাম্বার খুঁজবে (যে কোনো দেশের জন্য)
            found_indices = []
            for b_id, b_data in number_batches.items():
                for idx, n_obj in enumerate(b_data["numbers"]):
                    if n_obj["num"].replace("+", "").startswith(query) and chat_id not in n_obj.get("used_by", []):
                        found_indices.append((b_id, idx))
            
            fetched_nums = []
            if not found_indices:
                # 🌟 ২. যদি Local এ না পায়, তখন চেক করবে Voltx বা StexSMS থেকে আনা যাবে কি না
                stex_allowed = bot_settings.get("search_countries", [])
                voltx_allowed = bot_settings.get("voltx_search_countries", [])
                
                # ফিক্স: অ্যাডমিন প্যানেলে দেশ এড না থাকলে অটোমেটিক Block করে দেবে
                is_stex_allowed = any(query.startswith(c) for c in stex_allowed) if stex_allowed else False
                is_voltx_allowed = any(query.startswith(c) for c in voltx_allowed) if voltx_allowed else False
                
                if not is_stex_allowed and not is_voltx_allowed:
                    if wait_msg_id: delete_message(chat_id, wait_msg_id)
                    send_message(chat_id, render_body_text(f"📡 <b>No numbers found matching {query}</b>"))
                    return
                    
                if wait_msg_id: edit_message(chat_id, wait_msg_id, render_body_text("⌛ <i>Processing... Finding Number via API...</i>"))
                
                is_voltx_used = False
                req_count = bot_settings.get("num_req", 1)
                
                # 🌟 প্রথমে Voltx চেক করবে
                if is_voltx_allowed:
                    voltx_keys = bot_settings.get("voltx_keys", [])
                    for _ in range(req_count):
                        if len(fetched_nums) >= req_count: break
                        for api_key in voltx_keys:
                            try:
                                headers = {"mauthapi": api_key}
                                res = requests.post(f"{VOLTX_BASE_URL}/getnum", json={"rid": query}, headers=headers, timeout=10)
                                resp_data = res.json()
                                if resp_data.get("meta", {}).get("code") == 200 and resp_data.get("data"):
                                    num_str = str(resp_data["data"].get("no_plus_number", "")).replace("+", "")
                                    if not num_str: num_str = str(resp_data["data"].get("national_number", ""))
                                    fetched_nums.append(num_str)
                                    voltx_assigned_numbers[num_str] = chat_id 
                                    is_voltx_used = True
                                    global total_assigned_stats
                                    total_assigned_stats += 1
                                    break # শুধু api_key লুপ ব্রেক করবে, যাতে পরের নাম্বার আনতে পারে
                            except: continue

                # 🌟 Voltx এ না পেলে বা আরও নাম্বার লাগলে StexSMS তে চেক করবে
                if len(fetched_nums) < req_count and is_stex_allowed:
                    stex_keys = bot_settings.get("stex_keys", [])
                    
                    for _ in range(req_count - len(fetched_nums)): 
                        for api_key in stex_keys:
                            try:
                                headers = {"mauthapi": api_key}
                                res = requests.post(f"{STEX_BASE_URL}/getnum", json={"rid": query}, headers=headers, timeout=10)
                                data = res.json()
                                if data.get("meta", {}).get("code") == 200 and data.get("data"):
                                    num_str = str(data["data"].get("no_plus_number", "")).replace("+", "")
                                    if not num_str: num_str = str(data["data"].get("national_number", ""))
                                    fetched_nums.append(num_str)
                                    stex_assigned_numbers[num_str] = chat_id 
                                    total_assigned_stats += 1
                                    break 
                            except: continue
                        
                if not fetched_nums:
                    if wait_msg_id: delete_message(chat_id, wait_msg_id)
                    send_message(chat_id, render_body_text(f"📡 <b>No numbers found matching {query}</b>"))
                    return
                save_db()
            else:
                random.shuffle(found_indices)
                for b_id, idx in found_indices:
                    if len(fetched_nums) >= bot_settings.get("num_req", 1): break
                    n_obj = number_batches[b_id]["numbers"][idx]
                    num_str = n_obj["num"]
                    
                    fetched_nums.append(num_str)
                    
                    n_obj["shares"] += 1
                    n_obj["used_by"].append(chat_id)
                    total_assigned_stats += 1
                    
                    if n_obj["shares"] >= bot_settings.get("num_share", 1):
                        n_obj["to_remove"] = True
                        used_numbers_list.append(num_str)
                
                for b_id in number_batches:
                    number_batches[b_id]["numbers"] = [n for n in number_batches[b_id]["numbers"] if not n.get("to_remove")]
                save_db()
                
            vtx_ext = "_vtx" if 'is_voltx_used' in locals() and is_voltx_used else ""
            text_numbers, kb = _number_card(chat_id, fetched_nums, None, f"c_n_s_{query}{vtx_ext}",
                                            bot_settings["custom_messages"].get("search_number", {}).get("buttons", []))
            if wait_msg_id:
                edit_message(chat_id, wait_msg_id, text_numbers, reply_markup={"inline_keyboard": kb})
                user_active_sessions[chat_id] = {"msg_id": wait_msg_id, "nums": fetched_nums, "service": None}
            else:
                msg_res = send_message(chat_id, text_numbers, reply_markup={"inline_keyboard": kb})
                if msg_res and "result" in msg_res:
                    user_active_sessions[chat_id] = {"msg_id": msg_res["result"]["message_id"], "nums": fetched_nums, "service": None}
            if bot_settings.get("ws_on", True): ws_checker.decorate(chat_id, fetched_nums, kb, text_numbers)
            return

        elif state == "wait_for_withdraw_amount" and text:
            msg_id_to_edit = temp_data[chat_id].get("msg_id")
            try:
                amount = float(text.strip())
                bal = temp_data[chat_id]["balance"]
                min_w = bot_settings['min_withdraw']
                
                if amount < min_w:
                    if msg_id_to_edit: edit_message(chat_id, msg_id_to_edit, render_body_text(f"❌ Minimum withdrawal is {min_w} ৳!\n💰 Balance: {bal} ৳\n\n📝 Enter again:"), reply_markup=get_cancel_kb())
                    return
                if amount > bal:
                    if msg_id_to_edit: edit_message(chat_id, msg_id_to_edit, render_body_text(f"❌ You don't have enough balance!\n💰 Balance: {bal} ৳\n\n📝 Enter again:"), reply_markup=get_cancel_kb())
                    return
                    
                temp_data[chat_id]["amount"] = amount
                user_states[chat_id] = "wait_for_withdraw_number"
                if msg_id_to_edit:
                    edit_message(chat_id, msg_id_to_edit, render_body_text(f"✅ Amount: {amount} ৳\n\n📱 Now send your <b>{temp_data[chat_id]['method']}</b> account number:"), reply_markup=get_cancel_kb())
            except ValueError:
                if msg_id_to_edit: edit_message(chat_id, msg_id_to_edit, render_body_text("❌ Invalid amount!\n\n📝 Please send a valid number:"), reply_markup=get_cancel_kb())
            return
            
        elif state == "wait_for_2fa_key" and text:
            msg_id_to_edit = temp_data.get(chat_id, {}).get("msg_id")
            delete_message(chat_id, msg.get("message_id")) # ইউজারের মেসেজ ডিলিট

            if not msg_id_to_edit:
                send_message(chat_id, render_body_text("❌ Error: Message not found. Try again."))
                del user_states[chat_id]
                return

            try:
                secret = text.strip().replace(" ", "")
                totp = pyotp.TOTP(secret)
                code = totp.now()
                remaining_time = 30 - (int(time.time()) % 30)
                
                success_txt = (
                    f"━━━━━━━━━━━━━━━\n"
                    f"《 🔐 <b>2FA CODE</b> 》\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"🔐 <b>CODE:</b> <code>{code}</code>\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"🕓 <b>EXPIRES IN:</b> {remaining_time}s\n"
                    f"━━━━━━━━━━━━━━━"
                )
                kb = [[{"text": f"Click to copy {code}", "icon_custom_emoji_id": "5353022963132174959", "copy_text": {"text": code}, "style": "success"}],
                      [{"text": "Refresh", "icon_custom_emoji_id": "5420155432272438703", "callback_data": f"ref_2fa_{secret}", "style": "primary"},
                       {"text": "New Code", "icon_custom_emoji_id": "5352552689983067014", "callback_data": "gen_2fa", "style": "danger"}],
                      [{"text": "Close", "icon_custom_emoji_id": "5420130255174145507", "callback_data": "close_msg", "style": "danger"}]]
                
                edit_message(chat_id, msg_id_to_edit, render_body_text(success_txt), reply_markup={"inline_keyboard": kb})
                del user_states[chat_id]
                if chat_id in temp_data: del temp_data[chat_id]
            except Exception:
                error_txt = "━━━━━━━━━━━━━━━\n《 🔑 <b>ENTER 2FA KEY</b> 》\n━━━━━━━━━━━━━━━\n📝 <b>SEND YOUR 2FA SECRET KEY</b>\n━━━━━━━━━━━━━━━\n❌ <b>Invalid Secret Key! Try again.</b>\n━━━━━━━━━━━━━━━"
                cancel_kb = {"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "cancel_2fa", "style": "danger"}]]}
                edit_message(chat_id, msg_id_to_edit, render_body_text(error_txt), reply_markup=cancel_kb)
            return

        elif state == "wait_for_withdraw_number":
            msg_id_to_edit = temp_data[chat_id].get("msg_id")
            
            method = temp_data[chat_id]["method"]
            amount = temp_data[chat_id]["amount"]
            number = text
            req_id = f"W_{str(uuid.uuid4())[:6].upper()}"
            
            first_name = msg.get("from", {}).get("first_name", "User")
            last_name = msg.get("from", {}).get("last_name", "")
            full_name = f"{first_name} {last_name}".strip()
            
            update_balance(chat_id, -amount)
            pending_withdrawals[req_id] = {"user_id": chat_id, "amount": amount, "method": method, "number": number, "full_name": full_name}
            
            # Save to Firestore for History
            if db:
                try:
                    db.collection('withdrawals').document(req_id).set({
                        "user_id": str(chat_id),
                        "amount": amount,
                        "method": method,
                        "status": "pending",
                        "timestamp": firestore.SERVER_TIMESTAMP
                    })
                except: pass
                
            if bot_settings["w_group"]:
                admin_msg = f"🎙 <b>NEW WITHDRAWAL REQUEST</b>\n\n👤 <b>USER:</b> <a href='tg://user?id={chat_id}'>{full_name}</a>\n💳 <b>WITHDRAWAL:</b> {amount} TK\n🎟️ <b>NUMBER:</b> <code>{number}</code>\n🍍 <b>METHOD:</b> {method}\n\n🧾 <b>REQ ID:</b> {req_id}\n👨‍⚖️ <b>PROCESSED BY ADMIN</b>"
                kb = {"inline_keyboard": [[{"text": "APPROVE", "icon_custom_emoji_id": "5352694861990501856", "callback_data": f"wapp_{req_id}", "style": "success"}, {"text": "REJECT", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"wrej_{req_id}", "style": "danger"}]]}
                send_message(bot_settings["w_group"], render_body_text(admin_msg), reply_markup=kb)
            
            kb = {"inline_keyboard": [[{"text": "Close", "icon_custom_emoji_id": "5420130255174145507", "callback_data": "close_msg", "style": "danger"}]]}
            success_text = f"{PEM['ok']} Your withdrawal request has been submitted!\n\n🧾 <b>Req ID:</b> {req_id}\n💰 <b>Amount:</b> {amount} ৳\n🍍 <b>Method:</b> {method}\n🎟️ <b>Number:</b> <code>{number}</code>"
            
            if msg_id_to_edit:
                edit_message(chat_id, msg_id_to_edit, render_body_text(success_text), reply_markup=kb)
            else:
                send_message(chat_id, render_body_text(success_text), reply_markup=kb)
                
            del user_states[chat_id]
            del temp_data[chat_id]
            return

    # --- Regular Commands ---
    if text.startswith("/start"):
        get_user(chat_id)
        
        # --- PROCESS PENDING REFERRAL ---
        if db:
            doc = db.collection('users').document(str(chat_id)).get()
            if doc.exists:
                u_data = doc.to_dict()
                if u_data.get("referred_by") and not u_data.get("ref_paid"):
                    inviter = u_data["referred_by"]
                    db.collection('users').document(str(chat_id)).update({"ref_paid": True})
                    reward = bot_settings.get("refer_reward", 0.2)
                    update_balance(inviter, reward)
                    db.collection('users').document(str(inviter)).update({"total_refers": firestore.Increment(1)})
                    ref_msg = (
                        f"{PEM['gift']} <b>New Referral !</b>\n"
                        f"------------------\n"
                        f"🔥 <b>You Received {reward} TK</b>\n"
                        f"------------------\n"
                        f"{PEM['user']} <b>From User ID:</b> <code>{chat_id}</code>"
                    )
                    send_message(inviter, render_body_text(ref_msg))
                    
        send_message(chat_id, "•", reply_markup=main_menu(chat_id))
        _send_service_list(chat_id)

    elif text == "TRAFFIC":
        txt, markup = build_traffic_ui()
        send_message(chat_id, txt, reply_markup=markup)
        
    elif text == "Refer":
        u_data = get_user(chat_id)
        ref_link = f"https://t.me/{BOT_USERNAME}?start={chat_id}"
        c_msg = bot_settings["custom_messages"].get("refer", {})
        
        raw_txt = c_msg.get("text", f"{PEM['gift']} Refer").replace("{ref_link}", ref_link).replace("{total_ref}", str(u_data.get('total_refers', 0))).replace("{ref_reward}", str(bot_settings['refer_reward']))
        txt = render_body_text(raw_txt)
        
        kb = [[{"text": "COPY LINK", "icon_custom_emoji_id": "6169933851729600414", "copy_text": {"text": ref_link}, "style": "success"}]]
        for b in c_msg.get("buttons", []): 
            b_copy = b.copy()
            if "style" not in b_copy: b_copy["style"] = "primary"
            kb.append([b_copy])
        kb.append([{"text": "CLOSE", "icon_custom_emoji_id": "5420130255174145507", "callback_data": "close_msg", "style": "danger"}])
        
        send_message(chat_id, txt, reply_markup={"inline_keyboard": kb})

    elif text == "WITHDRAWAL":
        if not bot_settings["withdraw_on"]:
            send_message(chat_id, render_body_text(f"{PEM['no']} Withdrawals are currently disabled."))
            return
        
        u_data = get_user(chat_id)
        bal = u_data.get('balance', 0.0)
        
        c_msg = bot_settings["custom_messages"].get("withdrawal", {})
        raw_txt = c_msg.get("text", "Withdrawal").replace("{bal}", str(bal)).replace("{total_otp}", str(u_data.get('total_otps', 0))).replace("{total_ref}", str(u_data.get('total_refers', 0))).replace("{min_w}", str(bot_settings['min_withdraw']))
        txt = render_body_text(raw_txt)
        
        kb = []
        for m in bot_settings["w_methods"]:
            emoji_id = get_w_method_emoji(m.strip())
            kb.append([{"text": m.strip(), "icon_custom_emoji_id": emoji_id, "callback_data": f"sel_wm_{m.strip()}", "style": "primary"}])
        
        for b in c_msg.get("buttons", []): 
            b_copy = b.copy()
            if "style" not in b_copy: b_copy["style"] = "primary"
            kb.append([b_copy])
        kb.append([{"text": "Cancel", "icon_custom_emoji_id": "5420130255174145507", "callback_data": "close_msg", "style": "danger"}])
        send_message(chat_id, txt, reply_markup={"inline_keyboard": kb})

    elif text == "Admin Panel" and is_admin(chat_id):
        send_message(chat_id, get_admin_text(), reply_markup=admin_panel_keyboard())

    elif text == "GET NUMBER":
        _send_service_list(chat_id)

    elif text == "Search Number":
        user_states[chat_id] = "wait_for_search"
        txt = render_body_text("🔍 <b>SEARCH NUMBER</b>\n━━━━━━━━━━━━━━━━━━\n💬 <b>Enter number with country code (e.g. +88015 or +919)</b>")
        send_message(chat_id, txt, reply_markup=search_reply_kb())

    elif text == "MY ACCOUNT":
        txt, kb = build_account_ui(chat_id, msg.get("from"))
        send_message(chat_id, txt, reply_markup=kb)

    elif text == "TOP USERS":
        txt, kb = build_leaderboard_ui("today")
        send_message(chat_id, txt, reply_markup=kb)

    elif text == "2FA ONLINE" or text == "🔐 2FA ONLINE":
        txt = "━━━━━━━━━━━━━━━\n《 🔐 <b>2FA ONLINE</b> 》\n━━━━━━━━━━━━━━━\n<i>Generate your 2FA security code instantly using your secret key.</i>\n━━━━━━━━━━━━━━━"
        kb = [[{"text": "Generate 2fa code", "icon_custom_emoji_id": "5353022963132174959", "callback_data": "gen_2fa", "style": "success"}],
              [{"text": "Close", "icon_custom_emoji_id": "5420130255174145507", "callback_data": "close_msg", "style": "danger"}]]
        send_message(chat_id, render_body_text(txt), reply_markup={"inline_keyboard": kb})

    elif text == "SUPPORT":
        c_msg = bot_settings["custom_messages"].get("support", {})
        txt = render_body_text(c_msg.get("text", f"{PEM['msg']} Support"))
        if not txt.strip(): txt = render_body_text(f"{PEM['msg']} Support")
        kb = []
        for b in c_msg.get("buttons", []):
            b_copy = b.copy()
            if "style" not in b_copy: b_copy["style"] = "primary"
            kb.append([b_copy])
            
        sup_link = bot_settings.get("support_link", "")
        if sup_link:
            kb.insert(0, [{"text": "Contact Support", "icon_custom_emoji_id": "5337302974806922068", "url": sup_link, "style": "success"}])
            
        kb.append([{"text": "Close", "icon_custom_emoji_id": "5420130255174145507", "callback_data": "close_msg", "style": "danger"}])
        send_message(chat_id, txt, reply_markup={"inline_keyboard": kb} if kb else None)

def expire_previous_number(chat_id):
    if chat_id in user_active_sessions:
        prev_data = user_active_sessions[chat_id]
        prev_msg_id = prev_data["msg_id"]
        
        kb = [[{"text": "Number Expired (But still active for OTP)", "icon_custom_emoji_id": "5336997731481193790", "callback_data": "ignore", "style": "danger"}]]
        try:
            edit_message(chat_id, prev_msg_id, "NEW NUMBER\n", reply_markup={"inline_keyboard": kb})
        except:
            pass
        del user_active_sessions[chat_id]

# ==========================================
# Callback Query Handler
# ==========================================
def handle_callback(call):
    global total_assigned_stats
    chat_id = call["message"]["chat"]["id"]
    chat_type = call["message"]["chat"].get("type", "private")
    data = call.get("data", "")

    # 🌟 Button Loading Fix: বাটন চাপার সাথে সাথেই টেলিগ্রামকে Response দিয়ে দেওয়া, যাতে বাটন আটকে না থাকে!
    if (not data.startswith("test_p_conn_") and not data.startswith("c_n_") and not data.startswith("g_c_")
            and not data.startswith("g_p_") and not data.startswith("lb_ref_") and data != "refresh_traffic"
            and data != "acc_withdraw"):
        try: _aux_pool.submit(answer_callback, call["id"])
        except: pass

    if chat_type != "private" and not (data.startswith("wapp_") or data.startswith("wrej_")):
        return

    msg_id = call["message"]["message_id"]

    if chat_type == "private":
        if is_user_banned(chat_id):
            answer_callback(call["id"], "🚫 You are banned from using this bot!", show_alert=True)
            return

        if not check_force_join(chat_id) and data != "check_fj":
            send_force_join_msg(chat_id)
            return

    if data.startswith("ws_"):
        if not bot_settings.get("ws_on", True):
            edit_message(chat_id, msg_id, render_body_text("⛔ <b>WS Checker is currently disabled by admin.</b>"))
            return
        if ws_checker.handle_callback(call):
            return

    if _video_callback(call, chat_id, msg_id, data):
        return

    if data == "check_fj":
        if check_force_join(chat_id):
            delete_message(chat_id, msg_id)
            send_message(chat_id, render_body_text(f"{PEM['ok']} Thanks for joining! You can now use the bot."), reply_markup=main_menu(chat_id))
            
            # --- PROCESS PENDING REFERRAL ---
            if db:
                doc = db.collection('users').document(str(chat_id)).get()
                if doc.exists:
                    u_data = doc.to_dict()
                    if u_data.get("referred_by") and not u_data.get("ref_paid"):
                        inviter = u_data["referred_by"]
                        db.collection('users').document(str(chat_id)).update({"ref_paid": True})
                        reward = bot_settings.get("refer_reward", 0.2)
                        update_balance(inviter, reward)
                        db.collection('users').document(str(inviter)).update({"total_refers": firestore.Increment(1)})
                        ref_msg = (
                            f"{PEM['gift']} <b>New Referral !</b>\n"
                            f"------------------\n"
                            f"🔥 <b>You Received {reward} TK</b>\n"
                            f"------------------\n"
                            f"{PEM['user']} <b>From User ID:</b> <code>{chat_id}</code>"
                        )
                        send_message(inviter, render_body_text(ref_msg))
        else:
            answer_callback(call["id"], "❌ You haven't joined all channels yet!", show_alert=True)
        return

    if data.startswith("toggle_rm_cc"):
        u_data = get_user(chat_id)
        current_status = u_data.get("remove_cc", False)
        new_status = not current_status
        user_cache[chat_id]["remove_cc"] = new_status
        if db:
            try: db.collection('users').document(str(chat_id)).update({"remove_cc": new_status})
            except: pass
            
        # কুলডাউন বা নতুন নাম্বার না ডেকে, সরাসরি বর্তমান মেসেজের বাটন আপডেট করা হবে
        if chat_id in user_active_sessions:
            fetched_nums = user_active_sessions[chat_id].get("nums", [])
            current_kb = call["message"].get("reply_markup", {}).get("inline_keyboard", [])
            
            num_idx = 0
            for row in current_kb:
                for btn in row:
                    if "toggle_rm_cc" in btn.get("callback_data", ""):
                        btn["text"] = "Remove CC"
                        btn["icon_custom_emoji_id"] = "6170404567260341871"
                    elif "copy_text" in btn: 
                        if num_idx < len(fetched_nums):
                            raw_num = fetched_nums[num_idx]
                            display_num = get_display_number(raw_num, new_status)
                            btn["text"] = str(display_num)
                            btn["copy_text"]["text"] = str(display_num)
                            num_idx += 1
                            
            api_call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id, "reply_markup": {"inline_keyboard": current_kb}})
            
        answer_callback(call["id"], f"Remove CC is now {'ON' if new_status else 'OFF'}!", show_alert=False)
        return

    elif data == "close_msg":
        delete_message(chat_id, msg_id)
        
    elif data == "cancel_state":
        if chat_id in user_states: del user_states[chat_id]
        if chat_id in temp_data: del temp_data[chat_id]
        delete_message(chat_id, msg_id)

    elif data == "cancel_2fa":
        if chat_id in user_states: del user_states[chat_id]
        if chat_id in temp_data: del temp_data[chat_id]
        txt = "━━━━━━━━━━━━━━━\n《 🔐 <b>2FA ONLINE</b> 》\n━━━━━━━━━━━━━━━\n<i>Generate your 2FA security code instantly using your secret key.</i>\n━━━━━━━━━━━━━━━"
        kb = [[{"text": "Generate 2fa code", "icon_custom_emoji_id": "5353022963132174959", "callback_data": "gen_2fa", "style": "success"}],
              [{"text": "Close", "icon_custom_emoji_id": "5420130255174145507", "callback_data": "close_msg", "style": "danger"}]]
        edit_message(chat_id, msg_id, render_body_text(txt), reply_markup={"inline_keyboard": kb})
        answer_callback(call["id"])

    elif data == "gen_2fa":
        user_states[chat_id] = "wait_for_2fa_key"
        temp_data[chat_id] = {"msg_id": msg_id}
        txt = "━━━━━━━━━━━━━━━\n《 🔑 <b>ENTER 2FA KEY</b> 》\n━━━━━━━━━━━━━━━\n📝 <b>SEND YOUR 2FA SECRET KEY</b>\n━━━━━━━━━━━━━━━"
        kb = {"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "cancel_2fa", "style": "danger"}]]}
        edit_message(chat_id, msg_id, render_body_text(txt), reply_markup=kb)
        answer_callback(call["id"])

    elif data.startswith("ref_2fa_"):
        secret = data.replace("ref_2fa_", "")
        try:
            totp = pyotp.TOTP(secret)
            code = totp.now()
            remaining_time = 30 - (int(time.time()) % 30)
            
            success_txt = (
                f"━━━━━━━━━━━━━━━\n"
                f"《 🔐 <b>2FA CODE</b> 》\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🔐 <b>CODE:</b> <code>{code}</code>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🕓 <b>EXPIRES IN:</b> {remaining_time}s\n"
                f"━━━━━━━━━━━━━━━"
            )
            kb = [[{"text": f"Click to copy {code}", "icon_custom_emoji_id": "5353022963132174959", "copy_text": {"text": code}, "style": "success"}],
                  [{"text": "Refresh", "icon_custom_emoji_id": "5420155432272438703", "callback_data": f"ref_2fa_{secret}", "style": "primary"},
                   {"text": "New Code", "icon_custom_emoji_id": "5352552689983067014", "callback_data": "gen_2fa", "style": "danger"}],
                  [{"text": "Close", "icon_custom_emoji_id": "5420130255174145507", "callback_data": "close_msg", "style": "danger"}]]
            
            edit_message(chat_id, msg_id, render_body_text(success_txt), reply_markup={"inline_keyboard": kb})
        except:
            answer_callback(call["id"], "❌ Error refreshing code!", show_alert=True)

    elif data == "cancel_tre_edit":
        if chat_id in user_states: del user_states[chat_id]
        if chat_id in temp_data: del temp_data[chat_id]
        edit_message(chat_id, msg_id, render_body_text("🕹 <b>TRE CONTROL PANEL</b>"), reply_markup=tre_control_keyboard())
        
    elif data == "dummy_alert":
        answer_callback(call["id"], "This feature will be added later!", show_alert=True)
        
    elif data == "refresh_traffic":
        txt, markup = build_traffic_ui()
        edit_message(chat_id, msg_id, txt, reply_markup=markup)
        answer_callback(call["id"], "✅ Traffic Report Updated!", show_alert=False)

    elif data.startswith("exp_rng_"):
        srv_query = data.replace("exp_rng_", "")
        
        country_stats = {}
        current_time = time.time()
        for t in recent_traffic:
            if current_time - t.get("time", 0) <= 3600:
                if t.get("service", "").startswith(srv_query):
                    iso = t.get("iso", "XX")
                    flag = t.get("flag", "🌍")
                    if iso not in country_stats:
                        country_stats[iso] = {"count": 0, "flag": flag}
                    country_stats[iso]["count"] += 1
        
        if not country_stats:
            answer_callback(call["id"], "❌ No recent traffic found for this service!", show_alert=True)
            return
            
        kb = []
        for iso, c_data in sorted(country_stats.items(), key=lambda x: x[1]["count"], reverse=True):
            count = c_data["count"]
            c_name = iso
            emoji_id = "5780471598922337683"
            for code, fdata in bot_settings.get("premium_flags", {}).items():
                if fdata.get("iso") == iso:
                    c_name = fdata.get("name", iso)
                    if "id" in fdata: emoji_id = fdata["id"]
                    break
            
            btn_text = f"{c_name} ({iso}) - {count} OTP"
            kb.append([{"text": btn_text, "icon_custom_emoji_id": emoji_id, "callback_data": f"exp_c_{srv_query}_{iso}", "style": "primary"}])
            
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "refresh_traffic", "style": "danger"}])
        
        app_full_name, prem_app_html = get_service_info_html(srv_query)
        edit_message(chat_id, msg_id, render_body_text(f"📊 <b>Explore Service: {prem_app_html} {app_full_name}</b>\n\nSelect a country to view available ranges:"), reply_markup={"inline_keyboard": kb})
        answer_callback(call["id"])

    elif data.startswith("exp_c_"):
        parts = data.split("_")
        srv_query = parts[2]
        iso_query = parts[3]
        
        nums = []
        current_time = time.time()
        for t in recent_traffic:
            if current_time - t.get("time", 0) <= 3600:
                if t.get("service", "").startswith(srv_query) and t.get("iso") == iso_query:
                    # 🌟 X রিমুভ করে ক্লিন রেঞ্জ নেওয়া হলো
                    num = t.get("real_range", t.get("number", "").replace("X", "").replace("x", "")).replace("+", "").strip()
                    if num: nums.append(num)
        
        if not nums:
            answer_callback(call["id"], "❌ No recent numbers found for this country!", show_alert=True)
            return
            
        # শুধুমাত্র StexSMS Services থেকে রেঞ্জ নিবো (Search Countries নিবো না, কারণ ওগুলোতে শুধু দেশের কোড থাকে)
        known_ranges = set()
        for s_name, c_dict in bot_settings.get("stex_services", {}).items():
            for c_name, r_list in c_dict.items():
                for r in r_list:
                    # 🌟 এখানেও সেভ করা রেঞ্জ থেকে X সরিয়ে নিচ্ছি
                    known_ranges.add(r.replace("X", "").replace("x", ""))
                    
        sorted_known = sorted(list(known_ranges), key=len, reverse=True)
        
        r_counts = Counter()
        for num in nums:
            matched = False
            for r in sorted_known:
                if num.startswith(r):
                    r_counts[r] += 1
                    matched = True
                    break
            if not matched:
                if len(num) >= 7:
                    r_counts[num[:7]] += 1
                else:
                    r_counts[num] += 1
                    
        r_list = r_counts.most_common(12)
        
        kb = []
        temp_row = []
        for r, count in r_list:
            # 🌟 বাটনে এবং কলব্যাকে যাতে কোনো X না থাকে তা নিশ্চিত করা হলো
            clean_r = str(r).replace("X", "").replace("x", "")
            temp_row.append({"text": f"{clean_r} ({count})", "icon_custom_emoji_id": "5352862640592949843", "callback_data": f"c_n_s_{clean_r}_{srv_query}", "style": "primary"})
            if len(temp_row) == 2:
                kb.append(temp_row)
                temp_row = []
        if temp_row:
            kb.append(temp_row)
            
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"exp_rng_{srv_query}", "style": "danger"}])
        
        app_full_name, prem_app_html = get_service_info_html(srv_query)
        prem_flag_html = get_flag_info_html(iso_query)
        
        edit_message(chat_id, msg_id, render_body_text(f"📊 <b>Ranges for {prem_app_html} {app_full_name} - {prem_flag_html} {iso_query}</b>\n\nClick on any range to copy it."), reply_markup={"inline_keyboard": kb})
        answer_callback(call["id"])

    # --- User Management Flows Integration ---
    elif data == "user_management":
        edit_message(chat_id, msg_id, get_user_management_text(), reply_markup=user_management_keyboard())

    elif data == "um_manage_balance":
        user_states[chat_id] = "wait_for_um_bal_uid"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the User ID to Manage Balance:"), reply_markup=get_cancel_kb())
        
    elif data == "um_ban_unban":
        user_states[chat_id] = "wait_for_um_ban_uid"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the User ID to Ban or Unban:"), reply_markup=get_cancel_kb())

    elif data == "um_user_profile":
        user_states[chat_id] = "wait_for_um_prof_uid"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the User ID to View Profile:"), reply_markup=get_cancel_kb())

    # --- Menu Design Integration ---
    elif data == "menu_design_list":
        edit_message(chat_id, msg_id, render_body_text(f"🎨 <b>Menu Design Editor</b>\n\nSelect a menu block to edit its Body Text and Inline Buttons. You can use Premium Emojis too!"), reply_markup=menu_design_list_keyboard())

    elif data == "md_reset_defaults":
        bot_settings["custom_messages"] = DEFAULT_CUSTOM_MESSAGES.copy()
        save_db()
        answer_callback(call["id"], "✅ Resetted to Premium Defaults!", show_alert=True)

    elif data.startswith("md_edit_"):
        answer_callback(call["id"])
        if chat_id in user_states: del user_states[chat_id]
        if chat_id in temp_data: del temp_data[chat_id]
        key = data.replace("md_edit_", "")
        cm_text = render_body_text(bot_settings["custom_messages"].get(key, {}).get("text", "..."))
        try:
            edit_message(chat_id, msg_id, render_body_text(f"🎨 <b>Editing: {key.upper()}</b>\n\nPreview of current Text:\n{cm_text}"), reply_markup=menu_edit_options_keyboard(key))
        except: pass

    elif data.startswith("md_text_"):
        key = data.replace("md_text_", "")
        user_states[chat_id] = "wait_for_menu_text"
        temp_data[chat_id] = {"msg_id": msg_id, "menu_key": key}
        edit_message(chat_id, msg_id, render_body_text(f"📝 <b>Edit Body: {key.upper()}</b>\n\nSend the new text. You can use Premium Emojis directly here.\n(Use standard HTML like <b>bold</b>, <i>italic</i> for formatting)"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"md_edit_{key}", "style": "danger"}]]})

    elif data.startswith("md_btns_"):
        answer_callback(call["id"]) 
        if chat_id in user_states: del user_states[chat_id] 
        if chat_id in temp_data: del temp_data[chat_id]
        key = data.replace("md_btns_", "")
        try:
            edit_message(chat_id, msg_id, render_body_text(f"⚙️ <b>Edit Inline Buttons: {key.upper()}</b>"), reply_markup=menu_buttons_list_keyboard(key))
        except: pass

    elif data.startswith("md_addbtn_"):
        key = data.replace("md_addbtn_", "")
        user_states[chat_id] = "wait_for_menu_btn"
        temp_data[chat_id] = {"msg_id": msg_id, "menu_key": key}
        edit_message(chat_id, msg_id, render_body_text(f"➕ <b>Add Button: {key.upper()}</b>\n\nSend custom button in this format:\n<code>Button Text - https://link.com</code>\n\n<i>(Only normal Emojis supported here!)</i>"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"md_btns_{key}", "style": "danger"}]]})

    elif data.startswith("md_delbtn_"):
        parts = data.split("_")
        key = parts[2]
        b_idx = int(parts[3])
        if b_idx < len(bot_settings["custom_messages"][key]["buttons"]):
            del bot_settings["custom_messages"][key]["buttons"][b_idx]
            save_db()
            answer_callback(call["id"], "✅ Button Deleted!", show_alert=True)
            edit_message(chat_id, msg_id, render_body_text(f"⚙️ <b>Edit Inline Buttons: {key.upper()}</b>"), reply_markup=menu_buttons_list_keyboard(key))

    elif data.startswith("sel_wm_"):
        method = data.replace("sel_wm_", "")
        bal = get_user(chat_id).get('balance', 0.0)
        min_w = bot_settings['min_withdraw']
        
        if bal < min_w:
            answer_callback(call["id"], f"❌ আপনার ব্যালেন্স অপর্যাপ্ত! মিনিমাম {min_w} ৳ প্রয়োজন।", show_alert=True)
            return
            
        temp_data[chat_id] = {"method": method, "balance": bal, "msg_id": msg_id}
        user_states[chat_id] = "wait_for_withdraw_amount"
        edit_message(chat_id, msg_id, render_body_text(f"{PEM['ok']} Method: {method}\n💰 Available Balance: {bal} ৳\n\n📝 Enter the amount you want to withdraw (Min: {min_w} ৳):"), reply_markup=get_cancel_kb())
        answer_callback(call["id"])

    elif data == "test_message_flow":
        user_states[chat_id] = "wait_for_test_service"
        temp_data[chat_id] = {}
        edit_message(chat_id, msg_id, render_body_text("🧪 <b>Test Mode</b>\n\n📝 Send the Service Name (e.g., IG):"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "danger"}]]})

    elif data == "manage_emojis":
        edit_message(chat_id, msg_id, render_body_text(f"{PEM['star']} <b>Premium Emoji Management</b>\n\nUpload your TXT files or manually add them below:"), reply_markup=emoji_settings_keyboard())

    elif data == "up_flags_txt":
        user_states[chat_id] = "wait_for_flag_txt"
        edit_message(chat_id, msg_id, render_body_text("📂 Please upload the <b>Flag Emojis</b> <code>.txt</code> file."), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_emojis", "style": "danger"}]]})

    elif data == "up_apps_txt":
        user_states[chat_id] = "wait_for_app_txt"
        edit_message(chat_id, msg_id, render_body_text("📂 Please upload the <b>Service Apps</b> <code>.txt</code> file."), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_emojis", "style": "danger"}]]})

    elif data == "add_single_emoji":
        user_states[chat_id] = "wait_for_emoji_extract"
        edit_message(chat_id, msg_id, render_body_text("📝 যেকোনো একটি Premium Emoji সেন্ড করুন (যেমন: 🇧🇩 বা 🚫):"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_emojis", "style": "danger"}]]})

    elif data == "dl_flags_txt":
        content = generate_emoji_txt("flags")
        if content:
            send_document(chat_id, "Flag_Emojis.txt", content)
            answer_callback(call["id"], "✅ Downloaded!")
        else:
            answer_callback(call["id"], "❌ No Flag Emojis found!", show_alert=True)

    elif data == "dl_apps_txt":
        content = generate_emoji_txt("apps")
        if content:
            send_document(chat_id, "Service_Apps.txt", content)
            answer_callback(call["id"], "✅ Downloaded!")
        else:
            answer_callback(call["id"], "❌ No App Emojis found!", show_alert=True)

    elif data == "del_all_flags":
        bot_settings["premium_flags"] = {}
        save_db()
        answer_callback(call["id"], "✅ All Premium Flags Deleted Successfully!", show_alert=True)

    elif data == "broadcast_msg":
        user_states[chat_id] = "wait_for_broadcast"
        edit_message(chat_id, msg_id, render_body_text("📢 <b>Broadcast Mode</b>\n\nSend the message you want to broadcast (Text, Photo, Video, File etc)."), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "back_to_admin", "style": "danger"}]]})

    elif data == "upload_num":
        user_states[chat_id] = "wait_for_txt"
        edit_message(chat_id, msg_id, render_body_text("📂 Please upload the numbers in a <b>.txt</b> file."), reply_markup={"inline_keyboard": [[{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "back_to_admin", "style": "danger"}]]})

    elif data == "delete_files":
        kb = []
        for b_id, b_data in number_batches.items():
            kb.append([{"text": f"{b_data['filename']} ({len(b_data['numbers'])})", "icon_custom_emoji_id": "5422557736330106570", "callback_data": f"del_b_{b_id}", "style": "danger"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "back_to_admin", "style": "primary"}])
        txt = "🗑 Select a file to delete:" if len(kb) > 1 else f"{PEM['no']} No files found."
        edit_message(chat_id, msg_id, render_body_text(txt), reply_markup={"inline_keyboard": kb})

    elif data.startswith("del_b_"):
        b_id = data.split("del_b_")[1]
        if b_id in number_batches:
            del number_batches[b_id]
            save_db()
            answer_callback(call["id"], "✅ File deleted!", show_alert=True)
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": "delete_files", "id": call["id"]})

    elif data.startswith("bc_no_"):
        answer_callback(call["id"], "✅ Numbers added silently!", show_alert=True)
        edit_message(chat_id, msg_id, render_body_text(f"{PEM['ok']} Numbers added successfully! (No broadcast was sent)."))

    elif data.startswith("bc_yes_"):
        batch_id = data.replace("bc_yes_", "")
        if batch_id in number_batches:
            b_data = number_batches[batch_id]
            service = b_data["service"]
            country = b_data["country"]
            otp_price = b_data.get("custom_otp_rate", bot_settings.get("otp_reward", 0.0))
            num_count = len(b_data["numbers"])
            first_num = b_data["numbers"][0]["num"] if num_count > 0 else ""
            
            app_full_name, prem_app_html = get_service_info_html(service)
            prem_flag_html = get_flag_info_html(first_num) if first_num else f"{PEM['world']} "
            
            icon_money = '<tg-emoji emoji-id="6170385123943391899">💰</tg-emoji>'
            icon_graph = '<tg-emoji emoji-id="6169944314269934497">📊</tg-emoji>'
            
            broadcast_txt = (
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"《 𝐍𝐄𝐖 𝐍𝐔𝐌𝐁𝐄𝐑𝐒 𝐀𝐃𝐃 》\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"{prem_flag_html} {country.title()} -- {prem_app_html} {service}\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"{icon_graph} 𝐓𝐎𝐓𝐄𝐋𝐋  :  <b>{num_count} 𝐍𝐔𝐌𝐁𝐄𝐑</b>\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"{icon_money} 𝐎𝐓𝐏 𝐏𝐑𝐈𝐂𝐄 : <b>{otp_price} 𝐓𝐊</b>\n"
            )
            final_broadcast_text = render_body_text(broadcast_txt)
            
            get_number_btn = [[{
                "text": "Get Number", 
                "icon_custom_emoji_id": "6170482267513692724", 
                "callback_data": f"g_c_0_{service}_{country}", 
                "style": "success"
            }]]
            
            def simple_broadcast(txt, kb):
                b_session = requests.Session()
                url = f"{BASE_URL}/sendMessage"
                for u_id in list(all_known_users):
                    try:
                        b_session.post(url, json={"chat_id": u_id, "text": txt, "parse_mode": "HTML", "disable_web_page_preview": True, "disable_notification": True, "reply_markup": {"inline_keyboard": kb}}, timeout=5)
                    except: pass
                    time.sleep(0.035)
                    
            threading.Thread(target=simple_broadcast, args=(final_broadcast_text, get_number_btn)).start()
            answer_callback(call["id"], "✅ Broadcast Started!", show_alert=True)
            edit_message(chat_id, msg_id, render_body_text(f"{PEM['rocket']} Numbers added and Broadcast is sending to all users..."))
        else:
            answer_callback(call["id"], "❌ Batch not found!", show_alert=True)
            edit_message(chat_id, msg_id, render_body_text("❌ Error: Number batch not found!"))

    elif data == "show_used":
        kb = {"inline_keyboard": [[{"text": "Download TXT", "icon_custom_emoji_id": "5257969839313526622", "callback_data": "dl_used", "style": "primary"}], [{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "back_to_admin", "style": "danger"}]]}
        edit_message(chat_id, msg_id, render_body_text(f"{PEM['ok']} <b>Total Used Numbers:</b> {len(used_numbers_list)}"), reply_markup=kb)

    elif data == "show_unused":
        unused_count = sum(len(b["numbers"]) for b in number_batches.values())
        kb = {"inline_keyboard": [[{"text": "Download TXT", "icon_custom_emoji_id": "5257969839313526622", "callback_data": "dl_unused", "style": "primary"}], [{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "back_to_admin", "style": "danger"}]]}
        edit_message(chat_id, msg_id, render_body_text(f"{PEM['rocket']} <b>Total Unused Numbers:</b> {unused_count}"), reply_markup=kb)

    elif data == "dl_used":
        if not used_numbers_list:
            answer_callback(call["id"], "❌ No used numbers found!", show_alert=True)
            return
        content = "\n".join(used_numbers_list).encode('utf-8')
        send_document(chat_id, "used_numbers.txt", content)
        answer_callback(call["id"])

    elif data == "dl_unused":
        unused_list = [n["num"] for b in number_batches.values() for n in b["numbers"]]
        if not unused_list:
            answer_callback(call["id"], "❌ No unused numbers found!", show_alert=True)
            return
        content = "\n".join(unused_list).encode('utf-8')
        send_document(chat_id, "unused_numbers.txt", content)
        answer_callback(call["id"])

    elif data == "lb_main":
        txt = f"━━━━━━━━━━━━━━━\n《 {PEM['admin']} <b>LEADER BOARD MENU</b> 》\n━━━━━━━━━━━━━━━\n<i>Select a category to view the top performers or history.</i>\n━━━━━━━━━━━━━━━"
        kb = [
            [{"text": "Top Referrers", "icon_custom_emoji_id": "5420145051336485498", "callback_data": "lb_top_refs", "style": "primary"}],
            [{"text": "Top OTP (All Time)", "icon_custom_emoji_id": "5353001161878182134", "callback_data": "lb_top_otps", "style": "primary"}],
            [{"text": "Top OTP (Today)", "icon_custom_emoji_id": "6206185428702206246", "callback_data": "lb_top_otps_today", "style": "success"}],
            [{"text": "Withdrawal History", "icon_custom_emoji_id": "5348469219761626211", "callback_data": "lb_w_history", "style": "success"}],
            [{"text": "Back to Admin", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "back_to_admin", "style": "danger"}]
        ]
        edit_message(chat_id, msg_id, render_body_text(txt), reply_markup={"inline_keyboard": kb})

    elif data.startswith("lb_"):
        sub = data.replace("lb_", "")
        edit_message(chat_id, msg_id, render_body_text("⌛ <i>Fetching Data...</i>"))
        
        num_map = {"1": "1️⃣", "2": "2️⃣", "3": "3️⃣", "4": "4️⃣", "5": "5️⃣", "6": "6️⃣", "7": "7️⃣", "8": "8️⃣", "9": "9️⃣", "0": "0️⃣"}
        def get_p_num(n): return "".join([num_map.get(c, c) for c in str(n)])
        
        try:
            if sub == "top_refs":
                title, field, limit, icon = "TOP 5 REFERRERS", "total_refers", 5, PEM.get('user', '👥')
                users = db.collection('users').order_by(field, direction="DESCENDING").limit(limit).stream()
                res_txt = ""
                count = 1
                for u in users:
                    d = u.to_dict()
                    if d.get(field, 0) > 0:
                        p = "└" if count == limit else "├"
                        res_txt += f"{p} {get_p_num(count)} <a href='tg://user?id={u.id}'>{u.id}</a> ➔ <b>{d.get(field,0)}</b>\n"
                        count += 1
                if not res_txt: res_txt = "└ <i>No data found.</i>\n"

            elif sub == "top_otps":
                title, field, limit, icon = "TOP 5 OTP RECEIVERS", "total_otps", 5, PEM.get('msg', '📩')
                users = db.collection('users').order_by(field, direction="DESCENDING").limit(limit).stream()
                res_txt = ""
                count = 1
                for u in users:
                    d = u.to_dict()
                    if d.get(field, 0) > 0:
                        p = "└" if count == limit else "├"
                        res_txt += f"{p} {get_p_num(count)} <a href='tg://user?id={u.id}'>{u.id}</a> ➔ <b>{d.get(field,0)}</b>\n"
                        count += 1
                if not res_txt: res_txt = "└ <i>No data found.</i>\n"

            elif sub == "top_otps_today":
                title, field, limit, icon = "TOP 5 OTP TODAY", "daily_otps", 5, "🔥"
                try:
                    users = db.collection('users').order_by(field, direction="DESCENDING").limit(limit).stream()
                except:
                    users = db.collection('users').stream()
                res_txt = ""
                count = 1
                sorted_users = []
                for u in users:
                    d = u.to_dict()
                    if d.get(field, 0) > 0:
                        sorted_users.append((u.id, d.get(field, 0)))
                sorted_users.sort(key=lambda x: x[1], reverse=True)
                
                for uid, val in sorted_users[:limit]:
                    p = "└" if count == min(limit, len(sorted_users)) else "├"
                    res_txt += f"{p} {get_p_num(count)} <a href='tg://user?id={uid}'>{uid}</a> ➔ <b>{val}</b>\n"
                    count += 1
                if not res_txt: res_txt = "└ <i>No data found for today.</i>\n"

            elif sub == "w_history":
                title, limit, icon = "LAST 10 WITHDRAWALS", 10, PEM.get('money', '💸')
                ws = db.collection('withdrawals').order_by('timestamp', direction="DESCENDING").limit(limit).stream()
                res_txt = ""
                count = 1
                for w in ws:
                    d = w.to_dict()
                    s = str(d.get('status','Pending')).lower()
                    stat_icon = PEM.get('ok','✅') if s in ["approved","success"] else PEM.get('no','❌') if s=="rejected" else "⏳"
                    uid = d.get('user_id','User')
                    p = "└" if count == limit else "├"
                    res_txt += f"{p} {get_p_num(count)} <a href='tg://user?id={uid}'>{uid}</a> ➔ <b>{d.get('amount',0)}৳</b> {stat_icon}\n"
                    count += 1
                if not res_txt: res_txt = "└ <i>No history found.</i>\n"

            final_msg = f"━━━━━━━━━━━━━━━\n{icon} <b>{title}</b>\n━━━━━━━━━━━━━━━\n{res_txt}━━━━━━━━━━━━━━━"
            kb = [[{"text": "Refresh", "icon_custom_emoji_id": "5420155432272438703", "callback_data": data, "style": "success"}, {"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "lb_main", "style": "danger"}]]
            edit_message(chat_id, msg_id, render_body_text(final_msg), reply_markup={"inline_keyboard": kb})

        except Exception as e:
            edit_message(chat_id, msg_id, render_body_text(f"❌ Error: {e}"), reply_markup={"inline_keyboard": [[{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "lb_main", "style": "danger"}]]})

    elif data == "lb_main":
        txt = f"━━━━━━━━━━━━━━━\n《 {PEM['admin']} <b>LEADER BOARD MENU</b> 》\n━━━━━━━━━━━━━━━\n<i>Select a category to view the top performers or history.</i>\n━━━━━━━━━━━━━━━"
        kb = [
            [{"text": "Top Referrers", "icon_custom_emoji_id": "5420145051336485498", "callback_data": "lb_top_refs", "style": "primary"}],
            [{"text": "Top OTP (All Time)", "icon_custom_emoji_id": "5353001161878182134", "callback_data": "lb_top_otps", "style": "primary"}],
            [{"text": "Top OTP (Today)", "icon_custom_emoji_id": "6206185428702206246", "callback_data": "lb_top_otps_today", "style": "success"}],
            [{"text": "Withdrawal History", "icon_custom_emoji_id": "5348469219761626211", "callback_data": "lb_w_history", "style": "success"}],
            [{"text": "Back to Admin", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "back_to_admin", "style": "danger"}]
        ]
        edit_message(chat_id, msg_id, render_body_text(txt), reply_markup={"inline_keyboard": kb})

    elif data.startswith("lb_"):
        sub = data.replace("lb_", "")
        edit_message(chat_id, msg_id, render_body_text("⌛ <i>Fetching Data...</i>"))
        
        num_map = {"1": "1️⃣", "2": "2️⃣", "3": "3️⃣", "4": "4️⃣", "5": "5️⃣", "6": "6️⃣", "7": "7️⃣", "8": "8️⃣", "9": "9️⃣", "0": "0️⃣"}
        def get_p_num(n): return "".join([num_map.get(c, c) for c in str(n)])
        
        try:
            if sub == "top_refs":
                title, field, limit, icon = "TOP 5 REFERRERS", "total_refers", 5, PEM.get('user', '👥')
                users = db.collection('users').order_by(field, direction="DESCENDING").limit(limit).stream()
                res_txt = ""
                count = 1
                for u in users:
                    d = u.to_dict()
                    if d.get(field, 0) > 0:
                        p = "└" if count == limit else "├"
                        res_txt += f"{p} {get_p_num(count)} <a href='tg://user?id={u.id}'>{u.id}</a> ➔ <b>{d.get(field,0)}</b>\n"
                        count += 1
                if not res_txt: res_txt = "└ <i>No data found.</i>\n"

            elif sub == "top_otps":
                title, field, limit, icon = "TOP 5 OTP RECEIVERS", "total_otps", 5, PEM.get('msg', '📩')
                users = db.collection('users').order_by(field, direction="DESCENDING").limit(limit).stream()
                res_txt = ""
                count = 1
                for u in users:
                    d = u.to_dict()
                    if d.get(field, 0) > 0:
                        p = "└" if count == limit else "├"
                        res_txt += f"{p} {get_p_num(count)} <a href='tg://user?id={u.id}'>{u.id}</a> ➔ <b>{d.get(field,0)}</b>\n"
                        count += 1
                if not res_txt: res_txt = "└ <i>No data found.</i>\n"

            elif sub == "top_otps_today":
                title, field, limit, icon = "TOP 5 OTP TODAY", "daily_otps", 5, "🔥"
                try:
                    users = db.collection('users').order_by(field, direction="DESCENDING").limit(limit).stream()
                except:
                    users = db.collection('users').stream()
                res_txt = ""
                count = 1
                sorted_users = []
                for u in users:
                    d = u.to_dict()
                    if d.get(field, 0) > 0:
                        sorted_users.append((u.id, d.get(field, 0)))
                sorted_users.sort(key=lambda x: x[1], reverse=True)
                
                for uid, val in sorted_users[:limit]:
                    p = "└" if count == min(limit, len(sorted_users)) else "├"
                    res_txt += f"{p} {get_p_num(count)} <a href='tg://user?id={uid}'>{uid}</a> ➔ <b>{val}</b>\n"
                    count += 1
                if not res_txt: res_txt = "└ <i>No data found for today.</i>\n"

            elif sub == "w_history":
                title, limit, icon = "LAST 10 WITHDRAWALS", 10, PEM.get('money', '💸')
                ws = db.collection('withdrawals').order_by('timestamp', direction="DESCENDING").limit(limit).stream()
                res_txt = ""
                count = 1
                for w in ws:
                    d = w.to_dict()
                    s = str(d.get('status','Pending')).lower()
                    stat_icon = PEM.get('ok','✅') if s in ["approved","success"] else PEM.get('no','❌') if s=="rejected" else "⏳"
                    uid = d.get('user_id','User')
                    p = "└" if count == limit else "├"
                    res_txt += f"{p} {get_p_num(count)} <a href='tg://user?id={uid}'>{uid}</a> ➔ <b>{d.get('amount',0)}৳</b> {stat_icon}\n"
                    count += 1
                if not res_txt: res_txt = "└ <i>No history found.</i>\n"

            final_msg = f"━━━━━━━━━━━━━━━\n{icon} <b>{title}</b>\n━━━━━━━━━━━━━━━\n{res_txt}━━━━━━━━━━━━━━━"
            kb = [[{"text": "Refresh", "icon_custom_emoji_id": "5420155432272438703", "callback_data": data, "style": "success"}, {"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "lb_main", "style": "danger"}]]
            edit_message(chat_id, msg_id, render_body_text(final_msg), reply_markup={"inline_keyboard": kb})

        except Exception as e:
            edit_message(chat_id, msg_id, render_body_text(f"❌ Error: {e}"), reply_markup={"inline_keyboard": [[{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "lb_main", "style": "danger"}]]})

    elif data == "btn_color_menu" or data.startswith("btnclr_"):
        if not is_admin(chat_id): return
        if data.startswith("btnclr_"):
            _mode = data[len("btnclr_"):]
            if _mode in BTN_COLOR_MODES:
                bot_settings["btn_color"] = _mode
                save_db()
        _cur = BTN_COLOR_MODES.get(bot_settings.get("btn_color", "original"), "Original")
        edit_message(chat_id, msg_id, render_body_text(
            f"🎨 <b>BUTTON COLOR</b>\n\nCurrent: <b>{_cur}</b>\n\n"
            "Choose a color for <b>ALL</b> bot buttons. Reply menu (bottom) updates when a user opens the menu again (/start)."),
            reply_markup=btn_color_keyboard())

    elif data == "toggle_ws":
        if not is_admin(chat_id): return
        bot_settings["ws_on"] = not bot_settings.get("ws_on", True)
        save_db()
        edit_message(chat_id, msg_id, get_admin_text(), reply_markup=admin_panel_keyboard())

    elif data == "back_to_admin":
        if chat_id in user_states: del user_states[chat_id]
        edit_message(chat_id, msg_id, get_admin_text(), reply_markup=admin_panel_keyboard())
        
    elif data == "dev_info":
        dev_text = '<b>╔ ≪• </b><tg-emoji emoji-id="6206319341487527808">👑</tg-emoji><b> 𝐖𝐄𝐋𝐂𝐎𝐌𝐄 </b><tg-emoji emoji-id="6266995104687330978">👑</tg-emoji><b> •≫ ╗\n\xa0 \n\xa0\xa0\xa0</b><tg-emoji emoji-id="6269505633330923842">😎</tg-emoji><b> 𝐑 𝐀 𝐊 𝐈 𝐁 𝐔 𝐋\xa0\xa0 𝐇 𝐀 𝐒 𝐀 𝐍 </b><tg-emoji emoji-id="6269505633330923842">😎</tg-emoji><b>\n\n╠ ━━━━━━━━━━━━━ ╣\n </b><tg-emoji emoji-id="5809833575905760671">🟡</tg-emoji><b> </b><tg-emoji emoji-id="6039372815935741919">👤</tg-emoji><b> 𝐍ᴀᴍᴇ : 𝚁𝚊𝚔𝚒𝚋𝚞𝚕 𝙷𝚊𝚜𝚊𝚗\n\n </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji><b> </b><tg-emoji emoji-id="5452055425690123301">🎂</tg-emoji><b> 𝐁ɪʀᴛʜᴅᴀʏ : ❶❶/⓿❽\n\n </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji><b> </b><tg-emoji emoji-id="6309599324837781578">🔐</tg-emoji><b> 𝐀ɢᴇ : 𝙷𝚒𝚍𝚍𝚎𝚗\n\n </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji><b> </b><tg-emoji emoji-id="5298528899116853058">📍</tg-emoji><b> 𝐂ɪᴛʏ : 𝚂𝚊𝚝𝚔𝚑𝚒𝚛𝚊\n\n </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji><b> </b><tg-emoji emoji-id="6255958241327450201">🎓</tg-emoji><b> 𝐄ᴅᴜᴄᴀᴛɪᴏɴ : 𝙷𝚒𝚍𝚍𝚎𝚗\n\n </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji><b> </b><tg-emoji emoji-id="5290000481341415175">🕌</tg-emoji><b> 𝐑ᴇʟɪɢɪᴏɴ : 𝙸𝚜𝚕𝚊𝚖\n\n </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji><b> </b><tg-emoji emoji-id="5999317873623831250">🌐</tg-emoji><b> 𝐋ᴀɴɢᴜᴀɢᴇ : 𝙱𝚊𝚗𝚐𝚕𝚊 &amp; 𝙴𝚗𝚐𝚕𝚒𝚜𝚑\n\n </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji><b> </b><tg-emoji emoji-id="6068605166511202295">🥾</tg-emoji><b> 𝐇ᴏʙʙʏ : 𝙽𝚘𝚝 𝚍𝚎𝚌𝚒𝚍𝚎 𝚢𝚎𝚝\n\n </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji><b> </b><tg-emoji emoji-id="6206236607532504295">💼</tg-emoji><b> 𝐖ᴏʀᴋ : 𝚂𝚝𝚞𝚍𝚎𝚗𝚝\n\n </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji><b> </b><tg-emoji emoji-id="6217242478408378303">📱</tg-emoji><b> 𝐅ᴀᴠ 𝐀ᴘᴘ : 𝚃𝚎𝚕𝚎𝚐𝚛𝚊𝚖\n\n </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji><b> </b><tg-emoji emoji-id="5949785441728205181">⭕️</tg-emoji><b> 𝐃ɪsʟɪᴋᴇ : 𝚂𝚌𝚊𝚖𝚖𝚎ʀs\n╠ ━━━━━━━━━━━━━ ╣\n\n </b><tg-emoji emoji-id="6269267069372469947">⚡️</tg-emoji><b>𝐎ᴡɴᴇʀ </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji> @rs_sms19 <tg-emoji emoji-id="6266995104687330978">👑</tg-emoji>\n\n<tg-emoji emoji-id="6129805886383723340">👑</tg-emoji><b> </b><a href="tg://openmessage?user_id=8294522264"><b>𝐂ᴏɴᴛᴀᴄᴛ</b></a><b> </b><tg-emoji emoji-id="5834535062401454758">➡️</tg-emoji> @itz_rakib19s <tg-emoji emoji-id="6005831122808739013">✅</tg-emoji>\n<b>\n\n\xa0\xa0 </b><tg-emoji emoji-id="5406926593698312391">❤️</tg-emoji><b> 𝐈 𝐋ᴏᴠᴇ 𝐌ʏ 𝐌ᴏᴛʜᴇʀ &amp; 𝐀ʟʟᴀʜ </b><tg-emoji emoji-id="5406926593698312391">❤️</tg-emoji><b>\n\n╚≪• </b><tg-emoji emoji-id="5469641199348363998">⭐️</tg-emoji><b> 𝐓𝐇𝐀𝐍𝐊 𝐘𝐎𝐔 </b><tg-emoji emoji-id="5469641199348363998">⭐️</tg-emoji><b> •≫ ╝</b>'
        kb = {"inline_keyboard": [[{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "back_to_admin", "style": "danger"}]]}
        edit_message(chat_id, msg_id, dev_text, reply_markup=kb)
        
    elif data == "system_settings":
        edit_message(chat_id, msg_id, render_body_text(f"{PEM['gear']} <b>System Settings</b>\nManage advanced bot configurations below:"), reply_markup=system_settings_keyboard())

    elif data == "stex_control":
        edit_message(chat_id, msg_id, render_body_text(f"🌐 <b>StexSMS Control Panel</b>\n\nTotal API Keys: {len(bot_settings.get('stex_keys', []))}\nManage your StexSMS API Keys below:"), reply_markup=stex_control_keyboard())

    elif data == "add_stex_key":
        user_states[chat_id] = "wait_for_add_stex_key"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the new StexSMS API Key (e.g. nxa_...):"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "stex_control", "style": "danger"}]]})

    elif data == "view_stex_keys":
        kb = []
        for idx, key in enumerate(bot_settings.get("stex_keys", [])):
            safe_name = key[:10] + "..." if len(key)>10 else key
            kb.append([{"text": f"Delete {safe_name}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"del_nxa_{idx}", "style": "danger"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "stex_control", "style": "primary"}])
        edit_message(chat_id, msg_id, render_body_text("🗑 <b>Select StexSMS Key to Delete:</b>"), reply_markup={"inline_keyboard": kb})

    elif data.startswith("del_nxa_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(bot_settings.get("stex_keys", [])):
            del bot_settings["stex_keys"][idx]
            save_db()
            answer_callback(call["id"], "✅ StexSMS Key Deleted!", show_alert=True)
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": "view_stex_keys", "id": call["id"]})

    elif data == "stex_search_country":
        kb = []
        for idx, c in enumerate(bot_settings.get("search_countries", [])):
            kb.append([{"text": f"Delete {c}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"del_sc_{idx}", "style": "danger"}])
        kb.append([{"text": "Add Country Code", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_search_country", "style": "success"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "stex_control", "style": "primary"}])
        edit_message(chat_id, msg_id, render_body_text("🌍 <b>Allowed Search Countries:</b>\nOnly these country codes will be allowed in Search Number."), reply_markup={"inline_keyboard": kb})

    elif data == "add_search_country":
        user_states[chat_id] = "wait_for_add_sc"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the Country Code (e.g. 880 or 92):"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "stex_search_country", "style": "danger"}]]})

    elif data.startswith("del_sc_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(bot_settings.get("search_countries", [])):
            del bot_settings["search_countries"][idx]
            save_db()
            answer_callback(call["id"], "✅ Country Deleted!", show_alert=True)
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": "stex_search_country", "id": call["id"]})

    elif data == "manage_stex_srv":
        kb = []
        srvs = bot_settings.get("stex_services", {})
        apps_db = bot_settings.get("premium_apps", {})
        for srv in srvs:
            emoji_id = "5257969839313526622"
            for app_key, app_data in apps_db.items():
                if srv.upper() == app_key or srv.upper() in app_key or app_key in srv.upper():
                    if "id" in app_data:
                        emoji_id = app_data["id"]
                        break
            kb.append([{"text": f"{srv}", "icon_custom_emoji_id": emoji_id, "callback_data": f"nx_srv_{srv}", "style": "primary"}])
        kb.append([{"text": "Add New Service", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "nx_add_srv", "style": "success"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "stex_control", "style": "danger"}])
        edit_message(chat_id, msg_id, render_body_text("📦 <b>StexSMS Services Manager</b>\nManage your API-based dynamic services below:"), reply_markup={"inline_keyboard": kb})

    elif data == "nx_add_srv":
        user_states[chat_id] = "wait_nx_srv_name"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Enter Service Name (e.g. TELEGRAM):"), reply_markup=get_cancel_kb())

    elif data.startswith("nx_srv_"):
        srv = data.replace("nx_srv_", "")
        kb = []
        countries = bot_settings["stex_services"].get(srv, {})
        flags_db = bot_settings.get("premium_flags", {})
        for c in countries:
            emoji_id = "5780471598922337683"
            for flag_code, flag_data in flags_db.items():
                iso = flag_data.get("iso", "").upper()
                name = flag_data.get("name", "").upper()
                if c.upper() == iso or c.upper() == name or c.upper() in name or name in c.upper():
                    if "id" in flag_data:
                        emoji_id = flag_data["id"]
                        break
            kb.append([{"text": f"{c} ({len(countries[c])} Ranges)", "icon_custom_emoji_id": emoji_id, "callback_data": f"nx_cnt_{srv}_{c}", "style": "primary"}])
        kb.append([{"text": "Add Country", "icon_custom_emoji_id": "5420323438508155202", "callback_data": f"nx_add_cnt_{srv}", "style": "success"}])
        kb.append([{"text": "Delete Service", "icon_custom_emoji_id": "5422557736330106570", "callback_data": f"nx_del_srv_{srv}", "style": "danger"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_stex_srv", "style": "primary"}])
        edit_message(chat_id, msg_id, render_body_text(f"📂 <b>Service: {srv}</b>\nManage countries for this service:"), reply_markup={"inline_keyboard": kb})

    elif data.startswith("nx_add_cnt_"):
        srv = data.replace("nx_add_cnt_", "")
        user_states[chat_id] = "wait_nx_cnt_name"
        temp_data[chat_id] = {"msg_id": msg_id, "srv": srv}
        edit_message(chat_id, msg_id, render_body_text(f"🌍 Enter Country Name for <b>{srv}</b> (e.g. BD, INDIA):"), reply_markup=get_cancel_kb())

    elif data.startswith("nx_cnt_"):
        parts = data.split("_")
        srv, cnt = parts[2], parts[3]
        ranges = bot_settings["stex_services"][srv].get(cnt, [])
        
        kb = []
        row = []
        for r in ranges:
            row.append({"text": f"Delete {r}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"nx_dr_{srv}_{cnt}_{r}", "style": "danger"})
            if len(row) == 2:
                kb.append(row)
                row = []
        if row: kb.append(row)
        
        kb.append([{"text": "Add Range", "icon_custom_emoji_id": "5420323438508155202", "callback_data": f"nx_addr_{srv}_{cnt}", "style": "success"}])
        kb.append([{"text": "Delete Entire Country", "icon_custom_emoji_id": "5422557736330106570", "callback_data": f"nx_del_cnt_{srv}_{cnt}", "style": "danger"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"nx_srv_{srv}", "style": "primary"}])
        
        txt = f"📍 <b>Service: {srv} | Country: {cnt}</b>\n\n<b>Total Ranges:</b> {len(ranges)}\n<i>Click on a range below to delete it, or add a new one.</i>"
        edit_message(chat_id, msg_id, render_body_text(txt), reply_markup={"inline_keyboard": kb})

    elif data.startswith("nx_addr_"):
        parts = data.split("_")
        srv, cnt = parts[2], parts[3]
        user_states[chat_id] = "wait_nx_addr"
        temp_data[chat_id] = {"msg_id": msg_id, "srv": srv, "cnt": cnt}
        edit_message(chat_id, msg_id, render_body_text(f"📝 Send the new Range for <b>{cnt}</b> (e.g. 88017):"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"nx_cnt_{srv}_{cnt}", "style": "danger"}]]})

    elif data.startswith("nx_dr_"):
        parts = data.split("_")
        srv, cnt, rng = parts[2], parts[3], parts[4]
        if rng in bot_settings["stex_services"].get(srv, {}).get(cnt, []):
            bot_settings["stex_services"][srv][cnt].remove(rng)
            save_db()
            answer_callback(call["id"], f"✅ Range {rng} deleted!", show_alert=True)
        handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": f"nx_cnt_{srv}_{cnt}", "id": call["id"]})

    elif data.startswith("nx_del_srv_"):
        srv = data.replace("nx_del_srv_", "")
        if srv in bot_settings["stex_services"]: del bot_settings["stex_services"][srv]
        save_db()
        handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": "manage_stex_srv", "id": call["id"]})

    elif data.startswith("nx_del_cnt_"):
        parts = data.split("_")
        srv, cnt = parts[3], parts[4]
        if cnt in bot_settings["stex_services"].get(srv, {}): del bot_settings["stex_services"][srv][cnt]
        save_db()
        handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": f"nx_srv_{srv}", "id": call["id"]})

    elif data == "voltx_control":
        edit_message(chat_id, msg_id, render_body_text(f"⚡ <b>Voltx Control Panel</b>\n\nTotal API Keys: {len(bot_settings.get('voltx_keys', []))}\nManage your Voltx API Keys below:"), reply_markup=voltx_control_keyboard())

    elif data == "toggle_voltx_auto":
        bot_settings["voltx_auto"] = not bot_settings.get("voltx_auto", False)
        save_db()
        edit_message(chat_id, msg_id, render_body_text(f"⚡ <b>Voltx Control Panel</b>\n\nTotal API Keys: {len(bot_settings.get('voltx_keys', []))}\nManage your Voltx API Keys below:"), reply_markup=voltx_control_keyboard())
        answer_callback(call["id"], f"✅ Voltx Auto Range is now {'ON' if bot_settings['voltx_auto'] else 'OFF'}!", show_alert=False)

    elif data == "add_voltx_key":
        user_states[chat_id] = "wait_for_add_voltx_key"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the new Voltx API Key:"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "voltx_control", "style": "danger"}]]})

    elif data == "view_voltx_keys":
        kb = []
        for idx, key in enumerate(bot_settings.get("voltx_keys", [])):
            safe_name = key[:10] + "..." if len(key)>10 else key
            kb.append([{"text": f"Delete {safe_name}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"del_vtx_{idx}", "style": "danger"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "voltx_control", "style": "primary"}])
        edit_message(chat_id, msg_id, render_body_text("🗑 <b>Select Voltx Key to Delete:</b>"), reply_markup={"inline_keyboard": kb})

    elif data.startswith("del_vtx_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(bot_settings.get("voltx_keys", [])):
            del bot_settings["voltx_keys"][idx]
            save_db()
            answer_callback(call["id"], "✅ Voltx Key Deleted!", show_alert=True)
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": "view_voltx_keys", "id": call["id"]})

    elif data == "voltx_search_country":
        kb = []
        for idx, c in enumerate(bot_settings.get("voltx_search_countries", [])):
            kb.append([{"text": f"Delete {c}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"del_vsc_{idx}", "style": "danger"}])
        kb.append([{"text": "Add Country Code", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "add_voltx_search_country", "style": "success"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "voltx_control", "style": "primary"}])
        edit_message(chat_id, msg_id, render_body_text("🌍 <b>Voltx Allowed Ranges:</b>\nOnly these ranges/codes will be allowed in Voltx Search Number."), reply_markup={"inline_keyboard": kb})

    elif data == "add_voltx_search_country":
        user_states[chat_id] = "wait_for_add_vsc"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the Voltx Range Code (e.g. 26134):"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "voltx_search_country", "style": "danger"}]]})

    elif data.startswith("del_vsc_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(bot_settings.get("voltx_search_countries", [])):
            del bot_settings["voltx_search_countries"][idx]
            save_db()
            answer_callback(call["id"], "✅ Voltx Range Deleted!", show_alert=True)
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": "voltx_search_country", "id": call["id"]})

    elif data == "manage_voltx_srv":
        kb = []
        srvs = bot_settings.get("voltx_services", {})
        apps_db = bot_settings.get("premium_apps", {})
        for srv in srvs:
            emoji_id = "5257969839313526622"
            for app_key, app_data in apps_db.items():
                if srv.upper() == app_key or srv.upper() in app_key or app_key in srv.upper():
                    if "id" in app_data: emoji_id = app_data["id"]; break
            kb.append([{"text": f"{srv}", "icon_custom_emoji_id": emoji_id, "callback_data": f"vx_srv_{srv}", "style": "primary"}])
        kb.append([{"text": "Add New Service", "icon_custom_emoji_id": "5420323438508155202", "callback_data": "vx_add_srv", "style": "success"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "voltx_control", "style": "danger"}])
        edit_message(chat_id, msg_id, render_body_text("⚡ <b>Voltx Services Manager</b>\nManage your API-based dynamic services below:"), reply_markup={"inline_keyboard": kb})

    elif data == "vx_add_srv":
        user_states[chat_id] = "wait_vx_srv_name"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Enter Service Name (e.g. TELEGRAM):"), reply_markup=get_cancel_kb())

    elif data.startswith("vx_srv_"):
        srv = data.replace("vx_srv_", "")
        kb = []
        countries = bot_settings["voltx_services"].get(srv, {})
        flags_db = bot_settings.get("premium_flags", {})
        for c in countries:
            emoji_id = "5780471598922337683"
            for flag_code, flag_data in flags_db.items():
                iso = flag_data.get("iso", "").upper()
                name = flag_data.get("name", "").upper()
                if c.upper() == iso or c.upper() == name or c.upper() in name or name in c.upper():
                    if "id" in flag_data: emoji_id = flag_data["id"]; break
            kb.append([{"text": f"{c} ({len(countries[c])} Ranges)", "icon_custom_emoji_id": emoji_id, "callback_data": f"vx_cnt_{srv}_{c}", "style": "primary"}])
        kb.append([{"text": "Add Country", "icon_custom_emoji_id": "5420323438508155202", "callback_data": f"vx_add_cnt_{srv}", "style": "success"}])
        kb.append([{"text": "Delete Service", "icon_custom_emoji_id": "5422557736330106570", "callback_data": f"vx_del_srv_{srv}", "style": "danger"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_voltx_srv", "style": "primary"}])
        edit_message(chat_id, msg_id, render_body_text(f"📂 <b>Service: {srv}</b>\nManage countries for this service:"), reply_markup={"inline_keyboard": kb})

    elif data.startswith("vx_add_cnt_"):
        srv = data.replace("vx_add_cnt_", "")
        user_states[chat_id] = "wait_vx_cnt_name"
        temp_data[chat_id] = {"msg_id": msg_id, "srv": srv}
        edit_message(chat_id, msg_id, render_body_text(f"🌍 Enter Country Name for <b>{srv}</b> (e.g. BD, INDIA):"), reply_markup=get_cancel_kb())

    elif data.startswith("vx_cnt_"):
        parts = data.split("_")
        srv, cnt = parts[2], parts[3]
        ranges = bot_settings["voltx_services"][srv].get(cnt, [])
        kb = []
        row = []
        for r in ranges:
            row.append({"text": f"Delete {r}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"vx_dr_{srv}_{cnt}_{r}", "style": "danger"})
            if len(row) == 2:
                kb.append(row)
                row = []
        if row: kb.append(row)
        kb.append([{"text": "Add Range", "icon_custom_emoji_id": "5420323438508155202", "callback_data": f"vx_addr_{srv}_{cnt}", "style": "success"}])
        kb.append([{"text": "Delete Entire Country", "icon_custom_emoji_id": "5422557736330106570", "callback_data": f"vx_del_cnt_{srv}_{cnt}", "style": "danger"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"vx_srv_{srv}", "style": "primary"}])
        txt = f"📍 <b>Service: {srv} | Country: {cnt}</b>\n\n<b>Total Ranges:</b> {len(ranges)}\n<i>Click on a range below to delete it, or add a new one.</i>"
        edit_message(chat_id, msg_id, render_body_text(txt), reply_markup={"inline_keyboard": kb})

    elif data.startswith("vx_addr_"):
        parts = data.split("_")
        srv, cnt = parts[2], parts[3]
        user_states[chat_id] = "wait_vx_addr"
        temp_data[chat_id] = {"msg_id": msg_id, "srv": srv, "cnt": cnt}
        edit_message(chat_id, msg_id, render_body_text(f"📝 Send the new Range for <b>{cnt}</b> (e.g. 26134):"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"vx_cnt_{srv}_{cnt}", "style": "danger"}]]})

    elif data.startswith("vx_dr_"):
        parts = data.split("_")
        srv, cnt, rng = parts[2], parts[3], parts[4]
        if rng in bot_settings["voltx_services"].get(srv, {}).get(cnt, []):
            bot_settings["voltx_services"][srv][cnt].remove(rng)
            save_db()
            answer_callback(call["id"], f"✅ Range {rng} deleted!", show_alert=True)
        handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": f"vx_cnt_{srv}_{cnt}", "id": call["id"]})

    elif data.startswith("vx_del_srv_"):
        srv = data.replace("vx_del_srv_", "")
        if srv in bot_settings["voltx_services"]: del bot_settings["voltx_services"][srv]
        save_db()
        handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": "manage_voltx_srv", "id": call["id"]})

    elif data.startswith("vx_del_cnt_"):
        parts = data.split("_")
        srv, cnt = parts[3], parts[4]
        if cnt in bot_settings["voltx_services"].get(srv, {}): del bot_settings["voltx_services"][srv][cnt]
        save_db()
        handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": f"vx_srv_{srv}", "id": call["id"]})

    elif data in ["zenex_control", "zebra_control", "scarface_control"]:
        p_name = data.split("_")[0]
        kb_map = {"zenex": zenex_control_keyboard, "zebra": zebra_control_keyboard, "scarface": scarface_control_keyboard}
        edit_message(chat_id, msg_id, render_body_text(f"🌐 <b>{p_name.title()} Control Panel</b>\n\nTotal API Keys: {len(bot_settings.get(f'{p_name}_keys', []))}\nManage your API Keys below:"), reply_markup=kb_map[p_name]())

    elif data.startswith("add_pnl_key_"):
        p_name = data.split("_")[3]
        user_states[chat_id] = "wait_for_add_gen_key"
        temp_data[chat_id] = {"msg_id": msg_id, "pnl": p_name}
        edit_message(chat_id, msg_id, render_body_text(f"📝 Send the new {p_name.title()} API Key:"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"{p_name}_control", "style": "danger"}]]})

    elif data.startswith("view_keys_"):
        p_name = data.split("_")[2]
        kb = []
        for idx, key in enumerate(bot_settings.get(f"{p_name}_keys", [])):
            safe_name = key[:10] + "..." if len(key)>10 else key
            kb.append([{"text": f"Delete {safe_name}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"del_genk_{p_name}_{idx}", "style": "danger"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"{p_name}_control", "style": "primary"}])
        edit_message(chat_id, msg_id, render_body_text(f"🗑 <b>Select {p_name.title()} Key to Delete:</b>"), reply_markup={"inline_keyboard": kb})

    elif data.startswith("del_genk_"):
        parts = data.split("_")
        p_name, idx = parts[2], int(parts[3])
        if 0 <= idx < len(bot_settings.get(f"{p_name}_keys", [])):
            del bot_settings[f"{p_name}_keys"][idx]; save_db()
            answer_callback(call["id"], "✅ Key Deleted!", show_alert=True)
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": f"view_keys_{p_name}", "id": call["id"]})

    elif data.startswith("manage_sc_"):
        p_name = data.split("_")[2]
        kb = []
        for idx, c in enumerate(bot_settings.get(f"{p_name}_search_countries", [])):
            kb.append([{"text": f"Delete {c}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"del_gsc_{p_name}_{idx}", "style": "danger"}])
        kb.append([{"text": "Add Country Code", "icon_custom_emoji_id": "5420323438508155202", "callback_data": f"add_gsc_{p_name}", "style": "success"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"{p_name}_control", "style": "primary"}])
        edit_message(chat_id, msg_id, render_body_text("🌍 <b>Allowed Search Countries:</b>"), reply_markup={"inline_keyboard": kb})

    elif data.startswith("add_gsc_"):
        p_name = data.split("_")[2]
        user_states[chat_id] = "wait_for_add_gsc"
        temp_data[chat_id] = {"msg_id": msg_id, "pnl": p_name}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the Country Code (e.g. 880 or 92):"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"manage_sc_{p_name}", "style": "danger"}]]})

    elif data.startswith("del_gsc_"):
        parts = data.split("_")
        p_name, idx = parts[2], int(parts[3])
        if 0 <= idx < len(bot_settings.get(f"{p_name}_search_countries", [])):
            del bot_settings[f"{p_name}_search_countries"][idx]; save_db()
            answer_callback(call["id"], "✅ Deleted!", show_alert=True)
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": f"manage_sc_{p_name}", "id": call["id"]})

    elif data.startswith("manage_srv_"):
        p_name = data.split("_")[2]
        kb = []
        srvs = bot_settings.get(f"{p_name}_services", {})
        for srv in srvs:
            kb.append([{"text": f"{srv}", "icon_custom_emoji_id": "5257969839313526622", "callback_data": f"gsrv_{p_name}_{srv}", "style": "primary"}])
        kb.append([{"text": "Add New Service", "icon_custom_emoji_id": "5420323438508155202", "callback_data": f"gadd_srv_{p_name}", "style": "success"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"{p_name}_control", "style": "danger"}])
        edit_message(chat_id, msg_id, render_body_text(f"📦 <b>{p_name.title()} Services Manager</b>"), reply_markup={"inline_keyboard": kb})

    elif data.startswith("gadd_srv_"):
        p_name = data.split("_")[2]
        user_states[chat_id] = "wait_gadd_srv"
        temp_data[chat_id] = {"msg_id": msg_id, "pnl": p_name}
        edit_message(chat_id, msg_id, render_body_text("📝 Enter Service Name (e.g. TELEGRAM):"), reply_markup=get_cancel_kb())

    elif data.startswith("gsrv_"):
        parts = data.split("_")
        p_name = parts[1]
        srv = "_".join(parts[2:])
        kb = []
        countries = bot_settings[f"{p_name}_services"].get(srv, {})
        for c in countries: kb.append([{"text": f"{c} ({len(countries[c])} Ranges)", "icon_custom_emoji_id": "5780471598922337683", "callback_data": f"gcnt_{p_name}_{srv}_{c}", "style": "primary"}])
        kb.append([{"text": "Add Country", "icon_custom_emoji_id": "5420323438508155202", "callback_data": f"gadd_cnt_{p_name}_{srv}", "style": "success"}])
        kb.append([{"text": "Delete Service", "icon_custom_emoji_id": "5422557736330106570", "callback_data": f"gdel_srv_{p_name}_{srv}", "style": "danger"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"manage_srv_{p_name}", "style": "primary"}])
        edit_message(chat_id, msg_id, render_body_text(f"📂 <b>Service: {srv}</b>"), reply_markup={"inline_keyboard": kb})

    elif data.startswith("gadd_cnt_"):
        parts = data.split("_")
        p_name = parts[2]
        srv = "_".join(parts[3:])
        user_states[chat_id] = "wait_gadd_cnt"
        temp_data[chat_id] = {"msg_id": msg_id, "pnl": p_name, "srv": srv}
        edit_message(chat_id, msg_id, render_body_text(f"🌍 Enter Country Name for <b>{srv}</b>:"), reply_markup=get_cancel_kb())

    elif data.startswith("gcnt_"):
        parts = data.split("_")
        p_name = parts[1]
        cnt = parts[-1]
        srv = "_".join(parts[2:-1])
        ranges = bot_settings[f"{p_name}_services"][srv].get(cnt, [])
        kb, row = [], []
        for r in ranges:
            row.append({"text": f"Delete {r}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"gdr_{p_name}_{srv}_{cnt}_{r}", "style": "danger"})
            if len(row) == 2: kb.append(row); row = []
        if row: kb.append(row)
        kb.append([{"text": "Add Range", "icon_custom_emoji_id": "5420323438508155202", "callback_data": f"gaddr_{p_name}_{srv}_{cnt}", "style": "success"}])
        kb.append([{"text": "Delete Entire Country", "icon_custom_emoji_id": "5422557736330106570", "callback_data": f"gdel_cnt_{p_name}_{srv}_{cnt}", "style": "danger"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"gsrv_{p_name}_{srv}", "style": "primary"}])
        edit_message(chat_id, msg_id, render_body_text(f"📍 <b>Service: {srv} | Country: {cnt}</b>\n\n<b>Total Ranges:</b> {len(ranges)}"), reply_markup={"inline_keyboard": kb})

    elif data.startswith("gaddr_"):
        parts = data.split("_")
        p_name = parts[1]
        cnt = parts[-1]
        srv = "_".join(parts[2:-1])
        user_states[chat_id] = "wait_gadd_rng"
        temp_data[chat_id] = {"msg_id": msg_id, "pnl": p_name, "srv": srv, "cnt": cnt}
        edit_message(chat_id, msg_id, render_body_text(f"📝 Send the new Range for <b>{cnt}</b>:"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"gcnt_{p_name}_{srv}_{cnt}", "style": "danger"}]]})

    elif data.startswith("gdr_"):
        parts = data.split("_")
        p_name = parts[1]
        rng = parts[-1]
        cnt = parts[-2]
        srv = "_".join(parts[2:-2])
        if rng in bot_settings[f"{p_name}_services"].get(srv, {}).get(cnt, []):
            bot_settings[f"{p_name}_services"][srv][cnt].remove(rng); save_db()
        handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": f"gcnt_{p_name}_{srv}_{cnt}", "id": call["id"]})

    elif data.startswith("gdel_srv_"):
        parts = data.split("_")
        p_name = parts[2]
        srv = "_".join(parts[3:])
        if srv in bot_settings[f"{p_name}_services"]: del bot_settings[f"{p_name}_services"][srv]; save_db()
        handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": f"manage_srv_{p_name}", "id": call["id"]})

    elif data.startswith("gdel_cnt_"):
        parts = data.split("_")
        p_name = parts[2]
        cnt = parts[-1]
        srv = "_".join(parts[3:-1])
        if cnt in bot_settings[f"{p_name}_services"].get(srv, {}): del bot_settings[f"{p_name}_services"][srv][cnt]; save_db()
        handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": f"gsrv_{p_name}_{srv}", "id": call["id"]})

    elif data == "manage_fj":
        edit_message(chat_id, msg_id, render_body_text(f"{PEM['link']} <b>FORCE JOIN SYSTEM</b>\nManage channels below:"), reply_markup=fj_settings_keyboard())

    elif data == "toggle_fj":
        bot_settings["fj_on"] = not bot_settings["fj_on"]
        save_db()
        edit_message(chat_id, msg_id, render_body_text(f"{PEM['link']} <b>FORCE JOIN SYSTEM</b>\nManage channels below:"), reply_markup=fj_settings_keyboard())

    elif data == "add_fj":
        user_states[chat_id] = "wait_for_add_fj"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send Channel Username or Invite Link:\n<i>(Note: For private channels, use the numeric ID like -100...)</i>"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_fj", "style": "danger"}]]})

    elif data.startswith("del_fj_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(bot_settings["fj_channels"]):
            del bot_settings["fj_channels"][idx]
            save_db()
            answer_callback(call["id"], "✅ Channel deleted!", show_alert=True)
            edit_message(chat_id, msg_id, render_body_text(f"{PEM['link']} <b>FORCE JOIN SYSTEM</b>\nManage channels below:"), reply_markup=fj_settings_keyboard())

    elif data == "manage_admins":
        edit_message(chat_id, msg_id, render_body_text(f"{PEM['user']} <b>ADMIN MANAGEMENT</b>\nManage your bot admins below:"), reply_markup=admin_settings_keyboard())

    elif data == "add_adm":
        user_states[chat_id] = "wait_for_add_adm"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the User ID of the new Admin:"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_admins", "style": "danger"}]]})

    elif data.startswith("del_adm_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(bot_settings["admins"]):
            del bot_settings["admins"][idx]
            save_db()
            answer_callback(call["id"], "✅ Admin deleted!", show_alert=True)
            edit_message(chat_id, msg_id, render_body_text(f"{PEM['user']} <b>ADMIN MANAGEMENT</b>\nManage your bot admins below:"), reply_markup=admin_settings_keyboard())

    elif data == "manage_otp_groups":
        edit_message(chat_id, msg_id, render_body_text("🛡 <b>OTP GROUP MANAGEMENT</b>\nManage settings below:"), reply_markup=otp_groups_list_keyboard())

    elif data == "add_fw":
        user_states[chat_id] = "wait_for_add_fw_id"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the Group ID/Username to forward messages to:"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_otp_groups", "style": "danger"}]]})

    elif data.startswith("manage_fw_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(bot_settings["fw_groups"]):
            grp_id = bot_settings["fw_groups"][idx]["chat_id"]
            edit_message(chat_id, msg_id, render_body_text(f"🛡 <b>Manage Group:</b> {grp_id}"), reply_markup=specific_fw_group_keyboard(idx))

    elif data.startswith("add_fwbtn_"):
        idx = int(data.split("_")[2])
        user_states[chat_id] = "wait_for_add_fw_btn"
        temp_data[chat_id] = {"msg_id": msg_id, "fw_idx": idx}
        edit_message(chat_id, msg_id, render_body_text("📝 Send Custom Inline Button format:\n<code>Button Text - https://link.com</code>"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"manage_fw_{idx}", "style": "danger"}]]})

    elif data.startswith("del_fwbtn_"):
        parts = data.split("_")
        idx, b_idx = int(parts[2]), int(parts[3])
        if 0 <= idx < len(bot_settings["fw_groups"]):
            if 0 <= b_idx < len(bot_settings["fw_groups"][idx]["buttons"]):
                del bot_settings["fw_groups"][idx]["buttons"][b_idx]
                save_db()
                answer_callback(call["id"], "✅ Button deleted!", show_alert=True)
                edit_message(chat_id, msg_id, render_body_text(f"🛡 <b>Manage Group:</b> {bot_settings['fw_groups'][idx]['chat_id']}"), reply_markup=specific_fw_group_keyboard(idx))

    elif data.startswith("del_fw_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(bot_settings["fw_groups"]):
            del bot_settings["fw_groups"][idx]
            save_db()
            answer_callback(call["id"], "✅ Group deleted!", show_alert=True)
            edit_message(chat_id, msg_id, render_body_text("🛡 <b>OTP GROUP MANAGEMENT</b>\nManage settings below:"), reply_markup=otp_groups_list_keyboard())

    elif data == "edit_otp_link":
        user_states[chat_id] = "wait_for_otp_link"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the new OTP Group Link:"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_otp_groups", "style": "danger"}]]})

    elif data == "manage_panels":
        api_count = len([p for p in bot_settings["panels"] if p.get("type") == "API Panel"])
        cpt_count = len([p for p in bot_settings["panels"] if p.get("type", "API Panel") == "Auto Captcha Panel"])
        text = f"{PEM['gear']} <b>Panel Management</b>\n\nSelect which type of panel system you want to manage:"
        kb = {"inline_keyboard": [
            [{"text": f"Manage API Panels ({api_count})", "icon_custom_emoji_id": "5336972142066047577", "callback_data": "manage_api_panels", "style": "primary"}],
            [{"text": f"Manage Auto Captcha Panels ({cpt_count})", "icon_custom_emoji_id": "5353022963132174959", "callback_data": "manage_cpt_panels", "style": "success"}],
            [{"text": "Back to System", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "system_settings", "style": "danger"}]
        ]}
        edit_message(chat_id, msg_id, render_body_text(text), reply_markup=kb)

    elif data in ["manage_api_panels", "manage_cpt_panels"]:
        p_type = "API Panel" if data == "manage_api_panels" else "Auto Captcha Panel"
        p_list = [p for p in bot_settings["panels"] if p.get("type", "API Panel") == p_type]
        icon = f"{PEM['world']} API" if p_type == 'API Panel' else f"{PEM['lock']} Auto Captcha"
        
        text = f"{icon} <b>{p_type}s Management</b>\n\n👀 <b>Active Monitors:</b> {len(p_list)}\n\n🟢 <b>Available Providers:</b>\n"
        for p in p_list:
            status = "Monitoring" if p['status'] == 'ON' else "Stopped"
            login_state = p.get('login_status', '')
            if p['type'] == 'Auto Captcha Panel':
                conf = f" {login_state}" if login_state else f"{PEM['ok']} Configured"
            else:
                conf = f"{PEM['ok']} Configured" if p.get('api_url') else f"{PEM['no']} Not Configured"
            text += f"• {p['name']}: {PEM['ok'] if p['status']=='ON' else PEM['no']} {status} | {conf}\n"
        edit_message(chat_id, msg_id, render_body_text(text), reply_markup=typed_panels_list_keyboard(p_type))

    elif data in ["add_api_panel", "add_cpt_panel"]:
        user_states[chat_id] = "wait_for_panel_name"
        p_type = "api" if data == "add_api_panel" else "logc"
        temp_data[chat_id] = {"msg_id": msg_id, "add_type": p_type}
        edit_message(chat_id, msg_id, render_body_text("📝 Please send the name of the New Provider:"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"manage_{'api' if p_type=='api' else 'cpt'}_panels", "style": "danger"}]]})

    elif data.startswith("add_ptype_"):
        pass

    elif data in ["list_del_api", "list_del_cpt"]:
        p_type = "API Panel" if data == "list_del_api" else "Auto Captcha Panel"
        kb = []
        for idx, p in enumerate(bot_settings["panels"]):
            if p.get("type", "API Panel") == p_type:
                kb.append([{"text": f"Delete {p['name']}", "icon_custom_emoji_id": "5420130255174145507", "callback_data": f"do_del_pnl_{idx}", "style": "danger"}])
        kb.append([{"text": "Back", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"manage_{'api' if p_type=='API Panel' else 'cpt'}_panels", "style": "primary"}])
        edit_message(chat_id, msg_id, render_body_text(f"{PEM['trash']} <b>Select a Provider to Delete:</b>"), reply_markup={"inline_keyboard": kb})

    elif data.startswith("do_del_pnl_"):
        idx = int(data.split("_")[3])
        if 0 <= idx < len(bot_settings["panels"]):
            p_type = bot_settings["panels"][idx].get("type", "API Panel")
            del bot_settings["panels"][idx]
            save_db()
            answer_callback(call["id"], "✅ Provider Deleted!", show_alert=True)
            handle_callback({"message": {"chat": {"id": chat_id}, "message_id": msg_id}, "data": f"manage_{'api' if p_type=='API Panel' else 'cpt'}_panels", "id": "internal"})

    elif data.startswith("tog_pnl_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(bot_settings["panels"]):
            p = bot_settings["panels"][idx]
            
            p["status"] = "ON" if p["status"] == "OFF" else "OFF"
            save_db()
            
            if p["type"] == "Auto Captcha Panel":
                text = f"⚙️ <b>Configure {p['name']}</b>\n\n<b>Type:</b> {p['type']}\n<b>Status:</b> {'🟢 Monitoring' if p['status'] == 'ON' else '🔴 Stopped'}\n<b>Login Status:</b> {p.get('login_status', 'Unknown')}\n<b>Login URL:</b> <code>{p.get('login_url', 'None')}</code>\n<b>User:</b> <code>{p.get('username', 'None')}</code>"
            else:
                text = f"⚙️ <b>Configure {p['name']}</b>\n\n<b>Type:</b> {p['type']}\n<b>Status:</b> {'🟢 Monitoring' if p['status'] == 'ON' else '🔴 Stopped'}\n<b>API URL:</b> <code>{p.get('api_url', 'None')}</code>\n<b>Token:</b> <code>{p.get('token', 'None')}</code>"
            edit_message(chat_id, msg_id, render_body_text(text), reply_markup=panel_config_keyboard(idx))

    elif data.startswith("conf_pnl_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(bot_settings["panels"]):
            p = bot_settings["panels"][idx]
            if p["type"] == "Auto Captcha Panel":
                text = f"⚙️ <b>Configure {p['name']}</b>\n\n<b>Type:</b> {p['type']}\n<b>Status:</b> {'🟢 Monitoring' if p['status'] == 'ON' else '🔴 Stopped'}\n<b>Login Status:</b> {p.get('login_status', 'Unknown')}\n<b>Login URL:</b> <code>{p.get('login_url', 'None')}</code>\n<b>User:</b> <code>{p.get('username', 'None')}</code>\n<b>Num Col:</b> {p.get('num_col_name')} (Idx: {p.get('num_col_idx')})\n<b>Msg Col:</b> {p.get('msg_col_name')} (Idx: {p.get('msg_col_idx')})"
            else:
                text = f"⚙️ <b>Configure {p['name']}</b>\n\n<b>Type:</b> {p['type']}\n<b>Status:</b> {'🟢 Monitoring' if p['status'] == 'ON' else '🔴 Stopped'}\n<b>API URL:</b> <code>{p.get('api_url', 'None')}</code>\n<b>Token:</b> <code>{p.get('token', 'None')}</code>\n<b>Full API URL:</b> <code>{p.get('full_api_url', 'None')}</code>"
            edit_message(chat_id, msg_id, render_body_text(text), reply_markup=panel_config_keyboard(idx))

    elif data.startswith("set_p_api_"):
        idx = int(data.split("_")[3])
        user_states[chat_id] = "wait_for_p_api"
        temp_data[chat_id] = {"msg_id": msg_id, "p_idx": idx}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the API URL for this provider:"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"conf_pnl_{idx}", "style": "danger"}]]})

    elif data.startswith("set_p_tok_"):
        idx = int(data.split("_")[3])
        user_states[chat_id] = "wait_for_p_tok"
        temp_data[chat_id] = {"msg_id": msg_id, "p_idx": idx}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the Token for this provider:"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"conf_pnl_{idx}", "style": "danger"}]]})

    elif data.startswith("set_p_fapi_"):
        idx = int(data.split("_")[3])
        user_states[chat_id] = "wait_for_p_fapi"
        temp_data[chat_id] = {"msg_id": msg_id, "p_idx": idx}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the FULL API URL (Example: http://api.com/get?key=YOUR_TOKEN&start=0):"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"conf_pnl_{idx}", "style": "danger"}]]})

    elif data.startswith("set_p_rec_"):
        idx = int(data.split("_")[3])
        user_states[chat_id] = "wait_for_p_rec"
        temp_data[chat_id] = {"msg_id": msg_id, "p_idx": idx}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the number of records to fetch (e.g. 10).\nType <code>0</code> for Unlimited:"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": f"conf_pnl_{idx}", "style": "danger"}]]})

    elif data.startswith("test_p_conn_"):
        idx = int(data.split("_")[3])
        p = bot_settings["panels"][idx]
        wait_msg = send_message(chat_id, render_body_text("⏳ Testing connection. Please wait..."))
        wait_msg_id = wait_msg.get("result", {}).get("message_id") if wait_msg else None
        answer_callback(call["id"])
        
        try:
            parsed = []
            raw_text = ""
            
            if p["type"] == "Auto Captcha Panel":
                sess = panel_sessions.get(idx)
                if not sess:
                    success = attempt_auto_login(p, idx)
                    if not success:
                        if wait_msg_id: delete_message(chat_id, wait_msg_id)
                        send_message(chat_id, render_body_text(f"❌ <b>Auto Login Failed!</b>\nReason: {html.escape(str(p.get('login_status', 'Unknown')))}"))
                        return
                    sess = panel_sessions.get(idx)
                    
                login_url = p.get("login_url", "").strip()
                if not login_url.startswith("http"): login_url = "http://" + login_url
                msg_link = p.get("msg_link", "").strip()
                if not msg_link.startswith("http") and msg_link != "": msg_link = "http://" + msg_link
                check_url = msg_link if msg_link else f"{login_url.split('/login')[0]}/client/SMSCDRStats"
                
                # 🌟 test connection supports sAjaxSource & HTML table parser
                parsed, raw_text = fetch_cpt_panel_cdrs(p, sess, check_url)
                
            else:
                full_url = p.get("full_api_url", "").strip()
                url = p.get("api_url", "").strip()
                token = p.get("token", "").strip()
                if not full_url and not url:
                    if wait_msg_id: delete_message(chat_id, wait_msg_id)
                    send_message(chat_id, render_body_text("❌ Please Set API URL or Full API URL first!"))
                    return
                
                urls_to_try = []
                if full_url:
                    urls_to_try.append(full_url)
                else:
                    if "{token}" in url or "{key}" in url:
                        urls_to_try.append(url.replace("{token}", token).replace("{key}", token))
                    elif "token=" in url or "key=" in url:
                        urls_to_try.append(url)
                    else:
                        sep = '&' if '?' in url else '?'
                        urls_to_try.append(f"{url}{sep}token={token}")
                        urls_to_try.append(f"{url}{sep}key={token}&start=0")
                        urls_to_try.append(f"{url}{sep}key={token}")
                    
                parsed = []
                raw_text = ""
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
                for try_url in urls_to_try:
                    try:
                        res = requests.get(try_url, headers=headers, timeout=10)
                        raw_text = res.text
                        parsed = parse_panel_response(raw_text, p)
                        if parsed:
                            if not full_url and try_url != url and token:
                                p["api_url"] = try_url.replace(token, "{token}")
                                save_db()
                            break
                    except: pass
                 
            if wait_msg_id: delete_message(chat_id, wait_msg_id)
                 
            if parsed:
                txt = f"✅ <b>Connection Successful!</b>\n\n🎯 <b>Parsed Data Sample (Max 3):</b>\n\n"
                
                for i, sample in enumerate(parsed[:3]):
                    num = sample['number']
                    msg = sample['message']
                    otp = sample['otp']
                    
                    detected_app = detect_service(msg)
                    app_name = detected_app if detected_app else p.get("name", "Unknown")
                    app_full_name, prem_app_html = get_service_info_html(app_name, msg)
                    
                    txt += f"<b>{i+1}.</b> {prem_app_html} <b>{app_full_name}</b>\n"
                    txt += f"📱 Number: <code>{num}</code>\n"
                    txt += f"📝 Full Msg: <code>{html.escape(msg)}</code>\n"
                    txt += f"🔐 OTP: <code>{otp}</code>\n"
                    txt += "➖" * 12 + "\n"
                    
                send_message(chat_id, render_body_text(txt))
            else:
                if p["type"] == "Auto Captcha Panel":
                    try:
                        soup = BeautifulSoup(raw_text, 'html.parser')
                        tables = soup.find_all('table')
                        if tables:
                            full_table_data = "🔍 FULL TABLE DATA (A-Z)\n" + "="*50 + "\n\n"
                            for t_idx, table in enumerate(tables):
                                full_table_data += f"--- Table {t_idx+1} ---\n"
                                rows = table.find_all('tr')
                                for r_idx, row in enumerate(rows):
                                    cols = row.find_all(['th', 'td'])
                                    col_texts = [f"[{c_idx+1}] {c.get_text(separator=' ', strip=True)}" for c_idx, c in enumerate(cols)]
                                    full_table_data += f"Row {r_idx+1}: {' | '.join(col_texts)}\n"
                                full_table_data += "\n" + "="*50 + "\n"
                            
                            send_document(chat_id, f"Full_Panel_Data_{idx}.txt", full_table_data.encode('utf-8'))
                            fail_txt = f"⚠️ <b>Connected, but couldn't parse OTP data!</b>\n\n<i>আমি ওই লিংকের সম্পূর্ণ (A-Z) ডাটা একটি Text File এ পাঠিয়েছি। ফাইলটি ওপেন করে সঠিক Column Number (যেমন: [1], [3]) চেক করে প্যানেলে আপডেট করে নাও।</i>"
                            send_message(chat_id, render_body_text(fail_txt))
                        else:
                            send_message(chat_id, render_body_text(f"⚠️ <b>Connected, but no HTML Table found!</b>\nMake sure the message link is correct."))
                    except Exception as e:
                        send_message(chat_id, render_body_text(f"❌ <b>Error parsing HTML:</b> {html.escape(str(e))}"))
                else:
                    safe_html = html.escape(str(raw_text)[:300])
                    send_message(chat_id, render_body_text(f"⚠️ <b>Connected, but couldn't find/parse OTP data.</b>\n\n<i>Make sure your API config is correct.</i>\n\nRaw HTML/Data (excerpt):\n<code>{safe_html}...</code>"))
        except Exception as e:
            if wait_msg_id: delete_message(chat_id, wait_msg_id)
            send_message(chat_id, render_body_text(f"❌ <b>Connection Failed!</b>\nError: {html.escape(str(e))}"))

    elif data == "tre_control":
        if chat_id in user_states: del user_states[chat_id]
        edit_message(chat_id, msg_id, render_body_text("🕹 <b>TRE CONTROL PANEL</b>"), reply_markup=tre_control_keyboard())

    elif data == "tre_toggle_w":
        bot_settings["withdraw_on"] = not bot_settings["withdraw_on"]
        save_db()
        edit_message(chat_id, msg_id, render_body_text("🕹 <b>TRE CONTROL PANEL</b>"), reply_markup=tre_control_keyboard())

    elif data == "manage_w_methods":
        edit_message(chat_id, msg_id, render_body_text("💳 <b>WITHDRAWAL METHODS</b>\n\nManage your withdrawal methods below:"), reply_markup=w_methods_keyboard())

    elif data == "add_wm":
        user_states[chat_id] = "wait_for_add_wm"
        temp_data[chat_id] = {"msg_id": msg_id}
        edit_message(chat_id, msg_id, render_body_text("📝 Send the name of the new Withdrawal Method:"), reply_markup={"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "manage_w_methods", "style": "danger"}]]})

    elif data.startswith("del_wm_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(bot_settings["w_methods"]):
            del bot_settings["w_methods"][idx]
            save_db()
            answer_callback(call["id"], "✅ Method deleted!", show_alert=True)
            edit_message(chat_id, msg_id, render_body_text("💳 <b>WITHDRAWAL METHODS</b>\n\nManage your withdrawal methods below:"), reply_markup=w_methods_keyboard())

    elif data.startswith("tre_"):
        key = data.replace("tre_", "")
        key_map = {"min_w": "min_withdraw", "otp_r": "otp_reward", "ref_r": "refer_reward", "cool": "cooldown", "num_req": "num_req", "num_share": "num_share", "sup_link": "support_link", "w_group": "w_group"}
        if key in key_map:
            temp_data[chat_id] = {"msg_id": msg_id, "key": key_map[key]}
            user_states[chat_id] = "set_tre"
            cancel_kb = {"inline_keyboard": [[{"text": "Cancel", "icon_custom_emoji_id": "5267490665117275176", "callback_data": "cancel_tre_edit", "style": "danger"}]]}
            edit_message(chat_id, msg_id, render_body_text(f"📝 Please send the new value for <code>{key_map[key]}</code>:"), reply_markup=cancel_kb)
            answer_callback(call["id"])

    elif data == "back_to_services":
        txt, kb = _service_list_ui()
        if not txt:
            answer_callback(call["id"], "❌ No numbers or services available!", show_alert=True)
            return
        edit_message(chat_id, msg_id, txt, reply_markup=kb)

    elif data.startswith("g_s_"):
        service = data.split("g_s_", 1)[1]
        txt, kb = _country_list_ui(service)
        edit_message(chat_id, msg_id, txt, reply_markup=kb)

    elif data.startswith("g_p_"):
        parts = data.split("_", 3)
        service, country = parts[2], parts[3]
        pid = _pick_panel(service, country)
        if pid is None:
            answer_callback(call["id"], "❌ Number out of stock!", show_alert=True)
            return
        handle_callback(dict(call, data=f"g_c_{pid}_{service}_{country}"))

    elif data.startswith("g_c_") or data.startswith("c_n_"):
        # ১. গ্লোবাল কুলডাউন চেক (সকল নাম্বার মেথডের জন্য)
        now = time.time()
        if now - user_cooldowns.get(chat_id, 0) < bot_settings["cooldown"]:
            answer_callback(call["id"], f"⌛ Please wait {int(bot_settings['cooldown'] - (now - user_cooldowns.get(chat_id, 0)))}s.", show_alert=True)
            return
        
        # কুলডাউন আপডেট
        user_cooldowns[chat_id] = now
        
        # আগের নাম্বার এক্সপায়ার করা
        expire_previous_number(chat_id)

        # যদি সার্চ নাম্বার থেকে আসে
        if data.startswith("c_n_s_"):
            is_voltx_req = data.endswith("_vtx")
            is_stex_req = data.endswith("_stx")
            is_zenex_req = data.endswith("_znx")
            is_zebra_req = data.endswith("_zbr")
            is_scarface_req = data.endswith("_scf")
            
            specific_req = is_voltx_req or is_stex_req or is_zenex_req or is_zebra_req or is_scarface_req
            clean_data = data[:-4] if specific_req else data
            parts_s = clean_data.split("_", 4)
            
            query = parts_s[3] if len(parts_s) > 3 else ""
            service_from_cb = parts_s[4] if len(parts_s) > 4 else None
            
            allowed_countries = bot_settings.get("search_countries", [])
            voltx_allowed = bot_settings.get("voltx_search_countries", [])
            zenex_allowed = bot_settings.get("zenex_search_countries", [])
            zebra_allowed = bot_settings.get("zebra_search_countries", [])
            scarface_allowed = bot_settings.get("scarface_search_countries", [])
            
            is_stex_allowed = any(query.startswith(c) for c in allowed_countries) if allowed_countries else False
            is_voltx_allowed = any(query.startswith(c) for c in voltx_allowed) if voltx_allowed else False
            is_zenex_allowed = any(query.startswith(c) for c in zenex_allowed) if zenex_allowed else False
            is_zebra_allowed = any(query.startswith(c) for c in zebra_allowed) if zebra_allowed else False
            is_scarface_allowed = any(query.startswith(c) for c in scarface_allowed) if scarface_allowed else False
            
            if not is_voltx_req and not is_stex_allowed and not is_voltx_allowed and not is_zenex_allowed and not is_zebra_allowed and not is_scarface_allowed:
                answer_callback(call["id"], "❌ This country code is not allowed for search!", show_alert=True)
                return
                
            edit_message(chat_id, msg_id, render_body_text("⌛ <i>Processing... Finding Number...</i>"))
            wait_msg_id = msg_id
            
            found_indices = []
            for b_id, b_data in number_batches.items():
                for idx, n_obj in enumerate(b_data["numbers"]):
                    if n_obj["num"].replace("+", "").startswith(query) and chat_id not in n_obj.get("used_by", []):
                        found_indices.append((b_id, idx))
            
            fetched_nums = []
            if not found_indices:
                api_found = False
                req_count = bot_settings.get("num_req", 1)

                # Voltx SMS Check
                if len(fetched_nums) < req_count and (is_voltx_req or (not specific_req and is_voltx_allowed)):
                    keys = bot_settings.get("voltx_keys", [])
                    for _ in range(req_count - len(fetched_nums)):
                        for api_key in keys:
                            try:
                                res = requests.post(f"{VOLTX_BASE_URL}/getnum", json={"rid": query}, headers={"mauthapi": api_key}, timeout=10)
                                d = res.json()
                                if d.get("meta", {}).get("code") == 200 and d.get("data"):
                                    num_str = str(d["data"].get("no_plus_number", "")).replace("+", "")
                                    if not num_str: num_str = str(d["data"].get("national_number", ""))
                                    fetched_nums.append(num_str); voltx_assigned_numbers[num_str] = chat_id; api_found = True; total_assigned_stats += 1
                                    break
                            except: continue

                # Stex SMS Check
                if len(fetched_nums) < req_count and (is_stex_req or (not specific_req and is_stex_allowed)):
                    keys = bot_settings.get("stex_keys", [])
                    for _ in range(req_count - len(fetched_nums)):
                        for api_key in keys:
                            try:
                                res = requests.post(f"{STEX_BASE_URL}/getnum", json={"rid": query}, headers={"mauthapi": api_key}, timeout=10)
                                d = res.json()
                                if d.get("meta", {}).get("code") == 200 and d.get("data"):
                                    num_str = str(d["data"].get("no_plus_number", "")).replace("+", "")
                                    if not num_str: num_str = str(d["data"].get("national_number", ""))
                                    fetched_nums.append(num_str); stex_assigned_numbers[num_str] = chat_id; api_found = True; total_assigned_stats += 1
                                    break
                            except: continue

                # Zenex SMS Check
                if len(fetched_nums) < req_count and (is_zenex_req or (not specific_req and is_zenex_allowed)):
                    keys = bot_settings.get("zenex_keys", [])
                    for _ in range(req_count - len(fetched_nums)):
                        for api_key in keys:
                            try:
                                res = requests.post(f"{ZENEX_BASE_URL}/v1/getnum", json={"range": query, "is_national": False, "remove_plus": False}, headers={"mapikey": api_key, "Content-Type": "application/json"}, timeout=10)
                                d = res.json()
                                if d.get("meta", {}).get("code") == 200 and d.get("data"):
                                    num_str = str(d["data"].get("number", "")).replace("+", "")
                                    fetched_nums.append(num_str); zenex_assigned_numbers[num_str] = chat_id; api_found = True; total_assigned_stats += 1
                                    break
                            except: continue

                # Zebra SMS Check
                if len(fetched_nums) < req_count and (is_zebra_req or (not specific_req and is_zebra_allowed)):
                    keys = bot_settings.get("zebra_keys", [])
                    for _ in range(req_count - len(fetched_nums)):
                        for api_key in keys:
                            try:
                                res = requests.post(f"{ZEBRA_BASE_URL}/api/v1/publicapi/getnum", json={"range": query}, headers={"MAuth": api_key, "Content-Type": "application/json"}, timeout=10)
                                d = res.json()
                                if d.get("meta", {}).get("code") == 0 and d.get("data", {}).get("rows"):
                                    num_str = str(d["data"]["rows"][0].get("number", "")).replace("+", "")
                                    fetched_nums.append(num_str); zebra_assigned_numbers[num_str] = chat_id; api_found = True; total_assigned_stats += 1
                                    break
                            except: continue

                # ScarFace SMS Check
                if len(fetched_nums) < req_count and (is_scarface_req or (not specific_req and is_scarface_allowed)):
                    keys = bot_settings.get("scarface_keys", [])
                    for _ in range(req_count - len(fetched_nums)):
                        for api_key in keys:
                            try:
                                res = requests.post(f"{SCARFACE_BASE_URL}/numbers/allocate", json={"prefix": query}, headers={"X-API-Key": api_key, "Content-Type": "application/json"}, timeout=10)
                                d = res.json()
                                if res.status_code in [200, 201] and d.get("msisdn"):
                                    num_str = str(d.get("msisdn", "")).replace("+", "")
                                    fetched_nums.append(num_str); scarface_assigned_numbers[num_str] = chat_id; api_found = True; total_assigned_stats += 1
                                    break
                            except: continue

                if not api_found:
                    answer_callback(call["id"], "❌ Number out of stock!", show_alert=True)
                    delete_message(chat_id, wait_msg_id)
                    return
                save_db()
            else:
                random.shuffle(found_indices)
                for b_id, idx in found_indices:
                    if len(fetched_nums) >= bot_settings.get("num_req", 1): break
                    n_obj = number_batches[b_id]["numbers"][idx]
                    num_str = n_obj["num"]
                    fetched_nums.append(num_str)
                    n_obj["shares"] += 1
                    n_obj["used_by"].append(chat_id)
                    total_assigned_stats += 1
                    
                    b_srv = number_batches[b_id].get("service", service_from_cb if 'service_from_cb' in locals() and service_from_cb else "Search")
                    b_reward = number_batches[b_id].get("custom_otp_rate", bot_settings.get("otp_reward", 0.0))
                    assigned_number_metadata[str(num_str).replace("+", "").strip()] = {"service": b_srv, "reward": b_reward, "owner": chat_id}
                    
                    if db:
                        try: pass  # OTP count ekhon receive howar somoy hoy
                        except: pass
                        
                    if n_obj["shares"] >= bot_settings.get("num_share", 1):
                        n_obj["to_remove"] = True
                        used_numbers_list.append(num_str)
                for b_id in number_batches:
                    number_batches[b_id]["numbers"] = [n for n in number_batches[b_id]["numbers"] if not n.get("to_remove")]
                save_db()
                
            vtx_ext = "_vtx" if 'is_voltx_req' in locals() and is_voltx_req else ""
            stx_ext = "_stx" if 'is_stex_req' in locals() and is_stex_req else ""
            znx_ext = "_znx" if 'is_zenex_req' in locals() and is_zenex_req else ""
            zbr_ext = "_zbr" if 'is_zebra_req' in locals() and is_zebra_req else ""
            scf_ext = "_scf" if 'is_scarface_req' in locals() and is_scarface_req else ""
            all_exts = f"{vtx_ext}{stx_ext}{znx_ext}{zbr_ext}{scf_ext}"
            srv_ext = f"_{service_from_cb}" if service_from_cb else ""

            text_numbers, kb = _number_card(chat_id, fetched_nums, service_from_cb, f"c_n_s_{query}{srv_ext}{all_exts}",
                                            bot_settings["custom_messages"].get("search_number", {}).get("buttons", []))
            edit_message(chat_id, wait_msg_id, text_numbers, reply_markup={"inline_keyboard": kb})
            user_active_sessions[chat_id] = {"msg_id": wait_msg_id, "nums": fetched_nums, "service": service_from_cb}
            if bot_settings.get("ws_on", True): ws_checker.decorate(chat_id, fetched_nums, kb, text_numbers)
            answer_callback(call["id"], "✅ New Numbers Generated!") if data.startswith("c_n_") else answer_callback(call["id"])
            return

        # যদি আপলোড করা বা সার্ভিস থেকে আসে
        parts = data.split("_", 4)
        pid = parts[2]
        service = parts[3]
        country = parts[4]

        available_indices = []
        target_range = None
        is_voltx, is_stex, is_zenex, is_zebra, is_scarface = False, False, False, False, False

        if pid == "0":
            for b_id, b_data in number_batches.items():
                if b_data["service"] == service and b_data["country"] == country:
                    for idx, n_obj in enumerate(b_data["numbers"]):
                        if chat_id not in n_obj.get("used_by", []): available_indices.append((b_id, idx))
        elif pid == "1":
            srv_data = bot_settings.get("voltx_services", {}).get(service, {}).get(country)
            if srv_data: target_range = random.choice(srv_data); is_voltx = True
        elif pid == "2":
            srv_data = bot_settings.get("stex_services", {}).get(service, {}).get(country)
            if srv_data: target_range = random.choice(srv_data); is_stex = True
        elif pid == "3":
            srv_data = bot_settings.get("zenex_services", {}).get(service, {}).get(country)
            if srv_data: target_range = random.choice(srv_data); is_zenex = True
        elif pid == "4":
            srv_data = bot_settings.get("zebra_services", {}).get(service, {}).get(country)
            if srv_data: target_range = random.choice(srv_data); is_zebra = True
        elif pid == "5":
            srv_data = bot_settings.get("scarface_services", {}).get(service, {}).get(country)
            if srv_data: target_range = random.choice(srv_data); is_scarface = True

        if target_range:
            user_cooldowns[chat_id] = 0
            flag = "_vtx" if is_voltx else ("_stx" if is_stex else ("_znx" if is_zenex else ("_zbr" if is_zebra else ("_scf" if is_scarface else ""))))
            handle_callback({"message": call["message"], "data": f"c_n_s_{target_range}_{service}{flag}", "id": call["id"]})
            return
        elif not available_indices and pid != "0":
            answer_callback(call["id"], "❌ Number out of stock or range missing!", show_alert=True)
            if data.startswith("c_n_"): delete_message(chat_id, msg_id)
            return

        random.shuffle(available_indices)
        fetched_nums = []
        for b_id, idx in available_indices:
            if len(fetched_nums) >= bot_settings["num_req"]: break
            n_obj = number_batches[b_id]["numbers"][idx]
            fetched_nums.append(n_obj["num"])
            n_obj["shares"] += 1
            n_obj["used_by"].append(chat_id)
            total_assigned_stats += 1
            b_reward = number_batches[b_id].get("custom_otp_rate", bot_settings.get("otp_reward", 0.0))
            assigned_number_metadata[str(n_obj["num"]).replace("+", "").strip()] = {"service": service, "reward": b_reward, "owner": chat_id}
            
            if pid == "0":
                if db:
                    try: pass  # OTP count ekhon receive howar somoy hoy
                    except: pass
                    
            if n_obj["shares"] >= bot_settings.get("num_share", 1):
                n_obj["to_remove"] = True; used_numbers_list.append(n_obj["num"])
        
        for b_id in number_batches: number_batches[b_id]["numbers"] = [n for n in number_batches[b_id]["numbers"] if not n.get("to_remove")]
        save_db()

        if not fetched_nums:
            answer_callback(call["id"], "❌ You have already taken all numbers or stock is empty!", show_alert=True)
            if data.startswith("c_n_"): delete_message(chat_id, msg_id)
            return

        text_numbers, kb = _number_card(chat_id, fetched_nums, service, f"c_n_{pid}_{service}_{country}",
                                        bot_settings["custom_messages"].get("get_number", {}).get("buttons", []))
        try:
            edit_message(chat_id, msg_id, text_numbers, reply_markup={"inline_keyboard": kb})
            user_active_sessions[chat_id] = {"msg_id": msg_id, "nums": fetched_nums, "service": service}
        except:
            msg_res = send_message(chat_id, text_numbers, reply_markup={"inline_keyboard": kb})
            if msg_res and "result" in msg_res:
                user_active_sessions[chat_id] = {"msg_id": msg_res["result"]["message_id"], "nums": fetched_nums, "service": service}
        if bot_settings.get("ws_on", True): ws_checker.decorate(chat_id, fetched_nums, kb, text_numbers)
        answer_callback(call["id"], "✅ New Numbers Generated!") if data.startswith("c_n_") else answer_callback(call["id"])

    elif data.startswith("wapp_") or data.startswith("wrej_"):
        # অ্যাডমিন চেক (User ID চেক করতে হবে)
        user_id_clicked = call["from"]["id"]
        if not is_admin(user_id_clicked):
            answer_callback(call["id"], "🚫 Only Bot Admins can process withdrawals!", show_alert=True)
            return
            
        action = "APPROVE" if data.startswith("wapp_") else "REJECT"
        req_id = data.replace("wapp_", "").replace("wrej_", "")
        
        if req_id in pending_withdrawals:
            req_data = pending_withdrawals[req_id]
            u_id, amt = req_data["user_id"], req_data["amount"]
            num = req_data["number"]
            full_name = req_data.get("full_name", u_id)
            
            if action == "APPROVE" and len(num) >= 7:
                masked_num = f"{num[:4]}❖𝗥𝗗𝗫❖{num[-3:]}"
            else:
                masked_num = num
            
            status_text = "APPROVED" if action == "APPROVE" else "REJECTED"
            emoji_icon_id = "5352694861990501856" if action == "APPROVE" else "5420130255174145507"
            new_text = f"🎙 <b>WITHDRAWAL {status_text}</b>\n\n👤 <b>USER:</b> <a href='tg://user?id={u_id}'>{full_name}</a>\n💳 <b>WITHDRAWAL:</b> {amt} TK\n🎟️ <b>NUMBER:</b> <code>{masked_num}</code>\n🍍 <b>METHOD:</b> {req_data['method']}\n\n🧾 <b>REQ ID:</b> {req_id}\n👨‍⚖️ <b>PROCESSED BY ADMIN</b>"
            
            kb = {"inline_keyboard": [[{"text": status_text, "icon_custom_emoji_id": emoji_icon_id, "callback_data": "ignore", "style": "success" if action == "APPROVE" else "danger"}]]}
            edit_message(chat_id, msg_id, render_body_text(new_text), reply_markup=kb)
            
            if action == "REJECT":
                update_balance(u_id, amt) 
                send_message(u_id, render_body_text(f"❌ Your {amt} TK withdrawal request was rejected. Balance refunded."))
            else:
                send_message(u_id, render_body_text(f"{PEM['ok']} Your {amt} TK withdrawal request has been paid successfully!"))
                _bump(u_id, inc={"total_paid": float(amt)})
            
            if db:
                try: db.collection('withdrawals').document(req_id).update({"status": "approved" if action == "APPROVE" else "rejected"})
                except: pass
                
            del pending_withdrawals[req_id]
        else:
            answer_callback(call["id"], "❌ Request already processed!", show_alert=True)

# ==========================================
# Polling Loop
# ==========================================
def poll_otp_with_status(number_id, num_str, owner_id, api_key):
    headers = {"X-API-Key": api_key}
    for _ in range(150): # 150 * 4 seconds = 10 Minutes Polling
        try:
            res = requests.get(f"{STEX_BASE_URL}/api/v1/numbers/{number_id}/sms", headers=headers, timeout=10)
            data = res.json()
            if data.get("success") and data.get("otp"):
                otp = str(data["otp"])
                msg_text = data.get("message", f"Your code is {otp}")
                
                # 🌟 সম্পূর্ণ মেসেজ থেকে ড্যাশসহ বা বড় OTP খোঁজার ফিক্স
                extracted_otp = extract_otp_code(msg_text)
                if extracted_otp and len(extracted_otp) > len(otp):
                    otp = extracted_otp
                    
                # 🌟 সম্পূর্ণ মেসেজ থেকে সার্ভিস/অ্যাপ চেনার ফিক্স
                app_name = data.get("service", "StexSMS Service")
                detected_app = detect_service(msg_text)
                if detected_app:
                    app_name = detected_app
                
                unique_id = f"POLL_{number_id}_{otp}"
                if unique_id not in processed_otps:
                    processed_otps.add(unique_id)
                    
                    char, iso = get_flag_and_code(num_str)
                    app_full_name, prem_app_html = get_service_info_html(app_name, msg_text)
                    
                    global recent_traffic
                    current_time = time.time()
                    recent_traffic = [t for t in recent_traffic if current_time - t.get("time", 0) <= 3600]
                    recent_traffic.append({"service": app_full_name, "iso": iso, "flag": char, "number": num_str, "time": current_time})
                    save_local_db()
                    
                    display_num = f"+{num_str}" if not str(num_str).startswith("+") else str(num_str)
                    masked = mask_number(display_num)
                    lang = detect_language(msg_text)
                    
                    lang_name = lang.replace('#', '')
                    display_msg = get_group_otp_msg(num_str, app_full_name, prem_app_html, iso, lang_name, otp)
                    
                    for fw in bot_settings.get("fw_groups", []):
                        kb = []
                        for btn in fw.get("buttons", []):
                            b_obj = {"text": btn["text"], "url": btn["url"], "style": "primary"}
                            if "icon_custom_emoji_id" in btn: b_obj["icon_custom_emoji_id"] = btn["icon_custom_emoji_id"]
                            kb.append([b_obj])
                        send_message(fw["chat_id"], display_msg, reply_markup={"inline_keyboard": kb})
                    
                    inbox_msg = get_custom_inbox_msg(num_str, msg_text, app_name, otp, owner_id)
                    inbox_kb = []
                    
                    reward = float(bot_settings.get("otp_reward", 0.0))
                    raw_num_clean = str(num).replace("+", "").strip() if 'num' in locals() else str(num_str).replace("+", "").strip()
                    if 'assigned_number_metadata' in globals() and raw_num_clean in assigned_number_metadata:
                        reward = float(assigned_number_metadata[raw_num_clean].get("reward", reward))
                    
                    if reward > 0:
                        credit_otp_reward(owner_id, reward)
                        inbox_kb.append([{"text": f"Added {reward} tk", "icon_custom_emoji_id": "5420396762189831222", "callback_data": "ignore", "style": "primary"}])
                    
                    send_message(owner_id, inbox_msg, reply_markup={"inline_keyboard": inbox_kb})
                    
                    if owner_id in user_cache:
                        user_cache[owner_id]["total_otps"] = user_cache[owner_id].get("total_otps", 0) + 1
                    if db:
                        try: db.collection('users').document(str(owner_id)).update({"total_otps": firestore.Increment(1), "daily_otps": firestore.Increment(1)}); _record_otp_day(owner_id)
                        except: pass
                break
        except: pass
        time.sleep(4)

def voltx_console_listener():
    global recent_traffic, bot_settings
    seen_console_hits = set()
    last_auto_update = time.time()
    
    while True:
        try:
            if not bot_settings.get("voltx_on", True):
                time.sleep(5)
                continue
            voltx_keys = bot_settings.get("voltx_keys", [])
            if voltx_keys:
                api_key = voltx_keys[0]
                headers = {"mauthapi": api_key}
                res = requests.get(f"{VOLTX_BASE_URL}/console", headers=headers, timeout=10)
                data = res.json()
                
                if data.get("meta", {}).get("code") == 200 and data.get("data", {}).get("hits"):
                    current_time = time.time()
                    new_hits = False
                    
                    for hit in data["data"]["hits"]:
                        range_str = str(hit.get("range", "")).replace("X", "")
                        if len(range_str) > 9:
                            range_str = range_str[:9] # 9 ডিজিটের বেশি হলে প্রথম 9 ডিজিট নেবে
                        sid = str(hit.get("sid", "Unknown"))
                        msg = str(hit.get("message", ""))
                        hit_time = hit.get("time", current_time * 1000) / 1000.0
                        
                        unique_hit = f"{range_str}_{sid}_{hit.get('time', 0)}"
                        if unique_hit not in seen_console_hits and range_str:
                            seen_console_hits.add(unique_hit)
                            if len(seen_console_hits) > 2000: seen_console_hits.clear()
                            
                            char, iso = get_flag_and_code(range_str)
                            app_full_name, _ = get_service_info_html(sid, msg)
                            
                            recent_traffic.append({
                                "service": app_full_name,
                                "iso": iso,
                                "flag": char,
                                "number": f"{range_str}XXX", 
                                "time": hit_time,
                                "real_range": range_str
                            })
                            new_hits = True
                            
                    if new_hits:
                        recent_traffic = [t for t in recent_traffic if current_time - t.get("time", 0) <= 3600]
                        save_local_db()
                        
                    # 🌟 Auto ADD and DELETE logic every 2 minutes (120 seconds)
                    if bot_settings.get("voltx_auto", False) and current_time - last_auto_update > 120:
                        last_auto_update = current_time
                        changed = False
                        
                        if "voltx_services" not in bot_settings: bot_settings["voltx_services"] = {}
                        if "voltx_search_countries" not in bot_settings: bot_settings["voltx_search_countries"] = []
                        
                        # --- ১. Auto ADD Logic ---
                        srv_counts = Counter(t["service"].upper() for t in recent_traffic if "real_range" in t)
                        top_srvs = [s for s, c in srv_counts.most_common(6)] # Top 6 Services
                        
                        for srv in top_srvs:
                            if srv not in bot_settings["voltx_services"]:
                                bot_settings["voltx_services"][srv] = {}
                                changed = True
                                
                            iso_counts = Counter(t["iso"] for t in recent_traffic if t.get("service", "").upper() == srv and "real_range" in t)
                            top_isos = [i for i, c in iso_counts.most_common(3)] # Top 3 Countries
                            
                            for iso in top_isos:
                                full_country_name = get_flag_info_html(iso, return_full_name=True).title()
                                if full_country_name not in bot_settings["voltx_services"][srv]:
                                    bot_settings["voltx_services"][srv][full_country_name] = []
                                    changed = True
                                    
                                rng_counts = Counter(t["real_range"] for t in recent_traffic if t.get("service", "").upper() == srv and t.get("iso") == iso and "real_range" in t)
                                top_rngs = [r for r, c in rng_counts.most_common(2)] # Top 2 Ranges
                                
                                for rng in top_rngs:
                                    if rng not in bot_settings["voltx_services"][srv][full_country_name]:
                                        bot_settings["voltx_services"][srv][full_country_name].append(rng)
                                        changed = True
                                        
                                    # 🌟 শুধুমাত্র ৩ ডিজিট কান্ট্রি কোড অ্যাড করবে
                                    country_code = rng[:3]
                                    if country_code not in bot_settings["voltx_search_countries"]:
                                        bot_settings["voltx_search_countries"].append(country_code)
                                        changed = True

                        # --- ২. Auto DELETE Logic (Only if NO traffic in the LAST 120 SECONDS) ---
                        services_to_remove = []
                        for srv, countries in list(bot_settings["voltx_services"].items()):
                            # চেক করবে গত ১২০ সেকেন্ডে কোনো ট্রাফিক আছে কি না
                            srv_hit = sum(1 for t in recent_traffic if t.get("service", "").upper() == srv.upper() and "real_range" in t and current_time - t.get("time", 0) <= 120)
                            if srv_hit < 1:
                                services_to_remove.append(srv)
                                continue
                            
                            countries_to_remove = []
                            for country, ranges in list(countries.items()):
                                c_hit = sum(1 for t in recent_traffic if t.get("service", "").upper() == srv.upper() and get_flag_info_html(t.get("iso"), return_full_name=True).title() == country and "real_range" in t and current_time - t.get("time", 0) <= 120)
                                if c_hit < 1:
                                    countries_to_remove.append(country)
                                    continue
                                
                                ranges_to_remove = []
                                for rng in ranges:
                                    r_hit = sum(1 for t in recent_traffic if t.get("service", "").upper() == srv.upper() and t.get("real_range") == rng and current_time - t.get("time", 0) <= 120)
                                    if r_hit < 1: 
                                        ranges_to_remove.append(rng)
                                
                                for r in ranges_to_remove:
                                    ranges.remove(r)
                                    changed = True
                                    
                            for c in countries_to_remove:
                                del bot_settings["voltx_services"][srv][c]
                                changed = True
                                
                        for s in services_to_remove:
                            del bot_settings["voltx_services"][s]
                            changed = True
                            
                        if changed:
                            save_db()
                            
        except Exception as e:
            pass
        time.sleep(10)

def voltx_sms_listener():
    global processed_otps, recent_traffic, voltx_assigned_numbers
    while True:
        try:
            voltx_keys = bot_settings.get("voltx_keys", [])
            for api_key in voltx_keys:
                try:
                    headers = {"mauthapi": api_key}
                    res = requests.get(f"{VOLTX_BASE_URL}/success-otp", headers=headers, timeout=10)
                    resp_data = res.json()
                    
                    if resp_data.get("meta", {}).get("code") == 200 and "data" in resp_data and "otps" in resp_data["data"]:
                        for item in resp_data["data"]["otps"]:
                            num = str(item.get("number", "")).replace("+", "")
                            msg_text = str(item.get("message", ""))
                            otp = extract_otp_code(msg_text) or "CODE"
                            otp_id = str(item.get("otp_id", otp))
                            
                            app_name = "Voltx Service"
                            detected_app = detect_service(msg_text)
                            if detected_app: app_name = detected_app
                                
                            unique_id = f"VOLTX_{num}_{otp_id}"
                            
                            if unique_id not in processed_otps and num:
                                processed_otps.add(unique_id)
                                if len(processed_otps) > 15000: [processed_otps.pop() for _ in range(5000)]
                                
                                char, iso = get_flag_and_code(num)
                                actual_srv = get_real_service(num, app_name)
                                app_full_name, prem_app_html = get_service_info_html(actual_srv, msg_text)
                                current_time = time.time()
                                
                                recent_traffic = [t for t in recent_traffic if current_time - t.get("time", 0) <= 3600]
                                recent_traffic.append({"service": app_full_name, "iso": iso, "flag": char, "number": num, "time": current_time})
                                save_local_db()
                                
                                display_num = f"+{num}" if not str(num).startswith("+") else str(num)
                                masked = mask_number(display_num)
                                lang = detect_language(msg_text)
                                
                                lang_name = LANG_MAP.get(lang, "English") if 'LANG_MAP' in globals() else lang.replace("#", "")
                                display_msg = get_group_otp_msg(num, app_full_name, prem_app_html, iso, lang_name, otp)
                                
                                for fw in bot_settings.get("fw_groups", []):
                                    kb = []
                                    temp_row = []
                                    for btn in fw.get("buttons", []):
                                        b_obj = {"text": btn["text"], "url": btn["url"], "style": "primary"}
                                        if "icon_custom_emoji_id" in btn: b_obj["icon_custom_emoji_id"] = btn["icon_custom_emoji_id"]
                                        temp_row.append(b_obj)
                                        if len(temp_row) == 2:
                                            kb.append(temp_row)
                                            temp_row = []
                                    if temp_row: kb.append(temp_row)
                                    send_message(fw["chat_id"], display_msg, reply_markup={"inline_keyboard": kb})
                                    
                                owner_id = get_real_owner(num)
                                        
                                if owner_id:
                                    inbox_msg = get_custom_inbox_msg(num, msg_text, app_name, otp, owner_id)
                                    inbox_kb = []
                                    
                                    reward = float(bot_settings.get("otp_reward", 0.0))
                                    raw_num_clean = str(num).replace("+", "").strip() if 'num' in locals() else str(num_str).replace("+", "").strip()
                                    if 'assigned_number_metadata' in globals() and raw_num_clean in assigned_number_metadata:
                                        reward = float(assigned_number_metadata[raw_num_clean].get("reward", reward))
                                    
                                    if reward > 0:
                                        credit_otp_reward(owner_id, reward)
                                        inbox_kb.append([{"text": f"Added {reward} tk", "icon_custom_emoji_id": "6169972515025199096", "callback_data": "ignore", "style": "primary"}])
                                    
                                    send_message(owner_id, inbox_msg, reply_markup={"inline_keyboard": inbox_kb})
                                    
                                    if owner_id in user_cache:
                                        user_cache[owner_id]["total_otps"] = user_cache[owner_id].get("total_otps", 0) + 1
                                    if db:
                                        try: db.collection('users').document(str(owner_id)).update({"total_otps": firestore.Increment(1), "daily_otps": firestore.Increment(1)}); _record_otp_day(owner_id)
                                        except: pass
                except: pass
        except: pass
        time.sleep(5)

def global_sms_listener():
    global processed_otps, recent_traffic, stex_assigned_numbers
    while True:
        try:
            stex_keys = bot_settings.get("stex_keys", [])
            for api_key in stex_keys:
                try:
                    headers = {"mauthapi": api_key}
                    res = requests.get(f"{STEX_BASE_URL}/success-otp", headers=headers, timeout=10)
                    resp_data = res.json()
                    
                    if resp_data.get("meta", {}).get("code") == 200 and "data" in resp_data and "otps" in resp_data["data"]:
                        for item in resp_data["data"]["otps"]:
                            num = str(item.get("number", "")).replace("+", "")
                            msg_text = str(item.get("message", ""))
                            otp = extract_otp_code(msg_text) or "CODE"
                            otp_id = str(item.get("otp_id", otp))
                            
                            app_name = "Stex Service"
                            detected_app = detect_service(msg_text)
                            if detected_app: app_name = detected_app
                                
                            unique_id = f"STEX_{num}_{otp_id}"
                            
                            if unique_id not in processed_otps and num:
                                processed_otps.add(unique_id)
                                if len(processed_otps) > 15000: [processed_otps.pop() for _ in range(5000)]
                                
                                char, iso = get_flag_and_code(num)
                                actual_srv = get_real_service(num, app_name)
                                app_full_name, prem_app_html = get_service_info_html(actual_srv, msg_text)
                                current_time = time.time()
                                
                                recent_traffic = [t for t in recent_traffic if current_time - t.get("time", 0) <= 3600]
                                recent_traffic.append({"service": app_full_name, "iso": iso, "flag": char, "number": num, "time": current_time})
                                save_local_db()
                                
                                display_num = f"+{num}" if not str(num).startswith("+") else str(num)
                                masked = mask_number(display_num)
                                lang = detect_language(msg_text)
                                
                                lang_name = LANG_MAP.get(lang, "English") if 'LANG_MAP' in globals() else lang.replace("#", "")
                                display_msg = get_group_otp_msg(num, app_full_name, prem_app_html, iso, lang_name, otp)
                                
                                for fw in bot_settings.get("fw_groups", []):
                                    kb = []
                                    temp_row = []
                                    for btn in fw.get("buttons", []):
                                        b_obj = {"text": btn["text"], "url": btn["url"], "style": "primary"}
                                        if "icon_custom_emoji_id" in btn: b_obj["icon_custom_emoji_id"] = btn["icon_custom_emoji_id"]
                                        temp_row.append(b_obj)
                                        if len(temp_row) == 2:
                                            kb.append(temp_row)
                                            temp_row = []
                                    if temp_row: kb.append(temp_row)
                                    send_message(fw["chat_id"], display_msg, reply_markup={"inline_keyboard": kb})
                                    
                                owner_id = get_real_owner(num)
                                        
                                if owner_id:
                                    inbox_msg = get_custom_inbox_msg(num, msg_text, app_name, otp, owner_id)
                                    inbox_kb = []
                                    
                                    reward = float(bot_settings.get("otp_reward", 0.0))
                                    raw_num_clean = str(num).replace("+", "").strip() if 'num' in locals() else str(num_str).replace("+", "").strip()
                                    if 'assigned_number_metadata' in globals() and raw_num_clean in assigned_number_metadata:
                                        reward = float(assigned_number_metadata[raw_num_clean].get("reward", reward))
                                    
                                    if reward > 0:
                                        credit_otp_reward(owner_id, reward)
                                        inbox_kb.append([{"text": f"Added {reward} tk", "icon_custom_emoji_id": "6169972515025199096", "callback_data": "ignore", "style": "primary"}])
                                    
                                    send_message(owner_id, inbox_msg, reply_markup={"inline_keyboard": inbox_kb})
                                    
                                    if owner_id in user_cache:
                                        user_cache[owner_id]["total_otps"] = user_cache[owner_id].get("total_otps", 0) + 1
                                    if db:
                                        try: db.collection('users').document(str(owner_id)).update({"total_otps": firestore.Increment(1), "daily_otps": firestore.Increment(1)}); _record_otp_day(owner_id)
                                        except: pass
                except: pass
        except: pass
        time.sleep(5)

def generic_sms_listener(panel_name, keys_attr, assigned_dict, url, header_key, get_list_fn, get_data_fn):
    global processed_otps, recent_traffic
    while True:
        try:
            for api_key in bot_settings.get(keys_attr, []):
                try:
                    res = requests.get(url, headers={header_key: api_key}, timeout=10)
                    items = get_list_fn(res.json())
                    for item in items:
                        num, msg_text, otp_id = get_data_fn(item)
                        otp = extract_otp_code(msg_text) or "CODE"
                        unique_id = f"{panel_name[:3].upper()}_{num}_{otp_id}"
                        
                        if unique_id not in processed_otps and num:
                            processed_otps.add(unique_id)
                            if len(processed_otps) > 15000: 
                                for _ in range(5000): processed_otps.pop()
                            
                            app_name = f"{panel_name} Service"
                            detected_app = detect_service(msg_text)
                            if detected_app: app_name = detected_app
                            
                            owner_id = assigned_dict.get(num) or get_real_owner(num)
                            if owner_id:
                                char, iso = get_flag_and_code(num)
                                app_full_name, prem_app_html = get_service_info_html(app_name, msg_text)
                                current_time = time.time()
                                recent_traffic = [t for t in recent_traffic if current_time - t.get("time", 0) <= 3600]
                                recent_traffic.append({"service": app_full_name, "iso": iso, "flag": char, "number": num, "time": current_time})
                                save_local_db()
                                
                                display_num = f"+{num}" if not str(num).startswith("+") else str(num)
                                masked = mask_number(display_num)
                                lang = detect_language(msg_text)
                                lang_name = LANG_MAP.get(lang, "English") if 'LANG_MAP' in globals() else lang.replace("#", "")
                                display_msg = get_group_otp_msg(num, app_full_name, prem_app_html, iso, lang_name, otp)
                                
                                for fw in bot_settings.get("fw_groups", []):
                                    kb = []
                                    for btn in fw.get("buttons", []):
                                        b_obj = {"text": btn["text"], "url": btn["url"], "style": "primary"}
                                        if "icon_custom_emoji_id" in btn: b_obj["icon_custom_emoji_id"] = btn["icon_custom_emoji_id"]
                                        kb.append([b_obj])
                                    send_message(fw["chat_id"], display_msg, reply_markup={"inline_keyboard": kb})
                                
                                inbox_msg = get_custom_inbox_msg(num, msg_text, app_name, otp, owner_id)
                                inbox_kb = []
                                
                                reward = float(bot_settings.get("otp_reward", 0.0))
                                raw_num_clean = str(num).replace("+", "").strip()
                                if 'assigned_number_metadata' in globals() and raw_num_clean in assigned_number_metadata:
                                    reward = float(assigned_number_metadata[raw_num_clean].get("reward", reward))
                                
                                if reward > 0:
                                    credit_otp_reward(owner_id, reward)
                                    inbox_kb.append([{"text": f"Added {reward} tk", "icon_custom_emoji_id": "6169972515025199096", "callback_data": "ignore", "style": "primary"}])
                                
                                send_message(owner_id, inbox_msg, reply_markup={"inline_keyboard": inbox_kb})
                                if owner_id in user_cache: user_cache[owner_id]["total_otps"] = user_cache[owner_id].get("total_otps", 0) + 1
                                if db:
                                    try: db.collection('users').document(str(owner_id)).update({"total_otps": firestore.Increment(1), "daily_otps": firestore.Increment(1)}); _record_otp_day(owner_id)
                                    except: pass
                except: pass
        except: pass
        time.sleep(5)

def zenex_sms_listener():
    def get_list(d): return d.get("data", {}).get("otps", []) if d.get("meta", {}).get("code") == 200 else []
    def get_data(i): return str(i.get("number", "")).replace("+", ""), str(i.get("otp", "")), str(i.get("nid", ""))
    generic_sms_listener("Zenex", "zenex_keys", zenex_assigned_numbers, f"{ZENEX_BASE_URL}/v1/numsuccess/info", "mapikey", get_list, get_data)

def zebra_sms_listener():
    def get_list(d): return d.get("data", {}).get("rows", []) if d.get("meta", {}).get("code") == 0 else []
    def get_data(i): return str(i.get("number", "")).replace("+", ""), str(i.get("message", "")), str(i.get("at_ms", ""))
    generic_sms_listener("Zebra", "zebra_keys", zebra_assigned_numbers, f"{ZEBRA_BASE_URL}/api/v1/publicapi/getupdate", "MAuth", get_list, get_data)

def scarface_sms_listener():
    def get_list(d): return d.get("data", {}).get("otps", []) if d.get("meta", {}).get("code") == 200 else []
    def get_data(i): return str(i.get("number", "")).replace("+", ""), str(i.get("message", "")), str(i.get("otp_id", ""))
    generic_sms_listener("ScarFace", "scarface_keys", scarface_assigned_numbers, f"{SCARFACE_BASE_URL}/success-otp", "X-API-Key", get_list, get_data)

def daily_leaderboard_reset_worker():
    while True:
        try:
            from datetime import timedelta, timezone
            now_utc = datetime.now(timezone.utc)
            now_bd = now_utc + timedelta(hours=6)
            next_midnight_bd = (now_bd + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            sleep_seconds = (next_midnight_bd - now_bd).total_seconds()
            
            time.sleep(sleep_seconds + 5) # ৫ সেকেন্ড পর রিসেট শুরু হবে
            
            if db:
                users = db.collection('users').stream()
                sorted_users = []
                for u in users:
                    d = u.to_dict()
                    if d.get("daily_otps", 0) > 0:
                        sorted_users.append((u.id, d.get("daily_otps", 0)))
                        
                sorted_users.sort(key=lambda x: x[1], reverse=True)
                
                res_txt = ""
                count = 1
                for uid, val in sorted_users[:10]:
                    res_txt += f"├ {count}️⃣ <a href='tg://user?id={uid}'>{uid}</a> ➔ <b>{val}</b>\n"
                    count += 1
                
                if res_txt:
                    msg = f"🏆 <b>YESTERDAY'S TOP 10 OTP RECEIVERS</b> 🏆\n━━━━━━━━━━━━━━━\n{res_txt}━━━━━━━━━━━━━━━"
                    send_message(OWNER_ID, render_body_text(msg))
                    
                batch = db.batch()
                b_count = 0
                for uid, val in sorted_users:
                    u_ref = db.collection('users').document(uid)
                    batch.update(u_ref, {"daily_otps": 0})
                    b_count += 1
                    if b_count == 400:
                        batch.commit()
                        batch = db.batch()
                        b_count = 0
                if b_count > 0:
                    batch.commit()
        except Exception as e:
            time.sleep(60)

def _safe_start(fn):
    for _ in range(5):
        try:
            threading.Thread(target=fn, daemon=True).start()
            return
        except RuntimeError:
            time.sleep(2)
    print(f"❌ Thread start failed: {fn.__name__}")

def main():
    global BOT_USERNAME
    res = api_call("getMe")
    if res.get("ok"): BOT_USERNAME = res["result"]["username"]
    print(f"🤖 Bot is starting... @{BOT_USERNAME}")
    try:
        ws_checker.init(send_message, edit_message, user_states, temp_data, user_active_sessions,
                        render=render_body_text, cancel_kb=get_cancel_kb)
        print("✅ WS Checker ready", flush=True)
    except BaseException as _e:   # WS Checker fail korleo bot jeno cholte thake
        print(f"⚠️ WS Checker init failed (bot cholbe): {_e}", flush=True)
    
    _safe_start(daily_leaderboard_reset_worker)
    _safe_start(panel_monitor_thread)
    _safe_start(global_sms_listener)
    _safe_start(voltx_sms_listener)
    _safe_start(voltx_console_listener)
    _safe_start(zenex_sms_listener)
    _safe_start(zebra_sms_listener)
    _safe_start(scarface_sms_listener)
    print("📡 Background APIs & Global SMS Listener Started!")
    
    executor = ThreadPoolExecutor(max_workers=40)

    import traceback
    faulthandler.cancel_dump_traceback_later()
    _inflight = {}
    def _run(fn, arg):
        key = threading.get_ident()
        _inflight[key] = (time.time(), fn.__name__)
        try:
            fn(arg)
        except Exception:
            print(f"❌ {fn.__name__} error:\n{traceback.format_exc()}", flush=True)
        finally:
            _inflight.pop(key, None)

    def _watchdog():
        while True:
            time.sleep(10)
            stuck = [(n, int(time.time() - t)) for t, n in list(_inflight.values()) if time.time() - t > 15]
            if stuck:
                print(f"⚠️ STUCK handlers: {stuck} — thread stacks:", flush=True)
                faulthandler.dump_traceback(file=sys.stdout, all_threads=True)
    _safe_start(_watchdog)

    # webhook থাকলে getUpdates কাজ করে না
    try: api_call("deleteWebhook", {"drop_pending_updates": False})
    except Exception: pass

    try:
        _wh = api_call("getWebhookInfo").get("result", {})
        print(f"ℹ️ Webhook: url='{_wh.get('url','')}' pending={_wh.get('pending_update_count')} last_error={_wh.get('last_error_message')}", flush=True)
    except Exception as _e:
        print(f"ℹ️ getWebhookInfo failed: {_e}", flush=True)
    print("🟢 Polling started...", flush=True)
    offset = None
    while True:
        try:
            url = f"{BASE_URL}/getUpdates"
            params = {"timeout": 10}
            if offset is not None: params["offset"] = offset
            r = tg_session.get(url, params=params, timeout=25)
            updates = r.json()
            if not updates.get("ok"):
                print(f"❌ getUpdates error: {updates}", flush=True)
                time.sleep(3)
                continue
            globals()["_poll_ok"] = globals().get("_poll_ok", 0) + 1
            if globals()["_poll_ok"] in (1, 6) or globals()["_poll_ok"] % 60 == 0:
                print(f"💓 polling OK (getUpdates #{globals()['_poll_ok']}, new={len(updates.get('result', []))})", flush=True)
            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                fn = arg = None
                print(f"📩 update {update['update_id']} keys={[k for k in update if k!='update_id']}", flush=True)
                if "message" in update:
                    fn, arg = handle_message, update["message"]
                elif "callback_query" in update:
                    fn, arg = handle_callback, update["callback_query"]
                if fn is None:
                    continue
                try:
                    executor.submit(_run, fn, arg)
                except RuntimeError as e:
                    print(f"⚠️ submit failed ({e}), running inline", flush=True)
                    _run(fn, arg)
        except requests.exceptions.Timeout:
            _to = globals().get("_poll_timeouts", 0) + 1
            globals()["_poll_timeouts"] = _to
            if _to in (1, 3) or _to % 20 == 0:
                print(f"⚠️ getUpdates timeout x{_to} (network/proxy long-poll problem?)", flush=True)
            continue
        except Exception as e:
            print(f"❌ Polling loop error: {e}", flush=True)
            time.sleep(2)

if __name__ == "__main__":
    main()    
