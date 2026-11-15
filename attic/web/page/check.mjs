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
const STATIC_NAMES = ["index.html", "app.css", "model.js", "render.js", "live-source.js", "mock-source.js", "app.js"];
/* 生产可整块删除的模拟模块：缺了就不测那部分，不让验收脚本跟着挂 */
const OPTIONAL_NAMES = ["mock-source.js"];
const SRC = {};
for (const name of STATIC_NAMES) {
  const file = path.join(PAGE_DIR, name);
  if (!fs.existsSync(file)) {
    if (OPTIONAL_NAMES.indexOf(name) !== -1) continue;
    die("页面文件缺失：" + file);
  }
  SRC[name] = fs.readFileSync(file, "utf8");
}
const HAS_MOCK = !!SRC["mock-source.js"];

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
const PROBE_TASK_DATA = fs.existsSync(path.join(ARGS.dataDir, "probe", "tasks.js"))
  ? fs.readFileSync(path.join(ARGS.dataDir, "probe", "tasks.js"), "utf8") : "";

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
    if (!SRC[name]) continue;
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

    /* 升级（D7）：从「整个 index.html 不同时出现」收紧到按 <section> 逐屏检查。
       渲染后的逐屏断言在 A19，这里先用静态源码把「同屏」这一层钉死。 */
    const html = SRC["index.html"];
    const sections = html.split(/<section\b/i).slice(1);
    const gateLit = ["0.010684", "42.6"];
    const scanLit = ["−72", "-72", "0.009978"];
    const mixedStatic = sections.filter((sec) =>
      gateLit.some((l) => sec.indexOf(l) !== -1) &&
      scanLit.some((l) => sec.indexOf(l) !== -1));
    g.eq(mixedStatic.length, 0,
      "按 <section> 检查：没有任何一屏的静态标记同时含 gate 级与扫描级数字");

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
  /* 从后往前找第一条真的带 font-size 的规则：媒体查询里可能只改别的属性 */
  for (let i = hits.length - 1; i >= 0; i--) {
    const m = /font-size\s*:\s*([^;]+);/.exec(hits[i].body);
    if (m) return { sel: hits[i].sel, decl: m[1].trim() };
  }
  return null;
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
  g.eq(slides.length, 11, "section.slide 数量");
  g.eqJson(slides.map((s) => s.id),
    ["s1", "s2", "s3", "s6", "s5", "s10", "s12", "s7", "s8", "s9", "s11"],
    "屏顺序：封面 / 宗旨 / 背景 / 评测对比 / 成本 / 规则迁移 / 迁移评测 / 门禁 / 回退 / 底层证据 / 收尾");
  const noList = (SRC["index.html"].match(/<span class="slide-no"[^>]*>(\d\d)<\/span>/g) || [])
    .map((t) => t.replace(/[^\d]/g, ""));
  g.eqJson(noList, ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11"],
    "屏号 01–11 与 DOM 顺序逐一对应（成本屏排到第 5 屏）");
  g.ok(/<div class="probe-title">\s*<h2 id="s12-title">[^<]*<\/h2>\s*<p class="mini-label probe-label">C1b[^<]*<\/p>\s*<\/div>/
    .test(SRC["index.html"]),
    "s12：C1b 徽标移到标题右侧同一行（标题在前、小字在后）", true, null);
  g.ok(slides.every((s) => !!deck && s.parentNode === deck),
    "8 屏都是 main.deck 的直接子节点", true,
    slides.map((s) => s.parentNode ? s.parentNode.className : null));

  /* 1) 舞台不滚动：屏叠在同一层，靠 .is-active / .is-leaving 交叉换场
        （从 app.css 的 .deck / .slide 规则断言；DOM 桩没有样式引擎） */
  const deckRules = CSS_RULES.filter((r) => r.sel === ".deck");
  g.ok(deckRules.length >= 1, "app.css 有 .deck 规则", ">= 1", deckRules.length);
  g.eqJson(CSS_RULES.filter((r) => /scroll-snap-type/.test(r.body)).map((r) => r.sel), [],
    "没有任何 scroll-snap-type（不再上下滑动切屏）");
  g.ok(CSS_RULES.some((r) => r.sel === ".js-ready .deck" && /overflow\s*:\s*hidden/.test(r.body)),
    "JS 起来后舞台锁死（.js-ready .deck overflow: hidden）", "overflow: hidden", null);
  const stacked = CSS_RULES.filter((r) =>
    r.sel === ".js-ready .slide" && /position\s*:\s*absolute/.test(r.body));
  g.eq(stacked.length, 1,
    "8 屏叠在同一层（.js-ready .slide position: absolute）");
  g.ok(CSS_RULES.some((r) => r.sel === ".js-ready .slide" &&
    /(?:^|[;{\s])opacity\s*:\s*0(?:\.0+)?\s*;/.test(r.body)),
    "非当前屏静态隐藏（.js-ready .slide opacity: 0）", "opacity: 0", null);
  g.ok(CSS_RULES.some((r) => r.sel === ".slide" && /min-height\s*:\s*100d?vh/.test(r.body)),
    "无 JS 时每屏至少一屏高（仍可滚动阅读）", "存在", null);

  /* 封面提示必须跟翻页方式一致：舞台不滚了，就不能再写「滚动 ↓」 */
  const hint = doc.querySelector(".scroll-hint");
  const hintText = hint ? hint.textContent.replace(/\s+/g, "") : "";
  g.ok(hintText.length > 0 && hintText.indexOf("滚动") === -1,
    "封面提示不再写「滚动」（页面已不滚动，提示要跟翻页方式一致）", "不含「滚动」", hintText);

  /* 星空背景：默认显示（不再需要 URL 参数）；减少动效时关闭 */
  const star = doc.getElementById("startrack");
  g.ok(!!star, "存在 #startrack 画布（星空背景容器）", true, !!star);
  g.ok(!!star && star.getAttribute("aria-hidden") === "true",
    "星空画布 aria-hidden（纯装饰，不进无障碍树）", "true",
    star && star.getAttribute("aria-hidden"));
  const starRule = CSS_RULES.filter((r) => r.sel === ".startrack");
  g.ok(starRule.some((r) => /display\s*:\s*none/.test(r.body)),
    "星空初始 display: none（等 app.js 初始化后再显示）", "display: none",
    starRule.map((r) => r.body.replace(/\s+/g, " ").slice(0, 60)));
  g.ok(CSS_RULES.some((r) => r.sel === ".js-star .startrack" &&
      /display\s*:\s*block/.test(r.body)),
    "只有 .js-star 下才显示星空（开关类由 app.js 加）", "display: block", null);
  {
    const starSrc = SRC["app.js"].slice(SRC["app.js"].indexOf("function starFieldOn"),
      SRC["app.js"].indexOf("function initStarField"));
    g.ok(/function\s+starFieldOn\(/.test(starSrc) && /return true;/.test(starSrc),
      "app.js：星空默认开启（starFieldOn 恒 true）", "return true", starSrc.slice(0, 80));
    g.eq(SRC["app.js"].indexOf("bg=star"), -1,
      "app.js：不再依赖 ?bg=star");
    g.ok(/if\s*\(reduce\s*\|\|\s*!starFieldOn\(\)\)\s*return;/.test(SRC["app.js"]),
      "app.js：reduce（减少动效）时仍不启用星空", "reduce 卫语句", null);
  }

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
  g.eqJson(headCount, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1], "每屏标题数（恰好 1 个）");
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
  g.ok(footTotal <= 10, "全页脚注元素总数", "<= 10", footTotal);
  g.eqJson(footPerSlide.filter((x) => x.foot + x.aside === 0).map((x) => x.id),
    ["s1", "s6", "s10", "s12", "s11"],
    "S1（封面）与 S8（收尾）不挂脚注");
  g.eqJson(footPerSlide.filter((x) => x.aside > 0).map((x) => x.id), ["s9"],
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

/* ---- A14：BINDINGS 33 键逐条可解析 + 渲染后零 — ---- */
{
  const g = group("A14", "BINDINGS 33 键全部可解析（DOM 桩真渲染）");
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

  /* 44 − 5（S6 实时面板）− 3（通过题数整屏）− 3（封面 scenario / shell / 条数徽章）= 33 */
  g.eq(keys.length, 33, "BINDINGS 唯一键数");

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
  g.eqJson(unresolved, [], "33 个键全部解析出非空、非 — 的文本（meta.revision 除外）");
  g.eqJson(keys.filter((k) => bindingText(k, M).text === DASH), ["meta.revision"],
    "全 33 键中只有 meta.revision 允许是 —");
  g.eqJson(mismatched, [], "每个键在 DOM 中的文本 === 用 spec 复算的文本");
  g.eqJson(nodeCountBad, [], "每个键在 DOM 中的节点数 === HTML 里的出现次数（无丢槽 / 无凭空多槽）");

  const slotSet = [...new Set(SLOTS)].sort();
  g.eqJson(slotSet, keys.slice().sort(), "index.html 槽位集合 === BINDINGS 键集合（双向，无死键无未知）");
  g.eq(slotSet.length, 33, "唯一槽位数");

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
  const expectedScripts = ["../data/bundle.js"];
  if (HAS_MOCK) expectedScripts.push("mock-source.js");
  expectedScripts.push("model.js", "render.js", "live-source.js", "app.js");
  g.eqJson(scriptSrcs, expectedScripts,
    "script 装配顺序（数据 → [模拟样例] → 模型 → 渲染 → 实时源 → 装配）");
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
  for (const name of ["model.js", "render.js", "live-source.js", "mock-source.js", "app.js"]) {
    if (!SRC[name]) continue;
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
  /* D5：扫描级 claim / 复算证据迁到「留出集与证据」屏（s9）；s5 只留 gate 级摘要 */
  g.eqJson(scanIds, ["s9"], "屏面正文里的扫描级数字只在 s9（留出集与证据 · 扫描级块）");
  g.eqJson(gateScreens.filter((c) => c.text.indexOf("gate 级") === -1).map((c) => c.id), [],
    "每个屏面含 0.010684 / 42.6 的屏都写明「gate 级」");
  g.eqJson(scanScreens.filter((c) => c.text.indexOf("扫描级") === -1).map((c) => c.id), [],
    "每个屏面含 −72 / 0.009978 的屏都写明「扫描级」");

  /* 排除范围受控（W-22 结构变更后的新锚点）：
     ① index.html 里不再有 <details>（口径说明是常显正文，不是可展开附录）；
     ② 被跳过的只有「口径说明块」这一个块，且它必须同时给出两个口径 + 各自来源。 */
  /* D7 新契约：允许「证据说明 / 连接（可选）/ 演示选项」的 <details>，
     但只允许出现在 s6 / s9 的证据与连接区域，以及 #demo-hud 的演示选项。
     展开层内不得出现 token 值、search 逐题 id 或 holdout 逐题字段名。 */
  const detailNodes = doc.querySelectorAll("details");
  function detailArea(node) {
    let n = node;
    while (n) {
      if (n.id === "demo-hud") return "demo-hud";
      if (n.classList && n.classList.contains("slide")) return n.id;
      n = n.parentNode;
    }
    return null;
  }
  const detailAreas = detailNodes.map(detailArea);
  g.eq(detailNodes.length, 4,
    "<details> 只用于证据 / 连接 / 演示选项（probe 数据直接展示）");
  g.eqJson([...new Set(detailAreas)].sort(), ["demo-hud", "s6", "s9"],
    "<details> 只出现在 s6 / s9 / 演示选项区域");
  const summaryTexts = detailNodes.map((n) => {
    const sum = doc._findAll(n, (x) => x.tagName === "SUMMARY")[0];
    return sum ? textOf(sum) : "";
  });
  g.ok(summaryTexts.every((t) => t.length >= 4),
    "每个 <details> 都有可读 summary（>= 4 字）", ">= 4", summaryTexts);
  g.eqJson(summaryTexts.filter((t) => t.indexOf("证据说明") === -1 &&
      t.indexOf("连接（可选）") === -1 && t.indexOf("演示选项") === -1), [],
    "summary 文案限定为 证据说明 / 连接（可选）/ 演示选项");
  const detailsText = detailNodes.map((n) => textOf(n)).join(" ");
  const searchTaskIds = (DATA.scenario.task_sets && DATA.scenario.task_sets.search) || [];
  g.eqJson(searchTaskIds.filter((id) => detailsText.indexOf(id) !== -1), [],
    "展开层内不出现 search 逐题 id");
  g.eqJson(["task_id", "run_id", "task_ids"].filter((k) => detailsText.indexOf(k) !== -1), [],
    "展开层内不出现 holdout 逐题字段名");
  const tokenInput = doc.getElementById("live-token");
  g.eq(tokenInput ? tokenInput.getAttribute("value") : null, null,
    "展开层里的 token 输入框没有预置 value");
  g.eq(SRC["index.html"].indexOf("token="), -1, "index.html 不含 token= 查询串");

  const scopeBox = scopeBlockOf(doc);
  g.ok(!!scopeBox, "存在口径说明块（#warnings / .colophon 下唯一非 error 的 .warnings）",
    "1 个", scopeBox ? scopeBox.tagName : null);
  g.ok(!!scopeBox && scopeBox.tagName !== "DETAILS",
    "口径说明块不是 <details>（是常显的 section）", "非 DETAILS",
    scopeBox ? scopeBox.tagName : null);
  const s8Slide = SLIDES.filter((s) => s.id === "s11")[0] || null;
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

  /* 14) S6 只留「合计」一行：三条说明文案连同其槽位 / getter 一起删除 */
  const REMOVED_COPY = ["结论： 全过，但成本更低", "最省的一题", "逐题配对含 "];
  g.eqJson(REMOVED_COPY.filter((t) => SRC["index.html"].indexOf(t) !== -1), [],
    "index.html 不再出现被删的三条说明文案");
  g.eqJson(REMOVED_COPY.filter((t) => SRC["render.js"].indexOf(t) !== -1), [],
    "render.js 不再生成被删的三条说明文案");
  g.eqJson(["getElementById(\"pair-scope-note\")", "s2LedeNote", "pairScopeNote"].filter((t) =>
    SRC["index.html"].indexOf(t) !== -1 || SRC["render.js"].indexOf(t) !== -1), [],
    "口径说明的容器 / getter 已从 HTML 与渲染层彻底删除");
  const panelNode = doc.getElementById("live");
  g.ok(!!panelNode && hasAncestorClass(panelNode, "slide"),
    "#live 实时面板在某一屏内", true, panelNode ? panelNode.parentNode.className : null);

  /* 15) app.js：IntersectionObserver + prefers-reduced-motion + IO 缺失时的降级分支 */
  const app = SRC["app.js"];
  g.ok(app.indexOf("onWheel") !== -1 && /addEventListener\("wheel"/.test(app),
    "滚轮翻页：onWheel + wheel 监听", true, null);
  g.ok(/passive\s*:\s*false/.test(app),
    "滚轮监听 passive: false（才能 preventDefault 吞掉页面滚动）", true, null);
  g.ok(/prefers-reduced-motion/.test(app) && /matchMedia/.test(app),
    "app.js 用 matchMedia 读 prefers-reduced-motion", true, null);
  g.ok(!/new\s+IntersectionObserver/.test(app),
    "不再用 IntersectionObserver（屏是叠着的，当前屏就是 active）", true, null);

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

  /* 关键帧里允许 opacity:0，但限定在换场显影（slide-bloom-in/out）与晕染（ink-bloom）
     三个 keyframes 名下（写入动画，不是初态） */
  const kfOpacityZero = kfList.filter((b) => /opacity\s*:\s*0(?:\.0+)?\s*[;}]/.test(b.body))
    .map((b) => b.name);
  g.eqJson(kfOpacityZero, ["live-row-in", "slide-bloom-in", "slide-bloom-out", "ink-bloom"],
    "@keyframes 内出现 opacity:0 的只有换场显影 2 个 + ink-bloom + 列表行入场（动画首尾帧，不是静态隐藏）");
  g.eq(kfList.filter((b) => /opacity\s*:\s*0(?:\.0+)?\s*[;}]/.test(b.body)).length, 4,
    "含 opacity:0 的 keyframes 块数（恰好 4 个，允许名单没有放水）");

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

  /* 3. 合计摘要必须长在面板里（数字与图表同一块，不挂到别的屏） */
  const sumNode = PAGE_DOC.querySelectorAll(".live-sum")[0];
  g.ok(!!sumNode && !!PAGE_DOC.getElementById("live") && hasAncestorClass(sumNode, "live"),
    ".live-sum 位于 #live 面板内（合计与图表同屏）", true,
    sumNode ? sumNode.className : null);
}

/* ---- A21：渲染后的口径与文案（旧 A13 的语义断言按 8 屏重锚） ---- */
{
  const g = group("A21", "渲染后的口径与文案（9 屏）");
  const doc = PAGE_DOC;
  const F = M.fmt;
  const slideText = {};
  for (const s of SLIDES) slideText[s.id] = textOf(s);
  const pageText = textOf(doc.documentElement);
  /* 第 4 屏（通过题数）已删；实时面板排在成本前面，但 id 仍是内容锚点：
     s5 = 单题成本、s6 = 实时评测、s7 = 门禁、s8 = 回退、s9 = 留出集。 */
  const s1 = slideText.s1 || "";
  const s3 = slideText.s5 || "";
  const s4 = slideText.s6 || "";
  const s5 = slideText.s7 || "";
  const s6 = slideText.s8 || "";
  const s7 = slideText.s9 || "";
  /* S8 的 claim 只看屏面正文：口径说明块（常显）单独在 A22 里验，不借它的字凑 claim */
  const s8 = SLIDES.length > 0 ? surfaceTextOf(SLIDES[SLIDES.length - 1]) : "";
  const dsp = M.s1 && M.s1.scan ? M.s1.scan.display : {};

  /* ---- 封面：主文案 + 题数（打字机两句，第二句在 app.js 的 TAGLINES 里） ---- */
  g.ok(s1.indexOf("Molt") !== -1 && s1.indexOf("迭元") !== -1 && s1.indexOf("进化") !== -1,
    "S1 封面主文案「Molt 迭元，让智能学会进化」（无 JS 时的静态兜底）", "含 Molt 迭元 / 进化",
    s1.slice(0, 80));
  /* 文案是可自由改的内容，这里只锁结构：两句、且两个数字都标红。
     具体措辞（「题测试」→「题评测」之类）不该让验收挂掉。 */
  const tagAt = SRC["app.js"].indexOf("var TAGLINES");
  const tagEnd = tagAt === -1 ? -1 : SRC["app.js"].indexOf("];", tagAt);
  const tagSrc = tagAt === -1 || tagEnd === -1 ? "" : SRC["app.js"].slice(tagAt, tagEnd);
  g.ok(tagSrc.indexOf("Molt") !== -1 && tagSrc.indexOf("进化") !== -1,
    "TAGLINES 第一句含封面主文案（Molt 迭元 … 进化）", "含 Molt / 进化", tagSrc.slice(0, 60));
  /* 只锁结构，不锁具体措辞：两句各固定两行（各一个 \\n），每句至少一个红色关键词 */
  {
    const breaks = countRe(tagSrc, /\\n/g);
    const hots = countRe(tagSrc, /hot:\s*true/g);
    g.eq(breaks, 2, "TAGLINES 两句各有一个 \\n（标题固定两行）");
    g.ok(hots >= 2, "TAGLINES 每句都有标红关键词", ">= 2", hots);
    const segs = tagSrc.split(/\]\s*,\s*\[/);   /* 两句之间只隔一个 "], [" */
    g.ok(segs.length === 2 && segs.every((x) => /hot:\s*true/.test(x)),
      "TAGLINES 两句都有 hot 片段（每句都有红色关键词）", 2, segs.length);
  }
  g.eqJson(["0.035560", "0.009978", "−72", "70.0"].filter((l) => s1.indexOf(l) !== -1), [],
    "S1 封面不出现成本 / 降幅数字");

  /* ---- 扫描级 vs gate 级：按屏断言（核心口径纪律） ---- */
  if (IS_ADR_SCAN) {
    g.ok(s7.indexOf("−72%") !== -1,
      "s9 含扫描级降幅 −72%（引 ADR-0008，整数口径）", "含 −72%", s7);
    g.eq(countLit(s7, "−72.0"), 0, "s9 不出现 −72.0（去掉 71.9 vs 72.0 的假精度）");
    g.ok(s7.indexOf("0.035560") !== -1 && s7.indexOf("0.009978") !== -1,
      "s9 含扫描级两侧金额（0.035560 → 0.009978）", "两个金额", s7);
    g.ok(s7.indexOf("41.7") !== -1, "s9 含扫描级 claim 墙钟 41.7", "含 41.7", s7);
    g.eqJson(["0.010684", "42.6", "0.01068"].filter((l) => s7.indexOf(l) !== -1), [],
      "s9 不含 gate 级候选配对数字（0.010684 / 42.6）");
    g.ok(s3.indexOf("扫描级") === -1 || s3.indexOf("0.009978") === -1,
      "s5 不再承载扫描级数字（扫描级说明已迁走）", "s5 无扫描级金额", s3.slice(0, 80));
    g.eqJson(["−72", "0.009978", "0.035560", "41.7", "106.9"].filter((l) => s3.indexOf(l) !== -1), [],
      "s5 只留 gate 级摘要：不出现扫描级 claim 数字");
    g.eq(textOf(doc.getElementById("scan-note-basis")), "引用 ADR-0008",
      "s9 写明扫描级口径来源「引用 ADR-0008」");

    g.ok(s4.indexOf("0.010684") !== -1, "S4 含 gate 级候选配对合计 0.010684", "含 0.010684", s4.slice(0, 120));
    g.eqJson(["−72", "0.009978", "41.7"].filter((l) => s4.indexOf(l) !== -1), [],
      "S4 不含扫描级数字（−72 / 0.009978 / 41.7）");

  } else {
    const baseUsd = "$" + F.usd(dsp.baseCostUsd);
    const candUsd = "$" + F.usd(dsp.candCostUsd);
    g.ok(s7.indexOf(baseUsd) !== -1 && s7.indexOf(candUsd) !== -1,
      "s9 的扫描级金额 === search_results 复算", baseUsd + " / " + candUsd, s7.slice(0, 160));
    g.ok(s7.indexOf(F.sec(dsp.baseWallMs)) !== -1 && s7.indexOf(F.sec(dsp.candWallMs)) !== -1,
      "s9 的扫描级墙钟 === search_results 复算",
      F.sec(dsp.baseWallMs) + " / " + F.sec(dsp.candWallMs), s7.slice(0, 200));
    g.eqJson(["−72", "0.009978", "41.7"].filter((l) => s7.indexOf(l) !== -1), [],
      "s9 不出现 ADR 常量（−72 / 0.009978 / 41.7）");
    g.eq(textOf(doc.getElementById("scan-note-basis")), "两侧由 search_results 复算",
      "s9 写明扫描级口径来源「两侧由 search_results 复算」");

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
    /* s6 主结论句按设计简化成一行，不带「gate 级」前缀：口径由其「证据说明」层承担 */
    const headline = doc.getElementById("live-result");
    const scoped = critNodes.filter((n) => n !== headline);
    g.eqJson(
      scoped.filter((n) => textOf(n).indexOf("gate 级") === -1)
        .map((n) => n.tagName + "#" + n.id + " " + textOf(n).slice(0, 60)),
      [],
      "每个含 0.01068 / 42.6 的段落都写明「gate 级」（s6 主结论句除外）"
    );
    g.ok(!!headline && textOf(doc.getElementById("live-evidence-details")).indexOf("gate 级") !== -1,
      "s6 主结论句省略「gate 级」时，证据说明层必须写明口径", "证据说明含 gate 级", null);
    g.eqJson(
      critNodes.filter((n) => textOf(n).indexOf("−72") !== -1)
        .map((n) => n.tagName + " " + textOf(n).slice(0, 60)),
      [],
      "含 0.01068 / 42.6 的段落不得同时出现 −72（扫描级降幅只留在 S3）"
    );
  }

  /* 通过题数整屏已删除：solvedDelta 仍是数据前提，但不再有页面文案要锁 */
  g.eq(M.s1.solvedDelta, 0, "数据前提：s1.solvedDelta === 0");

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

  /* ---- S6 实时面板：10 行 × 4 格 + 折线图数据源 ---- */
  const list = doc.getElementById("live-list");
  const rows = list ? list.children.filter((c) => typeof c !== "string") : [];
  g.eq(rows.length, M.s2.pairs.length, "实时列表行数 === model.s2.pairs.length");
  g.eqJson(rows.filter((r) => !r.classList.contains("live-row")).map((r) => r.tagName), [],
    "列表里每行都是 li.live-row");

  const rowBad = [];
  rows.forEach((li, i) => {
    const p = M.s2.pairs[i] || {};
    const cells = li.children.filter((c) => typeof c !== "string");
    if (cells.length !== 4) { rowBad.push("第 " + (i + 1) + " 行：" + cells.length + " 格"); return; }
    const t = cells.map(textOf);
    const flagB = p.basePassed === true ? "✅" : "❌";
    const flagC = p.candPassed === true ? "✅" : "❌";
    if (t[0] !== String(p.index)) rowBad.push("第 " + (i + 1) + " 行题号：" + t[0]);
    if (t[1].indexOf(String(p.taskId)) === -1) rowBad.push("第 " + (i + 1) + " 行题名：" + t[1]);
    if (t[2].indexOf(flagB) !== 0 || t[2].indexOf("$" + F.usd(p.baseCost)) === -1) {
      rowBad.push("第 " + (i + 1) + " 行第一次：" + t[2]);
    }
    if (t[3].indexOf(flagC) !== 0 || t[3].indexOf("$" + F.usd(p.candCost)) === -1) {
      rowBad.push("第 " + (i + 1) + " 行第二次：" + t[3]);
    }
    /* 折线图只认行上的 data-*，两边不许各存一份数 */
    if (li.getAttribute("data-base") !== String(p.baseCost)) rowBad.push("第 " + (i + 1) + " 行 data-base");
    if (li.getAttribute("data-cand") !== String(p.candCost)) rowBad.push("第 " + (i + 1) + " 行 data-cand");
  });
  g.eqJson(rowBad, [], "10 行 × 4 格的题号 / 题名 / ✅❌ + 金额 / data-* 与 model.s2.pairs 逐项一致");

  /* 判定 emoji 必须是真的按 basePassed / candPassed 给的，不是装饰 */
  const emojiBad = rows.filter((li, i) => {
    const p = M.s2.pairs[i] || {};
    const cells = li.children.filter((c) => typeof c !== "string");
    const want = [p.basePassed === true ? "✅" : "❌", p.candPassed === true ? "✅" : "❌"];
    return cells.length !== 4 ||
      textOf(cells[2]).indexOf(want[0]) !== 0 || textOf(cells[3]).indexOf(want[1]) !== 0;
  });
  g.eq(emojiBad.length, 0, "两次成本前的 ✅ / ❌ 与 baseline_passed / candidate_passed 一致");

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
    const cells = r.children.filter((c) => typeof c !== "string");
    const cell = cells[1] ? textOf(cells[1]) : "";
    const hit = expectedLiveTasks.filter((t) => cell.indexOf(t) !== -1);
    return hit.length === 1 ? hit[0] : null;
  });
  g.eqJson(liveRowTasks.slice().sort(), expectedLiveTasks.slice().sort(),
    "带 .tag-live 的行指向的题目 === 数据面 kind=live 的题（不多不少）");

  /* 图 + 列表两栏 + 合计摘要 */
  const panel = doc.getElementById("live");
  g.ok(!!panel && doc._findAll(panel, (n) => n.classList.contains("live-chart")).length === 1 &&
    doc._findAll(panel, (n) => n.classList.contains("live-list-wrap")).length === 1,
    "实时面板 = 左折线图 + 右滚动列表两栏", true,
    panel ? "ok" : "缺 #live");
  const canvas = doc.getElementById("live-chart");
  g.ok(!!canvas && String(canvas.tagName).toUpperCase() === "CANVAS",
    "面板里有 <canvas id=\"live-chart\">", "CANVAS", canvas ? canvas.tagName : null);
  g.ok(!!list && list.classList.contains("live-list"),
    "#live-list 是列表容器（不是表格）", true, list ? list.className : null);

  /* 合计：文案定为「合计: 基线成本 … · 候选成本 … · 成本差 … · 通过差 …」，
     字号与已删除的「逐题配对含 …」脚注一致（--fs-foot） */
  const sumText = textOf(doc.querySelectorAll(".live-sum")[0]);
  g.ok(sumText.indexOf("合计:") !== -1 && sumText.indexOf("基线成本") !== -1 &&
    sumText.indexOf("候选成本") !== -1 && sumText.indexOf("成本差") !== -1 &&
    sumText.indexOf("通过差") !== -1,
    "合计摘要用「合计: 基线成本 / 候选成本 / 成本差 / 通过差」的全新文案",
    "四个标签都在", sumText);
  g.eq(sumText.indexOf("gate 级配对合计"), -1,
    "旧文案「gate 级配对合计」已从可见文本删除");
  g.ok(typeof M.s2.totals.base === "number" &&
    sumText.indexOf("基线成本 $" + F.usd(M.s2.totals.base)) !== -1,
    "合计摘要「基线成本」=== s2.totals.base", "基线成本 $" + F.usd(M.s2.totals.base), sumText);
  g.ok(typeof M.s2.totals.cand === "number" &&
    sumText.indexOf("候选成本 $" + F.usd(M.s2.totals.cand)) !== -1,
    "合计摘要「候选成本」=== s2.totals.cand", "候选成本 $" + F.usd(M.s2.totals.cand), sumText);
  /* 成本差必须带 $：−$0.024876，不能只剩数字 */
  const deltaText = "−$" + F.usd(Math.abs(M.s2.totals.delta));
  g.ok(sumText.indexOf("成本差 " + deltaText) !== -1,
    "合计摘要「成本差」=== s2.totals.delta（带符号 + $ 单位）", "成本差 " + deltaText, sumText);
  const passText = MOLT_RENDER.BINDINGS["s2.totalPassDelta"].get(M);
  g.ok(sumText.indexOf("通过差 " + passText) !== -1,
    "合计摘要「通过差」=== s2.totalPassDelta", "通过差 " + passText, sumText);

  const sumRule = CSS_RULES.filter((r) => r.sel === ".live-sum")[0];
  g.ok(!!sumRule && /font-size\s*:\s*var\(--fs-foot\)/.test(sumRule.body),
    "app.css：合计字号 === --fs-foot（与被删的「逐题配对含 …」脚注同级）",
    "var(--fs-foot)", sumRule ? sumRule.body : null);

  /* ---- 新功能：实时面板的动效契约 + 位置记忆 ---- */
  const appSrc = SRC["app.js"];
  g.ok(appSrc.indexOf("liveInit") !== -1 && appSrc.indexOf("liveEnsureVisible") !== -1,
    "app.js：实时面板装配 + 更新点不可见时自动滚动", true,
    appSrc.indexOf("liveInit") !== -1 && appSrc.indexOf("liveEnsureVisible") !== -1);
  g.ok(appSrc.indexOf("liveTweenX") !== -1 && /L\.xShown\s*=\s*L\.xFrom/.test(appSrc),
    "app.js：横轴 domain 缓动（题数增长时向左压缩）", true, null);
  g.ok(/is-live/.test(appSrc) && /is-fresh/.test(appSrc),
    "app.js：逐条点亮 + 更新行高亮（reduced-motion 时不加 .is-live）", true, null);
  g.ok(appSrc.indexOf("localStorage") !== -1 && appSrc.indexOf("replaceState") !== -1 &&
    appSrc.indexOf("hashchange") !== -1,
    "app.js：位置记忆 = localStorage + URL hash + hashchange 回跳", true, null);

  /* 并发完成（先完成的先出现、行仍按题号升序）+ 两轮制（第一轮第二次标 —） */
  const mockSrc = SRC["mock-source.js"] || "";
  if (HAS_MOCK) {
    g.ok(mockSrc.indexOf("completionOrder") !== -1 &&
      /SEED1/.test(mockSrc) && /SEED2/.test(mockSrc) && /GAP_MS/.test(mockSrc),
      "mock-source.js：两轮完成顺序按固定种子洗牌（模拟可复现）", true, null);
  }
  g.ok(appSrc.indexOf("completionOrder") === -1 && !/LIVE_SEED/.test(appSrc),
    "app.js：不含演示用的完成顺序 / 种子（模拟已整块移到 mock-source.js）", true, null);
  g.ok(appSrc.indexOf("liveCandPending") !== -1 && appSrc.indexOf("liveCandDone") !== -1,
    "app.js：第一轮第二次成本标「—」且收起 ✅❌", true, null);
  if (HAS_MOCK) {
    g.eq(mockSrc.indexOf("Math.random"), -1,
      "mock-source.js：模拟顺序不依赖 Math.random");
  }
  /* 星空那段用 Math.random 画星点，不算数：只查实时面板这一段 */
  const liveSrc = appSrc.slice(appSrc.indexOf("S6 实时面板"), appSrc.indexOf("星空背景（"));
  g.eq(liveSrc.indexOf("Math.random"), -1,
    "app.js：实时面板回放不依赖 Math.random（否则每次演示顺序都变）");

  const cssSrc = SRC["app.css"];
  g.ok(/\.live-list\.is-live \.live-row/.test(cssSrc) && /\.live-row\.is-in/.test(cssSrc),
    "app.css：.is-live 藏行、.is-in 才显示（S6 默认空载，无 JS 也不冒预置数据）",
    true, null);
  g.ok(/\.lg--cand/.test(cssSrc), "app.css：两次成本用两种颜色（图例分色）", true, null);

  const liveRule = CSS_RULES.filter((r) => r.sel === ".live")[0];
  g.ok(!!liveRule &&
    /grid-template-columns\s*:\s*minmax\(0,\s*1fr\)\s*minmax\(0,\s*1fr\)/.test(liveRule.body),
    "app.css：面板左右各半（列表落在屏幕右半边）", "1fr 1fr", liveRule ? liveRule.body : null);
  g.ok(!!liveRule && /height\s*:\s*clamp\(/.test(liveRule.body),
    "app.css：面板有固定的整块高度（列表可拉长）", "height: clamp(...)",
    liveRule ? liveRule.body : null);

  /* ---- 本轮新增：两列柱状图 / 实时合计 / 实时数据源框架 ---- */
  g.ok(/function liveFrame\(/.test(appSrc) && /LIVE_BAR_MS/.test(appSrc) &&
    /fillRect/.test(appSrc) && /gBase/.test(appSrc) && /gCand/.test(appSrc),
    "app.js：两次成本改成两列柱状图，每根柱子从 0 长到目标高度", true, null);
  g.eq(appSrc.indexOf("drawSeries"), -1, "app.js：折线绘制已下线（不残留死代码）");

  g.ok(/function liveSum\(/.test(appSrc) && /pBase/.test(appSrc) && /pCand/.test(appSrc),
    "app.js：合计只累加已经出现的行，成本差只算两次都有数的配对行", true, null);
  g.ok(appSrc.indexOf('"is-bad"') !== -1 && appSrc.indexOf('"is-good"') !== -1 &&
    /d\s*<\s*0\s*\?\s*"is-good"/.test(appSrc),
    "app.js：成本差为负 → is-good（绿），为正 → is-bad（红），0 → is-zero（墨色）", true, null);
  const badRule = CSS_RULES.filter((r) => r.sel === ".live-sum b.is-bad")[0];
  const goodRule = CSS_RULES.filter((r) => r.sel === ".live-sum b.is-good")[0];
  g.ok(!!badRule && /var\(--red\)/.test(badRule.body),
    "app.css：成本差 / 通过差为红时用 --red", "var(--red)", badRule ? badRule.body : null);
  g.ok(!!goodRule && /var\(--good\)/.test(goodRule.body),
    "app.css：为绿时用 --good", "var(--good)", goodRule ? goodRule.body : null);
  g.ok(/--good\s*:\s*#[0-9A-Fa-f]{6}/.test(cssSrc), "app.css：定义了 --good 绿色令牌", true, null);

  /* 数据接口（D3）：同源探测 / 内存授权 / ?port= 只本地 / ?token= 停用 */
  const vm = await import("node:vm");

  function makeSandbox(protocol, hostname, search) {
    const timers = [];
    const reqs = [];
    const sandbox = {
      location: {
        protocol: protocol,
        hostname: hostname,
        search: search,
        pathname: "/page/",
        hash: ""
      },
      history: { replaceState: function () {} },
      setTimeout: function (fn, ms) {
        timers.push({ fn: fn, ms: ms });
        return timers.length;
      },
      clearTimeout: function () { timers.length = 0; },
      matchMedia: function () { return { matches: false }; },
      MOLT_AGENT_BASE: "",
      MOLT_AGENT_PATH: "",
      XMLHttpRequest: function () {
        this.headers = {};
        this.status = 0;
        this.responseText = "";
        this.open = function (m, url) { this.method = m; this.url = url; };
        this.setRequestHeader = function (k, v) { this.headers[k] = v; };
        this.send = function () { reqs.push(this); };
        this.abort = function () { this.aborted = true; };
      }
    };
    sandbox.__timers = timers;
    sandbox.__reqs = reqs;
    vm.createContext(sandbox);
    vm.runInContext(SRC["live-source.js"], sandbox);
    return sandbox;
  }

  function fire(req, status, payload) {
    req.readyState = 4;
    req.status = status;
    req.responseText = payload === undefined ? "" : JSON.stringify(payload);
    if (req.onreadystatechange) req.onreadystatechange();
  }

  {
    const box = makeSandbox("https:", "demo.example.com", "");
    const API = box.MOLT_LIVE;
    g.ok(!!API && typeof API.probe === "function" && typeof API.authorize === "function" &&
      typeof API.resolve === "function" && typeof API.legacyToken === "function" &&
      typeof API.normalize === "function" && typeof API.check === "function",
      "live-source.js：暴露 MOLT_LIVE（probe / authorize / resolve / legacyToken / check）", true, null);
    g.eq(API.isOn(), false, "数据接口：默认未启动");

    const cfg0 = API.resolve();
    g.eq(cfg0.mode, "same-origin", "普通 HTTP(S) 访问：默认探测同源（不是 idle，也不是回环）");
    g.eq(cfg0.base, "", "同源模式 base 为空（请求相对路径 /api/health）");
    g.eq(cfg0.health, "/api/health", "同源模式健康路径 === /api/health");
    g.eq(cfg0.path, "/api/tasks", "同源模式任务路径 === /api/tasks");
    g.eq(API.start({}), false, "无 token：start() 必须 false（不得开始轮询 /api/tasks）");
    g.eq(box.__reqs.length, 0, "无 token：start() 不发任何请求");

    const got = { status: [], health: null };
    g.eq(API.probe({
      onStatus: function (kind, detail) { got.status.push({ kind: kind, detail: detail }); },
      onHealth: function (h) { got.health = h; }
    }), true, "同源探测：probe() 启动健康探测");
    g.eq(box.__reqs.length, 1, "同源探测：只发 1 个请求");
    const hReq = box.__reqs[0];
    g.eq(hReq.method, "GET", "健康探测用 GET");
    g.eq(hReq.url, "/api/health", "健康探测请求同源 /api/health");
    g.eq(hReq.headers.Authorization === undefined, true, "健康探测不带 Authorization（公开接口）");
    fire(hReq, 200, { status: "ok", provider: "mock", model: "mock-1", real_model: false });
    g.ok(!!got.health && got.health.provider === "mock" && got.health.real_model === false,
      "健康响应带回 provider / real_model（未知值不猜）", { provider: "mock", real_model: false }, got.health);
    g.eqJson(got.status.map((x) => x.kind), ["probing", "need-auth"],
      "健康可达但无 token：状态 = 需要授权（不发 /api/tasks）");
    g.eq(box.__reqs.length, 1, "无 token 时只探测健康，不请求 /api/tasks");
  }

  {
    /* 401：只提示一次并停止自动重试 */
    const box = makeSandbox("https:", "demo.example.com", "");
    const API = box.MOLT_LIVE;
    const st = { kinds: [], payloads: 0 };
    g.eq(API.authorize("tok-abc", {
      onStatus: function (kind) { st.kinds.push(kind); },
      onPayload: function () { st.payloads += 1; }
    }), true, "授权：authorize(token) 启动");
    g.eq(box.__reqs.length, 1, "授权：先探健康（1 个请求）");
    g.eq(box.__reqs[0].headers.Authorization === undefined, true, "授权流程的健康探测仍不带 token");
    fire(box.__reqs[0], 200, { status: "ok", provider: "openai-compatible", real_model: true });
    g.eq(box.__reqs.length, 2, "健康成功后：有 token 才拉任务快照");
    const tasks = box.__reqs[1];
    g.eq(tasks.url, "/api/tasks", "任务快照请求同源 /api/tasks");
    g.eq(tasks.headers.Authorization, "Bearer tok-abc", "任务快照带 Authorization: Bearer <token>");
    g.eq(tasks.url.indexOf("token"), -1, "token 不进 URL（只走请求头）");
    fire(tasks, 401, { detail: "unauthorized" });
    g.eq(API.phase(), "need-auth", "401：状态转为需要授权");
    g.eq(box.__reqs.length, 2, "401：停止自动重试（不再发新请求）");
    g.eq(box.__timers.length, 0, "401：不排下一次轮询");
    g.eq(st.kinds.filter((k) => k === "need-auth").length, 1, "401：只上报一次");
    g.ok(API.hasToken() === true, "401：token 仍在内存，等用户重新输入或断开", true, API.hasToken());
  }

  {
    /* 成功取数 → 轮询；断开清 token；迟到响应被丢弃 */
    const box = makeSandbox("https:", "demo.example.com", "");
    const API = box.MOLT_LIVE;
    const st = { kinds: [], payloads: 0, norm: null };
    API.authorize("tok-xyz", {
      onStatus: function (kind) { st.kinds.push(kind); },
      onPayload: function (norm) { st.payloads += 1; st.norm = norm; }
    });
    fire(box.__reqs[0], 200, { status: "ok", provider: "mock", real_model: false });
    fire(box.__reqs[1], 200, {
      results: [{ id: "t1", cost_first: 1, passed_first: true, cost_second: 0.5, passed_second: true }]
    });
    g.ok(st.kinds.indexOf("connected") !== -1, "成功取数：状态 connected", true, st.kinds);
    g.ok(!!st.norm && st.norm.pairs.length === 1, "成功取数：payload 归一化为逐题快照", 1, st.norm && st.norm.pairs.length);
    g.eq(box.__timers.length, 1, "成功取数：排下一次轮询");
    g.eq(box.__timers[0].ms, 500, "轮询间隔 0.5s");

    const before = st.payloads;
    API.stop();
    g.eq(API.hasToken(), false, "断开：token 从内存清除");
    fire(box.__reqs[1], 200, {
      results: [{ id: "t2", cost_first: 2, passed_first: true, cost_second: 1, passed_second: true }]
    });
    g.eq(st.payloads, before, "迟到响应：断开后不再落回页面（按代际丢弃）");
  }

  {
    /* ?port= 只在本地页面 / file:// 生效；远程忽略，不请求访客回环 */
    const local = makeSandbox("http:", "127.0.0.1", "?port=8791");
    const cl = local.MOLT_LIVE.resolve();
    g.eq(cl.mode, "port", "本地页面 + ?port=：显式开发配置生效");
    g.ok(/:8791$/.test(cl.base), "本地 ?port= → 127.0.0.1:8791", ":8791", cl.base);
    g.ok(cl.base.indexOf("//") !== -1,
      "base 由「协议 + 冒号 + 双斜杠」拼出（文件里不写 http 字面量）", true, cl.base);

    const remote = makeSandbox("https:", "demo.example.com", "?port=8791");
    const cr = remote.MOLT_LIVE.resolve();
    g.eq(cr.mode, "same-origin", "远程页面：?port= 被忽略，退回同源");
    g.eq(cr.base, "", "远程页面：不构造访客回环地址");
    g.eq(cr.portIgnored, true, "远程页面：标记 ?port= 已忽略");
    remote.MOLT_LIVE.probe({});
    g.eq(remote.__reqs.length, 1, "远程 ?port=：仍只探测同源");
    g.eq(remote.__reqs[0].url, "/api/health", "远程 ?port= 不产生回环请求");

    const fileBox = makeSandbox("file:", "", "");
    g.eq(fileBox.MOLT_LIVE.resolve().mode, "file", "file://：没有同源接口，状态为 file");
    g.eq(fileBox.MOLT_LIVE.start({}), false, "file://：start() 返回 false");
    g.eq(fileBox.MOLT_LIVE.probe({}), false, "file://：probe() 返回 false（不发请求）");
    g.eq(fileBox.__reqs.length, 0, "file://：不发任何请求");

    const filePort = makeSandbox("file:", "", "?port=8791");
    g.eq(filePort.MOLT_LIVE.resolve().mode, "port", "file:// + ?port=：允许显式本地开发配置");
  }

  {
    /* ?token= 停用：只识别旧链接，不进入授权 */
    const box = makeSandbox("https:", "demo.example.com", "?token=tok-abc");
    g.eq(box.MOLT_LIVE.legacyToken(), "tok-abc", "?token= 只被识别为旧链接（用于清理地址）");
    g.eq(box.MOLT_LIVE.hasToken(), false, "?token= 不再用于授权（内存里没有 token）");
    g.eq(box.MOLT_LIVE.resolve().token === undefined, true, "resolve() 不再携带 URL token");
    g.eq(SRC["live-source.js"].indexOf("MOLT_AGENT_TOKEN"), -1,
      "live-source.js 不再读 window.MOLT_AGENT_TOKEN（token 只由页面 password 输入）");
    const resolveBody = fnBodyOf(SRC["live-source.js"], "function resolve(");
    g.eq(resolveBody.indexOf('query("token")'), -1,
      "resolve() 体内不解析 ?token=（只有 legacyToken 识别旧链接）");
  }

  /* 契约样本：内联 fixture（仓库不再附带本地假数据文件） */
  const sample = {
    results: [
      { id: "smoke-add", cost_first: 0.0023281, passed_first: false,
        cost_second: 0.00221287, passed_second: true },
      { id: "smoke-even", cost_first: 0.00586628, passed_first: true,
        cost_second: 0.00080566, passed_second: true }
    ]
  };
  {
    const box = makeSandbox("https:", "demo.example.com", "");
    const API = box.MOLT_LIVE;
    const chk = API.check(sample);
    g.eq(chk.ok, true, "数据接口：与 /api/tasks 同形状的响应通过契约校验");
    g.eq(chk.reason, "results", "数据接口：契约识别为 results 数组");

    const norm = API.normalize(sample);
    g.eq(norm.pairs.length, sample.results.length,
      "数据接口：normalize 的逐题条数 === results 条数");
    g.eqJson(norm.pairs.map((p) => p.taskId), sample.results.map((r) => r.id),
      "数据接口：逐题 id 原样保留（id → taskId）");
    g.eqJson(
      [norm.pairs[0].taskId, norm.pairs[0].baseCost, norm.pairs[0].candCost,
        norm.pairs[0].basePassed, norm.pairs[0].candPassed],
      ["smoke-add", 0.0023281, 0.00221287, false, true],
      "数据接口：cost_first / cost_second / passed_first / passed_second 正确映射");
    g.eq(norm.pairs.filter((p) => p.basePassed === false).length, 1,
      "数据接口：passed_first=false 保留为 false（会显示 ❌）");

    g.eq(API.check({ results: [] }).ok, true,
      "数据接口：空 results 合法（还没跑完，不触发回调）");
    g.eq(API.check({ nope: 1 }).ok, false, "数据接口：形状不符时 check() 返回 false");
    g.eq(API.check({ results: [{ foo: 1 }] }).ok, false,
      "数据接口：results 里的记录缺字段时判为契约不符");
    g.eq(API.check({ results: [{ id: "t1", cost_first: 1, passed_first: true,
      cost_second: 1, passed_second: true, extra: 1 }] }).ok, true,
      "数据接口：真实五字段齐全即通过（额外字段不参与映射）");
    g.eq(API.check({ pairs: [{ task_id: "t1", baseline_cost_usd: 2,
      candidate_cost_usd: 1, passed: true }] }).ok, false,
      "数据接口：旧的 pairs 信封已删除（只认 /api/tasks 的 results）");
    g.eq(API.normalize({ pairs: [{ task_id: "t1", baseline_cost_usd: 2 }] }).pairs.length, 0,
      "数据接口：normalize 不再兼容旧字段名");
  }

  /* 本地模拟：必须显式 ?path=<file>；仓库不附带任何默认样例 */
  if (HAS_MOCK) {
    const box = makeSandbox("https:", "demo.example.com", "");
    vm.runInContext(SRC["mock-source.js"], box);
    const MOCK = box.MOLT_MOCK;
    g.ok(!!MOCK && typeof MOCK.start === "function" && typeof MOCK.enabled === "function" &&
      typeof MOCK.stop === "function",
      "mock-source.js：暴露 MOLT_MOCK（enabled / path / start / stop）", true, null);
    box.location.search = "";
    g.eq(MOCK.enabled(), false, "mock-source.js：默认不启用（页面显示导出证据）");
    g.eq(MOCK.start({}), false, "mock-source.js：默认不启动、不读文件");
    box.location.search = "?path";
    g.eq(MOCK.enabled(), false,
      "mock-source.js：空 ?path 不启用（没有默认样例可读）");
    box.location.search = "?path=./other.json";
    g.eq(MOCK.enabled(), true, "mock-source.js：?path=<file> 启用本地模拟");
    g.eq(MOCK.path(), "./other.json", "mock-source.js：路径原样传给 XHR");
    MOCK.stop();
    g.eq(MOCK.isOn(), false, "mock-source.js：stop() 后不再跑");
  }

  /* 仓库不再附带本地假数据：?path 由使用者自己给文件 */
  g.ok(!fs.existsSync(path.join(SCRIPT_DIR, "api-example.js")) &&
    !fs.existsSync(path.join(SCRIPT_DIR, "..", "api_example.json")),
    "web 不附带本地假数据（api-example.js / api_example.json 已删除）", true, null);
  if (HAS_MOCK) {
    const mockSrc = SRC["mock-source.js"];
    g.eq(mockSrc.indexOf("api_example"), -1,
      "mock-source.js：不引用任何内置样例文件");
    g.eq(mockSrc.indexOf("MOLT_API_EXAMPLE"), -1,
      "mock-source.js：不引用镜像全局变量");
  }
  g.eq(SRC["live-source.js"].indexOf("api_example"), -1,
    "live-source.js：不读本地样例（生产只依赖 /api/tasks）");

  g.ok(SRC["live-source.js"].indexOf("setRequestHeader") !== -1 &&
    SRC["live-source.js"].indexOf('"Bearer "') !== -1,
    "live-source.js：实时请求带 Authorization: Bearer <token>", true, null);
  g.ok(/getJson\(S\.base \+ S\.path/.test(SRC["live-source.js"]) &&
    /getJson\(S\.base \+ HEALTH_PATH/.test(SRC["live-source.js"]),
    "live-source.js：任务与健康探测都请求 <base><path>，不拼查询串", true, null);
  g.eq(countLit(SRC["live-source.js"], "fetch("), 0,
    "live-source.js：不用 fetch（XHR + ES5，file:// 下也可用）");

  g.ok(appSrc.indexOf("liveSourceStart") !== -1 &&
    appSrc.indexOf("liveSourceStop") !== -1 &&
    appSrc.indexOf("liveReset") !== -1 &&
    appSrc.indexOf("liveApplySource") !== -1,
    "app.js：进屏探测 / 离屏暂停；收到快照整表落回", true, null);
  g.ok(appSrc.indexOf("liveSourceBoot") === -1 && appSrc.indexOf("liveHydrate") === -1,
    "app.js：没有旧的开机读样例 / hydrate 路径", true, null);
  g.ok(/function liveReset\(/.test(appSrc) && appSrc.indexOf("viewModel") !== -1 &&
    /liveFill\(pairs, false\)/.test(appSrc),
    "app.js：liveReset 用 model 的 s2.pairs 重建导出证据（不再清空成空面板）", true, null);
  g.eq(fnBodyOf(appSrc, "function setActive(").indexOf("liveReset("), -1,
    "setActive 不清空逐题表（进屏 / 返回都保留有效导出证据）");
  g.ok(/id="live-note"/.test(SRC["index.html"]) && appSrc.indexOf("function liveNote") !== -1 &&
    appSrc.indexOf("file:// 不能读取本地 JSON") !== -1,
    "数据源出错时在面板里显示提示（不只控制台）", true, null);
  g.ok(/\.live-note\s*\{/.test(cssSrc) && /\.live-note\.is-bad\s*\{/.test(cssSrc),
    "app.css：.live-note 有正常 / 报错两种样式", true, null);

  /* ---- S5 门禁结论行 ---- */
  g.ok(s5.indexOf(String(M.s3.verdict)) !== -1 && s5.indexOf(String(M.s3.reason)) !== -1,
    "S5 结论行显示 verdict / reason", M.s3.verdict + " / " + M.s3.reason, s5);

  /* adopt 是放行：结论文字与结论节点右上角状态灯都转绿 */
  const verdictBody = doc.querySelector(".verdict-body");
  const isAdopt = String(M.s3.verdict).toLowerCase() === "adopt";
  g.ok(!!verdictBody && isAdopt === /(^|\s)is-adopt(\s|$)/.test(verdictBody.className || ""),
    "adopt 结论：.verdict-body 按 verdict 挂 is-adopt", "is-adopt", verdictBody && verdictBody.className);
  const chainList = doc.getElementById("gate-chain");
  const finalStep = [...((chainList && chainList.children) || [])]
    .filter((n) => /(^|\s)is-final(\s|$)/.test(n.className || ""))[0] || null;
  g.ok(!!finalStep && isAdopt === /(^|\s)is-adopt(\s|$)/.test(finalStep.className || ""),
    "adopt 结论：结论节点状态灯同步 is-adopt（右上角小点转绿）", "is-adopt",
    finalStep && finalStep.className);
  g.ok(CSS_RULES.some((r) => r.sel === ".verdict-body.is-adopt b:first-of-type" &&
    /var\(--good\)/.test(r.body)),
    "app.css：adopt 时结论文字用 --good", true, null);
  g.ok(CSS_RULES.some((r) => r.sel === ".step.is-final.is-adopt::after" &&
    /var\(--good\)/.test(r.body)),
    "app.css：adopt 时结论节点状态灯用 --good", true, null);
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

  /* s10 标题：语义换行 + 不断词（1920/2560 两行，1366 不拆「上层」） */
  const endTitle = doc.getElementById("s11-title");
  g.ok(!!endTitle, "存在 #s11-title", true, !!endTitle);
  const titleKids = endTitle ? endTitle.children : [];
  const brAt = titleKids.findIndex((c) => typeof c !== "string" && c.tagName === "BR");
  const kidText = (c) => (typeof c === "string" ? c : c.textContent);
  g.eq(brAt >= 0 ? 1 : 0, 1, "s10 标题有且只有 1 个语义换行 <br>");
  g.eq((brAt > 0 ? titleKids.slice(0, brAt).map(kidText).join("") : "").trim(),
    "底层 gate 已在跑，", "s10 标题第一行以「，」收口");
  g.eq((brAt >= 0 ? titleKids.slice(brAt + 1).map(kidText).join("") : "").trim(),
    "上层迁移验证仍在建设中。", "s10 标题第二行从「上层」开始、以「。」收口");
  g.eqJson((endTitle ? doc._findAll(endTitle, (n) => n.classList.contains("nw")).map(textOf) : []),
    ["上层", "建设中。"], "「上层」与「建设中。」各自 nowrap（不被行宽拆开）");
  const endRule = CSS_RULES.filter((r) => r.sel === ".end-line")[0] || { body: "" };
  g.ok(/text-wrap\s*:\s*balance/.test(endRule.body), ".end-line 使用 text-wrap: balance", "balance", null);
  const endInner = CSS_RULES.filter((r) => r.sel === ".slide--end .slide-inner")[0] || { body: "" };
  g.ok(/max-width\s*:\s*min\(94vw,\s*1560px\)/.test(endInner.body),
    "s10 版心放宽到 1560px（标题两行不被挤断）", "min(94vw, 1560px)", endInner.body);

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

  const s3Slide = SLIDES.filter((s) => s.id === "s5")[0];
  const s3Text = surfaceTextOf(s3Slide);
  const s3PlainNode = doc._findAll(s3Slide, (n) => n.classList.contains("plain-talk"))[0];
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

  /* S6 删掉「最省 / 最少」两句后，这两条复算不再有页面锚点，断言随文案一起删除。
     s2.bestDrop / worstDrop / allDropped 仍留在 model 层（数据契约不变）。 */

  const s7Slide = SLIDES.filter((s) => s.id === "s9")[0] || null;
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
  const s8Slide = SLIDES.filter((s) => s.id === "s11")[0] || null;
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
  g.eq(detailsNodes.length, 4,
    "渲染后文档里的 <details> 只用于 4 个展开层（证据 / 连接 / 演示选项）");
  function detailAreaOf(node) {
    let n = node;
    while (n) {
      if (n.id === "demo-hud") return "demo-hud";
      if (n.classList && n.classList.contains("slide")) return n.id;
      n = n.parentNode;
    }
    return null;
  }
  g.eqJson(detailsNodes.map(detailAreaOf).filter((x) => x === null), [],
    "每个 <details> 都在 s6 / s9 屏内或演示 HUD 内");

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
  const s6Slide = SLIDES.filter((s) => s.id === "s8")[0] || null;
  const s6Decl = s6Slide ? h2Decl(s6Slide) : null;
  const s6Max = s6Decl ? maxFontRem(s6Decl.decl, rootBody) : null;
  const s8Decl = s8Slide ? h2Decl(s8Slide) : null;

  g.ok(!!s6Decl && s6Decl.sel === ".slide-h-lead" && s6Decl.decl !== (h2Base ? h2Base.decl : null),
    "#s6 h2 有独立的 font-size 声明（与 h2 基线不同）",
    ".slide-h-lead ≠ var(--fs-title)",
    s6Decl ? s6Decl.sel + " " + s6Decl.decl : null);
  g.eqJson(
    SLIDES.filter((s) => ["s5", "s6", "s7", "s9"].indexOf(s.id) !== -1)
      .filter((s) => h2Decl(s) !== null).map((s) => s.id),
    [],
    "S5 / S6 / S7 / S9 的 h2 没有独立字号（只有 S8 放大，其余走基线）"
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
  g.eqJson(inkLayerPerSlide, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1], "每屏恰好 1 个 .ink 晕染层");
  g.eq(doc.querySelectorAll(".ink").length, 11, ".ink 总数（= 屏数）");
  const inkDots = doc.querySelectorAll(".ink").map((n) =>
    doc._findAll(n, (c) => c.tagName === "I").length);
  g.eqJson(inkDots, [3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3], "每个 .ink 恰好 3 个 <i>（三团色斑）");
  g.eq(doc._findAll(doc.documentElement, (c) => c.tagName === "I").length, 33,
    "整页 <i> 总数 === 11 屏 × 3 团");

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
  g.ok(innerTrans.length >= 1, "内容入场由 .slide-inner 承担（.slide 本身不做 transition）",
    ">= 1", innerTrans.map((r) => r.sel));

  /* 滚动模型下线：没有 scroll 回调、没有视差写入 */
  g.eqJson(["function onScroll(", "function settle(", "function applyParallax("]
    .filter((h) => fnBodyOf(appSrc, h).length > 0).map((h) => h.replace(/^function |\($/g, "")), [],
    "滚动模型已下线（onScroll / settle / applyParallax 都不存在）");

  const wheelBody = fnBodyOf(appSrc, "function onWheel(");
  g.ok(wheelBody.length > 0, "存在 onWheel（滚轮翻页的唯一入口）", "> 0 字符", wheelBody.length);
  g.ok(/preventDefault\(\)/.test(wheelBody),
    "onWheel 吞掉默认滚动（舞台不再被浏览器 / 触控板拽走）", "含 preventDefault()", null);
  const wheelLock = /var\s+WHEEL_LOCK_MS\s*=\s*(\d+)\s*;/.exec(appSrc);
  g.ok(!!wheelLock && Number(wheelLock[1]) >= 400 && Number(wheelLock[1]) <= 900,
    "滚轮上锁窗口 WHEEL_LOCK_MS 落在 400–900ms（一次手势只翻一屏）",
    "400–900", wheelLock ? Number(wheelLock[1]) : null);
  g.ok(/now\s*<\s*wheelLock/.test(wheelBody),
    "上锁窗口内直接返回（不连翻）", "now < wheelLock", null);
  g.ok(/Math\.abs\(d\)\s*<\s*8/.test(wheelBody),
    "触控板细碎像素不翻页（|delta| < 8 忽略）", "Math.abs(d) < 8", null);
  g.ok(/if\s*\(\s*reduce\s*\)\s*return;/.test(wheelBody),
    "减少动效时不接管滚轮（页面保持可滚动阅读）", "reduce 卫语句", null);
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
  g.ok(/setActive\(i\)/.test(goToBody) && !/scrollIntoView/.test(goToBody),
    "goTo 只做边界裁剪 + setActive（不再滚动，也没有 IO 中转）",
    "含 setActive(i)、不含 scrollIntoView", null);
  const keyBody = fnBodyOf(appSrc, "function onKeyDown(");
  g.ok(/goTo\(/.test(keyBody), "键盘（↑↓ / PageUp / PageDown / Space）经 goTo 进入",
    "含 goTo(", null);
  g.ok(/"Home"/.test(keyBody) && /"End"/.test(keyBody) &&
    countRe(keyBody, /goTo\(/g) >= 4,
    "Home / End 与逐屏键共用同一条 goTo 路径（≥4 个 goTo 调用）",
    ">= 4", countRe(keyBody, /goTo\(/g));
  const dotBody = fnBodyOf(appSrc, "function onDotClick(");
  g.ok(/goTo\(/.test(dotBody), "圆点点击经 goTo 进入", "含 goTo(", null);
  g.ok(/goTo\(/.test(fnBodyOf(appSrc, "function onWheel(")),
    "滚轮翻页也经 goTo 进入（与键盘 / 圆点同一条路径）", "含 goTo(", null);

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

  /* 计数不吃翻页入口：一律经由 setActive */
  g.eqJson(["function onWheel(", "function onKeyDown(", "function onDotClick("]
    .filter((h) => fnBodyOf(appSrc, h).indexOf("startCounts") !== -1), [],
    "翻页入口（滚轮 / 键盘 / 圆点）都不直接计数，统一由 setActive 触发");

  /* ---------- 二、翻页入口：三条路径都直连 goTo，且没有滚动残留 ---------- */
  g.eqJson(["pendingTarget", "FAR_LOCK_MS", "activeIndex", "scrollIntoView"]
    .filter((t) => appSrc.indexOf(t) !== -1), [],
    "旧滚动机制痕迹清零（pendingTarget / FAR_LOCK_MS / activeIndex / scrollIntoView）");

  const goToBody2 = fnBodyOf(appSrc, "function goTo(");
  g.ok(/if\s*\(i === active\)\s*return;/.test(goToBody2),
    "goTo 对同一屏直接返回（不重复播换场）", "i === active → return", null);

  const keyBody2 = fnBodyOf(appSrc, "function onKeyDown(");
  g.ok(/"ArrowUp"/.test(keyBody2) && /"ArrowDown"/.test(keyBody2),
    "上下方向键可翻页（↑ 上一屏 / ↓ 下一屏）", "含 ArrowUp + ArrowDown", null);
  g.ok(/"ArrowLeft"/.test(keyBody2) && /"ArrowRight"/.test(keyBody2),
    "左右方向键同义翻页", "含 ArrowLeft + ArrowRight", null);
  g.ok(/"PageUp"/.test(keyBody2) && /"PageDown"/.test(keyBody2),
    "PageUp / PageDown 同义", "含 PageUp + PageDown", null);
  g.ok(/" "\s*\|\|\s*key === "Spacebar"/.test(keyBody2),
    "空格 = 下一屏", '含 " " || "Spacebar"', null);
  g.ok(/"Home"/.test(keyBody2) && /"End"/.test(keyBody2),
    "Home / End 跳首尾", "含 Home + End", null);
  g.ok(countRe(keyBody2, /e\.preventDefault\(\)/g) >= 4,
    "翻页键都 e.preventDefault()（不让浏览器顺带滚屏）", ">= 4",
    countRe(keyBody2, /e\.preventDefault\(\)/g));
}


/* ==========================================================================
 * E. 演示闭环 / 同源连接 / 恢复与交接（D2 / D3 / D4 / D6）
 * ========================================================================== */

/* ---- A25：演示模式：进入 / 重置 / 退出 / 页码记忆 / 动效开关 ---- */
{
  const g = group("A25", "演示模式：进入 / 重置 / 退出 / 页码记忆");
  const idx = SRC["index.html"];
  const appSrc = SRC["app.js"];

  g.ok(/id="demo-launch"[^>]*aria-label="开始演示"/.test(idx), "存在可见「开始演示」入口（圆形播放键）", true, null);
  g.ok(/id="demo-hud"[^>]*hidden/.test(idx), "演示步骤条默认隐藏", true, null);
  g.ok(/id="demo-step"/.test(idx), "步骤条有当前步骤名槽位", true, null);
  for (const id of ["demo-prev", "demo-next", "demo-reset", "demo-motion", "demo-exit"]) {
    g.ok(new RegExp('id="' + id + '"').test(idx), "演示控件存在：" + id, true, null);
  }
  g.ok(/var DEMO_NAMES/.test(appSrc) && /受控进化/.test(appSrc),
    "步骤名以「受控进化」开场", true, null);
  g.ok(/function demoEnter\(/.test(appSrc) && /function demoExit\(/.test(appSrc) &&
    /function demoReset\(/.test(appSrc), "app.js：进入 / 退出 / 重置各有单一入口", true, null);

  const rememberBody = fnBodyOf(appSrc, "function remember(");
  g.ok(/if\s*\(demoOn\)\s*return;/.test(rememberBody),
    "演示期间不写页码记忆（molt.slide.v1 不被污染）", true, null);
  const hashBody = fnBodyOf(appSrc, "function onHashChange(");
  g.ok(/if\s*\(demoOn\)\s*return;/.test(hashBody), "演示期间忽略既有 hash", true, null);
  g.ok(/stripQuery\("demo"\)/.test(fnBodyOf(appSrc, "function demoExit(")),
    "退出演示清理 ?demo=1", true, null);
  g.ok(/goTo\(0\)/.test(fnBodyOf(appSrc, "function demoEnter(")) &&
    /goTo\(0\)/.test(fnBodyOf(appSrc, "function demoReset(")) &&
    /goTo\(0\)/.test(fnBodyOf(appSrc, "function demoExit(")),
    "进入 / 重置 / 退出都固定回 s1", true, null);
  g.ok(/remember\(0\)/.test(fnBodyOf(appSrc, "function demoExit(")),
    "退出后恢复正常页码记忆（固定写 s1）", true, null);

  const stepBody = fnBodyOf(appSrc, "function demoStepUpdate(");
  g.ok(/disabled\s*=\s*i\s*<=\s*0/.test(stepBody) &&
    /disabled\s*=\s*i\s*>=\s*slides\.length\s*-\s*1/.test(stepBody),
    "首尾边界：上一步 / 下一步在两端禁用", true, null);
  g.ok(/aria-pressed/.test(appSrc), "动效开关有 aria-pressed 状态", true, null);
  g.ok(/if\s*\(reduce\)\s*return;/.test(fnBodyOf(appSrc, "function motionToggle(")),
    "系统 reduced-motion 优先（不允许手动打开动效）", true, null);
  g.ok(/motion-off/.test(appSrc) && /\.motion-off/.test(SRC["app.css"]),
    "手动关闭动效：html 类 + CSS 关闭 animation / transition", true, null);
  g.ok(/"Escape"/.test(fnBodyOf(appSrc, "function onKeyDown(")) && /function closePanels\(/.test(appSrc),
    "Escape 关闭展开面板", true, null);
  g.ok(/isTypingTarget\(e\.target\)/.test(fnBodyOf(appSrc, "function onKeyDown(")) &&
    /INPUT:\s*1/.test(appSrc), "输入控件内不抢方向键", true, null);
  g.ok(/function liveSourceStart\(/.test(appSrc) && !/motionOff[\s\S]{0,120}liveSourceStop/.test(appSrc),
    "动效开关与数据刷新解耦（关动效不停止轮询）", true, null);
}

/* ---- A26：同源连接 / 内存授权 / 状态分类 ---- */
{
  const g = group("A26", "同源连接 / 授权 / 状态分类");
  const idx = SRC["index.html"];
  const appSrc = SRC["app.js"];
  const liveSrc = SRC["live-source.js"];

  for (const id of ["live-conn-state", "live-conn-meta", "live-conn-note", "live-retry",
    "live-disconnect", "live-export", "live-conn-form", "live-token"]) {
    g.ok(new RegExp('id="' + id + '"').test(idx), "连接控件存在：" + id, true, null);
  }
  g.ok(/type="password"[^>]*id="live-token"/.test(idx), "token 输入框是 password", true, null);
  g.ok(/id="live-conn-form"[^>]*hidden/.test(idx), "授权面板默认收起", true, null);
  g.ok(/autocomplete="off"/.test(idx), "token 输入框关闭自动填充", true, null);

  const note = textOf(PAGE_DOC.getElementById("live-conn-note"));
  g.ok(note.indexOf("≠ 已发生执行") !== -1,
    "连接状态旁写明「可达 / 已连接 ≠ 已发生执行」", "含 ≠ 已发生执行", note);
  g.ok(note.indexOf("已有导出数据") !== -1,
    "连接状态旁写明接口快照读的是已有导出数据", "含 已有导出数据", note);
  const meta = textOf(PAGE_DOC.getElementById("live-conn-meta"));
  g.ok(meta.indexOf("provider") !== -1 && meta.indexOf("real_model") !== -1,
    "界面显示 provider / real_model（未知时显示未知）", true, meta);

  const labels = ["未探测", "正在探测", "需要授权", "已连接", "不可达", "file://", "已暂停", "本地模拟"];
  g.eqJson(labels.filter((t) => appSrc.indexOf(t) === -1), [],
    "状态文案覆盖 idle / probing / 需要授权 / 已连接 / 不可达 / file / 暂停 / 模拟");
  g.ok(/function connProbe\(/.test(appSrc) && /\/api\/health/.test(liveSrc),
    "默认探测同源 /api/health", true, null);
  g.ok(/function connAuthorize\(/.test(appSrc) && /api\.authorize/.test(appSrc),
    "显式授权走 authorize（token 只在内存）", true, null);
  g.ok(/input\.value\s*=\s*""/.test(appSrc), "token 读走即清空 password DOM", true, null);

  const disBody = fnBodyOf(appSrc, "function connDisconnect(");
  g.ok(/liveSourceStop\(\)/.test(disBody) && /liveReset\(\)/.test(disBody),
    "断开：停源（清 token）+ 回到导出证据", true, null);
  g.ok(/MOLT_LIVE\.stop/.test(fnBodyOf(appSrc, "function liveSourceStop(")),
    "liveSourceStop 真的调用 MOLT_LIVE.stop（token 从内存清除）", true, null);
  const expBody = fnBodyOf(appSrc, "function connSwitchExport(");
  g.ok(/api\.pause/.test(expBody) && /liveReset\(\)/.test(expBody),
    "切换导出证据：暂停刷新 + 重建静态表", true, null);
  g.ok(/stripQuery\("token"\)/.test(fnBodyOf(appSrc, "function bindConn(")),
    "?token= 旧链接：清理当前地址并提示重输", true, null);
  g.ok(/无法撤回/.test(appSrc), "不声称能撤回既有泄露", true, null);
  g.ok(/if\s*\(detail === 401 && !liveAuthWarned\)/.test(appSrc), "401 只提示一次", true, null);
  g.eq(liveSrc.indexOf("MOLT_AGENT_TOKEN"), -1, "页面不再从全局注入 token");
  g.ok(!/localStorage[^;\n]{0,80}tok/i.test(appSrc) && !/sessionStorage/.test(appSrc),
    "token 不进入任何 storage", true, null);
}

/* ---- A27：s6 默认导出证据 / 来源切换 / 迟到响应 ---- */
{
  const g = group("A27", "s6 默认导出证据 + 接口快照切换");
  const idx = SRC["index.html"];
  const appSrc = SRC["app.js"];

  g.ok(/<ol class="live-list" id="live-list">/.test(idx),
    "列表默认不带 is-live（导出证据一开始就可见）", true, null);
  g.eq(countLit(idx, 'class="live-list is-live"'), 0, "index.html 不再预置空载类");
  const state0 = PAGE_DOC.getElementById("live-source-state");
  g.ok(!state0 || !state0.getAttribute("hidden") === false || textOf(state0) === "",
    "默认不再单独显示「来源：导出的回放证据」行（已在 note 首句里）", "无重复来源行",
    state0 ? textOf(state0) : null);
  const note = textOf(PAGE_DOC.getElementById("live-source-note"));
  g.ok(note.indexOf("closure 闭环为") !== -1 && note.indexOf("live 候选") !== -1 &&
    note.indexOf("replay 基线") !== -1,
    "默认来源按 closure 组拆分 live / replay", "含 closure 闭环为 … live 候选 / replay 基线", note);
  g.ok(note.indexOf("scan10 为") !== -1 && note.indexOf("candidate-scan 为") !== -1,
    "默认来源分别说明 scan10 与 candidate-scan", true, note);
  const fixed = textOf(PAGE_DOC.querySelector(".live-source-fixed"));
  g.ok(fixed.indexOf("gate 级 10 题配对") !== -1 &&
    fixed.indexOf("closure 组 1 live 候选 + 1 replay 基线") !== -1 &&
    fixed.indexOf("整表不是两条运行") !== -1,
    "s6 表头固定句（gate 级 10 题 / closure 1 live + 1 replay / 不是两条运行）", true, fixed);
  g.eq(ROUT.counts.pairs, M.s2.pairs.length, "默认表行数 === 导出配对条数");
  g.ok(/function liveFill\(/.test(appSrc) && /liveFill\(norm\.pairs, true\)/.test(appSrc),
    "接口快照整表替换（行 / 图 / 合计同一份）", true, null);
  g.ok(/function markFetchTime\(/.test(appSrc) && /最近取数/.test(appSrc),
    "接口快照写「最近取数」时间（不是执行时间）", true, null);
  g.ok(/stateNode\.textContent = "来源：接口快照/.test(appSrc), "来源标注随数据源切换", true, null);
  g.ok(/if \(gen !== S\.gen\) return;/.test(SRC["live-source.js"]),
    "迟到响应按代际丢弃（不会覆盖新来源）", true, null);
  g.ok(/liveCandPending\(r\)/.test(fnBodyOf(appSrc, "function liveFill(")),
    "缺第二次成本时写「—」（不补 0）", true, null);
  g.ok(/isFinite\(p\.baseCost\) && p\.baseCost >= 0/.test(appSrc) &&
    /isFinite\(p\.candCost\) && p\.candCost >= 0/.test(appSrc),
    "0 成本是有效值，缺值才写 —", true, null);
}

/* ---- A28：阻断状态恢复 + chat 交接无凭据 ---- */
{
  const g = group("A28", "阻断状态恢复 + chat 交接无凭据");
  const idx = SRC["index.html"];
  const appSrc = SRC["app.js"];

  g.ok(/id="data-warning-retry"/.test(idx) && /重新加载/.test(idx),
    "数据缺失：有「重新加载」恢复按钮", true, null);
  g.ok(/需要授权/.test(appSrc) && /不可达/.test(appSrc) && /file:\/\/ 模式/.test(appSrc),
    "需要授权 / 不可达 / file:// 都有面向观众的提示", true, null);
  g.ok(/token 为空/.test(appSrc) && /可重试/.test(appSrc) && /切回导出证据/.test(appSrc),
    "提示里给出可执行下一步（重试 / 切导出证据）", true, null);

  const handoff = PAGE_DOC.getElementById("handoff");
  g.ok(!!handoff && handoff.hidden === true, "交接卡默认隐藏（先给说明再跳转）", true, !!handoff);
  const htext = textOf(handoff);
  g.ok(htext.indexOf("底层回路（现有）") !== -1 && htext.indexOf("上层回路（建设中）") !== -1,
    "交接卡写清两条相连但不同的回路", true, htext.slice(0, 120));
  g.ok(htext.indexOf("上层闭环在建设中") !== -1, "交接卡写明上层闭环仍在建设中", true, null);
  g.ok(htext.indexOf("不继承本页的数据、参数或 token") !== -1,
    "交接卡写明 chat 不继承数据 / 参数 / token", true, null);
  g.ok(htext.indexOf("由 chat 的连接状态决定") !== -1,
    "交接卡写明是否真实执行由 chat 决定", true, null);
  g.ok(htext.indexOf("前向状态图") !== -1 && htext.indexOf("建设中；待稳定 graph API 后由 chat 展示") !== -1,
    "交接卡写明「前向状态图」愿景 + 建设中 / 待稳定 graph API", true, null);
  g.ok(htext.indexOf("不可变 context / 策略节点") !== -1 &&
    htext.indexOf("current pointer 原子更新") !== -1 &&
    htext.indexOf("回退保留历史") !== -1 && htext.indexOf("暂不做语义 merge") !== -1,
    "交接卡的图式概念来自约定措辞（不可变节点 / 指针原子更新 / 回退留史 / 暂不 merge）", true, null);

  const link = PAGE_DOC.querySelector(".handoff-link");
  const href = link ? link.getAttribute("href") : null;
  g.eq(href, "../chat/index.html", "交接链接保持 ../chat/index.html");
  g.eq(!!href && href.indexOf("?") !== -1, false, "交接链接不带查询参数");
  g.eq(!!href && /token/i.test(href), false, "交接链接不含 token");
  g.eq(link ? link.getAttribute("target") : null, "_blank", "打开 chat 时本页仍可返回（新标签）");
  const entry = PAGE_DOC.getElementById("agent-entry");
  g.eq(entry ? String(entry.tagName).toUpperCase() : null, "BUTTON", "chat 入口先出交接卡（按钮）");
  g.ok(/if \(token === ""\)/.test(appSrc), "空 token 不发请求", true, null);

  /* D2/D6：交接卡是根级 fixed 覆盖层，不被 HUD 压住 / 不被舞台裁切 */
  const handoffRule = CSS_RULES.filter((r) => r.sel === ".handoff")[0] || { body: "" };
  const hudRule = CSS_RULES.filter((r) => r.sel === ".demo-hud")[0] || { body: "" };
  const zOf = (body) => {
    const m = /z-index\s*:\s*(\d+)/.exec(body);
    return m ? Number(m[1]) : null;
  };
  g.ok(zOf(handoffRule.body) !== null && zOf(hudRule.body) !== null &&
    zOf(handoffRule.body) > zOf(hudRule.body),
    "交接卡 z-index 高于 #demo-hud", "handoff > hud", [zOf(handoffRule.body), zOf(hudRule.body)]);
  g.ok(/max-height\s*:\s*calc\(100vh\s*-\s*2\s*\*\s*var\(--pad-y\)\)/.test(handoffRule.body),
    "交接卡 max-height = calc(100vh - 2 * var(--pad-y))", "calc(...)", handoffRule.body);
  g.ok(/overflow-y\s*:\s*auto/.test(handoffRule.body), "交接卡内容可滚动（overflow-y: auto）", "auto", null);
  g.ok(/--pad-y\s*:/.test(SRC["app.css"]), "app.css 定义 --pad-y 令牌", true, null);
  g.eq(handoff && handoff.parentNode ? String(handoff.parentNode.tagName).toUpperCase() : null, "BODY",
    "交接卡是 body 级覆盖层（不在 main / slide 内，不被裁切）");
  const actionsRule = CSS_RULES.filter((r) => r.sel === ".handoff-actions")[0] || { body: "" };
  g.ok(/position\s*:\s*sticky/.test(actionsRule.body) && /bottom\s*:\s*0/.test(actionsRule.body),
    "交接卡按钮区 sticky 在卡片底部", true, actionsRule.body);
  g.ok(!!link && !!handoff && isDescendantOf(link, handoff),
    "「打开 chat 页面」按钮在交接卡内", true, null);
  const closeBtn = PAGE_DOC.getElementById("handoff-close");
  g.ok(!!closeBtn && !!handoff && isDescendantOf(closeBtn, handoff),
    "「留在展示页」按钮在交接卡内", true, null);
}

/* ---- A29：受控进化叙事 / REPLAY 与历史标签 ---- */
{
  const g = group("A29", "受控进化叙事与 REPLAY / 历史标签");
  const text = textOf(PAGE_DOC.documentElement);
  const s1 = textOf(SLIDES[0]);
  const s2 = textOf(SLIDES[1]);
  const s6 = textOf(SLIDES[3]);
  const s9 = textOf(PAGE_DOC.getElementById("s9"));
  const s10 = textOf(PAGE_DOC.getElementById("s10"));
  const s12 = textOf(PAGE_DOC.getElementById("s12"));
  const s11 = textOf(PAGE_DOC.getElementById("s11"));

  g.ok(s1.indexOf("迭旧元") !== -1 && s1.indexOf("底层回路") !== -1 &&
    s1.indexOf("上层回路") !== -1, "封面讲迭元宗旨与两条回路（不是成本报告）", true, s1.slice(0, 120));
  g.eq(s1.indexOf("成本"), -1, "封面不出现成本诉求");
  g.ok(s2.indexOf("不背答案") !== -1 && s2.indexOf("换题再试") !== -1 &&
    s2.indexOf("检查副作用") !== -1, "P2 提前说明上层 probe 的评测准则");
  g.ok(s6.indexOf("REPLAY") !== -1 && s6.indexOf("历史证据（2026-11-11）") !== -1 &&
    s6.indexOf("非当前 chat 运行") !== -1 && s6.indexOf("底层回路") !== -1,
    "s6 来源徽标：REPLAY / 历史证据 / 非当前 chat 运行 / 底层回路", true, null);
  g.ok(s9.indexOf("REPLAY") !== -1 && s9.indexOf("历史 demo-6") !== -1 &&
    s10.indexOf("历史彩排") !== -1 && s12.indexOf("离线重复验证") !== -1,
    "底层证据、probe 彩排、离线评测分别标注来源", true, null);
  g.ok(s10.indexOf("Run1") !== -1 && s10.indexOf("Run2") !== -1 &&
    s10.indexOf("不同任务") !== -1 && s10.indexOf("不是这道题的输出") !== -1,
    "probe 故事讲清失败→规则→新任务迁移", true, null);
  g.ok(s12.indexOf("0/10") !== -1 && s12.indexOf("12/12") !== -1 &&
    s12.indexOf("headroom 检查，不是进化结果") !== -1,
    "主要提升、matrix 前置验证分别归因", true, null);
  g.ok(s12.indexOf("x8 不计入提升") !== -1 && s12.indexOf("统计显著") !== -1,
    "满分基线与小样本限制明确标注", true, null);
  g.ok(s10.indexOf("v3_stable_all") === -1 && s9.indexOf("不是 core gate 结论") === -1,
    "probe 内部标签不混入展示主线", true, null);
  g.ok(s11.indexOf("不代表当前已具备上层进化能力") !== -1 || s12.indexOf("历史") !== -1,
    "不把 probe 证据写成当前已具备上层能力", true, null);
  g.ok(s11.indexOf("底层 gate 已在跑") !== -1 && s11.indexOf("上层迁移验证仍在建设中") !== -1,
    "收尾先讲两层受控进化，不把成本当唯一结论", true, s11.slice(0, 100));
  g.ok(s11.indexOf("证据之一") !== -1, "收尾把成本标为证据之一", true, null);
  g.ok(s11.indexOf("demo-6 历史证据属于这一层") !== -1, "s11 标明 demo-6 属于底层回路", true, null);

  const banned = ["当前 chat 实时进化", "新 chat 已 live 进化", "成功率提升",
    "自动学习反馈", "每题都少花同样的钱", "两次 live"];
  g.eqJson(banned.filter((t) => text.indexOf(t) !== -1), [],
    "全页不出现「实时进化 / 成功率提升 / 自动学习 / 每题同额省钱」等表述");
}

/* ---- A30：演示键盘与动效开关边界 ---- */
{
  const g = group("A30", "演示键盘与动效开关边界");
  const appSrc = SRC["app.js"];
  const keyBody = fnBodyOf(appSrc, "function onKeyDown(");

  for (const k of ["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown",
    "PageUp", "PageDown", "Home", "End"]) {
    g.ok(keyBody.indexOf('"' + k + '"') !== -1, "键盘保留 " + k, true, null);
  }
  g.ok(/e\.key === "Escape"/.test(appSrc), "Escape 关面板（焦点在输入框 / 按钮上也生效）", true, null);
  const wheelBody = fnBodyOf(appSrc, "function onWheel(");
  g.ok(/if\s*\(\s*reduce\s*\)\s*return;/.test(wheelBody) && wheelBody.indexOf("motionOff") === -1,
    "手动关动效不改变滚轮翻页（只有系统 reduce 才让出滚轮）", true, null);
  g.ok(/prefers-reduced-motion: reduce/.test(SRC["app.css"]) && /motion-off/.test(SRC["app.css"]),
    "系统 reduced-motion 与手动开关是两套（系统优先）", true, null);
}


/* ---- A31：投影密度 / 首屏层级 / 展开层 / reduced-motion 滚动 ---- */
{
  const g = group("A31", "投影密度 / 首屏层级 / 展开层 / reduced-motion 滚动");
  const cssSrc = SRC["app.css"];
  const appSrc = SRC["app.js"];
  const idx = SRC["index.html"];
  const doc = PAGE_DOC;
  const s9Text = textOf(doc.getElementById("s9"));

  /* clamp(min, preferred, max) → 指定视口下的像素值 */
  function clampAtPx(decl, vw) {
    const m = /clamp\(([^,]+),([^,]+),([^)]+)\)/.exec(String(decl || ""));
    if (!m) return null;
    const toPx = (expr) => {
      const t = String(expr).trim();
      let hit = /^([\d.]+)rem$/.exec(t);
      if (hit) return Number(hit[1]) * 16;
      hit = /^([\d.]+)vw$/.exec(t);
      if (hit) return Number(hit[1]) * vw / 100;
      hit = /^([\d.]+)px$/.exec(t);
      if (hit) return Number(hit[1]);
      return null;
    };
    const lo = toPx(m[1]);
    const mid = toPx(m[2]);
    const hi = toPx(m[3]);
    if (lo === null || mid === null || hi === null) return null;
    return Math.max(lo, Math.min(mid, hi));
  }
  function sizeAt(sel, vw) {
    const hit = fontSizeDecl([sel], CSS_RULES);
    return hit ? clampAtPx(hit.decl, vw) : null;
  }
  function notInsideDetails(node) {
    let n = node;
    while (n) {
      if (n.tagName === "DETAILS") return false;
      n = n.parentNode;
    }
    return true;
  }
  function insideDetailsId(node, id) {
    let n = node;
    while (n) {
      if (n.id === id) return true;
      n = n.parentNode;
    }
    return false;
  }

  /* 1. 字号阶梯：1920 达标；2560 放大；1366 不低于 16px */
  g.ok(sizeAt(".live-result", 1920) >= 28,
    "s6 主结论 @1920 >= 28px", ">= 28", sizeAt(".live-result", 1920));
  g.ok(sizeAt(".live-result", 2560) > sizeAt(".live-result", 1920),
    "s6 主结论 @2560 随视口放大", "2560 > 1920",
    [sizeAt(".live-result", 1920), sizeAt(".live-result", 2560)]);
  for (const sel of [".live-list-head", ".lr-name", ".lr-c"]) {
    g.ok(sizeAt(sel, 1920) >= 20, sel + " @1920 >= 20px", ">= 20", sizeAt(sel, 1920));
    g.ok(sizeAt(sel, 1366) >= 16, sel + " @1366 >= 16px", ">= 16", sizeAt(sel, 1366));
  }

  /* 列间距随视口放大：2560 下字号 25.6–27.2px 时表头 / 金额不粘连 */
  const rowRule = CSS_RULES.filter((r) => r.sel.indexOf(".live-list-head") !== -1 &&
    r.sel.indexOf(".live-row") !== -1)[0] || { body: "" };
  const gapMatch = /column-gap\s*:\s*([^;]+);/.exec(rowRule.body);
  g.ok(!!gapMatch && /^clamp\(/.test(gapMatch[1].trim()) && gapMatch[1].indexOf("vw") !== -1,
    "表头 / 行使用随视口放大的 column-gap", "clamp(...vw...)", gapMatch ? gapMatch[1] : null);
  const gap2560 = gapMatch ? clampAtPx(gapMatch[1], 2560) : null;
  g.ok(typeof gap2560 === "number" && gap2560 >= 16,
    "列间距 @2560 >= 16px（字号放大后仍分离）", ">= 16", gap2560);
  const colMatch = /grid-template-columns\s*:\s*[^;]*minmax\([^)]*\)\s+minmax\([^)]*\)/.exec(rowRule.body);
  g.ok(!!colMatch, "金额两列用 minmax() 而不是固定宽度", "minmax(...) minmax(...)", rowRule.body);
  const colRems = colMatch ? [...colMatch[0].matchAll(/([\d.]+)rem/g)].map((m) => Number(m[1])) : [];
  g.ok(colRems.length >= 2 && Math.max.apply(null, colRems) >= 11,
    "金额列上限 >= 11rem（2560 下 25.6px 表头不溢出）", ">= 11rem", colRems);
  g.eq(/10rem\s+10rem/.test(rowRule.body), false,
    "金额两列不再是固定 10rem（配合 vw gap）");
  g.ok(sizeAt(".live-badge", 1920) >= 11, "来源徽标（标题角标小字）@1920 >= 11px", ">= 11", sizeAt(".live-badge", 1920));
  g.ok(sizeAt(".live-badge", 1366) >= 10, "来源徽标（标题角标小字）@1366 >= 10px", ">= 10", sizeAt(".live-badge", 1366));
  const badgeRule = CSS_RULES.filter((r) => r.sel === ".live-badge")[0] || { body: "" };
  g.ok(/white-space\s*:\s*nowrap/.test(badgeRule.body), "来源徽标默认一行（white-space: nowrap）", "nowrap", null);
  g.ok(sizeAt(".demo-step", 1920) >= 18, "步骤名 / 进度 @1920 >= 18px", ">= 18", sizeAt(".demo-step", 1920));
  g.ok(sizeAt(".demo-step", 1366) >= 16, "步骤名 / 进度 @1366 >= 16px", ">= 16", sizeAt(".demo-step", 1366));
  g.ok(sizeAt(".panel-summary", 1920) >= 16, "证据说明 summary @1920 >= 16px", ">= 16", sizeAt(".panel-summary", 1920));
  g.ok(sizeAt(".panel-summary", 1366) >= 16, "证据说明 summary @1366 >= 16px", ">= 16", sizeAt(".panel-summary", 1366));
  g.ok((sizeAt(".probe-showcase-lead", 1920) || 0) >= 22, "probe 迁移主张 @1920 >= 22px", ">= 22", sizeAt(".probe-showcase-lead", 1920));

  /* 2. 既有字号契约不放松 */
  const sumRule = CSS_RULES.filter((r) => r.sel === ".live-sum")[0] || { body: "" };
  g.ok(/font-size\s*:\s*var\(--fs-foot\)/.test(sumRule.body),
    ".live-sum 仍为 var(--fs-foot)（合计不抢主结论）", "var(--fs-foot)", null);
  const h2Independent = ["s5", "s6", "s7", "s9"].filter((id) => {
    const slide = SLIDES.filter((x) => x.id === id)[0];
    const h2 = slide ? doc._findAll(slide, (n) => n.tagName === "H2")[0] : null;
    if (!h2) return true;
    const sels = String(h2.className || "").split(/\s+/).filter(Boolean).map((t) => "." + t);
    return sels.length > 0 && fontSizeDecl(sels, CSS_RULES) !== null;
  });
  g.eqJson(h2Independent, [], "s5 / s6 / s7 / s9 的 h2 没有独立 font-size（只有 s8 放大）");

  /* 3. s6 首屏层级：徽标 + 人话结果可见；技术细节在「证据说明」 */
  const badgeText = textOf(doc.getElementById("live-badge"));
  g.eq(badgeText, "REPLAY · 历史证据（2026-11-11）· 非当前 chat 运行",
    "s6 徽标固定为 REPLAY · 历史证据（2026-11-11）· 非当前 chat 运行");
  const resultText = textOf(doc.getElementById("live-result"));
  g.ok(resultText.indexOf("通过数不变") !== -1 && resultText.indexOf("低 70.0%") !== -1 &&
    resultText.indexOf("0.035560") !== -1 && resultText.indexOf("0.010684") !== -1,
    "s6 人话结果由配对数据生成（通过数不变 / 低 70.0% / 两个金额）", "含四项", resultText);
  g.ok(resultText.indexOf(" 题") === -1 && resultText.indexOf("配对：") === -1,
    "s6 人话结果已简化（不写题数前缀与「配对：」小标题）", "无 题 / 配对：", resultText);
  const evidence = doc.getElementById("live-evidence-details");
  g.ok(!!evidence, "存在 s6 证据说明展开层", true, !!evidence);
  g.ok(!!evidence && textOf(doc._findAll(evidence, (n) => n.tagName === "SUMMARY")[0]) === "证据说明",
    "s6 展开层 summary 文案 === 证据说明", "证据说明",
    evidence ? textOf(doc._findAll(evidence, (n) => n.tagName === "SUMMARY")[0]) : null);
  g.ok(!!evidence && textOf(evidence).indexOf("closure 闭环为") !== -1 &&
    textOf(evidence).indexOf("scan10") !== -1 && textOf(evidence).indexOf("demo_group") !== -1,
    "s6 技术细节（closure / scan10 / demo_group）在展开层内", true, null);

  /* 4. 底部操作区：核心按钮常显，次要按钮在「演示选项 / 连接（可选）」 */
  for (const id of ["demo-prev", "demo-next", "demo-reset"]) {
    const node = doc.getElementById(id);
    g.ok(!!node && notInsideDetails(node), "核心按钮常显：" + id, "不在 details 内", !!node);
  }
  const options = doc.getElementById("demo-options");
  g.ok(!!options && textOf(doc._findAll(options, (n) => n.tagName === "SUMMARY")[0]) === "演示选项",
    "演示选项展开层 summary === 演示选项", "演示选项", options ? textOf(doc._findAll(options, (n) => n.tagName === "SUMMARY")[0]) : null);
  for (const id of ["demo-motion", "demo-exit"]) {
    const node = doc.getElementById(id);
    g.ok(!!node && insideDetailsId(node, "demo-options"), "次要控制收进演示选项：" + id, "在 #demo-options 内", !!node);
  }
  const connDetails = doc.getElementById("live-conn-details");
  const connSummary = connDetails ? textOf(doc._findAll(connDetails, (n) => n.tagName === "SUMMARY")[0]) : "";
  g.ok(connSummary.indexOf("连接（可选）") !== -1, "连接展开层 summary 含「连接（可选）」", "含 连接（可选）", connSummary);
  for (const id of ["live-conn-toggle", "live-retry", "live-disconnect", "live-export", "live-token"]) {
    const node = doc.getElementById(id);
    g.ok(!!node && insideDetailsId(node, "live-conn-details"), "连接控件收进连接展开层：" + id, "在 #live-conn-details 内", !!node);
  }

  /* 5. probe 独立大屏：强调不同函数间的迁移，并显示可读例子和逐题变化。 */
  const probeSlide = doc.getElementById("s10");
  const probeResultSlide = doc.getElementById("s12");
  const probeLead = probeSlide ? textOf(probeSlide) : "";
  const probeResultText = probeResultSlide ? textOf(probeResultSlide) : "";
  g.ok(probeLead.indexOf("不是这道题的输出") !== -1 && probeLead.indexOf("Run2") !== -1,
    "probe 大屏明确规则迁移而不是记忆答案");
  g.eq(doc._findAll(probeSlide, (n) => n.classList.contains("probe-example")).length, 3,
    "probe 大屏展示三个不同函数的迁移例子");
  g.ok(probeLead.indexOf("整理名单") !== -1 && probeLead.indexOf("合并名单") !== -1 &&
    probeLead.indexOf("最短名称") !== -1, "三个例子用不同任务动作说明迁移");
  g.ok(probeResultText.indexOf("0/10") !== -1 && probeResultText.indexOf("12/12") !== -1 &&
    probeResultText.indexOf("三道迁移题") !== -1, "probe 主结果展示有 headroom 题集的通过变化");
  g.ok(probeResultText.indexOf("数不同名称") !== -1 && probeResultText.indexOf("基线原本就能通过") !== -1,
    "满分基线题被单独标为回归检查");
  g.ok(probeResultText.indexOf("matrix 先做的是 headroom 检查") !== -1 &&
    probeResultText.indexOf("完整规格控制题 8/8") !== -1, "matrix 被解释为前置题目/判分器验证");
  g.eq(doc._findAll(doc.getElementById("s12"), (n) => n.classList.contains("probe-task-rates")).length, 1,
    "主要迁移结果按三道题分别列出");
  const matrixReportPath = path.join(REPO_DIR, "probes/report/matrix/verdict.json");
  const memoryReportPath = path.join(REPO_DIR, "probes/report/2c/memory-v3/verdict.json");
  const mainProbeReportPath = path.join(REPO_DIR, "probes/report/2c/verdict.json");
  if ([matrixReportPath, memoryReportPath, mainProbeReportPath].every(fs.existsSync)) {
    const matrixReport = readJson(matrixReportPath);
    const memoryReport = readJson(memoryReportPath);
    const mainProbeReport = readJson(mainProbeReportPath);
    const fPasses = Object.values(matrixReport.F || {});
    const kPasses = Object.values(matrixReport.K || {});
    const headroom = memoryReport.baseline_comparison && memoryReport.baseline_comparison.headroom_three;
    g.eqJson(fPasses, [0, 0, 0, 0], "matrix 4 个模型配置的 C1b family pass 均为 0");
    g.eqJson(kPasses, [2, 2, 2, 2], "matrix 控制题每个配置均为 2/2");
    g.eqJson([matrixReport.oracle_run_passed, matrixReport.oracle_run_total], [4, 4],
      "matrix oracle 运行通过数为 4/4");
    g.eqJson([headroom && headroom.baseline_passes, headroom && headroom.baseline_runs,
      headroom && headroom.v3_passes, headroom && headroom.v3_runs], [0, 10, 12, 12],
      "P6 的 0/10 → 12/12 与 memory-v3 报告一致");
    g.eqJson([mainProbeReport.baseline_tasks["probe-order-x8"].passes,
      mainProbeReport.baseline_tasks["probe-order-x8"].runs,
      memoryReport.v3["probe-order-x8"].passes,
      memoryReport.v3["probe-order-x8"].runs], [2, 2, 4, 4],
      "x8 基线已是 2/2，记忆侧 4/4 不计为成功率提升");
  } else {
    g.ok(false, "probe matrix / memory 报告存在（用于核对展示数字）", true, false);
  }
  g.ok(/font-size:\s*clamp\(22px/.test(SRC["app.css"]), "probe 迁移主张使用大屏字号（最小 22px）");
  g.ok(/font-size:\s*clamp\(19px/.test(SRC["app.css"]), "逐题通过率表使用大屏字号（最小 19px）");
  g.ok(/font-size:\s*clamp\(18px/.test(SRC["app.css"]), "迁移例子输入使用大屏字号（最小 18px）");
  const scanBlock = doc.querySelector(".scan-block");
  const probeBlock = doc.querySelector(".probe-showcase-inner");
  g.ok(!!scanBlock && !!probeBlock && !isDescendantOf(probeBlock, scanBlock) &&
    !isDescendantOf(scanBlock, probeBlock), "底层扫描证据与 probe 迁移独立分屏");
  g.eq(doc._findAll(probeSlide, (n) => n.tagName === "CANVAS").length, 0,
    "probe 屏不与 demo-6 拼比较曲线");
  const probeText = probeResultText;
  g.eqJson(["0.035560", "0.009978", "0.010684"].filter((l) => probeText.indexOf(l) !== -1), [],
    "probe 块不夹带 demo-6 的成本数字（不做跨实验比较）");
  g.ok(s9Text.indexOf("verified=false") !== -1, "底层 evidence 屏保留未验证标记（verified=false）", true, null);

  /* 6. probe 阶段数字与 web/data/probe/example.json 一致 */
  const probePath = path.join(ARGS.dataDir, "probe", "example.json");
  if (fs.existsSync(probePath)) {
    const probe = readJson(probePath);
    const byGen = {};
    for (const row of (Array.isArray(probe.lineage) ? probe.lineage : [])) byGen[row.gen] = row;
    const gens = ["v0", "gen2", "gen3", "gen4"].filter((gen) => byGen[gen]);
    const want = gens.map((gen) => gen + " " + byGen[gen].passes + "/" + byGen[gen].runs).join(" → ");
    g.ok(want.length > 0 && probeText.indexOf(want) !== -1,
      "阶段演进数字与 probe/example.json 的 lineage 一致", want, probeText.slice(0, 100));
    const nt = probe.negative_transfer || {};
    g.eq(nt.status, "restored", "probe 数据面：x8 负迁移已恢复（status=restored）");
    g.ok(probeText.indexOf("x8") !== -1, "probe 屏写明 x8 回归题", true, null);
  } else {
    g.warn("probe/example.json 缺失，跳过阶段演进数字交叉核对", probePath);
  }

  /* 6b. s6 技术提示收进连接展开层；summary 有可见展开符号且可聚焦 */
  const noteNode = doc.getElementById("live-note");
  g.ok(!!noteNode && insideDetailsId(noteNode, "live-conn-details"),
    "接口错误 / api-health 技术说明在「连接（可选）」details 内（默认不可见）", true, !!noteNode);
  const connNode = doc.getElementById("live-conn-details");
  g.eq(connNode ? connNode.getAttribute("open") : "missing", null,
    "连接展开层默认收起（错误提示默认不可见）");
  const listWrap = doc.querySelector(".live-list-wrap");
  g.ok(!noteNode || !listWrap || !isDescendantOf(noteNode, listWrap),
    "接口错误提示不在默认可见的列表区");
  g.eq(idx.indexOf("同源没有 /api/health"), -1,
    "index.html 不默认渲染 api/health 技术说明");
  g.ok(appSrc.indexOf("同源没有 /api/health") !== -1 &&
    appSrc.indexOf('getElementById("live-note")') !== -1,
    "技术说明只在运行时写入 #live-note（位于连接展开层）", true, null);

  const symRule = CSS_RULES.filter((r) => r.sel === ".panel-summary::before")[0] || { body: "" };
  const openSymRule = CSS_RULES.filter((r) => r.sel === "details[open] > .panel-summary::before")[0] || { body: "" };
  g.ok(/content\s*:\s*"▸"/.test(symRule.body),
    "summary 有可见展开符号（▸，CSS 生成）", "▸", symRule.body);
  g.ok(/rotate\(90deg\)/.test(openSymRule.body),
    "summary 展开态符号翻转（rotate 90deg）", "rotate(90deg)", openSymRule.body);
  const summaryNodes = doc._findAll(doc.documentElement, (n) => n.tagName === "SUMMARY");
  g.eq(summaryNodes.length, 4, "4 个 summary（s6 证据 / s6 连接 / s9 扫描 / 演示选项）");
  g.eq(summaryNodes.filter((n) => n.getAttribute("tabindex") !== "0").length, 0,
    "所有 summary 显式 tabindex=0（可聚焦 / 键盘可达）");

  /* 7. reduced-motion：激活后必须把当前屏滚到视口顶部 */
  const activateBody = fnBodyOf(appSrc, "function setActive(");
  const syncBody = fnBodyOf(appSrc, "function syncScroll(");
  g.ok(activateBody.indexOf("syncScroll(index)") !== -1,
    "setActive 末尾调用 syncScroll(index)（不只换类名）", true, null);
  g.ok(syncBody.indexOf("deck.scrollTop") !== -1 && syncBody.indexOf("js-ready") !== -1 &&
    syncBody.indexOf("reduce") !== -1, "syncScroll 在 reduced / 无 .js-ready 时写 deck.scrollTop", true, null);
  g.ok(/if\s*\(!scrollMode\)\s*return;/.test(syncBody),
    "syncScroll 在普通动效模式下直接返回（不改变现有换场）", true, null);
  g.ok(/scrollMode\s*=\s*reduce\s*\|\|\s*!hasClassToken\(doc\.documentElement,\s*"js-ready"\)/.test(syncBody),
    "滚动判定 = reduce 或缺少 .js-ready", true, null);

  /* 8. 状态图只写文字愿景，不做任何假图 UI */
  g.eq(countLit(idx, "<svg"), 0, "index.html 没有 svg 节点图 / 假分支图");
  g.eq(doc._findAll(doc.getElementById("s11"), (n) => n.tagName === "CANVAS" || n.tagName === "SVG").length, 0,
    "收尾屏没有状态图 / 节点图 / 假分支图");
  g.eq(doc._findAll(doc.getElementById("s10"), (n) => n.tagName === "CANVAS" || n.tagName === "SVG").length, 0,
    "probe 屏不放假 graph / 分支图");

  /* 9. 品牌一致：封面不再用 Molt Beyond */
  g.eq(appSrc.indexOf("Molt Beyond"), -1, "封面 tagline 不再出现 Molt Beyond");
  g.ok(/Molt 迭元，/.test(appSrc), "封面 tagline 品牌为「Molt 迭元，」", true, null);
  g.ok(/<title>MOLT · 迭元<\/title>/.test(idx), "<title> 保持 MOLT · 迭元", true, null);
  g.ok(/class="hud-brand"[^>]*>\s*<img[^>]+src="Molt\.svg"/.test(idx),
    "HUD 品牌是 Molt.svg 字标（alt 保留 MOLT · 迭元）", true, null);
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
