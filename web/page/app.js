/*!
 * molt · web/page/app.js —— 启动 + 滚动翻页 + 换场/入场/视差/计数动效
 *
 * 职责边界：
 *   - boot()：window.MOLT_VIEW → MOLT_MODEL.build → MOLT_RENDER.render；出错只报错，不造数字。
 *   - 3 秒兜底：仍未渲染就把 #data-warning 显示出来。
 *   - .js-ready：只有 JS 跑起来才加这个类，CSS 的「初始隐藏」全部挂在它下面；
 *     无 JS 或 prefers-reduced-motion: reduce 时不加，页面就是静态全可见。
 *   - IntersectionObserver(threshold ≥ .45) 决定当前屏；滚动监听用 rAF 节流维护 --p 视差。
 *   - 键盘：↑/↓/PageUp/PageDown/Space 逐屏，←/→ 同义，Home/End 首尾；末屏不吞键。
 *
 * 零网络 / 零存储 / 零模块语法（普通 <script defer>，file:// 直开可用）。
 */
(function (root) {
  "use strict";

  var FALLBACK_MS = 3000;
  var WARN_TEXT = "数据未加载：请确认同目录可读 model.js / render.js 与 ../data/bundle.js";
  var SLIDE_SELECTOR = "main.deck section.slide";
  var COUNT_MS = 900;      // 数字计数（与内容入场的 900ms 同步）
  var SETTLE_MS = 120;     // 滚动静止判定：这段没有 scroll 才允许写样式
  var FAR_LOCK_MS = 1200;  // 远跳「目标锁」兜底：超时仍未到达就强制落到最近屏

  var doc = root.document;
  var slides = [];
  var seen = [];           // 哪些屏「播过」：只有播过的屏离开时才停在终态
  var active = -1;
  var reduce = false;
  var rafPending = 0;
  var settleTimer = 0;
  var raf = root.requestAnimationFrame
    ? function (cb) { return root.requestAnimationFrame(cb); }
    : function (cb) { return root.setTimeout(cb, 16); };
  var cancelRaf = root.cancelAnimationFrame
    ? function (id) { root.cancelAnimationFrame(id); }
    : function (id) { root.clearTimeout(id); };
  /* 计数是逐帧插值：没有原生 rAF 时不假装动画（直接落终值） */
  var hasRaf = typeof root.requestAnimationFrame === "function";

  /* ---------- 提示条 ---------- */

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
    return !!(html && html.getAttribute && html.getAttribute("data-ready") === "1");
  }

  /* ---------- 启动 ---------- */

  function boot() {
    if (!doc) return false;

    if (!root.MOLT_VIEW) {
      showWarning(WARN_TEXT);
      return false;
    }
    if (!root.MOLT_MODEL || typeof root.MOLT_MODEL.build !== "function" ||
        !root.MOLT_RENDER || typeof root.MOLT_RENDER.render !== "function") {
      showWarning("渲染脚本缺失。" + WARN_TEXT);
      return false;
    }

    var result;
    try {
      var model = root.MOLT_MODEL.build(root.MOLT_VIEW);
      result = root.MOLT_RENDER.render(root.MOLT_VIEW, { doc: doc, model: model });
    } catch (err) {
      showWarning("渲染失败：" + (err && err.message ? err.message : String(err)));
      return false;
    }

    if (result && result.ok === true) {
      doc.documentElement.setAttribute("data-ready", "1");
      hideWarning();
      return true;
    }

    var reasons = result && result.errors && result.errors.length
      ? result.errors.join("；")
      : "数据不完整";
    showWarning("未渲染数字（数据不完整）：" + reasons);
    return false;
  }

  /* ---------- 屏与 HUD ---------- */

  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function titleOf(node) {
    var h = node.querySelector("h1, h2");
    var t = h && h.textContent ? String(h.textContent).replace(/\s+/g, " ").replace(/^\s+|\s+$/g, "") : "";
    return t === "" ? "第 " + pad(slides.length) + " 屏" : t;
  }

  function buildDots() {
    var nav = doc.getElementById("dots");
    var total = doc.getElementById("pager-total");
    if (total) total.textContent = pad(slides.length);
    if (!nav || slides.length === 0) return;

    var i;
    for (i = 0; i < slides.length; i++) {
      var b = doc.createElement("button");
      b.type = "button";
      b.className = "dot";
      b.setAttribute("data-index", String(i));
      b.setAttribute("aria-label", "第 " + pad(i + 1) + " 屏：" + titleOf(slides[i]));
      b.addEventListener("click", onDotClick, false);
      nav.appendChild(b);
    }
  }

  function onDotClick(e) {
    var node = e && e.currentTarget ? e.currentTarget : this;
    /* 圆点可能跨多屏：一律按远跳处理（沿途屏不激活） */
    goTo(Number(node.getAttribute("data-index")), { far: true });
  }

  function updateHud(index) {
    var cur = doc.getElementById("pager-cur");
    if (cur) cur.textContent = pad(index + 1);

    var dots = doc.querySelectorAll("#dots .dot");
    for (var i = 0; i < dots.length; i++) {
      if (i === index) {
        dots[i].className = "dot is-active";
        dots[i].setAttribute("aria-current", "true");
      } else {
        dots[i].className = "dot";
        dots[i].removeAttribute("aria-current");
      }
    }
  }

  /* ---------- 计数 / 点亮 ----------
   * 契约（W-27）：数字和晕染一样，**每次激活都从头播**。
   *   - startCounts(slide)：先把该屏上一次没跑完的计数收尾（取消 rAF），
   *     再从 0 播到终值（COUNT_MS，easeOutCubic）。同一屏被极短间隔二次激活
   *     （连按 / 点回原屏）也从头来，不排队、不复用旧进度。
   *   - flushCounts(slide)：离开该屏（或被新激活抢占）时，把还没跑完的数字
   *     直接写成终值 —— 屏切走了不该留着半截数字。
   * 只给整数槽位（render.js 只对 /^\d+$/ 的文本写 data-count），金额不参与计数。
   */
  var counts = [];   // 未完成的计数项：{ slide, node, finalText, done, id }

  function finalTextOf(node) {
    var f = node.getAttribute("data-count-final");
    return f !== null && f !== "" ? f : String(node.getAttribute("data-count"));
  }

  /* 传 slide：只收这一屏的（正常路径）；不传：全部收尾（装配兜底） */
  function flushCounts(slide) {
    var rest = [];
    for (var i = 0; i < counts.length; i++) {
      var c = counts[i];
      if (slide && c.slide !== slide) {
        rest.push(c);
        continue;
      }
      if (c.done) continue;
      c.done = true;
      if (c.id) cancelRaf(c.id);
      c.node.textContent = c.finalText;
    }
    counts = rest;
  }

  function countUp(slide, node) {
    var target = Number(node.getAttribute("data-count"));
    var finalText = finalTextOf(node);
    if (!isFinite(target)) {
      node.textContent = finalText;
      return;
    }

    var item = { slide: slide, node: node, finalText: finalText, done: false, id: 0 };
    counts.push(item);

    var t0 = null;

    function frame(now) {
      if (item.done) return;
      if (t0 === null) t0 = now;
      var k = (now - t0) / COUNT_MS;
      if (k > 1) k = 1;
      /* easeOutCubic */
      var e = 1 - Math.pow(1 - k, 3);
      node.textContent = String(Math.round(target * e));
      if (k < 1) {
        item.id = raf(frame);
      } else {
        node.textContent = finalText;
        item.done = true;
      }
    }

    node.textContent = "0";
    item.id = raf(frame);
  }

  /* 只在 .js-ready（有动效）下跑：减少动效 / 无 rAF 时数字直接是终值。 */
  function startCounts(slide) {
    if (!slide) return;
    var i;
    var nums = slide.querySelectorAll("[data-count]");
    /* 该屏上一次没跑完的先取消并落终值，再从头播（不排队抢帧） */
    flushCounts(slide);
    if (reduce || !hasRaf) {
      for (i = 0; i < nums.length; i++) nums[i].textContent = finalTextOf(nums[i]);
      return;
    }
    for (i = 0; i < nums.length; i++) countUp(slide, nums[i]);
  }

  function addClass(node, cls) {
    if (!node || !node.className) return;
    if ((" " + node.className + " ").indexOf(" " + cls + " ") !== -1) return;
    node.className = node.className + " " + cls;
  }

  function removeClass(node, cls) {
    if (!node || !node.className) return;
    node.className = (" " + node.className + " ")
      .split(" " + cls + " ").join(" ")
      .replace(/^\s+|\s+$/g, "")
      .replace(/\s+/g, " ");
  }

  /* ---------- 墨水晕染：每次激活都重播 ----------
   * 缺陷（W-26 之前）：晕染挂在一个只加不删的「首访标记」CSS 类名上，首访播一次、
   * 回访无声；判断还写死成「同一屏不重播」，与「每次进入某一屏都要有晕染」相反。
   * 现在：动画绑定在 .is-active（见 app.css §16），进入即命中；再用下面这个
   * 单一入口显式重启一次，保证「同一屏在极短间隔内被再次激活」（快速连按、
   * 圆点跳转回原屏）也能从 0% 重新开始 —— 同帧内既删又加类名时，浏览器可能把
   * 两处样式变更合并，动画不会重来。
   * 顺序要求：必须先把 .is-active 加到该屏（选择器命中），再调本函数。
   * 调用点只在激活路径（setActive），不进 scroll 回调：滚动期零样式写入不变。
   */
  function restartInk(slide) {
    if (!slide || reduce) return;
    var nodes = slide.querySelectorAll(".ink i");
    var i;
    for (i = 0; i < nodes.length; i++) nodes[i].style.animation = "none";
    void slide.offsetWidth;   /* 强制一次 reflow，仅此一处：让浏览器确认动画已被取消 */
    for (i = 0; i < nodes.length; i++) nodes[i].style.animation = "";
  }

  function setActive(index) {
    if (index < 0 || index >= slides.length) return;
    if (index === active) return;
    var prev = active;
    active = index;

    /* 先把上一屏的 .is-active 撤掉，再加新屏的：撤在前、加在后、重启在最后 */
    for (var i = 0; i < slides.length; i++) {
      if (i !== index) {
        removeClass(slides[i], "is-active");
        /* 只有「播过」的屏才停在终态；没播过的屏要保持初始隐藏，
           否则它的入场动画会在第一次进入时被跳过（终值 → 终值，没有过渡）。 */
        if (seen[i]) addClass(slides[i], "is-revealed");
      }
    }

    removeClass(slides[index], "is-revealed");
    addClass(slides[index], "is-active");
    seen[index] = true;
    /* 无条件重播：不区分上翻 / 下翻 / 圆点 / Home / End */
    restartInk(slides[index]);
    /* 计数顺序（W-27）：restartInk → 收尾旧屏 → 从头播新屏。
       反了的话，旧屏的收尾会把新屏刚写下的 0 冲成终值。 */
    if (prev >= 0 && prev !== index) flushCounts(slides[prev]);
    startCounts(slides[index]);

    updateHud(index);
  }

  /* ---------- 视差 --p（只在滚动静止后写） ----------
   * 性能约定（W-22 §4.2.2）：
   *   - 滚动过程中一个样式都不写：只用 scroll 事件排一次「静止 120ms」判定，
   *     有原生 scrollend 时以它为准（定时器仅作回退）。
   *   - 只对当前屏算、只对当前屏写；不遍历 8 屏。
   *   - 位移量化到 1px（--p 的用途是 translateY(-48px * p)，故 1/48 一档），
   *     静止时数值不变 → 零写入、零重绘。
   */

  var pCache = [];

  function applyParallax() {
    rafPending = 0;
    if (reduce) return;

    var index = active;
    if (index < 0 || index >= slides.length) return;

    var h = root.innerHeight || 1;

    /* 一次读：只读当前屏 */
    var rect = slides[index].getBoundingClientRect();
    var p = (-rect.top) / h;
    if (p < 0) p = 0;
    if (p > 1) p = 1;

    var step = Math.round(p * 48);          // 量化到 1px
    var v = (step / 48).toFixed(3);
    if (pCache[index] === v) return;        // 静止时零写入

    pCache[index] = v;
    slides[index].style.setProperty("--p", v);
  }

  var hasScrollEnd = false;

  function settle() {
    if (settleTimer) {
      root.clearTimeout(settleTimer);
      settleTimer = 0;
    }
    /* 读写分离：先读完当前屏的 rect，再统一写 transform，帧内不交替 */
    if (rafPending) return;
    rafPending = raf(applyParallax);
  }

  function onScroll() {
    if (settleTimer) root.clearTimeout(settleTimer);
    if (hasScrollEnd) return;   // 有 scrollend 就等它，不定时器
    settleTimer = root.setTimeout(settle, SETTLE_MS);
  }

  /* ---------- 当前屏判定 ---------- */

  /* ---------- 远跳抑制（W-27） ----------
   * 圆点 / Home / End 一次跨多屏：平滑滚动会把中间每一屏都扫过一遍。
   * 那些屏只是「途经」，不是「到达」——一旦激活，入场 + 晕染 + 计数都会在
   * 滚动途中各播一遍，视觉上就是一路闪。做法：goTo 判定 far 后上一个目标锁
   * pendingTarget，IO 回调统一经 onIntersect 只认目标屏，其余早退（不激活、
   * 不播晕染、不计数、不写样式）。到达目标即解锁；FAR_LOCK_MS 兜底，
   * 防止「滚到一半被取消」时锁一直挂着。相邻翻页 far:false，不上锁。
   */

  var pendingTarget = null;
  var pendingTimer = 0;

  function clearPending() {
    pendingTarget = null;
    if (pendingTimer) {
      root.clearTimeout(pendingTimer);
      pendingTimer = 0;
    }
  }

  /* 兜底：锁超时还没等到目标屏（滚动被打断 / 目标判定没命中）→ 直接按几何位置落屏 */
  function onFarTimeout() {
    pendingTimer = 0;
    if (pendingTarget === null) return;
    clearPending();
    var near = activeIndex();
    if (near !== active) setActive(near);
  }

  /* IO 的唯一落点：远跳途中只放行目标屏 */
  function onIntersect(best) {
    /* 还没到目标屏 —— 途经屏早退：不激活、不播晕染、不计数 */
    if (pendingTarget !== null && best !== pendingTarget) return;
    if (best === pendingTarget) clearPending();
    if (best >= 0) setActive(best);
  }

  function indexOfSlide(node) {
    for (var i = 0; i < slides.length; i++) {
      if (slides[i] === node) return i;
    }
    return -1;
  }

  function initObserver() {
    if (!root.IntersectionObserver) {
      /* 降级：没有 IntersectionObserver 就当所有屏都是当前屏（静态可读，不动效）。
         8 屏同时带 .is-active 的话，CSS 侧的晕染会 24 团一起播；
         这条降级路径本来就不做动效，这里把行内 animation 钉成 none 保持静态。 */
      for (var j = 0; j < slides.length; j++) {
        slides[j].className = slides[j].className.replace(/\s*is-active/g, "") + " is-active";
        var inks = slides[j].querySelectorAll(".ink i");
        for (var m = 0; m < inks.length; m++) inks[m].style.animation = "none";
      }
      active = 0;
      updateHud(0);
      return;
    }

    var io = new root.IntersectionObserver(function (entries) {
      var best = -1;
      var ratio = 0;
      for (var i = 0; i < entries.length; i++) {
        var e = entries[i];
        if (!e.isIntersecting) continue;
        if (e.intersectionRatio < 0.45) continue;
        if (e.intersectionRatio >= ratio) {
          ratio = e.intersectionRatio;
          best = indexOfSlide(e.target);
        }
      }
      if (best >= 0) onIntersect(best);
    }, { threshold: [0.45, 1] });

    for (var k = 0; k < slides.length; k++) io.observe(slides[k]);
  }

  /* ---------- 键盘翻页 ---------- */

  function goTo(index, opts) {
    if (slides.length === 0) return;
    var i = index;
    if (i < 0) i = 0;
    if (i > slides.length - 1) i = slides.length - 1;
    var from = active < 0 ? 0 : active;
    /* 跨 2 屏以上算远跳；圆点 / Home / End 一律由调用方标 far: true，
       相邻翻页（↑↓ / PageUp / PageDown / Space）标 far: false → 不上锁 */
    var far = Math.abs(i - from) > 1;
    if (opts && opts.far === true) far = true;
    else if (opts && opts.far === false) far = false;
    if (far && i !== active) {
      pendingTarget = i;
      if (pendingTimer) root.clearTimeout(pendingTimer);
      pendingTimer = root.setTimeout(onFarTimeout, FAR_LOCK_MS);
    }
    slides[i].scrollIntoView({ block: "start" });
  }

  /* 顶部已越过视口 1/4 的最后一屏就是当前屏 */
  function activeIndex() {
    var limit = Math.round((root.innerHeight || 720) * 0.25);
    if (limit < 24) limit = 24;
    var index = 0;
    var found = false;
    for (var i = 0; i < slides.length; i++) {
      if (slides[i].getBoundingClientRect().top <= limit) {
        index = i;
        found = true;
      }
    }
    return found ? index : 0;
  }

  var KEY_SAFE_TAGS = { INPUT: 1, TEXTAREA: 1, SELECT: 1, BUTTON: 1, A: 1, SUMMARY: 1 };

  function isTypingTarget(node) {
    if (!node || !node.tagName) return false;
    if (KEY_SAFE_TAGS[String(node.tagName).toUpperCase()] === 1) return true;
    return node.isContentEditable === true;
  }

  function onKeyDown(e) {
    if (!e || e.defaultPrevented) return;
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (isTypingTarget(e.target)) return;
    if (slides.length === 0) return;

    var cur = activeIndex();
    var key = e.key;

    if (key === "ArrowRight" || key === "ArrowDown" || key === "PageDown" ||
        key === " " || key === "Spacebar") {
      /* 末屏不再吞键：把 Space / PageDown 交还浏览器 */
      if (cur >= slides.length - 1) return;
      e.preventDefault();
      /* 相邻翻页：不设远跳锁（far: false），行为与 W-26 一致 */
      goTo(cur + 1, { far: false });
      return;
    }
    if (key === "ArrowLeft" || key === "ArrowUp" || key === "PageUp") {
      if (cur <= 0) return;
      e.preventDefault();
      goTo(cur - 1, { far: false });
      return;
    }
    if (key === "Home") {
      e.preventDefault();
      /* 首尾跳转可能跨多屏：按远跳处理 */
      goTo(0, { far: true });
      return;
    }
    if (key === "End") {
      e.preventDefault();
      goTo(slides.length - 1, { far: true });
    }
  }

  /* ---------- 装配 ---------- */

  if (doc) {
    try {
      reduce = !!(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (err) {
      reduce = false;
    }

    slides = doc.querySelectorAll(SLIDE_SELECTOR);

    /* 动效相关的初始隐藏只在 .js-ready 下生效；减少动效时不加，保持静态全可见 */
    if (!reduce) {
      doc.documentElement.className += " js-ready";
    }
    doc.documentElement.className += " js";

    boot();
    buildDots();
    initObserver();
    if (slides.length > 0 && active < 0) setActive(0);

    var deck = doc.getElementById("main");
    if (deck) {
      deck.addEventListener("scroll", onScroll, { passive: true });
      try {
        hasScrollEnd = ("onscrollend" in deck);
      } catch (err2) {
        hasScrollEnd = false;
      }
      if (hasScrollEnd) deck.addEventListener("scrollend", settle, false);
    }
    root.addEventListener("resize", settle, false);
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
