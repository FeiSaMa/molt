/* Small browser client for the local Agent API. Uses fetch for SSE so the
 * Bearer token can be sent; native EventSource cannot set Authorization. */
(function () {
  "use strict";
  var state = { base: "", token: "", session: null, cursor: 0, busy: false, abort: null, lab: { caps: null, runId: null, busy: false, abort: null } };
  var $ = function (id) { return document.getElementById(id); };
  var messages = $("messages"), input = $("message-input"), send = $("send-button");
  var dialog = $("settings-dialog"), error = $("settings-error");

  function api(path, options) {
    var opts = options || {};
    opts.headers = Object.assign({ "Authorization": "Bearer " + state.token, "Accept": "application/json" }, opts.headers || {});
    return fetch(state.base.replace(/\/$/, "") + path, opts).then(function (res) {
      return res.text().then(function (text) {
        var body = text ? JSON.parse(text) : null;
        if (!res.ok) { var e = new Error(body && body.error ? body.error.message : "请求失败（" + res.status + "）"); e.status = res.status; throw e; }
        return body;
      });
    });
  }

  function clearWelcome() { var w = $("welcome"); if (w) w.remove(); }
  function addMessage(role, text, tool) {
    clearWelcome();
    var row = document.createElement("div"); row.className = "message-row " + role;
    var avatar = document.createElement("div"); avatar.className = "message-avatar"; avatar.textContent = role === "user" ? "你" : "m";
    var box = document.createElement("div"); box.className = "message-bubble";
    var content = document.createElement("span"); content.className = "message-content"; content.textContent = text || ""; box.appendChild(content);
    row.appendChild(avatar); row.appendChild(box); messages.appendChild(row);
    if (tool) { var badge = document.createElement("div"); badge.className = "tool-badge"; badge.textContent = "⌕ 读取实验：" + tool; box.appendChild(badge); }
    messages.scrollTop = messages.scrollHeight; return content;
  }
  function setStatus(text) { $("composer-status").textContent = text; $("status-pulse").className = "status-pulse" + (state.busy ? " is-busy" : ""); }
  function setConnected(yes) { $("connection-dot").className = "live-dot" + (yes ? " is-connected" : ""); $("session-label").textContent = yes ? "已连接" : "尚未连接"; document.body.classList.toggle("connected", yes); }
  function setStage(stage) { var steps = document.querySelectorAll(".track-step"), order = ["thinking", "tool", "responding"], active = stage === "tool" ? 1 : stage === "responding" ? 2 : 0; for (var i = 0; i < steps.length; i++) { var n = order.indexOf(steps[i].getAttribute("data-stage")); steps[i].classList.toggle("is-active", stage && n === active); steps[i].classList.toggle("is-done", stage && n < active); } document.body.classList.toggle("agent-working", !!stage); }
  function setEnabled(yes) { input.disabled = !yes; send.disabled = !yes || state.busy; $("new-chat").disabled = !yes || state.busy; }
  function showError(text) { error.textContent = text; error.hidden = false; }
  function hideError() { error.hidden = true; error.textContent = ""; }

  function element(tag, cls, text) { var node = document.createElement(tag); node.className = cls || ""; if (text !== undefined) node.textContent = text; return node; }
  function empty(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function showExperiment(experiment, button) {
    document.querySelectorAll(".experiment-select").forEach(function (item) { item.setAttribute("aria-pressed", String(item === button)); });
    var detail = $("experiment-detail"), m = experiment.metrics, coverage = experiment.provenance.coverage;
    empty(detail);
    detail.appendChild(element("h3", "", experiment.title));
    detail.appendChild(element("p", "evolution-note", experiment.experiment_id + " · " + experiment.stage + " · " + experiment.status));
    var metrics = element("div", "evolution-metrics");
    [["通过 / " + m.score_scope, m.passed + " / " + m.total], ["记录数", m.recorded_runs], ["成本 / " + m.cost.scope, "$" + m.cost.usd.toFixed(6)], ["墙钟 / " + m.wall_ms.scope, (m.wall_ms.value / 1000).toFixed(1) + "s"]].forEach(function (pair) { var cell = element("div"); cell.appendChild(element("small", "", pair[0])); cell.appendChild(element("b", "", pair[1])); metrics.appendChild(cell); });
    detail.appendChild(metrics);
    detail.appendChild(element("p", "evolution-note", "来源：" + experiment.provenance.kind + " · 有运行证据 " + coverage.records_with_evidence + " / " + coverage.records_total));
    detail.appendChild(element("p", "evolution-note", "scenario：" + (experiment.scenario_version || "未记录") + " · shell：" + (experiment.shell_version || "未记录")));
    Object.keys(experiment.comparison).forEach(function (key) { var group = experiment.comparison[key]; if (!group) return; detail.appendChild(element("p", "comparison-line", key + "：run " + group.runs_solved + "/" + group.runs + "；task " + group.tasks_solved + "/" + group.tasks)); });
    detail.appendChild(element("p", "evolution-note", "成本" + (m.cost.estimated ? "为估算；" : "；") + "对照/反思/重试包含情况：" + [m.cost.includes_control, m.cost.includes_reflection, m.cost.includes_retries].map(function (yes) { return yes ? "含" : "不含"; }).join(" / ") + "。墙钟" + (m.wall_ms.includes_reflection ? "包含" : "不含") + "反思。"));
    if (!experiment.comparability_key) detail.appendChild(element("p", "evolution-caution", "独立实验口径：不可直接与其他实验合并或计算提升幅度。"));
    var caveats = element("ul", "evolution-caveats");
    experiment.caveats.forEach(function (code) { caveats.appendChild(element("li", "", code.replace(/_/g, " "))); });
    detail.appendChild(caveats);
    var evidence = element("details", "evolution-sources"); evidence.appendChild(element("summary", "", "查看证据来源与完整性"));
    experiment.evidence.files.forEach(function (file) { evidence.appendChild(element("p", "", file)); });
    Object.keys(experiment.evidence.completeness).forEach(function (key) { evidence.appendChild(element("p", "", key + "：" + experiment.evidence.completeness[key])); });
    detail.appendChild(evidence);
    var ask = element("button", "prompt-chip", "让 Agent 解释这项实验 →"); ask.type = "button"; ask.addEventListener("click", function () { input.value = "请读取实验 " + experiment.experiment_id + "，说明结果、对照和结论边界。"; input.focus(); }); detail.appendChild(ask);
  }
  function loadExperiments() {
    var status = $("experiments-status"), button = $("refresh-experiments"); button.disabled = true; status.textContent = "正在读取实验索引…";
    return api("/api/experiments").then(function (index) {
      var list = $("experiment-list"); empty(list); empty($("experiment-detail"));
      status.textContent = index.experiments.length + " 项实验 · 更新于 " + index.generated_at + " · 各实验独立计分";
      index.experiments.forEach(function (experiment, i) { var select = element("button", "experiment-select", experiment.title + " · " + experiment.stage); select.type = "button"; select.setAttribute("aria-pressed", "false"); select.addEventListener("click", function () { showExperiment(experiment, select); }); list.appendChild(select); if (i === 0) showExperiment(experiment, select); });
    }).catch(function () { empty($("experiment-list")); empty($("experiment-detail")); status.textContent = "实验数据加载失败，可点击刷新重试；对话仍可使用。"; }).finally(function () { button.disabled = false; });
  }
  function loadTaskComparison() {
    var root = $("task-comparison"), count = $("task-count");
    return api("/api/tasks").then(function (payload) {
      empty(root); var results = payload.results || [], max = 0;
      results.forEach(function (task) { max = Math.max(max, task.cost_first || 0, task.cost_second || 0); });
      count.textContent = results.length + " 题";
      results.forEach(function (task, index) {
        var row = element("article", "task-row");
        var head = element("div", "task-row-head"); head.appendChild(element("span", "task-number", String(index + 1).padStart(2, "0"))); head.appendChild(element("b", "task-id", task.id));
        var delta = task.cost_first > 0 ? ((1 - task.cost_second / task.cost_first) * 100) : null;
        head.appendChild(element("span", "task-delta", delta === null ? "—" : (delta >= 0 ? "−" : "+") + Math.abs(delta).toFixed(0) + "%")); row.appendChild(head);
        [["first", task.cost_first, task.passed_first], ["second", task.cost_second, task.passed_second]].forEach(function (side) {
          var line = element("div", "task-cost-line " + side[0]); line.appendChild(element("span", "task-pass " + (side[2] ? "passed" : "not-passed"), side[2] ? "✓" : "×"));
          var track = element("span", "task-bar-track"); var bar = element("i", "task-bar"); bar.style.width = max > 0 ? Math.max(2, (side[1] / max) * 100) + "%" : "0%"; track.appendChild(bar); line.appendChild(track); line.appendChild(element("span", "task-cost", "$" + Number(side[1] || 0).toFixed(4))); row.appendChild(line);
        });
        root.appendChild(row);
      });
    }).catch(function () { empty(root); count.textContent = "不可用"; root.appendChild(element("p", "evolution-note", "逐题数据暂不可用；实验摘要仍可查看。")); });
  }
  $("refresh-experiments").addEventListener("click", loadExperiments);

  function setLabStatus(text) { var node = $("lab-status"); if (node) node.textContent = text; }
  function setLabError(text) { var node = $("lab-error"); if (!node) return; if (text) { node.textContent = text; node.hidden = false; } else { node.hidden = true; node.textContent = ""; } }
  function finishLab() { var lab = state.lab; lab.busy = false; lab.abort = null; $("lab-cancel").disabled = true; $("lab-run").disabled = !(lab.caps && lab.caps.execution && lab.caps.execution.available); }
  function markLabMeta(id, text, cls) { var node = $(id); if (!node) return; node.textContent = text; node.className = cls || ""; }
  function loadLabCapabilities() {
    var lab = state.lab;
    return api("/api/lab/capabilities").then(function (caps) {
      lab.caps = caps;
      var ex = caps.execution || {}, pv = caps.provider || {}, ev = caps.evolution || {};
      var ok = !!ex.available;
      var chip = $("isolation-chip"); if (chip) { chip.textContent = ok ? "ISOLATION READY" : "ISOLATION BLOCKED"; chip.className = "provider-chip " + (ok ? "is-ok" : "is-blocked"); }
      markLabMeta("lab-provider", "provider " + (pv.name || "?") + (pv.real_model ? " · LIVE" : " · MOCK"), pv.real_model ? "is-ok" : "");
      markLabMeta("lab-isolation", "isolation " + (ok ? "ready" : (ex.reason || "blocked")), ok ? "is-ok" : "is-blocked");
      markLabMeta("lab-evolution", "evolution " + (ev.mode || "?") + (ev.real_model ? " · real" : " · mock"), ev.real_model ? "is-ok" : "is-mock");
      var note = $("lab-capabilities");
      if (note) note.textContent = ok
        ? "隔离自检通过（" + (ex.backend || "bwrap") + "）；每轮真实跑测试，最多 2 轮 / 60s / 输出 16KB。"
        : "隔离不可用：" + (ex.detail || ex.reason || "fail closed") + "；执行端点已关闭，不会向公网暴露 shell。";
      $("lab-run").disabled = !ok; if (!lab.busy) setLabStatus(ok ? "就绪" : "已阻断");
      return api("/api/lab/tasks").then(function (payload) {
        var tasks = (payload && payload.tasks) || [], select = $("lab-task-select");
        if (tasks.length && select) {
          select.textContent = "";
          tasks.forEach(function (task) { var option = document.createElement("option"); option.value = task.task_id; option.textContent = task.task_id + " · " + task.title; select.appendChild(option); });
          select.value = tasks[0].task_id;
          $("lab-task").textContent = tasks[0].task_id + " · " + tasks[0].title;
          select.addEventListener("change", function () { var found = tasks.filter(function (task) { return task.task_id === select.value; })[0]; if (found) $("lab-task").textContent = found.task_id + " · " + found.title; });
        }
      }).catch(function () {});
    }).catch(function (err) {
      setLabStatus("能力未知"); setLabError("能力检查失败：" + err.message); $("lab-run").disabled = true;
    });
  }
  function appendLabRound(name, data) {
    var root = $("lab-rounds"); if (!root) return;
    var row = document.createElement("article"); row.className = "lab-round";
    var head = document.createElement("div"); head.className = "lab-round-head";
    var title = document.createElement("b"); title.textContent = name; head.appendChild(title);
    var pass = document.createElement("span"); pass.className = "lab-pass " + (data.passed ? "ok" : "bad"); pass.textContent = (data.passed ? "通过" : "未通过") + " · " + data.duration_ms + "ms"; head.appendChild(pass);
    row.appendChild(head);
    var text = (data.stdout || "") + (data.stderr ? (data.stdout ? "\n" : "") + data.stderr : "");
    if (text) { var pre = document.createElement("pre"); pre.textContent = text; row.appendChild(pre); }
    root.appendChild(row);
  }
  function handleLabEvent(event, data) {
    if (event === "run.started") { setLabStatus("运行中"); return; }
    if (event === "round1.completed") { appendLabRound("round1 · 初始测试", data); return; }
    if (event === "round2.completed") { appendLabRound("round2 · patch 后测试", data); return; }
    if (event === "evolution.proposed") {
      var prov = $("lab-provenance"); if (prov) { prov.textContent = "patch 来源：" + data.source + (data.real_model ? "（真实 provider）" : "（mock 确定性 fixture）"); prov.className = "lab-provenance " + (data.real_model ? "is-live" : "is-mock"); }
      return;
    }
    if (event === "evolution.diff") { var diff = $("lab-diff"); if (diff) { diff.textContent = data.diff || ""; diff.hidden = !data.diff; } return; }
    if (event === "run.completed") { renderLabResult({ status: "completed", result: data }); return; }
    if (event === "run.failed") { setLabStatus("失败"); setLabError("执行失败：" + (data.message || data.code || "未知错误")); finishLab(); return; }
    if (event === "run.cancelled") { setLabStatus("已取消"); finishLab(); return; }
    if (event === "done") { finishLab(); }
  }
  function readLabSse(response, onEvent) {
    var reader = response.body.getReader(), decoder = new TextDecoder(), buffer = "", event = "message", data = "";
    function flush(frame) {
      var lines = frame.split(/\r?\n/); event = "message"; data = "";
      lines.forEach(function (line) {
        if (line.indexOf("event:") === 0) event = line.slice(6).trim();
        else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
        else if (line.indexOf("id:") === 0) state.lab.cursor = Number(line.slice(3).trim()) || state.lab.cursor;
      });
      if (data) { try { onEvent(event, JSON.parse(data)); } catch (_) {} }
    }
    function pump() { return reader.read().then(function (part) { if (part.done) return; buffer += decoder.decode(part.value, { stream: true }); var chunks = buffer.split(/\r?\n\r?\n/); buffer = chunks.pop(); chunks.forEach(flush); return pump(); }); }
    return pump();
  }
  function renderLabResult(run) {
    var result = run.result || {}, root = $("lab-rounds"); setLabError("");
    root.textContent = "";
    if (result.round1) appendLabRound("round1 · 初始测试", result.round1);
    if (result.round2) appendLabRound("round2 · patch 后测试", result.round2);
    if (result.diff) { $("lab-diff").textContent = result.diff; $("lab-diff").hidden = false; }
    setLabStatus(result.adopted ? "已完成 · 通过" : "已完成 · 未通过");
    var p = result.provenance || {}, node = $("lab-provenance");
    if (node) {
      var fixture = p.kind === "lab_fixture" && p.historical === false;
      node.textContent = (p.real_model ? "LIVE provider patch · 真实模型两轮修复" : "MOCK 演示：真实模型未配置；patch 来自确定性 fixture，不是真实模型进化") + (fixture ? "；lab_fixture，非历史 demo/probe 数据" : "");
      node.className = "lab-provenance " + (p.real_model ? "is-live" : "is-mock");
    }
    finishLab();
  }
  function applyLabRun(run) {
    if (run.status === "completed") { renderLabResult(run); return; }
    if (run.status === "failed") {
      setLabStatus("失败"); setLabError("执行失败：" + ((run.error || {}).message || (run.error || {}).code || "未知错误")); finishLab(); return;
    }
    if (run.status === "cancelled") { setLabStatus("已取消"); finishLab(); }
  }
  function recoverLabRun(attempt) {
    var lab = state.lab;
    if (!lab.runId) { finishLab(); return; }
    if (attempt > 5) {
      setLabStatus("连接中断"); setLabError("事件流中断，无法确认任务是否终止；请刷新页面或稍后重试。");
      return;
    }
    setLabStatus("连接中断，正在核对…");
    api("/api/lab/runs/" + encodeURIComponent(lab.runId)).then(function (run) {
      if (run.status === "completed" || run.status === "failed" || run.status === "cancelled") { applyLabRun(run); return; }
      setLabStatus("运行中，正在重连事件流…");
      lab.abort = new AbortController();
      return fetch(state.base + "/api/lab/runs/" + encodeURIComponent(lab.runId) + "/events?since=" + encodeURIComponent(lab.cursor || 0), { headers: { "Authorization": "Bearer " + state.token, "Accept": "text/event-stream" }, signal: lab.abort.signal }).then(function (response) {
        if (!response.ok) throw new Error("执行事件流连接失败（" + response.status + "）");
        return readLabSse(response, handleLabEvent);
      }).then(function () { if (lab.busy) recoverLabRun(attempt + 1); });
    }).catch(function () { window.setTimeout(function () { recoverLabRun(attempt + 1); }, 800); });
  }
  function startLab() {
    var lab = state.lab;
    if (!lab.caps || !lab.caps.execution || !lab.caps.execution.available || lab.busy) return;
    lab.busy = true; lab.runId = null; lab.cursor = 0; $("lab-rounds").textContent = ""; $("lab-diff").hidden = true; $("lab-diff").textContent = ""; $("lab-provenance").textContent = ""; setLabError(""); setLabStatus("提交…"); $("lab-run").disabled = true; $("lab-cancel").disabled = false;
    api("/api/lab/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ task_id: ($("lab-task-select") && $("lab-task-select").value) || "calc-v1" }) }).then(function (accepted) {
      lab.runId = accepted.run_id; setLabStatus("运行中"); lab.abort = new AbortController();
      return fetch(state.base + "/api/lab/runs/" + encodeURIComponent(lab.runId) + "/events?since=0", { headers: { "Authorization": "Bearer " + state.token, "Accept": "text/event-stream" }, signal: lab.abort.signal }).then(function (response) { if (!response.ok) throw new Error("执行事件流连接失败（" + response.status + "）"); return readLabSse(response, handleLabEvent); });
    }).then(function () { if (lab.busy) recoverLabRun(0); }).catch(function (err) {
      if (err && err.name === "AbortError") return;
      if (!lab.runId) { setLabStatus("失败"); setLabError("创建执行任务失败：" + err.message); finishLab(); return; }
      setLabError("事件流中断，正在核对任务状态…"); recoverLabRun(0);
    });
  }
  function cancelLab() {
    var lab = state.lab; if (!lab.runId) return;
    $("lab-cancel").disabled = true; setLabStatus("取消中…");
    api("/api/lab/runs/" + encodeURIComponent(lab.runId), { method: "DELETE" }).catch(function (err) { setLabError("取消失败：" + err.message); });
  }

  function createSession() {
    return api("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(function (session) {
      state.session = session; state.cursor = session.last_event_id || 0; setConnected(true); setEnabled(true); setStatus("已连接，可以提问"); loadExperiments(); loadTaskComparison(); return session;
    });
  }
  function connect(base, token) {
    state.base = base.replace(/\/$/, ""); state.token = token;
    return fetch(state.base + "/api/health", { headers: { "Accept": "application/json" } }).then(function (r) { if (!r.ok) throw new Error("服务健康检查失败（" + r.status + "）"); return r.json(); }).then(function (health) { var chip = $("provider-chip"); chip.textContent = String(health.provider || "agent").toUpperCase() + (health.real_model ? " · LIVE" : ""); chip.title = health.model || ""; return createSession().then(function (session) { return loadLabCapabilities(); }); });
  }

  function addTyping() { var row = document.createElement("div"); row.className = "message-row assistant is-entering"; row.id = "typing"; var avatar = document.createElement("div"); avatar.className = "message-avatar"; avatar.textContent = "m"; var bubble = document.createElement("div"); bubble.className = "message-bubble"; var dots = document.createElement("span"); dots.className = "typing"; for (var i = 0; i < 3; i++) { dots.appendChild(document.createElement("i")); } bubble.appendChild(dots); row.appendChild(avatar); row.appendChild(bubble); messages.appendChild(row); messages.scrollTop = messages.scrollHeight; }
  function removeTyping() { var row = $("typing"); if (row) row.remove(); }
  function finishTurn(text) { removeTyping(); setStage(null); state.busy = false; setEnabled(true); setStatus(text || "已完成"); }
  function recoverChatTurn(sessionId, assistant, attempt, originalError) {
    if (!state.session || state.session.session_id !== sessionId) return;
    if (attempt > 5) {
      state.busy = true; setEnabled(false); setStatus("连接中断，未能确认本轮状态");
      addMessage("assistant", "事件流中断，无法确认本轮是否完成；请刷新页面后重试。");
      return;
    }
    api("/api/sessions/" + encodeURIComponent(sessionId)).then(function (session) {
      if (session.status === "in_progress") {
        setStatus("连接中断，正在重连事件流…");
        state.abort = new AbortController();
        return fetch(state.base + "/api/sessions/" + encodeURIComponent(sessionId) + "/events?since=" + encodeURIComponent(state.cursor || 0), { headers: { "Authorization": "Bearer " + state.token, "Accept": "text/event-stream" }, signal: state.abort.signal }).then(function (response) {
          if (!response.ok) throw new Error("事件流连接失败（" + response.status + "）");
          return readSse(response, assistant);
        }).then(function () {
          if (state.busy) return recoverChatTurn(sessionId, assistant, attempt + 1, originalError);
        });
      }
      var last = (session.messages || []).slice(-1)[0];
      if (assistant.accepted && last && last.role === "assistant" && !assistant.box) assistant.box = addMessage("assistant", last.content, assistant.tool);
      finishTurn("本轮已结束");
      if (originalError) addMessage("assistant", "连接中断：" + originalError.message);
    }).catch(function () {
      window.setTimeout(function () { recoverChatTurn(sessionId, assistant, attempt + 1, originalError); }, 800);
    });
  }
  function handleEvent(event, data, assistant) {
    if (event === "tool.call") { assistant.tool = data.tool || "实验"; setStage("tool"); setStatus("正在读取实验投影…"); return; }
    if (event === "status") { setStage(data.state); setStatus(data.state === "thinking" ? "正在思考…" : data.state === "responding" ? "正在组织回答…" : "正在读取实验…"); return; }
    if (event === "message.delta") { if (!assistant.box) assistant.box = addMessage("assistant", "", assistant.tool); assistant.box.textContent += data.text || ""; messages.scrollTop = messages.scrollHeight; return; }
    if (event === "message.completed") { if (!assistant.box) assistant.box = addMessage("assistant", data.content || "", assistant.tool); else assistant.box.textContent = data.content || assistant.box.textContent; return; }
    if (event === "error") { if (!assistant.box) addMessage("assistant", "服务错误：" + (data.message || data.code || "未知错误")); finishTurn("本轮未完成"); return; }
    if (event === "done" || event === "turn.completed") { finishTurn("已完成"); }
  }
  function readSse(response, assistant) {
    var reader = response.body.getReader(), decoder = new TextDecoder(), buffer = "", event = "message", data = "";
    function flush(frame) {
      var lines = frame.split(/\r?\n/); event = "message"; data = "";
      lines.forEach(function (line) { if (line.indexOf("event:") === 0) event = line.slice(6).trim(); else if (line.indexOf("data:") === 0) data += line.slice(5).trim(); else if (line.indexOf("id:") === 0) state.cursor = Number(line.slice(3).trim()) || state.cursor; });
      if (data) { try { handleEvent(event, JSON.parse(data), assistant); } catch (_) {} }
    }
    function pump() { return reader.read().then(function (part) { if (part.done) return; buffer += decoder.decode(part.value, { stream: true }); var chunks = buffer.split(/\r?\n\r?\n/); buffer = chunks.pop(); chunks.forEach(flush); return pump(); }); }
    return pump();
  }
  function sendMessage(text) {
    if (!state.session || state.busy || !text.trim()) return;
    state.busy = true; setStage("thinking"); setEnabled(true); addMessage("user", text.trim()); addTyping(); setStatus("正在发送…");
    var sessionId = state.session.session_id, assistant = { box: null, tool: null };
    api("/api/sessions/" + encodeURIComponent(sessionId) + "/messages", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: text.trim() }) }).then(function (accepted) {
      assistant.accepted = true;
      state.cursor = accepted.event_cursor || state.cursor;
      state.abort = new AbortController();
      return fetch(state.base + "/api/sessions/" + encodeURIComponent(sessionId) + "/events?since=" + encodeURIComponent(state.cursor), { headers: { "Authorization": "Bearer " + state.token, "Accept": "text/event-stream" }, signal: state.abort.signal }).then(function (response) { if (!response.ok) throw new Error("事件流连接失败（" + response.status + "）"); return readSse(response, assistant); });
    }).then(function () {
      if (state.busy) recoverChatTurn(sessionId, assistant, 0, null);
    }).catch(function (err) {
      if (err && err.name === "AbortError") return;
      if (!state.busy) return;
      setStatus("连接中断，正在核对…");
      recoverChatTurn(sessionId, assistant, 0, err);
    });
  }

  $("settings-open").addEventListener("click", function () { hideError(); if (state.base) $("api-url").value = state.base; dialog.showModal(); });
  $("settings-cancel").addEventListener("click", function () { dialog.close(); });
  $("settings-form").addEventListener("submit", function (e) { e.preventDefault(); hideError(); var button = $("connect-button"); button.disabled = true; connect($("api-url").value.trim(), $("api-token").value.trim()).then(function () { dialog.close(); }).catch(function (err) { showError(err.message); setConnected(false); setEnabled(false); }).finally(function () { button.disabled = false; }); });
  $("composer").addEventListener("submit", function (e) { e.preventDefault(); var text = input.value; input.value = ""; sendMessage(text); });
  var composing = false;
  input.addEventListener("compositionstart", function () { composing = true; });
  input.addEventListener("compositionend", function () { composing = false; });
  input.addEventListener("keydown", function (e) { if (composing || e.isComposing || e.keyCode === 229) return; if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); } });
  $("new-chat").addEventListener("click", function () { if (state.busy) return; api("/api/sessions/" + encodeURIComponent(state.session.session_id), { method: "DELETE" }).catch(function () {}).finally(function () { while (messages.firstChild) messages.removeChild(messages.firstChild); state.session = null; createSession().catch(function (e) { addMessage("assistant", "无法创建新会话：" + e.message); }); }); });
  document.querySelectorAll(".prompt-chip").forEach(function (button) { button.addEventListener("click", function () { input.value = button.getAttribute("data-prompt") || ""; input.focus(); }); });
  $("lab-run").addEventListener("click", startLab);
  $("lab-cancel").addEventListener("click", cancelLab);

  (function animateEvidenceFlow() {
    var canvas = $("evidence-canvas"), count = $("trace-count");
    if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext("2d"), width = 0, height = 0, frame = 0, raf = 0;
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function resize() { var rect = canvas.getBoundingClientRect(), ratio = Math.min(window.devicePixelRatio || 1, 2); width = rect.width; height = rect.height; canvas.width = Math.max(1, Math.round(width * ratio)); canvas.height = Math.max(1, Math.round(height * ratio)); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); }
    function draw() {
      ctx.clearRect(0, 0, width, height);
      var y = height * .54, xs = [width * .10, width * .37, width * .64, width * .90];
      for (var i = 0; i < xs.length - 1; i++) { ctx.beginPath(); ctx.moveTo(xs[i] + 28, y); ctx.lineTo(xs[i + 1] - 28, y); ctx.setLineDash([3, 4]); ctx.strokeStyle = "rgba(76,96,120,.28)"; ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]); }
      for (var j = 0; j < 3; j++) { var progress = ((frame * .008 + j / 3) % 1), x = xs[0] + (xs[3] - xs[0]) * progress; ctx.beginPath(); ctx.arc(x, y, j === 0 ? 3.1 : 2.2, 0, Math.PI * 2); ctx.fillStyle = j === 1 ? "#b43a2e" : "#1b4d8f"; ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = 8; ctx.fill(); ctx.shadowBlur = 0; }
      frame++;
      if (count) count.textContent = String(frame % 10000).padStart(4, "0");
      if (!reduced && !document.hidden) raf = window.requestAnimationFrame(draw);
    }
    resize(); draw();
    window.addEventListener("resize", function () { resize(); if (reduced) draw(); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) window.cancelAnimationFrame(raf); else if (!reduced) draw(); });
  })();

  (function animateStars() {
    var canvas = $("startrack"), ctx = canvas && canvas.getContext && canvas.getContext("2d");
    if (!ctx) return;
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var width = 0, height = 0, ratio = 1, phase = 0, raf = 0;
    var stars = [];
    for (var i = 0; i < 64; i++) {
      var x = ((i * 73.17) % 100) / 100, y = ((i * 41.53) % 100) / 100;
      stars.push({ x: x, y: y, r: i % 7 === 0 ? 1.2 : 0.7, a: 0.10 + (i % 5) * 0.025 });
    }
    function resize() {
      var box = canvas.getBoundingClientRect();
      ratio = Math.min(window.devicePixelRatio || 1, 2); width = box.width; height = box.height;
      canvas.width = Math.max(1, Math.round(width * ratio)); canvas.height = Math.max(1, Math.round(height * ratio));
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    }
    function draw() {
      ctx.clearRect(0, 0, width, height);
      for (var i = 0; i < stars.length; i++) {
        var s = stars[i]; ctx.beginPath(); ctx.arc(s.x * width, s.y * height, s.r, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(20,22,26," + s.a + ")"; ctx.fill();
      }
      var cx = width * .82, cy = height * .18, radius = Math.min(width, height) * .14;
      ctx.beginPath(); ctx.arc(cx, cy, radius, Math.PI * .15 + phase * .0004, Math.PI * .95 + phase * .0004);
      ctx.strokeStyle = "rgba(27,77,143,.10)"; ctx.lineWidth = 1; ctx.stroke();
      phase++;
      if (!reduced && !document.hidden) raf = window.requestAnimationFrame(draw);
    }
    resize(); draw();
    window.addEventListener("resize", function () { resize(); if (reduced) draw(); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) window.cancelAnimationFrame(raf); else if (!reduced) draw(); });
  })();
})();
