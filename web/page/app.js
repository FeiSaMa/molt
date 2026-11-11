/*!
 * molt · web/page/app.js —— 启动 + 投屏键盘翻页
 *
 * 职责边界：
 *   - boot()：window.MOLT_VIEW → MOLT_MODEL.build → MOLT_RENDER.render；出错只报错，不造数字。
 *   - 3 秒兜底：仍未渲染就把 #data-warning 显示出来（保留 W-01 的既有语义）。
 *   - 键盘：→ / PageDown / Space 下一屏，← / PageUp 上一屏，Home / End 首尾屏。
 *
 * 零网络 / 零存储 / 零模块语法（普通 <script defer>，file:// 直开可用）。
 */
(function (root) {
  "use strict";

  var FALLBACK_MS = 3000;
  var WARN_TEXT = "数据未加载：请确认同目录可读 model.js / render.js 与 ../data/bundle.js";
  var SCREEN_SELECTOR = "main section.screen";

  var doc = root.document;

  function warnEl() {
    return doc ? doc.getElementById("data-warning") : null;
  }

  function showWarning(text) {
    var box = warnEl();
    if (!box) return;
    if (typeof text === "string" && text !== "") box.textContent = text;
    box.hidden = false;
  }

  function hideWarning() {
    var box = warnEl();
    if (box) box.hidden = true;
  }

  function isReady() {
    var html = doc ? doc.documentElement : null;
    return !!(html && html.dataset && html.dataset.ready === "1");
  }

  /* ---------- 启动 ---------- */

  function boot() {
    if (!doc) return;

    if (!root.MOLT_VIEW) {
      showWarning(WARN_TEXT);
      return;
    }
    if (!root.MOLT_MODEL || typeof root.MOLT_MODEL.build !== "function" ||
        !root.MOLT_RENDER || typeof root.MOLT_RENDER.render !== "function") {
      showWarning("渲染脚本缺失。" + WARN_TEXT);
      return;
    }

    var result;
    try {
      var model = root.MOLT_MODEL.build(root.MOLT_VIEW);
      result = root.MOLT_RENDER.render(root.MOLT_VIEW, { doc: doc, model: model });
    } catch (err) {
      showWarning("渲染失败：" + (err && err.message ? err.message : String(err)));
      return;
    }

    if (result && result.ok === true) {
      doc.documentElement.dataset.ready = "1";
      hideWarning();
      return;
    }

    var reasons = result && result.errors && result.errors.length
      ? result.errors.join("；")
      : "数据不完整";
    showWarning("未渲染数字（数据不完整）：" + reasons);
  }

  /* ---------- 键盘翻页 ---------- */

  function screens() {
    return doc.querySelectorAll(SCREEN_SELECTOR);
  }

  /* 当前屏 = 顶部已滚到"视口上缘附近"的最后一屏。
     阈值取视口 1/4：scrollIntoView 带 scroll-margin 时落点可能停在 16px 而不是 0，
     阈值太小会让"当前屏"永远算成上一屏，翻页就卡住。 */
  function reachThreshold() {
    var h = root.innerHeight || 720;
    var t = Math.round(h * 0.25);
    return t < 24 ? 24 : t;
  }

  function activeIndex(list) {
    var limit = reachThreshold();
    var index = 0;
    var found = false;
    for (var i = 0; i < list.length; i++) {
      if (list[i].getBoundingClientRect().top <= limit) {
        index = i;
        found = true;
      }
    }
    return found ? index : 0;
  }

  function goTo(index) {
    var list = screens();
    if (list.length === 0) return;
    var i = index;
    if (i < 0) i = 0;
    if (i > list.length - 1) i = list.length - 1;
    list[i].scrollIntoView({ block: "start" });
  }

  /* 键盘只用于翻页：任何表单控件 / 链接 / 按钮 / <summary> 上的按键都不抢 */
  var KEY_SAFE_TAGS = { INPUT: 1, TEXTAREA: 1, SELECT: 1, BUTTON: 1, A: 1, SUMMARY: 1 };

  function isTypingTarget(node) {
    if (!node || !node.tagName) return false;
    var tag = String(node.tagName).toUpperCase();
    if (KEY_SAFE_TAGS[tag] === 1) return true;
    return node.isContentEditable === true;
  }

  function onKeyDown(e) {
    if (!e || e.defaultPrevented) return;
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (isTypingTarget(e.target)) return;

    var list = screens();
    if (list.length === 0) return;

    var cur = activeIndex(list);
    var key = e.key;

    if (key === "ArrowRight" || key === "PageDown" || key === " " || key === "Spacebar") {
      /* 末屏不再吞键：把 Space / PageDown 交还浏览器，用户才能滚到 footer 的折叠口径说明 */
      if (cur >= list.length - 1) return;
      e.preventDefault();
      goTo(cur + 1);
      return;
    }
    if (key === "ArrowLeft" || key === "PageUp") {
      if (cur <= 0) return;
      e.preventDefault();
      goTo(cur - 1);
      return;
    }
    if (key === "Home") {
      e.preventDefault();
      goTo(0);
      return;
    }
    if (key === "End") {
      e.preventDefault();
      goTo(list.length - 1);
    }
  }

  /* ---------- 装配 ---------- */

  if (doc) {
    boot();

    doc.addEventListener("keydown", onKeyDown, false);

    root.setTimeout(function () {
      if (isReady()) return;
      var box = warnEl();
      if (!box) return;
      if (box.hidden) {
        box.textContent = WARN_TEXT;
        box.hidden = false;
      }
    }, FALLBACK_MS);
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
