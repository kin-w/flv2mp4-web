#!/usr/bin/env python3
"""
视频 -> MP4 批量转封装 Web 服务（支持自定义输入格式）
纯标准库实现（无第三方依赖），调用系统 ffmpeg。
"""

import hmac
import json
import os
import queue
import re
import secrets
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")

CONFIG_DIR = os.environ.get("V2M_CONFIG", "/config")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
PORT = int(os.environ.get("V2M_PORT", "8080"))

FFMPEG = os.environ.get("V2M_FFMPEG") or shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
FFPROBE = os.environ.get("V2M_FFPROBE") or shutil.which("ffprobe") or "/usr/bin/ffprobe"

# 允许访问的根目录（容器内路径），多个用系统分隔符分隔
# 未显式设置时：宿主机根目录已挂到 /host 就用 /host，否则退回 /data
_ROOTS_ENV = os.environ.get("V2M_ROOTS", "").strip()
if not _ROOTS_ENV:
    _ROOTS_ENV = "/host" if os.path.isdir("/host") else "/data"
ROOTS = [
    os.path.realpath(p.strip())
    for p in _ROOTS_ENV.split(os.pathsep)
    if p.strip()
]
DEFAULT_INPUT = os.path.realpath(
    os.environ.get("V2M_DEFAULT_INPUT", ROOTS[0] if ROOTS else "/data")
)

TMP_SUFFIX = ".v2mtmp"
DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")

# ---- 登录鉴权 ----
PASSWORD = os.environ.get("V2M_PASSWORD", "")
SESSION_FILE = os.path.join(CONFIG_DIR, "sessions.json")
COOKIE_NAME = "v2m_session"
REMEMBER_SECONDS = 30 * 86400
SESSION_SECONDS = 12 * 3600
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW = 600

_sessions = {}
_sessions_lock = threading.Lock()
_login_state = {}
_login_lock = threading.Lock()


def load_sessions():
    try:
        with open(SESSION_FILE, encoding="utf-8") as f:
            data = json.load(f)
        now = time.time()
        return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("exp", 0) > now}
    except (OSError, ValueError):
        return {}


def save_sessions():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = SESSION_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_sessions, f)
        os.replace(tmp, SESSION_FILE)
    except OSError:
        pass

OUTPUT_EXT = ".mp4"
DEFAULT_EXTS = ["flv", "ts", "mkv", "avi", "mov", "wmv", "f4v", "webm", "mpg", "mpeg", "m4v", "3gp"]
DEFAULT_CMD = (
    "ffmpeg -hide_banner -nostdin -y -i {input} "
    "-map 0:v:0 -map 0:a? -c copy -f mp4 {output}"
)

_settings_lock = threading.Lock()


# --------------------------------------------------------------------------
# 设置
# --------------------------------------------------------------------------
def default_settings():
    return {
        "input_path": DEFAULT_INPUT,
        "output_path": "",
        "delete_source": True,
        "move_to_output": True,
        "recursive": False,
        "extensions": ",".join(DEFAULT_EXTS),
        "command": DEFAULT_CMD,
    }


def parse_exts(raw):
    """把 'flv, .ts;mkv' 解析为 ['.flv', '.ts', '.mkv']，非法项忽略。"""
    result = []
    for part in re.split(r"[,;\s]+", str(raw or "")):
        e = part.strip().lower().lstrip("*").lstrip(".")
        if not e or len(e) > 10 or not re.fullmatch(r"[a-z0-9]+", e):
            continue
        ext = "." + e
        if ext == OUTPUT_EXT or ext in result:
            continue
        result.append(ext)
    return result


def ext_string(exts):
    return ",".join(e.lstrip(".") for e in exts)


def active_exts(settings):
    return parse_exts(settings.get("extensions")) or ["." + e for e in DEFAULT_EXTS]


def build_command(template, src, dst):
    """按模板生成参数列表：{input}/{output} 替换为实际路径，不经 shell。"""
    try:
        tokens = shlex.split(template)
    except ValueError as exc:
        raise RuntimeError(f"命令解析失败: {exc}")
    if not tokens:
        raise RuntimeError("命令为空")
    tokens = [t.replace("{input}", src).replace("{output}", dst) for t in tokens]
    if tokens[0] in ("ffmpeg", "{ffmpeg}"):
        tokens[0] = FFMPEG
    # 模板里没有写进度输出时自动补上，保证界面能显示进度
    if "-progress" not in tokens:
        idx = len(tokens)
        for i in range(len(tokens) - 1, -1, -1):
            if tokens[i] == dst:
                idx = i
                break
        tokens[idx:idx] = ["-progress", "pipe:1", "-nostats"]
    return tokens


def within_roots(path):
    if not path:
        return False
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    for root in ROOTS:
        if real == root or real.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


def load_settings():
    with _settings_lock:
        data = default_settings()
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for k in data:
                    if k in saved and isinstance(saved[k], type(data[k])):
                        data[k] = saved[k]
        except (OSError, ValueError):
            pass
        # 挂载变化后，旧配置里的路径可能已不在允许范围内，自动回到默认目录
        if not within_roots(data["input_path"]):
            data["input_path"] = DEFAULT_INPUT
        if data["output_path"] and not within_roots(data["output_path"]):
            data["output_path"] = ""
        return data


def save_settings(new):
    data = load_settings()
    for k in data:
        if k in new and isinstance(new[k], type(data[k])):
            data[k] = new[k]
    with _settings_lock:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    return data


# --------------------------------------------------------------------------
# 路径工具
# --------------------------------------------------------------------------
def within_roots(path):
    rp = os.path.realpath(path)
    for r in ROOTS:
        rr = os.path.realpath(r)
        if rp == rr:
            return True
        rr_sep = rr if rr.endswith(os.sep) else rr + os.sep
        if rp.startswith(rr_sep):
            return True
    return False


def safe_path(raw):
    """规范化用户传入路径，并校验是否在允许的根目录内。"""
    if not raw:
        return None
    p = os.path.realpath(os.path.expanduser(raw.strip()))
    return p if within_roots(p) else None


def list_videos(path, recursive, exts):
    items = []
    exts = set(exts)

    def matched(name):
        if TMP_SUFFIX in name:
            return False
        return os.path.splitext(name)[1].lower() in exts

    if os.path.isfile(path):
        if matched(os.path.basename(path)):
            items.append(path)
        return items
    if not os.path.isdir(path):
        return items

    if recursive:
        for cur, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in files:
                if matched(name):
                    items.append(os.path.join(cur, name))
    else:
        try:
            for name in sorted(os.listdir(path)):
                full = os.path.join(path, name)
                if os.path.isfile(full) and matched(name):
                    items.append(full)
        except OSError:
            pass
    return items


def resolve_output(src, settings):
    """返回 (输出目录, 最终 mp4 完整路径)。

    指定输出目录时保留输入目录的结构：
    输入 /home/bililive-go/兔娘、输出 /home/录播归档
    -> /home/录播归档/兔娘/xxx.mp4（含子目录）。
    """
    base_dir = os.path.dirname(src)
    root = settings["output_path"]
    if not root:
        out_dir = base_dir
    else:
        inp = os.path.realpath(settings["input_path"]) if settings["input_path"] else ""
        out_dir = root
        if inp and os.path.isdir(inp) and inp != os.path.realpath(root):
            rel = os.path.relpath(os.path.realpath(src), inp)
            if rel.startswith(".."):
                out_dir = os.path.join(root, os.path.basename(base_dir))
            else:
                sub = os.path.dirname(rel)
                if inp in ROOTS:
                    out_dir = os.path.join(root, sub) if sub else root
                else:
                    name = os.path.basename(inp) or "root"
                    out_dir = os.path.join(root, name, sub) if sub else os.path.join(root, name)
    stem = os.path.splitext(os.path.basename(src))[0]
    return out_dir, os.path.join(out_dir, stem + OUTPUT_EXT)


def probe_duration(path):
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        return float(out) if out else 0.0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


# --------------------------------------------------------------------------
# 任务队列
# --------------------------------------------------------------------------
class LogBuffer:
    """带绝对序号的环形日志，供前端增量拉取。"""

    def __init__(self, limit=800):
        self.lines = []
        self.base = 0
        self.limit = limit
        self.lock = threading.Lock()

    def append(self, line):
        with self.lock:
            self.lines.append(line)
            if len(self.lines) > self.limit:
                drop = len(self.lines) - self.limit
                del self.lines[:drop]
                self.base += drop

    def since(self, idx):
        with self.lock:
            total = self.base + len(self.lines)
            if idx < self.base:
                idx = self.base
            return self.lines[idx - self.base:], total


class JobManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = []
        self.logs = {}
        self.q = queue.Queue()
        self.proc = None
        self.current_id = None
        threading.Thread(target=self._worker, daemon=True).start()

    # ---- 对外 ----
    def enqueue(self, srcs):
        settings = load_settings()
        created = []
        with self.lock:
            existing = {j["src"] for j in self.jobs if j["state"] in ("queued", "running", "moving")}
            for src in srcs:
                if src in existing:
                    continue
                if not os.path.isfile(src):
                    continue
                out_dir, out = resolve_output(src, settings)
                job = {
                    "id": uuid.uuid4().hex[:10],
                    "src": src,
                    "out": out,
                    "name": os.path.basename(src),
                    "size": _size_of(src),
                    "state": "queued",
                    "pct": 0.0,
                    "speed": "",
                    "fps": "",
                    "written": 0,
                    "duration": 0,
                    "error": "",
                    "last_log": "",
                    "delete_source": bool(settings["delete_source"]),
                    "move_to_output": bool(settings["move_to_output"]),
                    "command": settings.get("command") or DEFAULT_CMD,
                    "created": time.time(),
                    "ended": 0,
                }
                self.jobs.append(job)
                existing.add(src)
                self.logs[job["id"]] = LogBuffer()
                created.append(job)
                self.q.put(job["id"])
        return created

    def cancel(self, job_id):
        with self.lock:
            if job_id == self.current_id and self.proc is not None:
                for j in self.jobs:
                    if j["id"] == job_id:
                        j["canceled"] = True
                try:
                    self.proc.terminate()
                except OSError:
                    pass
                return True
            for j in self.jobs:
                if j["id"] == job_id and j["state"] == "queued":
                    j["state"] = "canceled"
                    j["ended"] = time.time()
                    return True
        return False

    def clear_finished(self):
        with self.lock:
            keep = [j for j in self.jobs if j["state"] in ("queued", "running", "moving")]
            keep_ids = {j["id"] for j in keep}
            self.jobs = keep
            self.logs = {k: v for k, v in self.logs.items() if k in keep_ids}

    def get_log(self, job_id, since):
        buf = self.logs.get(job_id)
        if buf is None:
            return None
        lines, total = buf.since(max(0, since))
        return {"lines": lines, "next": total}

    def snapshot(self):
        with self.lock:
            return [dict(j) for j in self.jobs]

    # ---- 内部 ----
    def _worker(self):
        while True:
            job_id = self.q.get()
            with self.lock:
                job = next((j for j in self.jobs if j["id"] == job_id), None)
                if job is None or job["state"] == "canceled":
                    continue
                job["state"] = "running"
                self.current_id = job_id
            try:
                self._process(job)
            except Exception as exc:  # 兜底，避免线程退出
                with self.lock:
                    job["state"] = "failed"
                    job["error"] = str(exc)[:500]
                    job["ended"] = time.time()
            finally:
                with self.lock:
                    self.current_id = None
                    self.proc = None
                self.q.task_done()

    def _process(self, job):
        src = job["src"]
        out = job["out"]
        base_dir = os.path.dirname(src)
        out_dir = os.path.dirname(out)
        move = job["move_to_output"]
        logbuf = self.logs.get(job["id"])

        stem = os.path.splitext(os.path.basename(out))[0]
        tmp_name = "." + stem + TMP_SUFFIX + OUTPUT_EXT
        final_local = os.path.join(base_dir, os.path.basename(out))
        if move:
            tmp = os.path.join(base_dir, tmp_name)
        else:
            tmp = os.path.join(out_dir, tmp_name)

        # 目标已存在 -> 跳过，绝不覆盖
        if os.path.exists(out) and os.path.getsize(out) > 0:
            if logbuf is not None:
                logbuf.append(f"目标文件已存在，跳过：{out}")
            with self.lock:
                job["state"] = "skipped"
                job["pct"] = 100.0
                job["last_log"] = "目标文件已存在，跳过"
                job["ended"] = time.time()
            return

        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            if logbuf is not None:
                logbuf.append(f"无法创建输出目录：{exc}")
            with self.lock:
                job["state"] = "failed"
                job["error"] = f"无法创建输出目录: {exc}"
                job["ended"] = time.time()
            return

        duration = probe_duration(src)
        with self.lock:
            job["duration"] = duration

        try:
            cmd = build_command(job.get("command") or DEFAULT_CMD, src, tmp)
        except RuntimeError as exc:
            if logbuf is not None:
                logbuf.append(f"命令解析失败：{exc}")
            with self.lock:
                job["state"] = "failed"
                job["error"] = str(exc)
                job["ended"] = time.time()
            return

        if logbuf is not None:
            logbuf.append(f"源文件：{src}")
            logbuf.append(f"时长：{duration:.1f}s" if duration else "时长：未知")
            logbuf.append("$ " + " ".join(shlex.quote(t) for t in cmd))

        stderr_tail = deque(maxlen=15)
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
        except OSError as exc:
            with self.lock:
                job["state"] = "failed"
                job["error"] = f"无法启动 ffmpeg: {exc}"
                job["ended"] = time.time()
            return

        with self.lock:
            self.proc = proc

        def drain():
            for line in proc.stderr:
                line = line.rstrip()
                if not line.strip():
                    continue
                stderr_tail.append(line.strip())
                if logbuf is not None:
                    logbuf.append(line)
                with self.lock:
                    job["last_log"] = line.strip()[:200]
                if not job.get("duration"):
                    m = DURATION_RE.search(line)
                    if m:
                        try:
                            dur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                                   + float(m.group(3)))
                            with self.lock:
                                job["duration"] = dur
                        except ValueError:
                            pass

        t = threading.Thread(target=drain, daemon=True)
        t.start()

        for line in proc.stdout:
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key == "out_time_us":
                try:
                    sec = int(val) / 1_000_000
                except ValueError:
                    continue
                pct = min(99.9, sec / duration * 100) if duration > 0 else 0.0
                with self.lock:
                    job["pct"] = round(pct, 1)
            elif key == "speed":
                with self.lock:
                    job["speed"] = val
            elif key == "fps":
                with self.lock:
                    job["fps"] = val
            elif key == "total_size":
                try:
                    with self.lock:
                        job["written"] = int(val)
                except ValueError:
                    pass

        rc = proc.wait()
        t.join(timeout=2)

        if job.get("canceled"):
            _remove_quietly(tmp)
            if logbuf is not None:
                logbuf.append("任务已取消，临时文件已清理")
            with self.lock:
                job["state"] = "canceled"
                job["last_log"] = "任务已取消"
                job["ended"] = time.time()
            return

        if rc != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            _remove_quietly(tmp)
            msg = "；".join(list(stderr_tail)[-4:]) or f"ffmpeg 退出码 {rc}"
            if logbuf is not None:
                logbuf.append(f"转换失败（退出码 {rc}），临时文件已清理")
            with self.lock:
                job["state"] = "failed"
                job["error"] = msg[:500]
                job["last_log"] = "转换失败"
                job["ended"] = time.time()
            return

        # 转换完成 -> 落盘
        try:
            if move:
                if os.path.abspath(tmp) != os.path.abspath(final_local):
                    os.replace(tmp, final_local)
                if os.path.abspath(final_local) != os.path.abspath(out):
                    with self.lock:
                        job["state"] = "moving"
                        job["pct"] = 100.0
                    shutil.move(final_local, out)
            else:
                os.replace(tmp, out)
        except OSError as exc:
            with self.lock:
                job["state"] = "failed"
                job["error"] = f"写入输出文件失败: {exc}"
                job["ended"] = time.time()
            return

        # 删除源文件（仅成功且用户选择删除）
        if job["delete_source"]:
            try:
                os.remove(src)
                if logbuf is not None:
                    logbuf.append(f"已删除源文件：{src}")
            except OSError:
                pass

        if logbuf is not None:
            logbuf.append(f"转换完成：{out}")
        with self.lock:
            job["state"] = "done"
            job["pct"] = 100.0
            job["last_log"] = f"转换完成：{out}"
            job["ended"] = time.time()


def _size_of(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _remove_quietly(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


manager = JobManager()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "flv2mp4-web/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 基础输出 ----
    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200, headers=None):
        self._send(code, data, headers=headers)

    def _redirect(self, location, headers=None):
        h = {"Location": location}
        h.update(headers or {})
        self._send(302, b"", "text/plain; charset=utf-8", h)

    # ---- 鉴权 ----
    def _cookies(self):
        out = {}
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _session_sig(self, token):
        return hmac.new(PASSWORD.encode("utf-8"), token.encode("utf-8"), "sha256").hexdigest()

    def _authed(self):
        if not PASSWORD:
            return True
        token = self._cookies().get(COOKIE_NAME)
        if not token:
            return False
        with _sessions_lock:
            sess = _sessions.get(token)
            if not sess:
                return False
            if not hmac.compare_digest(sess.get("sig", ""), self._session_sig(token)):
                _sessions.pop(token, None)
                save_sessions()
                return False
            if sess.get("exp", 0) <= time.time():
                _sessions.pop(token, None)
                save_sessions()
                return False
        return True

    def _require_auth(self, path):
        """返回 True 表示已放行，False 表示已经输出响应。"""
        if not PASSWORD or self._authed():
            return True
        if path.startswith("/api/"):
            self._json({"error": "未登录", "auth_required": True}, 401)
        else:
            self._redirect("/login")
        return False

    def _login_allowed(self, ip):
        now = time.time()
        with _login_lock:
            arr = [t for t in _login_state.get(ip, []) if now - t < LOGIN_WINDOW]
            _login_state[ip] = arr
            return len(arr) < LOGIN_MAX_FAILS

    def _login_fail(self, ip):
        with _login_lock:
            _login_state.setdefault(ip, []).append(time.time())

    def _login(self, body):
        if not PASSWORD:
            return self._json({"ok": True, "required": False})
        ip = self.client_address[0]
        if not self._login_allowed(ip):
            return self._json({"error": "尝试次数过多，请 10 分钟后再试"}, 429)
        pwd = str(body.get("password") or "")
        if not hmac.compare_digest(pwd.encode("utf-8"), PASSWORD.encode("utf-8")):
            self._login_fail(ip)
            time.sleep(0.3)
            return self._json({"error": "密码错误"}, 401)
        remember = bool(body.get("remember"))
        ttl = REMEMBER_SECONDS if remember else SESSION_SECONDS
        token = secrets.token_urlsafe(32)
        with _sessions_lock:
            _sessions[token] = {"exp": time.time() + ttl, "sig": self._session_sig(token)}
        save_sessions()
        with _login_lock:
            _login_state.pop(ip, None)
        cookie = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax"
        if remember:
            cookie += f"; Max-Age={REMEMBER_SECONDS}"
        return self._json({"ok": True}, headers={"Set-Cookie": cookie})

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def log_message(self, fmt, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass

    # ---- 路由 ----
    def do_GET(self):
        if os.environ.get("V2M_DEBUG"):
            print("REQ GET", self.path, flush=True)
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            if not self._require_auth(path):
                return
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/login":
            if self._authed() and PASSWORD:
                return self._redirect("/")
            return self._static("login.html")
        if path == "/api/auth":
            return self._json({"required": bool(PASSWORD), "authed": self._authed()})
        if not self._require_auth(path):
            return
        if path == "/api/settings":
            return self._json(self._decorate(load_settings()))
        if path == "/api/browse":
            return self._browse(qs.get("path", [""])[0])
        if path == "/api/jobs":
            return self._json({"jobs": manager.snapshot()})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._body()

        if path == "/api/login":
            return self._login(body)
        if path == "/api/logout":
            token = self._cookies().get(COOKIE_NAME)
            if token:
                with _sessions_lock:
                    _sessions.pop(token, None)
                save_sessions()
            return self._json(
                {"ok": True},
                headers={"Set-Cookie": f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"},
            )
        if not self._require_auth(path):
            return

        if path == "/api/settings":
            return self._save_settings(body)
        if path == "/api/settings/reset":
            save_settings(default_settings())
            return self._json(self._decorate(default_settings()))
        if path == "/api/scan":
            return self._scan(body)
        if path == "/api/jobs":
            srcs = body.get("files") or []
            if not isinstance(srcs, list):
                return self._json({"error": "files 必须是数组"}, 400)
            clean = []
            for s in srcs:
                p = safe_path(s)
                if p and os.path.isfile(p):
                    clean.append(p)
            created = manager.enqueue(clean)
            return self._json({"added": len(created), "jobs": created})
        if path == "/api/jobs/cancel":
            ok = manager.cancel(str(body.get("id", "")))
            return self._json({"ok": ok})
        if path == "/api/jobs/clear":
            manager.clear_finished()
            return self._json({"ok": True})
        if path == "/api/joblog":
            try:
                since = int(body.get("since") or 0)
            except (TypeError, ValueError):
                since = 0
            data = manager.get_log(str(body.get("id", "")), since)
            if data is None:
                return self._json({"error": "任务不存在或日志已清理"}, 404)
            return self._json(data)
        return self._json({"error": "not found"}, 404)

    # ---- 具体处理 ----
    def _static(self, name):
        name = name.replace("\\", "/").lstrip("/")
        if ".." in name.split("/"):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        full = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(full):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            data = f.read()
        self._send(200, data, CONTENT_TYPES.get(ext, "application/octet-stream"))

    def _browse(self, raw):
        target = safe_path(raw) or (ROOTS[0] if ROOTS else "/")
        if not os.path.isdir(target):
            target = os.path.dirname(target)
        if not os.path.isdir(target):
            target = ROOTS[0] if ROOTS else "/"

        dirs, files = [], []
        exts = set(active_exts(load_settings()))
        try:
            entries = sorted(os.listdir(target))
        except OSError:
            entries = []
        for name in entries:
            if name.startswith("."):
                continue
            full = os.path.join(target, name)
            try:
                if os.path.isdir(full):
                    if within_roots(full):
                        dirs.append({"name": name, "path": full})
                elif os.path.splitext(name)[1].lower() in exts and TMP_SUFFIX not in name:
                    files.append({"name": name, "path": full, "size": _size_of(full)})
            except OSError:
                continue

        parent = os.path.dirname(target)
        if parent == target or not within_roots(parent) or not within_roots(target):
            parent = ""
        return self._json({
            "path": target,
            "parent": parent,
            "roots": ROOTS,
            "dirs": dirs,
            "files": files,
        })

    def _save_settings(self, body):
        s = load_settings()

        inp = str(body.get("input_path", s["input_path"])).strip()
        out = str(body.get("output_path", s["output_path"])).strip()

        if inp:
            p = safe_path(inp)
            if not p or not os.path.exists(p):
                return self._json({"error": f"输入路径无效或不在允许范围内: {inp}"}, 400)
            inp = p
        else:
            inp = DEFAULT_INPUT

        if out:
            p = safe_path(out)
            if not p:
                return self._json({
                    "error": f"输出路径不在允许范围内: {out}。"
                             f"容器只能访问已挂载的目录，路径必须位于 V2M_ROOTS 内（当前: {', '.join(ROOTS)}）"
                }, 400)
            out = p

        s["input_path"] = inp
        s["output_path"] = out
        s["delete_source"] = bool(body.get("delete_source", s["delete_source"]))
        s["move_to_output"] = bool(body.get("move_to_output", s["move_to_output"]))
        s["recursive"] = bool(body.get("recursive", s["recursive"]))
        if "extensions" in body:
            parsed = parse_exts(body.get("extensions"))
            s["extensions"] = ext_string(parsed) if parsed else ",".join(DEFAULT_EXTS)
        if "command" in body:
            cmd = str(body.get("command") or "").strip()
            if not cmd:
                return self._json({"error": "转换命令不能为空"}, 400)
            if "{input}" not in cmd or "{output}" not in cmd:
                return self._json({"error": "转换命令必须包含 {input} 和 {output} 占位符"}, 400)
            if len(cmd) > 2000:
                return self._json({"error": "转换命令过长（最多 2000 字符）"}, 400)
            s["command"] = cmd
        s = save_settings(s)
        return self._json(self._decorate(s))

    def _decorate(self, s):
        s["roots"] = ROOTS
        s["default_input"] = DEFAULT_INPUT
        s["default_extensions"] = ",".join(DEFAULT_EXTS)
        s["default_command"] = DEFAULT_CMD
        return s

    def _scan(self, body):
        settings = load_settings()
        raw = body.get("path") or settings["input_path"]
        recursive = bool(body.get("recursive", settings["recursive"]))
        target = safe_path(raw)
        if not target or not os.path.exists(target):
            return self._json({"error": f"路径不存在: {raw}"}, 400)

        files = list_videos(target, recursive, active_exts(settings))
        result = []
        with manager.lock:
            active = {j["src"] for j in manager.jobs
                      if j["state"] in ("queued", "running", "moving")}
        for f in files:
            out_dir, out = resolve_output(f, settings)
            result.append({
                "path": f,
                "name": os.path.basename(f),
                "rel": os.path.relpath(f, target if os.path.isdir(target) else os.path.dirname(target)),
                "size": _size_of(f),
                "mtime": os.path.getmtime(f),
                "out_dir": out_dir,
                "exists": os.path.exists(out) and os.path.getsize(out) > 0,
                "active": f in active,
            })
        return self._json({
            "is_file": os.path.isfile(target),
            "files": result,
            "out_dir": settings["output_path"] or (
                os.path.dirname(target) if os.path.isfile(target) else target
            ),
        })


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    _sessions.update(load_sessions())
    print(f"视频转 MP4 Web 启动: http://0.0.0.0:{PORT}")
    print(f"允许访问的根目录: {ROOTS}")
    print(f"默认输入目录: {DEFAULT_INPUT}")
    print("登录鉴权: " + ("已开启" if PASSWORD else "未开启（设置 V2M_PASSWORD 可开启）"))
    print(f"默认识别格式: {','.join(DEFAULT_EXTS)}")
    Server(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
