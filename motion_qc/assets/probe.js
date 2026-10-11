/* motion-qc 运行期探针
   ------------------------------------------------------------------
   这段代码被注入到一个临时宿主页里，运行在无头 Chrome 中，结果通过
   document.title 传回 Python 侧。

   它同时回答两个问题——这也是它必须跑在浏览器里的原因：
     1. 组件跑起来有没有炸（pop_smoke 的判据）
     2. 动画**落定**之后有没有东西停在画布外（pop_overflow 的判据）

   两个问题共享同一次页面加载：打开 64 个组件要跑 64 次 Chrome，
   拆成两个探针就是 128 次，而出框判定必须建立在同一根时间轴上，
   两者本来就在同一次加载里。合并是提速，不是改口径。

   只读：探针只读被检文件的 DOM，从不写回。 */

(async () => {
  const SRC = __SRC__;
  const CFG = __CFG__;

  const out = {
    stageSelector: null,
    canvas: null,
    duration: null,
    frames: 0,
    visible: [],
    errors: [],
    overflow: [],
    zeroSize: [],
    notes: [],
  };
  const note = (m) => { if (out.notes.length < 12) out.notes.push(m); };
  const pushErr = (m) => { if (out.errors.length < 20) out.errors.push(m); };
  const pushOver = (m) => { if (out.overflow.length < 40) out.overflow.push(m); };
  const pushZero = (m) => { if (out.zeroSize.length < 10) out.zeroSize.push(m); };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const txt = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();
  /* SVG 元素的 className 是个 SVGAnimatedString 对象，String() 出来是
     "[object SVGAnimatedString]"——一屏这种噪音没人受得了。 */
  const clsOf = (el) =>
    (el.getAttribute && el.getAttribute('class')) || (el.className && el.className.baseVal) ||
    (typeof el.className === 'string' ? el.className : '') || '';

  /* ── 1. 装页面 ─────────────────────────────────────────────── */
  const frame = document.createElement('iframe');
  frame.style.cssText =
    'width:' + CFG.viewport.width + 'px;height:' + CFG.viewport.height +
    'px;border:0;position:absolute;left:0;top:0';
  document.body.appendChild(frame);
  const loaded = new Promise((res) => {
    frame.addEventListener('load', res, { once: true });
    frame.addEventListener('error', res, { once: true });
    setTimeout(res, CFG.loadTimeoutMs);
  });
  frame.src = SRC;
  await loaded;

  const win = frame.contentWindow;
  const doc = frame.contentDocument;
  if (!win || !doc) {
    pushErr('无法取得 iframe 内容（文件打不开，或不是 HTML）');
    return out;
  }

  /* 字体必须等完再量，而且没加载上这件事必须报出去。
     文字的宽度是出框判定里最吃重的一个量，而 @font-face 是异步加载的：
     兜底字体的字宽和原字体能差好几个像素——实测有一个组件，字体到位时标题
     正好 480px（贴边合规），字体加载失败退到系统字体时量到 489–495px（判成出框 4px）。
     这不是判据的问题，是**量到的不是同一个东西**。所以两件事都要做：
       等 document.fonts.ready（跨域字体可能永远不 resolve，套超时兜底）
       把没加载上的字体列出来，让看报告的人知道文字类结论要打折
     别把这段挪到 doc 取到之前——try/catch 会把那个 ReferenceError 一起吞掉，
     字体检查就静悄悄地一个都不报了。 */
  try {
    if (doc.fonts && doc.fonts.ready) {
      await Promise.race([doc.fonts.ready, sleep(CFG.fontTimeoutMs)]);
    }
  } catch (e) { /* 量不到字体就按现状量，不因此放弃整次探测 */ }

  out.fonts = [];
  try {
    if (doc.fonts) {
      /* FontFaceSet 是 Set 形态的：没有 length、没有下标，只有迭代器和 forEach。
         用 Array.prototype.forEach.call 去遍历它会静悄悄地一个都不跑。 */
      for (const face of doc.fonts) {
        if (face.status !== 'loaded') {
          out.fonts.push({ family: face.family || '(未命名)', status: face.status, weight: face.weight });
        }
      }
    }
  } catch (e) { /* 拿不到就当没有 */ }

  await sleep(CFG.settleMs);

  /* 必须挂 iframe 内部的监听：组件是独立文档，异常报在它自己的 window 上，
     挂父页面听不见。这是原始工具踩过的坑，别去掉。 */
  win.addEventListener('error', (e) => pushErr('运行时异常: ' + (e.message || e.type)));

  /* ── 2. 找根容器，并让视口至少装得下它 ───────────────────────── */
  let stage = null;
  for (const sel of CFG.stageSelectors) {
    try { stage = doc.querySelector(sel); } catch (e) { stage = null; }
    if (stage) { out.stageSelector = sel; break; }
  }
  if (!stage) {
    stage = doc.body;
    out.stageSelector = 'body';
    note('没匹配到配置的根容器，已回退到 <body>——出框判定会按整页宽高算');
  }
  /* 只在根容器比视口还大时放大，并且要**看到收敛**才敢继续。
     这里曾经写错过：每次按「根容器 +8px」放大，而根容器若是百分比宽度，
     放大视口会把它一起放大 —— 1920 → 1928 → 1936 → 1944，一路 runaway，
     画布被撑出一个根本不存在的尺寸，一堆元素因此被判成出框。
     判据是「放大了它也没变大」= 固定尺寸，收敛。
     另外只放大不缩小：根容器若是百分比宽度，缩小视口会把它一起缩小，
     量到的就不是它本来该有的尺寸。 */
  let prevW = 0, prevH = 0;
  for (let i = 0; i < 4; i++) {
    const r = stage.getBoundingClientRect();
    if (!(r.width > 1 && r.height > 1)) break;
    if (Math.abs(r.width - prevW) < 0.5 && Math.abs(r.height - prevH) < 0.5) break;
    prevW = r.width; prevH = r.height;
    const fw = parseFloat(frame.style.width), fh = parseFloat(frame.style.height);
    if (r.width > fw || r.height > fh) {
      frame.style.width = Math.ceil(r.width) + 'px';
      frame.style.height = Math.ceil(r.height) + 'px';
      await sleep(120);
    } else {
      break;
    }
  }
  {
    const r = stage.getBoundingClientRect();
    out.canvas = { w: Math.round(r.width), h: Math.round(r.height) };
  }
  if (!(out.canvas.w > 1 && out.canvas.h > 1)) {
    pushErr('根容器尺寸为 ' + out.canvas.w + '×' + out.canvas.h + '，没有可判定的画布');
    return out;
  }

  /* ── 3. 时间轴 ─────────────────────────────────────────────── */
  const tl = win.__timelines && win.__timelines.main;
  if (!tl || typeof tl.time !== 'function') {
    pushErr('没有 window.__timelines.main（时间轴没注册）');
    return out;
  }
  let duration = 0;
  try { duration = Number(tl.duration()); } catch (e) { duration = 0; }
  if (!isFinite(duration)) {
    pushErr('duration() 返回了非有限值');
    return out;
  }
  out.duration = +duration.toFixed(3);
  if (out.duration <= CFG.minDuration) pushErr('时长过短 ' + out.duration + 's（动效没铺开）');
  if (out.duration >= CFG.maxDuration) pushErr('时长过长 ' + out.duration + 's（观众要等）');

  const seek = (t) => { try { tl.time(t, false); } catch (e) { pushErr('拨时间轴失败: ' + e.message); } };

  /* ── 4. 烟测：可见元素采样 ─────────────────────────────────── */
  const N = CFG.visibleSamples;
  for (let i = 0; i < N; i++) {
    seek(out.duration * (i + 0.5) / N);
    let n = 0;
    for (const el of stage.querySelectorAll('*')) {
      const cs = win.getComputedStyle(el);
      if (parseFloat(cs.opacity) > 0.05) {
        const r = el.getBoundingClientRect();
        if (r.width > 1 && r.height > 1) n++;
      }
    }
    out.visible.push(n);
  }

  /* ── 5. 烟测：是不是几乎静止 ───────────────────────────────── */
  /* 只看「屏幕上可见几个元素」区分不了动与不动——一个元素自己转也算 1。
     所以再采一轮画面指纹：内联样式变了才算真的在动。 */
  const sig = new Set();
  for (let i = 0; i < CFG.frameSamples; i++) {
    seek(out.duration * (i + 0.5) / CFG.frameSamples);
    sig.add(
      stage.innerHTML.length + '|' +
      Array.prototype.map.call(stage.children, (c) => c.style.cssText).join('').length
    );
  }
  out.frames = sig.size;
  if (out.frames <= CFG.minFrames) {
    pushErr('画面几乎是静止的（' + out.frames + ' 种状态 / ' + CFG.frameSamples + ' 次采样）');
  }

  /* ── 6. 出框：只扫落定期 ───────────────────────────────────── */
  /* 全程扫会把「从画布外飞进来」「飞出画布」这类**设计效果**全报一遍。
     真正要抓的是动画结束后仍然停在框外的东西——预览片的定格帧就在这个
     窗口里，观众第一眼看到的就是它。 */
  const TOL = CFG.tolerance;
  const seen = new Set();
  for (let i = 0; i < CFG.settleSamples; i++) {
    const t = out.duration * (CFG.settleFrom + (1 - CFG.settleFrom) * i / (CFG.settleSamples - 1));
    seek(t);
    const sr = stage.getBoundingClientRect();
    for (const el of stage.querySelectorAll('*')) {
      const cs = win.getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none') continue;
      if (parseFloat(cs.opacity) < CFG.minOpacity) continue;
      const r = el.getBoundingClientRect();
      /* 纯定位容器（有子元素、自己没背景/边框/背景图）不报：
         它出框通常是被它某个孩子出框带着的，报孩子信息更全。 */
      const hasInk = el.children.length === 0 ||
        cs.backgroundColor !== 'rgba(0, 0, 0, 0)' ||
        cs.borderTopWidth !== '0px' || cs.backgroundImage !== 'none';
      if (!hasInk) continue;
      if (r.width < 1 || r.height < 1) {
        /* B 类：元素宽高为 0，等于没画出来。有文字的才报，空的占位不算。 */
        if (el.children.length === 0 && txt(el)) {
          pushZero({ t: +t.toFixed(2), tag: el.tagName.toLowerCase(), cls: clsOf(el).slice(0, 40), text: txt(el).slice(0, 24) });
        }
        continue;
      }
      const over = {
        left: sr.left - r.left,
        top: sr.top - r.top,
        right: r.right - sr.right,
        bottom: r.bottom - sr.bottom,
      };
      const sides = ['left', 'top', 'right', 'bottom'].filter((k) => over[k] > TOL);
      if (!sides.length) continue;
      const rec = {
        t: +t.toFixed(2),
        tag: el.tagName.toLowerCase(),
        cls: clsOf(el).slice(0, 40),
        text: txt(el).slice(0, 24),
        w: Math.round(r.width),
        h: Math.round(r.height),
        sides: sides.map((k) => k + '+' + Math.round(over[k])),
      };
      /* 同一元素跨采样点只报一次：定格窗口有 4 次采样，同一个 bug 报 4 遍
         只会把真正不同的那条淹掉。 */
      const key = rec.tag + '|' + rec.cls + '|' + sides.join(',');
      if (seen.has(key)) continue;
      seen.add(key);
      pushOver(rec);
    }
  }
  if (seen.size === 0 && out.overflow.length === 0) note('落定期没有出框元素');
  return out;
})()
