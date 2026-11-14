/*!
 * molt · web/page/app.js —— 启动 + 翻页 + 换场/入场/计数动效
 *
 * 职责边界：
 *   - boot()：window.MOLT_VIEW → MOLT_MODEL.build → MOLT_RENDER.render；出错只报错，不造数字。
 *   - 3 秒兜底：仍未渲染就把 #data-warning 显示出来。
 *   - .js-ready：只有 JS 跑起来才加这个类，CSS 的「初始隐藏」全部挂在它下面；
 *     无 JS 或 prefers-reduced-motion: reduce 时不加，页面就是静态全可见（可滚动阅读）。
 *   - 各屏叠在同一个舞台上，靠 .is-active / .is-leaving 交叉换场（淡入淡出 + 模糊）；
 *     没有滚动位置要判定，也没有途经屏要抑制。
 *   - 翻页入口：键盘（↑↓←→ / PageUp / PageDown / Space / Home / End）、右侧圆点、滚轮。
 *
 * 零网络 / 零存储 / 零模块语法（普通 <script defer>，file:// 直开可用）。
 */
(function (root) {
  "use strict";

  var FALLBACK_MS = 3000;
  var WARN_TEXT = "数据未加载：请确认同目录可读 model.js / render.js 与 ../data/bundle.js";
  var SLIDE_SELECTOR = "main.deck section.slide";
  var COUNT_MS = 900;      // 数字计数（与内容入场的 900ms 同步）
  var WHEEL_LOCK_MS = 520; // 滚轮翻页上锁：一次连续滑动只翻一屏（≈ 换场 400ms + 余量）

  var doc = root.document;
  var slides = [];
  var seen = [];           // 哪些屏「播过」：只有播过的屏离开时才停在终态
  var active = -1;
  var reduce = false;
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
      var bootLive = root.MOLT_LIVE && root.MOLT_LIVE.resolve ? root.MOLT_LIVE.resolve() : null;
      if (!bootLive || !bootLive.token) liveUpdateCps(0, 0, 0, 0, 0);
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
    goTo(Number(node.getAttribute("data-index")));
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

  /* ---------- 封面打字机 ----------
   * 循环播放两句文案；改文案只改 TAGLINES。
   *   - 每个元素 = 一句；句内片段 { t: "文字", hot: true } 中 hot 的字符标红。
   *   - 片段里的 "\n" 会渲染成 <br>（一行文案内部换行）。
   *   - 没有 JS / prefers-reduced-motion 时不启动，显示 HTML 里的静态兜底。
   */
  var TAGLINES = [
    [{ t: "Molt Beyond，\n" }, { t: "让智能学会" }, { t: "进化", hot: true }],
    [{ t: "让" },{ t: "Agent\n", hot: true }, { t: "在证据中" }, { t: "持续优化", hot: true }]
  ];
  var TW_TYPE_MS = 95;    /* 逐字出现 */
  var TW_ERASE_MS = 38;   /* 逐字回删 */
  var TW_HOLD_MS = 2800;  /* 打完停留 */
  var TW_GAP_MS = 320;    /* 删完到下一句的间隔 */

  var twTimer = 0;

  function taglineChars(line) {
    var out = [];
    for (var i = 0; i < line.length; i++) {
      var seg = line[i];
      var text = String(seg.t === undefined ? "" : seg.t);
      for (var j = 0; j < text.length; j++) {
        out.push({ ch: text.charAt(j) === "\n" ? null : text.charAt(j), hot: seg.hot === true });
      }
    }
    return out;
  }

  function startTagline() {
    var host = doc.querySelector(".tagline-text");
    if (!host || reduce) return;      /* 减少动效：保留静态兜底，不打字 */

    var chars = taglineChars(TAGLINES[0]);
    var line = 0;
    var pos = 0;
    var erasing = false;

    var paint = function () {
      while (host.firstChild) host.removeChild(host.firstChild);
      for (var i = 0; i < pos; i++) {
        var c = chars[i];
        if (c.ch === null) {
          host.appendChild(doc.createElement("br"));
          continue;
        }
        var node = doc.createElement(c.hot ? "em" : "span");
        if (c.hot) node.className = "hot";
        node.appendChild(doc.createTextNode(c.ch));
        host.appendChild(node);
      }
    };

    var step = function () {
      if (!erasing) {
        if (pos < chars.length) {
          pos += 1;
          paint();
          twTimer = root.setTimeout(step, chars[pos - 1].ch === null ? 60 : TW_TYPE_MS);
          return;
        }
        erasing = true;
        twTimer = root.setTimeout(step, TW_HOLD_MS);
        return;
      }
      if (pos > 0) {
        pos -= 1;
        paint();
        twTimer = root.setTimeout(step, TW_ERASE_MS);
        return;
      }
      line = (line + 1) % TAGLINES.length;   /* 循环播放 */
      chars = taglineChars(TAGLINES[line]);
      erasing = false;
      twTimer = root.setTimeout(step, TW_GAP_MS);
    };

    step();
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
    /* 整屏显影（app.css §16 slide-bloom-in）搭同一次重排一起重启，不额外加 reflow */
    slide.style.animation = "none";
    void slide.offsetWidth;   /* 强制一次 reflow，仅此一处：让浏览器确认动画已被取消 */
    for (i = 0; i < nodes.length; i++) nodes[i].style.animation = "";
    slide.style.animation = "";
  }

  /* 离开当前屏：加 .is-leaving 播一次 slide-bloom-out（400ms），到时自动摘类。
     不做 generation 守卫：快翻时后一次进入会先把上一屏的 is-leaving 摘掉。 */
  function playBloomOut(slide) {
    if (!slide || reduce) return;
    if (slide.bloomTimer) clearTimeout(slide.bloomTimer);
    addClass(slide, "is-leaving");
    slide.bloomTimer = setTimeout(function () {
      slide.bloomTimer = 0;
      removeClass(slide, "is-leaving");
    }, 460);
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
    removeClass(slides[index], "is-leaving");
    addClass(slides[index], "is-active");
    seen[index] = true;
    /* 离开的那一屏先挂 is-leaving（播淡出 + 模糊），再重启新屏的入场 */
    if (prev >= 0 && prev !== index) playBloomOut(slides[prev]);
    /* 无条件重播：不区分上翻 / 下翻 / 圆点 / Home / End */
    restartInk(slides[index]);
    /* 计数顺序（W-27）：restartInk → 收尾旧屏 → 从头播新屏。
       反了的话，旧屏的收尾会把新屏刚写下的 0 冲成终值。 */
    if (prev >= 0 && prev !== index) flushCounts(slides[prev]);
    startCounts(slides[index]);

    updateHud(index);

    /* 实时面板：进入该屏先清空再连数据源，离开就停 */
    if (live) {
      if (slides[index] === live.slide) {
        /* 同一个 index 的重入（hashchange / 初始化）不要清空并重启正在跑的数据源。 */
        if (prev === index && root.MOLT_LIVE && root.MOLT_LIVE.isOn && root.MOLT_LIVE.isOn()) {
          remember(index);
          return;
        }
        liveReset();
        liveSourceStart();
      } else if (prev >= 0 && slides[prev] === live.slide) {
        liveStop();
        liveSourceStop();
      }
    }

    /* 地址栏 + localStorage 记住当前位置（重载 / 分享都能回到这一屏） */
    remember(index);
  }

  /* ---------- 翻页入口（W-29：取消滚动切屏） ----------
   * 各屏叠在同一个舞台上，只换不滚：没有滚动位置要判定、没有途经屏要抑制，
   * --p 视差随滚动模型一起下线（CSS 里 .parallax 静止在 --p = 0）。
   * 滚轮（含触控板）用 WHEEL_LOCK_MS 上锁：一次连续滑动只翻一屏，
   * 否则触控板一个惯性手势会连翻三四屏。
   */

  var wheelLock = 0;

  function onWheel(e) {
    if (reduce) return;      /* 减少动效时不接管滚轮：页面保持可滚动阅读 */
    if (slides.length === 0) return;

    /* 指针在 S6 的滚动列表里：列表还能往这个方向滚，就把滚轮交还给列表，
       不要翻页。到达顶部 / 底部后才继续走翻页逻辑。 */
    var list = doc ? doc.getElementById("live-list") : null;
    if (list && e.target && list.contains && list.contains(e.target)) {
      var dl = e.deltaY !== 0 ? e.deltaY : e.deltaX;
      var canScroll = list.scrollHeight > list.clientHeight + 1;
      var atTop = list.scrollTop <= 0;
      var atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 1;
      if (canScroll && ((dl < 0 && !atTop) || (dl > 0 && !atBottom))) return;
    }

    /* 舞台不滚动，滚轮只用来翻页：一律吞掉，避免浏览器/触控板把页面拽走 */
    if (typeof e.preventDefault === "function") e.preventDefault();
    var d = e.deltaY !== 0 ? e.deltaY : e.deltaX;
    if (!d || Math.abs(d) < 8) return;      /* 触控板的细碎像素不翻页 */
    var now = Date.now ? Date.now() : new Date().getTime();
    if (now < wheelLock) return;
    wheelLock = now + WHEEL_LOCK_MS;
    goTo((active < 0 ? 0 : active) + (d > 0 ? 1 : -1));
  }

  /* 没有 IntersectionObserver / 几何判定：屏是叠着的，当前屏就是 active 本身。
     首屏在装配阶段由 setActive(0) 激活（见文件末尾），不依赖任何观察器。 */



  /* ---------- 键盘翻页 ---------- */

  function goTo(index) {
    if (slides.length === 0) return;
    var i = index;
    if (i < 0) i = 0;
    if (i > slides.length - 1) i = slides.length - 1;
    if (i === active) return;
    setActive(i);
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

    var cur = active < 0 ? 0 : active;
    var key = e.key;

    if (key === "ArrowRight" || key === "ArrowDown" || key === "PageDown" ||
        key === " " || key === "Spacebar") {
      /* 末屏不再吞键：把 Space / PageDown 交还浏览器 */
      if (cur >= slides.length - 1) return;
      e.preventDefault();
      goTo(cur + 1);
      return;
    }
    if (key === "ArrowLeft" || key === "ArrowUp" || key === "PageUp") {
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
      goTo(slides.length - 1);
    }
  }

  /* ---------- 位置记忆：重载回到原屏 ----------
   * 双写：地址栏 #s6（可分享、手改即跳）+ localStorage（打开裸链接也能回来）。
   * file:// 下个别浏览器不让 replaceState，就退回改 hash —— 会多一条历史，
   * 但「重载还在原屏」这个承诺不缩水。
   */
  var HOME_KEY = "molt.slide.v1";

  /* 屏顺序与 id 不再一一对应（实时排在成本前，但 id 仍是内容锚点 s5/s6…），
     所以按元素 id 查位置，而不是把 #s6 当成「第 6 屏」。 */
  function hashIndex() {
    var h = root.location && root.location.hash ? String(root.location.hash) : "";
    var m = /^#(s\d+)$/.exec(h);
    if (!m) return -1;
    for (var i = 0; i < slides.length; i++) {
      if (slides[i].id === m[1]) return i;
    }
    return -1;
  }

  function storedIndex() {
    try {
      var v = root.localStorage ? root.localStorage.getItem(HOME_KEY) : null;
      if (v === null || v === "") return -1;
      var i = Number(v);
      return isFinite(i) && i >= 0 && i < slides.length ? Math.floor(i) : -1;
    } catch (err) {
      return -1;   /* 隐私模式 / 存储被禁：当作没记过 */
    }
  }

  function remember(index) {
    var want = slides[index] && slides[index].id
      ? "#" + slides[index].id
      : "#s" + (index + 1);
    try {
      if (root.localStorage) root.localStorage.setItem(HOME_KEY, String(index));
    } catch (err) { /* 存不了就算了，hash 还在 */ }
    if (!root.location || root.location.hash === want) return;
    try {
      root.history.replaceState(null, "", want);
    } catch (err) {
      try { root.location.hash = want; } catch (e2) { /* 无历史 API：只是不记地址 */ }
    }
  }

  function startIndex() {
    var i = hashIndex();
    return i >= 0 ? i : storedIndex();
  }

  function onHashChange() {
    var i = hashIndex();
    if (i >= 0 && i !== active) goTo(i);
  }

  /* ---------- S6 实时面板 ----------
   * 默认空载：开机先把列表行藏起来、合计清成「—」、画布清空，不显示任何
   * 预置数据（近似真实上线状态）。数据只从下面两个入口来：
   *
   *   - ?port=<port>&token=<token> → MOLT_LIVE 每 0.5s 拉一次 /api/tasks；
   *   - ?path[=<file>]            → MOCK_SOURCE 读本地样例，按两轮节奏模拟完成。
   *
   * 本文件只做「收到一份逐题快照后怎么显示」：点亮新出现的行、给柱子起长、
   * 按已出现的行累加合计。它不认识 http、URL 或样例文件 —— 那些都在
   * live-source.js / mock-source.js 里；后者整块删掉也不影响本文件。
   *
   * 行永远按题号升序排在列表里（DOM 本来就是升序，只是显示时间不同）；
   * 新行 / 更新行不在可视区时列表缓动过去；横轴 1..N 用 420ms 缓动放大；
   * 每根柱子从 0 长到目标高度（LIVE_BAR_MS）。
   * 只在某一屏被激活时启动数据源，离开就停；再次进入先清空再重新接。
   */
  var LIVE_X_MS = 420;           /* 横轴压缩缓动时长 */
  var LIVE_BAR_MS = 460;         /* 单根柱子从 0 长到目标高度 */
  var LIVE_SCROLL_MS = 420;      /* 列表自动滚动缓动时长 */

  var live = null;
  var liveSourceMode = "";
  var liveAuthWarned = false;   /* 401/403 只提示一次，别每 0.5s 刷一条 */

  /* 纵轴上限取整刻度（1 / 2 / 2.5 / 5 × 10^n）：0.008296 → 0.0100，
     四格刻度于是落在 0.0025 / 0.0050 / 0.0075 上，而不是 0.0023 / 0.0046 …… */
  function niceCeil(v) {
    if (!(v > 0)) return 0.001;
    var exp = Math.pow(10, Math.floor(Math.log(v) / Math.LN10));
    var f = v / exp;
    var n = f <= 1 ? 1 : (f <= 2 ? 2 : (f <= 2.5 ? 2.5 : (f <= 5 ? 5 : 10)));
    return n * exp;
  }

  function cssVar(name, fallback) {
    if (!root.getComputedStyle || !doc || !doc.documentElement) return fallback;
    var v = root.getComputedStyle(doc.documentElement).getPropertyValue(name);
    v = v ? String(v).replace(/^\s+|\s+$/g, "") : "";
    return v === "" ? fallback : v;
  }

  function hasClassToken(node, name) {
    var tokens = String((node && node.className) || "").split(/\s+/);
    for (var i = 0; i < tokens.length; i++) {
      if (tokens[i] === name) return true;
    }
    return false;
  }

  function liveInit() {
    var list = doc.getElementById("live-list");
    var canvas = doc.getElementById("live-chart");
    if (!list || !canvas || !canvas.getContext) return;

    /* 行不预置：开机清掉 render.js 的演示骨架，之后全部由数据源按 taskId 动态创建 */
    while (list.firstChild) list.removeChild(list.firstChild);

    var sumBox = doc.querySelector(".live-sum");
    var sumNodes = sumBox ? sumBox.querySelectorAll("b") : [];
    var sums = {
      base: sumNodes[0] || null,
      cand: sumNodes[1] || null,
      delta: sumNodes[2] || null,
      pass: sumNodes[3] || null
    };

    var slide = list;
    while (slide && !hasClassToken(slide, "slide")) slide = slide.parentNode;

    live = {
      slide: slide,
      list: list,
      canvas: canvas,
      ctx: canvas.getContext("2d"),
      rows: [],
      byTask: {},
      sums: sums,
      timers: [],
      raf: 0,
      anim: 0,
      sraf: 0,
      w: 0,
      h: 0,
      yMax: 0.001,
      maxIdx: 0,
      xShown: 1,
      xFrom: 1,
      xTTo: 1,
      xT0: 0,
      colorBase: cssVar("--indigo", "#1B4D8F"),
      colorCand: cssVar("--red", "#C0392B"),
      grid: "rgba(230, 232, 236, 0.9)",
      label: cssVar("--ink-3", "#6B7280")
    };

    liveFit();
    liveDraw();
  }

  /* 动态行：结构对齐 render.js 原来的静态行（题号 / 题目 / 两次成本） */
  function liveMakeCell(cls) {
    var cell = doc.createElement("span");
    cell.className = "lr-c " + cls;

    var flag = doc.createElement("em");
    flag.className = "lr-flag";
    flag.setAttribute("aria-hidden", "true");
    flag.hidden = true;

    var cost = doc.createElement("b");
    cost.className = "lr-cost";
    cost.textContent = "—";

    var sr = doc.createElement("span");
    sr.className = "sr-only";
    sr.hidden = true;

    cell.appendChild(flag);
    cell.appendChild(cost);
    cell.appendChild(sr);
    return { cell: cell, flag: flag, cost: cost, sr: sr };
  }

  function liveMakeRow(p) {
    var L = live;
    if (!L) return null;

    var idx = (typeof p.index === "number" && isFinite(p.index) && p.index > 0)
      ? Math.round(p.index)
      : (L.maxIdx + 1);

    var li = doc.createElement("li");
    li.className = "live-row";
    li.setAttribute("data-idx", String(idx));
    li.setAttribute("data-task", String(p.taskId));

    var no = doc.createElement("span");
    no.className = "lr-idx";
    no.textContent = String(idx);

    var name = doc.createElement("span");
    name.className = "lr-name";
    var task = doc.createElement("span");
    task.className = "lr-task";
    task.textContent = String(p.taskId);
    name.appendChild(task);

    var bCell = liveMakeCell("lr-base");
    var cCell = liveMakeCell("lr-cand");

    li.appendChild(no);
    li.appendChild(name);
    li.appendChild(bCell.cell);
    li.appendChild(cCell.cell);

    var r = {
      el: li,
      idx: idx,
      taskId: String(p.taskId),
      base: null,
      cand: null,
      basePassed: false,
      candPassed: false,
      baseOn: false,
      candOn: false,
      gBase: -1,
      gCand: -1,
      bT0: 0,
      cT0: 0,
      flag: cCell.flag,
      cost: cCell.cost,
      sr: cCell.sr,
      costText: "—",
      bFlag: bCell.flag,
      bCost: bCell.cost,
      bSr: bCell.sr
    };

    L.rows.push(r);
    L.byTask[r.taskId] = r;
    if (idx > L.maxIdx) L.maxIdx = idx;
    liveInsertRow(r);
    return r;
  }

  /* 行按题号升序插入：先完成的题可能题号更大，后面的题要插到它前面 */
  function liveInsertRow(r) {
    var L = live;
    if (!L || !r) return;
    var ref = null;
    for (var n = L.list.firstChild; n; n = n.nextSibling) {
      var ix = n.getAttribute ? Number(n.getAttribute("data-idx")) : NaN;
      if (isFinite(ix) && ix > r.idx) { ref = n; break; }
    }
    L.list.insertBefore(r.el, ref);
  }

  /* 画布按 CSS 尺寸 × dpr 重建：只在初始化 / resize / 激活时做，不进逐帧 */
  function liveFit() {
    var L = live;
    if (!L) return;
    var dpr = root.devicePixelRatio || 1;
    var w = L.canvas.clientWidth || 640;
    var h = L.canvas.clientHeight || 320;
    if (w <= 0 || h <= 0) return;
    L.canvas.width = Math.round(w * dpr);
    L.canvas.height = Math.round(h * dpr);
    if (L.ctx.setTransform) L.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    L.w = w;
    L.h = h;
  }

  function liveDraw() {
    var L = live;
    if (!L || !L.ctx || L.w <= 0) return;

    var ctx = L.ctx;
    var padL = 58, padR = 18, padT = 12, padB = 24;
    var pw = Math.max(10, L.w - padL - padR);
    var ph = Math.max(10, L.h - padT - padB);
    var xMax = Math.max(2, L.xShown);
    var i, x, y;

    ctx.clearRect(0, 0, L.w, L.h);

    /* 默认空载 / 还没有任何一行到手：整块画布留白，不画假刻度 */
    var hasData = false;
    for (i = 0; i < L.rows.length; i++) {
      if (L.rows[i].baseOn || L.rows[i].candOn) { hasData = true; break; }
    }
    if (!hasData) return;

    /* 纵轴：成本 0 → yMax，4 格 */
    ctx.font = '11px "SF Mono", Menlo, Consolas, monospace';
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (i = 0; i <= 4; i++) {
      y = padT + ph - (ph * i) / 4;
      ctx.strokeStyle = L.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(L.w - padR, y);
      ctx.stroke();
      ctx.fillStyle = L.label;
      ctx.fillText("$" + ((L.yMax * i) / 4).toFixed(4), padL - 8, y);
    }

    /* 横轴：题号落在每组柱子的正中。10 题时全标；几十题时按步长跳标，避免糊成一条 */
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    var band = pw / xMax;
    var shown = Math.max(1, Math.min(L.maxIdx || 1, Math.floor(L.xShown)));
    var every = shown > 12 ? Math.ceil(shown / 10) : 1;
    for (i = 1; i <= shown; i++) {
      if (i % every !== 0 && i !== shown) continue;
      x = padL + (i - 0.5) * band;
      ctx.fillStyle = L.label;
      ctx.fillText(String(i), x, padT + ph + 8);
    }

    /* 横轴正在放大时，新柱可能落在绘图区之外：裁掉，别画到刻度 / 摘要上 */
    ctx.save();
    ctx.beginPath();
    ctx.rect(padL - 4, padT - 4, pw + 8, ph + 8);
    ctx.clip();

    /* 两列柱：同一题第一次在左、第二次在右，柱高 = 成本 × 生长进度。
       进度由 liveFrame 推进 —— 新数据落地时从 0 长上去，不是直接跳到终值。 */
    var bw = Math.max(2.5, Math.min(15, band * 0.3));
    var gap = Math.max(1, bw * 0.22);
    for (i = 0; i < L.rows.length; i++) {
      var r = L.rows[i];
      /* 柱子按题号定位：中间有题还没出现时，已有柱子不会被挤走 */
      bar(r.idx - 1, r.baseOn ? r.base : null, r.gBase, -1, L.colorBase);
      bar(r.idx - 1, r.candOn ? r.cand : null, r.gCand, 1, L.colorCand);
    }
    ctx.restore();

    function bar(k, val, grow, side, color) {
      if (!(val > 0) || !(grow > 0)) return;
      var cx = padL + (k + 0.5) * band;
      var w = Math.max(1.5, bw);
      var x0 = side < 0 ? cx - gap / 2 - w : cx + gap / 2;
      var hh = (val / L.yMax) * ph * Math.min(1, grow);
      var y0 = padT + ph - hh;
      /* 柱底贴住基线：未长起来时高度为 0，视觉上就是从最底下往上伸长 */
      ctx.fillStyle = color;
      ctx.fillRect(x0, y0, w, hh);
      /* 顶部压一道 1px 深色，白底上柱子的收口更利落 */
      ctx.fillStyle = "rgba(20, 22, 26, 0.18)";
      ctx.fillRect(x0, y0, w, 1);
    }
  }

  /* 横轴 domain 缓动：1 → N，easeOutCubic；每来一行重启一次（与柱高共用一个循环） */
  function liveTweenX(to) {
    var L = live;
    if (!L) return;
    if (reduce || !hasRaf) {
      L.xShown = to;
      L.xTTo = to;
      liveDraw();
      return;
    }
    if (L.xTTo === to && L.xShown === to) return;
    L.xFrom = L.xShown;
    L.xTTo = to;
    L.xT0 = 0;
    liveAnimStart();
  }

  /* 起一根柱子：从 0 开始长，进度写回 row.gBase / gCand */
  function liveGrowBar(r, key) {
    if (!r) return;
    if (key === "base") { r.gBase = 0; r.bT0 = 0; }
    else { r.gCand = 0; r.cT0 = 0; }
  }

  /* 单一动画循环：横轴放大 + 每根柱子生长都在这里推进。
     谁还需要动就继续排帧，全静了就停 —— 不空转烧 CPU。 */
  function liveAnimStart() {
    var L = live;
    if (!L) return;
    if (reduce || !hasRaf) { liveSnap(); return; }
    if (L.anim) return;
    L.anim = raf(liveFrame);
  }

  /* 减少动效 / 无 rAF：不演，直接落终值 */
  function liveSnap() {
    var L = live;
    if (!L) return;
    L.xShown = L.xTTo;
    for (var i = 0; i < L.rows.length; i++) {
      L.rows[i].gBase = L.rows[i].baseOn ? 1 : -1;
      L.rows[i].gCand = L.rows[i].candOn ? 1 : -1;
    }
    liveDraw();
  }

  function liveFrame(ts) {
    var L = live;
    if (!L) return;
    var t = ts || 1;
    var busy = false;
    var i;

    if (L.xShown !== L.xTTo) {
      if (!L.xT0) L.xT0 = t;
      var p = Math.min(1, (t - L.xT0) / LIVE_X_MS);
      var e = 1 - Math.pow(1 - p, 3);
      L.xShown = L.xFrom + (L.xTTo - L.xFrom) * e;
      if (p < 1) busy = true;
      else L.xShown = L.xTTo;
    }

    for (i = 0; i < L.rows.length; i++) {
      if (grow(L.rows[i], "base", t)) busy = true;
      if (grow(L.rows[i], "cand", t)) busy = true;
    }

    liveDraw();
    L.anim = busy ? raf(liveFrame) : 0;
  }

  function grow(r, key, t) {
    var g = key === "base" ? r.gBase : r.gCand;
    if (!(g >= 0) || g >= 1) return false;
    var f = key === "base" ? "bT0" : "cT0";
    if (!r[f]) r[f] = t;
    var p = Math.min(1, (t - r[f]) / LIVE_BAR_MS);
    var e = 1 - Math.pow(1 - p, 3);
    if (key === "base") r.gBase = e;
    else r.gCand = e;
    return p < 1;
  }

  function liveScrollTo(top) {
    var L = live;
    if (!L) return;
    if (reduce || !hasRaf) { L.list.scrollTop = top; return; }
    if (L.sraf) cancelRaf(L.sraf);
    var from = L.list.scrollTop;
    var dist = top - from;
    if (Math.abs(dist) < 1) return;
    var t0 = 0;

    var step = function (ts) {
      if (!t0) t0 = ts || 1;
      var p = Math.min(1, ((ts || t0) - t0) / LIVE_SCROLL_MS);
      /* easeInOutCubic：两头慢、中间快，滚过去不突然 */
      var e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      L.list.scrollTop = from + dist * e;
      L.sraf = p < 1 ? raf(step) : 0;
    };
    L.sraf = raf(step);
  }

  /* 更新点不在可视区才滚；已经看得见就不动，避免列表自己抖 */
  function liveEnsureVisible(row) {
    var L = live;
    if (!L || !row || !row.getBoundingClientRect) return;
    var rb = row.getBoundingClientRect();
    var bb = L.list.getBoundingClientRect();
    if (rb.top < bb.top + 2) {
      liveScrollTo(L.list.scrollTop + (rb.top - bb.top) - 4);
    } else if (rb.bottom > bb.bottom - 2) {
      liveScrollTo(L.list.scrollTop + (rb.bottom - bb.bottom) + 4);
    }
  }

  function liveFlash(row) {
    if (reduce || !row) return;
    addClass(row, "is-fresh");
    root.setTimeout(function () { removeClass(row, "is-fresh"); }, 900);
  }

  function liveStop() {
    var L = live;
    if (!L) return;
    for (var i = 0; i < L.timers.length; i++) root.clearTimeout(L.timers[i]);
    L.timers = [];
    if (L.raf) { cancelRaf(L.raf); L.raf = 0; }
    if (L.anim) { cancelRaf(L.anim); L.anim = 0; }
    if (L.sraf) { cancelRaf(L.sraf); L.sraf = 0; }
  }

  /* 第一轮：第二次成本还没有 → 「—」，判定 emoji 与读屏文案都先收起来 */
  function liveCandPending(r) {
    if (!r || !r.cost) return;
    if (r.flag) r.flag.hidden = true;
    if (r.sr) r.sr.hidden = true;
    r.cost.textContent = "—";
  }

  /* 第二轮：把 render.js 落好的原文案放回去（金额 / ✅❌ / 读屏说明） */
  function liveCandDone(r) {
    if (!r || !r.cost) return;
    if (r.flag) r.flag.hidden = false;
    if (r.sr) r.sr.hidden = false;
    r.cost.textContent = r.costText;
  }

  /* 合计：只累加「已经出现」的行，每落地一行就重算一次（数字跟着刷新走）。
     - 基线成本 = Σ 第一轮已出现的行
     - 候选成本 = Σ 第二轮已回填的行
     - 成本差 / 通过差 = 只算「两次都有数」的配对行，避免拿候选的空额去减全量
     颜色：基线 > 候选 → 红，否则绿；还没有可配对的行时保持默认墨色。
     最后一题落地时，四个数正好等于 render.js 用 s2.totals 写的终值。 */
  function liveSetBound(key, text) {
    if (!doc) return;
    var nodes = doc.querySelectorAll('[data-bind="' + key + '"]');
    for (var i = 0; i < nodes.length; i++) nodes[i].textContent = text;
  }

  function liveUpdateCps(pBase, pCand, pBaseOk, pCandOk, paired) {
    var ok = paired > 0 && pBase > 0 && pCand > 0 && pBaseOk > 0 && pCandOk > 0;
    if (!ok) {
      liveSetBound("s1.cpsBase", "—");
      liveSetBound("s1.cpsCand", "—");
      liveSetBound("s1.cpsDropText", "—");
      liveSetBound("s1.cpsDropPct", "—");
      liveSetBound("s1.costMultiplier", "—");
      return;
    }
    var cpsBase = pBase / pBaseOk;
    var cpsCand = pCand / pCandOk;
    var drop = (1 - cpsCand / cpsBase) * 100;
    var dropWord = Math.abs(drop) < 0.05 ? "持平" : (drop > 0 ? "低 " + drop.toFixed(1) + "%" : "高 " + Math.abs(drop).toFixed(1) + "%");
    liveSetBound("s1.cpsBase", cpsBase.toFixed(6));
    liveSetBound("s1.cpsCand", cpsCand.toFixed(6));
    liveSetBound("s1.cpsDropText", dropWord);
    liveSetBound("s1.cpsDropPct", drop.toFixed(1));
    liveSetBound("s1.costMultiplier", (cpsBase / cpsCand).toFixed(1));
  }

  function liveSum() {
    var L = live;
    if (!L || !L.sums) return;

    var i, r;
    var base = 0, cand = 0, pBase = 0, pCand = 0;
    var nBase = 0, nCand = 0, pBaseOk = 0, pCandOk = 0, paired = 0;

    for (i = 0; i < L.rows.length; i++) {
      r = L.rows[i];
      if (r.baseOn && r.base !== null) { base += r.base; nBase++; }
      if (r.candOn && r.cand !== null) { cand += r.cand; nCand++; }
      if (r.baseOn && r.candOn && r.base !== null && r.cand !== null) {
        paired++;
        pBase += r.base;
        pCand += r.cand;
        if (r.basePassed === true) pBaseOk++;
        if (r.candPassed === true) pCandOk++;
      }
    }

    liveUpdateCps(pBase, pCand, pBaseOk, pCandOk, paired);

    var S = L.sums;
    /* 一侧还一行都没有时写「—」：不要用 $0.000000 冒充「已经花了 0 元」 */
    setText(S.base, nBase === 0 ? "—" : "$" + base.toFixed(6));
    setText(S.cand, nCand === 0 ? "—" : "$" + cand.toFixed(6));

    var d = pCand - pBase;
    setText(S.delta, paired === 0 ? "—" :
      (d < 0 ? "−" : (d > 0 ? "+" : "")) + "$" + Math.abs(d).toFixed(6));

    var dp = pCandOk - pBaseOk;
    setText(S.pass, paired === 0 ? "—" :
      (dp < 0 ? "−" : (dp > 0 ? "+" : "")) + Math.abs(dp));

    tone(S.delta, paired === 0 ? "is-idle" : (pBase > pCand ? "is-bad" : "is-good"));
    tone(S.pass, paired === 0 ? "is-idle" : (pBaseOk > pCandOk ? "is-bad" : "is-good"));
    liveHeadline(base, cand, pBaseOk, pCandOk, paired);
  }

  function setText(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
  }

  /* 数据源状态提示：出错时才显示，成功后 hide */
  function liveNote(text, bad) {
    var node = doc ? doc.getElementById("live-note") : null;
    if (!node) return;
    if (!text) {
      node.hidden = true;
      node.textContent = "";
      removeClass(node, "is-bad");
      return;
    }
    node.textContent = text;
    node.hidden = false;
    if (bad) addClass(node, "is-bad");
    else removeClass(node, "is-bad");
  }

  function tone(node, cls) {
    if (!node) return;
    /* 这三个槽位只挂 is-bad / is-good / is-idle 之一，直接赋值最省事 */
    node.className = cls;
  }

  /* 清空面板：默认空载、重新进屏、换数据源前都先回到这里。
     列表行由 render.js 预先落好（含题名），这里只把它们藏起来；
     合计清成「—」，画布按空数据重画。 */
  function liveReset() {
    if (doc) {
      var badge = doc.getElementById("live-badge");
      var cfgBadge = root.MOLT_LIVE && root.MOLT_LIVE.resolve ? root.MOLT_LIVE.resolve() : null;
      if (badge && !liveSourceMode) {
        badge.textContent = cfgBadge && cfgBadge.mode === "live" && cfgBadge.token
          ? "RUNNING · 现场 probe replay（fixture/replay）"
          : "NEED TOKEN · 填入 token 后现场运行";
      }
    }
    var L = live;
    if (!L) return;

    liveUpdateCps(0, 0, 0, 0, 0);
    liveStop();
    liveFit();
    L.xShown = 1;
    L.xFrom = 1;
    L.xTTo = 1;
    L.xT0 = 0;
    L.yMax = 0.001;
    L.maxIdx = 0;
    L.list.scrollTop = 0;
    var resultNode = doc ? doc.getElementById("live-result") : null;
    if (resultNode) resultNode.textContent = "等待现场运行…";
    liveLogClear();
    var cfg0 = root.MOLT_LIVE && root.MOLT_LIVE.resolve ? root.MOLT_LIVE.resolve() : null;
    if (cfg0 && cfg0.mode === "live" && cfg0.token) {
      liveRunState("待运行", false);
      liveConnState("已连接", "is-ok");
    } else {
      liveRunState("需要 token", true);
      liveConnState("需要 token", "is-bad");
    }

    /* 清空整张表：不留任何上一次 / 预置的行 */
    L.rows = [];
    L.byTask = {};
    while (L.list.firstChild) L.list.removeChild(L.list.firstChild);

    addClass(L.list, "is-live");
    liveNote("");
    liveSum();
    liveDraw();
  }

  /* ---------- 数据入口 ----------
   * 两个来源都收敛成同一种 norm：{ pairs: [{taskId, baseCost, candCost,
   * basePassed, candPassed}] }。这里只负责落回行 / 柱子 / 合计。
   *   - ?port= 优先：真实接口，0.5s 轮询（live-source.js）；
   *   - 否则 ?path：本地模拟（mock-source.js，生产可整块删除）；
   *   - 都没有：保持空载，不发任何请求。
   */
  function liveLog(text, bad) {
    var node = doc ? doc.getElementById("live-log") : null;
    if (!node) return;
    node.hidden = false;
    var stamp = new Date().toISOString().slice(11, 19);
    node.textContent += "[" + stamp + "] " + text + "\n";
    node.scrollTop = node.scrollHeight;
    if (bad) addClass(node, "is-bad");
  }

  function liveLogClear() {
    var node = doc ? doc.getElementById("live-log") : null;
    if (!node) return;
    node.textContent = "";
    node.hidden = true;
    removeClass(node, "is-bad");
  }

  function liveRunState(text, disabled) {
    var button = doc ? doc.getElementById("live-run") : null;
    var state = doc ? doc.getElementById("live-run-state") : null;
    if (button) button.disabled = !!disabled;
    if (state) state.textContent = text || "待运行";
  }

  function liveBadge(text, source) {
    var node = doc ? doc.getElementById("live-badge") : null;
    if (!node) return;
    node.textContent = text;
    if (source) liveSourceMode = source;
  }

  function liveEmpty(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function liveSpan(cls, text) {
    var node = doc.createElement("span");
    if (cls) node.className = cls;
    node.textContent = text;
    return node;
  }

  function liveHeadline(baseCost, candCost, passBase, passCand, paired) {
    var node = doc ? doc.getElementById("live-result") : null;
    if (!node) return;
    if (!paired) {
      var cfg = root.MOLT_LIVE && root.MOLT_LIVE.resolve ? root.MOLT_LIVE.resolve() : null;
      node.textContent = cfg && cfg.mode === "live" && cfg.token
        ? "第一轮进行中，等待候选轮…"
        : "填入 token 后现场运行…";
      return;
    }
    var drop = baseCost > 0 ? (1 - candCost / baseCost) * 100 : 0;
    var word = Math.abs(drop) < 0.05 ? "持平" :
      (drop > 0 ? "低 " + drop.toFixed(1) + "%" : "高 " + Math.abs(drop).toFixed(1) + "%");
    var pass = passCand === passBase ? "不变" : (passCand > passBase ? "+" : "−") + Math.abs(passCand - passBase);
    liveEmpty(node);
    node.appendChild(doc.createTextNode("通过数 "));
    var passNode = liveSpan(passCand < passBase ? "hl-red" : "", pass);
    node.appendChild(passNode);
    node.appendChild(doc.createTextNode("，第二次总成本 " + word + "（$" + Number(baseCost).toFixed(6) + " → "));
    node.appendChild(liveSpan("hl-good", "$" + Number(candCost).toFixed(6)));
    node.appendChild(doc.createTextNode("）"));
    if (candCost > baseCost) {
      node.appendChild(doc.createTextNode("；通过数优先，成本上升需在结论中注明"));
    }
  }

  function liveTrim(value) {
    return String(value || "").replace(/^\s+|\s+$/g, "");
  }

  function liveTokenValue() {
    return liveTrim(root.MOLT_AGENT_TOKEN || "");
  }

  function liveConnState(text, cls) {
    var node = doc ? doc.getElementById("live-conn-state") : null;
    if (!node) return;
    node.textContent = text;
    node.className = "conn-pill" + (cls ? " " + cls : "");
  }

  function liveShowTokenForm(show) {
    var form = doc ? doc.getElementById("live-conn-form") : null;
    var input = doc ? doc.getElementById("live-token") : null;
    if (!form) return;
    form.hidden = !show;
    if (show && input) {
      try { input.focus(); } catch (err) { /* 忽略 */ }
    }
  }

  function liveValidateToken(base, token, done) {
    var xhr;
    try {
      xhr = new root.XMLHttpRequest();
      xhr.open("GET", String(base).replace(/\/$/, "") + "/api/tasks", true);
      xhr.timeout = 8000;
      xhr.setRequestHeader("Authorization", "Bearer " + token);
      xhr.onreadystatechange = function () {
        if (!xhr || xhr.readyState !== 4) return;
        if (xhr.status === 200) done(null);
        else if (xhr.status === 401 || xhr.status === 403) done({ status: xhr.status });
        else done({ status: xhr.status || 0 });
      };
      xhr.ontimeout = function () { done({ status: 0 }); };
      xhr.onerror = function () { done({ status: 0 }); };
      xhr.send();
    } catch (err) { done({ status: 0 }); }
  }

  function liveTokenSubmit(event) {
    if (event && event.preventDefault) event.preventDefault();
    var input = doc ? doc.getElementById("live-token") : null;
    var button = doc ? doc.getElementById("live-conn-submit") : null;
    var token = input ? liveTrim(input.value) : "";
    if (!token) { liveNote("需要 Bearer token 才能现场运行。", true); return; }
    var cfg = root.MOLT_LIVE && root.MOLT_LIVE.resolve ? root.MOLT_LIVE.resolve() : null;
    var base = cfg && cfg.base ? cfg.base : "";
    if (!base) { liveNote("没有可用 API 地址；请检查 ?port 或当前页面来源。", true); return; }
    if (button) button.disabled = true;
    liveConnState("验证中…", "");
    liveNote("正在验证 token…", false);
    liveValidateToken(base, token, function (err) {
      if (button) button.disabled = false;
      if (err) {
        root.MOLT_AGENT_TOKEN = "";
        liveReset();
        liveConnState("token 错误", "is-bad");
        liveNote("token 验证失败（HTTP " + (err.status || "网络错误") + "）。请重新输入有效的 Bearer token。", true);
        liveShowTokenForm(true);
        if (input) { try { input.focus(); } catch (focusErr) { /* 忽略 */ } }
        return;
      }
      root.MOLT_AGENT_TOKEN = token;
      if (input) input.value = "";
      liveShowTokenForm(false);
      liveConnState("已连接", "is-ok");
      liveNote("token 有效；token 仅保存在本页内存；开始现场 probe replay…", false);
      liveReset();
      liveSourceStop();
      liveSourceStart(true);
    });
  }

  function liveSourceStart(force) {
    var liveApi = root.MOLT_LIVE;
    var cfg = liveApi && liveApi.resolve ? liveApi.resolve() : null;

    var runButton = doc ? doc.getElementById("live-run") : null;
    var hasToken = !!(cfg && cfg.mode === "live" && cfg.token);
    if (runButton) runButton.disabled = !hasToken;
    if (!hasToken) {
      liveRunState("需要 token", true);
      liveConnState("需要 token", "is-bad");
      return;
    }
    if (cfg && cfg.mode === "live") {
      liveConnState("已连接", "is-ok");
      liveSourceMode = cfg.source || "";
      if (cfg.source === "probe2c") {
        liveBadge("REPLAY · probe 2c 真实 Pi 记录", "probe2c");
      } else {
        liveBadge("RUNNING · 现场 probe replay（fixture/replay）", "probe");
      }
      liveApi.start({
        force: !!force,
        /* 离线 / 契约不符都只是静默重试：页面继续空载，不造数字。
           401/403 是配置问题，控制台提醒一次：token / Origin 没配对。 */
        onPayload: function (norm) { liveApplySource(norm); },
        onStatus: function (kind, detail) {
          if (kind === "online") { liveNote(""); liveRunState("已完成", false); liveBadge(liveSourceMode === "probe2c" ? "REPLAY · probe 2c 真实 Pi 记录" : "LIVE · 本次 probe replay（fixture/replay，不是 live 模型）", liveSourceMode); return; }
          if (kind === "starting") { liveNote("正在运行一次 fresh probe replay，完成后显示新数据…", false); liveLog("启动 probe replay"); liveRunState("运行中…", true); return; }
          if (kind === "log") { liveLog(detail); return; }
          if (kind !== "offline") return;
          liveLog("数据源错误：" + detail, true);
          liveRunState("失败", false);
          if (detail === 401) {
            liveNote("实时接口 401：缺 token，用 ?token=<token> 传入（或设置 window.MOLT_AGENT_TOKEN）", true);
          } else if (detail === 403) {
            liveNote("实时接口 403：服务启动时要用 --allow-origin 允许本页 Origin", true);
          } else {
            liveNote("连不上实时接口，保持空载，正在重试…", false);
          }
          if (!liveAuthWarned && (detail === 401 || detail === 403) &&
              root.console && root.console.warn) {
            liveAuthWarned = true;
            root.console.warn(detail === 401
              ? "S6 实时接口 401：用 ?token=<token> 传入 Bearer token（或设置 window.MOLT_AGENT_TOKEN）"
              : "S6 实时接口 403：服务启动时要用 --allow-origin 精确允许页面 Origin");
          }
        }
      });
      return;
    }

    var mock = root.MOLT_MOCK;
    if (mock && mock.enabled && mock.enabled()) {
      mock.start({
        onPayload: function (norm) { liveApplySource(norm); },
        onStatus: function (kind, detail) {
          if (kind === "contract") {
            liveNote("本地数据格式不符：需要 { results: [...] }", true);
          } else if (detail === "file") {
            liveNote("file:// 不能读取本地 JSON；请用 HTTP 服务打开页面", true);
          } else if (kind === "offline") {
            liveNote("读不到 " + (mock.path ? mock.path() : "本地文件") + "，请检查路径", true);
          }
        }
      });
    }
  }

  function liveSourceStop() {
    if (root.MOLT_LIVE && root.MOLT_LIVE.stop) root.MOLT_LIVE.stop();
    if (root.MOLT_MOCK && root.MOLT_MOCK.stop) root.MOLT_MOCK.stop();
    liveNote("");
  }

  /* 收到一份逐题快照：点亮新出现的行、给柱子起长、按已出现的行重算合计。
     快照没覆盖的行一律藏起来 —— 默认空载时它们本来就不该出现。 */
  function liveApplySource(norm) {
    var L = live;
    if (!L || !norm || !norm.pairs) return;

    addClass(L.list, "is-live");

    var i, p, r, touched = 0;
    var got = {};
    for (i = 0; i < norm.pairs.length; i++) {
      p = norm.pairs[i];
      if (p && p.taskId) got[p.taskId] = 1;
    }

    /* 这轮快照没覆盖的行：退回答未完成（否则会显示上一次的旧数字） */
    for (i = 0; i < L.rows.length; i++) {
      r = L.rows[i];
      if (got[r.taskId]) continue;
      if (!r.baseOn && !r.candOn) continue;
      r.baseOn = false;
      r.candOn = false;
      r.gBase = -1;
      r.gCand = -1;
      removeClass(r.el, "is-in");
      liveCandPending(r);
      touched++;
    }

    for (i = 0; i < norm.pairs.length; i++) {
      p = norm.pairs[i];
      if (!p || !p.taskId) continue;
      /* 数据里没见过的题：当场建行（题名就用 id），不再依赖 bundle 的 10 条骨架 */
      r = L.byTask[p.taskId] || liveMakeRow(p);
      if (!r) continue;

      if (isFinite(p.baseCost) && p.baseCost > 0) {
        writeCost(r, "base", p.baseCost, p.basePassed === true);
        r.el.setAttribute("data-base", String(p.baseCost));
        r.el.setAttribute("data-base-ok", p.basePassed === true ? "1" : "0");
        if (!r.baseOn) {
          r.baseOn = true;
          liveGrowBar(r, "base");
          addClass(r.el, "is-in");
          if (L.xTTo < r.idx) liveTweenX(r.idx);
          liveEnsureVisible(r.el);
          liveFlash(r.el);
        }
        touched++;
      }

      if (isFinite(p.candCost) && p.candCost > 0) {
        writeCost(r, "cand", p.candCost, p.candPassed === true);
        r.el.setAttribute("data-cand", String(p.candCost));
        r.el.setAttribute("data-cand-ok", p.candPassed === true ? "1" : "0");
        if (!r.candOn) {
          r.candOn = true;
          liveGrowBar(r, "cand");
          liveEnsureVisible(r.el);
          liveFlash(r.el);
        }
        touched++;
      }
    }

    /* 纵轴跟着实际数据走：新数据成本更高时抬高上限，不拿旧样本的刻度 */
    var maxCost = 0;
    for (i = 0; i < L.rows.length; i++) {
      r = L.rows[i];
      if (r.base !== null && r.base > maxCost) maxCost = r.base;
      if (r.cand !== null && r.cand > maxCost) maxCost = r.cand;
    }
    if (maxCost > 0) L.yMax = niceCeil(maxCost * 1.05);

    if (!touched) return;
    liveNote("");
    liveSum();
    liveAnimStart();
  }

  function writeCost(r, key, v, ok) {
    var costNode = key === "base" ? r.bCost : r.cost;
    var flagNode = key === "base" ? r.bFlag : r.flag;
    var srNode = key === "base" ? r.bSr : r.sr;
    var text = "$" + Number(v).toFixed(6);

    if (key === "base") {
      r.base = Number(v);
      r.basePassed = ok;
    } else {
      r.cand = Number(v);
      r.candPassed = ok;
      r.costText = text;   /* liveCandDone 回填时用的是这份快照 */
    }
    if (costNode) costNode.textContent = text;
    if (flagNode) { flagNode.textContent = ok ? "✅" : "❌"; flagNode.hidden = false; }
    if (srNode) {
      /* 读屏文案跟着判定一起改：否则视觉上是 ❌、读屏还在念「通过」 */
      srNode.textContent = "（" + (key === "base" ? "第一次" : "第二次") +
        (ok ? "通过" : "未通过") + "）";
      srNode.hidden = false;
    }
  }

  /* ---------- 星空背景（默认启用；减少动效时关闭） ----------
   * 按 Diamond Website 的 #startrack background 复刻，三件事一件都不能少：
   *   1) 离屏缓存画满 20000 颗星（坐标范围 ±缓存尺寸，所以大部分在画面外）；
   *   2) 每帧把整张缓存 drawImage 叠到主画布上 —— 旧帧不擦，才会留下轨迹；
   *   3) 每 8 帧用 4% 的擦除把整张画布削一层 → 老轨迹慢慢退掉，新轨迹越来越浓；
   *      再每帧 ctx.rotate(0.025°) —— 轨迹才会弯成星轨，而不是拖成直线。
   * 白纸版与原版的差异只有擦除方式：原版每 8 帧填一层 4% 黑，这里用 destination-out
   * 削 alpha（填黑会把纸底和网格线一起盖掉）。星点颜色同样是「每个点一个随机色相」，
   * 只是明度压到纸面能显示的中低调 —— 原版 120–255 的亮彩色画在白纸上等于看不见。
   * 「朦胧」不在这里做：靠 app.css §19 那张盖在画布上的白纱蒙版（.deck::before）。
   * 默认路径完全不进这个函数：不加类、不建画布、不起 rAF。
   */
  var STAR_COUNT = 20000;
  var STAR_ALPHA_MIN = 0.10;
  var STAR_ALPHA_MAX = 0.30;
  var STAR_SPIN_DEG = 0.025;   /* 每帧旋转角度，与原版一致 */
  var STAR_CACHE_RATIO = 2.6;  /* 离屏缓存边长 = 2.6 × 长边，与原版一致 */

  function starFieldOn() {
    /* 默认就显示；保留这个判定函数是为了给以后留一个统一开关点 */
    return true;
  }

  function initStarField() {
    if (reduce || !starFieldOn()) return;
    var canvas = doc.getElementById("startrack");
    if (!canvas || typeof canvas.getContext !== "function") return;
    var ctx = canvas.getContext("2d");
    if (!ctx || typeof doc.createElement !== "function") return;
    var cache = doc.createElement("canvas");
    var cctx = cache.getContext ? cache.getContext("2d") : null;
    if (!cctx) return;

    doc.documentElement.className += " js-star";

    var cw = 0, ch = 0, longside = 0, raf = 0;
    var prev = 0, elapsed = 0, sinceFade = 0;

    function rand(lo, hi) {
      return lo + Math.random() * (hi - lo);
    }

    function build() {
      cw = canvas.clientWidth;
      ch = canvas.clientHeight;
      if (!cw || !ch) return false;

      canvas.width = cw;
      canvas.height = ch;
      longside = cw > ch ? cw : ch;

      cache.width = Math.round(STAR_CACHE_RATIO * longside);
      cache.height = cache.width;
      cctx.clearRect(0, 0, cache.width, cache.height);

      for (var i = 0; i < STAR_COUNT; i++) {
        var a = rand(STAR_ALPHA_MIN, STAR_ALPHA_MAX);
        /* 每个点一个随机色相 —— 原版是 rgb 三通道各 rand(120,255)，
           白纸上那样会糊成一片白，所以换成同色相随机、明度 34%–62% */
        var hue = Math.round(Math.random() * 360);
        var sat = Math.round(rand(55, 95));
        var lig = Math.round(rand(34, 62));
        cctx.beginPath();
        cctx.arc(rand(-cache.width, cache.width),
          rand(-cache.height, cache.height), 1, 0, 6.2832, true);
        cctx.closePath();
        cctx.fillStyle = "hsla(" + hue + "," + sat + "%," + lig + "%," + a.toFixed(3) + ")";
        cctx.fill();
      }

      /* 画布尺寸变化会重置 ctx 变换，所以这里重新 translate —— 原版是 (cw, 0) */
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
      ctx.lineCap = "round";
      ctx.translate(cw, 0);
      prev = 0;
      elapsed = 0;
      sinceFade = 0;
      return true;
    }

    function loop(now) {
      /* 原版是「每帧转 0.025°、每 8 帧擦一次」，掉帧就会变慢、尾巴变长。
         这里换算成时间：60fps 下与原文逐帧等价，慢机器上观感也不会走样。 */
      var dt = prev ? now - prev : 16.7;
      if (dt > 100) dt = 100;
      prev = now;
      elapsed += dt;
      sinceFade += dt;

      ctx.drawImage(cache, -cache.width / 2, -cache.height / 2);

      if (elapsed > 2500 && sinceFade >= 133) {
        /* 原版填 rgba(0,0,0,.04)；这里削 alpha，纸底和网格线才不会变灰 */
        ctx.globalCompositeOperation = "destination-out";
        ctx.fillStyle = "rgba(0,0,0,0.07)";
        ctx.fillRect(-3 * longside, -3 * longside, 6 * longside, 6 * longside);
        ctx.globalCompositeOperation = "source-over";
        sinceFade = 0;
      }

      ctx.rotate(STAR_SPIN_DEG * (dt / 16.7) * Math.PI / 180);
      raf = root.requestAnimationFrame(loop);
    }

    function restart() {
      if (raf) root.cancelAnimationFrame(raf);
      raf = 0;
      if (build()) raf = root.requestAnimationFrame(loop);
    }

    restart();
    root.addEventListener("resize", restart, false);
    doc.addEventListener("visibilitychange", function () {
      if (doc.hidden) {
        if (raf) root.cancelAnimationFrame(raf);
        raf = 0;
      } else {
        restart();
      }
    }, false);
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
    liveInit();
    liveReset();
    var liveRun = doc.getElementById("live-run");
    if (liveRun) liveRun.addEventListener("click", function () {
      if (!liveTokenValue() && !(root.MOLT_LIVE && root.MOLT_LIVE.resolve && root.MOLT_LIVE.resolve().token)) {
        liveShowTokenForm(true);
        liveNote("请先填入 Bearer token。", true);
        return;
      }
      liveReset();
      liveSourceStop();
      liveSourceStart(true);
    }, false);
    var liveConnToggle = doc.getElementById("live-conn-toggle");
    if (liveConnToggle) liveConnToggle.addEventListener("click", function () {
      var form = doc.getElementById("live-conn-form");
      liveShowTokenForm(!!(form && form.hidden));
    }, false);
    var liveConnForm = doc.getElementById("live-conn-form");
    if (liveConnForm) liveConnForm.addEventListener("submit", liveTokenSubmit, false);
    var liveConnCancel = doc.getElementById("live-conn-cancel");
    if (liveConnCancel) liveConnCancel.addEventListener("click", function () { liveShowTokenForm(false); }, false);
    var liveRetry = doc.getElementById("live-retry");
    if (liveRetry) liveRetry.addEventListener("click", function () {
      if (!liveTokenValue() && !(root.MOLT_LIVE && root.MOLT_LIVE.resolve && root.MOLT_LIVE.resolve().token)) {
        liveShowTokenForm(true);
        liveNote("请先填入 Bearer token。", true);
        return;
      }
      liveReset();
      liveSourceStop();
      liveSourceStart(true);
    }, false);
    var liveDisconnect = doc.getElementById("live-disconnect");
    if (liveDisconnect) liveDisconnect.addEventListener("click", function () {
      root.MOLT_AGENT_TOKEN = "";
      liveSourceStop();
      liveReset();
      liveConnState("已断开", "is-bad");
      liveNote("已断开；再次运行需要重新填入 token。", false);
    }, false);
    var agentEntry = doc.getElementById("agent-entry");
    var handoff = doc.getElementById("handoff");
    var handoffClose = doc.getElementById("handoff-close");
    if (agentEntry && handoff) agentEntry.addEventListener("click", function () {
      handoff.hidden = false;
      var link = handoff.querySelector(".handoff-link");
      if (link) { try { link.focus({ preventScroll: true }); } catch (focusErr) { /* 忽略 */ } }
    }, false);
    if (handoffClose && handoff) handoffClose.addEventListener("click", function () {
      handoff.hidden = true;
      if (agentEntry) { try { agentEntry.focus({ preventScroll: true }); } catch (focusErr) { /* 忽略 */ } }
    }, false);
    var archReplay = doc.getElementById("arch-replay");
    var archSlide = doc.getElementById("s4");
    if (archReplay && archSlide) archReplay.addEventListener("click", function () {
      var animated = archSlide.querySelectorAll(".arch-wire, .arch-node");
      for (var i = 0; i < animated.length; i++) animated[i].style.animation = "none";
      void archSlide.offsetWidth;
      for (var j = 0; j < animated.length; j++) animated[j].style.animation = "";
    }, false);
    startTagline();
    initStarField();
    if (slides.length > 0 && active < 0) {
      var at = startIndex();
      setActive(at >= 0 ? at : 0);
    }

    var deck = doc.getElementById("main");
    if (deck) deck.addEventListener("wheel", onWheel, { passive: false });
    doc.addEventListener("keydown", onKeyDown, false);
    root.addEventListener("hashchange", onHashChange, false);

    /* 画布尺寸跟着窗口走：只重算像素 + 重画，不动数据 */
    root.addEventListener("resize", function () {
      if (!live) return;
      liveFit();
      liveDraw();
    }, false);

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
