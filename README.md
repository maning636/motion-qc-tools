# motion-qc-tools

**AI 动效验收工具** —— 把你 AI 生成的动效 HTML 喂给它，告诉你哪里不行。

出框、运行时炸了、JS 语法错。一条命令跑完，终端人话 + `--json` 机器可读。

```bash
motion-qc check out/
```

```
motion-qc 0.1.0 · 检查 1 个文件

samples\bad\overflow-1px.html  FAIL
  [PASS] JS 语法
      · 1 段内联脚本全部通过 node --check
  [FAIL] 运行时烟测
      · 时长 2.1s · 6 帧形态 · 采样 [2, 4, 4, 4, 4, 4, 4]
  [FAIL] 落定期出框
      × .clip 出右 1px（画布 480×270，t=1.60s）

────────────────────────────────────────────────────
合计 1 个文件：0 通过 / 1 有问题 / 0 警告 / 0 跳过 / 0 工具出错
判定口径：容差 0.50px · 落定期后 76% · 时长 1.2–60.0s · 静止阈值 3 帧
不合格：samples\bad\overflow-1px.html
```

退出码：`0` 全过 · `1` 有不合格项 · `2` 工具自己没跑起来。直接进 CI。

---

## 为什么需要它

AI 生成动效 HTML 的失败模式，人眼恰恰是最难发现的那几种：

- **元素跑到画布外**。DOM 读数完全正常——元素在、尺寸对，只是位置错。缩略联络表里只是「底部有几个方块」，不放大根本看不出是被切掉的。
- **时间轴根本没注册**。批量渲帧照样打勾、照样产出文件，肉眼看才发现那是一张近乎静止的空片。
- **内联脚本解析期就死了**。整个 `<script>` 不执行，于是注册那行压根没跑到，而运行时日志**一条异常都没有**。

这三条都不是「写得糙」，是**看起来全绿**。这个工具专治这一类。

> 动效工作站（<https://maning789link.top>）的测试区用的是同一套品控逻辑。
> 本仓库只放工具本身。

---

## 安装

需要 **Python 3.9+**、**Chrome 或 Edge**（出框/烟测要用）、**Node.js**（语法检查要用）。

```bash
git clone https://github.com/maning636/motion-qc-tools.git
cd motion-qc-tools
pip install -e .
```

不想装也行：

```bash
python motion_qc.py check out/
```

先看看这台机器缺什么：

```bash
motion-qc doctor
```

浏览器路径按「`--chrome` → `$MOTION_QC_CHROME` → 各平台常见安装位置 → `$PATH`」找。
Node 按「`--node` → `$MOTION_QC_NODE` → `$PATH`」找。显式指定却指错了，就如实报 skip，
**不会**悄悄换一个别的 node 顶上去。

**没有 Node 也能跑**：语法检查会退到浏览器控制台日志（能报解析错误，只是覆盖不如完整解析器），
这一项标成 `skip` 而不是 `pass`。

---

## 用法

```bash
motion-qc check a.html b/                  # 文件或目录（目录会递归找 *.html 和 *.json）
motion-qc check out/ --json report.json    # 顺便出机器可读报告
motion-qc check out/ --only overflow       # 只跑一项：smoke / overflow / jscheck / compose
motion-qc check out/ -v                    # 展开每条发现的明细
motion-qc check out/ -q --json -           # 安静模式，JSON 走 stdout（给 Agent / CI）
motion-qc check out/ --jobs 8              # 并行度，默认 4
```

工具本体也可以单独跑，调用习惯跟内部那版一样：

```bash
python tools/pop_overflow.py a.html    # 落定期出框扫描
python tools/pop_smoke.py a.html       # 运行时烟测
python tools/pop_jscheck.py a.html     # JS 语法检查
```

每一项到底在判什么、为什么这么判：

```bash
motion-qc explain overflow
```

---

## 四项检查

| 项 | 判什么 | 需浏览器 | 需 node |
|---|---|---|---|
| `smoke` | 运行时烟测：异常 / 时间轴 / 时长 / 画面是不是几乎不动 | 是 | 否 |
| `overflow` | 落定期出框扫描：动画结束后还有东西停在画布外吗 | 是 | 否 |
| `jscheck` | JS 语法检查：内联 `<script>` 能不能被解析 | 否\* | 是 |
| `compose` | compose/2 编排核对：契约 / 运动包络出框 / 重叠 / 裸底 | 否 | 否 |

\* 没有 node 时退到浏览器控制台日志。

### 几条口径上的硬规矩

**只扫落定期。** 默认扫时长后 24%、4 个采样点。全程扫会把「从画布外飞进来」
「出场飞出画布」这类**设计效果**全报一遍——历史上 64 条报出 23 条，绝大多数是这么来的，
报了等于没报。真正要抓的是动画结束后仍停在框外的东西：预览片的定格帧就在这个窗口里。

**容差 0.5px，且必须小于 1px。** 容差是一个**会骗你的参数**：曾经留 2px，
结果一个正好越界 2px 的片段容器被判成合规——扫描器自己造了一个假阴性，
差点让这个 bug 带着「全族零出框」的结论上线。仓库自带的自测从两侧钉住它：
越界 1px 必须报，越界 0.2px 必须不报。想改用 `--tolerance`，那是在换判据，不是调精度。

**视口按文件声明的尺寸开。** 优先读 `<meta name="viewport" content="width=480, height=270">`。
视口大小会改变百分比 / `vw` 布局——用错尺寸，量到的就不是它本来的样子。
`--viewport WxH` 可以强制。

**根容器自动认。** 依次试 `#stage` → `#root` → `[data-composition-id]` → `main` → `body`。
480×270 的 UI 挂件和 1920×1080 的整版模板都能直接跑，不用告诉工具画布多大。
`--stage SELECTOR` 可以覆盖。

**判定不了就说判定不了。** 没有时间轴、画布尺寸为 0、浏览器没起来——这些一律标成
`error`/`skip`，**不会**当成「通过」。空片子「通过验收」是最容易被甲方当场问住的那种假通过。

### 和朴素实现的已知差异

下面这几条会让报告比一个偷懒的实现**多报**。方向是宁可多报也不漏报，但使用者要知道：

- **根容器回退到 `body`。** 找不到配置里的任何一个根容器时，朴素做法是直接放弃。
  这里回退到 `<body>` 按整页宽高量——那批没有 `#stage` 的文件，朴素做法会**静悄悄地报 0 条**，
  那是一个假阴性。代价是这类文件会报出更多，需要人排除。
- **加载期异常靠浏览器控制台日志补。** 页内 `error` 监听只能抓到监听挂上之后才发生的异常。
  组件脚本在自己执行的第一行就抛的、以及解析期就死掉的，页内一条都听不见。
  这里开 `--enable-logging=stderr`，从控制台日志里捞**属于被检文件**的报错，
  报告里单列 `SMOKE_LOAD_ERROR` / `SMOKE_SYNTAX_ERROR`。
- **去重按元素身份，不按「越界像素串」。** 同一个元素跨采样点报出多条不同越界量时，
  朴素做法会列好几遍，这里合成一条。所以条目更少——不是更松，是更干净。

### 文字出框要留意字体

文字宽度是出框判定里最吃重的一个量，而 `@font-face` 是异步加载的，
**兜底字体和原字体的字宽能差好几个像素**。

本地用 `file://` 打开组件时，字体常常取不到——组件里写的是站点绝对路径
（`/templates/_fonts/...`），在 `file://` 下解析不到。实测同一个标题：字体到位时
正好 480px（贴边合规），退到系统字体时量到 489–495px（判成出框 4px）。

所以这里做两件事：先 `await document.fonts.ready`（带超时）再量；
然后**只在这条结论真的牵扯到文字时才升成警告** `OF_FONT_NOT_LOADED`——
本地开发里字体取不到是常态（98 个组件里 97 个中招），无条件报警等于没报警。
无论触不触发，字体情况都进 `info.fontsNotLoaded`，机器能读到。

看到这条警告，请把相关出框结论当成「**待复核**」，别照着像素数去改布局。

---

## compose/2 的出框口径

`compose` 这一项判定的是**已编排好的图层**，量的是「这一层在 1920×1080 画布上、
含运动轨迹甩出去的最远处」的外框：

- 九宫格锚点表（`tl/tc/tr/cl/cc/cr/bl/bc/br`）
- 基准宽 34% 画布宽，随 `scale` 线性缩放，`scale` 夹在 20–200
- 运动轨迹按 **24 步采样**取包络——路径运动不单调，只看首尾两端会漏掉中间甩得最远的点
- 出框容差 0.5px

这四个常数和 `motion_qc/geometry.py` 里的算式，与站点验收卡是同一套口径。
两边改一处必须同时改另一边——判定不一致的工具没有可信度可言。
自测里有纯 Python 的断言锁住它们，改坏了会红。

`compose` 的第五项「素材可溯源」需要素材池 catalog，留在服务端，CLI 不做——
而不是给它一个「全部命中」的假绿勾。

---

## 自测：验证工具本身没坏

```bash
motion-qc selftest
```

仓库自带「已知好样本」和「已知坏样本」，一条命令把两侧都钉住：

- **几何算式**：锚点 / 34% 基准宽 / 路径包络 / 重叠 / 裸底——不依赖浏览器，跑得飞快
- **好样本必须全绿**（480×270 与 1920×1080 两个尺寸各一份，外加亚像素样本、
  字体取不到样本——后者还要求「字体没加载上」只进 `info`，不报警告）
- **坏样本必须响**：越界 1px、整块出画、宽高为 0、解析失败、加载期异常、
  没注册时间轴、静止片、时长过短、文字出框 + 字体取不到
- **容差两侧**：1px 报、0.2px 静、2px 容差会把 1px 放过（复现那个假阴性）

```
全部 35 条断言符合预期：坏样本响、亚像素放过、好样本静、几何口径一致
```

**一条从不触发的断言等于没有断言，而且比没有更坏——它给假安全感。**
所以每条断言都是从两侧钉的：既要求坏样本响，也要求好样本静。

---

## JSON 报告

```json
{
  "tool": "motion-qc",
  "version": "0.1.0",
  "summary": { "files": 98, "pass": 83, "fail": 15, "warn": 0, "skip": 0, "error": 0, "ok": false },
  "criteria": {
    "tolerancePx": 0.5,
    "settleWindowFrom": 0.76,
    "settleSamples": 4,
    "minDurationS": 1.2,
    "maxDurationS": 60.0,
    "viewport": "auto（按文件声明）"
  },
  "results": [
    {
      "file": "out/card.html",
      "status": "fail",
      "checks": [
        {
          "id": "overflow",
          "title": "落定期出框",
          "status": "fail",
          "findings": [
            {
              "code": "OF_OUT_OF_FRAME",
              "severity": "fail",
              "message": ".clip 出右 1px（画布 480×270，t=1.60s）",
              "detail": { "t": 1.6, "tag": "div", "cls": "clip", "w": 10, "h": 40, "sides": ["right+1"] }
            }
          ]
        }
      ]
    }
  ]
}
```

### 断言码

CI 和 Agent 用这些码做门禁，别去 parse 中文。

| 码 | 含义 |
|---|---|
| `OF_OUT_OF_FRAME` | 元素矩形越过画布边界 |
| `OF_ZERO_SIZE` | 元素宽高为 0（没画出来） |
| `OF_FONT_NOT_LOADED` | 字体没加载上，且有出框结论涉及文字（该结论待复核） |
| `OF_NO_CANVAS` / `OF_NO_TIMELINE` | 出框判定没跑成（不是「通过」） |
| `SMOKE_NO_TIMELINE` | `window.__timelines.main` 没注册 |
| `SMOKE_RUNTIME_ERROR` | 采样期间抛的异常 |
| `SMOKE_LOAD_ERROR` | 加载期就抛的异常（页内监听抓不到，靠浏览器控制台日志） |
| `SMOKE_SYNTAX_ERROR` | 解析期就炸了 |
| `SMOKE_TOO_SHORT` / `SMOKE_TOO_LONG` | 时长越界 |
| `SMOKE_STATIC` | 画面几乎是静止的 |
| `SMOKE_EMPTY_FRAME` | 采样点上整个画面是空的 |
| `JS_SYNTAX` | 内联脚本解析失败 |
| `QC_COMPOSE_CONTRACT` | compose/2 契约不符 |
| `QC_COMPOSE_OUT_OF_FRAME` | 图层运动包络出框 |
| `QC_COMPOSE_OVERLAP` | 时段重叠 |
| `QC_COMPOSE_BARE` | 裸底时长 |

状态：`pass` · `fail` · `warn` · `skip` · `error`。

---

## 脚本只负责报可疑坐标

这个工具是**前置筛子，不是判据**。它能把「出框几处、哪个类名、越了多少像素」一次报全，
但「字腔糊没糊」「这个出框是不是刻意的设计」仍然要人看渲帧判断。
尤其是特效类（confetti、纸屑、划出屏幕的导航条）——**落定后仍然停在框外本来就是它们的常态**，
这类会报出来，需要人排除。

## 不做的事

- **不渲帧、不出图**。判断「字认不认得出、动作说不说得上」需要把帧放大看，
  这是另一类工具（联络表 / 拼图），本仓库不含。
- **不修改被检文件**。所有探测都在 iframe 里的只读加载中完成。
- **不做通用 HTML 校验**。它只管动效合成这条线的事。

## License

MIT，见 [LICENSE](LICENSE)。

---

## English summary

**motion-qc-tools** — an open-source CLI that tells you *what is wrong* with an
AI-generated motion HTML file.

```bash
pip install -e .        # needs Python 3.9+, Chrome/Edge, Node.js
motion-qc check out/    # human-readable report
motion-qc check out/ --json report.json
motion-qc selftest      # verify the tool itself still works
```

Four checks:

- **`smoke`** — runtime smoke test: JS exceptions (both in-page and load-time, caught
  from the browser console log), `window.__timelines.main` presence, duration sanity,
  and "is the picture actually moving or just a still frame".
- **`overflow`** — scans only the **settle window** (last 24% of the timeline by default,
  4 sample points) and reports elements whose rectangle leaves the canvas, or whose
  width/height collapsed to zero. Tolerance is 0.5px and must stay below 1px — a looser
  tolerance once produced a false negative that nearly shipped a real bug.
- **`jscheck`** — extracts every inline `<script>` and runs `node --check` on it.
  A `SyntaxError` kills the whole block, so the timeline registration inside it never
  runs — and the runtime log stays completely clean. Falls back to the browser console
  log when Node is unavailable.
- **`compose`** — validates a compose/2 composition: contract, layer motion-envelope
  out-of-frame, overlapping time spans, and bare (uncovered) duration.

Design decisions worth knowing:

- Only the settle window is scanned. Sweeping the whole timeline reports entrance/exit
  animation as breakage — historically 23 of 64 components flagged that way, which makes
  the report worthless.
- The browser viewport defaults to the canvas each file declares in its
  `<meta name="viewport">`, because viewport size changes percentage/`vw` layout.
- The root element is auto-detected (`#stage`, `#root`, `[data-composition-id]`, `main`,
  `body`), so both 480×270 UI widgets and 1920×1080 full compositions work unchanged.
- When a check could not run (no timeline, zero-sized canvas, no browser), the result is
  `error` or `skip` — never `pass`. A false green is worse than no answer.

The tool reports suspicious coordinates; **a human still has to look at the rendered
frames** to decide whether an out-of-frame element is a bug or an intentional effect
(confetti, paper scatter, swipe-away navigation all sit outside the canvas by design at
rest).

MIT licensed.
