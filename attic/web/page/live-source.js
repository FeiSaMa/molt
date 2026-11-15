/*!
 * molt · web/page/live-source.js —— S6 实时数据源（真实接口）
 *
 * 事实源：contracts/openapi-v1.json + task-results.schema.json
 *   GET <base>/api/tasks
 *   Authorization: Bearer <token>
 *   -> { "results": [
 *          { "id", "cost_first", "passed_first", "cost_second", "passed_second" }
 *        ] }
 *
 * 只有显式给了地址才会发请求：
 *   ?port=<port>              端口（服务启动时 stdout 的 listen 里的端口）
 *   ?token=<token>            Bearer token（<state>/token 文件内容）
 *   window.MOLT_AGENT_BASE    完整 base，优先于 ?port=
 *   window.MOLT_AGENT_TOKEN   token 的全局注入方式（避免写在地址栏）
 *   window.MOLT_AGENT_PATH    覆盖默认路径 /api/tasks
 * 默认（没有任何地址）：mode=idle，不发请求、不造数据，页面保持空载。
 *
 * 本文件不读本地样例。本地模拟在 mock-source.js（生产可直接删掉那个文件）。
 * 跨域：页面必须用 HTTP 提供，服务启动时用 --allow-origin 精确允许页面 Origin；
 * file://（Origin: null）会被服务端 403，属于预期行为。
 *
 * 约定：ES5、零依赖、不用 fetch。每 0.5s 轮询一次，用结果签名去重。
 */
(function (root) {
  "use strict";

  var DEF_PATH = "/api/tasks";
  var DEF_INTERVAL = 500;
  var DEF_TIMEOUT = 4000;
  var MAX_BACKOFF = 12000;

  var S = {
    base: "",
    path: "",
    mode: "",
    token: "",
    running: false,
    timer: 0,
    xhr: null,
    backoff: 0,
    sig: "",
    runId: "",
    runReady: false,
    runTimer: 0,
    triggering: false,
    autoRun: false,
    force: false,
    source: "",
    replayTimer: 0,
    replayNorm: null,
    replayBase: null,
    replayIndex: 0,
    replayPhase: 0,
    onPayload: null,
    onStatus: null
  };

  function isArr(v) {
    return Object.prototype.toString.call(v) === "[object Array]";
  }

  function isObj(v) {
    return !!v && typeof v === "object" && !isArr(v);
  }

  function str(v) {
    return v === null || v === undefined ? "" : String(v);
  }

  function trim(v) {
    return str(v).replace(/^\s+|\s+$/g, "");
  }

  function stripSlash(v) {
    return str(v).replace(/\/+$/g, "");
  }

  function safeDecode(v) {
    try {
      return decodeURIComponent(str(v));
    } catch (err) {
      return str(v);
    }
  }

  function query(name) {
    var loc = root.location;
    var q = loc && loc.search ? str(loc.search) : "";
    if (!q) return "";
    var m = new RegExp("(?:^|[?&])" + name + "=([^&]*)").exec(q);
    return m ? safeDecode(m[1]) : "";
  }

  /* ---------- 配置解析 ----------
   * 没有地址 → idle（默认空载）；有端口 → live；token 只进请求头。 */
  function resolve() {
    var base = trim(root.MOLT_AGENT_BASE);
    var path = trim(root.MOLT_AGENT_PATH);
    var source = trim(query("source"));
    var runRequested = trim(query("run")) === "1";
    if (!source && !runRequested) source = "probe2c";
    var port = trim(query("port"));
    var token = trim(query("token")) || trim(root.MOLT_AGENT_TOKEN);

    if (!base) {
      /* 经 Web/API 同源代理访问时，默认使用当前 origin；无 token 也保留 base，
         这样 token 表单可以先做有效性检查，未连接时仍不会发 /api/tasks。 */
      var originLoc = root.location;
      if (originLoc && (originLoc.protocol === "http:" || originLoc.protocol === "https:") && originLoc.hostname) {
        base = originLoc.protocol + "//" + originLoc.hostname + (originLoc.port ? ":" + originLoc.port : "");
      }
    }

    if (!base && /^\d{1,5}$/.test(port)) {
      var loc = root.location;
      if (loc && (loc.protocol === "http:" || loc.protocol === "https:") && loc.hostname) {
        /* 页面经代理访问时，port 指向同一主机的 API；file:// 仍回退 loopback。 */
        base = loc.protocol + "//" + loc.hostname + ":" + port;
      } else {
        base = "http" + "://127.0.0.1:" + port;
      }
    }

    if (base) {
      return {
        mode: "live",
        base: stripSlash(base),
        path: path || (DEF_PATH + (source ? "?source=" + encodeURIComponent(source) : "")),
        source: source,
        token: token,
        autoRun: runRequested && !source,
        interval: DEF_INTERVAL,
        timeout: DEF_TIMEOUT
      };
    }

    return {
      mode: "idle",
      base: "",
      path: "",
      source: "",
      token: "",
      autoRun: false,
      interval: 0,
      timeout: DEF_TIMEOUT
    };
  }

  /* ---------- 契约校验：严格按 task-results.schema.json ---------- */
  function check(payload) {
    if (!isObj(payload) || !isArr(payload.results)) {
      return { ok: false, reason: "shape" };
    }

    var list = payload.results;
    for (var i = 0; i < list.length; i++) {
      if (!pairOf(list[i])) return { ok: false, reason: "items" };
    }
    return { ok: true, reason: "results", count: list.length };
  }

  /* ---------- 归一化：只有一组字段名 ---------- */
  function normalize(payload) {
    var out = { pairs: [], count: 0 };
    var list = isObj(payload) && isArr(payload.results) ? payload.results : [];

    var seen = {};
    for (var i = 0; i < list.length; i++) {
      var p = pairOf(list[i]);
      if (!p || seen[p.taskId]) continue;
      seen[p.taskId] = 1;
      /* 题号 = 源数据里的顺序（1 起）；动态建行 / 柱子定位都靠它 */
      p.index = i + 1;
      out.pairs.push(p);
    }
    out.count = out.pairs.length;
    return out;
  }

  function isCost(v) {
    return typeof v === "number" && isFinite(v) && v >= 0;
  }

  function pairOf(o) {
    if (!isObj(o)) return null;
    if (typeof o.id !== "string" || o.id === "") return null;
    if (!isCost(o.cost_first) || !isCost(o.cost_second)) return null;
    if (typeof o.passed_first !== "boolean") return null;
    if (typeof o.passed_second !== "boolean") return null;

    return {
      taskId: o.id,
      baseCost: o.cost_first,
      candCost: o.cost_second,
      basePassed: o.passed_first,
      candPassed: o.passed_second
    };
  }

  function signature(norm) {
    var parts = [norm.pairs.length];
    for (var i = 0; i < norm.pairs.length; i++) {
      var p = norm.pairs[i];
      parts.push(p.taskId + ":" + p.baseCost + ":" + p.candCost + ":" +
        (p.basePassed ? 1 : 0) + (p.candPassed ? 1 : 0));
    }
    return parts.join("|");
  }

  function stopReplay() {
    if (S.replayTimer) {
      root.clearTimeout(S.replayTimer);
      S.replayTimer = 0;
    }
    S.replayNorm = null;
    S.replayBase = null;
    S.replayIndex = 0;
    S.replayPhase = 0;
  }

  function startReplay(norm) {
    stopReplay();
    if (!norm || !isArr(norm.pairs) || !norm.pairs.length) return;
    S.replayNorm = norm;
    S.replayBase = norm.pairs.map(function (pair) {
      return {
        taskId: pair.taskId,
        baseCost: pair.baseCost,
        basePassed: pair.basePassed,
        candCost: 0,
        candPassed: false
      };
    });
    S.replayIndex = 0;
    S.replayPhase = 0;
    replayStep();
  }

  function replayStep() {
    if (!S.running || !S.replayNorm || !S.replayBase) return;
    var norm = S.replayNorm;
    var pairs = norm.pairs;
    var base = S.replayBase;
    var len = pairs.length;
    var partial = [];
    var i;

    if (S.replayPhase === 0) {
      /* 第一轮：只出现基线，所有题按顺序跑完，候选列保持 pending。 */
      if (S.replayIndex >= len) {
        S.replayPhase = 1;
        S.replayIndex = 0;
        S.replayTimer = root.setTimeout(replayStep, 700);
        return;
      }
      for (i = 0; i <= S.replayIndex; i++) partial.push(base[i]);
      S.replayIndex += 1;
      if (S.replayIndex < len) {
        S.replayTimer = root.setTimeout(replayStep, 420);
      } else {
        S.replayPhase = 1;
        S.replayIndex = 0;
        S.replayTimer = root.setTimeout(replayStep, 700);
      }
    } else {
      /* 第二轮：第一轮全部完成后，再逐题补候选结果。 */
      if (S.replayIndex >= len) {
        S.replayNorm = null;
        S.replayBase = null;
        S.replayIndex = 0;
        S.replayPhase = 0;
        S.replayTimer = 0;
        return;
      }
      for (i = 0; i < len; i++) partial.push(i <= S.replayIndex ? pairs[i] : base[i]);
      S.replayIndex += 1;
      S.replayTimer = S.replayIndex < len ? root.setTimeout(replayStep, 420) : 0;
    }
    if (typeof S.onPayload === "function") {
      try { S.onPayload({ pairs: partial, count: partial.length }); } catch (err) { /* 回调异常不打断轮询 */ }
    }
  }

  function emit(kind, detail) {
    if (typeof S.onStatus !== "function") return;
    try {
      S.onStatus(kind, detail);
    } catch (err) { /* 页面回调自己出错不该拖垮轮询 */ }
  }

  function schedule(ms) {
    if (!S.running) return;
    if (S.timer) root.clearTimeout(S.timer);
    S.timer = root.setTimeout(function () {
      S.timer = 0;
      poll();
    }, ms);
  }

  function deliver(payload) {
    var c = check(payload);
    if (!c.ok) {
      emit("contract", c.reason);
      return false;
    }

    var norm = normalize(payload);
    var sig = signature(norm);
    if (sig === S.sig) return true;
    S.sig = sig;
    startReplay(norm);
    return true;
  }

  function accept(text) {
    var payload = null;
    try {
      payload = JSON.parse(text);
    } catch (err) {
      emit("offline", "json");
      return false;
    }
    return deliver(payload);
  }

  function stopTimer() {
    if (S.timer) {
      root.clearTimeout(S.timer);
      S.timer = 0;
    }
  }

  function runSchedule(ms) {
    if (!S.running) return;
    if (S.runTimer) root.clearTimeout(S.runTimer);
    S.runTimer = root.setTimeout(function () {
      S.runTimer = 0;
      pollRun();
    }, ms);
  }

  function triggerRun(force) {
    var xhr;
    try {
      xhr = new root.XMLHttpRequest();
      if (!xhr) return;
      S.triggering = true;
      emit("starting", "probe");
      emit("log", "请求启动 probe replay" + (force ? "（强制新 run）" : ""));
      xhr.open("POST", S.base + "/api/tasks/run", true);
      xhr.timeout = DEF_TIMEOUT;
      xhr.setRequestHeader("Content-Type", "application/json");
      if (S.token) xhr.setRequestHeader("Authorization", "Bearer " + S.token);
      xhr.onreadystatechange = function () {
        if (!xhr || xhr.readyState !== 4) return;
        S.xhr = null;
        if (xhr.status < 200 || xhr.status >= 300) {
          S.triggering = false;
          S.runId = "";
          emit("offline", xhr.status);
          return;
        }
        var payload = null;
        try { payload = JSON.parse(xhr.responseText); } catch (err) { payload = null; }
        if (!payload || typeof payload.run_id !== "string" || !payload.run_id) {
          S.triggering = false;
          emit("offline", "probe");
          return;
        }
        S.runId = payload.run_id;
        S.runReady = false;
        pollRun();
      };
      S.xhr = xhr;
      xhr.send(JSON.stringify({ force: !!force }));
    } catch (err) {
      S.xhr = null;
      S.triggering = false;
      emit("offline", "xhr");
      S.path = DEF_PATH;
      poll();
    }
  }

  function pollRun() {
    if (!S.running || !S.runId) return;
    var xhr;
    try {
      xhr = new root.XMLHttpRequest();
      if (!xhr) return;
      xhr.open("GET", S.base + "/api/probe/runs/" + encodeURIComponent(S.runId), true);
      xhr.timeout = DEF_TIMEOUT;
      if (S.token) xhr.setRequestHeader("Authorization", "Bearer " + S.token);
      xhr.onreadystatechange = function () {
        if (!xhr || xhr.readyState !== 4) return;
        S.xhr = null;
        if (xhr.status < 200 || xhr.status >= 300) {
          S.triggering = false;
          emit("offline", xhr.status);
          S.path = DEF_PATH;
          poll();
          return;
        }
        var payload = null;
        try { payload = JSON.parse(xhr.responseText); } catch (err) { payload = null; }
        if (!payload || typeof payload.status !== "string") {
          S.triggering = false;
          emit("offline", "probe");
          S.path = DEF_PATH;
          poll();
          return;
        }
        if (payload.status === "completed") {
          S.triggering = false;
          S.runReady = true;
          S.path = DEF_PATH + "?probe_run_id=" + encodeURIComponent(S.runId);
          S.sig = "";
          var gate = payload.gate || {};
          var action = payload.action || {};
          emit("log", "probe 完成 · gate=" + (gate.verdict || "—") + " · action=" + (action.kind || "—") + (action.archive_id ? " · archive " + action.archive_id : ""));
          poll();
          return;
        }
        if (payload.status === "failed") {
          emit("log", "probe 失败 · " + ((payload.error && payload.error.code) || "probe_error"));
          S.triggering = false;
          S.runId = "";
          emit("offline", "probe");
          S.path = DEF_PATH;
          poll();
          return;
        }
        runSchedule(1000);
      };
      S.xhr = xhr;
      xhr.send(null);
    } catch (err) {
      S.xhr = null;
      S.triggering = false;
      emit("offline", "xhr");
      S.path = DEF_PATH;
      poll();
    }
  }

  function poll() {
    if (!S.running) return;

    var xhr;
    try {
      xhr = new root.XMLHttpRequest();
      if (!xhr) return;
      xhr.open("GET", S.base + S.path, true);
      xhr.timeout = DEF_TIMEOUT;
      /* 真实接口要求 Bearer；token 只放请求头，不进 URL / 日志 */
      if (S.token) xhr.setRequestHeader("Authorization", "Bearer " + S.token);
      xhr.onreadystatechange = function () {
        if (!xhr || xhr.readyState !== 4) return;
        S.xhr = null;
        var ok = xhr.status >= 200 && xhr.status < 300;
        if (!ok) {
          emit("offline", xhr.status);
          S.backoff = S.backoff ? Math.min(MAX_BACKOFF, S.backoff * 2) : DEF_INTERVAL;
          schedule(S.backoff);
        } else {
          S.backoff = 0;
          if (accept(xhr.responseText)) emit("online", "live");
          schedule(DEF_INTERVAL);
        }
      };
      S.xhr = xhr;
      xhr.send(null);
    } catch (err) {
      S.xhr = null;
      emit("offline", "xhr");
      S.backoff = S.backoff ? Math.min(MAX_BACKOFF, S.backoff * 2) : DEF_INTERVAL;
      schedule(S.backoff);
    }
  }

  function start(opts) {
    if (S.running) {
      /* 已经在跑：换回调即可（重新进屏不叠定时器） */
      if (opts && opts.onPayload) S.onPayload = opts.onPayload;
      if (opts && opts.onStatus) S.onStatus = opts.onStatus;
      return true;
    }

    var cfg = resolve();
    if (!cfg || cfg.mode !== "live") return false;   /* 默认空载：不发请求 */

    S.base = cfg.base;
    S.path = cfg.path;
    S.mode = cfg.mode;
    S.token = cfg.token;
    S.autoRun = cfg.autoRun;
    S.source = cfg.source || "";
    S.force = !!(opts && opts.force);
    if (S.force) {
      S.runReady = false;
      S.runId = "";
    }
    S.running = true;
    S.backoff = 0;
    S.sig = "";
    S.onPayload = opts && opts.onPayload ? opts.onPayload : null;
    S.onStatus = opts && opts.onStatus ? opts.onStatus : null;

    if (S.force && S.mode === "live" && S.source === "probe2c") {
      S.sig = "";
      emit("log", "回放 probe 2c 真实 Pi 记录");
      poll();
    } else if (S.runReady && S.runId) {
      S.path = DEF_PATH + "?probe_run_id=" + encodeURIComponent(S.runId);
      poll();
    } else if (S.runId && S.autoRun) {
      /* 重新进入 S6：附着到已经启动的 run，不再触发第二次。 */
      pollRun();
    } else if (S.autoRun || S.force) {
      triggerRun(S.force);
    } else {
      poll();
    }
    return true;
  }

  function stop() {
    stopTimer();
    stopReplay();
    if (S.runTimer) {
      root.clearTimeout(S.runTimer);
      S.runTimer = 0;
    }
    if (S.xhr) {
      try { S.xhr.abort(); } catch (err) { /* 忽略 */ }
      S.xhr = null;
    }
    S.base = "";
    S.path = "";
    S.mode = "";
    S.token = "";
    S.running = false;
    S.backoff = 0;
    /* 清掉签名：下次进屏即使数据没变也重新交付一次（离屏期间 UI 已重置） */
    S.sig = "";
  }

  root.MOLT_LIVE = {
    resolve: resolve,
    check: check,
    normalize: normalize,
    parse: normalize,
    start: start,
    stop: stop,
    isOn: function () { return S.running; }
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
