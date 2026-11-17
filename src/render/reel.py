"""
render/reel.py — Templated reel renderer via MoviePy + FFmpeg
Produces 1080x1920 MP4 with burned-in captions
"""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Optional

from src.utils import get_logger, load_settings, load_brand
from src.render.image import render_post
from src.render.tts import generate_voiceover
from src.copywriter import CopyResult

log = get_logger("render.reel")


def render_reel(
    copy: CopyResult,
    base_context: dict,
    output_dir: Path,
    template_name: str,
) -> Optional[Path]:
    """
    Render a reel MP4 from beat cards + optional voiceover.
    Returns path to final MP4, or None on failure.
    """
    settings = load_settings()
    reel_cfg = settings["reel"]
    W, H = reel_cfg["reel_width"], reel_cfg["reel_height"]  # 1080x1920

    script = copy.reel_script or {}
    beats = script.get("beats", [copy.hook, copy.caption[:80]])
    if not beats:
        beats = [copy.hook]

    beat_duration = reel_cfg["beat_duration_s"]
    end_card_duration = reel_cfg["end_card_duration_s"]

    # 1. Render each beat as a frame PNG
    beat_paths = []
    for i, beat_text in enumerate(beats):
        ctx = {
            **base_context,
            "beat_text": beat_text,
            "beat_index": i,
            "total_beats": len(beats),
            "is_cover": i == 0,
        }
        frame_path = output_dir / f"reel_frame_{i:02d}.png"
        success = render_post(template_name, ctx, frame_path, W, H)
        if success:
            beat_paths.append(frame_path)
        else:
            log.warning(f"Beat frame {i} failed to render")

    if not beat_paths:
        log.error("No reel frames rendered")
        return None

    # 2. Render end card
    end_ctx = {**base_context, "is_end_card": True,
               "beat_text": "gyodify.shop", "is_cover": False}
    end_card_path = output_dir / "reel_endcard.png"
    render_post(template_name, end_ctx, end_card_path, W, H)
    if end_card_path.exists():
        beat_paths.append(end_card_path)

    # 3. Generate voiceover
    vo_text = script.get("voiceover", copy.hook)
    vo_path = output_dir / "voiceover"
    vo_success, vo_method = generate_voiceover(vo_text, copy.language, vo_path)
    vo_mp3 = vo_path.with_suffix(".mp3") if vo_success else None

    # 4. Assemble with FFmpeg
    mp4_path = output_dir / "reel.mp4"
    success = _assemble_ffmpeg(
        frame_paths=beat_paths,
        beat_duration=beat_duration,
        end_card_duration=end_card_duration,
        audio_path=vo_mp3,
        output_path=mp4_path,
        reel_cfg=reel_cfg,
    )

    if not success:
        log.error("FFmpeg assembly failed")
        return None

    # 5. Generate cover image (1080x1920 from first frame)
    cover_path = output_dir / "reel_cover.png"
    if beat_paths:
        import shutil
        shutil.copy(str(beat_paths[0]), str(cover_path))

    # 6. Verify output
    info = _get_video_info(mp4_path)
    log.info(
        f"Reel rendered: {mp4_path.name} | "
        f"{info.get('duration', 0):.1f}s | "
        f"{mp4_path.stat().st_size // (1024*1024):.1f}MB | "
        f"audio: {vo_method}"
    )

    # Tag audio vibe in Telegram note
    base_context["audio_vibe"] = script.get("audio_vibe", "upbeat, energetic")
    base_context["tts_method"] = vo_method

    return mp4_path


def _assemble_ffmpeg(
    frame_paths: list[Path],
    beat_duration: float,
    end_card_duration: float,
    audio_path: Optional[Path],
    output_path: Path,
    reel_cfg: dict,
) -> bool:
    """Build MP4 from still frames using FFmpeg concat + optional audio."""
    try:
        # Write concat file
        concat_file = output_path.parent / "ffmpeg_concat.txt"
        with open(concat_file, "w") as f:
            for i, path in enumerate(frame_paths):
                duration = end_card_duration if i == len(frame_paths) - 1 else beat_duration
                f.write(f"file '{path.resolve()}'\n")
                f.write(f"duration {duration}\n")

        # Build FFmpeg command
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
        ]

        if audio_path and audio_path.exists():
            cmd.extend(["-i", str(audio_path)])
            cmd.extend(["-map", "0:v", "-map", "1:a",
                        "-shortest", "-af", "loudnorm"])
        else:
            # Silent: add silent audio track so Instagram accepts it
            cmd.extend([
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-map", "0:v", "-map", "1:a", "-shortest",
            ])

        total_frames = len(frame_paths)
        expected_duration = (
            (total_frames - 1) * beat_duration + end_card_duration
        )

        cmd.extend([
            "-c:v", reel_cfg["codec"],
            "-preset", reel_cfg["preset"],
            "-crf", str(reel_cfg["crf"]),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-c:a", reel_cfg["audio_codec"],
            "-b:a", reel_cfg["audio_bitrate"],
            "-r", str(reel_cfg["fps"]),
            "-vf", f"scale={reel_cfg['reel_width']}:{reel_cfg['reel_height']}:force_original_aspect_ratio=decrease,pad={reel_cfg['reel_width']}:{reel_cfg['reel_height']}",
            str(output_path),
        ])

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )

        if result.returncode != 0:
            log.error(f"FFmpeg error:\n{result.stderr[-2000:]}")
            return False

        return True

    except subprocess.TimeoutExpired:
        log.error("FFmpeg timed out after 120s")
        return False
    except FileNotFoundError:
        log.error("FFmpeg not found — install via: sudo apt-get install ffmpeg")
        return False
    except Exception as e:
        log.error(f"FFmpeg assembly error: {e}")
        return False


def _get_video_info(mp4_path: Path) -> dict:
    """Get basic video info via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(mp4_path)],
            capture_output=True, text=True, timeout=10,
        )
        import json
        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        return {
            "duration": float(fmt.get("duration", 0)),
            "size_mb": int(fmt.get("size", 0)) / (1024 * 1024),
            "bit_rate": fmt.get("bit_rate"),
        }
    except Exception:
        return {}
