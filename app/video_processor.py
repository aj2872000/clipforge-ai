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
        self.has_cas = self._check_cas_filter()

        os.makedirs(self.downloads_dir, exist_ok=True)
        os.makedirs(self.clips_dir, exist_ok=True)
        os.makedirs(self.subtitles_dir, exist_ok=True)

    def _check_cas_filter(self) -> bool:
        """Check if FFmpeg has CAS (Contrast Adaptive Sharpen) filter compiled."""
        try:
            res = subprocess.run(["ffmpeg", "-h", "filter=cas"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return res.returncode == 0 and "Contrast Adaptive Sharpen" in res.stdout
        except Exception:
            return False

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
            "-preset", "ultrafast",
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
        High-Performance 9:16 Shorts Rendering Pipeline:
        - Downscale-blur-upscale algorithm (cuts background blur processing time by ~70%)
        - Preset 'veryfast' for rapid H.264 rendering
        - Burns animated ASS karaoke subtitles
        """
        os.makedirs(os.path.dirname(output_clip_path), exist_ok=True)

        ass_path = os.path.join(self.subtitles_dir, f"{clip_id}.ass")
        srt_path = os.path.join(self.subtitles_dir, f"{clip_id}.srt")
        thumb_path = os.path.splitext(output_clip_path)[0] + ".jpg"

        # Probe source video dimensions to match highest available resolution
        src_w, src_h = 1920, 1080
        try:
            import cv2
            cap = cv2.VideoCapture(source_video_path)
            if cap.isOpened():
                src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
                src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
                cap.release()
        except Exception:
            pass

        # Target resolution higher than 1080p:
        # If source is 4K (height >= 2160 or width >= 3840): output 2160x3840 (4K UHD 9:16)
        # Otherwise: output 1440x2560 (2K QHD 9:16)
        if src_h >= 2160 or src_w >= 3840:
            target_w, target_h = 2160, 3840
            logger.info(f"Source video is 4K ({src_w}x{src_h}). Rendering 4K Ultra-HD vertical short (2160x3840).")
        else:
            target_w, target_h = 1440, 2560
            logger.info(f"Source video is {src_w}x{src_h}. Rendering 2K QHD vertical short (1440x2560).")

        # Generate subtitles scaled proportionally to target resolution
        if words:
            build_ass_subtitles(
                words=words,
                output_path=ass_path,
                style_key=caption_style,
                clip_offset=start_time_offset,
                target_w=target_w,
                target_h=target_h
            )
            build_srt_subtitles(words, srt_path, clip_offset=start_time_offset)
            has_subtitles = True
            escaped_ass = self.escape_ffmpeg_path(ass_path)
        else:
            has_subtitles = False
            escaped_ass = ""

        # High-Fidelity Upscaling & Sharpening filter strings
        sharpen_filter = "cas=0.55" if self.has_cas else "unsharp=5:5:0.6:5:5:0.2"
        fg_sharpen = "cas=0.45" if self.has_cas else "unsharp=5:5:0.5:5:5:0.2"

        # Build filter complex according to layout
        filter_complex = ""

        if layout == "face_crop":
            # Smart face tracking crop + Lanczos upscaling + adaptive sharpening to target resolution
            crop_x, crop_y, crop_w, crop_h = self.face_tracker.track_speaker_center(source_video_path)
            v_chain = (
                f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                f"scale={target_w}:{target_h}:flags=lanczos+accurate_rnd+full_chroma_int,"
                f"{sharpen_filter}[cropped]"
            )
            if has_subtitles:
                filter_complex = f"{v_chain};[cropped]ass='{escaped_ass}'[outv]"
            else:
                filter_complex = f"{v_chain};[cropped]null[outv]"

        elif layout == "center_crop":
            # Direct 9:16 center crop + Lanczos upscaling + adaptive sharpening to target resolution
            v_chain = (
                "[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
                f"scale={target_w}:{target_h}:flags=lanczos+accurate_rnd+full_chroma_int,"
                f"{sharpen_filter}[cropped]"
            )
            if has_subtitles:
                filter_complex = f"{v_chain};[cropped]ass='{escaped_ass}'[outv]"
            else:
                filter_complex = f"{v_chain};[cropped]null[outv]"

        else: # Default: blur_fill (High-Speed Downscale-Blur-Upscale Algorithm in Ultra-HD)
            bg_scale_w = int(target_w / 4)
            bg_scale_h = int(target_h / 4)
            v_chain = (
                "[0:v]split=2[bg_in][fg_in];"
                f"[bg_in]scale={bg_scale_w}:{bg_scale_h}:force_original_aspect_ratio=increase,"
                f"crop={bg_scale_w}:{bg_scale_h},boxblur=5:1,"
                f"scale={target_w}:{target_h}:flags=bilinear,"
                "eq=brightness=-0.08:contrast=1.05[bg];"
                f"[fg_in]scale={target_w}:-1:flags=lanczos+accurate_rnd+full_chroma_int,{fg_sharpen},eq=contrast=1.03:saturation=1.04[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[merged]"
            )
            if has_subtitles:
                filter_complex = f"{v_chain};[merged]ass='{escaped_ass}'[outv]"
            else:
                filter_complex = f"{v_chain};[merged]null[outv]"

        # Execute FFmpeg rendering with near-lossless CRF 16, 320k audio, and 'veryfast' preset
        cmd = [
            "ffmpeg", "-y",
            "-threads", "0",
            "-i", source_video_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "16",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "320k",
            "-movflags", "+faststart",
            output_clip_path
        ]

        logger.info(f"Rendering short clip ({layout}, {caption_style}) with fast blur...")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            logger.error(f"FFmpeg render error: {res.stderr}")
            raise RuntimeError(f"Render failed: {res.stderr[-400:]}")

        # Quick thumbnail extraction
        thumb_cmd = [
            "ffmpeg", "-y",
            "-ss", "00:00:01",
            "-i", output_clip_path,
            "-vframes", "1",
            "-q:v", "3",
            thumb_path
        ]
        subprocess.run(thumb_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        return output_clip_path, thumb_path, srt_path
