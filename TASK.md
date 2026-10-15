# 任务书：AI 短剧《车内搭讪》本地制作工作流复现

> 你是一个**无人值守的新会话**。严格按本文件执行：不提问、不使用 TodoWrite、不向用户追问。
> 完成全部交付物并启动后台生成后，写好 README.md 立即退出。全程禁止 `taskkill /F /IM python.exe`（本机多项目并发，按 PID 操作）。

## 0. 背景

用本机已跑通的 Wan 2.1 T2V 1.3B 文生视频管线（RTX 4060 Ti 8GB），复现一条 5 镜头车内对话短剧的完整制作工作流：分镜表 → 逐镜 AI 提示词 → 音效/BGM/剪辑方案 → 本地逐镜出片。

### 既有基础设施（必须复用，不要重造）

- 管线根目录（下称 INFRA）：`E:\1workspace\local-video-gen`
- Python：`E:\1workspace\local-video-gen\venv\Scripts\python.exe`（唯一使用的解释器）
- `INFRA\generate.py [提示词]`：**一个进程只出一个镜头**。读环境变量 `NUM_FRAMES`（须 4k+1）、`NUM_STEPS`、`GUIDANCE_SCALE`、`SEED`、`NEGATIVE_PROMPT`；扩散完成后潜变量存 `INFRA\output\latents_f{N}_umt5-xxl-fp8_bf16_g{G}.pt`，同进程尝试解码出 mp4（8GB 卡常 OOM，属预期）。
- `INFRA\decode_video.py`：**独立进程解码**。先通读该文件确认入参/输出命名，再写工厂。诗歌工厂的成熟调用序列见 `INFRA\make_poem.py` 的 `make_shot()`（扩散→独立解码→移动产物，失败等 30s 重试 1 次），照抄该序列。
- 成熟调度件（直接参考 `INFRA\make_poem.py` 复制改造）：`acquire_lock/release_lock/lock_owner_alive`（PID 锁防重复跑）、`gpu_rest/maybe_rest`（忙碌累计 60 分钟，镜头间隙休 10 分钟）、`keep_awake`（Windows 防休眠）、幂等跳过已存在产物。
- ffmpeg：`F:\java\ffmpeg-7.1-essentials_build\bin\ffmpeg.exe` / `ffprobe.exe`；字体 `INFRA\fonts\simkai.ttf`。
- 已验证参数：分辨率 **832×480**、16fps、40 steps、guidance 5.0、bf16、fp8 文本编码器（CPU 编码）、VAE tiling。不要改这些参数（8GB 显存稳定性优先）。
- 模型对英文提示词遵循更好：**generate.py 传入英文提示词**，文档里中英对照。

### 本项目目录

`E:\1workspace\duanju-car\`（当前目录）

```
TASK.md                 # 本文件
01-分镜表.md             # 任务一产物
02-镜头提示词.md          # 任务二产物
03-音效BGM与剪辑方案.md   # 任务三产物
shots.json              # 任务二机器可读版（5 镜：id/时长/帧数/seed/英文prompt/英文negative）
make_shots.py           # 任务四：无人值守生成工厂
run_factory.bat         # 任务四：纯 ASCII 内容的脱离会话启动器
README.md               # 最后写：产物清单/进度状态/续跑方法
clips\shot1..5.mp4       # 工厂产物（视频）
latents\shot1..5.pt      # 工厂产物（潜变量，便于换参重解码）
logs\factory.log         # 工厂日志
```

## 1. 原始素材（逐字依据）

### 角色与场景

- 副驾女主：年轻长发女生，黑色吊带，十字架项链。
- 驾驶位男主：短发男生，黑色上衣。
- 场景：行驶中的轿车，公路，窗外虚化流动风景，电影感低饱和青灰调色，车内柔和自然光。

### 分镜清单原文

- **镜号1｜4.5s｜近景｜固定**｜车内副驾年轻长发女生，黑色吊带，十字架项链，转头看向驾驶位男生，笑容灿烂，眼神期待；男生目视前方开车，侧脸入镜。｜司机在看着前方专心开车。女子微笑对司机说：你有对象吗？司机回答：没有。女子凑近司机满眼期待开心说：那咱俩试试呗！车内光线自然，近景镜头，固定拍摄。
- **镜号2｜5.3s｜近景｜固定**｜后排视角，同时拍到驾驶位男子、副驾女子；男子手握方向盘目视前方，女子抬手轻拍胸口。｜汽车行驶在公路上，后排视角拍摄驾驶位的男子和副驾驶位的女子，男子手握方向盘冷静回答：不试。女子信誓旦旦手拍胸口对司机说：我能陪你吃苦！司机瞥了一眼女子说：没你我用吃苦吗？车窗外风景快速后退，近景镜头，固定运镜。
- **镜号3｜2.8s｜特写｜固定**｜女子面部特写，笑容消失，眼神委屈不甘，看着驾驶位男人。｜车内女子面部特写，情绪略显委屈与不甘，眼神真诚，说：我花不了多少。车内柔和光影，特写镜头，固定拍摄。
- **镜号4｜3.8s｜近景｜固定**｜驾驶位男生正面近景，表情平淡从容，目视前方开车。｜男子正面近景，表情从容淡漠地回应女子"你但凡够花你还来找我呀"。车窗外高架桥景色穿梭，近景镜头，固定拍摄。
- **镜号5｜3.6s｜近景｜固定**｜后排双人近景，女生挥手，情绪激动。｜车内后排视角近景，男子对着挥手表示拒绝说：去去去去去滚！女子看着（**原文此处截断**）

### 台词表

1. 女：你有对象吗？
2. 男：没有。
3. 女：那咱俩试试呗。
4. 男：不试。
5. 女：我能陪你吃苦。
6. 男：没你我用吃苦吗？
7. 女：我花不了多少。
8. 男：你但凡够花你还来找我呀。
9. 女：去去去去去滚！

### 镜5 补全口径（原文有冲突，以此裁定）

镜5 散文把"去去去去去滚"系于男子，但**画面明确是"女生挥手，情绪激动"，且台词表最后一句归属为女**。以画面+台词表为准：女子被连环噎住后恼羞成怒，冲男生方向连挥五下赶人手势，爆怼"去去去去去滚！"；男生目视前方、嘴角微微上扬（忍笑），窗外街景流动。补全文字按此写。

## 2. 任务一：`01-分镜表.md`

标准 Markdown，包含：

1. 片头信息块：片名、总时长、画幅（832×480 / 16:9 母版）、帧率、整体风格基调（低饱和青灰、电影感、车内柔和自然光）、角色圣经（固定中英描述，全片复用）。
2. 分镜表，列：`镜号 | 时长 | 景别 | 机位/视角 | 运镜 | 画面内容（含动作/表情/视线） | 台词 | 备注（光影/窗外景/音效点）`。镜5 用第 1 节裁定口径补全（"女子看着"之后写完整）。
3. 表后附一句节奏说明：镜头时长长短交替（4.5/5.3/2.8/3.8/3.6），镜3 最短特写做情绪急停。

## 3. 任务二：`02-镜头提示词.md` + `shots.json`

### 3.1 文档结构

- 开头"全局设定"小节：**英文角色圣经与场景圣经原文**（每镜 prompt 中原样复用，保证跨镜一致性）、全局英文负面提示词、跨镜一致性策略说明（固定角色描述锚点 + 同族 seed + 无换装/无场景切换）。
- 5 个镜头各一节，每节固定 5 个子项：
  1. **画面描述（中文）**：机位、构图、动作、表情、视线、窗外景；一个镜头只描述一个连续动作，禁止多动作/多机位。
  2. **AI 视频生成提示词（English）**：一整段现在时英文，结构 = 角色圣经（本镜出场者）+ 场景圣经 + 机位焦段 + 连续动作表演 + 光影 + 风格尾注。写实电影感、35mm film、shallow DoF、motion blur on background。不要求口型精确同步（T2V 做不到），只写 subtle natural facial motion；不要求画面出现任何文字。
  3. **光影**：中文写明主光方向/色温/反差/窗外流动高光。
  4. **镜头参数**：摄影建议（等效焦段如 35mm/50mm、光圈感、固定机位）+ 实际生成参数表（832×480、16fps、帧数、steps 40、CFG 5.0、seed）。
  5. **负面提示词**：英文（喂模型）+ 中文注释。针对车内场景补充：no extra people, no rear-seat passenger（镜1/3/4）、no text/subtitles/watermark/logo、no deformed hands/fingers/face、no identity drift、no abrupt cut/scene change、no camera shake/zoom、no cartoon/anime/oversaturated。
- 各镜机位要点：镜1=中控/挡风玻璃侧近景，女转头看男、男侧脸；镜2=后排双人，女拍胸口、男瞥一眼；镜3=女主面部大特写，笑容收起、委屈真诚；镜4=副驾侧拍男主正面近景，淡漠从容，窗外高架桥；镜5=后排双人，女连挥手赶人、娇嗔恼怒，男嘴角微扬。

### 3.2 帧数（必须 4k+1，8GB 稳妥上限 81）

| 镜 | 目标时长 | NUM_FRAMES | 实际时长 |
|----|---------|------------|---------|
| 1 | 4.5s | 73 | 4.56s |
| 2 | 5.3s | 81 | 5.06s（略截短 0.24s，文档注明） |
| 3 | 2.8s | 45 | 2.81s |
| 4 | 3.8s | 61 | 3.81s |
| 5 | 3.6s | 57 | 3.56s |

### 3.3 `shots.json`

UTF-8 JSON 数组，5 个对象：`id`（1..5）、`duration_sec`（实际）、`num_frames`、`seed`（基数 20261015 + id，即 …16..20）、`prompt_en`、`negative_en`、`subtitle_cn`（该镜中文字幕台词，可多行）。工厂只读这个 JSON，不要在脚本里硬编码文案。

## 4. 任务三：`03-音效BGM与剪辑方案.md`

必须包含：

1. **声音设计总览**：持续床声（车内低频引擎轰鸣 + 轮胎路面 + 偶尔对向车 whoosh，低通透视感），人声优先的混音层级，BGM 在台词下 ducking。
2. **逐镜音效表**：镜号 | 入点(s) | 音效 | 设计意图。例如：镜1 气氛轻快"叮"动机音落在"你有对象吗"；镜2 拍胸口闷响与台词重音对齐、"没你我用吃苦吗"后留 0.3s 空隙；镜3 环境声抽空+轻微心跳低频做情绪急停；镜4 高架桥 passing 金属扫声衬冷漠；镜5 五下"去去去去去滚"每挥手一记轻快打击点 + 末尾 record scratch / boing 喜剧收尾。
3. **配音方案**：edge-tts（venv 已装；注意 2026-10 起端点 403，`INFRA\make_poem.py` 的 `patch_edge_tts_endpoint()` 是现成热修）。女声 `zh-CN-XiaoyiNeural` 或 `XiaoxiaoNeural`（活泼年轻，速率 +5%）、男声 `zh-CN-YunxiNeural`/`YunjianNeural`（冷静偏低）。**本方案只设计、本轮不生成配音**；给出 9 句台词的时间轴建议（按每镜实际时长分配，句间留呼吸）。
4. **BGM 方案**：风格（轻喜剧 lo-fi / 弹跳拨弦+轻鼓点，90–105 BPM）、情绪曲线（镜1好奇→镜2自信→镜3掉线半停→镜4毒舌停顿→镜5爆点收尾）、搜索关键词与无版权来源建议（YouTube Audio Library / Pixabay Music / 剪映商用库）、ducking 目标（人声 -6dB 之上时 BGM 压到 -20dB 左右）。
5. **剪辑卡点方案**：
   - 精确时间轴表：每镜 in/out/时长（用实际帧数时长）、切点类型（硬切为主；镜2→镜3 用 J-cut 女声先入 0.2s；镜4→镜5 台词尾叠化）、卡点对应台词字重音、转场音效（whoosh 一律短而轻）。
   - 全片硬切，无转场特效；镜1→2 同机位关系顺切，镜3 特写做全片唯一景别跳跃（节奏重拍）。
   - 字幕样式：底部居中白字描黑边（可复用 ffmpeg drawtext，simkai.ttf 或换黑体），不遮挡面部；竖屏改造方案：16:9 母版→9:16 用中心裁切或高斯模糊填充背景，字幕安全区下移。
   - 母带规格：H.264 yuv420p、AAC、成片响度 -14 LUFS（短视频平台建议 -16 LUFS 可选说明）、总时长约 19.8s。
6. 明确写出：本轮工厂只产出 5 条无声素材 + 1 条无声粗剪，配音/BGM/精剪按本方案人工或下一轮执行。

## 5. 任务四：`make_shots.py` 无人值守工厂 + 启动

### 5.1 工厂规格（在本项目目录，Python 用 INFRA 的 venv）

- 读 `shots.json`；命令行：`make_shots.py [next|1..5] [--dry-run]`，默认 next（第一个缺 clip 的镜头）。
- `make_shot(item)`：严格照抄 INFRA `make_poem.py::make_shot()` 的成熟序列——
  - `clips\shotN.mp4` 已存在则跳过（幂等可随时中断续跑）；
  - subprocess 调 `INFRA\venv\Scripts\python.exe generate.py <prompt_en>`，cwd=INFRA，env 带 `NUM_FRAMES/NUM_STEPS=40/GUIDANCE_SCALE=5.0/SEED/NEGATIVE_PROMPT=<negative_en>`（HF 相关 env generate.py 自己会设）；
  - 再 subprocess 调 `decode_video.py`（先读源码确认 LATENTS 入参与 mp4 输出名，照搬诗歌工厂写法）；
  - 把产物 mp4 移到 `clips\shotN.mp4`、latent 移到 `latents\shotN.pt`（移动前清理 INFRA\output 里同轮的 wan_*.mp4 中间文件，仅限本镜时间窗口内生成的，按文件名时间戳判断，禁止误删其他文件）；
  - 失败等 30s 整体重试 1 次，再失败记 ERROR 并继续下一镜（不要中断整个工厂）。
- 调度：复制 `lock_owner_alive/acquire_lock/release_lock`（锁文件 `logs\.runner.lock`）、`keep_awake`、`gpu_rest/maybe_rest`（`REST_EVERY_MIN=60`、`REST_MIN=10`，检查点在镜头间隙）。
- 全部 5 镜完成后（best-effort，每步 try/except，失败只告警不影响已完成素材）：
  1. ffmpeg concat 重编码拼接 `rough_cut.mp4`（832×480@16，统一编码参数，不做转场）；
  2. 每镜抽中帧横排拼 `质检五连.jpg`；
  3. 日志打印 `=== ALL SHOTS DONE ===`。
- 日志带时间戳，UTF-8 输出。脚本里所有路径用 pathlib/raw string 处理反斜杠。
- **先 `--dry-run` 自检一遍**（打印将执行的命令序列与休息策略），确认无误再启动真实运行。

### 5.2 `run_factory.bat`（内容必须纯 ASCII，中文只允许出现在它调用的文件名里）

参照 INFRA 的 `run_next_hidden.bat`：chcp 65001、cd 到本项目、`set PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`、`REST_EVERY_MIN=60`、`REST_MIN=10`，运行 `E:\1workspace\local-video-gen\venv\Scripts\python.exe make_shots.py >> logs\factory.log 2>&1`，首尾写 started/finished 时间戳行。

### 5.3 脱离会话启动（关键）

文档与脚本就绪、dry-run 通过后，用 PowerShell 以隐藏窗口启动，使其脱离你的会话独立存活，然后**立即**做一次性启动核验（查 logs\factory.log 是否出现首行、python 进程是否在），**不要等待生成完成、不要轮询显卡、不要追踪进度**：

```powershell
powershell -NoProfile -Command "Start-Process -FilePath cmd.exe -ArgumentList '/c','run_factory.bat' -WorkingDirectory 'E:\1workspace\duanju-car' -WindowStyle Hidden"
```

预计每镜 6–13 分钟（CPU 文本编码 + bf16 扩散 + 独立解码），5 镜加休息约 45–80 分钟。

## 6. 收尾：`README.md`

写清：三份文档与 shots.json 的用途、工厂运行机制（单镜头单进程防显存溢出 / 60 分钟休 10 分钟 / 幂等续跑 / 锁）、产物路径、启动与续跑命令（重跑 run_factory.bat 即可自动跳过已完成镜头）、当前状态（文档完成、生成已于 HH:MM 后台启动）、预计耗时。写完立即退出。

## 7. 验收清单（自检，全部满足才退出）

- [ ] 三份 Markdown 齐全；分镜表镜5 已按裁定补全；5 镜提示词均含五个子项且英文 prompt 与负面词完整、帧数表正确。
- [ ] shots.json 合法 UTF-8 JSON，帧数 73/81/45/61/57，seed 20261016..20261020，无中文 prompt 混入生成字段。
- [ ] make_shots.py dry-run 通过；run_factory.bat 纯 ASCII。
- [ ] 工厂已隐藏窗口脱离启动，logs\factory.log 有输出；未出现第二个并发 python 生成进程。
- [ ] README.md 已写，状态如实。
