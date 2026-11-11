#!/usr/bin/env node
/*!
 * molt · web/page/check.mjs —— P0 展示页验收脚本（W-04）
 *
 * 一条命令验证：页面与数据面一致、口径纪律、静态约束、聚合边界、负路径。
 *   用法：node check.mjs [--data-dir <path>]
 * 退出码：全 PASS → 0；任意 [FAIL] → 1；参数/加载错误 → 2。
 *
 * 纪律：
 *   - 只用 node 标准库，零 npm 依赖，不跑 pytest / molt CLI。
 *   - 不读 cwd 定位自身目录：一律基于 import.meta.url（from /tmp 也能跑）。
 *   - 断言的是「行为边界」，不是为了通过而放宽：口径类断言全部写成硬 FAIL，
 *     只有「数据面自身退化」这类环境问题才降级为 [WARN] DEGRADED。
 *   - 反向验证：--data-dir 指向复制品并改坏一处，应出现对应 FAIL（见交付说明）。
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const PAGE_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_DIR = path.resolve(PAGE_DIR, "..", "..");
const require = createRequire(import.meta.url);

/* ==========================================================================
 * 0. 参数与加载
 * ========================================================================== */

function parseArgs(argv) {
  const out = {
    dataDir: path.join(PAGE_DIR, "..", "data"),
    /* 反向验证专用：指向 /tmp 下被改坏的 render.js 副本，证明断言不是空转。
       主运行不传该参数 → 永远测仓库里的 render.js。 */
    renderModule: null,
    help: false
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--data-dir" || a === "--data") {
      const v = argv[i + 1];
      if (!v) {
        console.error("--data-dir 需要一个路径参数");
        process.exit(2);
      }
      out.dataDir = path.resolve(process.cwd(), v);
      i += 1;
    } else if (a === "--render-module") {
      const v = argv[i + 1];
      if (!v) {
        console.error("--render-module 需要一个路径参数");
        process.exit(2);
      }
      out.renderModule = path.resolve(process.cwd(), v);
      i += 1;
    } else if (a === "-h" || a === "--help") {
      out.help = true;
    } else {
      console.error("未知参数：" + a);
      process.exit(2);
    }
  }
  out.dataDir = path.resolve(out.dataDir);
  return out;
}

const ARGS = parseArgs(process.argv.slice(2));
if (ARGS.help) {
  console.log("用法: node check.mjs [--data-dir <web/data 的路径>] [--render-module <render.js 的副本>]");
  console.log("  --render-module 仅供反向验证（指向 /tmp 下被改坏的副本），主运行不要传。");
  process.exit(0);
}

function die(msg) {
  console.error("[FAIL] LOAD " + msg);
  console.error("RESULT: FAIL");
  process.exit(2);
}

function readJson(file) {
  const raw = fs.readFileSync(file, "utf8");
  try {
    return JSON.parse(raw);
  } catch (err) {
    die("JSON 解析失败：" + file + " → " + err.message);
    return null;
  }
}

/* 页面文件（被测静态资产） */
const STATIC_NAMES = ["index.html", "app.css", "model.js", "render.js", "app.js"];
const SRC = {};
for (const name of STATIC_NAMES) {
  const file = path.join(PAGE_DIR, name);
  if (!fs.existsSync(file)) die("页面文件缺失：" + file);
  SRC[name] = fs.readFileSync(file, "utf8");
}

/* 数据面（12 键 view；events 无 .json 文件，model 不消费，传空对象） */
const DATA_FILES = {
  scenario: "scenario.json",
  runs: "runs.json",
  search_results: "search_results.json",
  report_results: "report_results.json",
  gate: "gate.json",
  variants: "variants.json",
  archive: "archive.json",
  snapshots: "snapshots.json",
  audit: "audit.json",
  proposals: "proposals.json",
  redaction: "redaction.json"
};

const DATA = {};
for (const key of Object.keys(DATA_FILES)) {
  const file = path.join(ARGS.dataDir, DATA_FILES[key]);
  if (!fs.existsSync(file)) die("数据文件缺失：" + file);
  DATA[key] = readJson(file);
}

const INDEX_JSON = (() => {
  const file = path.join(ARGS.dataDir, "index.json");
  if (!fs.existsSync(file)) return null;
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    return null;
  }
})();

const VIEW = {
  scenario: DATA.scenario,
  runs: DATA.runs,
  search_results: DATA.search_results,
  report_results: DATA.report_results,
  gate: DATA.gate,
  variants: DATA.variants,
  archive: DATA.archive,
  snapshots: DATA.snapshots,
  audit: DATA.audit,
  proposals: DATA.proposals,
  redaction: DATA.redaction,
  events: {}
};

let MOLT_MODEL = null;
let MOLT_RENDER = null;
try {
  MOLT_MODEL = require(path.join(PAGE_DIR, "model.js"));
  MOLT_RENDER = require(ARGS.renderModule || path.join(PAGE_DIR, "render.js"));
} catch (err) {
  die("UMD 模块加载失败：" + err.message);
}

let M = null;
try {
  M = MOLT_MODEL.build(VIEW);
} catch (err) {
  die("model.build(view) 抛错：" + err.message);
}

/* ==========================================================================
 * 0.5 最小 DOM 桩（零依赖）—— 让 render.js 真跑一遍
 *
 * 为什么需要：W-07 修的全是「渲染后的口径」，只看 model 输出和 HTML 静态文本
 * 都验证不到。这里按 index.html 原文建树（id / class / data-bind 从 HTML 抽取，
 * 不手抄槽位表），render.js 用到的 DOM API 全部实现：
 *   getElementById / querySelector(All) / createElement / textContent /
 *   appendChild / removeChild / firstChild / className / classList.add /
 *   setAttribute / getAttribute / dataset / style / hidden
 * 只读 index.html，不写任何文件。
 * ========================================================================== */

function makeDoc(html) {
  function mkNode(tag) {
    const node = {
      tagName: String(tag).toUpperCase(),
      attrs: {},
      children: [],
      parentNode: null,
      style: {},
      dataset: {},
      hidden: false,
      _text: ""
    };

    Object.defineProperty(node, "id", {
      get() { return this.attrs.id || ""; },
      set(v) { this.attrs.id = String(v); docIndex[this.attrs.id] = this; }
    });
    Object.defineProperty(node, "className", {
      get() { return this.attrs["class"] || ""; },
      set(v) { this.attrs["class"] = String(v); }
    });
    Object.defineProperty(node, "firstChild", { get() { return this.children[0]; } });
    Object.defineProperty(node, "childNodes", { get() { return this.children; } });
    Object.defineProperty(node, "textContent", {
      get() {
        let out = this._text;
        for (const c of this.children) out += typeof c === "string" ? c : c.textContent;
        return out;
      },
      set(v) {
        this.children = [];
        this._text = String(v);
      }
    });

    node.setAttribute = function (k, v) {
      this.attrs[k] = String(v);
      if (k === "id") docIndex[v] = this;
    };
    node.getAttribute = function (k) {
      return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
    };
    node.appendChild = function (child) {
      child.parentNode = this;
      this.children.push(child);
      return child;
    };
    node.removeChild = function (child) {
      const i = this.children.indexOf(child);
      if (i === -1) throw new Error("removeChild: 不是子节点");
      this.children.splice(i, 1);
      if (child && typeof child === "object") child.parentNode = null;
      return child;
    };
    node.classList = {
      add(c) {
        const cls = String(node.attrs["class"] || "").split(/\s+/).filter(Boolean);
        if (cls.indexOf(c) === -1) cls.push(c);
        node.attrs["class"] = cls.join(" ");
      },
      contains(c) {
        return String(node.attrs["class"] || "").split(/\s+/).indexOf(c) !== -1;
      }
    };
    return node;
  }

  const docIndex = Object.create(null);
  const docEl = mkNode("html");
  docIndex.html = docEl;

  const VOID = {
    br: 1, img: 1, input: 1, meta: 1, link: 1, hr: 1, source: 1, wbr: 1,
    area: 1, base: 1, col: 1, embed: 1, param: 1, track: 1
  };
  const stack = [docEl];

  function attrMap(tagInner) {
    const out = {};
    const re = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*"([^"]*)")?/g;
    let m;
    while ((m = re.exec(tagInner))) out[m[1]] = m[2] === undefined ? "" : m[2];
    return out;
  }

  const tokens = String(html).match(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g) || [];
  for (const tok of tokens) {
    if (tok.startsWith("<!--") || tok.startsWith("<!") || tok.startsWith("<?")) continue;

    if (tok.startsWith("</")) {
      const name = tok.slice(2, -1).trim().toUpperCase();
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName === name) { stack.length = i; break; }
      }
      continue;
    }

    if (tok.startsWith("<")) {
      const inner = tok.slice(1, -1).trim();
      const selfClose = inner.endsWith("/");
      const name = inner.split(/[\s/>]/)[0].toLowerCase();
      const node = mkNode(name);
      const attrs = attrMap(inner);
      for (const k of Object.keys(attrs)) node.setAttribute(k, attrs[k]);
      if (Object.prototype.hasOwnProperty.call(attrs, "hidden")) node.hidden = true;
      const top = stack[stack.length - 1];
      top.appendChild(node);
      if (!VOID[name] && !selfClose) stack.push(node);
      continue;
    }

    stack[stack.length - 1].children.push(tok);
  }

  function walk(node, fn) {
    for (const c of node.children) {
      if (typeof c === "string") continue;
      fn(c);
      walk(c, fn);
    }
  }

  function matchSel(sel) {
    const s = String(sel).trim();
    let m;
    if ((m = /^#([\w.:-]+)$/.exec(s))) return (n) => n.id === m[1];
    if ((m = /^\[([\w:-]+)\]$/.exec(s))) return (n) => n.getAttribute(m[1]) !== null;
    if ((m = /^\[([\w:-]+)="([^"]*)"\]$/.exec(s))) return (n) => n.getAttribute(m[1]) === m[2];
    if ((m = /^([\w-]*)\.([\w-]+)$/.exec(s))) {
      const tag = m[1] ? m[1].toUpperCase() : null;
      return (n) => (!tag || n.tagName === tag) && n.classList.contains(m[2]);
    }
    if ((m = /^([\w-]+)$/.exec(s))) return (n) => n.tagName === m[1].toUpperCase();
    throw new Error("DOM 桩不支持的选择器：" + sel);
  }

  function queryAll(sel) {
    const pred = matchSel(sel);
    const out = [];
    walk(docEl, (n) => { if (pred(n)) out.push(n); });
    return out;
  }

  return {
    documentElement: docEl,
    /* 与浏览器一致：已从树上摘掉的节点不再按 id 命中 */
    getElementById(id) {
      const target = docIndex[id];
      if (!target) return null;
      if (target === docEl) return docEl;
      let found = null;
      walk(docEl, (n) => { if (n === target) found = n; });
      return found;
    },
    createElement(tag) { return mkNode(tag); },
    querySelectorAll: queryAll,
    querySelector(sel) { return queryAll(sel)[0] || null; },
    /* 断言专用（非 DOM 标准）：递归按谓词查 */
    _findAll(node, pred) {
      const out = [];
      walk(node, (n) => { if (pred(n)) out.push(n); });
      return out;
    }
  };
}

/** 按 index.html 建一棵全新树并渲染给定 model；返回 {doc, out} */
function renderInto(model) {
  const doc = makeDoc(SRC["index.html"]);
  const out = MOLT_RENDER.render(VIEW, { doc: doc, model: model });
  return { doc: doc, out: out };
}

const RENDERED = renderInto(M);
const PAGE_DOC = RENDERED.doc;
const ROUT = RENDERED.out;

/* ==========================================================================
 * 1. 断言框架
 * ========================================================================== */

const GROUPS = [];
let subPass = 0;
let subFail = 0;
let subWarn = 0;

function show(v) {
  if (v === undefined) return "undefined";
  if (typeof v === "string") return JSON.stringify(v);
  if (typeof v === "number" || typeof v === "boolean" || v === null) return String(v);
  try {
    return JSON.stringify(v);
  } catch (err) {
    return String(v);
  }
}

function group(id, title) {
  const g = { id, title, subs: [], failed: false };

  function add(status, label, expected, actual) {
    g.subs.push({ status, label, expected, actual });
    if (status === "FAIL") {
      g.failed = true;
      subFail += 1;
    } else if (status === "PASS") {
      subPass += 1;
    } else {
      subWarn += 1;
    }
  }

  g.ok = (cond, label, expected, actual) => {
    add(cond ? "PASS" : "FAIL", label, expected, actual);
    return cond === true;
  };
  g.eq = (actual, expected, label) => g.ok(actual === expected, label, expected, actual);
  g.eqJson = (actual, expected, label) =>
    g.ok(JSON.stringify(actual) === JSON.stringify(expected), label, expected, actual);
  g.near = (actual, expected, tol, label) =>
    g.ok(
      typeof actual === "number" && Math.abs(actual - expected) < tol,
      label,
      expected + " ±" + tol,
      actual
    );
  g.warn = (label, detail) => add("WARN", label, undefined, detail);

  GROUPS.push(g);
  return g;
}

/* ==========================================================================
 * 2. 小工具
 * ========================================================================== */

/** 字面量出现次数（非重叠） */
function countLit(text, lit) {
  return text.split(lit).length - 1;
}

/** 正则出现次数 */
function countRe(text, re) {
  const m = text.match(re);
  return m ? m.length : 0;
}

function htmlSlots(html) {
  return [...html.matchAll(/data-bind\s*=\s*"([^"]*)"/g)].map((m) => m[1]);
}

const S1_BINDS = [
  "s1.scanBaseCost",
  "s1.scanCandCost",
  "s1.scanCostDropPct",
  "s1.scanBaseWall",
  "s1.scanCandWall",
  "s1.scanWallDropPct"
];

/* ---- 渲染断言工具 ---- */

const DASH = (MOLT_RENDER && MOLT_RENDER.DASH) || "—";

/** 节点可见文本，空白折叠（HTML 里的缩进不该影响断言） */
function textOf(node) {
  if (!node) return "";
  return String(node.textContent).replace(/\s+/g, " ").trim();
}

/** 包含 lit 的「最小容器」：自身含 lit，但没有任何子元素还含 lit。
 *  = 语义上的「同一段落」；用它断言「这个数字有没有和那个数字同段出现」。 */
function minimalContainers(root, lit) {
  const out = [];
  const seen = new Set();

  function visit(node) {
    for (const c of node.children) {
      if (typeof c === "string") continue;
      if (String(c.textContent).indexOf(lit) === -1) { visit(c); continue; }
      let deeper = false;
      for (const g of c.children) {
        if (typeof g !== "string" && String(g.textContent).indexOf(lit) !== -1) {
          deeper = true;
          break;
        }
      }
      if (!deeper && !seen.has(c)) { seen.add(c); out.push(c); }
      visit(c);
    }
  }

  visit(root);
  return out;
}

/** 文档顺序：DFS 收集所有元素节点（用于断言「谁在谁之前」） */
function docOrder(doc) {
  const out = [];
  (function walk(node) {
    for (const c of node.children) {
      if (typeof c === "string") continue;
      out.push(c);
      walk(c);
    }
  })(doc.documentElement);
  return out;
}

/** 数字的「语义容器」：表格单元格上溯到所在行（一行才算同一段），其余原样 */
function noteContainer(node) {
  let n = node;
  if (n && n.tagName === "TD" && n.parentNode) n = n.parentNode;
  return n;
}

/** 与 render.js 内部 pick 同语义：中途缺失返回 null */
function pickPath(obj, p) {
  let node = obj;
  const parts = String(p).split(".");
  for (const part of parts) {
    if (node === null || node === undefined || typeof node !== "object") return null;
    node = node[part];
  }
  return node === undefined ? null : node;
}

/** 用 BINDINGS 的 {path|get + f} 复算某个槽位「应该」显示什么文本 */
function bindingText(key, model) {
  const spec = MOLT_RENDER.BINDINGS[key];
  if (!spec) return { text: null, spec: null };
  const raw = typeof spec.get === "function" ? spec.get(model) : pickPath(model, spec.path);
  if (raw === null || raw === undefined) return { text: DASH, spec: spec };
  if (typeof raw === "number" && !isFinite(raw)) return { text: DASH, spec: spec };
  return { text: String(spec.f(raw, model.fmt)), spec: spec };
}

/** 门禁链每一步的徽章文本（通过 / 停在此步 / 未执行 / 未判定） */
function chainBadges(doc) {
  const list = doc.getElementById("gate-chain");
  if (!list) return [];
  const out = [];
  for (const li of list.children) {
    if (typeof li === "string") continue;
    const badge = doc._findAll(li, (n) => n.classList.contains("step-badge"))[0];
    out.push(badge ? textOf(badge) : null);
  }
  return out;
}

/* ==========================================================================
 * A. 静态约束
 * ========================================================================== */

/* ---- A1：五个页面文件不得出现任何 http(s) 字面量（含注释里的来源引用） ---- */
{
  const g = group("A1", "页面文件零外链字面量");
  for (const name of STATIC_NAMES) {
    const n = countRe(SRC[name], /https?:\/\//g);
    g.eq(n, 0, name + ": https?:// 命中数");
  }
}

/* ---- A2：无 module / 无 fetch / 无 XHR / 无 innerHTML / 无 ES2020 语法 ---- */
{
  const g = group("A2", "无网络、无 HTML 拼接、无 ES2020 语法");

  const idx = SRC["index.html"];
  g.eq(countRe(idx, /type\s*=\s*["']module["']/g), 0, 'index.html: type="module" 命中数');
  g.eq(countLit(idx, "fetch("), 0, "index.html: fetch( 命中数");
  g.eq(countLit(idx, "XMLHttpRequest"), 0, "index.html: XMLHttpRequest 命中数");

  for (const name of ["render.js", "app.js"]) {
    const t = SRC[name];
    g.eq(countLit(t, "innerHTML"), 0, name + ": innerHTML 命中数");
    g.eq(countLit(t, "fetch("), 0, name + ": fetch( 命中数");
    g.eq(countLit(t, "?."), 0, name + ": ?. 命中数");
    g.eq(countLit(t, "??"), 0, name + ": ?? 命中数");
  }

  const mdl = SRC["model.js"];
  g.eq(countLit(mdl, "innerHTML"), 0, "model.js: innerHTML 命中数");
  g.eq(countLit(mdl, "fetch("), 0, "model.js: fetch( 命中数");
}

/* ---- A3：index.html 引用的相对脚本/样式文件真实存在 ---- */
{
  const g = group("A3", "index.html 引用的本地资产存在");
  const html = SRC["index.html"];
  const refs = [];

  for (const m of html.matchAll(/<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>/gi)) {
    refs.push({ kind: "script", url: m[1] });
  }
  for (const m of html.matchAll(/<link\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>/gi)) {
    refs.push({ kind: "link", url: m[1] });
  }

  g.ok(refs.length >= 4, "引用条目数（4 个 script + 1 个 stylesheet）", ">= 4", refs.length);

  for (const r of refs) {
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(r.url) || r.url.startsWith("//")) {
      g.ok(false, r.kind + " " + r.url + " 不得为外部绝对地址", "相对路径", r.url);
      continue;
    }
    const abs = path.resolve(PAGE_DIR, r.url);
    g.ok(fs.existsSync(abs), r.kind + " " + r.url + " 解析后存在", true, fs.existsSync(abs));
  }
}

/* ---- A4：data-bind 槽位 ⊆ render.js BINDINGS，且槽位数 >= 24 ---- */
let SLOTS = [];
let BINDING_KEYS = [];
{
  const g = group("A4", "data-bind 槽位与 BINDINGS 对齐");

  SLOTS = htmlSlots(SRC["index.html"]);
  g.ok(SLOTS.length >= 24, "data-bind 槽位数", ">= 24", SLOTS.length);

  if (MOLT_RENDER && MOLT_RENDER.BINDINGS && typeof MOLT_RENDER.BINDINGS === "object") {
    BINDING_KEYS = Object.keys(MOLT_RENDER.BINDINGS);
  } else {
    /* UMD 未导出 BINDINGS → 回退正则抽 `"xxx": {` 形式的键 */
    BINDING_KEYS = [
      ...new Set([...SRC["render.js"].matchAll(/"([^"\n]+)"\s*:\s*\{/g)].map((m) => m[1]))
    ];
  }
  g.ok(BINDING_KEYS.length > 0, "render.js BINDINGS 键集合非空", "> 0", BINDING_KEYS.length);

  const keySet = new Set(BINDING_KEYS);
  const unknown = [...new Set(SLOTS.filter((s) => !keySet.has(s)))];
  g.eqJson(unknown, [], "index.html 槽位 ⊆ BINDINGS（未知槽位）");

  const slotSet = new Set(SLOTS);
  const unused = BINDING_KEYS.filter((k) => !slotSet.has(k));
  if (unused.length > 0) g.warn("BINDINGS 有未被 HTML 使用的键（死代码，不阻断）", unused);
  else g.ok(true, "BINDINGS 无未使用键", 0, 0);
}

/* ==========================================================================
 * B. 数据一致性（model 实算 vs web/data 原值）
 * ========================================================================== */

const RUNS = Array.isArray(DATA.runs.runs) ? DATA.runs.runs : [];
const RESULTS = Array.isArray(DATA.search_results.results) ? DATA.search_results.results : [];
const DECISIONS = Array.isArray(DATA.gate.decisions) ? DATA.gate.decisions : [];
const LAST = DECISIONS.length > 0 ? DECISIONS[DECISIONS.length - 1] : null;
const COUNTS = DATA.scenario.provenance_counts || {};

/* ---- B5：provenance 三数与 scenario 一致，且 total === runs.length ---- */
{
  const g = group("B5", "provenance 与 scenario 一致");

  g.ok(M.ok === true, "model.build(view).ok", true, M.ok);
  g.eqJson(M.errors, [], "model.build(view).errors");

  g.eq(M.provenance.basis, "runs", "provenance 口径依据");
  g.eq(M.provenance.total, RUNS.length, "provenance.total === runs.length");

  /* 硬断言：model 的三数 === 独立扫 runs 得到的 kind 计数（形态无关） */
  const kindCount = { live: 0, replay: 0, fixture: 0 };
  for (const r of RUNS) {
    const k = r && r.provenance ? r.provenance.kind : null;
    if (kindCount[k] !== undefined) kindCount[k] += 1;
  }
  g.eq(M.provenance.live, kindCount.live, "provenance.live === Σ runs[kind=live]");
  g.eq(M.provenance.replay, kindCount.replay, "provenance.replay === Σ runs[kind=replay]");
  g.eq(M.provenance.fixture, kindCount.fixture, "provenance.fixture === Σ runs[kind=fixture]");

  const sumKinds = M.provenance.live + M.provenance.replay + M.provenance.fixture;
  g.eq(sumKinds, RUNS.length, "live + replay + fixture === runs.length（无漏计 kind）");

  /* scenario.provenance_counts 是导出时的声明：只在它与 runs 一致时交叉核对。
     现场跑完两条 live 扫描后，scenario.json 可能还没重导出（声明仍是 1 live），
     此时**不该**把模型判红 —— 记一条 DEGRADED，说明数据面自身不一致。 */
  const declared = {
    live: COUNTS.live === undefined ? null : COUNTS.live,
    replay: COUNTS.replay === undefined ? null : COUNTS.replay,
    fixture: COUNTS.fixture === undefined ? null : COUNTS.fixture
  };
  const declaredMatches = declared.live === kindCount.live &&
    declared.replay === kindCount.replay &&
    declared.fixture === kindCount.fixture;

  if (declaredMatches) {
    g.eq(M.provenance.live, COUNTS.live, "provenance.live === scenario.provenance_counts.live");
    g.eq(M.provenance.replay, COUNTS.replay, "provenance.replay === scenario.provenance_counts.replay");
    g.eq(M.provenance.fixture, COUNTS.fixture, "provenance.fixture === scenario.provenance_counts.fixture");
  } else {
    g.warn(
      "DEGRADED scenario.provenance_counts 与 runs 实际 kind 计数不一致（数据面未重导出，形态已变），跳过声明侧交叉核对",
      { declared: declared, actual: kindCount }
    );
  }

  if (INDEX_JSON && INDEX_JSON.counts) {
    g.eq(INDEX_JSON.counts.runs, RUNS.length, "index.json counts.runs === runs.length");
    g.eq(INDEX_JSON.counts.search_results, RESULTS.length, "index.json counts.search_results === results.length");
  } else {
    g.warn("index.json 缺失，跳过导出计数交叉核对", path.join(ARGS.dataDir, "index.json"));
  }
}

/* ---- B6：solved 按 genome_id 匹配（不假设数组顺序） ---- */
const PAIR = (() => {
  const baseGid = LAST ? LAST.baseline_genome_id : null;
  const candGid = LAST ? LAST.candidate_genome_id : null;
  const base = RESULTS.find((r) => r && r.genome_id === baseGid) || null;
  const cand = RESULTS.find((r) => r && r.genome_id === candGid) || null;
  return { base, cand, matched: !!(base && cand), baseGid, candGid };
})();

{
  const g = group("B6", "通过题数与门禁 genome 对齐");

  const expectedSolved = Array.isArray(DATA.scenario.task_sets && DATA.scenario.task_sets.search)
    ? DATA.scenario.task_sets.search.length
    : 10;

  if (PAIR.matched) {
    const bs = PAIR.base.scores && PAIR.base.scores.search ? PAIR.base.scores.search : {};
    const cs = PAIR.cand.scores && PAIR.cand.scores.search ? PAIR.cand.scores.search : {};

    g.eq(M.s1.solvedBase, bs.solved, "s1.solvedBase === baseline result 的 scores.search.solved");
    g.eq(M.s1.solvedCand, cs.solved, "s1.solvedCand === candidate result 的 scores.search.solved");
    g.eq(bs.solved, 10, "baseline solved");
    g.eq(cs.solved, 10, "candidate solved");
    g.eq(bs.solved, expectedSolved, "baseline solved === scenario.task_sets.search.length");
    g.eq(M.s1.totalBase, bs.total, "s1.totalBase === baseline scores.search.total");
    g.eq(M.s1.totalCand, cs.total, "s1.totalCand === candidate scores.search.total");
    g.eq(M.s1.available, true, "s1.available");
  } else {
    g.warn(
      "DEGRADED 未能按 gate genome_id 匹配 search_results，改用 model 退化结果",
      { baseline_genome_id: PAIR.baseGid, candidate_genome_id: PAIR.candGid }
    );
    g.eq(M.s1.solvedBase, 10, "s1.solvedBase（退化路径）");
    g.eq(M.s1.solvedCand, 10, "s1.solvedCand（退化路径）");
    g.ok(
      Array.isArray(M.warnings) && M.warnings.some((w) => String(w).includes("成本降序")),
      "退化路径必须在 model.warnings 里留下可追溯提示",
      true,
      Array.isArray(M.warnings) ? M.warnings.length : null
    );
  }
}

/* ---- B7：gate 级 cps 与 search_results 一致（< 1e-12），降幅 ∈ [69.9, 70.1] ---- */
{
  const g = group("B7", "gate 级 cps 可复算");

  if (PAIR.matched) {
    const bs = PAIR.base.scores.search;
    const cs = PAIR.cand.scores.search;
    g.near(M.s1.cpsBase, bs.cost_per_solved_usd, 1e-12, "s1.cpsBase vs baseline cost_per_solved_usd");
    g.near(M.s1.cpsCand, cs.cost_per_solved_usd, 1e-12, "s1.cpsCand vs candidate cost_per_solved_usd");
  } else {
    g.warn("DEGRADED 未匹配 genome，cps 只做区间与量级校验", "见 B6");
    g.ok(
      typeof M.s1.cpsBase === "number" && typeof M.s1.cpsCand === "number",
      "s1.cpsBase / cpsCand 均为数字",
      "number",
      [typeof M.s1.cpsBase, typeof M.s1.cpsCand]
    );
    g.ok(M.s1.cpsCand < M.s1.cpsBase, "候选 cps < 基线 cps", true, M.s1.cpsCand < M.s1.cpsBase);
  }

  g.ok(
    typeof M.s1.cpsDropPct === "number" && M.s1.cpsDropPct >= 69.9 && M.s1.cpsDropPct <= 70.1,
    "s1.cpsDropPct ∈ [69.9, 70.1]",
    "[69.9, 70.1]",
    M.s1.cpsDropPct
  );
  /* 独立复算一遍降幅，避免只信 model 的 dropPct */
  const manual = Math.round((1 - M.s1.cpsCand / M.s1.cpsBase) * 1000) / 10;
  g.near(M.s1.cpsDropPct, manual, 1e-9, "s1.cpsDropPct 与手工复算一致");
}

/* ---- B8：s2.pairCount 与 gate 配对表条数/顺序一致 ---- */
{
  const g = group("B8", "逐题配对表边界");

  const pairs = LAST && Array.isArray(LAST.pairs) ? LAST.pairs : null;
  g.ok(pairs !== null, "gate.decisions[last].pairs 存在", "array", pairs === null ? null : "array");
  const list = pairs || [];

  g.eq(M.s2.pairCount, list.length, "s2.pairCount === gate.decisions[last].pairs.length");
  g.eq(M.s2.pairs.length, list.length, "s2.pairs 长度与配对表一致");

  const expectedOrder = list.map((p) => p && p.task_id).sort();
  const modelOrder = M.s2.pairs.map((p) => p && p.taskId);
  g.eqJson(modelOrder, expectedOrder, "s2.pairs 的 taskId 顺序 === [...pairs].map(task_id).sort()");

  const idxOk = M.s2.pairs.every((p, i) => p && p.index === i + 1);
  g.ok(idxOk, "s2.pairs[].index 从 1 连续递增", true, idxOk);

  /* 配对表任务集必须与 scenario 声明的 10 题搜索集一致（不多不少） */
  const declared = DATA.scenario.task_sets && DATA.scenario.task_sets.search;
  if (Array.isArray(declared)) {
    g.eqJson(
      [...new Set(modelOrder)].sort(),
      [...new Set(declared)].sort(),
      "配对表 taskId 集合 === scenario.task_sets.search"
    );
  }
}

/* ---- B9：扫描级口径纪律（本脚本核心） ---- */
const SCAN = M.s1 && M.s1.scan ? M.s1.scan : null;
const IS_ADR_SCAN = !(SCAN && SCAN.source === "data");
{
  const g = group("B9", "扫描级 / gate 级口径不混用");

  const scan = SCAN;
  const hasScan = g.ok(
    !!scan && !!scan.verified && !!scan.recomputed && !!scan.claim && !!scan.display,
    "s1.scan 结构完整（claim / recomputed / verified / display）",
    true,
    !!scan
  );

  if (hasScan) {
    const v = scan.verified;
    const rc = scan.recomputed;
    const d = scan.display;

    g.ok(
      scan.source === "adr" || scan.source === "data",
      "scan.source ∈ {adr, data}（两种数据形态之一）",
      "adr | data",
      scan.source
    );
    g.ok(
      [d.baseCostUsd, d.candCostUsd, d.baseWallMs, d.candWallMs].every((x) => typeof x === "number"),
      "display 的四项金额 / 墙钟都是数字（缺值不补 0）",
      "4 个 number",
      [d.baseCostUsd, d.candCostUsd, d.baseWallMs, d.candWallMs]
    );

    /* 两个口径的数字必须不同源：claim 候选成本 ≠ 数据面可复算的候选配对合计 */
    const F = M.fmt;
    g.eq(F.usd(scan.claim.candCostUsd), "0.009978", "claim 候选扫描成本格式化 === ADR 的 0.009978");
    if (typeof rc.candPairCostUsd === "number") {
      g.eq(F.usd(rc.candPairCostUsd), "0.010684", "数据面候选配对合计格式化 === 0.010684");
      g.ok(
        scan.claim.candCostUsd !== rc.candPairCostUsd,
        "claim.candCostUsd 与 recomputed.candPairCostUsd 不是同一个数（口径已分离）",
        true,
        scan.claim.candCostUsd !== rc.candPairCostUsd
      );
    } else {
      g.warn("本形态没有 demo_group 分组，recomputed.candPairCostUsd 为空（不参与复算）", null);
    }

    if (IS_ADR_SCAN) {
      /* ADR 形态（replay 回放数据面）：基线可复算，候选侧未导出 → 必须如实标 false */
      g.eq(v.baseCost, true, "verified.baseCost === true（基线成本可复算）");
      g.eq(v.baseWall, true, "verified.baseWall === true（基线墙钟可复算）");
      g.eq(v.candCost, false, "verified.candCost === false（候选成本不可复算，如实标注）");
      g.eq(v.candWall, false, "verified.candWall === false（候选墙钟不可复算，如实标注）");

      /* 独立复算基线侧，不信任 model */
      const scan10 = RUNS.filter((r) => r && r.demo_group === "scan10");
      const baseCost = scan10.reduce((a, r) => a + (typeof r.cost_usd === "number" ? r.cost_usd : 0), 0);
      const baseWall = scan10.reduce((a, r) => a + (typeof r.wall_ms === "number" ? r.wall_ms : 0), 0);

      g.eq(scan10.length, 10, "scan10 组 run 数");
      g.ok(
        typeof rc.baseCostUsd === "number" && Math.abs(rc.baseCostUsd - baseCost) < 1e-12,
        "recomputed.baseCostUsd === Σ(runs[demo_group=scan10].cost_usd)",
        baseCost,
        rc.baseCostUsd
      );
      g.eq(rc.baseWallMs, baseWall, "recomputed.baseWallMs === Σ(runs[demo_group=scan10].wall_ms)");

      /* display 与 claim 同源：ADR 常量，降幅取整数（72 / 61，不写 72.0） */
      g.eq(scan.display.baseCostUsd, scan.claim.baseCostUsd, "adr 形态 display.baseCostUsd === claim");
      g.eq(scan.display.candCostUsd, scan.claim.candCostUsd, "adr 形态 display.candCostUsd === claim");
      g.eq(scan.display.costDropPct, 72, "adr 形态 display.costDropPct === 72（整数）");
      g.eq(scan.display.wallDropPct, 61, "adr 形态 display.wallDropPct === 61（整数）");
      g.eq(scan.display.costDropText, "−72%", "adr 形态降幅文案 === −72%（无假精度）");
      g.eq(scan.display.wallDropText, "−61%", "adr 形态墙钟文案 === −61%");
    } else {
      /* 现场形态：两条 live 扫描，扫描级数字由 search_results 复算 */
      g.ok(v.baseCost === true && v.baseWall === true && v.candCost === true && v.candWall === true,
        "data 形态 verified 四项全 true（自我一致）", true, v);
      g.eq(M.s1.costBase, d.baseCostUsd, "s1.costBase === scan.display.baseCostUsd（同一来源）");
      g.eq(M.s1.costCand, d.candCostUsd, "s1.costCand === scan.display.candCostUsd（同一来源）");

      /* 独立复算：按 genome_id 把 search run 的墙钟加起来 */
      function wallSum(gid) {
        let total = 0;
        let n = 0;
        for (const r of RUNS) {
          if (!r || r.scope !== "search" || r.genome_id !== gid) continue;
          if (typeof r.wall_ms === "number") { total += r.wall_ms; n += 1; }
        }
        return n > 0 ? total : null;
      }
      const baseGid = PAIR.baseGid;
      const candGid = PAIR.candGid;
      g.eq(d.baseWallMs, wallSum(baseGid), "data 形态 display.baseWallMs === Σ runs[genome=基线].wall_ms");
      g.eq(d.candWallMs, wallSum(candGid), "data 形态 display.candWallMs === Σ runs[genome=候选].wall_ms");

      g.eq(scan.coverage.want, M.meta.searchTaskCount, "data 形态覆盖率分母 === scenario 搜索集题数");
      g.ok(scan.coverage.runs >= M.meta.searchTaskCount,
        "data 形态无 demo_group 的 search run 覆盖题数 >= 搜索集题数",
        ">= " + M.meta.searchTaskCount, scan.coverage.runs);
    }

    /* index.html 不得把「gate 级配对的 0.010684」和「−72%」写在同一屏 */
    const idx = SRC["index.html"];
    const hasRecomputedLiteral = idx.includes("0.010684");
    const has72 = idx.includes("−72") || idx.includes("-72");
    g.ok(
      !(hasRecomputedLiteral && has72),
      "index.html 不同屏出现 0.010684 与 −72/-72",
      "二者不同时出现",
      { "0.010684": hasRecomputedLiteral, "−72": has72 }
    );

    /* 更强的结构性检查：扫描级 KPI 只允许取 display，不允许直连 recomputed */
    const scanBinds = SLOTS.filter((s) => s.indexOf("s1.scan") === 0);
    const illegal = scanBinds.filter((s) => S1_BINDS.indexOf(s) === -1);
    g.ok(
      illegal.length === 0,
      "index.html 的 s1.scan* 槽位全部是登记的扫描级槽位",
      [],
      illegal
    );
    const leaked = scanBinds.filter((s) => {
      const spec = MOLT_RENDER.BINDINGS[s];
      const p = spec && typeof spec.path === "string" ? spec.path : "";
      return p.indexOf("recomputed") !== -1;
    });
    g.eqJson(leaked, [], "没有任何 s1.scan* 槽位直连 s1.scan.recomputed（gate 级配对合计）");
    const offDisplay = scanBinds.filter((s) => {
      const spec = MOLT_RENDER.BINDINGS[s];
      return !spec || typeof spec.path !== "string" || spec.path.indexOf("s1.scan.display.") !== 0;
    });
    g.eqJson(offDisplay, [], "6 个扫描级槽位全部绑定 s1.scan.display.*");
    g.eq(S1_BINDS.filter((s) => SLOTS.indexOf(s) !== -1).length, S1_BINDS.length, "6 个扫描级槽位均在 HTML 中");

    /* KPI 第 3 张卡（扫描级）与第 2 张卡（gate 级）不得共用同一个数字 */
    g.ok(
      SLOTS.indexOf("s1.cpsCand") !== -1 && SLOTS.indexOf("s1.scanCandCost") !== -1,
      "gate 级 cps 与扫描级成本各自有独立槽位",
      true,
      { "s1.cpsCand": SLOTS.indexOf("s1.cpsCand") !== -1, "s1.scanCandCost": SLOTS.indexOf("s1.scanCandCost") !== -1 }
    );
  }
}

/* ---- B10：页面上的引用值在 ADR-0008 原文里有出处 ---- */
{
  const g = group("B10", "扫描级引用可追溯到 ADR-0008");

  const adrPath = path.join(REPO_DIR, "docs", "decisions", "0008-demo.md");
  const exists = fs.existsSync(adrPath);
  g.ok(exists, "ADR-0008 存在", true, exists);

  if (exists) {
    const adr = fs.readFileSync(adrPath, "utf8");
    for (const lit of ["0.035560", "0.009978", "106.9", "41.7"]) {
      g.ok(adr.includes(lit), 'ADR-0008 原文含 "' + lit + '"', true, adr.includes(lit));
    }

    const claim = (M.s1 && M.s1.scan && M.s1.scan.claim) || MOLT_MODEL.SCAN_CLAIM || {};
    const F = M.fmt;
    g.eq(F.usd(claim.baseCostUsd), "0.035560", "claim.baseCostUsd 渲染为 ADR 的 0.035560");
    g.eq(F.usd(claim.candCostUsd), "0.009978", "claim.candCostUsd 渲染为 ADR 的 0.009978");
    g.eq(F.sec(claim.baseWallS), "106.9", "claim.baseWallS 渲染为 ADR 的 106.9");
    g.eq(F.sec(claim.candWallS), "41.7", "claim.candWallS 渲染为 ADR 的 41.7");
    g.eq(claim.source, "docs/decisions/0008-demo.md", "claim.source 指向 ADR 文件");
  }
}

/* ==========================================================================
 * C. 契约与聚合边界
 * ========================================================================== */

/* ---- C11：holdout 只允许聚合；report_results / snapshots / audit 无逐题键 ---- */
{
  const g = group("C11", "holdout 聚合边界");

  const audit = MOLT_MODEL.assertAggregateOnly(DATA.report_results);
  g.eq(audit.ok, true, "assertAggregateOnly(report_results).ok");
  g.eqJson(audit.hits, [], "assertAggregateOnly(report_results).hits");
  g.eq(M.audit.aggregateOk, true, "model.audit.aggregateOk");

  /* 检测器自检：负样本必须能被命中，否则上面的 PASS 是空转 */
  const negProbe = MOLT_MODEL.assertAggregateOnly({
    a: [{ task_id: "x" }],
    b: { nested: { run_id: "r" } }
  });
  g.eq(negProbe.ok, false, "检测器自检：含 task_id / run_id 时必须判不通过");
  g.ok(negProbe.hits.length >= 2, "检测器自检：命中路径数", ">= 2", negProbe.hits.length);

  /* 独立实现一遍递归扫描（不复用 model 的检测器） */
  const BANNED = ["task_id", "run_id", "task_ids"];
  function scanBanned(node, where, hits, depth) {
    if (depth > 16 || !node || typeof node !== "object") return hits;
    if (Array.isArray(node)) {
      node.forEach((child, i) => scanBanned(child, where + "[" + i + "]", hits, depth + 1));
      return hits;
    }
    Object.keys(node).forEach((k) => {
      const here = where ? where + "." + k : k;
      if (BANNED.indexOf(k) !== -1) hits.push(here);
      scanBanned(node[k], here, hits, depth + 1);
    });
    return hits;
  }

  for (const name of ["report_results", "snapshots", "audit"]) {
    const hits = scanBanned(DATA[name], name, [], 0);
    g.eqJson(hits, [], name + " 中不存在 task_id / run_id / task_ids");
  }

  /* 自检：同一个扫描器在含逐题字段的样本上必须报错 */
  const probe = scanBanned({ x: [{ task_ids: ["a"] }] }, "probe", [], 0);
  g.ok(probe.length === 1, "扫描器自检：能命中 task_ids", 1, probe.length);
}

/* ---- C12：缺数据时不编数字（负路径） ---- */
{
  const g = group("C12", "缺数据时 fail closed");

  const empty = MOLT_MODEL.build({ scenario: {}, gate: { decisions: [] } });
  g.eq(empty.ok, false, "build({scenario:{}, gate:{decisions:[]}}).ok");
  g.ok(empty.errors.length >= 1, "空 gate 的 errors.length", ">= 1", empty.errors.length);
  g.eq(empty.s1.available, false, "空 gate 时 s1.available === false");
  g.eq(empty.s1.solvedBase, null, "空 gate 时 s1.solvedBase 不填数");
  g.eq(empty.s2.pairCount, 0, "空 gate 时 s2.pairCount");

  const nullBuild = MOLT_MODEL.build(null);
  g.eq(nullBuild.ok, false, "build(null).ok");
  g.ok(
    nullBuild.errors.some((e) => String(e).indexOf("bundle.js") !== -1),
    "build(null).errors 提到 bundle.js（缺数据溯源）",
    "含 bundle.js",
    nullBuild.errors
  );
  g.eq(nullBuild.s1.cpsBase, null, "build(null) 时 s1.cpsBase 为 null（不编数字）");
  g.eq(nullBuild.s1.cpsCand, null, "build(null) 时 s1.cpsCand 为 null");
  g.eq(nullBuild.provenance.live, 0, "build(null) 时 provenance.live === 0");
  g.eq(nullBuild.provenance.total, 0, "build(null) 时 provenance.total === 0");
  g.eq(nullBuild.s1.scan.verified.baseCost, null, "build(null) 时扫描级 verified 不伪造 true");
}

/* ==========================================================================
 * D. 渲染后的口径与结构（W-08）
 * ========================================================================== */

/* ---- A13：把 render.js 真跑一遍（DOM 桩），断言渲染后的文本 ---- */
{
  const g = group("A13", "渲染后的口径与结构（DOM 桩真渲染）");

  const doc = PAGE_DOC;
  const out = ROUT;
  const s1Text = textOf(doc.getElementById("run-compare"));
  const noteText = textOf(doc.getElementById("verify-note"));
  const scanText = textOf(doc.getElementById("scan-note"));
  const pageText = textOf(doc.documentElement);

  g.eq(out.ok, true, "render(view, {doc}).ok");
  g.eqJson(out.errors, [], "render(...).errors");
  g.eq(out.total, SLOTS.length, "out.total === index.html 的 data-bind 出现数");
  g.eq(out.filled + out.dashes, out.total, "filled + dashes === total（写值阶段每个槽位有归属）");
  /* N7：revision 子句可能被摘除（filled 少 1）或填充（filled = total），两种都合法；
     唯一硬约束是渲染后不许留 —。断言落在「渲染后存活槽位」上。 */
  g.eq(
    out.domFilled + out.domDashes,
    out.domTotal,
    "filled + dashes === total（渲染后存活槽位）"
  );
  g.eq(out.domDashes, 0, "渲染后不得有 — 槽位（dashes === 0）");
  g.ok(
    out.filled === out.total || out.filled === out.total - 1,
    "out.filled ∈ {total, total − 1}（revision 子句被填上或被摘除）",
    "total 或 total−1",
    out.filled
  );
  g.eqJson(
    out.counts,
    { pairs: 10, chain: 6, lineage: 3, archives: 2, snapshots: 2 },
    "out.counts"
  );
  g.eq(Object.keys(MOLT_RENDER.BINDINGS).length, 36, "BINDINGS 唯一键数");

  /* 渲染完还留在 DOM 里的槽位，一个 — 都不该有 */
  const liveSlotNodes = doc.querySelectorAll("[data-bind]");
  const liveDash = liveSlotNodes.filter((n) => textOf(n) === DASH);
  g.eq(
    liveDash.length,
    0,
    "渲染后 DOM 中文本仍是 — 的槽位数（revision 子句已摘除，故不计）"
  );
  g.eq(liveSlotNodes.length, out.domTotal, "留在 DOM 的槽位数 === 渲染后存活槽位数");

  /* #verify-note / #scan-note / 首屏：按数据形态分支断言 */
  const dsp = M.s1 && M.s1.scan ? M.s1.scan.display : {};
  if (IS_ADR_SCAN) {
    /* ADR 形态：只许写基线侧复算结果 + 候选侧定性说明 */
    for (const lit of ["0.01068", "42.6", "0.008471"]) {
      g.ok(
        noteText.indexOf(lit) === -1,
        "#verify-note 不含 " + lit + "（候选侧未导出的数字不许在这里出现）",
        "不含 " + lit,
        noteText
      );
    }
    for (const lit of ["0.035560", "106.9", "ADR-0008"]) {
      g.ok(noteText.indexOf(lit) !== -1, "#verify-note 含 " + lit, "含 " + lit, noteText);
    }

    /* #scan-note：扫描级墙钟用 claim（41.7），不许出现 recomputed（42.6） */
    g.ok(scanText.indexOf("41.7") !== -1, "#scan-note 含 41.7（扫描级 claim 墙钟）", "含 41.7", scanText);
    g.ok(scanText.indexOf("42.6") === -1, "#scan-note 不含 42.6（gate 级配对墙钟）", "不含 42.6", scanText);

    /* S1 冲突守卫：
       方向一：首屏必须出现扫描级降幅（整数 72，去假精度）；
       方向二：首屏不得出现 recomputed 候选侧数字。 */
    g.ok(
      s1Text.indexOf("−72%") !== -1,
      "S1 区域含扫描级降幅 −72%（引 ADR-0008，整数口径）",
      "含 −72%",
      s1Text.slice(0, 200)
    );
    g.ok(
      s1Text.indexOf("−72.0") === -1,
      "S1 区域不出现 −72.0（去掉 71.9 vs 72.0 的假精度）",
      "不含 −72.0",
      s1Text.slice(0, 200)
    );
    const s1Recomputed = ["0.01068", "42.6", "0.008471"].filter((l) => s1Text.indexOf(l) !== -1);
    g.eqJson(s1Recomputed, [], "S1 区域不得出现 recomputed 候选侧数字（0.01068 / 42.6 / 0.008471）");
  } else {
    /* 现场形态：两侧数字都来自 search_results，页面必须把它们与口径说明写在一起 */
    const baseUsd = "$" + M.fmt.usd(dsp.baseCostUsd);
    const candUsd = "$" + M.fmt.usd(dsp.candCostUsd);

    g.ok(
      noteText.indexOf("两条完整") !== -1 && noteText.indexOf("题扫描") !== -1,
      "#verify-note 写明「两条完整 N 题扫描」",
      "含 两条完整 … 题扫描",
      noteText
    );
    g.ok(
      noteText.indexOf("search_results") !== -1,
      "#verify-note 指明两侧由 search_results 复算",
      "含 search_results",
      noteText
    );
    g.ok(
      noteText.indexOf(baseUsd) !== -1 && noteText.indexOf(candUsd) !== -1,
      "#verify-note 的两个金额 === scan.display",
      baseUsd + " / " + candUsd,
      noteText
    );
    g.ok(
      noteText.indexOf("0.009978") === -1 && noteText.indexOf("41.7") === -1,
      "#verify-note 不出现 ADR 常量（0.009978 / 41.7）",
      "不含 ADR 常量",
      noteText
    );

    g.ok(
      scanText.indexOf(M.fmt.sec(dsp.baseWallMs)) !== -1 &&
      scanText.indexOf(M.fmt.sec(dsp.candWallMs)) !== -1,
      "#scan-note 的墙钟 === scan.display（search_results 复算）",
      M.fmt.sec(dsp.baseWallMs) + " / " + M.fmt.sec(dsp.candWallMs),
      scanText
    );
    g.ok(
      scanText.indexOf("41.7") === -1,
      "#scan-note 不出现 ADR 候选墙钟 41.7",
      "不含 41.7",
      scanText
    );

    g.ok(
      s1Text.indexOf(baseUsd) !== -1 && s1Text.indexOf(candUsd) !== -1,
      "S1 的扫描级金额 === search_results 复算",
      baseUsd + " / " + candUsd,
      s1Text.slice(0, 240)
    );
    g.ok(
      s1Text.indexOf("−72") === -1 && s1Text.indexOf("0.009978") === -1,
      "S1 不出现 ADR 常量（−72% / 0.009978）",
      "不含 ADR 常量",
      s1Text.slice(0, 240)
    );
    g.eq(countLit(pageText, "0.009978"), 0, "data 形态整页不出现 ADR 候选常量 0.009978");
  }

  /* 整页里 0.01068 / 42.6 只许出现在写明「gate 级」的同一段落（限 ADR 形态：
     data 形态下这些数就是扫描级本身，没有 gate/scan 混用风险，另用上一条守卫）。 */
  if (IS_ADR_SCAN) {
    const critNodes = [];
    const seen = new Set();
    for (const lit of ["0.01068", "42.6"]) {
      for (const n of minimalContainers(doc.documentElement, lit)) {
        const c = noteContainer(n);
        if (!seen.has(c)) { seen.add(c); critNodes.push(c); }
      }
    }
    g.ok(
      critNodes.length >= 1,
      "整页确实出现了 gate 级候选配对数字（否则下面两条守卫是空转）",
      ">= 1 个段落",
      critNodes.length
    );
    const unlabeled = critNodes.filter((n) => textOf(n).indexOf("gate 级") === -1);
    g.eqJson(
      unlabeled.map((n) => n.tagName + "#" + n.id + " " + textOf(n).slice(0, 60)),
      [],
      "每个含 0.01068 / 42.6 的段落都写明「gate 级」"
    );
    const mixed = critNodes.filter((n) => textOf(n).indexOf("−72") !== -1);
    g.eqJson(
      mixed.map((n) => n.tagName + " " + textOf(n).slice(0, 60)),
      [],
      "含 0.01068 / 42.6 的段落不得同时出现 −72（扫描级降幅只留在首屏）"
    );
  }

  /* 配对表 live 标签：数量 === 数据面里 kind=live 的题目 ∩ 配对表题集（形态无关） */
  const body = doc.getElementById("pairs-body");
  const rows = body.children.filter((c) => typeof c !== "string");
  const liveTags = doc._findAll(body, (n) => n.classList.contains("tag-live"));
  const liveRunTasks = new Set(
    RUNS.filter((r) => r && r.scope === "search" && r.provenance && r.provenance.kind === "live" &&
      typeof r.task_id === "string").map((r) => r.task_id)
  );
  const pairTasks = new Set(M.s2.pairs.map((p) => p.taskId));
  const expectedLive = [...pairTasks].filter((t) => liveRunTasks.has(t)).length;
  g.eq(liveTags.length, expectedLive, "#pairs-body 中 .tag-live 数量 === 数据面 kind=live 的题数");

  if (expectedLive === 1) {
    const liveRow = rows.filter((r) => doc._findAll(r, (n) => n.classList.contains("tag-live")).length > 0)[0];
    const liveTask = [...pairTasks].filter((t) => liveRunTasks.has(t))[0];
    g.ok(
      !!liveRow && textOf(liveRow).indexOf(liveTask) !== -1,
      "唯一 live 行的题目是 " + liveTask,
      "含 " + liveTask,
      liveRow ? textOf(liveRow) : null
    );
  }

  /* 谱系：三条标签文案 */
  const lin = doc.getElementById("lineage");
  const linTags = doc._findAll(lin, (n) => n.classList.contains("tag"));
  g.eqJson(
    linTags.map((n) => textOf(n)),
    ["起点 · 第 1 代", "已采纳 · 第 2 代", "未采纳"],
    "谱系三行标签文案"
  );
  g.eq(countLit(pageText, "起点 · 第 1 代"), 1, "整页「起点 · 第 1 代」出现 1 次（基线不写成已采纳）");
  g.eq(countLit(pageText, "已采纳 · 第 2 代"), 1, "整页「已采纳 · 第 2 代」出现 1 次");

  /* N9：谱系父子关系可见 —— 有父的 genome 画「genome ← parent」 */
  const withParent = M.b5.lineage.filter((r) => r && r.parentShort);
  const arrows = doc._findAll(lin, (n) => n.classList.contains("lineage-parent"));
  g.eq(arrows.length, withParent.length, "谱系里的「←」条数 === 有 parent 的 genome 数");
  g.eqJson(
    arrows.map((n) => textOf(n)),
    withParent.map((r) => "← " + r.parentShort),
    "「←」后面跟的正是 parentShort"
  );
  g.ok(
    pageText.indexOf("← 表示派生来源") !== -1,
    "谱系面板标题下有「← 表示派生来源」说明",
    "含 ← 表示派生来源",
    null
  );

  /* 留出集：只许聚合 */
  const holdLine = doc.querySelector(".holdout-line");
  g.ok(
    !!holdLine && textOf(holdLine).indexOf("5/5 → 5/5") !== -1,
    "留出集行含 5/5 → 5/5",
    "含 5/5 → 5/5",
    holdLine ? textOf(holdLine) : null
  );
  g.ok(
    !!holdLine && textOf(holdLine).indexOf("仅聚合") !== -1,
    "留出集行标明「仅聚合」",
    "含 仅聚合",
    holdLine ? textOf(holdLine) : null
  );

  /* N4：留出集归属可见，且不在折叠块里 */
  const attNode = doc.getElementById("holdout-attribution");
  const att = M.holdout.attribution || {};
  g.ok(!!attNode, "存在 #holdout-attribution（留出集归属提示）", true, !!attNode);
  g.eq(textOf(attNode), String(att.note || ""), "#holdout-attribution 文本 === model.holdout.attribution.note");
  g.ok(textOf(attNode).indexOf("switches") !== -1, "#holdout-attribution 提到 switches", "含 switches", textOf(attNode));
  if (att.sameSwitches !== false) {
    g.ok(textOf(attNode).indexOf("genome") !== -1, "#holdout-attribution 提到 genome", "含 genome", textOf(attNode));
  }
  let anc = attNode ? attNode.parentNode : null;
  let inDetails = false;
  while (anc) {
    if (anc.tagName === "DETAILS") { inDetails = true; break; }
    anc = anc.parentNode;
  }
  g.eq(inDetails, false, "#holdout-attribution 不在折叠的 <details> 里");

  /* footer：revision 为 null 时子句整段摘除，有值时保留（N7 条件化） */
  const footerText = textOf(doc.querySelector(".colophon"));
  const rev = M.meta ? M.meta.revision : null;
  if (rev === null || rev === undefined) {
    g.eq(footerText.indexOf("revision"), -1, "footer 不含 revision（null 时子句被摘除）");
  } else {
    g.ok(footerText.indexOf("revision") !== -1, "footer 含 revision（有值时子句保留）", "含 revision", footerText.slice(0, 80));
  }
  g.ok(footerText.indexOf("证据截至") !== -1, "footer 含「证据截至」", "含 证据截至", footerText.slice(0, 80));

  /* 徽章说明 / KPI1 注脚 / H1 / S2 脚注：W-07 + W-14 的文案 */
  const badgeNote = doc.querySelectorAll('[data-bind="provenance.note"]')[0];
  const p = M.provenance;
  const noteLabel = p.searchOnly === true ? "条 search 运行记录" : "条运行记录";
  let expectedNote = "共 " + p.total + " " + noteLabel + "：live " + p.live +
    " · replay " + p.replay + " · fixture " + p.fixture;
  if (typeof p.reportRunsTotal === "number") {
    expectedNote += "（report 运行另有 " + p.reportRunsTotal + " 条，只导出聚合）";
  }
  g.eq(textOf(badgeNote), expectedNote, "徽章说明的三数来自 runs 实算（report 条数单列）");
  g.ok(
    textOf(badgeNote).indexOf("search 运行记录") !== -1,
    "徽章说明把总数限定在 search 运行记录",
    "含 search 运行记录",
    textOf(badgeNote)
  );
  if (DATA.report_results && DATA.report_results.report_runs_total &&
      typeof DATA.report_results.report_runs_total.count === "number") {
    g.ok(
      textOf(badgeNote).indexOf("report 运行另有") !== -1,
      "report 运行条数单列（不混进 search 计数）",
      "含 report 运行另有",
      textOf(badgeNote)
    );
  }
  g.eq(textOf(doc.querySelectorAll('[data-bind="s1.solvedNote"]')[0]), "成功率不变", "KPI1 注脚文案");
  g.ok(
    textOf(doc.getElementById("s1-title")).indexOf("10 道题") !== -1,
    "H1 含「10 道题」",
    "含 10 道题",
    textOf(doc.getElementById("s1-title"))
  );

  const scopeText = textOf(doc.getElementById("pair-scope-note"));
  if (IS_ADR_SCAN) {
    g.ok(scopeText.indexOf("9 条 replay") !== -1, "S2 脚注含「9 条 replay」", "含 9 条 replay", scopeText);
    g.ok(scopeText.indexOf("smoke-add") !== -1, "S2 脚注含 live 题目 smoke-add", "含 smoke-add", scopeText);
    g.ok(scopeText.indexOf("0.010684") !== -1, "S2 脚注含 gate 级配对成本 0.010684", "含 0.010684", scopeText);
  } else {
    g.ok(
      scopeText.indexOf("两次扫描都是 live 运行") !== -1,
      "data 形态 S2 口径说明改为「两次扫描都是 live 运行」",
      "含 两次扫描都是 live 运行",
      scopeText
    );
    g.ok(
      scopeText.indexOf("没有 replay/fixture 混合") !== -1,
      "data 形态 S2 口径说明声明没有 replay/fixture 混合",
      "含 没有 replay/fixture 混合",
      scopeText
    );
    g.eq(scopeText.indexOf("9 条 replay"), -1, "data 形态不再声称「9 条 replay」");
  }

  /* 「不要对逐 run 求和」的理由也要随形态变（回放 = 重复题；现场 = 两条扫描各一条 run） */
  const sumTail = textOf(doc.getElementById("sum-note-tail"));
  if (IS_ADR_SCAN) {
    g.ok(sumTail.indexOf("smoke-add") !== -1, "回放形态「不要求和」的理由点名 smoke-add 重复", "含 smoke-add", sumTail);
  } else {
    g.ok(
      sumTail.indexOf("smoke-add") === -1 && sumTail.indexOf("两条扫描") !== -1,
      "现场形态「不要求和」的理由改为「同题在两条扫描里各有一条 run」",
      "含 两条扫描 且不含 smoke-add",
      sumTail
    );
  }

  /* N2：口径说明与数字同屏（在表格之前），并且表格有合计行 */
  const order = docOrder(doc);
  const scopeNode = doc.getElementById("pair-scope-note");
  const tableNode = doc.querySelector("table.pairs");
  g.ok(
    !!scopeNode && !!tableNode &&
      order.indexOf(scopeNode) !== -1 && order.indexOf(tableNode) !== -1 &&
      order.indexOf(scopeNode) < order.indexOf(tableNode),
    "#pair-scope-note 在 DOM 中位于 table.pairs 之前",
    "scope 在 table 之前",
    { scope: order.indexOf(scopeNode), table: order.indexOf(tableNode) }
  );

  const foot = doc.getElementById("pairs-foot");
  const totalRow = foot ? doc._findAll(foot, (n) => n.classList.contains("pairs-total"))[0] : null;
  g.ok(!!totalRow, "配对表有合计行 tr.pairs-total", true, !!totalRow);
  const totalText = textOf(totalRow);
  g.ok(
    totalText.indexOf("gate 级配对合计") !== -1,
    "合计行写明「gate 级配对合计」",
    "含 gate 级配对合计",
    totalText
  );
  const totals = M.s2.totals || {};
  g.ok(
    typeof totals.base === "number" && totalText.indexOf("$" + M.fmt.usd(totals.base)) !== -1,
    "合计行的基线合计 === s2.totals.base",
    "$" + M.fmt.usd(totals.base),
    totalText
  );
  g.ok(
    typeof totals.cand === "number" && totalText.indexOf("$" + M.fmt.usd(totals.cand)) !== -1,
    "合计行的候选合计 === s2.totals.cand",
    "$" + M.fmt.usd(totals.cand),
    totalText
  );

  /* N5：KPI2 负号由 model 给（带符号字符串），页面不得出现「−−」 */
  const cpsNote = textOf(doc.querySelectorAll('[data-bind="s1.cpsDropText"]')[0]);
  g.eq(cpsNote, M.s1.cpsDropText, "KPI2 降幅文案 === model.s1.cpsDropText");
  g.eq(countLit(pageText, "−−"), 0, "整页不出现「−−」（负号只出现一次）");
  g.ok(
    SRC["index.html"].indexOf("−<b data-bind=") === -1,
    "index.html 不再把负号硬编码在 binding 前面",
    "不含 −<b data-bind=",
    null
  );
}

/* ---- A14：BINDINGS 36 键逐条可解析（改错 path 不许静默渲染 —） ---- */
{
  const g = group("A14", "BINDINGS 36 键全部可解析");

  const keys = Object.keys(MOLT_RENDER.BINDINGS);
  /* 唯一允许为 — 的键：数据面（bundle.js 12 键）没有 index，revision 合法为 null */
  const EXPECTED_DASH = { "meta.revision": true };

  const unresolved = [];
  const mismatched = [];
  for (const k of keys) {
    const r = bindingText(k, M);
    if (!r.spec) { unresolved.push(k + "：BINDINGS 无 spec"); continue; }
    if (r.text === null || r.text === "") unresolved.push(k + "：空文本");
    else if (r.text === DASH && !EXPECTED_DASH[k]) unresolved.push(k + "：—（path/get 解析不到值）");
    const nodes = PAGE_DOC.querySelectorAll('[data-bind="' + k + '"]');
    for (const n of nodes) {
      if (textOf(n) !== r.text) {
        mismatched.push(k + "：DOM " + JSON.stringify(textOf(n)) + " ≠ 复算 " + JSON.stringify(r.text));
      }
    }
  }

  g.eqJson(unresolved, [], "36 个键全部解析出非空、非 — 的文本");
  const dashKeys = keys.filter((k) => bindingText(k, M).text === DASH);
  g.eqJson(dashKeys, ["meta.revision"], "全 36 键中只有 meta.revision 允许是 —");
  g.eqJson(mismatched, [], "每个键在 DOM 中的文本 === 用 spec 复算的文本");
  g.eq(PAGE_DOC.getElementById("revision-clause"), null, "revision 为 null 时 revision 子句已从 DOM 摘除");

  const slotSet = [...new Set(SLOTS)].sort();
  g.eqJson(slotSet, keys.slice().sort(), "index.html 槽位集合 === BINDINGS 键集合（双向，无死键无未知）");
  g.eq(slotSet.length, 36, "唯一槽位数");
}

/* ---- A15：门禁失败态（合成 decision，真实数据走不到） ---- */
{
  const g = group("A15", "门禁失败态：首个 false 之后一律「未执行」");

  g.eqJson(
    M.s3.chain.map((s) => s.passed),
    [true, true, true, true, true, true],
    "真实数据 6 步 passed 全 true"
  );
  g.eqJson(
    chainBadges(PAGE_DOC),
    ["通过", "通过", "通过", "通过", "通过", "通过"],
    "真实数据 6 个徽章全「通过」"
  );

  /* 内存里改副本：不写回仓库任何文件 */
  function variant(mutate) {
    const v = Object.assign({}, VIEW);
    v.gate = JSON.parse(JSON.stringify(DATA.gate));
    v.search_results = JSON.parse(JSON.stringify(DATA.search_results));
    const d = v.gate.decisions[v.gate.decisions.length - 1];
    mutate(d, v);
    const m = MOLT_MODEL.build(v);
    const r = renderInto(m);
    return { m: m, doc: r.doc, badges: chainBadges(r.doc) };
  }

  function expectStopAt(name, v, k) {
    const passed = v.m.s3.chain.map((s) => s.passed);
    g.eq(passed[k], false, name + "：第 " + (k + 1) + " 步 passed === false");
    const before = passed.slice(0, k);
    g.eqJson(before, before.map(() => true), name + "：失败点之前全部 true（判定顺序未被跳过）");
    g.eq(v.badges[k], "停在此步", name + "：第 " + (k + 1) + " 个徽章「停在此步」");
    const after = v.badges.slice(k + 1);
    g.eqJson(after, after.map(() => "未执行"), name + "：其后徽章全「未执行」");
  }

  expectStopAt(
    "limits_identical=false",
    variant((d) => { d.limits_identical = false; }),
    0
  );
  expectStopAt(
    "invalid_ratio.candidate=0.40",
    variant((d) => { d.invalid_ratio.candidate = 0.4; }),
    1
  );
  expectStopAt(
    "budget_check.execution_exceeded=true",
    variant((d) => {
      d.budget_check.execution_exceeded = true;
      d.budget_check.reason = "execution budget exceeded";
    }),
    2
  );
  expectStopAt(
    "候选 solved 9 + verdict reject",
    variant((d, v) => {
      d.pairs = d.pairs.slice(0, d.pairs.length - 1);
      d.deltas.solved = -1;
      d.verdict = "reject";
      d.reason = "solved_dropped";
      const cand = v.search_results.results.filter((r) => r.genome_id === d.candidate_genome_id)[0];
      cand.scores.search.solved = 9;
    }),
    3
  );

  /* 字段缺失 → 未判定，不 fail-open 也不 fail-closed */
  const noLimits = variant((d) => { delete d.limits_identical; });
  g.eq(noLimits.m.s3.chain[0].passed, null, "删除 limits_identical → 第 1 步 passed === null（不 fail-open）");
  g.eq(noLimits.badges[0], "未判定", "删除 limits_identical → 徽章「未判定」");
  g.eq(
    textOf(noLimits.doc.querySelectorAll('[data-bind="s3.limitsIdentical"]')[0]),
    DASH,
    "删除 limits_identical → 事实块显示 —（不显示 true/false）"
  );
}

/* ---- A16：provenance × demo_group 结构不变式（不锁具体数字） ---- */
{
  const g = group("A16", "provenance × demo_group 结构不变式");

  /* 规则函数：现场数据换成 20 条 live（demo_group 全 null）时仍然成立 */
  function violations(runs) {
    const bad = [];
    const closureKinds = [];
    let replayGrouped = false;

    for (const r of runs) {
      if (!r || typeof r !== "object") continue;
      const p = r.provenance && typeof r.provenance === "object" ? r.provenance : {};
      const kind = p.kind;
      const rid = p.replay_of_run_id;
      const hasReplayOf = typeof rid === "string" && rid !== "";

      if (r.demo_group === "scan10") {
        replayGrouped = true;
        if (kind !== "replay") bad.push(r.run_id + "：scan10 必须 kind=replay，实际 " + kind);
        if (!hasReplayOf) bad.push(r.run_id + "：scan10 缺 replay_of_run_id");
      }
      if (r.demo_group === "candidate-scan") {
        replayGrouped = true;
        if (kind !== "replay") bad.push(r.run_id + "：candidate-scan 必须 kind=replay，实际 " + kind);
        if (!hasReplayOf) bad.push(r.run_id + "：candidate-scan 缺 replay_of_run_id");
      }
      if (kind === "replay" && !hasReplayOf) bad.push(r.run_id + "：replay 缺 replay_of_run_id");
      if (kind === "live") {
        if (hasReplayOf) bad.push(r.run_id + "：live 不该有 replay_of_run_id");
        if (p.live !== true) bad.push(r.run_id + "：live 必须 provenance.live === true");
      }
      if (r.demo_group === "closure") closureKinds.push(kind);
    }

    /* closure 组是 replay 模式才有的一对（live + replay / replay + fixture）。
       纯 live 现场（demo_group 全 null）没有 closure 组，不算违规。 */
    if (closureKinds.length === 0) {
      if (replayGrouped) bad.push("closure 组缺失：与 scan10 / candidate-scan 并存时必须恰好 2 条");
    } else if (closureKinds.length !== 2) {
      bad.push("closure 组必须恰好 2 条，实际 " + closureKinds.length);
    } else {
      const set = closureKinds.slice().sort().join(",");
      if (set !== "live,replay" && set !== "fixture,replay") {
        bad.push("closure 组 kind 集合必须是 {live,replay} 或 {replay,fixture}，实际 {" + set + "}");
      }
    }

    return bad;
  }

  g.eqJson(violations(RUNS), [], "真实数据满足全部结构不变式（scan10 / candidate-scan / replay / live / closure）");

  /* 下面三条只在数据面带分组时成立；现场两条 live 扫描（demo_group 全 null）没有分组，
     这时不是失败，是形态不同 —— 记 DEGRADED，不误红。 */
  const scan10 = RUNS.filter((r) => r && r.demo_group === "scan10");
  if (scan10.length > 0) {
    g.ok(
      scan10.every((r) => r.provenance.kind === "replay" && !!r.provenance.replay_of_run_id),
      "scan10 组全部 replay 且带 replay_of_run_id",
      "全部满足",
      scan10.length + " 条"
    );
  } else {
    g.warn("DEGRADED 本形态没有 scan10 组（现场 live / demo_group 全 null），跳过该组断言", 0);
  }

  const candScan = RUNS.filter((r) => r && r.demo_group === "candidate-scan");
  if (candScan.length > 0) {
    g.ok(
      candScan.every((r) => r.provenance.kind === "replay" && !!r.provenance.replay_of_run_id),
      "candidate-scan 组全部 replay 且带 replay_of_run_id",
      "全部满足",
      candScan.length + " 条"
    );
  } else {
    g.warn("DEGRADED 本形态没有 candidate-scan 组，跳过该组断言", 0);
  }

  const closure = RUNS.filter((r) => r && r.demo_group === "closure");
  if (scan10.length > 0 || candScan.length > 0) {
    g.eq(closure.length, 2, "closure 组 run 数");
  } else {
    g.warn("DEGRADED 本形态没有 closure 组（纯 live），跳过该组断言", closure.length);
  }

  /* 自检：规则在坏样本上必须报错，否则上面的 PASS 是空转。
     用合成样本（不依赖当前数据形态），这样现场 live 数据也照样检验规则本身。 */
  const selfA = [
    { run_id: "probe-a", demo_group: "scan10", scope: "search", task_id: "t",
      provenance: { kind: "live", live: true } }
  ];
  g.ok(violations(selfA).length > 0, "自检：scan10 里混进 live → 规则必须报错", "> 0", violations(selfA).length);

  const selfB = [
    { run_id: "probe-b", demo_group: "candidate-scan", scope: "search", task_id: "t",
      provenance: { kind: "replay" } }
  ];
  g.ok(violations(selfB).length > 0, "自检：replay 缺 replay_of_run_id → 规则必须报错", "> 0", violations(selfB).length);

  const selfC = [
    { run_id: "probe-c0", demo_group: "scan10", scope: "search", task_id: "t0",
      provenance: { kind: "replay", replay_of_run_id: "r0" } },
    { run_id: "probe-c1", demo_group: "closure", scope: "search", task_id: "t1",
      provenance: { kind: "live", live: true } },
    { run_id: "probe-c2", demo_group: "closure", scope: "search", task_id: "t2",
      provenance: { kind: "replay", replay_of_run_id: "r2" } },
    { run_id: "probe-c3", demo_group: "closure", scope: "search", task_id: "t3",
      provenance: { kind: "fixture" } }
  ];
  g.ok(violations(selfC).length > 0, "自检：closure 组多一条 → 规则必须报错", "> 0", violations(selfC).length);

  /* 正向对照：模拟「现场 20 条 live、demo_group 全 null」，规则不得误报 */
  const liveMode = [];
  for (let i = 0; i < 20; i++) {
    liveMode.push({
      run_id: "probe-live-" + i,
      demo_group: null,
      task_id: "probe-task-" + i,
      provenance: { kind: "live", live: true, model: "probe/model", runtime_id: "probe-runtime" }
    });
  }
  g.eqJson(violations(liveMode), [], "正向对照：20 条 live、demo_group 全 null 时规则不误报");
}

/* ---- A17：配对表滚账（KPI2「gate 级」标签的支撑） ---- */
{
  const g = group("A17", "配对表滚账支撑 gate 级 cps");

  const pairs = Array.isArray(LAST.pairs) ? LAST.pairs : [];
  const sum = (arr, f) => arr.reduce((a, x) => a + f(x), 0);
  const sumBase = sum(pairs, (p) => p.baseline_cost_usd);
  const sumCand = sum(pairs, (p) => p.candidate_cost_usd);
  const sumDelta = sum(pairs, (p) => p.cost_delta_usd);

  g.near(sumBase / M.s1.solvedBase, M.s1.cpsBase, 1e-9, "Σ pairs.baseline_cost_usd / solvedBase === s1.cpsBase");
  g.near(sumCand / M.s1.solvedCand, M.s1.cpsCand, 1e-9, "Σ pairs.candidate_cost_usd / solvedCand === s1.cpsCand");
  g.near(sumDelta, LAST.deltas.cost_total_usd, 1e-9, "Σ pairs.cost_delta_usd === gate.deltas.cost_total_usd");

  const allFlat = M.s2.pairs.length > 0 && M.s2.pairs.every((p) => p.passedDelta === 0);
  const allCheaper = M.s2.pairs.length > 0 &&
    M.s2.pairs.every((p) => typeof p.costDelta === "number" && p.costDelta < 0);
  g.eq(allFlat, true, "前提：配对表 passed_delta 全 0");
  g.eq(allCheaper, true, "前提：配对表 cost_delta 全 < 0");

  const lede = MOLT_RENDER.BINDINGS["s2.ledeNote"].get(M);
  g.ok(allFlat && lede.indexOf("一道没掉") !== -1, "passed_delta 全 0 → 导语含「一道没掉」", "含 一道没掉", lede);
  g.ok(
    allCheaper && lede.indexOf("成本差：见下表") === -1,
    "cost_delta 全 < 0 → 导语不含「成本差：见下表」",
    "不含 成本差：见下表",
    lede
  );
  g.eq(
    textOf(PAGE_DOC.querySelectorAll('[data-bind="s2.ledeNote"]')[0]),
    lede,
    "DOM 里的导语 === 用同一 getter 复算的文案"
  );

  /* 反向对照：有一题掉分时同一 getter 必须改口径（证明上面两条不是空转） */
  const broken = Object.assign({}, M, {
    s2: Object.assign({}, M.s2, {
      pairs: M.s2.pairs.map((p, i) => (i === 0 ? Object.assign({}, p, { passedDelta: -1 }) : p))
    })
  });
  const ledeBroken = MOLT_RENDER.BINDINGS["s2.ledeNote"].get(broken);
  g.ok(
    ledeBroken.indexOf("成本差：见下表") !== -1,
    "对照：有一题掉分时导语改为「成本差：见下表」",
    "含 成本差：见下表",
    ledeBroken
  );
}

/* ---- A18：静态文案不硬编码 claim 数字 + 装配顺序 + ES 语法面 ---- */
{
  const g = group("A18", "静态文案 / 装配顺序 / ES 语法黑名单");

  const html = SRC["index.html"];
  const banned = ["0.009978", "0.035560", "106.9", "41.7", "72.0", "70.0", "10/10"];
  g.eqJson(
    banned.filter((l) => html.indexOf(l) !== -1),
    [],
    "index.html 不得硬编码 claim 数字 / 百分比 / 10/10（全部由 binding 生成）"
  );

  const scriptAttrs = [...html.matchAll(/<script\b([^>]*)>/gi)].map((m) => m[1]);
  const scriptSrcs = scriptAttrs.map((a) => {
    const m = /\bsrc\s*=\s*"([^"]*)"/i.exec(a);
    return m ? m[1] : null;
  });
  g.eqJson(scriptSrcs, ["../data/bundle.js", "model.js", "render.js", "app.js"], "4 个 script 的装配顺序");
  g.eq(
    scriptAttrs.filter((a) => /\bdefer\b/i.test(a)).length,
    scriptAttrs.length,
    "全部 script 带 defer"
  );
  g.eq(
    scriptAttrs.filter((a) => /type\s*=\s*["']module["']/i.test(a)).length,
    0,
    "任何 script 都不带 type=module"
  );

  const BANNED_SYNTAX = ["=>", "?.", "??", "`", ".at(", "Object.fromEntries", "class ", "let ", "const "];
  for (const name of ["model.js", "render.js", "app.js"]) {
    const found = BANNED_SYNTAX.filter((tok) => SRC[name].indexOf(tok) !== -1);
    g.eqJson(found, [], name + " 不得含 ES2015+ 语法（箭头 / 可选链 / 空值合并 / 模板串 / .at / fromEntries / class / let / const）");
  }
}

/* ---- A19：W-14 收口的三条硬断言（形态无关） ---- */
{
  const g = group("A19", "W-14：降幅自洽 / 合计复算 / 口径说明前置");

  /* 1. 扫描级降幅与两个金额自洽（容差 0.06pp：ADR 常量是整数降幅 72/61，
        由 0.009978 / 0.035560 复算是 71.9405，差 0.0595pp —— 假精度已在本页去掉） */
  const d = M.s1.scan.display;
  const costCalc = (1 - d.candCostUsd / d.baseCostUsd) * 100;
  g.near(d.costDropPct, costCalc, 0.06, "display.costDropPct 与两个金额自洽（±0.06pp）");
  const wallCalc = (1 - d.candWallMs / d.baseWallMs) * 100;
  g.near(d.wallDropPct, wallCalc, 0.06, "display.wallDropPct 与两侧墙钟自洽（±0.06pp）");
  g.ok(
    typeof d.costDropText === "string" && d.costDropText.indexOf("−−") === -1 &&
      /^[−+]?\d+(\.\d)?%$/.test(d.costDropText),
    "display.costDropText 是带符号的单个负号文案",
    "−N% / −N.N% / +N.N%",
    d.costDropText
  );

  /* 2. s2.totals 三项分别等于逐题求和 */
  const pairs = Array.isArray(M.s2.pairs) ? M.s2.pairs : [];
  const sumOf = (f) => pairs.reduce((a, p) => a + (typeof p[f] === "number" ? p[f] : NaN), 0);
  g.near(M.s2.totals.base, sumOf("baseCost"), 1e-9, "s2.totals.base === Σ pairs.baseCost");
  g.near(M.s2.totals.cand, sumOf("candCost"), 1e-9, "s2.totals.cand === Σ pairs.candCost");
  g.near(M.s2.totals.delta, sumOf("costDelta"), 1e-9, "s2.totals.delta === Σ pairs.costDelta");
  g.near(M.s2.totals.delta, M.s2.totals.cand - M.s2.totals.base, 1e-5,
    "s2.totals.delta ≈ cand − base（逐题差按 1e-6 取整，累计误差放 1e-5）");

  /* 3. 口径说明在表格之前（防 N2 回退） */
  const order = docOrder(PAGE_DOC);
  const scopeNode = PAGE_DOC.getElementById("pair-scope-note");
  const tableNode = PAGE_DOC.querySelector("table.pairs");
  g.ok(
    !!scopeNode && !!tableNode && order.indexOf(scopeNode) < order.indexOf(tableNode),
    "#pair-scope-note 在 DOM 中位于 table.pairs 之前（口径与数字同屏）",
    "scope < table",
    { scope: order.indexOf(scopeNode), table: order.indexOf(tableNode) }
  );
}

/* ==========================================================================
 * 3. 输出
 * ========================================================================== */

const BAR = "─".repeat(72);
console.log("molt · web/page 验收（W-04）");
console.log("page : " + PAGE_DIR);
console.log("data : " + ARGS.dataDir);
console.log(BAR);

for (const g of GROUPS) {
  console.log("");
  console.log(g.id + " · " + g.title);
  let seq = 0;
  for (const s of g.subs) {
    seq += 1;
    const tag = "[" + s.status + "] " + g.id + "." + seq;
    if (s.status === "FAIL") {
      console.log(tag + " " + s.label + " 期望 " + show(s.expected) + " 实际 " + show(s.actual));
    } else if (s.status === "WARN") {
      console.log(tag + " " + s.label + " " + show(s.actual));
    } else {
      console.log(tag + " " + s.label);
    }
  }
}

const failedGroups = GROUPS.filter((g) => g.failed).length;
const passedGroups = GROUPS.length - failedGroups;

console.log("");
console.log(BAR);
console.log(
  "assertions: " + subPass + " passed, " + subFail + " failed, " + subWarn + " warnings"
);
console.log(passedGroups + " passed, " + failedGroups + " failed, " + subWarn + " warnings");
console.log("RESULT: " + (failedGroups === 0 ? "OK" : "FAIL"));

process.exit(failedGroups === 0 ? 0 : 1);
