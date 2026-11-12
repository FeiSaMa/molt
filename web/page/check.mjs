#!/usr/bin/env node
/*!
 * molt · web/page/check.mjs —— 展示页验收脚本（W-19：8 屏白底翻页版）
 *
 * 一条命令验证：8 屏版式与结构、页面与数据面一致、口径纪律（扫描级 vs gate 级
 * 不混屏）、动效降级（无 JS / 减少动效不白屏）、静态约束、聚合边界、负路径。
 *   用法：node check.mjs [--data-dir <path>] [--render-module <path>]
 * 退出码：全 PASS → 0；任意 [FAIL] → 1；参数/加载错误 → 2。
 *
 * 纪律：
 *   - 只用 node 标准库，零 npm 依赖，不跑 pytest / molt CLI。
 *   - 不读 cwd 定位自身目录：一律基于 import.meta.url（from /tmp 也能跑）。
 *   - 断言的是「行为边界」，不是为了通过而放宽：口径类断言全部写成硬 FAIL，
 *     只有「数据面自身退化」这类环境问题才降级为 [WARN] DEGRADED。
 *   - 反向验证：--data-dir / --render-module 指向复制品并改坏一处，应出现对应 FAIL。
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

/* 脚本自身所在目录：定位仓库与默认数据面，永远按 import.meta.url 算 */
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_DIR = path.resolve(SCRIPT_DIR, "..", "..");
const require = createRequire(import.meta.url);

/* ==========================================================================
 * 0. 参数与加载
 * ========================================================================== */

function parseArgs(argv) {
  const out = {
    dataDir: null,
    /* 反向验证专用（两个）：指向 /tmp 下被改坏的页面副本 / render.js 副本，
       证明断言不是空转。主运行不传 → 永远测仓库里的页面与 render.js。 */
    pageDir: null,
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
    } else if (a === "--page-dir") {
      const v = argv[i + 1];
      if (!v) {
        console.error("--page-dir 需要一个路径参数");
        process.exit(2);
      }
      out.pageDir = path.resolve(process.cwd(), v);
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
  /* 数据面默认跟着脚本走（不跟 --page-dir 走，反向验证时数据仍是仓库那份） */
  out.dataDir = path.resolve(out.dataDir || path.join(SCRIPT_DIR, "..", "data"));
  return out;
}

const ARGS = parseArgs(process.argv.slice(2));
if (ARGS.help) {
  console.log("用法: node check.mjs [--data-dir <web/data 的路径>] " +
    "[--page-dir <页面副本目录>] [--render-module <render.js 的副本>]");
  console.log("  --page-dir / --render-module 仅供反向验证（指向 /tmp 下被改坏的副本），主运行不要传。");
  process.exit(0);
}

/* 被测页面目录：默认就是脚本所在目录（仓库），只有反向验证才指向副本 */
const PAGE_DIR = ARGS.pageDir ? ARGS.pageDir : SCRIPT_DIR;

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

/** 数字的「语义容器」：行内格式元素先上溯，表格单元格再上溯到所在行。
 *  8 屏版把数字包进 <b> / <span>（例如合计行的 <b data-bind>），
 *  「同一段」应以行 / 列表项 / 段落为界，而不是那个 <b>。 */
const INLINE_TAGS = { B: 1, STRONG: 1, EM: 1, I: 1, SPAN: 1, CODE: 1, SMALL: 1, A: 1 };
function noteContainer(node) {
  let n = node;
  while (n && INLINE_TAGS[n.tagName] === 1 && n.parentNode) n = n.parentNode;
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
 * D. 8 屏版式 / 绑定 / 渲染文案（W-19：白底翻页版）
 *
 * 与旧版的结构位移：单页分区（#run-compare / #scan-note / .holdout-line /
 * #pairs-foot / #sum-note-tail …）改成 8 个 <section class="slide"> 翻页，
 * 口径数字按屏拆开。所以断言从「按 id 找区块」改成「按屏切分 + 槽位复算」。
 * 口径语义（扫描级 vs gate 级不混、缺数据不补数、聚合边界）一条没放松。
 * ========================================================================== */

const SLIDES = PAGE_DOC.querySelectorAll("section.slide");
const CSS_RULES = cssRules(SRC["app.css"]);

/* ---- CSS 静态解析：DOM 桩没有样式引擎，只能做规则级文本匹配 ---- */

function cssRules(src) {
  const clean = String(src).replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = [];
  const stack = [];
  let selStart = 0;
  let i = 0;
  while (i < clean.length) {
    const ch = clean[i];
    if (ch === "{") {
      stack.push({ sel: clean.slice(selStart, i).trim(), bodyStart: i + 1 });
      selStart = i + 1;
    } else if (ch === "}") {
      const top = stack.pop();
      if (top) rules.push({ sel: top.sel, body: clean.slice(top.bodyStart, i), depth: stack.length });
      selStart = i + 1;
    }
    i += 1;
  }
  return rules;
}

function hexToRgb(hex) {
  const h = String(hex).replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return null;
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16)
  ];
}

/** WCAG 相对亮度：用来断言「纸是浅色、墨是深色」，不靠肉眼 */
function relLum(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return null;
  const ch = rgb.map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}

function hasAncestorClass(node, cls) {
  let n = node;
  while (n) {
    const tokens = String(n.className || "").split(/\s+/);
    if (tokens.indexOf(cls) !== -1) return true;
    n = n.parentNode;
  }
  return false;
}

/** 脚注类元素：class 里以 foot* / aside* 开头的 token（一个节点只算一种） */
function footKind(node) {
  const tokens = String(node.className || "").split(/\s+/).filter(Boolean);
  if (tokens.some((t) => t.indexOf("aside") === 0)) return "aside";
  if (tokens.some((t) => t.indexOf("foot") === 0)) return "foot";
  return null;
}

function pad2(n) {
  return n < 10 ? "0" + n : String(n);
}

/* ---- W-23 新增的读法工具（结构变了，读法跟着变） ---- */

/** 口径说明块：S8 常显的 warnings 区块（W-22 起不再是 <details>）。
 *  render.js 目前不给它 id，所以按「.colophon 下唯一不带 .is-error 的 .warnings」
 *  定位；若哪天补上 id="warnings"，这里自动优先用它。找不到返回 null（断言硬 FAIL）。 */
function scopeBlockOf(doc) {
  const byId = doc.getElementById("warnings");
  if (byId) return byId;
  const boxes = doc.querySelectorAll(".warnings").filter((n) => !n.classList.contains("is-error"));
  return boxes.length === 1 ? boxes[0] : null;
}

/** 屏面正文：按 textContent 拼文本，但整棵跳过给定的子树（口径说明块）。
 *  口径块是「并列解释两个口径」的地方，不算屏面上的 claim；屏面正文仍不许并列。 */
function textExcluding(node, skip) {
  if (!node) return "";
  const parts = [];
  (function walk(n) {
    if (typeof n._text === "string" && n._text !== "") parts.push(n._text);
    for (const c of n.children) {
      if (typeof c === "string") { parts.push(c); continue; }
      if (skip && skip.indexOf(c) !== -1) continue;
      walk(c);
    }
  })(node);
  return parts.join("").replace(/\s+/g, " ").trim();
}

/** 屏面正文（自动排除该屏内的口径块） */
function surfaceTextOf(slide) {
  const block = scopeBlockOf(PAGE_DOC);
  const skip = block && hasAncestorClass(block, "slide") && isDescendantOf(block, slide) ? [block] : [];
  return textExcluding(slide, skip);
}

/** a 是否是 b 的后代（含自身） */
function isDescendantOf(a, b) {
  let n = a;
  while (n) {
    if (n === b) return true;
    n = n.parentNode;
  }
  return false;
}

/** 可见文本：跳过 hidden 节点整棵子树（口径说明与「仅聚合」这类常显内容不受影响）。
 *  用来验证「两句互斥的人话只显示一句」——hidden 的那句仍在 DOM 里（槽位不丢）。 */
function visibleTextOf(node) {
  if (!node) return "";
  const parts = [];
  (function walk(n) {
    if (n.hidden === true) return;
    if (typeof n._text === "string" && n._text !== "") parts.push(n._text);
    for (const c of n.children) {
      if (typeof c === "string") { parts.push(c); continue; }
      walk(c);
    }
  })(node);
  return parts.join("").replace(/\s+/g, " ").trim();
}

/** 文本里的全部数字（整数 / 小数），用于「页面上的数必须能在数据面复算」 */
function numbersIn(text) {
  return [...String(text).matchAll(/\d+(?:\.\d+)?/g)].map((m) => Number(m[0]));
}

/** 去掉注释后的 CSS 原文 */
function cssClean(src) {
  return String(src).replace(/\/\*[\s\S]*?\*\//g, "");
}

/** 提取所有 @keyframes 块（含名字与体内文本），用花括号配平，不依赖正则贪婪。 */
function keyframesBlocks(src) {
  const clean = cssClean(src);
  const out = [];
  const re = /@keyframes\s+([\w-]+)\s*\{/g;
  let m;
  while ((m = re.exec(clean))) {
    const bodyStart = re.lastIndex;
    let depth = 1;
    let i = bodyStart;
    while (i < clean.length && depth > 0) {
      if (clean[i] === "{") depth += 1;
      else if (clean[i] === "}") depth -= 1;
      i += 1;
    }
    out.push({
      name: m[1],
      body: clean.slice(bodyStart, i - 1),
      start: m.index,
      end: i
    });
    re.lastIndex = i;
  }
  return out;
}

/** CSS 去掉 @keyframes 之后剩下的部分（关键帧里的 opacity:0 不是「初始隐藏规则」） */
function cssWithoutKeyframes(src) {
  const clean = cssClean(src);
  const blocks = keyframesBlocks(src);
  let out = "";
  let at = 0;
  for (const b of blocks) {
    out += clean.slice(at, b.start);
    at = b.end;
  }
  return out + clean.slice(at);
}

/** font-size 声明 → 最大字号（rem）。var(--x) 先按 :root 令牌展开。
 *  只认 rem / clamp(...,Nrem)，认不出返回 null（断言会显式报「认不出」而不是猜）。 */
function maxFontRem(decl, rootBody) {
  let expr = String(decl || "").trim();
  const v = /^var\(\s*(--[\w-]+)\s*\)$/.exec(expr);
  if (v) {
    const m = new RegExp(v[1] + "\\s*:\\s*([^;]+);").exec(String(rootBody || ""));
    if (!m) return null;
    expr = m[1].trim();
  }
  const rems = [...expr.matchAll(/(\d+(?:\.\d+)?)rem/g)].map((x) => Number(x[1]));
  return rems.length > 0 ? Math.max.apply(null, rems) : null;
}

/** 取某个选择器（精确匹配）最后一条 font-size 声明 */
function fontSizeDecl(selList, rules) {
  const hits = rules.filter((r) => selList.indexOf(r.sel) !== -1);
  if (hits.length === 0) return null;
  const m = /font-size\s*:\s*([^;]+);/.exec(hits[hits.length - 1].body);
  return m ? { sel: hits[hits.length - 1].sel, decl: m[1].trim() } : null;
}

/** JS 函数体（花括号配平）。header 传 "function onScroll(" 这种前缀。 */
function fnBodyOf(src, header) {
  const i = String(src).indexOf(header);
  if (i === -1) return "";
  const open = String(src).indexOf("{", i + header.length);
  if (open === -1) return "";
  let depth = 0;
  for (let j = open; j < src.length; j++) {
    if (src[j] === "{") depth += 1;
    else if (src[j] === "}") {
      depth -= 1;
      if (depth === 0) return src.slice(open + 1, j);
    }
  }
  return "";
}

/** transition / animation-duration 声明里的所有毫秒 / 秒数 → 毫秒数组 */
function durationsMs(body, prop) {
  const out = [];
  const re = new RegExp(prop + "\\s*:\\s*([^;]+);", "g");
  let m;
  while ((m = re.exec(String(body)))) {
    const val = m[1];
    for (const d of val.matchAll(/(\d+(?:\.\d+)?)(ms|s)\b/g)) {
      out.push(d[2] === "s" ? Number(d[1]) * 1000 : Number(d[1]));
    }
  }
  return out;
}

/* ---- A13：8 屏版式与结构（白底 / 翻页 / 每屏一个标题） ---- */
{
  const g = group("A13", "8 屏版式与结构");
  const doc = PAGE_DOC;
  const slides = SLIDES;
  const deck = doc.querySelector("main.deck");

  g.ok(!!deck, '存在 <main class="deck"> 滚动容器', true, !!deck);
  g.eq(slides.length, 8, "section.slide 数量");
  g.eqJson(slides.map((s) => s.id), ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"],
    "屏 id 依次为 s1..s8");
  g.ok(slides.every((s) => !!deck && s.parentNode === deck),
    "8 屏都是 main.deck 的直接子节点", true,
    slides.map((s) => s.parentNode ? s.parentNode.className : null));

  /* 1) 滚动吸附：从 app.css 的 .deck / .slide 规则断言（DOM 桩无样式引擎） */
  const deckRules = CSS_RULES.filter((r) => r.sel === ".deck");
  g.ok(deckRules.length >= 1, "app.css 有 .deck 规则", ">= 1", deckRules.length);
  g.ok(deckRules.some((r) => /scroll-snap-type\s*:\s*y\s+mandatory/.test(r.body)),
    "滚动容器 scroll-snap-type: y mandatory", "y mandatory",
    deckRules.map((r) => r.body.replace(/\s+/g, " ").slice(0, 80)));
  g.ok(CSS_RULES.some((r) => r.sel === ".slide" && /scroll-snap-align\s*:\s*start/.test(r.body)),
    "每屏 scroll-snap-align: start", "start", null);
  g.ok(CSS_RULES.some((r) => r.sel === ".slide" && /min-height\s*:\s*100d?vh/.test(r.body)),
    "每屏至少一屏高（min-height: 100vh / 100dvh）", "存在", null);

  /* 2) 每屏恰好一个 h1 或 h2；S1 为 h1，其余 h2；aria-labelledby 自指 */
  const headCount = [];
  const headBad = [];
  for (const s of slides) {
    const hs = doc._findAll(s, (n) => n.tagName === "H1" || n.tagName === "H2");
    headCount.push(hs.length);
    if (hs.length !== 1) {
      headBad.push(s.id + "：" + hs.length + " 个标题");
      continue;
    }
    const want = s.id === "s1" ? "H1" : "H2";
    if (hs[0].tagName !== want) headBad.push(s.id + " 的标题是 " + hs[0].tagName + "，应为 " + want);
    if (!hs[0].id || s.getAttribute("aria-labelledby") !== hs[0].id) {
      headBad.push(s.id + " 的 aria-labelledby 未指向自己的标题 id");
    }
  }
  g.eqJson(headCount, [1, 1, 1, 1, 1, 1, 1, 1], "每屏标题数（恰好 1 个）");
  g.eqJson(headBad, [], "标题层级 / aria-labelledby 与屏一一对应");

  /* 3) 白底黑字：--paper 浅、--ink 深（从 :root 令牌算相对亮度） */
  const rootRule = CSS_RULES.filter((r) => r.sel === ":root")[0] || { body: "" };
  const paper = (/--paper\s*:\s*(#[0-9a-fA-F]{3,8})/.exec(rootRule.body) || [])[1] || null;
  const ink = (/--ink\s*:\s*(#[0-9a-fA-F]{3,8})/.exec(rootRule.body) || [])[1] || null;
  g.ok(paper !== null && ink !== null, ":root 定义 --paper / --ink", "#rrggbb", { paper: paper, ink: ink });

  const lumPaper = relLum(paper || "");
  const lumInk = relLum(ink || "");
  g.ok(typeof lumPaper === "number" && lumPaper >= 0.8,
    "--paper 是浅色（相对亮度 >= 0.8）", ">= 0.8", lumPaper);
  g.ok(typeof lumInk === "number" && lumInk <= 0.2,
    "--ink 是深色（相对亮度 <= 0.2）", "<= 0.2", lumInk);
  g.ok(typeof lumPaper === "number" && typeof lumInk === "number" && lumPaper > lumInk,
    "纸比墨亮（对比方向正确）", true, { paper: lumPaper, ink: lumInk });

  const bodyRule = CSS_RULES.filter((r) => r.sel === "body")[0] || { body: "" };
  g.ok(/background\s*:\s*var\(--paper\)/.test(bodyRule.body), "body 背景取 --paper", "var(--paper)", null);
  g.ok(/color\s*:\s*var\(--ink\)/.test(bodyRule.body), "body 文字取 --ink", "var(--ink)", null);

  /* 4) 三档字体（display / body / num）+ 减少动效媒体查询 */
  const fontProps = ["--font-display", "--font-body", "--font-num"];
  const fontVals = fontProps.map((p) => {
    const m = new RegExp(p + "\\s*:\\s*([^;]+);").exec(rootRule.body);
    return m ? m[1].trim() : null;
  });
  g.ok(fontVals.every((v) => typeof v === "string" && v !== ""),
    "三档字体令牌都已定义", fontProps, fontVals);
  g.eq(new Set(fontVals.map((v) => String(v))).size, 3, "display / body / num 三个 font-family 互不相同");
  const fontUse = fontProps.map((p) =>
    countRe(SRC["app.css"], new RegExp("font-family\\s*:\\s*var\\(" + p + "\\)", "g")));
  g.ok(fontUse.every((n) => n >= 1), "三档字体都在 font-family 声明里实际使用", "各 >= 1", fontUse);
  const famDecls = new Set([...SRC["app.css"].matchAll(/font-family\s*:\s*([^;]+);/g)]
    .map((m) => m[1].trim()));
  g.ok(famDecls.size >= 3, "app.css 的不同 font-family 声明数", ">= 3", famDecls.size);
  g.ok(CSS_RULES.some((r) => /prefers-reduced-motion\s*:\s*reduce/.test(r.sel)),
    "存在 prefers-reduced-motion: reduce 媒体查询", true, null);

  /* 5) 脚注密度：同类脚注每屏至多 1 个，全页 <= 8
        （规格写的是「每屏最多 1 个 foot/aside 元素」；S7 的 .aside「仅聚合」
         与 .footnote 是两种不同角色，这里按类别各限 1 个，总数仍是硬上限。） */
  const footPerSlide = [];
  const footBad = [];
  for (const s of slides) {
    const nodes = doc._findAll(s, (n) => footKind(n) !== null);
    const foot = nodes.filter((n) => footKind(n) === "foot").length;
    const aside = nodes.filter((n) => footKind(n) === "aside").length;
    footPerSlide.push({ id: s.id, foot: foot, aside: aside });
    if (foot > 1) footBad.push(s.id + "：.foot* " + foot + " 个");
    if (aside > 1) footBad.push(s.id + "：.aside* " + aside + " 个");
  }
  const footTotal = footPerSlide.reduce((a, x) => a + x.foot + x.aside, 0);
  g.eqJson(footBad, [], "每屏同类脚注元素不超过 1 个");
  g.ok(footTotal >= 1, "全页脚注元素数（非空，否则上一条空转）", ">= 1", footTotal);
  g.ok(footTotal <= 8, "全页脚注元素总数", "<= 8", footTotal);
  g.eqJson(footPerSlide.filter((x) => x.foot + x.aside === 0).map((x) => x.id), ["s1", "s8"],
    "S1（封面）与 S8（收尾）不挂脚注");
  g.eqJson(footPerSlide.filter((x) => x.aside > 0).map((x) => x.id), ["s7"],
    "只有 S7 用 .aside（留出集「仅聚合」标注）");

  /* 6) 动效钩子：入场元素 35 个，且全部挂在某一屏内 */
  const reveals = doc.querySelectorAll("[data-reveal]");
  g.ok(reveals.length >= 20, "[data-reveal] 数量", ">= 20", reveals.length);
  g.eq(reveals.filter((n) => !hasAncestorClass(n, "slide")).length, 0,
    "每个 [data-reveal] 都在某一屏内");
  const counters = doc.querySelectorAll("[data-count]");
  g.ok(counters.length >= 1, "[data-count] 数量（计数动效钩子）", ">= 1", counters.length);
  g.eqJson(
    counters.filter((n) => !/^\d+$/.test(String(n.getAttribute("data-count") || "")))
      .map((n) => n.getAttribute("data-count")),
    [],
    "[data-count] 的属性值都是纯整数"
  );
  g.eq(counters.filter((n) => !hasAncestorClass(n, "slide")).length, 0,
    "每个 [data-count] 都在某一屏内");

  /* 7) HUD：圆点导航 / 页码 / 视差层与屏数自洽（加屏必须同步页码） */
  g.ok(!!doc.getElementById("dots"), "存在 #dots 屏导航容器", true, !!doc.getElementById("dots"));
  g.eq(textOf(doc.getElementById("pager-total")), pad2(slides.length), "#pager-total 与屏数一致");
  g.eq(textOf(doc.getElementById("pager-cur")), "01", "#pager-cur 初始为 01");
  const parallax = doc.querySelectorAll(".parallax");
  g.ok(parallax.length >= 1, ".parallax 视差层数量", ">= 1", parallax.length);
  g.ok(parallax.every((n) => n.getAttribute("aria-hidden") === "true"),
    "视差层对读屏隐藏（aria-hidden）", true,
    parallax.map((n) => n.getAttribute("aria-hidden")));

  /* 8) 每屏都有实质内容（防止「屏在但空」） */
  g.eqJson(slides.filter((s) => textOf(s).length < 20).map((s) => s.id), [],
    "每屏都有实质内容（文本 >= 20 字）");
}

/* ---- A14：BINDINGS 44 键逐条可解析 + 渲染后零 — ---- */
{
  const g = group("A14", "BINDINGS 44 键全部可解析（DOM 桩真渲染）");
  const doc = PAGE_DOC;
  const out = ROUT;
  const keys = Object.keys(MOLT_RENDER.BINDINGS);
  const revNull = !M.meta || M.meta.revision === null || M.meta.revision === undefined;

  g.eq(out.ok, true, "render(view, {doc}).ok");
  g.eqJson(out.errors, [], "render(...).errors");
  g.eq(out.total, SLOTS.length, "out.total === index.html 的 data-bind 出现数");
  g.eq(out.filled + out.dashes, out.total, "filled + dashes === total（写值阶段每个槽位有归属）");
  g.eq(out.domFilled + out.domDashes, out.domTotal, "filled + dashes === total（渲染后存活槽位）");
  g.eq(out.domDashes, 0, "渲染后不得有 — 槽位（dashes === 0）");
  g.eq(revNull, true, "前提：meta.revision 为 null（其容器整体不渲染）");
  g.eq(out.dashes, revNull ? 1 : 0, "写值阶段只有 revision 槽位可能是 —");
  g.eq(out.domTotal, out.total - 1, "渲染后存活槽位 = total − 1（revision 子句整体摘除）");
  g.eq(doc.getElementById("revision-clause"), null, "revision 为 null 时 revision 子句已从 DOM 摘除");

  g.eq(out.counts.pairs, M.s2.pairs.length, "out.counts.pairs === model.s2.pairs.length");
  g.eq(out.counts.chain, M.s3.chain.length, "out.counts.chain === model.s3.chain.length");
  g.eq(out.counts.lineage, M.b5.lineage.length, "out.counts.lineage === model.b5.lineage.length");
  g.eq(out.counts.archives, M.b5.archives.length, "out.counts.archives === model.b5.archives.length");
  g.eq(out.counts.snapshots, M.b5.snapshots.length, "out.counts.snapshots === model.b5.snapshots.length");

  /* W-22 新增 5 个槽位：人话句的倍数 / 逐题极值（s1.costMultiplier、
     s2.bestTask / bestKeepPct / worstTask / worstKeepPct），39 + 5 = 44 */
  g.eq(keys.length, 44, "BINDINGS 唯一键数");

  const EXPECTED_DASH = { "meta.revision": true };
  const unresolved = [];
  const mismatched = [];
  const nodeCountBad = [];
  for (const k of keys) {
    const r = bindingText(k, M);
    if (!r.spec) { unresolved.push(k + "：BINDINGS 无 spec"); continue; }
    if (r.text === null || r.text === "") unresolved.push(k + "：空文本");
    else if (r.text === DASH && !EXPECTED_DASH[k]) unresolved.push(k + "：—（path/get 解析不到值）");

    const nodes = doc.querySelectorAll('[data-bind="' + k + '"]');
    const mentions = SLOTS.filter((s) => s === k).length;
    /* revision 为 null 时整个子句被摘除：节点按设计消失，不算「丢槽」 */
    const removedByDesign = k === "meta.revision" && revNull;
    if (!removedByDesign && nodes.length !== mentions) {
      nodeCountBad.push(k + "：DOM " + nodes.length + " ≠ HTML " + mentions);
    }
    for (const n of nodes) {
      if (textOf(n) !== r.text) {
        mismatched.push(k + "：DOM " + JSON.stringify(textOf(n)) + " ≠ 复算 " + JSON.stringify(r.text));
      }
    }
  }
  g.eqJson(unresolved, [], "44 个键全部解析出非空、非 — 的文本（meta.revision 除外）");
  g.eqJson(keys.filter((k) => bindingText(k, M).text === DASH), ["meta.revision"],
    "全 44 键中只有 meta.revision 允许是 —");
  g.eqJson(mismatched, [], "每个键在 DOM 中的文本 === 用 spec 复算的文本");
  g.eqJson(nodeCountBad, [], "每个键在 DOM 中的节点数 === HTML 里的出现次数（无丢槽 / 无凭空多槽）");

  const slotSet = [...new Set(SLOTS)].sort();
  g.eqJson(slotSet, keys.slice().sort(), "index.html 槽位集合 === BINDINGS 键集合（双向，无死键无未知）");
  g.eq(slotSet.length, 44, "唯一槽位数");

  /* 计数动效：属性值 = 文本数字（去千分位后），且必须是整数 */
  const counters = doc.querySelectorAll("[data-count]");
  const countBad = [];
  for (const n of counters) {
    const attr = String(n.getAttribute("data-count") || "");
    const shown = textOf(n).replace(/[\s,，]/g, "");
    if (!/^\d+$/.test(attr)) countBad.push(n.tagName + "：data-count 非整数 " + attr);
    if (shown.indexOf(".") !== -1) countBad.push(n.tagName + "：文本出现小数 " + shown);
    if (!/^\d+$/.test(shown)) countBad.push(n.tagName + "：文本非整数 " + shown);
    else if (Number(attr) !== Number(shown)) {
      countBad.push(n.tagName + "：data-count " + attr + " ≠ 文本 " + shown);
    }
  }
  g.ok(counters.length >= 1, "渲染后 [data-count] 数量（非空，否则下一条空转）", ">= 1", counters.length);
  g.eqJson(countBad, [], "[data-count] 数值 === 去千分位后的文本数字（整数，无小数）");
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

/* ---- A19：口径分屏 + 动效降级（无 JS / 减少动效也不白屏） ---- */
{
  const g = group("A19", "口径分屏与动效降级");
  const doc = PAGE_DOC;

  /* 12) 口径不混屏：按 <section class="slide"> 切分。
        「屏面正文」= 屏内文本，但整棵跳过 S8 常显的口径说明块 —— 那一块从 W-22 起
         就是正文的一部分（不再折叠），它的职责恰恰是把两个口径并列解释清楚；
         它不算屏面上的一条 claim，所以「并列」只许出现在那里（下面硬断言它必须并列、
         且必须标明各自来源）。屏面正文里仍然一个都不许并列。 */
  const claims = SLIDES.map((s) => ({ id: s.id, text: surfaceTextOf(s) }));
  const scanScreens = claims.filter((c) =>
    c.text.indexOf("−72") !== -1 || c.text.indexOf("0.009978") !== -1);
  const gateScreens = claims.filter((c) =>
    c.text.indexOf("0.010684") !== -1 || c.text.indexOf("42.6") !== -1);

  g.ok(scanScreens.length >= 1,
    "有屏出现扫描级数字（−72 / 0.009978），否则混屏断言空转", ">= 1",
    scanScreens.map((c) => c.id));
  g.ok(gateScreens.length >= 1,
    "有屏出现 gate 级数字（0.010684 / 42.6），否则混屏断言空转", ">= 1",
    gateScreens.map((c) => c.id));

  const scanIds = scanScreens.map((c) => c.id);
  const gateIds = gateScreens.map((c) => c.id);
  g.eqJson(scanIds.filter((id) => gateIds.indexOf(id) !== -1), [],
    "屏面正文里扫描级数字与 gate 级数字不落在同一屏");
  g.eqJson(scanIds, ["s3"], "屏面正文里的扫描级数字只在 S3（单题成本 · 扫描级脚注）");
  g.eqJson(gateScreens.filter((c) => c.text.indexOf("gate 级") === -1).map((c) => c.id), [],
    "每个屏面含 0.010684 / 42.6 的屏都写明「gate 级」");
  g.eqJson(scanScreens.filter((c) => c.text.indexOf("扫描级") === -1).map((c) => c.id), [],
    "每个屏面含 −72 / 0.009978 的屏都写明「扫描级」");

  /* 排除范围受控（W-22 结构变更后的新锚点）：
     ① index.html 里不再有 <details>（口径说明是常显正文，不是可展开附录）；
     ② 被跳过的只有「口径说明块」这一个块，且它必须同时给出两个口径 + 各自来源。 */
  g.eq(countLit(SRC["index.html"], "<details"), 0,
    "index.html 不含 <details（口径说明常显，不再是折叠块）");
  const scopeBox = scopeBlockOf(doc);
  g.ok(!!scopeBox, "存在口径说明块（#warnings / .colophon 下唯一非 error 的 .warnings）",
    "1 个", scopeBox ? scopeBox.tagName : null);
  g.ok(!!scopeBox && scopeBox.tagName !== "DETAILS",
    "口径说明块不是 <details>（是常显的 section）", "非 DETAILS",
    scopeBox ? scopeBox.tagName : null);
  const s8Slide = SLIDES.filter((s) => s.id === "s8")[0] || null;
  g.eq(!!scopeBox && !!s8Slide && isDescendantOf(scopeBox, s8Slide), true,
    "口径说明块常显在 S8（作为正文，而不是附录）", true,
    scopeBox ? (scopeBox.parentNode ? scopeBox.parentNode.className : null) : null);

  const scopeText = textOf(scopeBox);
  g.eqJson(
    ["0.009978", "0.010684"].filter((lit) => scopeText.indexOf(lit) === -1),
    [],
    "口径说明块必须同时给出两个口径的数字（$0.009978 扫描级 / $0.010684 gate 级）"
  );
  g.ok(scopeText.indexOf("扫描级") !== -1 && scopeText.indexOf("ADR-0008") !== -1,
    "口径说明块里扫描级的来源标明为 ADR-0008", "含 扫描级 + ADR-0008", scopeText.slice(0, 80));
  g.ok(scopeText.indexOf("gate 级") !== -1 && scopeText.indexOf("配对") !== -1,
    "口径说明块里 gate 级的来源标明为门禁配对", "含 gate 级 + 配对", scopeText.slice(0, 120));

  /* 屏面正文里两个口径的数字一个都不许并列（逐屏双向） */
  const bothOnSurface = claims.filter((c) =>
    (c.text.indexOf("0.009978") !== -1 || c.text.indexOf("−72") !== -1) &&
    (c.text.indexOf("0.010684") !== -1 || c.text.indexOf("42.6") !== -1));
  g.eqJson(bothOnSurface.map((c) => c.id), [],
    "屏面正文（排除口径块）不得并列两个口径的数字");

  /* 被排除的必须只有口径块本身：colophon 里的其它块（数据错误）不许藏口径数字 */
  const colophon = doc.querySelector(".colophon");
  const otherNotes = colophon
    ? textExcluding(colophon, scopeBox ? [scopeBox] : [])
    : "";
  g.eqJson(
    ["0.009978", "0.010684", "42.6"].filter((lit) => otherNotes.indexOf(lit) !== -1),
    [],
    "colophon 内口径块之外的块不夹带口径数字（排除范围没有放水）"
  );

  /* 13) #verify-note 只写定性句：没有任何数字 / 金额 / 秒数 */
  const noteText = textOf(doc.getElementById("verify-note"));
  g.ok(noteText.length >= 8, "#verify-note 非空（否则定性断言空转）", ">= 8 字", noteText.length);
  g.eq(countRe(noteText, /\$\d/g), 0, "#verify-note 不含「$数字」形式的金额");
  g.eq(countRe(noteText, /\d+\.\d+s/g), 0, "#verify-note 不含「N.Ns」形式的时长");
  g.eq(countRe(noteText, /\d+\.\d+/g), 0, "#verify-note 不含任何小数（不写候选侧金额 / 墙钟）");
  g.eq(countRe(noteText, /\d/g), 0, "#verify-note 不含任何数字（纯定性表述）");
  g.ok(IS_ADR_SCAN ? noteText.indexOf("引用值") !== -1 : noteText.indexOf("search_results") !== -1,
    "#verify-note 指明候选侧是引用值 / 两侧来自 search_results",
    IS_ADR_SCAN ? "含 引用值" : "含 search_results", noteText);

  /* 14) 口径说明在表格之前（同屏可读） */
  const order = docOrder(doc);
  const scopeNode = doc.getElementById("pair-scope-note");
  const tableNode = doc.querySelector("table.pairs");
  g.ok(
    !!scopeNode && !!tableNode &&
      order.indexOf(scopeNode) !== -1 && order.indexOf(tableNode) !== -1 &&
      order.indexOf(scopeNode) < order.indexOf(tableNode),
    "#pair-scope-note 在 DOM 中位于 table.pairs 之前",
    "scope < table",
    { scope: order.indexOf(scopeNode), table: order.indexOf(tableNode) }
  );
  g.ok(!!scopeNode && hasAncestorClass(scopeNode, "slide"),
    "#pair-scope-note 与表格在同一屏内（S4）", true,
    scopeNode ? scopeNode.parentNode.className : null);

  /* 15) app.js：IntersectionObserver + prefers-reduced-motion + IO 缺失时的降级分支 */
  const app = SRC["app.js"];
  g.ok(app.indexOf("IntersectionObserver") !== -1, "app.js 使用 IntersectionObserver", true, null);
  g.ok(/prefers-reduced-motion/.test(app) && /matchMedia/.test(app),
    "app.js 用 matchMedia 读 prefers-reduced-motion", true, null);
  g.ok(/!\s*root\.IntersectionObserver|typeof\s+IntersectionObserver/.test(app),
    "app.js 有 IntersectionObserver 不可用的降级分支", true, null);

  const fbStart = app.indexOf("if (!root.IntersectionObserver)");
  const fbEnd = fbStart === -1 ? -1 : app.indexOf("return;", fbStart);
  const fbBlock = fbStart === -1 || fbEnd === -1 ? "" : app.slice(fbStart, fbEnd);
  g.ok(fbBlock.indexOf("is-active") !== -1,
    "降级分支把全部屏标为 is-active（无 IO 也静态可读）", "含 is-active",
    fbBlock.replace(/\s+/g, " ").slice(0, 90));

  g.ok(/if\s*\(!reduce\)\s*\{[\s\S]{0,160}?js-ready/.test(app),
    "只有非 reduce 时才加 .js-ready（减少动效时不隐藏内容）", true, null);
  g.ok(/className\s*\+=\s*"\s*js"/.test(app), "app.js 会给 <html> 加 js 类", true, null);

  /* 16) 隐藏初始态必须限定在 .js-ready 下（否则无 JS 白屏）。
         W-22 起 @keyframes ink-bloom 里也有 opacity:0 —— 那是关键帧的动画值，
         不是「初始隐藏规则」，所以先在解析前把 @keyframes 整块摘掉再判。 */
  const revealHide = CSS_RULES.filter((r) =>
    r.sel.indexOf("[data-reveal]") !== -1 &&
    /opacity\s*:\s*0(?:\.0+)?\s*(?:!\s*important\s*)?;/.test(r.body));
  g.ok(revealHide.length >= 1, "存在 [data-reveal] 的初始隐藏规则（否则空转）", ">= 1", revealHide.length);
  g.eqJson(revealHide.filter((r) => !/^\.js-ready\b/.test(r.sel)).map((r) => r.sel), [],
    "所有 [data-reveal] 的 opacity:0 规则前缀都是 .js-ready");

  const kfList = keyframesBlocks(SRC["app.css"]);
  g.ok(kfList.length >= 1, "app.css 里的 @keyframes 数量（否则下面两条空转）", ">= 1", kfList.length);
  const staticRules = cssRules(cssWithoutKeyframes(SRC["app.css"]));
  const allHidden = staticRules.filter((r) =>
    /(?:^|[;{\s])opacity\s*:\s*0(?:\.0+)?\s*;/.test(r.body));
  g.ok(allHidden.length >= 1, "app.css 里的 opacity:0 初始态规则数（排除 @keyframes 后）",
    ">= 1", allHidden.length);
  g.eqJson(allHidden.filter((r) => !/^\.js-ready\b/.test(r.sel)).map((r) => r.sel), [],
    "排除 @keyframes 后，app.css 里所有 opacity:0 静态声明都限定在 .js-ready 内");

  /* 关键帧里允许 opacity:0，但只允许 ink-bloom 一个 keyframes 名下（写入动画，不是初态） */
  const kfOpacityZero = kfList.filter((b) => /opacity\s*:\s*0(?:\.0+)?\s*[;}]/.test(b.body))
    .map((b) => b.name);
  g.eqJson(kfOpacityZero, ["ink-bloom"],
    "@keyframes 内出现 opacity:0 的只有 ink-bloom（动画首尾帧，不是静态隐藏）");
  g.eq(kfList.filter((b) => /opacity\s*:\s*0(?:\.0+)?\s*[;}]/.test(b.body)).length, 1,
    "含 opacity:0 的 keyframes 块数（恰好 1 个，允许名单没有放水）");

  /* 17) 减少动效：媒体查询块要真的把动效关掉（至少 2 个相关属性） */
  const rm = CSS_RULES.filter((r) => /prefers-reduced-motion/.test(r.sel) && /reduce/.test(r.sel));
  g.ok(rm.length >= 1, "存在 prefers-reduced-motion: reduce 块", ">= 1", rm.length);
  const rmKeywords = ["opacity", "transform", "animation", "transition"]
    .filter((k) => rm.some((r) => r.body.indexOf(k) !== -1));
  g.ok(rmKeywords.length >= 2,
    "减少动效块内同时覆盖 opacity / transform / animation / transition 中至少两个",
    ">= 2", rmKeywords);
  g.ok(rm.some((r) => /animation\s*:\s*none/.test(r.body)), "减少动效块把 animation 关掉", true, null);
  g.ok(rm.some((r) => /opacity\s*:\s*1\s*(?:!\s*important)?/.test(r.body)),
    "减少动效块把隐藏元素显式置为 opacity:1", true, null);
}

/* ---- A20：W-14 收口的三条硬断言（形态无关） ---- */
{
  const g = group("A20", "W-14：降幅自洽 / 合计复算 / 口径说明前置");

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

/* ---- A21：渲染后的口径与文案（旧 A13 的语义断言按 8 屏重锚） ---- */
{
  const g = group("A21", "渲染后的口径与文案（8 屏）");
  const doc = PAGE_DOC;
  const F = M.fmt;
  const slideText = {};
  for (const s of SLIDES) slideText[s.id] = textOf(s);
  const pageText = textOf(doc.documentElement);
  const s1 = slideText.s1 || "";
  const s2 = slideText.s2 || "";
  const s3 = slideText.s3 || "";
  const s4 = slideText.s4 || "";
  const s5 = slideText.s5 || "";
  const s6 = slideText.s6 || "";
  const s7 = slideText.s7 || "";
  /* S8 的 claim 只看屏面正文：口径说明块（常显）单独在 A22 里验，不借它的字凑 claim */
  const s8 = SLIDES.length > 0 ? surfaceTextOf(SLIDES[SLIDES.length - 1]) : "";
  const dsp = M.s1 && M.s1.scan ? M.s1.scan.display : {};

  /* ---- 封面：只写题数，不写成本 / 降幅（数字后移到 S3） ---- */
  g.ok(s1.indexOf(String(M.meta.searchTaskCount)) !== -1 && s1.indexOf("道题") !== -1,
    "S1 封面写明题数（N 道题，N 来自 meta.searchTaskCount）", "含 N 道题",
    s1.slice(0, 80));
  g.eqJson(["0.035560", "0.009978", "−72", "70.0"].filter((l) => s1.indexOf(l) !== -1), [],
    "S1 封面不出现成本 / 降幅数字");
  g.ok(s1.indexOf("high/full") !== -1 && s1.indexOf("low/full") !== -1,
    "S1 写明两次的开关（high/full → low/full）", "含两个开关组合", s1.slice(0, 120));

  /* ---- 扫描级 vs gate 级：按屏断言（核心口径纪律） ---- */
  if (IS_ADR_SCAN) {
    g.ok(s3.indexOf("−72%") !== -1,
      "S3 含扫描级降幅 −72%（引 ADR-0008，整数口径）", "含 −72%", s3);
    g.eq(countLit(s3, "−72.0"), 0, "S3 不出现 −72.0（去掉 71.9 vs 72.0 的假精度）");
    g.ok(s3.indexOf("0.035560") !== -1 && s3.indexOf("0.009978") !== -1,
      "S3 含扫描级两侧金额（0.035560 → 0.009978）", "两个金额", s3);
    g.ok(s3.indexOf("41.7") !== -1, "S3 含扫描级 claim 墙钟 41.7", "含 41.7", s3);
    g.eqJson(["0.01068", "42.6", "0.008471"].filter((l) => s3.indexOf(l) !== -1), [],
      "S3 不含 gate 级候选配对数字（0.01068 / 42.6 / 0.008471）");
    g.eq(textOf(doc.getElementById("scan-note-basis")), "引用 ADR-0008",
      "S3 写明扫描级口径来源「引用 ADR-0008」");

    g.ok(s4.indexOf("0.010684") !== -1, "S4 含 gate 级候选配对合计 0.010684", "含 0.010684", s4.slice(0, 120));
    g.eqJson(["−72", "0.009978", "41.7"].filter((l) => s4.indexOf(l) !== -1), [],
      "S4 不含扫描级数字（−72 / 0.009978 / 41.7）");

    const scope = textOf(doc.getElementById("pair-scope-note"));
    g.ok(scope.indexOf("9 条 replay") !== -1, "#pair-scope-note 含「9 条 replay」", "含 9 条 replay", scope);
    g.ok(scope.indexOf("smoke-add") !== -1, "#pair-scope-note 点名 live 题 smoke-add", "含 smoke-add", scope);
    g.ok(scope.indexOf("0.010684") !== -1, "#pair-scope-note 含 gate 级配对成本 0.010684", "含 0.010684", scope);
    g.ok(scope.indexOf("gate 级") !== -1, "#pair-scope-note 标明这是 gate 级口径", "含 gate 级", scope);
    g.ok(scope.indexOf("不等于扫描级") !== -1, "#pair-scope-note 声明不等于扫描级", "含 不等于扫描级", scope);
    g.ok(scope.indexOf("不要对逐 run 成本求和") !== -1, "#pair-scope-note 保留「不对逐 run 求和」的告诫",
      "含 不要对逐 run 成本求和", scope);
    g.ok(scope.indexOf("同一道题在多条扫描里各有一条 run") !== -1,
      "#pair-scope-note 给出「不要求和」的理由（同题在多条扫描里重复）",
      "含 同一道题在多条扫描里各有一条 run", scope);
  } else {
    const baseUsd = "$" + F.usd(dsp.baseCostUsd);
    const candUsd = "$" + F.usd(dsp.candCostUsd);
    g.ok(s3.indexOf(baseUsd) !== -1 && s3.indexOf(candUsd) !== -1,
      "S3 的扫描级金额 === search_results 复算", baseUsd + " / " + candUsd, s3.slice(0, 160));
    g.ok(s3.indexOf(F.sec(dsp.baseWallMs)) !== -1 && s3.indexOf(F.sec(dsp.candWallMs)) !== -1,
      "S3 的扫描级墙钟 === search_results 复算",
      F.sec(dsp.baseWallMs) + " / " + F.sec(dsp.candWallMs), s3.slice(0, 200));
    g.eqJson(["−72", "0.009978", "41.7"].filter((l) => s3.indexOf(l) !== -1), [],
      "S3 不出现 ADR 常量（−72 / 0.009978 / 41.7）");
    g.eq(textOf(doc.getElementById("scan-note-basis")), "两侧由 search_results 复算",
      "S3 写明扫描级口径来源「两侧由 search_results 复算」");

    const scope = textOf(doc.getElementById("pair-scope-note"));
    g.ok(scope.indexOf("两次扫描都是 live 运行") !== -1,
      "#pair-scope-note 改为「两次扫描都是 live 运行」", "含 两次扫描都是 live 运行", scope);
    g.ok(scope.indexOf("没有 replay/fixture 混合") !== -1,
      "#pair-scope-note 声明没有 replay/fixture 混合", "含 没有 replay/fixture 混合", scope);
    g.eq(scope.indexOf("9 条 replay"), -1, "data 形态不再声称「9 条 replay」");
    g.eq(countLit(pageText, "0.009978"), 0, "data 形态整页不出现 ADR 候选常量 0.009978");
  }

  /* ---- gate 级数字的段落级标注（行 / 单元格 / 列表项都算同一段） ---- */
  if (IS_ADR_SCAN) {
    const critNodes = [];
    const seen = new Set();
    for (const lit of ["0.01068", "42.6"]) {
      for (const n of minimalContainers(doc.documentElement, lit)) {
        const c = noteContainer(n);
        if (c && !seen.has(c)) { seen.add(c); critNodes.push(c); }
      }
    }
    g.ok(critNodes.length >= 1,
      "整页确实出现 gate 级候选配对数字（否则下面两条守卫空转）", ">= 1 个段落", critNodes.length);
    g.eqJson(
      critNodes.filter((n) => textOf(n).indexOf("gate 级") === -1)
        .map((n) => n.tagName + "#" + n.id + " " + textOf(n).slice(0, 60)),
      [],
      "每个含 0.01068 / 42.6 的段落都写明「gate 级」"
    );
    g.eqJson(
      critNodes.filter((n) => textOf(n).indexOf("−72") !== -1)
        .map((n) => n.tagName + " " + textOf(n).slice(0, 60)),
      [],
      "含 0.01068 / 42.6 的段落不得同时出现 −72（扫描级降幅只留在 S3）"
    );
  }

  /* ---- S2 通过题数：静态文案必须与数据一致 ---- */
  g.eq(M.s1.solvedDelta, 0,
    "数据前提：s1.solvedDelta === 0（S2 静态文案「成功率没有变化」必须为真）");
  g.ok(s2.indexOf("成功率没有变化") !== -1, "S2 文案写明「成功率没有变化」", "含 成功率没有变化", s2);
  g.ok(s2.indexOf("这是门禁的前提，不是结论") !== -1,
    "S2 把成功率不变定位为门禁前提、不是结论", "含 这是门禁的前提，不是结论", s2);
  g.ok(s2.indexOf(String(M.s1.solvedBase)) !== -1 && s2.indexOf(String(M.s1.solvedCand)) !== -1,
    "S2 两侧通过题数 === model（solvedBase / solvedCand）",
    M.s1.solvedBase + " / " + M.s1.solvedCand, s2);
  g.ok(s2.indexOf("3/10") !== -1 && s2.indexOf("calibrate") !== -1 && s2.indexOf("不是真实难度") !== -1,
    "S2 脚注把 fixture 3/10 限定为 calibrate 校准口径（不当真实基线）",
    "含 3/10 + calibrate + 不是真实难度", s2);

  /* ---- S3 KPI：gate 级 cps 两侧与降幅文案 ---- */
  g.eq(textOf(doc.querySelectorAll('[data-bind="s1.cpsBase"]')[0]),
    F.usd(M.s1.cpsBase), "S3 KPI 基线 cps 文本 === fmt.usd(s1.cpsBase)");
  g.eq(textOf(doc.querySelectorAll('[data-bind="s1.cpsCand"]')[0]),
    F.usd(M.s1.cpsCand), "S3 KPI 候选 cps 文本 === fmt.usd(s1.cpsCand)");
  g.eq(textOf(doc.querySelectorAll('[data-bind="s1.cpsDropText"]')[0]), M.s1.cpsDropText,
    "S3 降幅文案 === model.s1.cpsDropText（负号由 model 给）");
  g.eq(countLit(pageText, "−−"), 0, "整页不出现「−−」（负号只出现一次）");
  g.ok(SRC["index.html"].indexOf("−<b data-bind=") === -1,
    "index.html 不再把负号硬编码在 binding 前面", "不含 −<b data-bind=", null);
  g.ok(s3.indexOf("gate 级") !== -1, "S3 标题 / 脚注标明 gate 级口径", "含 gate 级", s3.slice(0, 60));

  /* ---- S4 配对表：10 行 × 7 列逐项复算 ---- */
  const body = doc.getElementById("pairs-body");
  const rows = body ? body.children.filter((c) => typeof c !== "string") : [];
  g.eq(rows.length, M.s2.pairs.length, "配对表行数 === model.s2.pairs.length");
  const rowBad = [];
  rows.forEach((tr, i) => {
    const p = M.s2.pairs[i] || {};
    const tds = tr.children.filter((c) => typeof c !== "string");
    if (tds.length !== 7) { rowBad.push("第 " + (i + 1) + " 行：" + tds.length + " 列"); return; }
    const t = tds.map(textOf);
    if (t[0] !== String(p.index)) rowBad.push("第 " + (i + 1) + " 行 index：" + t[0]);
    if (t[1].indexOf(String(p.taskId)) === -1) rowBad.push("第 " + (i + 1) + " 行 taskId：" + t[1]);
    if (t[2] !== "$" + F.usd(p.baseCost)) rowBad.push("第 " + (i + 1) + " 行基线成本：" + t[2]);
    if (t[3] !== "$" + F.usd(p.candCost)) rowBad.push("第 " + (i + 1) + " 行候选成本：" + t[3]);
    const sign = p.costDelta < 0 ? "−" : (p.costDelta > 0 ? "+" : "");
    if (t[4] !== sign + F.usd(Math.abs(p.costDelta))) rowBad.push("第 " + (i + 1) + " 行成本差：" + t[4]);
    const pd = Math.round(p.passedDelta);
    const pdText = pd < 0 ? "−" + Math.abs(pd) : (pd > 0 ? "+" + pd : "0");
    if (t[5] !== pdText) rowBad.push("第 " + (i + 1) + " 行通过差：" + t[5]);
  });
  g.eqJson(rowBad, [], "10 行 × 7 列的文本与 model.s2.pairs 逐项一致");
  const liveRows = rows.filter((r) =>
    doc._findAll(r, (n) => n.classList.contains("tag-live")).length > 0);
  g.eq(liveRows.length, M.s2.pairs.filter((p) => p.live === true).length,
    "带 .tag-live 的行数 === model.s2.pairs 里 live 行的数量");

  /* live 标签必须挂在「数据面里真有 kind=live run」的那道题上 */
  const liveRunTasks = new Set(RUNS.filter((r) => r && r.scope === "search" &&
    r.provenance && r.provenance.kind === "live" && typeof r.task_id === "string")
    .map((r) => r.task_id));
  const expectedLiveTasks = M.s2.pairs.map((p) => p.taskId).filter((t) => liveRunTasks.has(t));
  const liveRowTasks = liveRows.map((r) => {
    const tds = r.children.filter((c) => typeof c !== "string");
    const cell = tds[1] ? textOf(tds[1]) : "";
    const hit = expectedLiveTasks.filter((t) => cell.indexOf(t) !== -1);
    return hit.length === 1 ? hit[0] : null;
  });
  g.eqJson(liveRowTasks.slice().sort(), expectedLiveTasks.slice().sort(),
    "带 .tag-live 的行指向的题目 === 数据面 kind=live 的题（不多不少）");

  const totalRow = doc.querySelector("tr.pairs-total");
  g.ok(!!totalRow, "配对表有合计行 tr.pairs-total", true, !!totalRow);
  g.ok(!!totalRow && hasAncestorClass(totalRow, "pairs"),
    "合计行在 table.pairs 内（tfoot）", true,
    totalRow && totalRow.parentNode ? totalRow.parentNode.tagName : null);
  const totalText = textOf(totalRow);
  g.ok(totalText.indexOf("gate 级配对合计") !== -1,
    "合计行写明「gate 级配对合计」", "含 gate 级配对合计", totalText);
  g.ok(totalText.indexOf("不等于扫描级") !== -1,
    "合计行写明「不等于扫描级」", "含 不等于扫描级", totalText);
  g.ok(typeof M.s2.totals.base === "number" && totalText.indexOf("$" + F.usd(M.s2.totals.base)) !== -1,
    "合计行基线合计 === s2.totals.base", "$" + F.usd(M.s2.totals.base), totalText);
  g.ok(typeof M.s2.totals.cand === "number" && totalText.indexOf("$" + F.usd(M.s2.totals.cand)) !== -1,
    "合计行候选合计 === s2.totals.cand", "$" + F.usd(M.s2.totals.cand), totalText);
  const deltaText = "−" + F.usd(Math.abs(M.s2.totals.delta));
  g.ok(totalText.indexOf(deltaText) !== -1,
    "合计行成本差 === s2.totals.delta（带符号）", deltaText, totalText);

  /* ---- S5 门禁结论行 ---- */
  g.ok(s5.indexOf(String(M.s3.verdict)) !== -1 && s5.indexOf(String(M.s3.reason)) !== -1,
    "S5 结论行显示 verdict / reason", M.s3.verdict + " / " + M.s3.reason, s5);
  g.eqJson(chainBadges(doc), M.s3.chain.map(() => "通过"),
    "S5 六个徽章在真实数据下全「通过」");

  /* ---- S6 谱系 / 回退 ---- */
  g.ok(s6.indexOf("只改两个开关") !== -1 && s6.indexOf("high → low") !== -1,
    "S6 写明只改 thinking 一档（high → low）", "含 只改两个开关 + high → low", s6.slice(0, 120));
  const baseGid = LAST ? LAST.baseline_genome_id : null;
  const candGid = LAST ? LAST.candidate_genome_id : null;
  const linBase = M.b5.lineage.filter((r) => r && r.genomeId === baseGid)[0] || null;
  const linCand = M.b5.lineage.filter((r) => r && r.genomeId === candGid)[0] || null;
  g.ok(!!linBase && !!linCand, "谱系里能按 genome_id 找到门禁基线与候选", true,
    { base: !!linBase, cand: !!linCand });
  if (linBase && linCand) {
    const swKeys = ["thinking", "context", "tools"];
    const diff = swKeys.filter((k) => linBase.switches[k] !== linCand.switches[k]);
    g.eqJson(diff, ["thinking"], "基线与候选的 switches 只差 thinking（context / tools 不动）");
    g.eqJson([linBase.switches.thinking, linCand.switches.thinking], ["high", "low"],
      "thinking 从 high 降到 low（S6 静态文案的数据依据）");
  }

  const lin = doc.getElementById("lineage");
  const linRows = lin ? lin.children.filter((c) => typeof c !== "string") : [];
  g.eq(linRows.length, M.b5.lineage.length, "S6 谱系行数 === model.b5.lineage.length");
  const linTags = doc._findAll(lin, (n) => n.classList.contains("tag"));
  g.eqJson(linTags.map(textOf), ["起点 · 第 1 代", "已采纳 · 第 2 代", "未采纳"], "谱系三行标签文案");
  g.eq(countLit(pageText, "起点 · 第 1 代"), 1, "整页「起点 · 第 1 代」出现 1 次（基线不写成已采纳）");
  g.eq(countLit(pageText, "已采纳 · 第 2 代"), 1, "整页「已采纳 · 第 2 代」出现 1 次");

  const withParent = M.b5.lineage.filter((r) => r && r.parentShort);
  const arrows = doc._findAll(lin, (n) => n.classList.contains("lineage-parent"));
  g.eq(arrows.length, withParent.length, "谱系里的「←」条数 === 有 parent 的 genome 数");
  g.eqJson(arrows.map(textOf), withParent.map((r) => "← " + r.parentShort),
    "「←」后面跟的正是 parentShort");

  const chipBad = [];
  M.b5.lineage.forEach((r, i) => {
    const row = linRows[i];
    if (!row) { chipBad.push("第 " + (i + 1) + " 行缺失"); return; }
    const chips = doc._findAll(row, (n) => n.classList.contains("chip")).map(textOf);
    const want = [
      "thinking" + r.switches.thinking,
      "context" + r.switches.context,
      "tools" + r.switches.tools
    ];
    if (JSON.stringify(chips) !== JSON.stringify(want)) {
      chipBad.push("第 " + (i + 1) + " 行 " + JSON.stringify(chips) + " ≠ " + JSON.stringify(want));
    }
  });
  g.eqJson(chipBad, [], "每个谱系行的三档开关 chip 与 model 一致");

  /* ---- S7 留出集（仅聚合）/ 证据 / 归属 ---- */
  const holdBig = doc.querySelector(".holdout-big");
  const expectedHold = M.holdout.baseSolved + "/" + M.holdout.baseTotal +
    " → " + M.holdout.candSolved + "/" + M.holdout.candTotal;
  g.eq(textOf(holdBig), expectedHold, "S7 留出集大字 === 模型聚合值（A/B → C/D）");
  const aside = doc.querySelector(".aside");
  g.ok(!!aside && textOf(aside).indexOf("仅聚合") !== -1,
    "留出集标注「仅聚合」", "含 仅聚合", aside ? textOf(aside) : null);
  g.eq(!!aside && hasAncestorClass(aside, "colophon"), false,
    "「仅聚合」标注在屏面上，不藏在折叠块里");

  const searchIds = (DATA.scenario.task_sets && DATA.scenario.task_sets.search) || [];
  g.eqJson(searchIds.filter((id) => s7.indexOf(id) !== -1), [],
    "S7 不出现逐题 task id（留出集只许聚合）");

  const ev = doc.querySelector(".evidence");
  const evNums = ev ? doc._findAll(ev, (n) => n.classList.contains("ev-num")).map(textOf) : [];
  g.eqJson(evNums,
    [String(M.provenance.live), String(M.provenance.replay), String(M.provenance.fixture)],
    "S7 证据三数（live / replay / fixture）=== model.provenance");

  const noteNode = doc.querySelectorAll('[data-bind="provenance.note"]')[0];
  const pv = M.provenance;
  const noteLabel = pv.searchOnly === true ? "条 search 运行记录" : "条运行记录";
  let expectedNote = "共 " + pv.total + " " + noteLabel + "：live " + pv.live +
    " · replay " + pv.replay + " · fixture " + pv.fixture;
  if (typeof pv.reportRunsTotal === "number") {
    expectedNote += "（report 运行另有 " + pv.reportRunsTotal + " 条，只导出聚合）";
  }
  g.eq(textOf(noteNode), expectedNote, "S7 徽章说明的三数来自 runs 实算（report 条数单列）");
  g.ok(textOf(noteNode).indexOf("search 运行记录") !== -1,
    "徽章说明把总数限定在 search 运行记录", "含 search 运行记录", textOf(noteNode));
  if (DATA.report_results && DATA.report_results.report_runs_total &&
      typeof DATA.report_results.report_runs_total.count === "number") {
    g.ok(textOf(noteNode).indexOf("report 运行另有") !== -1,
      "report 运行条数单列（不混进 search 计数）", "含 report 运行另有", textOf(noteNode));
  }

  const attNode = doc.getElementById("holdout-attribution");
  const att = M.holdout.attribution || {};
  g.ok(!!attNode, "存在 #holdout-attribution（留出集归属提示）", true, !!attNode);
  g.eq(textOf(attNode), String(att.note || ""),
    "#holdout-attribution 文本 === model.holdout.attribution.note");
  g.ok(textOf(attNode).indexOf("switches") !== -1,
    "#holdout-attribution 提到 switches", "含 switches", textOf(attNode));
  if (att.sameSwitches !== false) {
    g.ok(textOf(attNode).indexOf("genome") !== -1,
      "#holdout-attribution 提到 genome", "含 genome", textOf(attNode));
  }
  g.eq(!!attNode && hasAncestorClass(attNode, "colophon"), false,
    "#holdout-attribution 不在折叠的口径说明里");

  /* ---- S8 收尾 / 元信息 / 口径说明折叠块 ---- */
  g.ok(s8.indexOf("成本降了") !== -1 && s8.indexOf(F.pct(M.s1.cpsDropPct) + "%") !== -1,
    "S8 结尾句的降幅 === fmt.pct(s1.cpsDropPct)（gate 级）",
    "成本降了 " + F.pct(M.s1.cpsDropPct) + "%", s8.slice(0, 60));
  g.ok(s8.indexOf("每一次采纳都能回退") !== -1, "S8 写明每一次采纳都能回退", "含 每一次采纳都能回退", s8);

  const metaLine = textOf(doc.querySelector(".meta-line"));
  g.ok(metaLine.indexOf("证据截至") !== -1, "S8 meta-line 含「证据截至」", "含 证据截至", metaLine);
  g.ok(metaLine.indexOf(F.iso(M.meta.generatedAt)) !== -1,
    "S8 meta-line 的截止时间 === fmt.iso(meta.generatedAt)", F.iso(M.meta.generatedAt), metaLine);

  const colophonText = textOf(doc.querySelector(".colophon"));
  g.eq(countLit(colophonText, "证据截至"), 0,
    ".colophon 不再承载「证据截至」（已移到 S8 meta-line）");

  /* W-22：口径说明不再是「口径说明（N）」折叠块，而是常显 section + 逐条 li。
     条数锚点从标题文案改成 li 数（文案换了不算违规，条数少了才算）。 */
  const warnBlock = scopeBlockOf(doc);
  g.ok(!!warnBlock, "存在口径说明块（否则条数断言空转）", "1 个",
    warnBlock ? warnBlock.tagName : null);
  const warnItems = warnBlock
    ? doc._findAll(warnBlock, (n) => n.tagName === "LI")
    : [];
  g.eq(warnItems.length, M.warnings.length,
    "口径说明块的 li 数 === model.warnings.length");
  g.eqJson(warnItems.filter((n) => textOf(n).length <= 10).map(textOf), [],
    "口径说明每条都是完整句子（文本 > 10 字，没有被截断成标题）");
  g.ok(colophonText.indexOf("口径说明") !== -1,
    ".colophon 里写明「口径说明」标题", "含 口径说明", colophonText.slice(0, 40));
  if (M.errors.length > 0) {
    g.ok(colophonText.indexOf("数据错误（" + M.errors.length + "）") !== -1,
      ".colophon 数据错误块条数 === model.errors.length",
      "数据错误（" + M.errors.length + "）", colophonText.slice(0, 60));
  } else {
    g.ok(colophonText.indexOf("数据错误") === -1,
      "无 errors 时不渲染「数据错误」块", "不含 数据错误", colophonText.slice(0, 60));
  }
}

/* ==========================================================================
 * A22：W-22 新特性 —— 人话句可复算 / 口径常显 / S6 标题放大 / 墨水晕染 /
 *      时长 / 性能结构
 *
 * 纪律：人话句里的每个数字都用「读 web/data + 自己算」独立复算，不碰 render 的
 * getter；视觉类（字体大小 / 晕染 / 时长 / contain）从 CSS / JS 文本断言，因为
 * DOM 桩没有样式引擎。
 * ========================================================================== */
{
  const g = group("A22", "W-22 新特性：人话句 / 口径常显 / 标题 / 晕染 / 时长 / 性能");
  const doc = PAGE_DOC;
  const appSrc = SRC["app.js"];
  const cssSrc = SRC["app.css"];

  /* ---- 独立复算（只用 web/data，不用 model 的派生 getter） ---- */
  const round1 = (n) => {
    const r = Math.round(Number(n) * 10) / 10;
    return r === 0 ? 0 : r;   // 抹掉 -0，与 model 的 round1 同口径但独立实现
  };
  const decisions = DATA.gate && Array.isArray(DATA.gate.decisions) ? DATA.gate.decisions : [];
  const dec = decisions.length > 0 ? decisions[0] : null;
  const rawPairs = dec && Array.isArray(dec.pairs) ? dec.pairs : [];

  const reportRows = DATA.report_results && Array.isArray(DATA.report_results.results)
    ? DATA.report_results.results
    : [];
  const rowOf = (gid) => reportRows.filter((r) => r && r.genome_id === gid)[0] || null;
  const holdoutCost = (r) => (r && r.scores && r.scores.holdout &&
    typeof r.scores.holdout.cost_total_usd === "number") ? r.scores.holdout.cost_total_usd : Infinity;

  /* 两侧怎么配对：这是导出契约（model.js 的 pickPair 同规则）——
     先用门禁 genome_id 精确配，配不齐就按 holdout 成本降序推断（贵的当基线）。
     本数据里候选侧 genome 与 report 行对不上，所以走的是推断分支（口径说明里也写了）。 */
  let baseRow = dec ? rowOf(dec.baseline_genome_id) : null;
  let candRow = dec ? rowOf(dec.candidate_genome_id) : null;
  const pairInferred = !baseRow || !candRow;
  if (pairInferred) {
    const usable = reportRows
      .filter((r) => r && r.scores && r.scores.holdout && holdoutCost(r) !== Infinity)
      .slice()
      .sort((a, b) => holdoutCost(b) - holdoutCost(a));
    if (usable.length >= 2) {
      baseRow = usable[0];
      candRow = usable[usable.length - 1];
    }
  }

  const baseCps = baseRow && baseRow.scores && baseRow.scores.search
    ? baseRow.scores.search.cost_per_solved_usd : null;
  const candCps = candRow && candRow.scores && candRow.scores.search
    ? candRow.scores.search.cost_per_solved_usd : null;
  const multCalc = (typeof baseCps === "number" && typeof candCps === "number" && candCps > 0)
    ? round1(baseCps / candCps) : null;

  const ratioOf = (p) => p.candidate_cost_usd / p.baseline_cost_usd;
  const scored = rawPairs
    .filter((p) => p && p.baseline_cost_usd > 0 && typeof p.candidate_cost_usd === "number")
    .slice()
    .sort((a, b) => ratioOf(a) - ratioOf(b));
  const bestP = scored.length > 0 ? scored[0] : null;
  const worstP = scored.length > 0 ? scored[scored.length - 1] : null;
  const bestKeepCalc = bestP ? round1(ratioOf(bestP) * 100) : null;
  const worstKeepCalc = worstP ? round1(ratioOf(worstP) * 100) : null;

  const holdBase = baseRow && baseRow.scores && baseRow.scores.holdout
    ? baseRow.scores.holdout : null;
  const holdCand = candRow && candRow.scores && candRow.scores.holdout
    ? candRow.scores.holdout : null;
  const droppedCalc = holdBase && holdCand &&
      typeof holdBase.solved === "number" && typeof holdCand.solved === "number"
    ? holdCand.solved < holdBase.solved : null;

  const fixed1 = (n) => (typeof n === "number" ? n.toFixed(1) : null);

  /* ---- 1. 人话句存在且可复算 ---- */
  g.ok(typeof M.s1.cpsBase === "number" && typeof baseCps === "number" &&
      Math.abs(M.s1.cpsBase - baseCps) < 1e-12,
    "s1.cpsBase === 数据面 report 基线 search cps（人话句的分子）", baseCps, M.s1.cpsBase);
  g.ok(typeof M.s1.cpsCand === "number" && typeof candCps === "number" &&
      Math.abs(M.s1.cpsCand - candCps) < 1e-12,
    "s1.cpsCand === 数据面 report 候选 search cps（人话句的分母）", candCps, M.s1.cpsCand);

  g.eq(M.s1.costMultiplier, multCalc,
    "s1.costMultiplier === round1(基线 cps / 候选 cps)（独立复算）");

  const s3Text = surfaceTextOf(SLIDES[2]);
  const s3PlainNode = doc._findAll(SLIDES[2], (n) => n.classList.contains("plain-talk"))[0];
  const s3Plain = s3PlainNode ? textOf(s3PlainNode) : "";
  g.ok(s3Text.indexOf("倍的题") !== -1, "S3 屏面正文含「倍的题」（人话句存在）",
    "含 倍的题", s3Text.slice(0, 120));

  const s3Phrase = /能跑\s*([\d.]+)\s*倍的题/.exec(s3Text);
  g.ok(!!s3Phrase && s3Phrase[1] === fixed1(multCalc),
    "S3 句子里写出的倍数 === round1(基线 cps / 候选 cps)（1 位小数）",
    fixed1(multCalc), s3Phrase ? s3Phrase[1] : null);

  const multNode = doc.querySelectorAll('[data-bind="s1.costMultiplier"]')[0];
  g.eq(multNode ? textOf(multNode) : null, fixed1(multCalc),
    "S3 倍数槽位的页面文本 === 同一复算值（数字不是另写的）");
  g.eqJson(numbersIn(s3Plain), [multCalc],
    "S3 人话句里的数字有且只有这个倍数（没有夹带别的口径数）");

  const s4Text = surfaceTextOf(SLIDES[3]);
  const s4PlainNode = doc._findAll(SLIDES[3], (n) => n.classList.contains("plain-talk"))[0];
  const s4Plain = s4PlainNode ? textOf(s4PlainNode) : "";
  g.ok(s4Text.indexOf("最省的一题") !== -1 && s4Text.indexOf("最少的一题") !== -1,
    "S4 屏面正文同时含「最省的一题」与「最少的一题」", "两个都在", s4Text.slice(0, 120));

  g.eq(M.s2.bestTask, bestP ? bestP.task_id : null,
    "s2.bestTask === 逐题 cand/base 最小者（按 gate.pairs 复算）");
  g.eq(M.s2.bestKeepPct, bestKeepCalc,
    "s2.bestKeepPct === round1(该题 cand/base × 100)（独立复算）");
  g.eq(M.s2.worstTask, worstP ? worstP.task_id : null,
    "s2.worstTask === 逐题 cand/base 最大者（按 gate.pairs 复算）");
  g.eq(M.s2.worstKeepPct, worstKeepCalc,
    "s2.worstKeepPct === round1(该题 cand/base × 100)（独立复算）");

  const bestPhrase = /最省的一题（([^）]+)）成本降到原来的\s*([\d.]+)%/.exec(s4Text);
  g.ok(!!bestPhrase && bestP &&
      bestPhrase[1] === bestP.task_id && bestPhrase[2] === fixed1(bestKeepCalc),
    "S4「最省的一题（X）…原来的 Y%」的 X / Y 都能按 pairs 复算",
    bestP ? bestP.task_id + " / " + fixed1(bestKeepCalc) : null,
    bestPhrase ? bestPhrase[1] + " / " + bestPhrase[2] : null);

  const worstPhrase = /最少的一题（([^）]+)）也降到\s*([\d.]+)%/.exec(s4Text);
  g.ok(!!worstPhrase && worstP &&
      worstPhrase[1] === worstP.task_id && worstPhrase[2] === fixed1(worstKeepCalc),
    "S4「最少的一题（X）也降到 Y%」的 X / Y 都能按 pairs 复算",
    worstP ? worstP.task_id + " / " + fixed1(worstKeepCalc) : null,
    worstPhrase ? worstPhrase[1] + " / " + worstPhrase[2] : null);

  g.eqJson(numbersIn(s4Plain), [bestKeepCalc, worstKeepCalc],
    "S4 人话句里的数字 === 两个复算出的保留率（顺序一致，无夹带）");

  const s7Slide = SLIDES.filter((s) => s.id === "s7")[0] || null;
  const plainOk = doc.getElementById("holdout-plain-ok");
  const plainDrop = doc.getElementById("holdout-plain-drop");
  const s7Visible = s7Slide ? visibleTextOf(s7Slide) : "";
  const s7PlainVisible = s7Slide ? visibleTextOf(doc.getElementById("holdout-plain")) : "";

  g.eq(M.holdout.holdoutNote.dropped, droppedCalc,
    "holdoutNote.dropped === 数据面两侧 holdout solved 比较（独立复算）");
  g.ok(droppedCalc === false
    ? s7Visible.indexOf("调参") !== -1 && s7Visible.indexOf("需人工复核") === -1
    : s7Visible.indexOf("需人工复核") !== -1 && s7Visible.indexOf("调参") === -1,
    "S7 可见文本的句式与 data 一致（没掉 → 调参；掉题 → 需人工复核）",
    droppedCalc === false ? "含 调参 / 不含 需人工复核" : "含 需人工复核 / 不含 调参",
    s7Visible.slice(0, 100));

  const visibleCount = [plainOk, plainDrop].filter((n) => n && n.hidden !== true).length;
  g.eq(visibleCount, 1, "S7 两条句式恰好一条可见（互斥，不并列）");
  g.eq(!!plainDrop && plainDrop.hidden === true, droppedCalc !== true,
    "S7 掉题句的 hidden === (dropped !== true)");
  g.eq(!!plainOk && plainOk.hidden === true, droppedCalc === true,
    "S7 没掉句的 hidden === (dropped === true)");
  g.eqJson(
    numbersIn(s7PlainVisible).filter((n) =>
      [holdCand ? holdCand.solved : null, holdCand ? holdCand.total : null,
        holdBase ? holdBase.solved : null, holdBase ? holdBase.total : null,
        M.meta.searchTaskCount].indexOf(n) === -1),
    [],
    "S7 可见人话句里的数字全部来自 web/data（holdout solved/total 或题数）"
  );
  g.eq(M.warnings.some((w) => String(w).indexOf("genome") !== -1 &&
      String(w).indexOf("未对齐") !== -1 && String(w).indexOf("推断") !== -1), pairInferred,
    "报告侧配对走推断时，口径说明必须写明「与门禁 genome 未对齐 + 按成本推断」（不静默推断）");

  /* ---- 2. 口径说明常显（不是折叠附录，也不许被藏起来） ---- */
  const s8Slide = SLIDES.filter((s) => s.id === "s8")[0] || null;
  const scope = scopeBlockOf(doc);
  const hiddenAncestors = [];
  for (let n = scope; n; n = n.parentNode) {
    if (n.hidden === true) hiddenAncestors.push((n.id || n.tagName) + "：hidden");
    if (n.getAttribute && n.getAttribute("aria-hidden") === "true") {
      hiddenAncestors.push((n.id || n.tagName) + "：aria-hidden");
    }
  }
  g.ok(!!scope, "存在口径说明块（否则下面两条空转）", "1 个", scope ? scope.tagName : null);
  g.eqJson(hiddenAncestors, [],
    "口径说明块及其祖先都不带 hidden / aria-hidden=\"true\"（真的常显）");
  const s8Inner = s8Slide ? doc._findAll(s8Slide, (n) => n.classList.contains("slide-inner"))[0] : null;
  g.eq(!!scope && !!s8Inner && isDescendantOf(scope, s8Inner), true,
    "口径说明块在 S8 的 .slide-inner 内（正文位置）", true,
    scope && scope.parentNode ? scope.parentNode.className : null);
  const detailsNodes = doc._findAll(doc.documentElement, (n) => n.tagName === "DETAILS");
  g.eq(detailsNodes.length, 0, "渲染后文档里没有 <details>（口径常显 + 本次无数据错误）");

  /* ---- 3. S6 标题放大（只放大 S6，其余屏继续用基线字号） ---- */
  const rootBody = (CSS_RULES.filter((r) => r.sel === ":root")[0] || { body: "" }).body;
  const h2Base = fontSizeDecl(["h2"], CSS_RULES);
  const h2BaseMax = h2Base ? maxFontRem(h2Base.decl, rootBody) : null;
  g.ok(!!h2Base && h2BaseMax !== null,
    "h2 基线字号可解析（var(--fs-title) → rem）", "> 0", h2BaseMax);

  const h2Of = (slide) => doc._findAll(slide, (n) => n.tagName === "H2")[0] || null;
  const h2Decl = (slide) => {
    const node = h2Of(slide);
    if (!node) return null;
    const sels = String(node.className || "").split(/\s+/).filter(Boolean).map((t) => "." + t);
    return sels.length === 0 ? null : fontSizeDecl(sels, CSS_RULES);
  };
  const s6Slide = SLIDES.filter((s) => s.id === "s6")[0] || null;
  const s6Decl = s6Slide ? h2Decl(s6Slide) : null;
  const s6Max = s6Decl ? maxFontRem(s6Decl.decl, rootBody) : null;
  const s8Decl = s8Slide ? h2Decl(s8Slide) : null;

  g.ok(!!s6Decl && s6Decl.sel === ".slide-h-lead" && s6Decl.decl !== (h2Base ? h2Base.decl : null),
    "#s6 h2 有独立的 font-size 声明（与 h2 基线不同）",
    ".slide-h-lead ≠ var(--fs-title)",
    s6Decl ? s6Decl.sel + " " + s6Decl.decl : null);
  g.eqJson(
    SLIDES.filter((s) => ["s2", "s3", "s4", "s5", "s7"].indexOf(s.id) !== -1)
      .filter((s) => h2Decl(s) !== null).map((s) => s.id),
    [],
    "S2 / S3 / S4 / S5 / S7 的 h2 没有独立字号（只有 S6 放大，其余走基线）"
  );
  g.ok(typeof s6Max === "number" && typeof h2BaseMax === "number" && s6Max >= h2BaseMax &&
      s6Max > h2BaseMax,
    "#s6 h2 的声明字号 ≥ 其它常规屏 h2 的基线字号（严格更大）",
    (h2BaseMax || 0) + "rem 起", s6Max);
  g.ok(!!s8Decl && s8Decl.sel === ".end-line" && maxFontRem(s8Decl.decl, rootBody) > s6Max,
    "S8 的 h2 是 .end-line 大字收尾（比 S6 更大）：例外受控，只在 S8 且只来自 .end-line",
    ".end-line 且 > " + s6Max, s8Decl ? s8Decl.sel + " " + s8Decl.decl : null);

  /* ---- 4. 墨水晕染（每屏一层三团；只动 transform / opacity；可被 reduce 关掉） ---- */
  const inkLayerPerSlide = SLIDES.map((s) =>
    doc._findAll(s, (n) => n.classList.contains("ink")).length);
  g.eqJson(inkLayerPerSlide, [1, 1, 1, 1, 1, 1, 1, 1], "每屏恰好 1 个 .ink 晕染层");
  g.eq(doc.querySelectorAll(".ink").length, 8, ".ink 总数（= 屏数）");
  const inkDots = doc.querySelectorAll(".ink").map((n) =>
    doc._findAll(n, (c) => c.tagName === "I").length);
  g.eqJson(inkDots, [3, 3, 3, 3, 3, 3, 3, 3], "每个 .ink 恰好 3 个 <i>（三团色斑）");
  g.eq(doc._findAll(doc.documentElement, (c) => c.tagName === "I").length, 24,
    "整页 <i> 总数 === 8 屏 × 3 团");

  const kfs = keyframesBlocks(cssSrc);
  const inkKf = kfs.filter((b) => b.name === "ink-bloom")[0] || null;
  g.ok(!!inkKf, "app.css 存在 @keyframes ink-bloom", "1 个", kfs.map((b) => b.name));
  const kfProps = inkKf
    ? [...new Set([...inkKf.body.matchAll(/([a-zA-Z-]+)\s*:/g)].map((m) => m[1]))].sort()
    : [];
  g.eqJson(kfProps, ["opacity", "transform"],
    "ink-bloom 只声明 transform 与 opacity（不引入滤镜 / 阴影这类昂贵属性）");
  g.eqJson(["filter", "backdrop-filter", "box-shadow"].filter((p) => inkKf &&
    inkKf.body.indexOf(p) !== -1), [],
    "ink-bloom 体内没有任何 filter / backdrop-filter / box-shadow");

  const inkAnimDur = CSS_RULES
    .filter((r) => r.sel.indexOf(".ink i") !== -1 && /animation-duration/.test(r.body))
    .flatMap((r) => durationsMs(r.body, "animation-duration"));
  g.ok(inkAnimDur.length >= 3, ".ink i 的 animation-duration 声明数（三团各一条）",
    ">= 3", inkAnimDur.length);
  g.ok(inkAnimDur.some((d) => d >= 1400), "至少一团晕染动画时长 >= 1400ms",
    ">= 1400ms", inkAnimDur);
  g.ok(inkAnimDur.every((d) => d >= 800), "所有晕染时长 >= 800ms（不是一闪而过）",
    "全部 >= 800ms", inkAnimDur);

  const rmRules = CSS_RULES.filter((r) => /prefers-reduced-motion/.test(r.sel) && /reduce/.test(r.sel));
  g.ok(rmRules.some((r) => /(?:^|[\s,])\.ink(?![-\w])/.test(r.body)),
    "prefers-reduced-motion: reduce 块里关掉 .ink（默认可被减动效关闭）",
    "含 .ink", rmRules.length);

  /* ---- 5. 时长加长（入场 / 计数都不再是抢帧的短脉冲） ---- */
  const revealDur = CSS_RULES
    .filter((r) => r.sel.indexOf("[data-reveal]") !== -1 && /transition\s*:/.test(r.body))
    .flatMap((r) => durationsMs(r.body, "transition"));
  g.ok(revealDur.length >= 1, "[data-reveal] 的 transition 时长声明数（否则空转）",
    ">= 1", revealDur.length);
  g.ok(revealDur.every((d) => d >= 800), "[data-reveal] 的入场时长全部 >= 800ms",
    "全部 >= 800ms", revealDur);

  const countMs = /var\s+COUNT_MS\s*=\s*(\d+)\s*;/.exec(appSrc);
  g.ok(!!countMs && Number(countMs[1]) >= 800, "app.js 计数时长常量 COUNT_MS >= 800ms",
    ">= 800", countMs ? countMs[1] : null);
  g.ok(/\bCOUNT_MS\b/.test(appSrc) && /\/\s*COUNT_MS/.test(appSrc),
    "COUNT_MS 真的被计数用上（不是定义了没人读）", "countUp 用它做进度分母", null);

  /* ---- 6. 性能结构（8 屏不再齐动；滚动回调里零样式写） ---- */
  const slideRules = CSS_RULES.filter((r) => r.sel === ".slide");
  g.ok(slideRules.length >= 1, "存在 .slide 规则（否则下面两条空转）", ">= 1", slideRules.length);
  g.ok(slideRules.some((r) => /contain\s*:\s*layout\s+paint/.test(r.body)),
    ".slide 规则含 contain: layout paint（单屏重绘不外溢）", "contain: layout paint",
    slideRules.map((r) => /contain\s*:\s*([^;]+);/.exec(r.body)).map((m) => (m ? m[1].trim() : null)));
  g.eqJson(slideRules.filter((r) => /transition\s*:[^;]*opacity/.test(r.body)).map((r) => r.sel), [],
    ".slide 规则不含 transition: … opacity（8 屏不再一起淡入）");
  const innerTrans = CSS_RULES.filter((r) => r.sel.indexOf(".slide-inner") !== -1 &&
    /transition\s*:/.test(r.body));
  g.ok(innerTrans.length >= 1, "内容入场改由 .slide-inner 承担（.slide 本身不动）",
    ">= 1", innerTrans.map((r) => r.sel));

  const onScrollBody = fnBodyOf(appSrc, "function onScroll(");
  g.ok(onScrollBody.length > 0, "能取出 onScroll 的函数体（否则下面两条空转）",
    "> 0 字符", onScrollBody.length);
  g.eqJson([".style.", "setProperty", "classList", "className"].filter((t) =>
    onScrollBody.indexOf(t) !== -1), [],
    "滚动回调体内不写样式 / 不摸 class（只排一次静止判定）");
  const parallaxBody = fnBodyOf(appSrc, "function applyParallax(");
  g.ok(parallaxBody.indexOf(".style.setProperty") !== -1,
    "样式写入集中在 applyParallax（经 rAF 调用）", "含 .style.setProperty", null);
  const settleBody = fnBodyOf(appSrc, "function settle(");
  g.ok(/\braf\(/.test(settleBody), "settle 通过 rAF 调度写入（不在 scroll 回调里同步写）",
    "含 raf(", settleBody.replace(/\s+/g, " ").slice(0, 80));
}

/* ---- A23：W-26 晕染每次激活都重播（上翻 / 下翻 / 圆点 / Home / End） ----
 * 旧契约把 bug 写成了规范（「同一屏不重播晕染」）。新契约反过来：
 * 动画挂在 .is-active 上 + restartInk 单一入口显式重启 → 任何入口进入任何一屏
 * 都从 0% 重播一次。下面逐条钉住这个行为，且不允许退回到方向分支。 */
{
  const g = group("A23", "W-26：晕染每次激活都重播（双向 / 圆点 / Home / End）");
  const appSrc = SRC["app.js"];
  const cssSrc = SRC["app.css"];
  const appLines = appSrc.split("\n");

  /* 1) 旧机制彻底消失（两处源码都不许再出现首访标记） */
  g.eq(countLit(appSrc, "is-first"), 0,
    "app.js 不含 is-first（「只加不删」的首访标记已移除）");
  g.eq(countLit(cssSrc, "is-first"), 0,
    "app.css 不含 is-first（动画不再挂在首访标记上）");

  /* 2) 动画绑定在 .is-active */
  const inkBind = CSS_RULES.filter((r) => /animation-name\s*:\s*ink-bloom/.test(r.body));
  g.eqJson(inkBind.map((r) => r.sel), [".js-ready .slide.is-active .ink i"],
    "ink-bloom 只由 .is-active 选择器绑定（每次激活都命中）");
  g.eqJson(inkBind.filter((r) => r.sel.indexOf("is-active") === -1).map((r) => r.sel), [],
    "没有任何一条 ink-bloom 绑定绕开 .is-active");
  const inkDurRules = CSS_RULES
    .filter((r) => r.sel.indexOf(".ink i") !== -1 && /animation-duration/.test(r.body));
  g.ok(inkDurRules.length >= 3 && inkDurRules.every((r) => r.sel.indexOf("is-active") !== -1),
    ".ink i 的三条 animation-duration 全部声明在 .is-active 上",
    ">= 3 且选择器都含 is-active", inkDurRules.map((r) => r.sel));

  /* 3) restartInk：重启的唯一入口，三要素齐全 */
  const restartBody = fnBodyOf(appSrc, "function restartInk(");
  g.ok(restartBody.length > 0, "app.js 存在 restartInk（晕染重启的唯一入口）",
    "> 0 字符", restartBody.length);
  g.ok(/style\.animation\s*=\s*"none"/.test(restartBody),
    "restartInk 先把 animation 置 none（取消正在进行的那次）",
    'animation = "none"', null);
  const reflowHits = countRe(restartBody, /offsetWidth|getBoundingClientRect/g);
  g.ok(reflowHits === 1,
    "restartInk 体内有且只有 1 处强制 reflow（每帧最多一次布局）",
    1, reflowHits);
  g.ok(/style\.animation\s*=\s*""/.test(restartBody),
    "restartInk 再把 animation 清空（选择器上的 keyframes 从 0% 重来）",
    'animation = ""', null);
  g.ok(/if\s*\([^)]*\breduce\b/.test(restartBody),
    "restartInk 在 prefers-reduced-motion 下直接返回（不写行内样式）",
    "含 reduce 卫语句", null);

  /* 4) 激活函数里无条件调用 —— 不在任何方向分支内 */
  const activateBody = fnBodyOf(appSrc, "function setActive(");
  g.ok(activateBody.indexOf("restartInk(") !== -1,
    "setActive 体内调用 restartInk（激活即重播）", "含 restartInk(", null);
  const callHits = appLines
    .map((l, i) => ({ l, n: i + 1 }))
    .filter((x) => x.l.indexOf("restartInk(") !== -1 &&
      x.l.indexOf("function restartInk(") === -1);
  g.eq(callHits.length, 1,
    "restartInk 全文件只有 1 处调用（不存在「只在某个方向里调」的第二处）");
  g.eqJson(callHits.filter((x) => {
    const from = Math.max(0, x.n - 9);
    const to = Math.min(appLines.length, x.n + 8);
    return appLines.slice(from, to).some((l) => /\bdir\b/.test(l));
  }).map((x) => x.n), [],
    "调用点上下 8 行内没有 dir（不是「只有向下翻才有晕染」）");
  const iActive = activateBody.indexOf('addClass(slides[index], "is-active")');
  const iRestart = activateBody.indexOf("restartInk(slides[index])");
  g.ok(iActive !== -1 && iRestart !== -1 && iActive < iRestart,
    "setActive 内顺序为 addClass(\"is-active\") → restartInk（先命中选择器再重启）",
    "is-active 在前", [iActive, iRestart]);

  /* 5) 三条入口最终都走同一个激活函数 */
  g.eq(countRe(appSrc, /function goTo\s*\(/g), 1, "goTo 只定义 1 次（单一入口）");
  g.eq(countRe(appSrc, /function setActive\s*\(/g), 1, "setActive 只定义 1 次（单一激活入口）");
  const goToBody = fnBodyOf(appSrc, "function goTo(");
  g.ok(/scrollIntoView/.test(goToBody),
    "goTo 只负责滚到目标屏（激活统一由 IO 回调落到 setActive）",
    "含 scrollIntoView", null);
  const keyBody = fnBodyOf(appSrc, "function onKeyDown(");
  g.ok(/goTo\(/.test(keyBody), "键盘（↑↓ / PageUp / PageDown / Space）经 goTo 进入",
    "含 goTo(", null);
  g.ok(/"Home"/.test(keyBody) && /"End"/.test(keyBody) &&
    countRe(keyBody, /goTo\(/g) >= 4,
    "Home / End 与逐屏键共用同一条 goTo 路径（≥4 个 goTo 调用）",
    ">= 4", countRe(keyBody, /goTo\(/g));
  const dotBody = fnBodyOf(appSrc, "function onDotClick(");
  g.ok(/goTo\(/.test(dotBody), "圆点点击经 goTo 进入", "含 goTo(", null);
  g.ok(/if\s*\(best\s*>=\s*0\)\s*setActive\(best\)/.test(appSrc),
    "IntersectionObserver 回调统一落到 setActive（滚轮 / 翻页最终都走它）",
    "setActive(best)", null);

  /* 6) 「播过」标记改由 seen 数组承载，is-revealed 语义不放宽 */
  g.ok(/\bvar seen\s*=\s*\[\]/.test(appSrc) && /seen\[index\]\s*=\s*true/.test(appSrc) &&
      /if\s*\(seen\[i\]\)\s*addClass\(slides\[i\], ["']is-revealed["']\)/.test(appSrc),
    "is-first 的「播过」职责由 seen 数组接手（is-revealed 仍只给播过的屏）",
    "seen[i] 守卫", null);

  /* 7) 重播不进入滚动回调（滚动期仍然零样式写入） */
  g.ok(["function onScroll(", "function settle(", "function applyParallax("]
    .every((h) => fnBodyOf(appSrc, h).indexOf("restartInk") === -1),
    "restartInk 不在 scroll / settle / applyParallax 内（只在落定的激活路径里）",
    "都不含 restartInk", null);
}

/* ---- A24：W-27 计数每次激活都重播 + 远跳抑制途经屏 ----
 * 旧实现把计数也写成「首访才播」（`data-counted` 一次性守卫），与晕染的
 * 新契约不一致；同时圆点 / Home / End 跨多屏跳转会在滚动途中把途经屏逐个
 * 激活，入场 + 晕染 + 计数各闪一遍。本组钉住两件事：
 *   1) 每次激活都从 0 重播，离开即收尾（不留半截数字），激活顺序固定；
 *   2) 远跳上「目标锁」，途经屏一律早退（不激活 / 不播晕染 / 不计数），
 *      并有 1200ms 兜底，相邻翻页不受影响。
 * 断言强度只增不减：不再存在「首访才播」的早退分支，顺序与入口都被逐条固定。 */
{
  const g = group("A24", "W-27：计数每次激活都重播 + 远跳抑制途经屏");
  const appSrc = SRC["app.js"];
  const cssSrc = SRC["app.css"];
  const appLines = appSrc.split("\n");

  /* ---------- 一、计数：每次激活都重播（不再有一次性守卫） ---------- */
  g.eq(countLit(appSrc, "data-counted"), 0,
    "app.js 不含 data-counted（「首访才播」的一次性守卫已移除）");
  g.eq(countLit(cssSrc, "data-counted"), 0,
    "app.css 不含 data-counted（没有绕开 JS 的「已播」样式钩子）");
  g.eq(countRe(appSrc, /dataset\.counted/g), 0,
    "app.js 不存在 dataset.counted 早退（if (!el.dataset.counted) return 一类写法）");

  const startBody = fnBodyOf(appSrc, "function startCounts(");
  const flushBody = fnBodyOf(appSrc, "function flushCounts(");
  g.ok(startBody.length > 0, "app.js 存在 startCounts(slide)（每次激活的计数入口）",
    "> 0 字符", startBody.length);
  g.ok(flushBody.length > 0, "app.js 存在 flushCounts(slide)（离开 / 抢占时收尾）",
    "> 0 字符", flushBody.length);

  /* startCounts：先取消该屏上一次未完成的 rAF，再从 0 起播 */
  const iStartFlush = startBody.indexOf("flushCounts(slide)");
  const iStartUp = startBody.indexOf("countUp(");
  g.ok(iStartFlush !== -1 && iStartUp !== -1 && iStartFlush < iStartUp,
    "startCounts 先 flushCounts(slide) 收掉该屏上一次的 rAF，再从头起播",
    "flush 在 countUp 之前", [iStartFlush, iStartUp]);
  g.ok(/node\.textContent = "0"/.test(fnBodyOf(appSrc, "function countUp(")),
    "countUp 每次都把文本重置为 0（不复用上一次进度）", 'textContent = "0"', null);
  g.ok(/hasRaf/.test(startBody) && /\breduce\b/.test(startBody) &&
      /textContent = finalTextOf\(/.test(startBody),
    "startCounts 在 reduce / 无 rAF 时直接写终值（不播）",
    "reduce || !hasRaf → finalTextOf", null);
  g.ok(/var hasRaf = typeof root\.requestAnimationFrame === "function"/.test(appSrc),
    "hasRaf 由特性检测得出（无 rAF 环境不假装动画）", "typeof typeof", null);
  g.ok(/\/ COUNT_MS/.test(fnBodyOf(appSrc, "function countUp(")),
    "countUp 仍以 COUNT_MS 作进度分母（900ms 不变）", "/ COUNT_MS", null);

  /* flushCounts：只收指定屏，且真的写终值 */
  g.ok(/c\.slide !== slide/.test(flushBody),
    "flushCounts(slide) 按屏过滤（只收尾正在离开的那一屏）", "c.slide !== slide", null);
  g.ok(/textContent = c\.finalText/.test(flushBody),
    "flushCounts 把未完成的数字直接写成终值（不留半截）", "= c.finalText", null);
  g.ok(/cancelRaf\(c\.id\)/.test(flushBody),
    "flushCounts 取消未完成的 rAF（收尾后不会再被旧帧覆盖）", "cancelRaf(c.id)", null);

  /* 激活顺序：addClass(is-active) → restartInk → flushCounts(旧屏) → startCounts(新屏) */
  const activateBody = fnBodyOf(appSrc, "function setActive(");
  const iActive = activateBody.indexOf('addClass(slides[index], "is-active")');
  const iRestart = activateBody.indexOf("restartInk(slides[index])");
  const iFlush = activateBody.indexOf("flushCounts(slides[prev])");
  const iStart = activateBody.indexOf("startCounts(slides[index])");
  g.ok(iActive !== -1 && iRestart !== -1 && iFlush !== -1 && iStart !== -1 &&
      iActive < iRestart && iRestart < iFlush && iFlush < iStart,
    "setActive 顺序固定：is-active → restartInk → flushCounts(旧屏) → startCounts(新屏)",
    "is-active < restartInk < flushCounts < startCounts", [iActive, iRestart, iFlush, iStart]);
  g.ok(/var prev = active;/.test(activateBody),
    "setActive 先记住上一屏（prev）再改 active（旧屏收尾有明确对象）", "var prev = active", null);

  /* 计数入口唯一：只有 setActive 调 startCounts（不存在「某个方向才播」的第二处） */
  const startCalls = appLines
    .map((l, i) => ({ l, n: i + 1 }))
    .filter((x) => /\bstartCounts\(/.test(x.l) &&
      x.l.indexOf("function startCounts(") === -1 &&
      !/^\s*(\*|\/\/)/.test(x.l));
  g.eq(startCalls.length, 1,
    "startCounts 全文件只有 1 处调用（激活函数的无条件路径）");

  /* 计数不吃滚动回调：滚动期零样式写入的约束不破 */
  g.ok(["function onScroll(", "function settle(", "function applyParallax("]
    .every((h) => fnBodyOf(appSrc, h).indexOf("startCounts") === -1),
    "startCounts 不在 scroll / settle / applyParallax 内（滚动途中不计数）",
    "都不含 startCounts", null);

  /* ---------- 二、远跳抑制：目标锁 + 途经屏早退 ---------- */
  const goToBody = fnBodyOf(appSrc, "function goTo(");
  g.ok(/Math\.abs\([^)]*\)\s*>\s*1/.test(goToBody),
    "goTo 内有远跳判定（Math.abs(...) > 1 视为跨 2 屏以上）", "Math.abs(...) > 1", null);
  g.ok(/pendingTarget = i;/.test(goToBody),
    "远跳时给 pendingTarget 赋值（上目标锁）", "pendingTarget = i", null);
  g.ok(/opts\s*&&\s*opts\.far === true/.test(goToBody) &&
      /opts\s*&&\s*opts\.far === false/.test(goToBody),
    "goTo 支持调用方覆盖 far（圆点 / Home / End 强制远跳，相邻键强制非远跳）",
    "opts.far === true / false", null);

  const lockMs = /var\s+FAR_LOCK_MS\s*=\s*(\d+)\s*;/.exec(appSrc);
  g.ok(!!lockMs && Number(lockMs[1]) === 1200,
    "兜底窗口 FAR_LOCK_MS === 1200ms", 1200, lockMs ? Number(lockMs[1]) : null);
  g.ok(/root\.setTimeout\(onFarTimeout, FAR_LOCK_MS\)/.test(goToBody),
    "上锁同时排一次 1200ms 兜底定时器", "setTimeout(onFarTimeout, FAR_LOCK_MS)", null);

  const timeoutBody = fnBodyOf(appSrc, "function onFarTimeout(");
  g.ok(timeoutBody.length > 0, "存在 onFarTimeout（兜底落屏）", "> 0 字符", timeoutBody.length);
  g.ok(/clearPending\(\)/.test(timeoutBody),
    "兜底触发时清锁（pendingTarget 归 null，不会永久挂着）", "clearPending()", null);
  g.ok(/var near = activeIndex\(\)/.test(timeoutBody) && /setActive\(near\)/.test(timeoutBody),
    "兜底按几何位置强制激活最近屏（中途取消滚动也不卡死）",
    "near = activeIndex() → setActive(near)", null);  g.ok(/pendingTimer = 0;/.test(timeoutBody),
    "兜底触发后定时器句柄归零（重复触发不叠定时器）", "pendingTimer = 0", null);

  const clearBody = fnBodyOf(appSrc, "function clearPending(");
  g.ok(/pendingTarget = null;/.test(clearBody) && /clearTimeout\(pendingTimer\)/.test(clearBody),
    "clearPending 同时清 pendingTarget 与定时器", "pendingTarget = null + clearTimeout",
    null);

  const intersectBody = fnBodyOf(appSrc, "function onIntersect(");
  g.ok(intersectBody.length > 0, "存在 onIntersect(best)（IO 的唯一落点）",
    "> 0 字符", intersectBody.length);
  g.ok(/if\s*\(pendingTarget !== null && best !== pendingTarget\)\s*return;/.test(intersectBody),
    "IO 回调里：锁未解除且不是目标屏 → 直接 return（途经屏不激活 / 不播晕染 / 不计数）",
    "pendingTarget !== null && best !== pendingTarget → return", null);
  g.ok(/if\s*\(best === pendingTarget\)\s*clearPending\(\)/.test(intersectBody),
    "到达目标屏时解锁（best === pendingTarget → clearPending）", "best === pendingTarget", null);
  g.ok(intersectBody.indexOf("restartInk") === -1 && intersectBody.indexOf("startCounts") === -1,
    "onIntersect 体内不直接调 restartInk / startCounts（必须经由 setActive）",
    "都不含", null);
  g.ok(/if\s*\(best\s*>=\s*0\)\s*onIntersect\(best\)/.test(appSrc),
    "IntersectionObserver 回调先过 onIntersect 再激活（锁只在这一层生效）",
    "onIntersect(best)", null);
  g.ok(["function onScroll(", "function settle(", "function applyParallax("]
    .every((h) => fnBodyOf(appSrc, h).indexOf("pendingTarget") === -1),
    "远跳锁不在 scroll / settle / applyParallax 内（滚动回调仍不写样式）",
    "都不含 pendingTarget", null);

  /* 各入口的 far 标记 */
  const dotBody = fnBodyOf(appSrc, "function onDotClick(");
  g.ok(/goTo\(/.test(dotBody) && /far:\s*true/.test(dotBody),
    "圆点点击一律按远跳处理（far: true）", "goTo(..., { far: true })", null);
  const keyBody = fnBodyOf(appSrc, "function onKeyDown(");
  g.ok(countRe(keyBody, /far:\s*true/g) >= 2,
    "Home / End 走远跳（far: true，≥2 处）", ">= 2", countRe(keyBody, /far:\s*true/g));
  g.ok(countRe(keyBody, /far:\s*false/g) >= 2,
    "↑↓ / PageUp / PageDown 走相邻（far: false，≥2 处）",
    ">= 2", countRe(keyBody, /far:\s*false/g));
  g.ok(!/goTo\(cur \+ 1\)/.test(keyBody) && !/goTo\(cur - 1\)/.test(keyBody),
    "逐屏键不再调用无 opts 的 goTo（far 标记必须显式）",
    "无 goTo(cur ± 1)", null);
}

/* ==========================================================================
 * 3. 输出
 * ========================================================================== */

const BAR = "─".repeat(72);
console.log("molt · web/page 验收（W-19：8 屏白底翻页版）");
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
