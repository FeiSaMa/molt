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
    var port = trim(query("port"));
    var token = trim(query("token")) || trim(root.MOLT_AGENT_TOKEN);

    if (!base && /^\d{1,5}$/.test(port)) {
      base = "http" + "://127.0.0.1:" + port;
    }

    if (base) {
      return {
        mode: "live",
        base: stripSlash(base),
        path: path || DEF_PATH,
        token: token,
        interval: DEF_INTERVAL,
        timeout: DEF_TIMEOUT
      };
    }

    return {
      mode: "idle",
      base: "",
      path: "",
      token: "",
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
    if (typeof S.onPayload === "function") {
      try {
        S.onPayload(norm, payload);
      } catch (err) { /* 回调异常不打断轮询 */ }
    }
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
    S.running = true;
    S.backoff = 0;
    S.sig = "";
    S.onPayload = opts && opts.onPayload ? opts.onPayload : null;
    S.onStatus = opts && opts.onStatus ? opts.onStatus : null;

    poll();
    return true;
  }

  function stop() {
    stopTimer();
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
