/* Small browser client for the local Agent API. Uses fetch for SSE so the
 * Bearer token can be sent; native EventSource cannot set Authorization. */
(function () {
  "use strict";
  var state = { base: "", token: "", session: null, cursor: 0, busy: false, abort: null, lab: { caps: null, runId: null, busy: false, abort: null }, upper: { graph: null, assets: null, replay: null, selected: null, busy: false, loading: false } };
  var $ = function (id) { return document.getElementById(id); };
  var messages = $("messages"), input = $("message-input"), send = $("send-button");
  var dialog = $("settings-dialog"), error = $("settings-error");

  function api(path, options) {
    var opts = options || {};
    opts.headers = Object.assign({ "Authorization": "Bearer " + state.token, "Accept": "application/json" }, opts.headers || {});
    return fetch(state.base.replace(/\/$/, "") + path, opts).then(function (res) {
      return res.text().then(function (text) {
        var body = text ? JSON.parse(text) : null;
        if (!res.ok) {
          var e = new Error(body && body.error ? body.error.message : "请求失败（" + res.status + "）");
          e.status = res.status;
          if (body && body.error) { e.code = body.error.code; e.details = body.error.details; }
          throw e;
        }
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

  function upperSetStatus(text) { var node = $("upper-status"); if (node) node.textContent = text; }
  function upperSetError(text) { var node = $("upper-error"); if (!node) return; node.textContent = text || ""; node.hidden = !text; }
  function upperDescribe(err) {
    var details = (err && err.details) || {}, reason = details.reason || (err && err.code) || "";
    var extra = "";
    if (details.current_revision !== undefined) extra = "；当前 revision " + details.current_revision;
    else if (details.current_tip) extra = "；当前 tip " + String(details.current_tip).slice(0, 12);
    return (err && err.message ? err.message : "请求失败") + (reason ? " [" + reason + "]" : "") + extra;
  }
  function shortHash(value) { return value ? String(value).slice(0, 12) : "—"; }
  function upperConflictRefresh(err, label) {
    if (!err || (err.status !== 409 && err.status !== 422)) return;
    var message = label + "失败：" + upperDescribe(err);
    upperSetError(message);
    loadUpper().then(function () {
      upperSetError(message + "；已刷新 active/revision");
      upperSetStatus(label + "失败 · 已刷新");
    }).catch(function () { upperSetError(message); });
  }
  function upperBranchList() { return (state.upper.graph && state.upper.graph.branches) || []; }
  function upperSelectedBranch() {
    var branches = upperBranchList(), wanted = state.upper.selected;
    for (var i = 0; i < branches.length; i++) if (branches[i].branch_id === wanted) return branches[i];
    var ops = branches.filter(function (item) { return item.label === "ops"; })[0];
    var branch = ops || branches[0] || null;
    state.upper.selected = branch ? branch.branch_id : null;
    return branch;
  }
  function upperChain(branch) {
    var nodes = (state.upper.graph && state.upper.graph.nodes) || [], byId = {}, chain = [], seen = {};
    nodes.forEach(function (node) { byId[node.node_id] = node; });
    var current = branch ? branch.tip_node_id : null;
    while (current && byId[current] && !seen[current]) {
      seen[current] = true; chain.push(byId[current]);
      var parents = byId[current].parents || []; current = parents.length ? parents[0] : null;
    }
    return chain.reverse();
  }
  function upperOriginText(origin) { return origin === "real_run" ? "REAL RUN" : origin === "seed_import" ? "SEED" : "SYNTHETIC"; }
  function upperEvalText(stateName) {
    if (stateName === "not_evaluated") return "未评估";
    if (stateName === "replayed") return "REPLAY 已复算";
    return "已评估";
  }
  function upperBadge(text, cls) { var node = element("span", "upper-badge " + (cls || ""), text); return node; }
  function renderUpperNode(node, compact) {
    var row = element("article", "upper-node " + (compact ? "is-compact " : "") + "origin-" + (node.origin || "synthetic") + (node.eval_state === "not_evaluated" ? " is-unevaluated" : ""));
    var head = element("div", "upper-node-head");
    head.appendChild(upperBadge(upperOriginText(node.origin), "badge-" + (node.origin || "synthetic")));
    head.appendChild(upperBadge(node.kind, "badge-kind"));
    head.appendChild(upperBadge(upperEvalText(node.eval_state), node.eval_state === "not_evaluated" ? "badge-pending" : "badge-done"));
    row.appendChild(head);
    row.appendChild(element("p", "upper-node-id", "节点 " + shortHash(node.hash_short || node.node_id) + " · context " + shortHash(node.context_hash)));
    if (!compact) row.appendChild(element("p", "upper-node-date", node.created_at || ""));
    return row;
  }
  function renderUpper() {
    var graph = state.upper.graph;
    if (!graph) return;
    var branchesRoot = $("upper-branches"), activeRoot = $("upper-active"), pathRoot = $("upper-path");
    empty(branchesRoot); empty(activeRoot); empty(pathRoot);
    var selected = upperSelectedBranch();
    upperBranchList().forEach(function (branch) {
      var isActive = graph.active && graph.active.branch_id === branch.branch_id;
      var button = element("button", "upper-branch" + (isActive ? " is-active" : "") + (selected && selected.branch_id === branch.branch_id ? " is-selected" : ""));
      button.type = "button";
      button.setAttribute("role", "tab");
      button.setAttribute("aria-pressed", String(!!(selected && selected.branch_id === branch.branch_id)));
      button.appendChild(element("b", "", branch.label));
      button.appendChild(element("small", "", (branch.permission_scope || "scope —") + " · tip " + shortHash(branch.tip_hash)));
      if (isActive) button.appendChild(upperBadge("ACTIVE", "badge-active"));
      button.addEventListener("click", function () { state.upper.selected = branch.branch_id; renderUpper(); });
      branchesRoot.appendChild(button);
    });
    if (graph.active) {
      activeRoot.appendChild(upperBadge("ACTIVE POINTER", "badge-active"));
      activeRoot.appendChild(element("span", "", "revision " + graph.revision + " · 分支 " + (graph.active.branch_id || "—") + " · context " + shortHash(graph.active.context_node_id) + " · hash " + shortHash(graph.active.context_hash)));
    } else {
      activeRoot.appendChild(element("span", "upper-muted", "没有 active pointer"));
    }
    if (!selected) { updateUpperButtons(); return; }
    var chain = upperChain(selected);
    if (!chain.length) pathRoot.appendChild(element("p", "upper-muted", "该分支没有可展示的提交节点。"));
    chain.forEach(function (node, index) {
      if (index) pathRoot.appendChild(element("span", "upper-edge", "↓"));
      pathRoot.appendChild(renderUpperNode(node, false));
    });
    var onChain = {};
    chain.forEach(function (node) { onChain[node.node_id] = true; });
    var others = graph.nodes.filter(function (node) { return !onChain[node.node_id]; });
    if (others.length) {
      var more = element("details", "upper-other-nodes");
      more.appendChild(element("summary", "", "其它节点（未成为 tip）· " + others.length));
      others.forEach(function (node) { more.appendChild(renderUpperNode(node, true)); });
      pathRoot.appendChild(more);
    }
    renderUpperAssets();
    updateUpperButtons();
  }
  function renderUpperAssets() {
    var root = $("upper-assets");
    if (!root) return;
    empty(root);
    var payload = state.upper.assets;
    if (!payload || !payload.assets) { root.appendChild(element("p", "upper-muted", "未读取到资产。")); return; }
    var head = element("div", "upper-section-head");
    head.appendChild(element("span", "", "资产卡片"));
    var counts = payload.counts || {};
    head.appendChild(element("small", "", Object.keys(counts).map(function (kind) { return kind + " " + counts[kind]; }).join(" · ")));
    root.appendChild(head);
    payload.assets.forEach(function (asset) {
      var card = element("article", "upper-asset kind-" + asset.kind);
      var title = element("div", "upper-asset-head");
      title.appendChild(element("b", "", asset.name || asset.asset_id));
      title.appendChild(upperBadge(asset.kind, "badge-kind"));
      card.appendChild(title);
      card.appendChild(element("p", "upper-asset-summary", asset.summary || "（无摘要）"));
      var meta = element("div", "upper-asset-meta");
      meta.appendChild(element("span", "", "v" + (asset.version || "—")));
      meta.appendChild(upperBadge(asset.status || "draft", asset.status === "active" ? "badge-active" : asset.status === "rolled_back" ? "badge-rollback" : "badge-kind"));
      meta.appendChild(upperBadge(upperOriginText(asset.origin), "badge-" + (asset.origin || "synthetic")));
      if (asset.eval_state === "not_evaluated") meta.appendChild(upperBadge("未评估", "badge-pending"));
      else if (asset.eval_state === "replayed") meta.appendChild(upperBadge("REPLAY", "badge-replay"));
      else meta.appendChild(upperBadge("已评估", "badge-done"));
      card.appendChild(meta);
      var identity = asset.seed_identity_hash ? "seed identity " + shortHash(asset.seed_identity_hash) : "record " + shortHash(asset.record_hash || asset.hash_short);
      card.appendChild(element("p", "upper-asset-hash", identity + (asset.classification ? " · " + asset.classification : "")));
      if (asset.kind === "mcp_profile") {
        card.appendChild(element("p", "upper-asset-scope", "权限 " + (asset.permission_scope || "—") + " · 审批 " + (asset.approval_level || "—")));
        if (asset.pending_permission) card.appendChild(element("p", "upper-warning", "权限变更待审批：" + (asset.pending_permission.requested_scope || "—")));
      }
      if (asset.status_reason) card.appendChild(element("p", "upper-muted", asset.status_reason));
      root.appendChild(card);
    });
  }
  function decisionBadge(decision) {
    return upperBadge(String(decision || "—").toUpperCase(), decision === "keep" ? "badge-keep" : decision === "rollback" ? "badge-rollback" : "badge-reject");
  }
  function renderUpperEval() {
    var root = $("upper-eval");
    if (!root) return;
    empty(root);
    var replay = state.upper.replay;
    if (!replay) {
      root.appendChild(element("p", "upper-muted", "尚未运行 REPLAY。点击下方按钮运行固定 C1b 评估；不会调用模型。"));
      return;
    }
    var head = element("div", "upper-eval-head");
    head.appendChild(element("span", "replay-badge", "REPLAY"));
    head.appendChild(element("b", "", "C1b 记忆迁移 / 回退"));
    head.appendChild(element("small", "", (replay.upper_scenario_version || "") + " · " + (replay.replay_id || "")));
    root.appendChild(head);
    root.appendChild(element("p", "upper-muted", "mode=" + replay.mode + " · calls_model=" + String(replay.calls_model) + " · 成本 0（重新判分记录解法）"));
    ["transfer", "regression"].forEach(function (phase) {
      (replay.phases[phase] || []).forEach(function (item) {
        var card = element("article", "upper-eval-row decision-" + (item.upper_decision || ""));
        var rowHead = element("div", "upper-eval-row-head");
        rowHead.appendChild(upperBadge(phase === "transfer" ? "TRANSFER" : "REGRESSION", "badge-kind"));
        rowHead.appendChild(element("b", "", item.asset_id + " · " + item.asset_version));
        rowHead.appendChild(decisionBadge(item.upper_decision));
        card.appendChild(rowHead);
        var candidate = item.candidate || {}, baseline = item.baseline || {};
        card.appendChild(element("p", "upper-eval-numbers", "候选 " + (candidate.passed || 0) + "/" + (candidate.attempts || 0) + " · 基线 " + (baseline.passed || 0) + "/" + (baseline.attempts || 0)));
        card.appendChild(element("p", "upper-muted", item.reason || ""));
        var taskIds = phase === "transfer" ? "x5–x7" : "x8";
        card.appendChild(element("p", "upper-muted", "任务 " + taskIds + " · eval " + (item.evaluation_id || "—")));
        var evidence = element("button", "text-button", "查看逐题证据");
        evidence.type = "button";
        evidence.addEventListener("click", function () { showUpperEvaluation(item.evaluation_id); });
        card.appendChild(evidence);
        root.appendChild(card);
      });
    });
    if (replay.graph_rollback) {
      root.appendChild(element("p", "upper-rollback-note", "负迁移 -> 回退：gen2 在 x8 回归后 rollback 到 " + shortHash(replay.graph_rollback.target_node_id) + "；历史节点与证据保留。"));
    }
    if (replay.graph && replay.graph.nodes) {
      var strip = element("details", "upper-replay-path");
      strip.appendChild(element("summary", "", "REPLAY 路径节点 · " + replay.graph.nodes.length));
      var nodes = replay.graph.nodes;
      nodes.forEach(function (node) { strip.appendChild(renderUpperNode(node, true)); });
      root.appendChild(strip);
    }
  }
  function renderUpperEvidence() {
    var root = $("upper-evidence");
    if (!root) return;
    empty(root);
    var replay = state.upper.replay;
    root.appendChild(element("p", "", "图谱 revision 与 active pointer 来自 /api/upper/graph；资产来自 /api/upper/assets。切换/回退在服务端做 CAS 与审批校验，失败时不改 pointer。"));
    root.appendChild(element("p", "", "REPLAY 在独立状态目录运行，不改当前 synthetic 图谱的 active pointer；资产状态来自上层 registry，与 synthetic 路径分开标注。"));
    root.appendChild(element("p", "", "REPLAY 只重新判分记录解法；synthetic/seed 节点不产生 upper decision，页面标“未评估”。"));
    if (!replay) { root.appendChild(element("p", "upper-muted", "运行 REPLAY 后可展开评估 ID 与 lower_refs。")); return; }
    (replay.decisions || []).forEach(function (decision) {
      var refs = decision.lower_refs || {};
      var line = element("p", "upper-evidence-line");
      line.appendChild(element("b", "", decision.asset_id + " · " + decision.upper_decision + " "));
      line.appendChild(element("span", "", "lower_refs: scenario " + (refs.lower_scenario_id || "—") + "/" + (refs.lower_scenario_version || "—") + " · genome " + shortHash(refs.lower_genome_id) + " · runs " + ((refs.lower_run_ids || []).length)));
      var button = element("button", "text-button", "打开评估");
      button.type = "button";
      button.addEventListener("click", function () { showUpperEvaluation(decision.evaluation_ids && decision.evaluation_ids[0]); });
      line.appendChild(button);
      root.appendChild(line);
    });
    if (replay.pending_permissions && replay.pending_permissions.length) {
      replay.pending_permissions.forEach(function (item) {
        root.appendChild(element("p", "upper-warning", "待审批权限：" + item.asset_id + " -> " + (item.requested_scope || "—") + "（strict）"));
      });
    }
  }
  function showUpperEvaluation(evaluationId) {
    if (!evaluationId) return;
    var root = $("upper-evidence");
    upperSetError("");
    api("/api/upper/evaluations/" + encodeURIComponent(evaluationId)).then(function (record) {
      empty(root);
      root.appendChild(element("h3", "", evaluationId + " · " + record.task_set + " · " + record.status));
      root.appendChild(element("p", "upper-muted", "mode=" + record.mode + " · policy=" + record.asset_id + " v" + record.asset_version));
      (record.tasks || []).forEach(function (task) {
        var recorded = task.recorded || {}, replayed = task.replayed || {}, retrieval = task.retrieval || {};
        var recordedSolved = recorded.solved;
        var recordedText = recordedSolved === true ? "solved" : recordedSolved === false ? "unsolved" : "—";
        var row = element("p", "upper-task-row");
        row.appendChild(element("span", "upper-task-label", task.task_id + " · 变体 " + (recorded.group || "—") + " · r" + task.rep));
        row.appendChild(element("span", "upper-task-source", "source_run " + shortHash(task.source_run_id)));
        row.appendChild(element("span", "upper-task-result", (replayed.passed ? "REPLAY 通过" : "REPLAY 未通过") + " · recorded " + recordedText + " · " + (task.latency_ms || 0) + "ms · cost $" + (task.cost_usd || 0) + " · retrieval " + ((retrieval.hits || []).length) + " hit"));
        root.appendChild(row);
      });
      var counts = record.reference_counts || {};
      root.appendChild(element("p", "upper-muted", "reference_counts: " + JSON.stringify(counts)));
      var telemetry = record.telemetry || {};
      if (telemetry.errors) root.appendChild(element("p", "upper-muted", "errors: " + JSON.stringify(telemetry.errors)));
      if (telemetry.latency_ms) root.appendChild(element("p", "upper-muted", "latency: total " + telemetry.latency_ms.total + "ms · max " + telemetry.latency_ms.max + "ms"));
      if (record.decision) root.appendChild(element("p", "upper-muted", "upper_decision: " + record.decision.upper_decision + " · " + record.decision.reason));
    }).catch(function (err) { upperSetError("读取评估失败：" + upperDescribe(err)); });
  }
  function updateUpperButtons() {
    var graph = state.upper.graph, branch = upperSelectedBranch();
    var busy = state.upper.busy || state.upper.loading;
    var replayButton = $("upper-replay"), switchButton = $("upper-switch"), rollbackButton = $("upper-rollback");
    if (!replayButton || !switchButton || !rollbackButton) return;
    replayButton.disabled = !graph || busy;
    var activeId = graph && graph.active ? graph.active.branch_id : null;
    switchButton.disabled = !graph || !branch || busy || activeId === branch.branch_id;
    rollbackButton.disabled = !graph || !branch || busy || upperChain(branch).length < 2;
  }
  function loadUpper() {
    state.upper.loading = true; updateUpperButtons(); upperSetStatus("读取中…"); upperSetError("");
    return Promise.all([api("/api/upper/graph"), api("/api/upper/assets")]).then(function (results) {
      state.upper.graph = results[0]; state.upper.assets = results[1];
      var branch = upperSelectedBranch();
      upperSetStatus(state.upper.replay ? "REPLAY 已加载" : "就绪");
      $("upper-scope").textContent = "只读图谱 · " + (state.upper.graph.branches || []).length + " 条分支 · " + (state.upper.graph.nodes || []).length + " 个节点；切换与回退需要 CAS 与审批。";
      renderUpper(); renderUpperEval(); renderUpperEvidence(); updateUpperButtons();
      return branch;
    }).catch(function (err) {
      if (err && err.status === 503) {
        upperSetStatus("未启用");
        $("upper-scope").textContent = "上层状态未启用：服务端 --state 必须在仓库外，且上层 evaluator 可用。";
        upperSetError("上层 API 503：" + upperDescribe(err));
      } else {
        upperSetStatus("读取失败");
        upperSetError("读取上层图谱失败：" + upperDescribe(err));
      }
      updateUpperButtons();
    }).finally(function () { state.upper.loading = false; updateUpperButtons(); });
  }
  function startUpperReplay() {
    if (state.upper.busy) return;
    state.upper.busy = true; updateUpperButtons(); upperSetStatus("REPLAY 运行中…"); upperSetError("");
    api("/api/upper/replay", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(function (payload) {
      state.upper.replay = payload;
      return loadUpper().then(function () { upperSetStatus("REPLAY 完成"); renderUpperEval(); renderUpperEvidence(); });
    }).catch(function (err) {
      upperSetStatus("REPLAY 失败");
      upperSetError("运行 REPLAY 失败：" + upperDescribe(err));
    }).finally(function () { state.upper.busy = false; updateUpperButtons(); });
  }
  function switchUpperBranch() {
    var graph = state.upper.graph, branch = upperSelectedBranch();
    if (!graph || !branch || state.upper.busy) return;
    state.upper.busy = true; updateUpperButtons(); upperSetStatus("切换中…"); upperSetError("");
    api("/api/upper/branches/" + encodeURIComponent(branch.branch_id) + "/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: graph.revision, expected_tip: branch.tip_node_id }) }).then(function () {
      return loadUpper().then(function () { upperSetStatus("已切换到 " + branch.label); });
    }).catch(function (err) {
      upperSetStatus("切换失败");
      upperSetError("切换分支失败：" + upperDescribe(err));
      upperConflictRefresh(err, "切换分支");
    }).finally(function () { state.upper.busy = false; updateUpperButtons(); });
  }
  function rollbackUpper() {
    var graph = state.upper.graph, branch = upperSelectedBranch();
    if (!graph || !branch || state.upper.busy) return;
    var chain = upperChain(branch), target = chain.length > 1 ? chain[chain.length - 2] : null;
    if (!target) { upperSetError("回退失败：该分支没有历史父节点。"); return; }
    state.upper.busy = true; updateUpperButtons(); upperSetStatus("回退中…"); upperSetError("");
    api("/api/upper/rollback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ branch_id: branch.branch_id, target_node_id: target.node_id, expected_revision: graph.revision }) }).then(function () {
      return loadUpper().then(function () { upperSetStatus("已回退到 " + shortHash(target.node_id)); });
    }).catch(function (err) {
      upperSetStatus("回退失败");
      upperSetError("回退失败：" + upperDescribe(err));
      upperConflictRefresh(err, "回退");
    }).finally(function () { state.upper.busy = false; updateUpperButtons(); });
  }

  function createSession() {
    return api("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(function (session) {
      state.session = session; state.cursor = session.last_event_id || 0; setConnected(true); setEnabled(true); setStatus("已连接，可以提问"); loadExperiments(); loadTaskComparison(); loadUpper(); return session;
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
  $("upper-replay").addEventListener("click", startUpperReplay);
  $("upper-switch").addEventListener("click", switchUpperBranch);
  $("upper-rollback").addEventListener("click", rollbackUpper);

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
