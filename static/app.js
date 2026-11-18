const $ = (id) => document.getElementById(id);

const state = {
  settings: null,
  files: [],
  jobs: [],
  browseTarget: null,
  browsePath: "",
  lastJobStates: {},
  jobsSig: "",
};

/* ---------------- 基础 ---------------- */
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (res.status === 401 && data.auth_required) {
    location.replace("/login");
    throw new Error("未登录");
  }
  if (!res.ok) throw new Error(data.error || `请求失败 (${res.status})`);
  return data;
}

const post = (path, body) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

function fmtSize(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}

function fmtTime(sec) {
  if (!sec || !isFinite(sec)) return "";
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
}

const STATE_TEXT = {
  queued: "排队中",
  running: "转换中",
  moving: "移动中",
  done: "已完成",
  failed: "失败",
  skipped: "已跳过",
  canceled: "已取消",
};

/* ---------------- 设置 ---------------- */
async function loadSettings() {
  const s = await api("/api/settings");
  state.settings = s;
  fillForm(s);
  return s;
}

function fillForm(s) {
  $("inputPath").value = s.input_path || "";
  $("outputPath").value = s.output_path || "";
  $("extensions").value = s.extensions || "";
  $("command").value = s.command || "";
  $("deleteSource").checked = !!s.delete_source;
  $("moveToOutput").checked = !!s.move_to_output;
  $("recursive").checked = !!s.recursive;
  updateRisk();
}

function updateRisk() {
  const out = $("outputPath").value.trim();
  const move = $("moveToOutput").checked;
  $("risk").classList.toggle("hidden", !(out && !move));
}

async function saveSettings(silent = false) {
  const payload = {
    input_path: $("inputPath").value.trim(),
    output_path: $("outputPath").value.trim(),
    extensions: $("extensions").value.trim(),
    command: $("command").value.trim(),
    delete_source: $("deleteSource").checked,
    move_to_output: $("moveToOutput").checked,
    recursive: $("recursive").checked,
  };

  if (!$("risk").classList.contains("hidden") && !silent) {
    const ok = await confirmDialog(
      "确认关闭“成功后移动”？",
      "输出目录已设置，但转换结果将直接写入输出目录。若转换过程中断，输出目录会残留不完整文件；跨磁盘时也不保证原子写入。建议保持“转换成功后移动到输出目录”开启。",
      "仍然保存"
    );
    if (!ok) return;
  }

  try {
    const s = await post("/api/settings", payload);
    state.settings = s;
    fillForm(s);
    const hint = $("saveHint");
    hint.classList.add("show");
    setTimeout(() => hint.classList.remove("show"), 1600);
    setStatus("就绪", "");
    if (!silent) toast("设置已保存");
    scan(true);
  } catch (e) {
    toast(e.message, true);
  }
}

async function resetSettings() {
  const ok = await confirmDialog(
    "恢复默认设置？",
    "将重置为：输入目录为默认目录、输出目录留空（与输入相同）、识别格式与转换命令恢复默认、转换成功后删除源文件、转换后移动到输出目录。",
    "恢复默认"
  );
  if (!ok) return;
  try {
    const s = await post("/api/settings/reset", {});
    state.settings = s;
    fillForm(s);
    toast("已恢复默认设置");
    scan(true);
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------------- 扫描与文件列表 ---------------- */
async function scan(silent = false) {
  const path = $("inputPath").value.trim();
  if (!path) { toast("请先设置输入目录", true); return; }
  if (!silent) setStatus("扫描中…", "busy");
  try {
    const data = await post("/api/scan", { path, recursive: $("recursive").checked });
    state.files = data.files;
    renderFiles();
    if (!silent) {
      setStatus(`共 ${data.files.length} 个文件`, "");
    }
  } catch (e) {
    state.files = [];
    renderFiles();
    setStatus("扫描失败", "err");
    toast(e.message, true);
  }
}

function renderFiles() {
  const list = $("fileList");
  const files = state.files;
  $("fileCount").textContent = files.length ? `${files.length} 个视频文件` : "没有找到匹配的视频文件";

  if (!files.length) {
    list.innerHTML = `<div class="empty">当前路径下没有待转换的视频文件</div>`;
    updateSelection();
    return;
  }

  list.innerHTML = files.map((f, i) => {
    const disabled = f.exists || f.active;
    const chip = f.active
      ? `<span class="chip run">转换中</span>`
      : f.exists
        ? `<span class="chip ok">已存在 mp4</span>`
        : `<span class="chip warn">待转换</span>`;
    return `
      <div class="row">
        <label class="check">
          <input type="checkbox" data-idx="${i}" ${disabled ? "disabled" : ""}>
        </label>
        <div class="meta">
          <div class="name" title="${esc(f.rel)}">${esc(f.rel)}</div>
          <div class="sub">${fmtSize(f.size)}</div>
        </div>
        ${chip}
        <button class="btn sm" data-convert="${i}" ${disabled ? "disabled" : ""}>转换</button>
      </div>`;
  }).join("");

  list.querySelectorAll("input[data-idx]").forEach(cb => {
    cb.addEventListener("change", updateSelection);
  });
  list.querySelectorAll("button[data-convert]").forEach(btn => {
    btn.addEventListener("click", () => convertFiles([state.files[+btn.dataset.convert].path]));
  });
  updateSelection();
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function selectedFiles() {
  return [...document.querySelectorAll("#fileList input[data-idx]:checked")]
    .map(cb => state.files[+cb.dataset.idx].path);
}

function updateSelection() {
  const checked = document.querySelectorAll("#fileList input[data-idx]:checked").length;
  const enabled = [...document.querySelectorAll("#fileList input[data-idx]:not(:disabled)")];
  $("btnConvertSelected").disabled = checked === 0;
  $("btnConvertSelected").textContent = checked ? `转换所选 (${checked})` : "转换所选";
  $("checkAll").checked = enabled.length > 0 && checked === enabled.length;
}

async function convertFiles(paths) {
  if (!paths.length) return;
  try {
    const res = await post("/api/jobs", { files: paths });
    if (res.added === 0) {
      toast("这些文件已在队列中");
    } else {
      toast(`已加入 ${res.added} 个转换任务`);
    }
    await loadJobs();
    scan(true);
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------------- 任务 ---------------- */
async function loadJobs() {
  try {
    const data = await api("/api/jobs");
    state.jobs = data.jobs;
    renderJobs();
    setStatusFromJobs();
  } catch (_) {
    setStatus("连接断开", "err");
  }
}

function setStatusFromJobs() {
  const running = state.jobs.filter(j => j.state === "running" || j.state === "moving");
  const queued = state.jobs.filter(j => j.state === "queued").length;
  if (running.length) {
    const j = running[0];
    setStatus(`${STATE_TEXT[j.state]} · ${j.pct.toFixed(0)}%${j.speed ? ` · ${j.speed}` : ""}`, "busy");
  } else if (queued) {
    setStatus(`${queued} 个任务排队中`, "busy");
  } else {
    setStatus("就绪", "");
  }
}

function renderJobs() {
  const list = $("jobList");
  if (!state.jobs.length) {
    if (state.jobsSig !== "[]") {
      state.jobsSig = "[]";
      list.innerHTML = `<div class="empty">暂无任务</div>`;
    }
    return;
  }

  // 轮询结果没变化就不重绘，避免进度条闪烁
  const sig = JSON.stringify(state.jobs);
  if (sig === state.jobsSig) return;
  state.jobsSig = sig;

  // 从后往前展示最新任务（最多 60 条）
  const jobs = [...state.jobs].reverse().slice(0, 60);
  list.innerHTML = jobs.map(j => {
    const cls = j.state === "done" || j.state === "skipped" ? "done"
      : j.state === "failed" ? "failed" : "";
    const pct = j.state === "done" || j.state === "skipped" ? 100 : (j.pct || 0);
    const chipCls = j.state === "done" ? "ok"
      : j.state === "failed" ? "err"
      : j.state === "skipped" || j.state === "canceled" ? ""
      : "run";
    const detail = [];
    if (j.state === "running" || j.state === "moving") {
      detail.push(`${pct.toFixed(1)}%`);
      if (j.speed) detail.push(j.speed);
      if (j.fps) detail.push(`${j.fps} fps`);
      if (j.written) detail.push(fmtSize(j.written));
      if (j.duration) detail.push(`总时长 ${fmtTime(j.duration)}`);
    } else if (j.state === "done") {
      detail.push("输出：" + j.out);
    } else if (j.state === "skipped") {
      detail.push("目标 mp4 已存在，未覆盖");
    } else if (j.state === "failed") {
      detail.push(j.error || "转换失败");
    }
    const cancelBtn = (j.state === "queued" || j.state === "running")
      ? `<button class="link" data-cancel="${j.id}">取消</button>` : "";
    const logBtn = `<button class="link" data-log="${j.id}">日志</button>`;
    const liveLog = (j.state === "running" || j.state === "moving") && j.last_log
      ? `<div class="job-log" title="${esc(j.last_log)}">${esc(j.last_log)}</div>` : "";

    return `
      <div class="job">
        <div class="job-top">
          <div class="name" title="${esc(j.src)}">${esc(j.name)}</div>
          <span class="chip ${chipCls}">${STATE_TEXT[j.state] || j.state}</span>
          ${logBtn}
          ${cancelBtn}
        </div>
        <div class="bar ${cls}"><span style="width:${pct}%"></span></div>
        <div class="job-sub">
          <span class="err">${esc(detail.join(" · "))}</span>
          <span>${fmtSize(j.size)}</span>
        </div>
        ${liveLog}
      </div>`;
  }).join("");

  list.querySelectorAll("button[data-cancel]").forEach(btn => {
    btn.addEventListener("click", async () => {
      try {
        await post("/api/jobs/cancel", { id: btn.dataset.cancel });
        loadJobs();
      } catch (e) { toast(e.message, true); }
    });
  });

  list.querySelectorAll("button[data-log]").forEach(btn => {
    btn.addEventListener("click", () => {
      const job = state.jobs.find(j => j.id === btn.dataset.log);
      openLog(btn.dataset.log, job ? job.name : "");
    });
  });

  // 有任务完成时刷新待转换列表
  let finished = false;
  for (const j of state.jobs) {
    const prev = state.lastJobStates[j.id];
    if (prev && prev !== j.state && ["done", "failed", "skipped", "canceled"].includes(j.state)) {
      finished = true;
    }
    state.lastJobStates[j.id] = j.state;
  }
  if (finished) scan(true);
}

function setStatus(text, cls) {
  const el = $("status");
  el.className = "status" + (cls ? " " + cls : "");
  el.querySelector("span").textContent = text;
}

/* ---------------- 目录浏览 ---------------- */
async function openBrowse(target) {
  state.browseTarget = target;
  const current = target === "input" ? $("inputPath").value.trim() : $("outputPath").value.trim();
  $("browseTitle").textContent = target === "input" ? "选择输入目录或视频文件" : "选择输出目录";
  $("browseModal").classList.remove("hidden");
  await navigate(current || null);
}

async function navigate(path) {
  $("browser").innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const q = path ? `?path=${encodeURIComponent(path)}` : "";
    const data = await api("/api/browse" + q);
    state.browsePath = data.path;
    renderCrumbs(data.path, data.roots);
    renderBrowser(data);
  } catch (e) {
    $("browser").innerHTML = `<div class="empty">无法打开目录</div>`;
    toast(e.message, true);
  }
}

function renderCrumbs(path, roots) {
  const root = roots.find(r => path === r || path.startsWith(r.replace(/\/$/, "") + "/")) || "/";
  const rest = path.slice(root === "/" ? 0 : root.length).split("/").filter(Boolean);
  const parts = [{ name: root === "/" ? "/" : root, path: root }];
  let acc = root === "/" ? "" : root;
  for (const seg of rest) {
    acc += "/" + seg;
    parts.push({ name: seg, path: acc });
  }
  $("crumbs").innerHTML = parts.map((p, i) => {
    const sep = i ? `<span class="sp">/</span>` : "";
    return `${sep}<button data-path="${esc(p.path)}">${esc(p.name)}</button>`;
  }).join("");
  $("crumbs").querySelectorAll("button").forEach(b => {
    b.addEventListener("click", () => navigate(b.dataset.path));
  });
}

function renderBrowser(data) {
  const box = $("browser");
  const items = [];

  if (data.parent) {
    items.push(`<div class="entry" data-dir="${esc(data.parent)}"><span class="ico">↰</span><span>上一级</span></div>`);
  }
  for (const d of data.dirs) {
    items.push(`<div class="entry" data-dir="${esc(d.path)}"><span class="ico">▸</span><span>${esc(d.name)}</span></div>`);
  }
  if (state.browseTarget === "input") {
    for (const f of data.files) {
      items.push(`<div class="entry file" data-file="${esc(f.path)}"><span class="ico">◻</span><span>${esc(f.name)}</span><span class="sz">${fmtSize(f.size)}</span></div>`);
    }
  }

  box.innerHTML = items.length ? items.join("") : `<div class="empty">此目录下没有子目录${state.browseTarget === "input" ? "或视频文件" : ""}</div>`;

  box.querySelectorAll("[data-dir]").forEach(el => {
    el.addEventListener("click", () => navigate(el.dataset.dir));
  });
  box.querySelectorAll("[data-file]").forEach(el => {
    el.addEventListener("click", () => {
      $("inputPath").value = el.dataset.file;
      closeBrowse();
      updateRisk();
      saveSettings(true);
    });
  });
}

function closeBrowse() {
  $("browseModal").classList.add("hidden");
}

/* ---------------- 转换日志 ---------------- */
const logState = { id: null, next: 0, timer: null };

function stopLogPolling() {
  if (logState.timer) {
    clearInterval(logState.timer);
    logState.timer = null;
  }
}

async function openLog(id, name) {
  stopLogPolling();
  logState.id = id;
  logState.next = 0;
  $("logTitle").textContent = name ? `转换日志 · ${name}` : "转换日志";
  $("logView").textContent = "";
  $("logState").textContent = "实时";
  $("logState").className = "chip run";
  $("logModal").classList.remove("hidden");
  await pullLog();
  logState.timer = setInterval(pullLog, 800);
}

async function pullLog() {
  if (!logState.id) return;
  const id = logState.id;
  try {
    const data = await post("/api/joblog", { id, since: logState.next });
    if (logState.id !== id) return;
    if (data.lines && data.lines.length) {
      const view = $("logView");
      const atBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 60;
      view.appendChild(document.createTextNode((view.textContent ? "\n" : "") + data.lines.join("\n")));
      if (atBottom) view.scrollTop = view.scrollHeight;
    }
    logState.next = data.next || 0;

    const job = state.jobs.find(j => j.id === id);
    const running = job && ["queued", "running", "moving"].includes(job.state);
    const el = $("logState");
    if (running) {
      el.textContent = job.state === "running" ? "实时" : STATE_TEXT[job.state];
      el.className = "chip run";
    } else {
      el.textContent = job ? (STATE_TEXT[job.state] || job.state) : "已结束";
      el.className = "chip " + (job && job.state === "failed" ? "err"
        : job && job.state === "done" ? "ok" : "");
      stopLogPolling();
    }
  } catch (e) {
    stopLogPolling();
    console.error("log fetch failed", e);
    $("logState").textContent = "日志不可用";
    $("logState").className = "chip err";
    $("logView").textContent += (($("logView").textContent ? "\n" : "")
      + "获取日志失败：" + (e && e.message ? e.message : e));
  }
}

function closeLog() {
  stopLogPolling();
  logState.id = null;
  $("logModal").classList.add("hidden");
}

/* ---------------- 通用确认 ---------------- */
function confirmDialog(title, text, okText = "继续") {
  return new Promise(resolve => {
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = text;
    $("confirmYes").textContent = okText;
    $("confirmModal").classList.remove("hidden");

    const done = (v) => {
      $("confirmModal").classList.add("hidden");
      $("confirmYes").removeEventListener("click", yes);
      $("confirmNo").removeEventListener("click", no);
      resolve(v);
    };
    const yes = () => done(true);
    const no = () => done(false);
    $("confirmYes").addEventListener("click", yes);
    $("confirmNo").addEventListener("click", no);
  });
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  $("btnSave").addEventListener("click", () => saveSettings(false));
  $("btnReset").addEventListener("click", resetSettings);
  $("btnScan").addEventListener("click", () => scan(false));

  $("btnBrowseInput").addEventListener("click", () => openBrowse("input"));
  $("btnBrowseOutput").addEventListener("click", () => openBrowse("output"));
  $("btnResetExt").addEventListener("click", () => {
    $("extensions").value = state.settings.default_extensions || "";
    saveSettings(true);
  });
  $("extensions").addEventListener("change", () => saveSettings(true));
  $("command").addEventListener("change", () => saveSettings(true));
  $("btnResetCmd").addEventListener("click", () => {
    $("command").value = state.settings.default_command || "";
    saveSettings(true);
  });
  $("btnClearOutput").addEventListener("click", () => {
    $("outputPath").value = "";
    updateRisk();
    saveSettings(true);
  });

  $("outputPath").addEventListener("input", updateRisk);
  $("moveToOutput").addEventListener("change", updateRisk);
  $("inputPath").addEventListener("change", () => scan(true));
  $("recursive").addEventListener("change", () => saveSettings(true));

  $("checkAll").addEventListener("change", (e) => {
    document.querySelectorAll("#fileList input[data-idx]:not(:disabled)").forEach(cb => {
      cb.checked = e.target.checked;
    });
    updateSelection();
  });

  $("btnConvertSelected").addEventListener("click", () => convertFiles(selectedFiles()));
  $("btnClearJobs").addEventListener("click", async () => {
    await post("/api/jobs/clear", {});
    loadJobs();
  });

  $("browseClose").addEventListener("click", closeBrowse);
  $("browseCancel").addEventListener("click", closeBrowse);
  $("browsePickDir").addEventListener("click", () => {
    if (state.browseTarget === "input") $("inputPath").value = state.browsePath;
    else $("outputPath").value = state.browsePath;
    closeBrowse();
    updateRisk();
    saveSettings(true);
  });

  $("browseModal").addEventListener("click", (e) => {
    if (e.target === $("browseModal")) closeBrowse();
  });

  $("logClose").addEventListener("click", closeLog);
  $("logModal").addEventListener("click", (e) => {
    if (e.target === $("logModal")) closeLog();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeBrowse();
      closeLog();
      if (!$("confirmModal").classList.contains("hidden")) $("confirmNo").click();
    }
  });
}

/* ---------------- 启动 ---------------- */
(async function init() {
  bind();
  try {
    const auth = await api("/api/auth");
    if (auth.required) $("logoutBtn").hidden = false;
  } catch (_) {}
  try {
    await loadSettings();
    await loadJobs();
    await scan(true);
  } catch (e) {
    toast(e.message, true);
  }
  $("logoutBtn").addEventListener("click", async () => {
    try { await post("/api/logout", {}); } catch (_) {}
    location.replace("/login");
  });
  setInterval(loadJobs, 1500);
})();
