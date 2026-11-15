/*!
 * molt · web/page/mock-source.js —— S6 本地模拟数据源（演示用，生产可删）
 *
 * 触发条件：URL 带 ?path=<file>，必须显式给文件；没有默认样例。
 *   ?path=data/results.json     → 相对页面目录（找不到再试上一级）
 *   ?path=/abs/results.json     → 绝对路径
 * 文件格式与 /api/tasks 相同：{ "results": [ {id, cost_first, passed_first,
 * cost_second, passed_second} ] }。
 *
 * 注意：file:// 下浏览器禁止 XHR 读本地 JSON；本地模拟需要 HTTP 服务
 * （例如在仓库根目录 python3 -m http.server）。file:// 会保持空载并提示一次。
 *
 * 行为：读一份 { "results": [ {id, cost_first, passed_first, cost_second,
 * passed_second} ] }（与 /api/tasks 同形状），按固定顺序模拟两轮完成：
 *   - 第一轮逐题出现（只带 cost_first / passed_first），第二次先挂「—」；
 *   - 第一轮全部完成后隔一拍，第二轮逐题回填 cost_second / passed_second；
 *   - 每次只把「当前累计已完成」的行交给 onPayload，页面照常逐条点亮。
 *
 * 删除方式（真实上线时）：
 *   1) 删掉本文件；
 *   2) 删掉 index.html 里 mock-source.js 的 <script>。
 * 页面其余部分只认 onPayload 的 { pairs }，不依赖本文件。
 *
 * 约定：ES5、零依赖、只用 XHR。
 */
(function (root) {
  "use strict";

  var FIRST_MS = 120;
  var STEP_MS = 620;
  var STEP2_MS = 480;
  var GAP_MS = 1100;
  var SEED1 = 7;
  var SEED2 = 23;

  var S = {
    running: false,
    timers: [],
    onPayload: null,
    onStatus: null,
    path: "",
    warned: false
  };

  function str(v) {
    return v === null || v === undefined ? "" : String(v);
  }

  function safeDecode(v) {
    try {
      return decodeURIComponent(str(v));
    } catch (err) {
      return str(v);
    }
  }

  function hasParam(name) {
    var loc = root.location;
    var q = loc && loc.search ? str(loc.search) : "";
    if (!q) return false;
    return new RegExp("(?:^|[?&])" + name + "(=|&|$)").test(q);
  }

  function query(name) {
    var loc = root.location;
    var q = loc && loc.search ? str(loc.search) : "";
    if (!q) return "";
    var m = new RegExp("(?:^|[?&])" + name + "=([^&]*)").exec(q);
    return m ? safeDecode(m[1]) : "";
  }

  function resolvePath() {
    if (!hasParam("path")) return "";
    return query("path");
  }

  function enabled() {
    return resolvePath() !== "";
  }

  /* 0..n-1 的确定洗牌：同一 seed 每次演示顺序一致（不用随机数） */
  function completionOrder(n, seed) {
    var a = [];
    var i, j, t;
    for (i = 0; i < n; i++) a.push(i);
    var s = seed > 0 ? seed : 1;
    for (i = n - 1; i > 0; i--) {
      s = (s * 1103515245 + 12345) % 2147483648;
      j = s % (i + 1);
      t = a[i];
      a[i] = a[j];
      a[j] = t;
    }
    return a;
  }

  function emitStatus(kind, detail) {
    if (typeof S.onStatus !== "function") return;
    try {
      S.onStatus(kind, detail);
    } catch (err) { /* 忽略 */ }
  }

  function clearTimers() {
    for (var i = 0; i < S.timers.length; i++) root.clearTimeout(S.timers[i]);
    S.timers = [];
  }

  function reduced() {
    try {
      return !!(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (err) {
      return false;
    }
  }

  /* 用 MOLT_LIVE 的严格归一化，保证模拟与真实接口是同一份字段表。
     若 live-source 未加载（理论上不会），退化为直接读 results。 */
  function normalize(payload) {
    if (root.MOLT_LIVE && typeof root.MOLT_LIVE.normalize === "function") {
      return root.MOLT_LIVE.normalize(payload).pairs;
    }
    var out = [];
    var list = payload && payload.results ? payload.results : [];
    for (var i = 0; i < list.length; i++) {
      var r = list[i];
      if (!r || !r.id) continue;
      out.push({
        taskId: r.id,
        index: i + 1,
        baseCost: r.cost_first,
        candCost: r.cost_second,
        basePassed: r.passed_first === true,
        candPassed: r.passed_second === true
      });
    }
    return out;
  }

  /* 累计快照：还没完成的轮次不带数（null），页面会保持「—」 */
  function snapshot(pairs, baseDone, candDone) {
    var out = [];
    for (var i = 0; i < pairs.length; i++) {
      if (!baseDone[i]) continue;
      out.push({
        taskId: pairs[i].taskId,
        index: pairs[i].index,
        baseCost: pairs[i].baseCost,
        candCost: candDone[i] ? pairs[i].candCost : null,
        basePassed: pairs[i].basePassed,
        candPassed: pairs[i].candPassed
      });
    }
    return { pairs: out, count: out.length };
  }

  function deliver(norm) {
    if (typeof S.onPayload !== "function") return;
    try {
      S.onPayload(norm, null);
    } catch (err) { /* 忽略 */ }
  }

  function simulate(pairs) {
    var n = pairs.length;
    var baseDone = [];
    var candDone = [];
    var i;

    if (reduced()) {
      for (i = 0; i < n; i++) { baseDone[i] = true; candDone[i] = true; }
      deliver(snapshot(pairs, baseDone, candDone));
      S.running = false;
      return;
    }

    var order1 = completionOrder(n, SEED1);
    var order2 = completionOrder(n, SEED2);
    /* 题多时压缩节奏，40 题也不会演一分多钟 */
    var gap1 = n > 12 ? Math.max(80, Math.round(6200 / n)) : STEP_MS;
    var gap2 = n > 12 ? Math.max(60, Math.round(4800 / n)) : STEP2_MS;
    var t = FIRST_MS;

    function push(fn, ms) {
      S.timers.push(root.setTimeout(fn, ms));
    }

    function step1(idx) {
      baseDone[idx] = true;
      deliver(snapshot(pairs, baseDone, candDone));
    }

    function step2(idx) {
      candDone[idx] = true;
      deliver(snapshot(pairs, baseDone, candDone));
    }

    for (i = 0; i < n; i++) {
      (function (idx, ms) {
        push(function () { step1(idx); }, ms);
      })(order1[i], t);
      t += gap1;
    }

    t += GAP_MS;
    for (i = 0; i < n; i++) {
      (function (idx, ms) {
        push(function () { step2(idx); }, ms);
      })(order2[i], t);
      t += gap2;
    }

    /* 最后一题落地后收尾：isOn() 归 false，不再占着「运行中」 */
    push(function () { S.running = false; }, t + 40);
  }

  function accept(payload) {
    var c = root.MOLT_LIVE && root.MOLT_LIVE.check
      ? root.MOLT_LIVE.check(payload)
      : { ok: !!(payload && payload.results) };
    if (!c.ok) {
      emitStatus("contract", c.reason || "mock");
      return false;
    }
    var pairs = normalize(payload);
    if (pairs.length === 0) return false;
    simulate(pairs);
    return true;
  }

  function isFileMode() {
    return !!(root.location && root.location.protocol === "file:");
  }

  function load(cb) {
    var path = resolvePath();
    S.path = path;
    if (!path) {
      emitStatus("offline", "mock");
      S.running = false;
      return;
    }

    /* file:// 下浏览器禁止 XHR 读本地 JSON：提示一次，保持空载 */
    if (isFileMode()) {
      if (!S.warned && root.console && root.console.warn) {
        S.warned = true;
        root.console.warn("本地模拟需要 HTTP 服务：file:// 不能读取 " + path);
      }
      emitStatus("offline", "file");
      S.running = false;
      return;
    }

    /* 相对路径先按页面目录找；找不到再按上一级找（web/page/ → web/） */
    var candidates = [];
    function add(p) {
      if (p && candidates.indexOf(p) === -1) candidates.push(p);
    }
    if (path.charAt(0) === "/" || path.indexOf("://") !== -1) {
      add(path);
    } else if (path.indexOf("/") === -1) {
      /* 裸文件名：先按上一级（web/）找，减少一次 404 */
      add("../" + path);
      add(path);
    } else {
      add(path);
      add("../" + path);
    }

    var i = 0;
    function next() {
      if (i >= candidates.length) {
        emitStatus("offline", "mock");
        S.running = false;
        return;
      }
      var file = candidates[i++];
      var xhr;
      try {
        xhr = new root.XMLHttpRequest();
        xhr.open("GET", file, true);
        xhr.timeout = 4000;
        xhr.onreadystatechange = function () {
          if (!xhr || xhr.readyState !== 4) return;
          var ok = xhr.status >= 200 && xhr.status < 300;
          var payload = null;
          if (ok) {
            try { payload = JSON.parse(xhr.responseText); } catch (err) { payload = null; }
          }
          if (payload) cb(payload);
          else next();
        };
        xhr.send(null);
      } catch (err) {
        next();
      }
    }
    next();
  }

  function start(opts) {
    if (!enabled()) return false;
    stop();
    S.running = true;
    S.onPayload = opts && opts.onPayload ? opts.onPayload : null;
    S.onStatus = opts && opts.onStatus ? opts.onStatus : null;

    load(function (payload) {
      if (!S.running) return;
      if (!accept(payload)) S.running = false;
    });
    return true;
  }

  function stop() {
    clearTimers();
    S.running = false;
  }

  root.MOLT_MOCK = {
    enabled: enabled,
    path: resolvePath,
    start: start,
    stop: stop,
    isOn: function () { return S.running; }
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
