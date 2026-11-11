/*!
 * molt · web/page/render.js —— 渲染层（P0 四屏）
 *
 * 定位：只吃 MOLT_MODEL.build() 的输出，只写 DOM。
 *   - 所有文本走 textContent / createElement；数据按不可信输入处理，绝不拼 HTML 字符串。
 *   - 不在本文件里出现任何"业务数字"常量：数字全部来自 model 输出或 fmt 格式化。
 *   - 缺值一律写 "—"（DASH），不猜、不补 0。
 *
 * 契约：
 *   MOLT_RENDER.render(view, { doc, model })
 *     → { ok, filled, dashes, counts:{pairs,chain,lineage,archives,snapshots}, errors[] }
 *   model 可选；缺省时自己调 window.MOLT_MODEL.build(view)（app.js 会传，省一次重复构建）。
 *
 * 零网络 / 零存储 / 零定时器 / 零 Date：时间只从数据里的 ISO 串来。
 */
(function (root, factory) {
  "use strict";
  var api = factory(root);
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.MOLT_RENDER = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  var DASH = "—";

  /* 门禁链状态文案：通过 / 停在此步 / 未执行 / 未判定 */
  var BADGE = {
    pass: "通过",
    stop: "停在此步",
    skip: "未执行",
    unknown: "未判定"
  };

  /* ==========================================================================
   * 格式化（不依赖 model.js 是否导出 fmt，缺了就退化）
   * ========================================================================== */

  function isNum(v) {
    return typeof v === "number" && isFinite(v);
  }

  function isArr(v) {
    return Object.prototype.toString.call(v) === "[object Array]";
  }

  function asStr(v) {
    return typeof v === "string" && v !== "" ? v : DASH;
  }

  function asInt(v) {
    return isNum(v) ? String(Math.round(v)) : DASH;
  }

  function asBool(v) {
    if (v === true) return "true";
    if (v === false) return "false";
    return DASH;
  }

  /* model.fmt 是权威实现；这里只做"拿不到时的最小兜底"，不改变语义 */
  var LOCAL_FMT = {
    usd: function (n, d) {
      if (typeof n === "string") n = Number(n);
      if (!isNum(n)) return DASH;
      var digits = isNum(d) ? Math.floor(d) : 6;
      return n.toFixed(digits);
    },
    pct: function (n) {
      if (!isNum(n)) return DASH;
      var s = n.toFixed(1);
      return s === "-0.0" ? "0.0" : s;
    },
    ratio: function (n) {
      return isNum(n) ? n.toFixed(2) : DASH;
    },
    sec: function (v) {
      if (!isNum(v)) return DASH;
      return (Math.abs(v) >= 1000 ? v / 1000 : v).toFixed(1);
    },
    iso: function (s) {
      if (typeof s !== "string") return DASH;
      var m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(s);
      return m ? m[1] + " " + m[2] + " UTC" : DASH;
    },
    shortId: function (s) {
      if (typeof s !== "string") return DASH;
      var t = s.replace(/^sha256:/, "").slice(0, 8);
      return t === "" ? DASH : t;
    }
  };

  function fmtOf(m) {
    if (m && m.fmt && typeof m.fmt.usd === "function") return m.fmt;
    if (root && root.MOLT_MODEL && root.MOLT_MODEL.fmt) return root.MOLT_MODEL.fmt;
    return LOCAL_FMT;
  }

  /* ==========================================================================
   * 绑定表：扁平槽位名 → { path, f } 或 { get, f }
   *   path 走 model 输出的嵌套结构（s1.scan.claim.baseCostUsd 这类），
   *   get 用于「一段文案要同时看两个分支」的少数槽位（拿到整个 model）。
   *   槽位名保持 HTML 里的扁平别名，两边解耦、互不改名。
   * ========================================================================== */

  /* 通过题数差 → 一句话。0 之外必须写清方向，不能永远说「不变」 */
  function solvedNote(v) {
    if (!isNum(v)) return DASH;
    var n = Math.round(v);
    if (n === 0) return "成功率不变";
    if (n > 0) return "通过数 +" + n;
    return "通过数 −" + Math.abs(n);
  }

  /* S2 导语：只有「每题都不掉分、每题都更便宜」才说原文，否则交出具体数字 */
  function s2LedeNote(m) {
    var pairs = m && m.s2 && m.s2.pairs ? m.s2.pairs : [];
    var allFlat = pairs.length > 0;
    var allCheaper = pairs.length > 0;

    for (var i = 0; i < pairs.length; i++) {
      var p = pairs[i] || {};
      if (p.passedDelta !== 0) allFlat = false;
      if (!(isNum(p.costDelta) && p.costDelta < 0)) allCheaper = false;
    }

    if (allFlat && allCheaper) return "通过数一道没掉，成本每题都更低。";

    var s1 = m && m.s1 ? m.s1 : {};
    var a = isNum(s1.solvedBase) ? String(Math.round(s1.solvedBase)) : DASH;
    var b = isNum(s1.solvedCand) ? String(Math.round(s1.solvedCand)) : DASH;
    return "通过数：基线 " + a + " → 候选 " + b + "；成本差：见下表。";
  }

  var BINDINGS = {
    /* --- header --- */
    "meta.scenarioVersion": { path: "meta.scenarioVersion", f: asStr },
    "meta.shellId": { path: "meta.shellId", f: asStr },
    "meta.searchTaskCount": { path: "meta.searchTaskCount", f: asInt },
    "provenance.live": { path: "provenance.live", f: asInt },
    "provenance.replay": { path: "provenance.replay", f: asInt },
    "provenance.fixture": { path: "provenance.fixture", f: asInt },
    "provenance.note": { path: "provenance.note", f: asStr },

    /* --- S1 四张 KPI 卡 --- */
    "s1.solvedBase": { path: "s1.solvedBase", f: asInt },
    "s1.solvedCand": { path: "s1.solvedCand", f: asInt },
    "s1.solvedNote": { path: "s1.solvedDelta", f: function (v) { return solvedNote(v); } },
    "s1.cpsBase": { path: "s1.cpsBase", f: function (v, F) { return F.usd(v); } },
    "s1.cpsCand": { path: "s1.cpsCand", f: function (v, F) { return F.usd(v); } },
    /* 带符号字符串（−70.0% / +3.2%）：负号由 model 给，HTML 里不再写字面负号 */
    "s1.cpsDropText": { path: "s1.cpsDropText", f: asStr },
    "s1.verdict": { path: "s1.verdict", f: asStr },
    "s1.reason": { path: "s1.reason", f: asStr },

    /* 扫描级口径：一律取 s1.scan.display（ADR 形态给 ADR 常量，现场形态给 search_results），
       绝不直连 s1.scan.recomputed —— 那是 gate 级配对的候选合计。 */
    "s1.scanBaseCost": {
      path: "s1.scan.display.baseCostUsd",
      f: function (v, F) { return F.usd(v); }
    },
    "s1.scanCandCost": {
      path: "s1.scan.display.candCostUsd",
      f: function (v, F) { return F.usd(v); }
    },
    "s1.scanCostDropPct": { path: "s1.scan.display.costDropText", f: asStr },
    "s1.scanBaseWall": {
      path: "s1.scan.display.baseWallMs",
      f: function (v, F) { return F.sec(v); }
    },
    "s1.scanCandWall": {
      path: "s1.scan.display.candWallMs",
      f: function (v, F) { return F.sec(v); }
    },
    "s1.scanWallDropPct": { path: "s1.scan.display.wallDropText", f: asStr },

    /* --- S2 配对表导语 + 脚注 --- */
    "s2.ledeNote": { get: function (m) { return s2LedeNote(m); }, f: asStr },
    "s2.invalidBase": { path: "s2.invalidBase", f: function (v, F) { return F.ratio(v); } },
    "s2.invalidCand": { path: "s2.invalidCand", f: function (v, F) { return F.ratio(v); } },
    "s2.invalidMax": { path: "s2.invalidMax", f: function (v, F) { return F.ratio(v); } },
    "s2.k": { path: "s2.k", f: asInt },

    /* --- S3 事实块 --- */
    "s3.limitsIdentical": { path: "s3.limitsIdentical", f: asBool },
    "s3.budgetCheck": { path: "s3.budgetCheck", f: asStr },
    "s3.verdict": { path: "s3.verdict", f: asStr },
    "s3.reason": { path: "s3.reason", f: asStr },

    /* --- B5 留出集（仅聚合） --- */
    "holdout.baseSolved": { path: "holdout.baseSolved", f: asInt },
    "holdout.baseTotal": { path: "holdout.baseTotal", f: asInt },
    "holdout.candSolved": { path: "holdout.candSolved", f: asInt },
    "holdout.candTotal": { path: "holdout.candTotal", f: asInt },

    /* --- footer --- */
    "meta.revision": { path: "meta.revision", f: asStr },
    "meta.generatedAt": { path: "meta.generatedAt", f: function (v, F) { return F.iso(v); } }
  };

  /* ==========================================================================
   * DOM 小工具
   * ========================================================================== */

  function el(doc, tag, cls, text) {
    var node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  /* 数一遍当前 DOM 里还挂着的槽位（渲染后 / revision 子句摘除后） */
  function countSlots(doc) {
    var nodes = doc.querySelectorAll("[data-bind]");
    var filled = 0;
    var dashes = 0;
    for (var i = 0; i < nodes.length; i++) {
      var t = nodes[i].textContent;
      var text = t === null || t === undefined ? "" : String(t);
      if (text.replace(/\s+/g, "") === DASH) dashes += 1;
      else filled += 1;
    }
    return { total: nodes.length, filled: filled, dashes: dashes };
  }

  /* 按 "a.b.c" 取值；中途缺失返回 null（不抛错、不兜默认数字） */
  function pick(obj, path) {
    var parts = String(path).split(".");
    var node = obj;
    for (var i = 0; i < parts.length; i++) {
      if (node === null || node === undefined || typeof node !== "object") return null;
      node = node[parts[i]];
    }
    return node === undefined ? null : node;
  }

  /* ==========================================================================
   * 1. data-bind 槽位（数量由 index.html 决定，新增槽位必须在 BINDINGS 登记）
   * ========================================================================== */

  function writeBindings(doc, m) {
    var F = fmtOf(m);
    var nodes = doc.querySelectorAll("[data-bind]");
    var filled = 0;
    var dashes = 0;
    var unknown = [];

    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var key = node.getAttribute("data-bind");
      var spec = BINDINGS[key];
      var text = DASH;

      if (!spec) {
        unknown.push(key);
      } else {
        var raw = typeof spec.get === "function" ? spec.get(m) : pick(m, spec.path);
        if (raw === null || raw === undefined) {
          text = DASH;
        } else if (typeof raw === "number" && !isFinite(raw)) {
          text = DASH;
        } else {
          text = String(spec.f(raw, F));
        }
      }

      node.textContent = text;
      if (text === DASH) dashes += 1;
      else filled += 1;
    }

    return { filled: filled, dashes: dashes, unknown: unknown, total: nodes.length };
  }

  /* ==========================================================================
   * 2. #verify-note —— 只写定性句，不写候选侧任何金额与秒数
   *    S1 同段落里只允许存在一组扫描级数字（KPI3），否则评委一除就自相矛盾。
   *    基线侧数字从 s1.scan.recomputed 取，经 fmt 格式化，不写字面量。
   * ========================================================================== */

  function verifyNote(m) {
    var scan = m && m.s1 && m.s1.scan ? m.s1.scan : null;
    if (!scan || !scan.verified) return "";
    var F = fmtOf(m);
    var rc = scan.recomputed || {};
    var v = scan.verified;
    var parts = [];

    /* 现场形态（两条 live 扫描）：两侧都是 result 现算，直接说清口径与数字 */
    if (scan.source === "data") {
      var d = scan.display || {};
      var n = m.meta && isNum(m.meta.searchTaskCount) ? m.meta.searchTaskCount : null;
      return "两侧均由 search_results 复算一致：$" + F.usd(d.baseCostUsd) +
        " → $" + F.usd(d.candCostUsd) + "，" + F.sec(d.baseWallMs) +
        "s → " + F.sec(d.candWallMs) + "s（两条完整" +
        (isNum(n) ? " " + n + " " : " ") + "题扫描）。";
    }

    if (v.baseCost === true && v.baseWall === true) {
      parts.push(
        "基线侧 $" + F.usd(rc.baseCostUsd) + " / " + F.sec(rc.baseWallMs) + "s 已由 runs 独立复算一致"
      );
    } else if (v.baseCost === false || v.baseWall === false) {
      parts.push(
        "基线侧与扫描级口径不一致（runs 复算 $" + F.usd(rc.baseCostUsd) +
        " / " + F.sec(rc.baseWallMs) + "s）"
      );
    }

    if (v.candCost === false || v.candWall === false) {
      parts.push("候选侧扫描级数字引自 ADR-0008，未随本数据面导出");
    } else if (v.candCost === true && v.candWall === true) {
      /* 即使可复算也不在这里再报一遍金额：首屏只留 KPI3 那一组扫描级数字 */
      parts.push("候选侧同批数据可复算，与扫描级口径一致");
    }

    return parts.length === 0 ? "" : parts.join("；") + "。";
  }

  /* ==========================================================================
   * 2a. KPI3 的 label / note 尾巴 + #scan-note 的口径依据
   *     静态兜底文本留在 index.html 里（不许写死任何数字），这里按 source 覆写。
   * ========================================================================== */

  function writeScanScopeText(doc, m) {
    var scan = m && m.s1 && m.s1.scan ? m.s1.scan : null;
    if (!scan) return;

    var n = m.meta && isNum(m.meta.searchTaskCount) ? m.meta.searchTaskCount : null;
    var countText = isNum(n) ? String(n) + " 题" : "全部题";
    var isData = scan.source === "data";

    var label = doc.getElementById("kpi3-label");
    if (label) {
      label.textContent = isData
        ? "执行成本（扫描级 · 两条 live 扫描）"
        : "执行成本（扫描级 · 引用 ADR-0008）";
    }

    var tail = doc.getElementById("kpi3-note-tail");
    if (tail) {
      tail.textContent = isData
        ? "两条 " + countText + "扫描复算"
        : "候选侧未随数据面导出";
    }

    var basis = doc.getElementById("scan-note-basis");
    if (basis) {
      basis.textContent = isData
        ? "两侧都由 search_results 复算，未引用 ADR 常量"
        : "引自 ADR-0008，候选侧未随本数据面导出（基线侧已由 runs 复算一致）";
    }

    /* 「不要对逐 run 求和」的理由随形态变：回放形态是 closure/scan10 重复同一题，
       现场形态是同题在两条扫描里各有一条 run。 */
    var sumNote = doc.getElementById("sum-note-tail");
    if (sumNote) {
      sumNote.textContent = isData
        ? "同一道题在两条扫描里各有一条 run，直接求和会把两次扫描混在一起。"
        : "closure 与 scan10 各含同一条 smoke-add，会重复计算。";
    }
  }

  /* ==========================================================================
   * 2b. S2 脚注末尾：候选侧 = 9 条 replay + 1 条 closure live，属 gate 级口径
   *     条数取自 s1.scan.counts，金额取自 s1.scan.recomputed，题目名取自 live 配对行。
   * ========================================================================== */

  function pairScopeNote(m) {
    var scan = m && m.s1 && m.s1.scan ? m.s1.scan : null;
    if (!scan) return "";

    /* 现场形态：两条扫描都是 live，没有 replay/fixture 混合，不需要 gate/scan 拆分说明 */
    if (scan.source === "data") {
      return "两次扫描都是 live 运行，逐题配对直接来自 gate 决策；本形态没有 replay/fixture 混合。";
    }

    if (!scan.counts || !scan.recomputed) return "";
    var F = fmtOf(m);
    var c = scan.counts;
    var rc = scan.recomputed;

    if (c.candReplay + c.candLive <= 0) return "";

    var liveIds = [];
    var pairs = m.s2 && m.s2.pairs ? m.s2.pairs : [];
    for (var i = 0; i < pairs.length; i++) {
      if (pairs[i] && pairs[i].live === true) liveIds.push(asStr(pairs[i].taskId));
    }
    if (liveIds.length === 0 && isArr(c.candLiveTaskIds)) liveIds = c.candLiveTaskIds.slice(0, c.candLive);

    var liveN = isNum(c.candLive) ? c.candLive : liveIds.length;
    var tail = liveIds.length > 0 ? "（题目 " + liveIds.join(" / ") + "）" : "";

    return "逐题配对含 " + c.candReplay + " 条 replay + " + liveN + " 条 closure live" + tail +
      "→ 这是 gate 级口径；成本合计 $" + F.usd(rc.candPairCostUsd) +
      " 只用于门禁配对，不等于扫描级。";
  }

  /* ==========================================================================
   * 3. #pairs-body —— 7 列逐题配对
   * ========================================================================== */

  function signedUsd(v, F) {
    if (!isNum(v)) return DASH;
    var head = v < 0 ? "−" : (v > 0 ? "+" : "");
    return head + F.usd(Math.abs(v));
  }

  function signedInt(v) {
    if (!isNum(v)) return DASH;
    var n = Math.round(v);
    if (n < 0) return "−" + Math.abs(n);
    if (n > 0) return "+" + n;
    return "0";
  }

  function costBar(doc, ratio, F) {
    var wrap = el(doc, "span", "bar-wrap");
    var track = el(doc, "span", "bar");
    var fill = el(doc, "span", "bar-fill");
    var width = 0;
    var worse = isNum(ratio) && ratio > 1;

    if (isNum(ratio)) {
      width = Math.max(1, Math.min(100, Math.round(ratio * 100)));
      track.title = "候选 / 基线 = " + F.pct(ratio * 100) + "%";
    } else {
      track.title = "候选 / 基线 = " + DASH;
    }

    if (worse) fill.className = "bar-fill is-worse";
    fill.style.width = width + "%";
    track.appendChild(fill);
    wrap.appendChild(track);

    /* 比例 > 1：宽度已夹到 100%，再补一个右侧溢出标记，避免「满格绿」被读成更便宜 */
    if (worse) {
      var over = el(doc, "span", "bar-over", "▸");
      over.title = "候选比基线贵 " + F.pct((ratio - 1) * 100) + "%";
      wrap.appendChild(over);
    }

    return wrap;
  }

  function renderPairs(doc, m) {
    var body = doc.getElementById("pairs-body");
    if (!body) return 0;

    var F = fmtOf(m);
    var pairs = m.s2 && m.s2.pairs ? m.s2.pairs : [];
    clear(body);

    for (var i = 0; i < pairs.length; i++) {
      var p = pairs[i] || {};
      var tr = doc.createElement("tr");

      tr.appendChild(el(doc, "td", "num", asInt(p.index)));

      var idCell = el(doc, "td", "pair-id-cell");
      idCell.appendChild(el(doc, "span", null, asStr(p.taskId)));
      if (p.live === true) idCell.appendChild(el(doc, "span", "tag tag-live", "live"));
      tr.appendChild(idCell);

      var c1 = el(doc, "td", "num", "$" + F.usd(p.baseCost));
      var c2 = el(doc, "td", "num", "$" + F.usd(p.candCost));
      tr.appendChild(c1);
      tr.appendChild(c2);

      var delta = el(doc, "td", "num delta", signedUsd(p.costDelta, F));
      if (isNum(p.costDelta) && p.costDelta < 0) delta.className = "num delta is-better";
      tr.appendChild(delta);

      tr.appendChild(el(doc, "td", "num", signedInt(p.passedDelta)));

      var barCell = el(doc, "td", "pair-bar");
      barCell.appendChild(costBar(doc, p.ratio, F));
      /* 成本条的视觉差异（宽度 / 颜色）对读屏不可见，补一行文字等价物 */
      barCell.appendChild(el(doc, "span", "sr-only",
        "基线 $" + F.usd(p.baseCost) + "，候选 $" + F.usd(p.candCost) +
        "，差 " + signedUsd(p.costDelta, F)));
      tr.appendChild(barCell);

      body.appendChild(tr);
    }

    renderPairsTotal(doc, m, F);

    return pairs.length;
  }

  /* 表格合计行（<tfoot>）：gate 级逐题求和，明确不等于扫描级 */
  function renderPairsTotal(doc, m, F) {
    var foot = doc.getElementById("pairs-foot");
    if (!foot) return 0;
    clear(foot);

    var t = m.s2 && m.s2.totals ? m.s2.totals : null;
    var pairs = m.s2 && m.s2.pairs ? m.s2.pairs : [];
    var passSum = 0;
    var passOk = pairs.length > 0;
    for (var i = 0; i < pairs.length; i++) {
      if (!isNum(pairs[i].passedDelta)) { passOk = false; break; }
      passSum += pairs[i].passedDelta;
    }

    var row = el(doc, "tr", "pairs-total");
    row.appendChild(el(doc, "td", "num total-label", "合计"));
    row.appendChild(el(doc, "td", "total-note", "gate 级配对合计，不等于扫描级"));
    row.appendChild(el(doc, "td", "num", t && isNum(t.base) ? "$" + F.usd(t.base) : DASH));
    row.appendChild(el(doc, "td", "num", t && isNum(t.cand) ? "$" + F.usd(t.cand) : DASH));
    row.appendChild(el(doc, "td", "num total-delta", t && isNum(t.delta) ? signedUsd(t.delta, F) : DASH));
    row.appendChild(el(doc, "td", "num", passOk ? signedInt(passSum) : DASH));
    row.appendChild(el(doc, "td", "pair-bar", ""));

    foot.appendChild(row);
    return 1;
  }

  /* ==========================================================================
   * 4. #gate-chain —— 6 步；序号由 app.css 的 counter 提供（避免重复编号）
   *    passed === false → 停在当前步，其后一律"未执行"
   * ========================================================================== */

  function renderChain(doc, m) {
    var list = doc.getElementById("gate-chain");
    if (!list) return 0;

    var chain = m.s3 && m.s3.chain ? m.s3.chain : [];
    clear(list);

    var stopped = false;
    var allPassed = true;
    var i;

    for (i = 0; i < chain.length; i++) {
      if (chain[i] && chain[i].passed !== true) allPassed = false;
    }

    for (i = 0; i < chain.length; i++) {
      var item = chain[i] || {};
      var li = doc.createElement("li");
      var status;

      if (stopped) {
        status = "skip";
      } else if (item.passed === true) {
        status = "pass";
      } else if (item.passed === false) {
        status = "stop";
        stopped = true;
      } else {
        status = "unknown";
      }

      li.className = "step";
      if (status === "stop") li.className = "step is-stop";
      if (status === "skip") li.className = "step is-skip";
      if (status === "pass" && allPassed && i === chain.length - 1) li.className = "step is-final";

      var head = el(doc, "div", "step-head");
      head.appendChild(el(doc, "span", "step-label", asStr(item.label)));
      head.appendChild(el(doc, "span", "step-badge is-" + status, BADGE[status]));
      li.appendChild(head);

      var body = el(doc, "div", "step-body");
      body.appendChild(el(doc, "span", "step-obs", asStr(item.observed)));
      if (item.threshold !== null && item.threshold !== undefined && item.threshold !== DASH) {
        body.appendChild(el(doc, "span", "step-th", asStr(item.threshold)));
      }
      li.appendChild(body);

      list.appendChild(li);
    }

    return chain.length;
  }

  /* ==========================================================================
   * 5. #lineage —— 每个 genome 一行（带三个开关 chip）
   * ========================================================================== */

  function chip(doc, key, value) {
    var c = el(doc, "span", "chip");
    c.appendChild(el(doc, "em", null, key));
    c.appendChild(el(doc, "b", null, asStr(value)));
    return c;
  }

  function genText(g) {
    return isNum(g) ? "第 " + Math.round(g) + " 代" : "未采纳";
  }

  /* 谱系行标签：基线是起点，不是「已采纳」；未采纳的候选不写代数 */
  function lineageTag(r) {
    var g = r ? r.generation : null;
    if (r && r.adopted === false) return "未采纳";
    if (isNum(g) && Math.round(g) === 1) return "起点 · 第 1 代";
    if (isNum(g) && Math.round(g) >= 2 && r && r.adopted === true) {
      return "已采纳 · 第 " + Math.round(g) + " 代";
    }
    return genText(g);
  }

  function renderLineage(doc, m) {
    var list = doc.getElementById("lineage");
    if (!list) return 0;

    var rows = m.b5 && m.b5.lineage ? m.b5.lineage : [];
    clear(list);

    for (var i = 0; i < rows.length; i++) {
      var r = rows[i] || {};
      var li = doc.createElement("li");

      var head = el(doc, "div", "lin-head");
      head.appendChild(el(doc, "span", "lin-id", asStr(r.short)));
      /* 父子关系：genome ← parent（没有父就不画箭头，不写空箭头） */
      if (typeof r.parentShort === "string" && r.parentShort !== "") {
        head.appendChild(el(doc, "span", "lineage-parent", "← " + r.parentShort));
      }
      head.appendChild(el(doc, "span", "tag" + (r.adopted ? " is-ok" : ""), lineageTag(r)));
      li.appendChild(head);

      var chips = el(doc, "div", "lin-chips");
      var sw = r.switches || {};
      chips.appendChild(chip(doc, "thinking", sw.thinking));
      chips.appendChild(chip(doc, "context", sw.context));
      chips.appendChild(chip(doc, "tools", sw.tools));
      li.appendChild(chips);

      var metaRow = el(doc, "div", "row-meta", "runtime " + asStr(r.runtimeId));
      metaRow.appendChild(el(doc, "span", "muted", "（runtime 声明，不代表本页证据的 provenance）"));
      metaRow.appendChild(el(doc, "span", "muted", " · " + genText(r.generation)));
      li.appendChild(metaRow);

      list.appendChild(li);
    }

    return rows.length;
  }

  /* ==========================================================================
   * 6. #archives / #snapshots
   * ========================================================================== */

  function renderArchives(doc, m) {
    var list = doc.getElementById("archives");
    if (!list) return 0;

    var rows = m.b5 && m.b5.archives ? m.b5.archives : [];
    clear(list);

    for (var i = 0; i < rows.length; i++) {
      var a = rows[i] || {};
      var li = doc.createElement("li");

      var head = el(doc, "div", "row-head");
      head.appendChild(el(doc, "span", "row-id", asStr(a.archiveId)));
      head.appendChild(el(doc, "span", "tag", genText(a.generation)));
      li.appendChild(head);

      li.appendChild(el(doc, "div", "row-meta", "snapshot " + asStr(a.snapshotId)));
      if (typeof a.note === "string" && a.note !== "") {
        li.appendChild(el(doc, "div", "row-meta", a.note));
      }

      list.appendChild(li);
    }

    return rows.length;
  }

  function renderSnapshots(doc, m) {
    var list = doc.getElementById("snapshots");
    if (!list) return 0;

    var rows = m.b5 && m.b5.snapshots ? m.b5.snapshots : [];
    clear(list);

    for (var i = 0; i < rows.length; i++) {
      var s = rows[i] || {};
      var li = doc.createElement("li");

      var head = el(doc, "div", "row-head");
      var hash = el(doc, "span", "row-id", asStr(s.treeHashShort));
      if (typeof s.treeHash === "string" && s.treeHash !== "") hash.title = s.treeHash;
      head.appendChild(hash);
      head.appendChild(el(doc, "span", "tag", genText(s.generation)));
      head.appendChild(el(doc, "span", "tag", isNum(s.fileCount) ? s.fileCount + " 文件" : DASH));
      li.appendChild(head);

      var roots = s.roots && s.roots.length ? s.roots.join(" · ") : DASH;
      li.appendChild(el(doc, "div", "row-meta", roots));

      list.appendChild(li);
    }

    return rows.length;
  }

  /* 7b. #holdout-attribution —— 留出集候选侧是哪个 genome、switches 是否与门禁候选一致 */
  function renderHoldoutAttribution(doc, m) {
    var node = doc.getElementById("holdout-attribution");
    if (!node) return 0;
    var att = m.holdout && m.holdout.attribution ? m.holdout.attribution : null;
    if (!att || !m.holdout.available) {
      node.textContent = "";
      return 0;
    }
    node.textContent = asStr(att.note);
    return 1;
  }

  /* ==========================================================================
   * 7. 脚注区：错误 / 口径说明（<details>，全 textContent）
   * ========================================================================== */

  function detailBlock(doc, title, items, cls, open) {
    var box = doc.createElement("details");
    box.className = cls;
    if (open) box.open = true;

    var sum = doc.createElement("summary");
    sum.textContent = title;
    box.appendChild(sum);

    var ul = doc.createElement("ul");
    for (var i = 0; i < items.length; i++) {
      var li = doc.createElement("li");
      li.textContent = String(items[i]);
      ul.appendChild(li);
    }
    box.appendChild(ul);

    return box;
  }

  function mountNotes(doc, m) {
    var host = doc.querySelector(".colophon");
    if (!host) return;

    var old = doc.getElementById("w03-notes");
    if (old && old.parentNode) old.parentNode.removeChild(old);

    var errs = m && m.errors ? m.errors : [];
    var warns = m && m.warnings ? m.warnings : [];
    if (errs.length === 0 && warns.length === 0) return;

    var box = doc.createElement("div");
    box.id = "w03-notes";

    if (errs.length > 0) {
      box.appendChild(detailBlock(doc, "数据错误（" + errs.length + "）", errs, "warnings is-error", true));
    }
    if (warns.length > 0) {
      box.appendChild(detailBlock(doc, "口径说明（" + warns.length + "）", warns, "warnings", false));
    }

    host.appendChild(box);
  }

  /* ==========================================================================
   * render 主入口
   * ========================================================================== */

  function render(view, opts) {
    var o = opts || {};
    var doc = o.doc || (root && root.document);
    var out = {
      ok: false,
      filled: 0,
      dashes: 0,
      total: 0,
      counts: { pairs: 0, chain: 0, lineage: 0, archives: 0, snapshots: 0 },
      errors: []
    };

    if (!doc) {
      out.errors.push("缺少 document");
      return out;
    }

    var m = o.model;
    if (!m) {
      if (!root || !root.MOLT_MODEL || typeof root.MOLT_MODEL.build !== "function") {
        out.errors.push("model.js 未加载");
        return out;
      }
      m = root.MOLT_MODEL.build(view);
    }

    var errors = m && m.errors ? m.errors.slice() : [];
    var usable = !!(m && m.ok === true && m.s1 && m.s1.available === true);

    if (usable) {
      var w = writeBindings(doc, m);
      out.filled = w.filled;
      out.dashes = w.dashes;
      out.total = w.total;
      if (w.unknown.length > 0) {
        errors.push("未登记的绑定槽位：" + w.unknown.join(", "));
      }

      writeScanScopeText(doc, m);

      var note = doc.getElementById("verify-note");
      if (note) note.textContent = verifyNote(m);

      var scope = doc.getElementById("pair-scope-note");
      if (scope) scope.textContent = pairScopeNote(m);

      /* revision 为 null 时整段子句从 DOM 摘掉，footer 只留「证据截至 …」 */
      var revClause = doc.getElementById("revision-clause");
      if (revClause) {
        var hasRev = !!(m.meta && typeof m.meta.revision === "string" && m.meta.revision !== "");
        if (hasRev) {
          revClause.hidden = false;
        } else if (revClause.parentNode) {
          revClause.parentNode.removeChild(revClause);
        }
      }

      out.counts.pairs = renderPairs(doc, m);
      out.counts.chain = renderChain(doc, m);
      out.counts.lineage = renderLineage(doc, m);
      out.counts.archives = renderArchives(doc, m);
      out.counts.snapshots = renderSnapshots(doc, m);
      renderHoldoutAttribution(doc, m);

      /* 摘除 revision 子句之后再数一遍：断言用「渲染后存活槽位」，
         这样 revision 被摘掉（filled 少 1）或被填上都成立，但一个 — 都不许留。 */
      var live = countSlots(doc);
      out.domTotal = live.total;
      out.domFilled = live.filled;
      out.domDashes = live.dashes;
    } else {
      /* 数据不可用：所有槽位保持 "—"，只显示错误 / 警告，不出现任何猜测数字 */
      var slots = doc.querySelectorAll("[data-bind]");
      for (var i = 0; i < slots.length; i++) slots[i].textContent = DASH;
      var vn = doc.getElementById("verify-note");
      if (vn) vn.textContent = "";
      var ps = doc.getElementById("pair-scope-note");
      if (ps) ps.textContent = "";
      var ha = doc.getElementById("holdout-attribution");
      if (ha) ha.textContent = "";
      out.total = slots.length;
      out.dashes = slots.length;
      out.domTotal = slots.length;
      out.domFilled = 0;
      out.domDashes = slots.length;
      if (errors.length === 0) errors.push("数据不完整：缺少门禁判定或搜索结果");
    }

    mountNotes(doc, m);

    out.errors = errors;
    out.ok = usable && errors.length === 0;
    return out;
  }

  return {
    render: render,
    BINDINGS: BINDINGS,
    DASH: DASH
  };
});
