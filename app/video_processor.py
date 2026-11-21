import os
import subprocess
import logging
from typing import Optional, List, Tuple
from app.models import TranscriptWord
from app.face_tracker import FaceTracker
from app.subtitle_generator import build_ass_subtitles, build_srt_subtitles

logger = logging.getLogger("shorts_creator.video_processor")

class VideoProcessor:
    def __init__(self, base_dir: str = "storage"):
        self.base_dir = base_dir
        self.downloads_dir = os.path.join(base_dir, "downloads")
        self.clips_dir = os.path.join(base_dir, "clips")
        self.subtitles_dir = os.path.join(base_dir, "subtitles")
        self.face_tracker = FaceTracker()

        os.makedirs(self.downloads_dir, exist_ok=True)
        os.makedirs(self.clips_dir, exist_ok=True)
        os.makedirs(self.subtitles_dir, exist_ok=True)

    def escape_ffmpeg_path(self, path: str) -> str:
        """Escape Windows file paths for FFmpeg filter arguments."""
        abs_path = os.path.abspath(path).replace("\\", "/")
        return abs_path.replace(":", "\\:")

    def trim_video(
        self,
        input_path: str,
        output_path: str,
        start_time: float,
        end_time: float
    ) -> str:
        """Cut video accurately around requested timestamps."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        duration = max(1.0, end_time - start_time)

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_time),
            "-i", input_path,
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            output_path
        ]
        logger.info(f"Trimming segment: {' '.join(cmd)}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            logger.error(f"FFmpeg trim error: {res.stderr}")
            raise RuntimeError(f"Trimming failed: {res.stderr[-300:]}")
        return output_path

    def render_short_clip(
        self,
        source_video_path: str,
        output_clip_path: str,
        words: List[TranscriptWord],
        layout: str = "blur_fill",
        caption_style: str = "hormozi",
        clip_id: str = "clip",
        start_time_offset: float = 0.0
    ) -> Tuple[str, str, str]:
        """
        Main processing pipeline:
        1. Generates ASS animated subtitles & SRT clean subtitles
        2. Applies 9:16 vertical re-framing (blur background, face tracking, or center crop)
        3. Burns subtitles onto output vertical MP4
        Returns: (output_video_path, thumbnail_path, srt_path)
        """
        os.makedirs(os.path.dirname(output_clip_path), exist_ok=True)

        ass_path = os.path.join(self.subtitles_dir, f"{clip_id}.ass")
        srt_path = os.path.join(self.subtitles_dir, f"{clip_id}.srt")
        thumb_path = os.path.splitext(output_clip_path)[0] + ".jpg"

        # Generate subtitles
        if words:
            build_ass_subtitles(words, ass_path, style_key=caption_style, clip_offset=start_time_offset)
            build_srt_subtitles(words, srt_path, clip_offset=start_time_offset)
            has_subtitles = True
            escaped_ass = self.escape_ffmpeg_path(ass_path)
        else:
            has_subtitles = False
            escaped_ass = ""

        # Build filter complex according to layout
        filter_complex = ""

        if layout == "face_crop":
            # Smart face tracking crop
            crop_x, crop_y, crop_w, crop_h = self.face_tracker.track_speaker_center(source_video_path)
            # Crop to 9:16 and scale to 1080x1920
            v_chain = f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=1080:1920[cropped]"
            if has_subtitles:
                filter_complex = f"{v_chain};[cropped]ass='{escaped_ass}'[outv]"
            else:
                filter_complex = f"{v_chain};[cropped]null[outv]"

        elif layout == "center_crop":
            # Direct 9:16 center crop
            v_chain = "[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920[cropped]"
            if has_subtitles:
                filter_complex = f"{v_chain};[cropped]ass='{escaped_ass}'[outv]"
            else:
                filter_complex = f"{v_chain};[cropped]null[outv]"

        else: # Default: blur_fill (The Opus Clip gold standard)
            # Background: scaled & blurred to 1080x1920 with slight darkening
            # Foreground: original 16:9 scaled to 1080 width, centered vertically
            v_chain = (
                "[0:v]split=2[bg_in][fg_in];"
                "[bg_in]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,boxblur=25:3,eq=brightness=-0.08:contrast=1.05[bg];"
                "[fg_in]scale=1080:-1[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[merged]"
            )
            if has_subtitles:
                filter_complex = f"{v_chain};[merged]ass='{escaped_ass}'[outv]"
            else:
                filter_complex = f"{v_chain};[merged]null[outv]"

        # Execute FFmpeg rendering
        cmd = [
            "ffmpeg", "-y",
            "-i", source_video_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            output_clip_path
        ]

        logger.info(f"Rendering short clip ({layout}, {caption_style})...")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            logger.error(f"FFmpeg render error: {res.stderr}")
            raise RuntimeError(f"Render failed: {res.stderr[-400:]}")

        # Generate thumbnail
        thumb_cmd = [
            "ffmpeg", "-y",
            "-ss", "00:00:01",
            "-i", output_clip_path,
            "-vframes", "1",
            "-q:v", "2",
            thumb_path
        ]
        subprocess.run(thumb_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        return output_clip_path, thumb_path, srt_path
