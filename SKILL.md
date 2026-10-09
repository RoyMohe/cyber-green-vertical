---
name: cyber-green-vertical
description: 把一段口播文案做成 9:16 竖版科技解说视频——深色纵深空间背景（透视网格隧道、景深模糊暗角、光斑粒子）、荧光科技绿 HUD、程序化动态图形（旋转地球、雷达扫描、混沌吸引子、湍流场、数据包流、电路走线、3D 分层板、实时图表）、底部大字幕，输出 1080x1920 MP4。用户要做"科技口播竖屏视频""差评风/赛博绿解说动画""动态图形/MG 风格解说""把这段稿子做成抖音/视频号/小红书竖版视频"时使用。
---

# 赛博绿 · 竖版口播视频（动态图形版）

口播稿 → 分镜 JSON → 逐帧渲染 → 1080×1920 / 30fps MP4（可合入配音与 BGM）。

**核心原则：图形讲故事，文字只做标签；一屏只有一个重点。** 每个场景由一个主图形撑起来，文字只是贴在图形上的关键词。不要做成大字报，也不要满屏乱动——观众的注意力要留给重点和口播的人。

## 克制与重点（默认 `"style": "calm"`）

- **默认克制模式**：背景只保留缓慢、变暗的透视网格和景深暗角；去掉粒子、光斑、扫描线、滚动数据条、故障闪烁、切场闪光、两侧导轨和 REC/时间码读数；HUD 环变成静态细环 + 一段缓慢弧线；波纹只在元素出现时扩散一次；数据包每条线只有一颗、慢速；镜头摇晃幅度很小；颗粒默认 3。
- 想要早期那种满屏动效的版本，顶层写 `"style": "rich"`。
- **重点机制**：每个场景有一个「重点元素」(key)，其余是「次要元素」(dim)。重点出现时会轻弹一次，次要元素同时降到约 40% 亮度，视线自然落到重点上。各 kind 已内置：`bill` 的合计、`tiers` 中 `hot:true` 的那根柱、`delta` 的涨幅数字（旧价卡变暗）、`vs` 的下方卡（上方变暗）、`overlay` 的警示条。用 `focus_d`（秒）可手动指定聚焦时刻。产品实拍图不会被压暗。
- 字幕里 `[关键词]` 会放大 12%，变绿并带荧光笔底纹——**每句最多 1 处，只标数字和结论词**。
- 动的东西只给「正在讲的那个点」：入场动画干脆利落，入场后画面基本静止，直到下一个点。

## 文件

- `assets/template.html` — 全部视觉：空间背景、HUD、场景组件、canvas 动态图形库（时间驱动，逐帧确定）
- `scripts/asr.py` — 离线中文语音识别（SenseVoice ONNX），输出逐字时间戳与 SRT
- `scripts/render.py` — 渲染器（Playwright 截帧 → ffmpeg；多进程并行；自动合音频；胶片颗粒）
- `references/style.md` — 设计规范：色板、空间层次、版面安全区、动效语法、图形选择
- `examples/demo.json` — 完整示例（12 个场景覆盖全部 kind），先跑它确认环境

## 工作流程

1. **拿到稿子**：口播文案（可能还有配音、频道名、配图）。没有配音也能出片，按语速估算时长。
2. **拆分镜**：先读 `references/style.md`。切成 8–15 个场景，每场景 2–4 句字幕。**先想"这句话画成什么图形"**，再挑 kind 和 `viz`。
3. **写 JSON**：照 `examples/demo.json`。字幕里 `[关键词]` 变绿（每句最多 1 处）。
4. **出预览帧**：`python3 scripts/render.py spec.json --stills stills/`，看 `stills/contact.png`（**必须用 Read 看**）。检查：图形是否被遮挡、文字是否溢出、是否压到字幕区、画面是否"太空"或"全是字"。改 JSON 再出。
5. **渲染**：`python3 scripts/render.py spec.json -o video.mp4 [--audio voice.mp3] [--bgm bgm.mp3] [--srt voice.srt]`
   - 有 `--audio` 时各句时长按字数比例铺满音频；写了 `dur` 的句子保持不变。`--srt` 用现成时间轴。
   - 默认按 CPU 核数并行（`--workers N`）。8 核约 1 分钟成片 ≈ 3–4 分钟；2 核约 12 分钟。先用 `--fps 15` 试看。
   - 若渲染环境有超时限制，用 `nohup ... &` 后台跑并轮询日志。
6. 交付 mp4 + spec.json（方便改稿重渲）。

## 模式二：口播素材剪辑（真人出镜 + 动画）

用户给了口播视频（竖版真人讲话）时，用它做底，在上面叠动画：

1. **转写**：`python3 scripts/asr.py talk.mp4 -m <sense-voice模型目录> -o talk.srt --json talk.json`
   - 模型从 GitHub 下载（HF 常被墙）：`https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2`，只需 `onnxruntime` + `numpy`。
   - 识别会把专有名词听错（ChatGPT→"chadGB"、Claude→"cloud"），看 `talk.json` 的逐字时间戳，**人工校对**后按语义重切成 ≤16 字的句子，写一个修正版 SRT（时间取每句首字时间）。
2. **画中画**：整段口播画面按固定比例缩小，再用圆形/圆角矩形遮罩裁出来——位置和缩放在每个镜头内恒定（不抖），并强制铺满窗口（不露黑边）。只需在 `cam.face` 里填一次脸的大致中心 `x/y`（抽一帧加网格看），让遮罩对准脸。不要开人脸追踪（`"track": true` 会让小窗跟着脸漂移）。
   - **产品图**：`delta` 的 `product.image` 放实拍图即可，白底/灰底/渐变底会自动抠成透明；底部被裁切的图加 `"fade": true`。
3. **写 spec**：顶层加 `"cam": {"src": "talk.mp4", "face": {...}}`，每个场景加 `cam` 版式：
   - `full` 全屏口播（上下自动压暗）——配 `overlay` 场景：顶部标题 + 警示条（侧标签、人脸锁定框 `lock` 默认不出，需要时再写）。用于开场钩子、情绪句、短过渡句。
   - `circle` 右上小圆窗 / `rect` 右上竖框 —— 主画面给图形场景，左上用 `head`/`head_en` 写场景小标题。
   - `half` 上半大框，下方留给少量图形。
   - 版式切换时镜头框自动平滑变形（0.6s）。全片在 full 与小窗之间交替，避免一直小窗。
4. 每个元素的入场 `d`（秒，相对场景开始）对齐到口播里说到那个词的时刻——这是"跟嘴动画"的关键。
5. 渲染：`python3 scripts/render.py edit.json --srt talk_fixed.srt -o out.mp4`（自动用素材原声；首次会抽帧缓存到 `.cam_frames_*`；素材会被轻度调色以融入绿色画面，`"grade": false` 关闭）。

口播专用 kind：`overlay`（全屏口播上的包装）、`bill`（账单/价格清单+合计滚动）、`tiers`（档位柱状对比）、`delta`（旧价→新价+涨幅）。

## 场景类型 kind

| kind | 画面 | 关键字段 |
|---|---|---|
| `hero` | HUD 环 + 中心动态图形 + 标题 + 两端数据流 | `title`, `en`, `viz`, `left{title,en}`, `right{...}` |
| `duo`(=`bubbles`) | 两个圆形"镜头"各放一个动态图形，旁边贴词和气泡，镜头间数据包连线 | `items:[{big, bubble, en, viz}]` |
| `layers` | 3D 堆叠透明板缓慢旋转，板上有光点运动，右侧引线标签 | `items:[{title,en}]`(自下而上 2–4), `focus` |
| `flow` | 节点链：编号节点 + 步骤条，数据包沿链流动，节点脉冲 | `steps:[{title, sub}]`(2–4), `note` |
| `hub`(=`tags`) | 中心芯片 + 6 个标签，电路走线逐条点亮、数据包往返 | `center{title,en,icon}`, `tags:[{t,en}]`(≤6) |
| `vs` | 两张标签卡，信号从两端射向中心环并碰撞出波纹，中心标签故障闪烁 | `top{title,en,icon,label}`, `bottom{...}`, `center` |
| `number` | 数字滚动计数 + 背后指数曲线生长 + 管线框之间数据流 | `value`, `caption`, `pipeline`, `k`(曲线陡度) |
| `card` | 浮动纸质卡片，折线自绘、游标来回读数、扫描线、高亮区锁定框 | `chart{values,labels,min,max,mean,highlight,hlabel}` 或 `image`, `title`, `badge` |
| `quote` | 头像 + HUD 环 + 声波条 + 逐字打出引语 + 锁定框 | `name`, `role`, `text`, `image`, `tag` |
| `compare2` | 上下两个窗口各放一个动态图形 + 扫描线 | `left{title,big,en,viz}`, `right{...}` |
| `list` | 窗口 + 雷达镜头 + 扫描线 + 滚动数据条 | `title`, `items`, `viz` |
| `overlay` | 叠在全屏口播上：标题、警示条、侧标签、人脸锁定框、可选右上 viz | `title`, `en`, `warn`, `wd`, `tags`, `lock`, `viz` |
| `bill` | 账单窗口逐行出现，合计数字滚动，盖章 | `title`, `items:[{name,en,price,mark,d}]`, `total{label,value,d}`, `stamp`, `stamp_d` |
| `tiers` | 柱状档位逐根长出，数值滚动，倍数章 | `items:[{label,en,value,text,d,hot}]`, `badge`, `note` |
| `delta` | 产品标签 + 旧价卡(划掉)→新价卡 + 涨幅大数字 | `product{name,en,icon}`, `from{label,price,d}`, `to{...}`, `diff{text,d,caption}` |
| `banner` | 收尾金句：HUD 环 + 波纹 + 绿条字（偶发故障抖动） | `text`, `per`(每行字数), `icon`, `en` |

### 动态图形 `viz`

`globe` 旋转线框地球+飞线 · `radar` 雷达扫描+回波 · `lorenz` 洛伦兹吸引子（混沌/蝴蝶效应/不可预测）· `flow` 湍流流线场（流体/空气/数据洪流）· `wave` 多层示波器波形（信号/声音/量子叠加）· `bars` 声波柱 · `matrix` 逐格枚举的二进制矩阵（穷举/计算/传统算力）· `curve` 指数曲线（增长/误差放大）· `rings` 纯 HUD 环。

`icon`：`mini` `phone` `card` `camera` `chip` `radar` `doc` `bolt` `globe` `user` `lock` `cloud` `x` `check` `!`。

每个场景都可加：`cam`（口播版式）、`head`/`head_en`（左上小标题）、`scale`/`ctop`/`cheight`（内容区缩放与位置，内容放不下时用）。`vs` 可用 `bottom.d` / `center_d` 控制出现时刻。

## 通用字段

```json
{
  "brand": "频道名",
  "topic": "EP.12 · 天气预报",
  "cps": 4.6,
  "scenes": [ { "kind": "...", "lines": ["字幕1", {"text":"字幕2","dur":2.4}] } ]
}
```

`image` 路径相对 spec.json 所在目录。

## 写分镜的要点

- **每个场景必须有一个主图形和一个重点**（价格、数字、结论）。只剩文字的场景（banner）全片最多 1 次，放结尾。
- 全屏口播时画面要干净：一行标题 + 一个警示条就够，不加锁定框和侧标签，除非真的需要。
- 画面上的中文大字 ≤ 6 字；完整句子只出现在字幕里，不要放进画面。
- 字幕一句 ≤ 16 字。每 3–6 秒换场景，同一 kind 不连续出现。
- 开头 3 秒用 `hero` / `duo` / `number` 抓眼球。
- `viz` 要和内容语义对应（讲混沌用 lorenz，讲全球用 globe，讲探测用 radar），不要随手乱配。
- 用户的品牌、截图、人像有就用；没人像时 `quote` 画首字母头像，不要去网上找真人照片。
