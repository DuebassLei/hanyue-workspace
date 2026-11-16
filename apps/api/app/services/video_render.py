"""图文成片渲染引擎：镜头配图 → TTS 配音 → FFmpeg Ken Burns 合成。

设计约束（对齐 AGENT.md「不伪造成功」）：
- ffmpeg/ffprobe 缺失、生图/TTS 不可用 → 明确 RenderError 带 errorCode；
- 单镜头失败 = 整任务失败并指明镜头序号，绝不静默丢镜头出片；
- 时长以 ffprobe 实测音频为准（TTS 词边界值只做下限兜底）。

产物：1080x1920（或 1920x1080）H.264+AAC MP4，可选 ASS 硬字幕与 BGM 垫乐。
中间产物在 storage/video_tmp/<shotId>/，整任务成功后统一清理。
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

MOTION_FILTERS = {
    # zoompan：d=总帧数；z 从 1.0 线性到极值即推拉，固定中心即纯推拉，偏移即横移。
    # 注意：ffmpeg 9 的 eval 求值器在 z 表达式里引用上一帧变量 z 会报
    # 「Undefined constant or missing '('」（如 if(eq(on,0),…,max(z-…))），
    # 故全部改写为只依赖 on（输出帧号）的闭式表达。
    # 每帧步长由调用方按 12% ÷ 总帧数 归一（ZOOM_TRAVEL），长短镜头推拉速度一致，
    # 不再出现「长镜头提前静止、短镜头推完不成 12%」。
    "zoom_in": "zoompan=z='min(1+on*{step},1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
    "zoom_out": "zoompan=z='max(1.12-on*{step},1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
    "pan_left": "zoompan=z='1.08':x='(iw-iw/zoom)*(1-on/{frames})':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
    "pan_right": "zoompan=z='1.08':x='(iw-iw/zoom)*on/{frames}':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
}
# 推拉运镜总幅度（1.0 → 1.12），步长按镜头帧数摊分
ZOOM_TRAVEL = 0.12
RESOLUTIONS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
FPS = 30
TAIL_MS = 500          # 旁白结束后的留白，避免话音刚落就切镜
MIN_SHOT_MS = 1_200    # 无旁白镜头的最低画面时长
MAX_TOTAL_MS = 10 * 60 * 1000
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Narration,{font},{fontsize},&H00FFFFFF,&H00FFFFFF,&H00101010,&H96000000,-1,0,0,0,100,100,0,0,1,4,1,2,60,60,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


class RenderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RenderError(
            "ffmpeg_missing",
            "服务器未安装 ffmpeg：视频合成不可用。请安装 ffmpeg 并确保其在 PATH 中（Docker 镜像需 apt install ffmpeg）",
        )
    return path


def ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


def probe_duration_ms(path: Path) -> int:
    """实测媒体时长（毫秒）。优先 ffprobe format.duration，退回解析 ffmpeg -i stderr。"""
    probe = ffprobe_bin()
    if probe:
        result = _run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            what="ffprobe",
        )
        try:
            return max(int(float(result.stdout.strip()) * 1000), 0)
        except ValueError:
            pass
    # 少数构建没有 ffprobe：解析 ffmpeg -i 的 Duration 行兜底
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    import re

    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
    if not match:
        raise RenderError("media_probe_failed", f"无法读取媒体时长：{path.name}")
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return int((hours * 3600 + minutes * 60 + seconds) * 1000)


def _run(cmd: list[str], *, what: str, expect_video: bool = False) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RenderError("ffmpeg_missing", f"{what} 无法启动：{exc}") from exc
    if proc.returncode != 0:
        # 失败时把完整 stderr 落日志：ffmpeg 的真实报错（如滤镜解析失败）只在 stderr，
        # 截断尾巴经常丢掉根因行（历史上「静默丢视频流」排障即因此受阻）
        import logging

        logging.getLogger("app.video_render").error(
            "[ffmpeg] %s exit=%s cmd=%s\n%s", what, proc.returncode, " ".join(map(str, cmd)),
            (proc.stderr or "").strip(),
        )
        tail = (proc.stderr or "").strip()[-400:]
        raise RenderError("ffmpeg_failed", f"{what} 执行失败（exit {proc.returncode}）：{tail}")
    # 防御性校验：ffmpeg 会「exit 0 但丢流」（如 map 错误导致只剩音轨），必须查产物。
    # 仅渲染类步骤传 expect_video=True；ffprobe/探测纯音频文件不适用该校验。
    if expect_video and not _has_video_stream(Path(cmd[-1])):
        raise RenderError("ffmpeg_output_invalid", f"{what} 产物缺少视频流：{Path(cmd[-1]).name}")
    return proc


def _has_video_stream(path: Path) -> bool:
    probe = ffprobe_bin()
    if not probe or not path.is_file():
        return False
    result = subprocess.run(
        [probe, "-v", "error", "-select_streams", "v", "-show_entries",
         "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def escape_ass(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        .replace("\r", "").replace("\n", "\\N")
    )


def _ms_to_ass(ms: int) -> str:
    cs = ms // 10
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def build_ass(events: list[tuple[int, int, str]], *, width: int, height: int) -> str:
    """events: [(start_ms, end_ms, text)] → ASS 全文（竖屏大字号、底部居中）。"""
    fontsize = 64 if height > width else 48
    marginv = 380 if height > width else 90
    lines = [ASS_HEADER.format(w=width, h=height, font="Microsoft YaHei", fontsize=fontsize, marginv=marginv)]
    for start, end, text in events:
        lines.append(
            f"Dialogue: 0,{_ms_to_ass(start)},{_ms_to_ass(end)},Narration,,0,0,0,,{escape_ass(text)}"
        )
    return "\n".join(lines) + "\n"


def render_shot_clip(
    *, image: Path, audio: Path | None, duration_ms: int, motion: str,
    width: int, height: int, out: Path,
) -> None:
    frames = max(int(duration_ms * FPS / 1000), FPS)
    template = MOTION_FILTERS.get(motion) or MOTION_FILTERS["zoom_in"]
    vf = template.format(frames=frames, w=width, h=height, fps=FPS, step=f"{ZOOM_TRAVEL / frames:.8f}")
    # -t 是输出选项（放在任何 -i 之后都会被当成下一个输入的文件名，ffmpeg 9 直接报错）
    cmd = [ffmpeg_bin(), "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(image)]
    if audio is not None:
        cmd += ["-i", str(audio)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    # 必须显式 -map：一旦手动指定任何 -map，ffmpeg 不再自动选流；
    # 只 map 音频会让视频被静默丢弃且 exit 0（成片「只有音轨」的根因）
    cmd += [
        "-vf", vf, "-map", "0:v", "-map", "1:a", "-t", f"{duration_ms / 1000:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-shortest", str(out),
    ]
    _run(cmd, what=f"镜头合成（{out.name}）", expect_video=True)


def concat_clips(clips: list[Path], out: Path, workdir: Path) -> None:
    listing = workdir / "concat.txt"
    # concat demuxer 里相对路径按「列表文件所在目录」解析：统一转绝对路径，
    # 并把单引号转义成 '\''（列表语法以 ' 包裹文件名）
    listing.write_text(
        "".join(f"file '{p.resolve().as_posix().replace(chr(39), chr(92)+chr(39)*2+chr(39))}'\n" for p in clips),
        encoding="utf-8",
    )
    _run(
        [ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", str(out)],
        what="镜头拼接", expect_video=True,
    )


def burn_subtitles(video: Path, ass: Path, out: Path) -> None:
    # Windows 反斜杠会打断 libass 路径解析，滤镜参数里统一用正斜杠并转义 ':'
    ass_path = ass.as_posix().replace(":", "\\:")
    _run(
        [ffmpeg_bin(), "-y", "-i", str(video), "-vf", f"subtitles='{ass_path}'",
         "-c:v", "libx264", "-preset", "medium", "-crf", "21",
         "-c:a", "copy", str(out)],
        what="字幕烧录", expect_video=True,
    )


def mix_bgm(video: Path, bgm: Path, volume: float, out: Path) -> None:
    gain = min(max(volume, 0.02), 0.6)
    _run(
        [ffmpeg_bin(), "-y", "-i", str(video), "-stream_loop", "-1", "-i", str(bgm),
         "-filter_complex",
         f"[1:a]volume={gain:.2f}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         str(out)],
        what="BGM 混音", expect_video=True,
    )
