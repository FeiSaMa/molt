/*!
 * molt · web/page/model.js —— 数据适配层（P0 四屏）
 *
 * 定位：纯函数 / 零 DOM / 零网络 / 零副作用。
 * 输入：window.MOLT_VIEW（由 ../data/bundle.js 注入，本文件不读文件、不发请求）。
 * 输出：四屏视图模型 + 格式化工具。所有数字都能从 data/ 复算，或明确标注为「引用」。
 *
 * 为什么需要这一层：file:// 下 fetch JSON 会被同源策略拦掉，唯一数据入口是
 * window.MOLT_VIEW。渲染层（render.js / app.js）只消费本文件的输出，不碰原始 payload。
 * 本文件同时用 UMD 包装，node 里可以 require() 做验收（不需要 DOM）。
 *
 * 数据面事实（2026-11-11 导出，已核实）：
 *   window.MOLT_VIEW 有 12 个键：
 *     archive, audit, events, gate, proposals, redaction, report_results,
 *     runs, scenario, search_results, snapshots, variants
 *   **没有 index 键** → meta.revision 恒 null，generatedAt 只能从 runs / gate 取。
 *
 * 两个口径，永远不要混：
 *   扫描级 = 两条 10 题扫描（scan10 vs candidate-scan+closure live）比较 cost / wall；
 *   gate 级 = decisions[0].pairs 的逐题配对，比较 cost_per_solved（cps）。
 *   runs[].cost_usd 不能直接求和当页面成本结论：closure 与 scan10 各含同一条 smoke-add。
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.MOLT_MODEL = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /* ==========================================================================
   * 常量
   * ========================================================================== */

  var DASH = "—";                  // 缺值占位符，渲染层直接显示
  var EPS_COST = 5e-7;             // 成本核对容差（USD，$ 记到 1e-6）
  var EPS_WALL = 0.06;             // 墙钟核对容差（秒）
  var DEMO_GROUPS = ["closure", "scan10", "candidate-scan", "none"];

  // ADR-0008 的扫描级口径（常量照抄，不是从数据面算出来的）。
  // 注意：候选侧 10 题扫描未随数据面导出（第 10 题被 closure live 顶替，只剩 9 条 replay），
  // 所以下面 recomputed 的候选配对合计是 $0.010684 / 42.6s，与 claim 的 $0.009978 / 41.7s 不符，
  // verified.candCost / candWall 因此为 false —— 这是「如实标注」，不是 bug。
  var SCAN_CLAIM = {
    baseCostUsd: 0.035560,
    candCostUsd: 0.009978,
    baseWallS: 106.9,
    candWallS: 41.7,
    costDropPct: 72,
    wallDropPct: 61,
    source: "docs/decisions/0008-demo.md"
  };

  // 口径说明：数字全部由 buildScan 的实算结果 + SCAN_CLAIM 现拼，不写字面量，
  // 避免页脚与首屏出现两个互相对不上的候选侧数字。
  function scanScopeNote(scan) {
    if (scan.source === "data") {
      var d = scan.display;
      return "扫描级取自 search_results：两次扫描都是 live 运行，基线 $" +
        fmt.usd(d.baseCostUsd) + " / " + fmt.sec(d.baseWallMs) + "s，候选 $" +
        fmt.usd(d.candCostUsd) + " / " + fmt.sec(d.candWallMs) + "s，均由 runs 复算";
    }

    var rc = scan.recomputed;
    var c = scan.counts;
    var head = "扫描级（ADR-0008）的候选侧成本/墙钟未随数据面导出：";

    if (c.candReplay + c.candLive <= 0) {
      return head + "数据面没有候选侧 run 可复算，扫描级取 ADR 常量 $" +
        fmt.usd(SCAN_CLAIM.candCostUsd) + " / " + fmt.sec(SCAN_CLAIM.candWallS) + "s";
    }

    return head +
      "数据面候选侧只有 " + c.candReplay + " 条 replay + " + c.candLive + " 条 closure live，" +
      "合计 $" + fmt.usd(rc.candPairCostUsd) + " / " + fmt.sec(rc.candPairWallMs) +
      "s 属 gate 级配对口径，不等于扫描级的 $" +
      fmt.usd(SCAN_CLAIM.candCostUsd) + " / " + fmt.sec(SCAN_CLAIM.candWallS) + "s";
  }

  var SUM_NOTE = "不得对 runs.cost_usd 直接求和作为页面成本结论：closure 与 scan10 各含同一条 smoke-add";

  /* ==========================================================================
   * 基础类型工具（ES5，避免老 WebView 翻车）
   * ========================================================================== */

  function isArr(v) {
    return Object.prototype.toString.call(v) === "[object Array]";
  }

  function isObj(v) {
    return !!v && typeof v === "object" && !isArr(v);
  }

  function isNum(v) {
    return typeof v === "number" && isFinite(v);
  }

  function numOrNull(v) {
    return isNum(v) ? v : null;
  }

  function intOrNull(v) {
    return isNum(v) ? Math.round(v) : null;
  }

  function strOrNull(v) {
    return typeof v === "string" && v !== "" ? v : null;
  }

  function toNum(v) {
    if (isNum(v)) return v;
    if (typeof v === "string") {
      var t = v.replace(/\s+/g, "");
      if (t === "") return null;
      var n = Number(t);
      return isFinite(n) ? n : null;
    }
    return null;
  }

  function round1(n) {
    if (!isNum(n)) return null;
    var r = Math.round(n * 10) / 10;
    return r === 0 ? 0 : r;   // 抹掉 -0
  }

  // 变动百分比（from → to，from 为 0 时无意义）
  function dropPct(from, to) {
    if (!isNum(from) || !isNum(to) || from === 0) return null;
    return round1((1 - to / from) * 100);
  }

  function stripScheme(id) {
    return typeof id === "string" ? id.replace(/^sha256:/, "") : "";
  }

  /* ==========================================================================
   * 格式化工具（API.fmt）
   *   数字一律不返回 NaN / undefined / Infinity；非法输入返回 "—"
   * ========================================================================== */

  var fmt = {
    // 金额：默认 6 位小数，不足补零，不用科学计数法
    usd: function (n, digits) {
      var d = isNum(digits) && digits >= 0 && digits <= 12 ? Math.floor(digits) : 6;
      var v = toNum(n);
      return v === null ? DASH : v.toFixed(d);
    },

    // 百分比数值（不含 % 号），1 位小数，保留负号
    // 注意：入参是「已经换算成百分比」的数（69.99 → "70.0"），不是 0.6999 这种比例
    pct: function (n) {
      var v = toNum(n);
      if (v === null) return DASH;
      var s = v.toFixed(1);
      return s === "-0.0" ? "0.0" : s;
    },

    // 比例（0–1，如 invalid_ratio）→ 2 位小数，不换算成百分比
    ratio: function (n) {
      var v = toNum(n);
      return v === null ? DASH : v.toFixed(2);
    },

    // 时长：≥1000 视为毫秒，否则视为秒；输出 1 位小数
    sec: function (v) {
      var n = toNum(v);
      if (n === null) return DASH;
      var s = Math.abs(n) >= 1000 ? n / 1000 : n;
      return s.toFixed(1);
    },

    // genome / archive id：去掉 sha256: 前缀取前 8 位
    shortId: function (s) {
      var t = stripScheme(s).slice(0, 8);
      return t === "" ? DASH : t;
    },

    // ISO → "2026-11-10 17:05 UTC"（纯字符串切分，不做本地时区换算）
    iso: function (s) {
      if (typeof s !== "string") return DASH;
      var m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(s);
      return m ? m[1] + " " + m[2] + " UTC" : DASH;
    }
  };

  /* ==========================================================================
   * holdout 聚合导出契约：只允许聚合值，出现逐题字段即违规
   *   API.assertAggregateOnly(reportPayload) → { ok, hits[], detail }
   * ========================================================================== */

  var BANNED_KEYS = { task_id: 1, run_id: 1, task_ids: 1 };

  function assertAggregateOnly(payload) {
    var hits = [];
    var MAX_DEPTH = 12;

    function walk(node, path, depth) {
      if (depth > MAX_DEPTH || !node || typeof node !== "object") return;
      if (isArr(node)) {
        for (var i = 0; i < node.length; i++) {
          walk(node[i], path + "[" + i + "]", depth + 1);
        }
        return;
      }
      for (var k in node) {
        if (!Object.prototype.hasOwnProperty.call(node, k)) continue;
        var here = path ? path + "." + k : k;
        if (BANNED_KEYS[k]) hits.push(here);
        walk(node[k], here, depth + 1);
      }
    }

    walk(payload, "", 0);

    return {
      ok: hits.length === 0,
      hits: hits,
      detail: hits.length === 0 ? "" : "holdout 出现逐题字段，违反导出契约"
    };
  }

  /* ==========================================================================
   * 选择门禁判定：created_at 最大；全部相等时取最后一条
   * ========================================================================== */

  function pickDecision(decisions) {
    var best = null;
    var bestAt = null;
    for (var i = 0; i < decisions.length; i++) {
      var d = decisions[i];
      if (!isObj(d)) continue;
      var at = strOrNull(d.created_at);
      if (best === null) {
        best = d;
        bestAt = at;
        continue;
      }
      if (at && (bestAt === null || at >= bestAt)) {   // >= ：并列时后一条胜出
        best = d;
        bestAt = at;
      }
    }
    return best;
  }

  /* ==========================================================================
   * 基线 / 候选识别
   *   主路：按 gate decision 的 genome_id 精确匹配 result
   *   退化：匹配不到时（现场两条 live 扫描的 genome 与 gate 不一致），
   *         按 cost_total_usd 降序取最高为 baseline、最低为 candidate，并告警
   * ========================================================================== */

  function scoreBlock(res, domain) {
    if (!isObj(res) || !isObj(res.scores) || !isObj(res.scores[domain])) return null;
    return res.scores[domain];
  }

  function pickPair(results, genomeBase, genomeCand, domain) {
    var base = null;
    var cand = null;
    var i;

    for (i = 0; i < results.length; i++) {
      var r = results[i];
      if (!isObj(r)) continue;
      if (!base && genomeBase && r.genome_id === genomeBase) base = r;
      if (!cand && genomeCand && r.genome_id === genomeCand) cand = r;
    }
    if (base && cand) {
      return { base: base, cand: cand, inferred: false, reason: "genome" };
    }

    var usable = [];
    for (i = 0; i < results.length; i++) {
      if (scoreBlock(results[i], domain)) usable.push(results[i]);
    }
    if (usable.length < 2) {
      return { base: null, cand: null, inferred: false, reason: "insufficient" };
    }

    usable.sort(function (a, b) {
      var ca = toNum(scoreBlock(a, domain).cost_total_usd);
      var cb = toNum(scoreBlock(b, domain).cost_total_usd);
      if (ca === null && cb === null) return 0;
      if (ca === null) return 1;
      if (cb === null) return -1;
      return cb - ca;                                  // 降序：贵的在前
    });

    return {
      base: usable[0],
      cand: usable[usable.length - 1],
      inferred: true,
      reason: "cost_desc"
    };
  }

  /* ==========================================================================
   * provenance：优先按 runs[].provenance.kind 计数，缺失时回退 scenario.provenance_counts
   *   绝不硬编码「1 live」：现场两次 live 扫描后应为 live=20 / replay=0
   * ========================================================================== */

  function countProvenance(runs, scenario, reportPayload) {
    var out = {
      live: 0,
      replay: 0,
      fixture: 0,
      total: 0,
      byDemoGroup: { closure: 0, scan10: 0, "candidate-scan": 0, none: 0 },
      basis: "runs",
      searchOnly: true,
      reportRunsTotal: null,
      note: null
    };
    var i, g, kind;

    var reportTotal = reportPayload && isObj(reportPayload.report_runs_total)
      ? intOrNull(reportPayload.report_runs_total.count)
      : null;
    if (reportTotal !== null && reportTotal > 0) out.reportRunsTotal = reportTotal;

    if (isArr(runs)) {
      for (i = 0; i < runs.length; i++) {
        var r = runs[i];
        if (!isObj(r)) continue;
        if (r.scope !== "search") out.searchOnly = false;
        kind = isObj(r.provenance) ? r.provenance.kind : null;
        if (kind === "live" || kind === "replay" || kind === "fixture") out[kind] += 1;
        g = r.demo_group === null || r.demo_group === undefined ? "none" : String(r.demo_group);
        if (out.byDemoGroup[g] === undefined) out.byDemoGroup[g] = 0;
        out.byDemoGroup[g] += 1;
      }
      out.total = runs.length;
      out.basis = "runs";
      return finishProvenance(out);
    }

    var counts = scenario && scenario.provenance_counts;
    if (isObj(counts)) {
      out.live = intOrNull(counts.live) || 0;
      out.replay = intOrNull(counts.replay) || 0;
      out.fixture = intOrNull(counts.fixture) || 0;
      out.total = out.live + out.replay + out.fixture;
      out.basis = "scenario";
      out.searchOnly = false;
      return finishProvenance(out);
    }

    out.basis = "none";
    return finishProvenance(out);
  }

  // 徽章旁的一行小字：三个 kind 全部实算，不写死「1 live」；
  // report 运行（holdout）只导出聚合，条数单列，不混进 search 计数。
  function finishProvenance(out) {
    if (out.basis === "none") return out;
    var label = out.searchOnly === true ? "条 search 运行记录" : "条运行记录";
    out.note = "共 " + out.total + " " + label + "：live " + out.live +
      " · replay " + out.replay + " · fixture " + out.fixture;
    if (isNum(out.reportRunsTotal)) {
      out.note += "（report 运行另有 " + out.reportRunsTotal + " 条，只导出聚合）";
    }
    return out;
  }

  /* ==========================================================================
   * meta
   * ========================================================================== */

  function pickGeneratedAt(view, runs, decision) {
    var index = view && view.index;
    if (isObj(index) && strOrNull(index.generated_at)) {
      return { at: index.generated_at, source: "index" };
    }

    if (isArr(runs)) {
      var best = null;
      for (var i = 0; i < runs.length; i++) {
        var at = runs[i] && strOrNull(runs[i].finished_at);
        if (at && (best === null || at > best)) best = at;
      }
      if (best) return { at: best, source: "runs" };
    }

    if (isObj(decision) && strOrNull(decision.created_at)) {
      return { at: decision.created_at, source: "gate" };
    }

    return { at: null, source: "none" };
  }

  /* ==========================================================================
   * S1 两次运行对照（扫描级 cost / wall + gate 级 cps）
   * ========================================================================== */

  /* 降幅文案：正值 = 变便宜（页面写 −），负值 = 变贵（页面写 +）。
     mode="int" 只用于 ADR 常量（72 / 61），避免 71.9 vs 72.0 的假精度。 */
  function dropTextOf(v, mode) {
    if (!isNum(v)) return null;
    var abs = Math.abs(v);
    var body = mode === "int" ? String(Math.round(abs)) : fmt.pct(abs);
    var sign = v > 0 ? "−" : (v < 0 ? "+" : "");
    return sign + body + "%";
  }

  /* 现场形态判定：没有 demo_group 的 scope=search run 覆盖了多少道不同的题。
     两条 live 扫描全部落成 demo_group=null 时，这个数 = 搜索集题数。 */
  function countScanCoverage(runs) {
    var seen = {};
    var n = 0;
    if (!isArr(runs)) return 0;
    for (var i = 0; i < runs.length; i++) {
      var r = runs[i];
      if (!isObj(r)) continue;
      if (r.scope !== "search") continue;
      if (r.demo_group !== null && r.demo_group !== undefined) continue;
      var tid = strOrNull(r.task_id);
      if (!tid || seen[tid] === true) continue;
      seen[tid] = true;
      n += 1;
    }
    return n;
  }

  /* 某 genome 的 search run 墙钟合计（毫秒）；匹配不到返回 null，不补 0 */
  function sumWallByGenome(runs, genomeId) {
    if (!isArr(runs) || typeof genomeId !== "string" || genomeId === "") return null;
    var total = 0;
    var n = 0;
    for (var i = 0; i < runs.length; i++) {
      var r = runs[i];
      if (!isObj(r)) continue;
      if (r.scope !== "search") continue;
      if (r.genome_id !== genomeId) continue;
      total += toNum(r.wall_ms) || 0;
      n += 1;
    }
    return n > 0 ? total : null;
  }

  function buildScan(runs, ctx) {
    var c = isObj(ctx) ? ctx : {};
    var scan = {
      claim: SCAN_CLAIM,
      /* "adr" = 扫描级数字引自 ADR-0008（replay 回放形态，demo_group 齐备）
         "data" = 扫描级数字由 search_results 复算（现场两条 live 扫描，无 demo_group） */
      source: "adr",
      recomputed: {
        baseCostUsd: null,
        baseWallMs: null,
        candPairCostUsd: null,
        candPairWallMs: null
      },
      verified: { baseCost: null, baseWall: null, candCost: null, candWall: null },
      /* KPI3 与 #scan-note 唯一允许取数的地方：两种形态都由这里给出 */
      display: {
        baseCostUsd: null,
        candCostUsd: null,
        baseWallMs: null,
        candWallMs: null,
        costDropPct: null,
        wallDropPct: null,
        costDropText: null,
        wallDropText: null
      },
      coverage: { runs: 0, want: null },
      // 条数：渲染层拼「N 条 replay + M 条 closure live」时只许用这里的实算值
      counts: { baseRuns: 0, candReplay: 0, candLive: 0, candLiveTaskIds: [] }
    };

    if (!isArr(runs)) return scan;

    var baseCost = 0, baseWall = 0, baseN = 0;
    var candCost = 0, candWall = 0, candN = 0;
    var candReplayN = 0, candLiveN = 0;
    var i, r;

    // 基线扫描：scan10（10 条 replay）
    for (i = 0; i < runs.length; i++) {
      r = runs[i];
      if (!isObj(r)) continue;
      if (r.demo_group !== "scan10") continue;
      if (r.scope !== "search") continue;
      baseCost += toNum(r.cost_usd) || 0;
      baseWall += toNum(r.wall_ms) || 0;
      baseN += 1;
    }

    // 候选配对：9 条 candidate-scan + 1 条 closure live（第 10 题）
    for (i = 0; i < runs.length; i++) {
      r = runs[i];
      if (!isObj(r)) continue;
      var isCandScan = r.demo_group === "candidate-scan";
      var isLiveClosure = r.demo_group === "closure" &&
        isObj(r.provenance) && r.provenance.kind === "live";
      if (!isCandScan && !isLiveClosure) continue;
      candCost += toNum(r.cost_usd) || 0;
      candWall += toNum(r.wall_ms) || 0;
      candN += 1;
      if (isLiveClosure) {
        candLiveN += 1;
        if (typeof r.task_id === "string") scan.counts.candLiveTaskIds.push(r.task_id);
      } else {
        candReplayN += 1;
      }
    }

    scan.counts.baseRuns = baseN;
    scan.counts.candReplay = candReplayN;
    scan.counts.candLive = candLiveN;

    if (baseN > 0) {
      scan.recomputed.baseCostUsd = baseCost;
      scan.recomputed.baseWallMs = baseWall;
    }
    if (candN > 0) {
      scan.recomputed.candPairCostUsd = candCost;
      scan.recomputed.candPairWallMs = candWall;
    }

    var rc = scan.recomputed;
    scan.verified.baseCost = isNum(rc.baseCostUsd)
      ? Math.abs(rc.baseCostUsd - SCAN_CLAIM.baseCostUsd) < EPS_COST
      : null;
    scan.verified.baseWall = isNum(rc.baseWallMs)
      ? Math.abs(rc.baseWallMs / 1000 - SCAN_CLAIM.baseWallS) < EPS_WALL
      : null;
    scan.verified.candCost = isNum(rc.candPairCostUsd)
      ? Math.abs(rc.candPairCostUsd - SCAN_CLAIM.candCostUsd) < EPS_COST
      : null;
    scan.verified.candWall = isNum(rc.candPairWallMs)
      ? Math.abs(rc.candPairWallMs / 1000 - SCAN_CLAIM.candWallS) < EPS_WALL
      : null;

    // ---- ADR 形态的默认 display：候选侧数字引自 ADR，降幅取整数（72 / 61）
    scan.display.baseCostUsd = SCAN_CLAIM.baseCostUsd;
    scan.display.candCostUsd = SCAN_CLAIM.candCostUsd;
    scan.display.baseWallMs = SCAN_CLAIM.baseWallS * 1000;
    scan.display.candWallMs = SCAN_CLAIM.candWallS * 1000;
    scan.display.costDropPct = SCAN_CLAIM.costDropPct;
    scan.display.wallDropPct = SCAN_CLAIM.wallDropPct;

    applyDataDisplay(scan, runs, c);

    scan.display.costDropText = dropTextOf(
      scan.display.costDropPct,
      scan.source === "adr" ? "int" : "raw"
    );
    scan.display.wallDropText = dropTextOf(
      scan.display.wallDropPct,
      scan.source === "adr" ? "int" : "raw"
    );

    return scan;
  }

  /* 现场形态：两条完整 live 扫描（没有 demo_group）+ 两条 search result。
     此时扫描级数字来自 search_results，verified 表示「自我一致」，不是与 ADR 比对。 */
  function applyDataDisplay(scan, runs, c) {
    var results = isArr(c.results) ? c.results : [];
    var want = isNum(c.taskCount) && c.taskCount > 0 ? c.taskCount : null;
    var coverage = countScanCoverage(runs);
    scan.coverage = { runs: coverage, want: want };

    if (want === null || results.length < 2 || coverage < want) return;

    var pair = isObj(c.pair) ? c.pair : null;
    var bs = pair ? scoreBlock(pair.base, "search") : null;
    var cs = pair ? scoreBlock(pair.cand, "search") : null;
    if (!bs || !cs) return;

    var baseCost = toNum(bs.cost_total_usd);
    var candCost = toNum(cs.cost_total_usd);
    if (baseCost === null || candCost === null) return;

    var baseWall = sumWallByGenome(runs, pair.base.genome_id);
    var candWall = sumWallByGenome(runs, pair.cand.genome_id);

    scan.source = "data";
    scan.display.baseCostUsd = baseCost;
    scan.display.candCostUsd = candCost;
    scan.display.baseWallMs = baseWall;
    scan.display.candWallMs = candWall;
    scan.display.costDropPct = dropPct(baseCost, candCost);
    scan.display.wallDropPct = (baseWall !== null && candWall !== null)
      ? dropPct(baseWall, candWall)
      : null;

    // 成本两侧都由 result 现算 → 自查通过；墙钟拿不到 run 就不敢说 true
    scan.verified = {
      baseCost: true,
      candCost: true,
      baseWall: baseWall !== null ? true : null,
      candWall: candWall !== null ? true : null
    };
  }

  function emptyS1() {
    return {
      available: false,
      solvedBase: null,
      solvedCand: null,
      solvedDelta: null,      // solvedCand - solvedBase，渲染层据此写「成功率不变 / 通过数 ±N」
      totalBase: null,
      totalCand: null,
      cpsBase: null,
      cpsCand: null,
      cpsDropPct: null,
      cpsDropText: null,      // 带符号字符串（−70.0% / +3.2%），渲染层直接写
      costBase: null,
      costCand: null,
      costDropPct: null,      // 由 costBase / costCand 复算（扫描级，页面 KPI 用）
      wallBase: null,         // 秒，来自 recomputed 墙钟
      wallCand: null,
      wallDropPct: null,
      verdict: null,
      reason: null,
      scan: null
    };
  }

  function buildS1(decision, searchResults, runs, warnings, searchTaskCount) {
    var s1 = emptyS1();

    var genomeBase = decision ? strOrNull(decision.baseline_genome_id) : null;
    var genomeCand = decision ? strOrNull(decision.candidate_genome_id) : null;
    var pair = pickPair(searchResults, genomeBase, genomeCand, "search");

    s1.scan = buildScan(runs, {
      // 没有门禁判定就不算「现场形态」：扫描级保持 ADR 口径，不靠推断的基线/候选
      pair: decision ? pair : null,
      results: searchResults,
      taskCount: searchTaskCount
    });

    if (isArr(runs)) {
      warnings.push(scanScopeNote(s1.scan));
      warnings.push(SUM_NOTE);
    }

    // 墙钟口径与 KPI3 同源：一律取 scan.display，不再各算一套
    var disp = s1.scan.display;
    s1.wallBase = isNum(disp.baseWallMs) ? round1(disp.baseWallMs / 1000) : null;
    s1.wallCand = isNum(disp.candWallMs) ? round1(disp.candWallMs / 1000) : null;
    s1.wallDropPct = disp.wallDropPct;

    if (!decision) return s1;

    s1.verdict = strOrNull(decision.verdict);
    s1.reason = strOrNull(decision.reason);

    if (pair.inferred) {
      warnings.push("按成本降序推断基线/候选，未与门禁 genome 对齐");
    }
    if (pair.reason === "insufficient") {
      warnings.push("搜索结果不足两条，无法做两次运行对照");
      return s1;
    }

    var bs = scoreBlock(pair.base, "search");
    var cs = scoreBlock(pair.cand, "search");
    if (!bs || !cs) return s1;

    s1.available = true;
    s1.solvedBase = intOrNull(bs.solved);
    s1.solvedCand = intOrNull(cs.solved);
    s1.solvedDelta = (s1.solvedBase === null || s1.solvedCand === null)
      ? null
      : s1.solvedCand - s1.solvedBase;
    s1.totalBase = intOrNull(bs.total);
    s1.totalCand = intOrNull(cs.total);
    s1.cpsBase = numOrNull(bs.cost_per_solved_usd);
    s1.cpsCand = numOrNull(cs.cost_per_solved_usd);
    s1.cpsDropPct = dropPct(s1.cpsBase, s1.cpsCand);
    s1.cpsDropText = dropTextOf(s1.cpsDropPct, "raw");
    s1.costBase = numOrNull(bs.cost_total_usd);
    s1.costCand = numOrNull(cs.cost_total_usd);
    s1.costDropPct = dropPct(s1.costBase, s1.costCand);

    // 与门禁自带分数交叉核对：不一致说明导出层与 result 漂移了
    var gs = isObj(decision.scores) ? decision.scores : null;
    if (gs && isObj(gs.baseline) && isObj(gs.candidate)) {
      var gb = toNum(gs.baseline.cost_per_solved_usd);
      var gc = toNum(gs.candidate.cost_per_solved_usd);
      if (gb !== null && isNum(s1.cpsBase) && Math.abs(gb - s1.cpsBase) > 1e-9) {
        warnings.push("门禁判定内嵌的基线 cps 与 search_results 不一致");
      }
      if (gc !== null && isNum(s1.cpsCand) && Math.abs(gc - s1.cpsCand) > 1e-9) {
        warnings.push("门禁判定内嵌的候选 cps 与 search_results 不一致");
      }
    }

    return s1;
  }

  /* ==========================================================================
   * S2 逐题配对（按 task_id 字典序，index 从 1 递增，渲染稳定）
   * ========================================================================== */

  /* kind = live 的 task_id 集合：配对表用它标 live 行。
     现场形态没有 demo_group，所以不能只看 closure 组——只认 provenance.kind。 */
  function liveTaskSet(runs) {
    var set = {};
    if (!isArr(runs)) return set;
    for (var i = 0; i < runs.length; i++) {
      var r = runs[i];
      if (!isObj(r)) continue;
      if (r.scope !== "search") continue;
      if (!isObj(r.provenance) || r.provenance.kind !== "live") continue;
      if (typeof r.task_id === "string") set[r.task_id] = true;
    }
    return set;
  }

  function buildS2(decision, runs, errors) {
    var s2 = {
      pairs: [],
      pairCount: 0,
      /* 逐题求和（gate 级口径）：渲染层用它画表格合计行。
         只在这里求和，页面不许拿 runs.cost_usd 求和。 */
      totals: { base: null, cand: null, delta: null },
      invalid: { base: null, cand: null, max: null },
      k: null,
      // 便于渲染层直连 data-bind="s2.invalidBase" 这类槽位
      invalidBase: null,
      invalidCand: null,
      invalidMax: null
    };

    if (!decision) return s2;

    var liveSet = liveTaskSet(runs);
    var raw = isArr(decision.pairs) ? decision.pairs.slice() : [];
    raw.sort(function (a, b) {
      return String(a && a.task_id).localeCompare(String(b && b.task_id));
    });

    s2.pairs = raw.map(function (p, i) {
      var baseCost = numOrNull(p && p.baseline_cost_usd);
      var candCost = numOrNull(p && p.candidate_cost_usd);
      var taskId = p && p.task_id;
      return {
        index: i + 1,
        taskId: taskId,
        baseCost: baseCost,
        candCost: candCost,
        costDelta: numOrNull(p && p.cost_delta_usd),
        passedDelta: numOrNull(p && p.passed_delta),
        basePassed: !!(p && p.baseline_passed),
        candPassed: !!(p && p.candidate_passed),
        ratio: isNum(baseCost) && baseCost > 0 && isNum(candCost) ? candCost / baseCost : null,
        // 该题在数据面里由 closure live 那条 run 提供（匹配不到即 false，不猜）
        live: typeof taskId === "string" && liveSet[taskId] === true
      };
    });

    s2.pairCount = s2.pairs.length;
    if (s2.pairCount === 0) errors.push("门禁配对表为空");

    // 合计：逐题相加，任一题缺数就整行不给（不用 0 顶替）
    var sumBase = 0, sumCand = 0, sumDelta = 0, sumOk = s2.pairCount > 0;
    for (var i = 0; i < s2.pairs.length; i++) {
      var one = s2.pairs[i];
      if (!isNum(one.baseCost) || !isNum(one.candCost) || !isNum(one.costDelta)) {
        sumOk = false;
        break;
      }
      sumBase += one.baseCost;
      sumCand += one.candCost;
      sumDelta += one.costDelta;
    }
    if (sumOk) {
      s2.totals = { base: sumBase, cand: sumCand, delta: sumDelta };
    }

    var inv = isObj(decision.invalid_ratio) ? decision.invalid_ratio : {};
    s2.invalid = {
      base: numOrNull(inv.baseline),
      cand: numOrNull(inv.candidate),
      max: numOrNull(inv.threshold)
    };
    s2.invalidBase = s2.invalid.base;
    s2.invalidCand = s2.invalid.cand;
    s2.invalidMax = s2.invalid.max;
    s2.k = numOrNull(decision.k);

    return s2;
  }

  /* ==========================================================================
   * S3 六道闸（顺序固定）
   *   文案拼好放 observed，同时保留 observedValue 原始数字/对象。
   *   terminal 只在 passed === false 时标记（= 停在这一步）。
   * ========================================================================== */

  function buildS3(decision, s1) {
    var s3 = {
      chain: [],
      verdict: null,
      reason: null,
      limitsIdentical: null,
      budgetCheck: null
    };

    if (!decision) return s3;

    var inv = isObj(decision.invalid_ratio) ? decision.invalid_ratio : {};
    var iBase = numOrNull(inv.baseline);
    var iCand = numOrNull(inv.candidate);
    var iMax = numOrNull(inv.threshold);

    var bc = isObj(decision.budget_check) ? decision.budget_check : {};
    var execExceeded = bc.execution_exceeded === true;
    var overheadExceeded = bc.overhead_exceeded === true;
    var budgetReason = strOrNull(bc.reason);

    var limits = decision.limits_identical;
    var limitsIsBool = limits === true || limits === false;

    var k = numOrNull(decision.k);
    var solvedBase = s1 ? s1.solvedBase : null;
    var solvedCand = s1 ? s1.solvedCand : null;
    var cpsBase = s1 ? s1.cpsBase : null;
    var cpsCand = s1 ? s1.cpsCand : null;
    var verdict = strOrNull(decision.verdict);

    s3.verdict = verdict;
    s3.reason = strOrNull(decision.reason);
    s3.limitsIdentical = limitsIsBool ? limits : null;
    s3.budgetCheck = budgetReason;

    // 1. incomparable —— 四项执行上限是否完全一致
    //    缺失（非布尔）一律「未判定」，不 fail-open 也不 fail-closed
    var limitsPassed = (limits === true || limits === false) ? limits : null;
    var limitsObserved = limits === true
      ? "四项上限全等"
      : (limits === false ? "上限不一致" : "未记录 limits_identical");

    // 2. unstable —— 无效运行比例
    var unstablePassed = null;
    if (iMax !== null) {
      var overBase = iBase !== null && iBase > iMax;
      var overCand = iCand !== null && iCand > iMax;
      if (iBase !== null || iCand !== null) unstablePassed = !(overBase || overCand);
    }

    // 3. budget_exceeded —— 执行与搜索预算
    var budgetPassed = !(execExceeded || overheadExceeded);

    // 4. solved_dropped —— 候选 ≥ 基线 − k
    var solvedPassed = null;
    if (solvedBase !== null && solvedCand !== null && k !== null) {
      solvedPassed = solvedCand >= solvedBase - k;
    }

    // 5. cost_not_lower —— 候选 cps ≤ 基线 cps
    var costPassed = null;
    if (isNum(cpsBase) && isNum(cpsCand)) costPassed = cpsCand <= cpsBase;

    s3.chain = [
      {
        id: "incomparable",
        label: "执行上限是否完全一致",
        observed: limitsObserved,
        threshold: "limits_identical = true",
        passed: limitsPassed,
        terminal: limitsPassed === false ? "incomparable" : null,
        observedValue: limitsIsBool ? limits : null
      },
      {
        id: "unstable",
        label: "无效运行比例",
        observed: "基线 " + fmt.ratio(iBase) + " / 候选 " + fmt.ratio(iCand),
        threshold: "≤ " + fmt.ratio(iMax),
        passed: unstablePassed,
        terminal: null,
        observedValue: { base: iBase, cand: iCand, max: iMax }
      },
      {
        id: "budget_exceeded",
        label: "执行与搜索预算",
        observed: budgetReason === null ? DASH : budgetReason,
        threshold: "均未超限",
        passed: budgetPassed,
        terminal: budgetPassed === false ? "budget_exceeded" : null,
        observedValue: {
          execution_exceeded: execExceeded,
          overhead_exceeded: overheadExceeded,
          reason: budgetReason
        }
      },
      {
        id: "solved_dropped",
        label: "通过题数是否下降",
        observed: "基线 " + (solvedBase === null ? DASH : solvedBase) +
          " → 候选 " + (solvedCand === null ? DASH : solvedCand) +
          "（k = " + (k === null ? DASH : k) + "）",
        threshold: "候选 ≥ 基线 − k",
        passed: solvedPassed,
        terminal: solvedPassed === false ? "solved_dropped" : null,
        observedValue: { base: solvedBase, cand: solvedCand, k: k }
      },
      {
        id: "cost_not_lower",
        label: "单题成本是否更低",
        observed: "$" + fmt.usd(cpsBase) + " → $" + fmt.usd(cpsCand),
        threshold: "候选 ≤ 基线",
        passed: costPassed,
        terminal: costPassed === false ? "cost_not_lower" : null,
        observedValue: { base: cpsBase, cand: cpsCand }
      },
      {
        id: "adopt",
        label: "结论",
        observed: verdict === null ? DASH : verdict,
        threshold: DASH,
        passed: verdict === "adopt",
        terminal: verdict === "adopt" ? null : "adopt",
        observedValue: verdict
      }
    ];

    return s3;
  }

  /* ==========================================================================
   * B5 谱系 / 归档 / 快照
   * ========================================================================== */

  function genKey(g) {
    return isNum(g) ? g : Infinity;      // 未采纳候选（generation = null）排最后
  }

  function buildB5(lineage, variants, archives, snapshots) {
    var byId = {};
    var i;
    for (i = 0; i < variants.length; i++) {
      var v = variants[i];
      if (isObj(v) && typeof v.id === "string") byId[v.id] = v;
    }

    var linfos = [];
    for (i = 0; i < lineage.length; i++) {
      var item = lineage[i];
      if (!isObj(item)) continue;
      var vv = typeof item.genome_id === "string" ? byId[item.genome_id] : null;
      linfos.push({
        genomeId: item.genome_id,
        short: fmt.shortId(item.genome_id),
        switches: vv && isObj(vv.switches) ? vv.switches : null,
        runtime: vv && isObj(vv.runtime) ? vv.runtime : null,
        runtimeId: vv && isObj(vv.runtime) ? strOrNull(vv.runtime.runtime_id) : null,
        adopted: vv && typeof vv.adopted === "boolean" ? vv.adopted : item.adopted === true,
        generation: numOrNull(item.generation),
        archiveId: strOrNull(item.archive_id),
        parentShort: item.parent_id ? fmt.shortId(item.parent_id) : null
      });
    }
    linfos.sort(function (a, b) {
      var d = genKey(a.generation) - genKey(b.generation);
      return d !== 0 ? d : String(a.genomeId).localeCompare(String(b.genomeId));
    });

    var arcInfos = [];
    for (i = 0; i < archives.length; i++) {
      var a = archives[i];
      if (!isObj(a)) continue;
      arcInfos.push({
        archiveId: a.archive_id,
        generation: numOrNull(a.generation),
        note: strOrNull(a.note),
        short: fmt.shortId(a.genome_id),
        snapshotId: strOrNull(a.snapshot_id),
        adoptedAt: strOrNull(a.adopted_at)
      });
    }
    arcInfos.sort(function (x, y) { return genKey(x.generation) - genKey(y.generation); });

    var snapInfos = [];
    for (i = 0; i < snapshots.length; i++) {
      var s = snapshots[i];
      if (!isObj(s)) continue;
      snapInfos.push({
        snapshotId: s.snapshot_id,
        generation: numOrNull(s.generation),
        treeHash: strOrNull(s.tree_hash),
        treeHashShort: typeof s.tree_hash === "string" ? s.tree_hash.slice(0, 12) : null,
        fileCount: intOrNull(s.file_count),
        roots: isArr(s.roots) ? s.roots.slice() : []
      });
    }
    snapInfos.sort(function (x, y) { return genKey(x.generation) - genKey(y.generation); });

    var adoptedCount = 0;
    for (i = 0; i < linfos.length; i++) if (linfos[i].adopted) adoptedCount += 1;

    return {
      lineage: linfos,
      adoptedCount: adoptedCount,
      archiveCount: arcInfos.length,
      snapshotCount: snapInfos.length,
      archives: arcInfos,
      snapshots: snapInfos
    };
  }

  /* ==========================================================================
   * holdout（聚合口径）
   * ========================================================================== */

  function buildHoldout(reportPayload, decision, warnings, variants) {
    var out = {
      available: false,
      base: { solved: null, total: null, cps: null, costTotal: null },
      cand: { solved: null, total: null, cps: null, costTotal: null },
      runsTotal: { count: null, solved: null, costUsd: null, wallMs: null, byStatus: null },
      /* 归属可见：候选侧到底是哪个 genome 在跑、它的 switches 与门禁候选是否一致。
         这里只做标注，不改变任何数字。 */
      attribution: { candShort: null, baseShort: null, sameSwitches: null, note: null },
      // 扁平别名：渲染层直接 data-bind，不把嵌套结构写进 HTML
      baseSolved: null,
      baseTotal: null,
      candSolved: null,
      candTotal: null
    };

    var results = reportPayload && isArr(reportPayload.results) ? reportPayload.results : [];
    var totals = reportPayload && isObj(reportPayload.report_runs_total)
      ? reportPayload.report_runs_total
      : null;

    if (totals) {
      out.runsTotal = {
        count: intOrNull(totals.count),
        solved: intOrNull(totals.solved),
        costUsd: numOrNull(totals.cost_usd),
        wallMs: numOrNull(totals.wall_ms),
        byStatus: isObj(totals.by_status) ? totals.by_status : null
      };
    }

    if (results.length === 0) return out;

    var genomeBase = decision ? strOrNull(decision.baseline_genome_id) : null;
    var genomeCand = decision ? strOrNull(decision.candidate_genome_id) : null;
    var pair = pickPair(results, genomeBase, genomeCand, "holdout");

    if (pair.inferred && genomeBase && genomeCand) {
      warnings.push("holdout 报告结果与门禁 genome 未对齐，按 holdout 成本降序推断基线/候选");
    }
    if (pair.reason === "insufficient") return out;

    var b = scoreBlock(pair.base, "holdout");
    var c = scoreBlock(pair.cand, "holdout");
    if (!b || !c) return out;

    out.available = true;
    out.base = {
      solved: intOrNull(b.solved),
      total: intOrNull(b.total),
      cps: numOrNull(b.cost_per_solved_usd),
      costTotal: numOrNull(b.cost_total_usd)
    };
    out.cand = {
      solved: intOrNull(c.solved),
      total: intOrNull(c.total),
      cps: numOrNull(c.cost_per_solved_usd),
      costTotal: numOrNull(c.cost_total_usd)
    };
    out.cpsDropPct = dropPct(out.base.cps, out.cand.cps);
    out.baseSolved = out.base.solved;
    out.baseTotal = out.base.total;
    out.candSolved = out.cand.solved;
    out.candTotal = out.cand.total;

    out.attribution = buildAttribution(
      pair.base ? pair.base.genome_id : null,
      pair.cand ? pair.cand.genome_id : null,
      genomeCand,
      variants
    );

    return out;
  }

  /* ==========================================================================
   * holdout 归属：report 结果用的是哪个 genome，它的 switches 与门禁候选是否同一套
   *   —— report 侧常常是另一个 genome（候选的 fixture 版本），页面必须说出来。
   * ========================================================================== */

  function variantSwitches(variants, genomeId) {
    if (!isArr(variants) || typeof genomeId !== "string") return null;
    for (var i = 0; i < variants.length; i++) {
      var v = variants[i];
      if (isObj(v) && v.id === genomeId && isObj(v.switches)) return v.switches;
    }
    return null;
  }

  function sameShallow(a, b) {
    if (!isObj(a) || !isObj(b)) return null;
    var k;
    var na = 0;
    var nb = 0;
    for (k in a) if (Object.prototype.hasOwnProperty.call(a, k)) na += 1;
    for (k in b) if (Object.prototype.hasOwnProperty.call(b, k)) nb += 1;
    if (na !== nb) return false;
    for (k in a) {
      if (!Object.prototype.hasOwnProperty.call(a, k)) continue;
      if (!Object.prototype.hasOwnProperty.call(b, k)) return false;
      var va = a[k];
      var vb = b[k];
      if (isArr(va) && isArr(vb)) {
        if (va.length !== vb.length) return false;
        for (var i = 0; i < va.length; i++) {
          if (String(va[i]) !== String(vb[i])) return false;
        }
      } else if (isObj(va) && isObj(vb)) {
        if (sameShallow(va, vb) !== true) return false;
      } else if (va !== vb) {
        return false;
      }
    }
    return true;
  }

  function buildAttribution(baseGid, candGid, gateCandGid, variants) {
    var candShort = typeof candGid === "string" ? fmt.shortId(candGid) : null;
    var baseShort = typeof baseGid === "string" ? fmt.shortId(baseGid) : null;
    var candSw = variantSwitches(variants, candGid);
    var gateSw = variantSwitches(variants, gateCandGid);
    var same = sameShallow(candSw, gateSw);
    var note;

    if (candShort === null) {
      note = "候选侧 report 结果未标注 genome";
    } else if (same === true) {
      note = "候选侧来自 report 结果（genome " + candShort + "，与门禁候选 switches 相同）";
    } else if (same === false) {
      note = "候选侧 genome " + candShort + " 的 switches 与门禁候选不同，需人工核对";
    } else {
      note = "候选侧 genome " + candShort + "：switches 无法核对（variants 缺该 genome）";
    }

    return { candShort: candShort, baseShort: baseShort, sameSwitches: same, note: note };
  }

  /* ==========================================================================
   * build(view)：主入口
   * ========================================================================== */

  function build(view) {
    var errors = [];
    var warnings = [];
    var v = isObj(view) ? view : {};

    var scenario = isObj(v.scenario) ? v.scenario : null;
    var runsPayload = v.runs;
    var runs = runsPayload && isArr(runsPayload.runs) ? runsPayload.runs : null;
    var searchResults = v.search_results && isArr(v.search_results.results)
      ? v.search_results.results
      : [];
    var reportPayload = v.report_results || null;
    var decisions = v.gate && isArr(v.gate.decisions) ? v.gate.decisions : [];
    var variants = v.variants && isArr(v.variants.variants) ? v.variants.variants : [];
    var lineage = v.archive && isArr(v.archive.lineage) ? v.archive.lineage : [];
    var archives = v.archive && isArr(v.archive.archives) ? v.archive.archives : [];
    var snapshots = v.snapshots && isArr(v.snapshots.snapshots) ? v.snapshots.snapshots : [];

    // ---- 硬前置：没有 scenario 就等于没读到 bundle
    if (!scenario) {
      errors.push("未读取到数据：data/bundle.js 未加载");
    }

    // ---- 门禁判定
    var decision = pickDecision(decisions);
    if (!decision) {
      errors.push("没有门禁判定记录，无法展示两次运行对照");
    }

    // ---- meta
    var gen = pickGeneratedAt(v, runs, decision);
    var searchSet = scenario && isObj(scenario.task_sets) && isArr(scenario.task_sets.search)
      ? scenario.task_sets.search
      : null;
    var meta = {
      scenarioId: scenario ? strOrNull(scenario.scenario_id) : null,
      scenarioVersion: scenario ? strOrNull(scenario.scenario_version) : null,
      shellId: scenario && isObj(scenario.shell) ? strOrNull(scenario.shell.shell_id) : null,
      shellVersion: scenario && isObj(scenario.shell) ? strOrNull(scenario.shell.shell_version) : null,
      revision: isObj(v.index) && strOrNull(v.index.revision) ? v.index.revision : null,
      generatedAt: gen.at,
      generatedAtSource: gen.source,
      // 标题里的「N 道题」只许来自 scenario，不许写死
      searchTaskCount: searchSet ? searchSet.length : null,
      provenanceBasis: runs ? "runs" : (scenario && isObj(scenario.provenance_counts) ? "scenario" : "none")
    };

    // ---- provenance（不硬编码 1 live）
    var provenance = countProvenance(runs, scenario, reportPayload);

    // ---- holdout 聚合契约自检（build 内部也调一次）
    var aggregateAudit = assertAggregateOnly(reportPayload);
    if (!aggregateAudit.ok) {
      warnings.push("holdout 出现逐题字段，违反导出契约");
    }

    // ---- 各屏
    var s1 = buildS1(decision, searchResults, runs, warnings, meta.searchTaskCount);
    var s2 = buildS2(decision, runs, errors);
    var s3 = buildS3(decision, s1);
    var b5 = buildB5(lineage, variants, archives, snapshots);
    var holdout = buildHoldout(reportPayload, decision, warnings, variants);

    return {
      ok: errors.length === 0,
      errors: errors,
      warnings: warnings,
      meta: meta,
      provenance: provenance,
      s1: s1,
      s2: s2,
      s3: s3,
      b5: b5,
      holdout: holdout,
      audit: {
        aggregateOk: aggregateAudit.ok,
        aggregateHits: aggregateAudit.hits
      },
      fmt: fmt
    };
  }

  /* ==========================================================================
   * 导出
   * ========================================================================== */

  return {
    build: build,
    fmt: fmt,
    assertAggregateOnly: assertAggregateOnly,
    // 常量也给出去，渲染层想引用 ADR 口径时不用自己抄
    SCAN_CLAIM: SCAN_CLAIM
  };
});
