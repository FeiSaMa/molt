/*!
 * molt · web/page/render.js —— 渲染层（8 屏路演版）
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

  /* S4 合计行的通过差：逐题相加，任一行缺数就整格不给 */
  function totalPassDelta(m) {
    var pairs = m && m.s2 && isArr(m.s2.pairs) ? m.s2.pairs : [];
    var sum = 0;
    if (pairs.length === 0) return null;
    for (var i = 0; i < pairs.length; i++) {
      if (!isNum(pairs[i].passedDelta)) return null;
      sum += pairs[i].passedDelta;
    }
    return sum;
  }

  /* 金额槽位：合计行给 "$0.035560"，与逐行一致 */
  function usdCell(v, F) {
    return isNum(v) ? "$" + F.usd(v) : DASH;
  }

  function signedUsdCell(v, F) {
    return isNum(v) ? signedUsd(v, F) : DASH;
  }

  /* 通过题数差 → 一句话。0 之外必须写清方向，不能永远说「不变」 */
  function solvedNote(v) {
    if (!isNum(v)) return DASH;
    var n = Math.round(v);
    if (n === 0) return "成功率不变";
    if (n > 0) return "通过数 +" + n;
    return "通过数 −" + Math.abs(n);
  }

  var BINDINGS = {
    /* --- HUD / 封面 --- */
    "meta.searchTaskCount": { path: "meta.searchTaskCount", f: asInt },
    "provenance.live": { path: "provenance.live", f: asInt },
    "provenance.replay": { path: "provenance.replay", f: asInt },
    "provenance.fixture": { path: "provenance.fixture", f: asInt },
    "provenance.note": { path: "provenance.note", f: asStr },

    /* --- S2 / S3 主数字（通过题数整屏已删，只保留成本相关槽位） --- */
    "s1.cpsBase": { path: "s1.cpsBase", f: function (v, F) { return F.usd(v); } },
    "s1.cpsCand": { path: "s1.cpsCand", f: function (v, F) { return F.usd(v); } },
    /* 带符号字符串（−70.0% / +3.2%）：负号由 model 给，HTML 里不再写字面负号 */
    "s1.cpsDropText": { path: "s1.cpsDropText", f: asStr },
    /* 只读派生：同样的钱能跑几倍的题（1 位小数；「倍」字由 HTML 提供） */
    "s1.costMultiplier": { path: "s1.costMultiplier", f: function (v, F) { return F.pct(v); } },
    /* S8 结尾句用：只有数值，% 由 HTML 提供 */
    "s1.cpsDropPct": { path: "s1.cpsDropPct", f: function (v, F) { return F.pct(v); } },

    /* 扫描级口径：一律取 s1.scan.display（ADR 形态给 ADR 常量，现场形态给 search_results），
       绝不直连 s1.scan.recomputed —— 那是 gate 级配对的候选合计。 */
    "s1.scanBaseCost": {
      path: "s1.scan.display.baseCostUsd",
      f: function (v, F) { return "$" + F.usd(v); }
    },
    "s1.scanCandCost": {
      path: "s1.scan.display.candCostUsd",
      f: function (v, F) { return "$" + F.usd(v); }
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

    /* --- S4 配对表导语 / 合计行（静态 tfoot 里的槽位） --- */
    "s2.totalBase": {
      get: function (m) { return m && m.s2 && m.s2.totals ? m.s2.totals.base : null; },
      f: usdCell
    },
    "s2.totalCand": {
      get: function (m) { return m && m.s2 && m.s2.totals ? m.s2.totals.cand : null; },
      f: usdCell
    },
    "s2.totalDelta": {
      get: function (m) { return m && m.s2 && m.s2.totals ? m.s2.totals.delta : null; },
      f: signedUsdCell
    },
    "s2.totalPassDelta": { get: totalPassDelta, f: function (v) { return signedInt(v); } },
    "s2.invalidMax": { path: "s2.invalidMax", f: function (v, F) { return F.ratio(v); } },

    /* S4 人话句：逐题降幅的两个极值（model 已算好，这里只格式化；不重算、不猜） */

    /* --- S5 事实块 --- */
    "s3.limitsIdentical": { path: "s3.limitsIdentical", f: asBool },
    "s3.budgetCheck": { path: "s3.budgetCheck", f: asStr },
    "s3.verdict": { path: "s3.verdict", f: asStr },
    "s3.reason": { path: "s3.reason", f: asStr },

    /* --- S7 留出集（仅聚合）+ 证据 + 审计 --- */
    "holdout.baseSolved": { path: "holdout.baseSolved", f: asInt },
    "holdout.baseTotal": { path: "holdout.baseTotal", f: asInt },
    "holdout.candSolved": { path: "holdout.candSolved", f: asInt },
    "holdout.candTotal": { path: "holdout.candTotal", f: asInt },
    "audit.ok": { path: "audit.ok", f: asBool },
    "audit.runs": { path: "audit.runs", f: asInt },

    /* --- S8 元信息 --- */
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
      /* 计数动效的钩子：只有标了 .count 且目标值是整数的槽位才参与计数，
         目标值与最终文案都留在 DOM 属性里，无 JS 时 textContent 本身就是最终值。 */
      if (node.className && /\bcount\b/.test(String(node.className)) && /^\d+$/.test(text)) {
        node.setAttribute("data-count", text);
        node.setAttribute("data-count-final", text);
      }
      if (text === DASH) dashes += 1;
      else filled += 1;
    }

    return { filled: filled, dashes: dashes, unknown: unknown, total: nodes.length };
  }

  /* ==========================================================================
   * 2. #verify-note —— 只写定性句，不写候选侧任何金额与秒数
   *    S3 同段落里只允许存在一组扫描级数字，否则评委一除就自相矛盾。
   * ========================================================================== */

  function verifyNote(m) {
    var scan = m && m.s1 && m.s1.scan ? m.s1.scan : null;
    if (!scan || !scan.verified) return "";

    /* 现场形态（两条 live 扫描）：两侧都是 result 现算 → 直接说清 */
    if (scan.source === "data") {
      return "两侧均由 search_results 复算一致。";
    }

    /* 回放形态：基线侧可由 runs 复算，候选侧只能引用 ADR-0008 */
    var v = scan.verified;
    if (v.baseCost === true && v.baseWall === true) {
      return "基线侧已由 runs 复算一致；候选侧为引用值，未随本数据面导出。";
    }
    if (v.baseCost === false || v.baseWall === false) {
      return "基线侧与扫描级口径不一致，需人工核对；候选侧为引用值。";
    }
    return "候选侧为引用值，未随本数据面导出。";
  }

  /* ==========================================================================
   * 2a. #scan-note-basis —— 只写口径来源，不重复任何金额
   * ========================================================================== */

  function writeScanScopeText(doc, m) {
    var scan = m && m.s1 && m.s1.scan ? m.s1.scan : null;
    if (!scan) return;
    var isData = scan.source === "data";

    var basis = doc.getElementById("scan-note-basis");
    if (basis) {
      basis.textContent = isData
        ? "两侧由 search_results 复算"
        : "引用 ADR-0008";
    }

    /* 「不要对逐 run 求和」的理由随形态变 */
    var sumNote = doc.getElementById("sum-note-tail");
    if (sumNote) {
      sumNote.textContent = isData
        ? "同一道题在两条扫描里各有一条 run，直接求和会把两次扫描混在一起。"
        : "closure 与 scan10 各含同一条 smoke-add，会重复计算。";
    }
  }

  /* ==========================================================================
   * 3. #live-list —— 实时列表：题号 / 题目 / 两次成本（✅ ❌ + 金额）
   * ========================================================================== */

  function signedUsd(v, F) {
    if (!isNum(v)) return DASH;
    var head = v < 0 ? "−" : (v > 0 ? "+" : "");
    /* 单位不能漏：合计行的成本差与逐题金额同形（−$0.024876） */
    return head + "$" + F.usd(Math.abs(v));
  }

  function signedInt(v) {
    if (!isNum(v)) return DASH;
    var n = Math.round(v);
    if (n < 0) return "−" + Math.abs(n);
    if (n > 0) return "+" + n;
    return "0";
  }

  /* 成本单元格：判定 emoji + 金额。emoji 只做视觉，读屏靠 sr-only 文案。 */
  function costCell(doc, cls, passed, cost, F, who) {
    var cell = el(doc, "span", "lr-c " + cls);
    var flag = el(doc, "em", "lr-flag", passed === true ? "✅" : "❌");
    flag.setAttribute("aria-hidden", "true");
    cell.appendChild(flag);
    cell.appendChild(el(doc, "b", "lr-cost", "$" + F.usd(cost)));
    cell.appendChild(el(doc, "span", "sr-only", "（" + who + (passed === true ? "通过" : "未通过") + "）"));
    return cell;
  }

  /* 实时列表：一行一题。四个单元格 = 题号 / 题目 / 第一次成本 / 第二次成本。
     数值同时写进 data-base / data-cand，app.js 的折线图直接读行上的数，
     不另建数据源 —— 列表和图表永远是同一份数字。
     无 JS / 减少动效时这些行一开始就全可见（见 app.css §21）。 */
  function renderLiveRows(doc, m) {
    var list = doc.getElementById("live-list");
    if (!list) return 0;

    var F = fmtOf(m);
    var pairs = m.s2 && m.s2.pairs ? m.s2.pairs : [];
    clear(list);

    for (var i = 0; i < pairs.length; i++) {
      var p = pairs[i] || {};
      var li = el(doc, "li", "live-row");
      li.setAttribute("data-idx", String(p.index));
      /* 数值 + 判定都落在行上：app.js 的柱状图 / 合计只读这里，不另建数据源 */
      if (p.taskId !== null && p.taskId !== undefined) {
        li.setAttribute("data-task", String(p.taskId));
      }
      if (isNum(p.baseCost)) li.setAttribute("data-base", String(p.baseCost));
      if (isNum(p.candCost)) li.setAttribute("data-cand", String(p.candCost));
      li.setAttribute("data-base-ok", p.basePassed === true ? "1" : "0");
      li.setAttribute("data-cand-ok", p.candPassed === true ? "1" : "0");

      li.appendChild(el(doc, "span", "lr-idx", asInt(p.index)));

      var nameCell = el(doc, "span", "lr-name");
      nameCell.appendChild(el(doc, "span", "lr-task", asStr(p.taskId)));
      if (p.live === true) nameCell.appendChild(el(doc, "span", "tag tag-live", "live"));
      li.appendChild(nameCell);

      li.appendChild(costCell(doc, "lr-base", p.basePassed, p.baseCost, F, "第一次"));
      li.appendChild(costCell(doc, "lr-cand", p.candPassed, p.candCost, F, "第二次"));

      list.appendChild(li);
    }

    return pairs.length;
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
      if (r.adopted === true) li.className = "is-adopted";

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
      metaRow.appendChild(el(doc, "span", null, "（声明值，非本页证据）"));
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

  function noteList(doc, items, cls) {
    var ul = el(doc, "ul", cls);
    for (var i = 0; i < items.length; i++) {
      var li = doc.createElement("li");
      li.textContent = String(items[i]);
      ul.appendChild(li);
    }
    return ul;
  }

  /* 数据错误：仍然折叠（错误不是常读内容，且出现时页面本身已经不可用） */
  function errorBlock(doc, items) {
    var box = doc.createElement("details");
    box.className = "warnings is-error";
    box.open = true;

    var sum = doc.createElement("summary");
    sum.textContent = "数据错误（" + items.length + "）";
    box.appendChild(sum);
    box.appendChild(noteList(doc, items, "warnings-list"));

    return box;
  }

  /* 口径说明：默认展开、不折叠、无箭头 —— 它是 S8 的正文，不是「可展开的附录」。
     每条 warning 全文渲染（不截断、不加省略号）。 */
  function warningBlock(doc, items) {
    var box = doc.createElement("section");
    box.className = "warnings";
    box.id = "warnings";

    var title = doc.createElement("h3");
    title.className = "warnings-title";
    title.textContent = "口径说明";
    box.appendChild(title);

    box.appendChild(noteList(doc, items, "warnings-list"));

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

    if (errs.length > 0) box.appendChild(errorBlock(doc, errs));
    if (warns.length > 0) box.appendChild(warningBlock(doc, warns));

    host.appendChild(box);
  }

  /* ==========================================================================
   * 7c. S7 人话句：措辞由 holdoutNote.dropped 决定（true 掉题 / false 没掉 / null 缺数）
   *     两句都在 DOM 里，只切 hidden —— 槽位因此不会被摘掉。
   * ========================================================================== */

  function writeHoldoutPlain(doc, m) {
    var okNode = doc.getElementById("holdout-plain-ok");
    var dropNode = doc.getElementById("holdout-plain-drop");
    if (!okNode || !dropNode) return;

    var note = m && m.holdout && m.holdout.holdoutNote ? m.holdout.holdoutNote : null;
    var dropped = note ? note.dropped : null;

    /* dropped === true 才换成「掉题」句式；false 与 null 都保留默认句
       （null = 数据不足，此时数字自己是「—」，句子不会变成断言）。 */
    okNode.hidden = dropped === true;
    dropNode.hidden = dropped !== true;
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

      out.counts.pairs = renderLiveRows(doc, m);
      out.counts.chain = renderChain(doc, m);
      out.counts.lineage = renderLineage(doc, m);
      out.counts.archives = renderArchives(doc, m);
      out.counts.snapshots = renderSnapshots(doc, m);
      renderHoldoutAttribution(doc, m);
      writeHoldoutPlain(doc, m);

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
