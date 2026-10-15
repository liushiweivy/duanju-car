# AI 短剧《车内搭讪》本地制作工作流

5 镜头车内对话轻喜剧短剧（总时长约 19.8s），复用本机 Wan 2.1 T2V 1.3B 管线（RTX 4060 Ti 8GB）：
分镜 → 逐镜英文提示词 → 音效/BGM/剪辑方案 → 本地无人值守逐镜出片。

## 文档与数据

| 文件 | 用途 |
|------|------|
| `TASK.md` | 任务书（执行依据） |
| `01-分镜表.md` | 片头信息、角色圣经（中英固定锚点）、5 镜分镜表（镜5 已按裁定口径补全）、节奏说明 |
| `02-镜头提示词.md` | 全局角色/场景英文圣经、全局负面词、跨镜一致性策略、帧数表，5 镜各含：中文画面描述 / English prompt / 光影 / 镜头与生成参数 / 英文负面词 |
| `03-音效BGM与剪辑方案.md` | 床声与混音层级、逐镜音效点、edge-tts 配音方案与 9 句时间轴、BGM 情绪曲线与无版权来源、精确剪辑时间轴/字幕/竖屏改造/母带规格（本轮只设计不执行） |
| `shots.json` | 工厂唯一数据源（UTF-8）：id / duration_sec / num_frames / seed(20261016–20) / prompt_en / negative_en / subtitle_cn，脚本不硬编码文案 |
| `make_shots.py` | 无人值守生成工厂 |
| `run_factory.bat` | 脱离会话启动器（纯 ASCII） |

## 工厂运行机制（严格复刻 INFRA 诗歌工厂成熟序列）

- **单镜头单进程**：每镜依次独立跑 `generate.py`（CPU fp8 文本编码 → bf16 40 步扩散，潜变量落盘）→ 再开独立进程 `decode_video.py`（VAE fp32 tiling 解码），规避 8GB 卡同进程解码 OOM。
- **固定参数**：832×480、16fps、40 steps、CFG 5.0、bf16、NUM_FRAMES 取 4k+1（73/81/45/61/57，镜2 81 为稳妥上限，实际 5.06s 较目标截短 0.24s）。
- **失败处理**：单镜整体失败等 30s 重试 1 次；再失败记 ERROR 跳过该镜、继续后面镜头，不中断整厂。
- **幂等续跑**：`clips\shotN.mp4` 已存在即跳过；扩散潜变量同步归档到 `latents\shotN.pt`，日后可换 VAE 参数重解码而不必重新扩散。
- **PID 锁**：`logs\.runner.lock` 记录持锁进程并做存活校验（STILL_ACTIVE），防止第二个工厂并发。
- **显卡休息**：累计忙碌 60 分钟（`REST_EVERY_MIN=60`），在镜头间隙休 10 分钟（`REST_MIN=10`）。
- **防休眠 / 防误删**：Windows SetThreadExecutionState 防睡眠；只清理本镜时间窗口内新生成的 `INFRA\output\wan_*.mp4` 进程内解码中间文件（文件名时间戳双重判定），不碰其他任何文件。
- **收尾（5 镜齐备后 best-effort，失败只告警）**：ffmpeg concat 重编码统一 832×480@16 输出无声 `rough_cut.mp4`（无转场无字幕）；各镜抽中帧横排拼 `质检五连.jpg`；日志打印 `=== ALL SHOTS DONE ===`。

## 产物路径

```
clips\shot1..5.mp4        # 5 条无声成片素材
latents\shot1..5.pt       # 对应扩散潜变量
rough_cut.mp4             # 无声硬切粗剪（约 19.8s，全部镜头完成后生成）
质检五连.jpg              # 5 镜中帧横排质检图
logs\factory.log          # 带时间戳的 UTF-8 全量日志
logs\.runner.lock         # 运行中 PID 锁（正常退出自动删除）
```

## 启动与续跑

```powershell
# 脱离会话隐藏窗口启动（cmd 找不到相对路径的 bat，务必用绝对路径）：
powershell -NoProfile -Command "Start-Process -FilePath cmd.exe -ArgumentList '/c','E:\1workspace\duanju-car\run_factory.bat' -WorkingDirectory 'E:\1workspace\duanju-car' -WindowStyle Hidden"

# 自检（只打印命令序列与休息策略，不启动生成）：
& "E:\1workspace\local-video-gen\venv\Scripts\python.exe" make_shots.py next --dry-run

# 指定单镜（仅补做该镜；clip 已存在会跳过）：
& "E:\1workspace\local-video-gen\venv\Scripts\python.exe" make_shots.py 3
```

中断后**重跑 `run_factory.bat` 即可自动续跑**（已完成镜头跳过）；删 `rough_cut.mp4` / `质检五连.jpg` 可在下次 5 镜齐备时重建。
查看日志请用 UTF-8 打开（PowerShell：`Get-Content logs\factory.log -Encoding UTF8`）。

## 当前状态（2026-10-16 00:27 收官）

- 三份 Markdown 与 `shots.json`：完成；`make_shots.py` dry-run/实跑通过；`run_factory.bat` 纯 ASCII。
- **5 镜无声素材全部完成，`rough_cut.mp4`（19.81s 硬切粗剪）与 `质检五连.jpg` 已生成。**
- 抽卡履历（详见 `02-镜头提示词.md` 修订记录）：
  - 镜1/3/4 首抽通过；镜2 二抽修复（首抽司机被生成为女性→加男性锚点+换 seed）；
  - 镜5 共四抽：后排机位三连败（稳定多出背对/前景人物），四抽改中控前侧机位成功（seed 8810205）。
- 已知偏差（1.3B 纯 T2V 无参考图的固有限制，精剪阶段可规避/接受）：
  1. 镜5 机位为中控前侧而非分镜表的后排视角；挥赶动作未生成（实际侧身对话，靠 03 方案的五下打击音效+字幕节奏补动作感）。
  2. 男主跨镜长相有漂移；上衣镜1/2/4 为白、镜5 为黑（模型服装遵循失败）。
  3. 根治方案：换 Wan I2V / 首帧参考图（固定人物与服装）或 14B 模型，列入后续优化。
- 工厂修复记录：收尾 ffmpeg 两处 bug（漏传输出路径 → "unconnected output"；`settb=AVG` 在 ffmpeg 7.1 报表达式错误，已移除该 filter）。
- **未产出**：配音/音效/BGM/字幕/精剪，按 `03-音效BGM与剪辑方案.md` 下一轮执行（edge-tts 403 热修复用 `E:\1workspace\local-video-gen\make_poem.py` 的 `patch_edge_tts_endpoint()`）。
- 注：镜5 四抽为手动经 `shots.json` 直接驱动（后台扩散+独立解码+`build_rough_cut/build_qc_sheet` 收尾），因当时工厂进程内解码长卡且已手动按 PID 清理；工厂本身后续可正常幂等续跑。
