# -*- coding: utf-8 -*-
"""
AI 短剧《车内搭讪》无人值守生成工厂：一次一个进程只出一个镜头，幂等可中断续跑。

用法（解释器固定用 INFRA 的 venv）：
  E:\\1workspace\\local-video-gen\\venv\\Scripts\\python.exe make_shots.py next      # 从第一个缺 clip 的镜头开始
  E:\\1workspace\\local-video-gen\\venv\\Scripts\\python.exe make_shots.py 3         # 只重做指定镜头（clip 已存在仍会跳过）
  E:\\1workspace\\local-video-gen\\venv\\Scripts\\python.exe make_shots.py next --dry-run

流程（严格照抄 INFRA make_poem.py::make_shot 成熟序列）：
  generate.py <英文 prompt>  -> output/latents_f{N}_umt5-xxl-fp8_bf16_g5.pt
  decode_video.py（独立进程） -> output/latents_f{N}_..._g5.mp4
  移动产物到 clips/shotN.mp4、latents/shotN.pt；清理本镜时间窗口内的 wan_*.mp4 中间文件
  失败等 30s 整体重试 1 次，再失败记 ERROR 并继续下一镜
调度：PID 锁防重复跑、Windows 防休眠、累计忙碌 60 分钟镜头间隙休 10 分钟。
全部 5 镜齐备后 best-effort：concat 重编码 rough_cut.mp4、抽中帧拼 质检五连.jpg。
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

INFRA = Path(r"E:\1workspace\local-video-gen")
PY = str(INFRA / "venv" / "Scripts" / "python.exe")
FFMPEG = r"F:\java\ffmpeg-7.1-essentials_build\bin\ffmpeg.exe"
OUT_INFRA = INFRA / "output"

ENC_TAG = "umt5-xxl-fp8"          # generate.py 默认 ENCODER_DIR=models/umt5-xxl-fp8
DTYPE_TAG = "bf16"
GUIDANCE_TAG = "5"                # GUIDANCE_SCALE=5.0 经 :g 格式化后为 g5
NUM_STEPS = "40"
GUIDANCE_SCALE = "5.0"
FPS = 16

CLIPS = ROOT / "clips"
LATS = ROOT / "latents"
LOGS = ROOT / "logs"
LOCK = LOGS / ".runner.lock"
SHOTS_JSON = ROOT / "shots.json"

REST_EVERY_SEC = int(os.environ.get("REST_EVERY_MIN", "60")) * 60
REST_SEC = int(os.environ.get("REST_MIN", "10")) * 60

DRY = "--dry-run" in sys.argv
_positional = [a for a in sys.argv[1:] if not a.startswith("--")]
target = _positional[0] if _positional else "next"

for _d in (CLIPS, LATS, LOGS):
    _d.mkdir(parents=True, exist_ok=True)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- 调度件（抄 make_poem.py）

def keep_awake():
    """Windows 防休眠：ES_CONTINUOUS | ES_SYSTEM_REQUIRED（只影响本进程存活期）"""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    except Exception:
        pass


def lock_owner_alive(pid):
    """OpenProcess + GetExitCodeProcess 判断 pid 是否仍活跃（STILL_ACTIVE=259）。"""
    STILL_ACTIVE = 259
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return bool(ok) and code.value == STILL_ACTIVE


def acquire_lock():
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = 0
        if pid and lock_owner_alive(pid):
            log(f"已有工厂在运行（pid={pid}），本次不重复启动。")
            raise SystemExit(3)
    LOCK.write_text(str(os.getpid()), encoding="utf-8")


def release_lock():
    try:
        LOCK.unlink()
    except OSError:
        pass


def gpu_rest(seconds):
    """镜头间隙让显卡闲置散热：子进程已退出，只在父进程里计时。"""
    log(f"=== 显卡休息 {seconds // 60} 分钟开始（{time.strftime('%H:%M')}），GPU 空闲降温 ===")
    left = seconds
    while left > 0:
        step = min(60, left)
        time.sleep(step)
        left -= step
        if left > 0:
            log(f"休息中…剩余 {left // 60} 分{left % 60}秒")
    log("=== 休息结束，继续制作 ===")


_busy_since = time.time()


def maybe_rest():
    """镜头间隙检查：累计忙碌满 REST_EVERY_SEC 就休息 REST_SEC。"""
    global _busy_since
    if time.time() - _busy_since >= REST_EVERY_SEC:
        gpu_rest(REST_SEC)
        _busy_since = time.time()


# ---------------------------------------------------------------- 单镜头生产

def latent_names(num_frames):
    """generate.py 落盘潜变量名与 decode_video.py 解码出的 mp4 名。"""
    stem = f"latents_f{num_frames}_{ENC_TAG}_{DTYPE_TAG}_g{GUIDANCE_TAG}"
    return stem + ".pt", stem + ".mp4"


def list_wan_mp4():
    return {p.name for p in OUT_INFRA.glob("wan_*.mp4")}


def wan_timestamp(name):
    """wan_YYYYmmdd_HHMMSS.mp4 -> epoch 秒；解析失败返回 None。"""
    try:
        return datetime.strptime(name[4:19], "%Y%m%d_%H%M%S").timestamp()
    except (ValueError, IndexError):
        return None


def cleanup_wan(before_names, since_ts):
    """只删本镜尝试窗口内新出现的 wan_*.mp4（generate.py 进程内解码产物，通常 OOM 不会有）。
    双重判定：文件名不在尝试前快照中 + 文件名时间戳在窗口起点 120s 内。"""
    for p in OUT_INFRA.glob("wan_*.mp4"):
        if p.name in before_names:
            continue
        ts = wan_timestamp(p.name)
        if ts is not None and ts < since_ts - 120:
            continue
        try:
            p.unlink()
            log(f"清理本镜中间文件 output/{p.name}")
        except OSError as e:
            log(f"WARN 清理 {p.name} 失败：{e}")


def make_shot(item):
    """单镜：扩散(独立进程) -> 独立解码(独立进程) -> 移动产物。失败重试 1 次。"""
    sid = item["id"]
    n = item["num_frames"]
    clip = CLIPS / f"shot{sid}.mp4"
    if clip.exists():
        log(f"镜头{sid} clip 已存在（{clip.name}），跳过")
        return True

    latent_pt, latent_mp4 = latent_names(n)
    if DRY:
        log(f"[DRY] 镜头{sid}: NUM_FRAMES={n} SEED={item['seed']} STEPS={NUM_STEPS} CFG={GUIDANCE_SCALE}")
        log(f"[DRY]   RUN(cwd={INFRA.name}): python generate.py <prompt_en {len(item['prompt_en'])} chars>")
        log(f"[DRY]   RUN: python decode_video.py  LATENTS={latent_pt} FPS=16 QUALITY=9")
        log(f"[DRY]   MOVE output/{latent_mp4} -> clips/shot{sid}.mp4")
        log(f"[DRY]   MOVE output/{latent_pt} -> latents/shot{sid}.pt")
        return True

    env_gen = os.environ.copy()
    env_gen.update(
        NUM_FRAMES=str(n),
        NUM_STEPS=NUM_STEPS,
        GUIDANCE_SCALE=GUIDANCE_SCALE,
        SEED=str(item["seed"]),
        NEGATIVE_PROMPT=item["negative_en"],
    )
    env_dec = os.environ.copy()
    env_dec.update(LATENTS=latent_pt, QUALITY="9", FPS=str(FPS))

    for attempt in (1, 2):
        before = list_wan_mp4()
        t0 = time.time()
        try:
            log(f"====== 镜头{sid} 第 {attempt}/2 次尝试：{n} 帧 / seed={item['seed']} ======")
            subprocess.run([PY, "generate.py", item["prompt_en"]],
                           cwd=str(INFRA), env=env_gen, check=True)
            subprocess.run([PY, "decode_video.py"],
                           cwd=str(INFRA), env=env_dec, check=True)
            src_mp4 = OUT_INFRA / latent_mp4
            src_pt = OUT_INFRA / latent_pt
            if not src_mp4.exists():
                raise RuntimeError(f"解码产物缺失：{src_mp4}")
            if not src_pt.exists():
                raise RuntimeError(f"潜变量缺失：{src_pt}")
            shutil.move(str(src_mp4), str(clip))
            shutil.move(str(src_pt), str(LATS / f"shot{sid}.pt"))
            cleanup_wan(before, t0)
            log(f"镜头{sid} 完成：{clip.name}")
            return True
        except Exception as e:
            log(f"ERROR 镜头{sid} 第 {attempt} 次失败：{e}")
            cleanup_wan(before, t0)
            if attempt == 2:
                log(f"ERROR 镜头{sid} 两次尝试均失败，跳过，继续后续镜头")
                return False
            log("等待 30s 后整体重试一次…")
            time.sleep(30)
    return False


# ---------------------------------------------------------------- 收尾（best-effort）

def run_ffmpeg(args, out_path):
    """out_path 必须作为最后一个位置参数传给 ffmpeg（输出文件），否则滤镜图输出无消费方。"""
    log(f"RUN: ffmpeg -> {Path(out_path).name}")
    if DRY:
        return
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args, str(out_path)],
                   cwd=str(ROOT), check=True)


def build_rough_cut(shots):
    """5 镜 concat 重编码拼接：统一 832x480@16 yuv420p，无转场无音频。"""
    out = ROOT / "rough_cut.mp4"
    if out.exists():
        log("rough_cut.mp4 已存在，跳过拼接（删除后可重建）")
        return
    inputs, labels = [], []
    chains = []
    for i, item in enumerate(shots):
        inputs += ["-i", str(CLIPS / f"shot{item['id']}.mp4")]
        labels.append(f"[v{i}]")
        # 各镜本来就是同规格 832x480@16；不写 settb（ffmpeg 7.1 把 AVG 当表达式解析会报错），
        # concat 内部自行对齐时基
        chains.append(
            f"[{i}:v]fps={FPS},scale=832:480:flags=lanczos,format=yuv420p[v{i}]"
        )
    fc = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(shots)}:v=1:a=0[vout]"
    run_ffmpeg([*inputs, "-filter_complex", fc, "-map", "[vout]",
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                "-r", str(FPS)], out)
    if not DRY:
        log("rough_cut.mp4 拼接完成")


def build_qc_sheet(shots):
    """各镜抽中帧横排拼 质检五连.jpg。"""
    out = ROOT / "质检五连.jpg"
    if out.exists():
        log("质检五连.jpg 已存在，跳过（删除后可重建）")
        return
    mids = [LOGS / f"_mid{item['id']}.png" for item in shots]
    if not DRY:
        for item, mid in zip(shots, mids):
            ss = item["duration_sec"] / 2
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", f"{ss:.2f}",
                            "-i", str(CLIPS / f"shot{item['id']}.mp4"),
                            "-frames:v", "1", str(mid)],
                           cwd=str(ROOT), check=True)
    chain = "".join(f"[{i}:v]" for i in range(len(shots)))
    fc = chain + f"hstack=inputs={len(shots)},scale=3328:-2[o]"
    run_ffmpeg([*sum([["-i", str(m)] for m in mids], []),
                "-filter_complex", fc, "-map", "[o]", "-q:v", "3"], out)
    if not DRY:
        for m in mids:
            m.unlink(missing_ok=True)
        log("质检五连.jpg 生成完成")


def finalize(shots):
    log("=== 全部 5 镜齐备，执行收尾（best-effort）===")
    for step_name, step in (("rough_cut.mp4", build_rough_cut), ("质检五连.jpg", build_qc_sheet)):
        try:
            step(shots)
        except Exception as e:
            log(f"WARN 收尾步骤 {step_name} 失败（不影响已完成素材）：{e}")
    log("=== ALL SHOTS DONE ===")


# ---------------------------------------------------------------- 主流程

def load_shots():
    return json.loads(SHOTS_JSON.read_text(encoding="utf-8"))


def clip_done(item):
    return (CLIPS / f"shot{item['id']}.mp4").exists()


def main():
    global _busy_since
    keep_awake()
    shots = load_shots()

    if target == "next":
        first_missing = next((i for i, it in enumerate(shots) if not clip_done(it)), None)
        if first_missing is None:
            log("5 镜 clip 全部存在。")
            finalize(shots)
            return
        queue = shots[first_missing:]
    else:
        try:
            want = int(target)
        except ValueError:
            raise SystemExit(f"无法识别的镜头号：{target!r}（支持 next / 1..5）")
        queue = [it for it in shots if it["id"] == want]
        if not queue:
            raise SystemExit(f"shots.json 中找不到镜头 {want}")

    log(f"=== 工厂启动：目标={target}，dry_run={DRY}，"
        f"休息策略=每忙碌 {REST_EVERY_SEC // 60} 分钟休 {REST_SEC // 60} 分钟 ===")
    log(f"排队镜头：{[it['id'] for it in queue]}（已存在 clip 者自动跳过）")

    _busy_since = time.time()
    for k, item in enumerate(queue):
        make_shot(item)
        if k < len(queue) - 1:
            maybe_rest()   # 检查点只在镜头间隙
        time.sleep(2)

    if all(clip_done(it) for it in shots):
        finalize(shots)
    else:
        missing = [it["id"] for it in shots if not clip_done(it)]
        log(f"本轮结束，仍缺 clip 的镜头：{missing}（重跑 run_factory.bat 续跑）")


if __name__ == "__main__":
    if not DRY:
        acquire_lock()
    try:
        main()
    finally:
        if not DRY:
            release_lock()
