import os
import re
import uuid
import shutil
import subprocess
import logging
from typing import Optional, List, Tuple, Dict, Any
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

    def detect_scene_intervals(
        self,
        video_path: str,
        threshold: float = 0.35,
        min_scene_sec: float = 1.5
    ) -> List[Tuple[float, float]]:
        """
        Stage 1: Scene / Shot Cut Detection
        Detects internal camera cuts/scene boundaries using FFmpeg scene change detection:
        select='gt(scene,0.35)',showinfo
        Enforces a minimum scene duration (1.5s) to prevent rapid flash micro-cuts.
        Returns discrete [start_time, end_time] scene intervals spanning the full video duration.
        """
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.warning(f"Could not open video {video_path} for scene detection, defaulting to 1 interval.")
            return [(0.0, 1.0)]

        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
        frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 30.0
        duration = frame_count / fps
        cap.release()

        if duration <= min_scene_sec:
            return [(0.0, duration)]

        cmd = [
            "ffmpeg", "-an", "-i", video_path,
            "-vf", f"scale=320:180,select='gt(scene,{threshold})',showinfo",
            "-f", "null", "-"
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
            raw_cuts = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", res.stderr)]
            raw_cuts = sorted(list(set(raw_cuts)))
        except Exception as e:
            logger.error(f"Error running FFmpeg scene cut detection: {e}")
            raw_cuts = []

        intervals: List[Tuple[float, float]] = []
        t_start = 0.0

        for cut in raw_cuts:
            if cut <= t_start:
                continue
            # Enforce minimum scene duration (1.5s) and ensure remaining video also meets minimum
            if (cut - t_start >= min_scene_sec) and (duration - cut >= min_scene_sec):
                intervals.append((round(t_start, 3), round(cut, 3)))
                t_start = cut

        intervals.append((round(t_start, 3), round(duration, 3)))
        logger.info(f"Scene detection complete for {os.path.basename(video_path)}: {len(intervals)} scene intervals detected: {intervals}")
        return intervals

    def _build_scene_filter(
        self,
        mode: str,
        meta: Dict[str, Any],
        src_w: int,
        src_h: int,
        target_w: int,
        target_h: int,
        fg_sharpen: str
    ) -> str:
        """
        Builds FFmpeg filter complex string for a specific scene mode (fit, fill, split, screenshare, center_crop)
        rendering output labeled as [v_out] at target resolution target_w x target_h.
        """
        mode = mode.lower()

        if mode == "fill":
            # 1 face detected → fill: Calculate median horizontal center (x) of detected face,
            # clamp within safe bounds, and crop to 9:16 (target_w x target_h) centered on speaker
            crop_h = src_h
            crop_w = int(crop_h * 9.0 / 16.0)
            crop_w -= (crop_w % 2)

            cx = meta.get("center_x", src_w / 2.0)
            ideal_x = int(cx - (crop_w / 2.0))
            clamped_x = max(0, min(src_w - crop_w, ideal_x))
            clamped_x -= (clamped_x % 2)

            filter_str = (
                f"[0:v]crop={crop_w}:{crop_h}:{clamped_x}:0,"
                f"scale={target_w}:{target_h}:flags=lanczos+accurate_rnd+full_chroma_int,"
                f"{fg_sharpen},scale={target_w}:{target_h}[v_out]"
            )

        elif mode == "split":
            # 2 faces detected → split: Sort the two faces left-to-right, crop two separate regions
            # centered on left & right speaker, and stack vertically (vstack) into target_w x target_h (top=left, bottom=right)
            half_h = target_h // 2
            aspect = target_w / float(half_h) # 1080/960 = 1.125
            crop_h = src_h
            crop_w = int(crop_h * aspect)
            if crop_w > src_w:
                crop_w = src_w
                crop_h = int(crop_w / aspect)
            crop_w -= (crop_w % 2)
            crop_h -= (crop_h % 2)
            crop_y = (src_h - crop_h) // 2
            crop_y -= (crop_y % 2)

            left_x = meta.get("left_x", src_w * 0.25)
            clamped_left = max(0, min(src_w - crop_w, int(left_x - (crop_w / 2.0))))
            clamped_left -= (clamped_left % 2)

            right_x = meta.get("right_x", src_w * 0.75)
            clamped_right = max(0, min(src_w - crop_w, int(right_x - (crop_w / 2.0))))
            clamped_right -= (clamped_right % 2)

            filter_str = (
                f"[0:v]split=2[v_left][v_right];"
                f"[v_left]crop={crop_w}:{crop_h}:{clamped_left}:{crop_y},"
                f"scale={target_w}:{half_h}:flags=lanczos+accurate_rnd+full_chroma_int,{fg_sharpen}[top_half];"
                f"[v_right]crop={crop_w}:{crop_h}:{clamped_right}:{crop_y},"
                f"scale={target_w}:{half_h}:flags=lanczos+accurate_rnd+full_chroma_int,{fg_sharpen}[bottom_half];"
                f"[top_half][bottom_half]vstack=inputs=2,scale={target_w}:{target_h}[v_out]"
            )

        elif mode == "screenshare":
            # Screenshare / Gameplay: Clean uncropped view with darkened padding/blur canvas
            bg_scale_w = int(target_w / 4)
            bg_scale_h = int(target_h / 4)
            filter_str = (
                f"[0:v]split=2[bg_in][fg_in];"
                f"[bg_in]scale={bg_scale_w}:{bg_scale_h}:force_original_aspect_ratio=increase,"
                f"crop={bg_scale_w}:{bg_scale_h},boxblur=20:5,"
                f"scale={target_w}:{target_h}:flags=bilinear,"
                f"eq=brightness=-0.18:contrast=1.0[bg];"
                f"[fg_in]scale={target_w}:-1:flags=lanczos+accurate_rnd+full_chroma_int[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto,scale={target_w}:{target_h}[v_out]"
            )

        elif mode == "center_crop":
            filter_str = (
                f"[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
                f"scale={target_w}:{target_h}:flags=lanczos+accurate_rnd+full_chroma_int,"
                f"{fg_sharpen},scale={target_w}:{target_h}[v_out]"
            )

        else: # Default: fit (0 faces or 3+ faces)
            # 0 faces detected → fit: Render full horizontal frame centered vertically on blurred/darkened canvas (boxblur=20:5)
            # If video is already vertical (9:16), scale directly
            is_already_vertical = abs((src_w / max(1, src_h)) - (9.0 / 16.0)) < 0.05
            if is_already_vertical:
                filter_str = f"[0:v]scale={target_w}:{target_h}:flags=lanczos+accurate_rnd+full_chroma_int[v_out]"
            else:
                bg_scale_w = int(target_w / 4)
                bg_scale_h = int(target_h / 4)
                filter_str = (
                    f"[0:v]split=2[bg_in][fg_in];"
                    f"[bg_in]scale={bg_scale_w}:{bg_scale_h}:force_original_aspect_ratio=increase,"
                    f"crop={bg_scale_w}:{bg_scale_h},boxblur=20:5,"
                    f"scale={target_w}:{target_h}:flags=bilinear,"
                    f"eq=brightness=-0.10:contrast=1.05[bg];"
                    f"[fg_in]scale={target_w}:-1:flags=lanczos+accurate_rnd+full_chroma_int[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto,scale={target_w}:{target_h}[v_out]"
                )

        return filter_str

    def render_short_clip(
        self,
        source_video_path: str,
        output_clip_path: str,
        words: List[TranscriptWord],
        layout: str = "auto",
        caption_style: str = "hormozi",
        clip_id: str = "clip",
        start_time_offset: float = 0.0
    ) -> Tuple[str, str, str, str]:
        """
        Smart AI Framing & High-Performance 9:16 Shorts Rendering Pipeline:
        - Auto Mode: 4-stage pipeline (Scene cut detection -> Per-scene face classification [Fit/Fill/Split] ->
          Segment rendering -> Seamless concat with continuous audio & karaoke subtitles)
        - Manual Mode: Forced override (Fill, Fit, Split, Screenshare, Center Crop)
        - Strictly enforces minimum resolution of 1080x1920 (or 1440p/4K if source is higher)
        - Zero audio clicks or drift across layout transitions
        Returns: (output_clip_path, thumb_path, srt_path, applied_framing_badge)
        """
        os.makedirs(os.path.dirname(output_clip_path), exist_ok=True)

        ass_path = os.path.join(self.subtitles_dir, f"{clip_id}.ass")
        srt_path = os.path.join(self.subtitles_dir, f"{clip_id}.srt")
        thumb_path = os.path.splitext(output_clip_path)[0] + ".jpg"

        # Probe source video dimensions and frame rate
        src_w, src_h = 1920, 1080
        src_fps = 30.0
        src_duration = 30.0
        try:
            import cv2
            cap = cv2.VideoCapture(source_video_path)
            if cap.isOpened():
                src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
                src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
                src_fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
                frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 30.0
                src_duration = frame_count / src_fps
                cap.release()
        except Exception as e:
            logger.warning(f"Error probing video parameters: {e}")

        # Enforce minimum 1080p, scaling to 1440p or 4K if source matches
        if src_h >= 2160 or src_w >= 3840:
            target_w, target_h = 2160, 3840
            res_label = "4K UHD (2160x3840)"
        elif src_h >= 1440 or src_w >= 2560:
            target_w, target_h = 1440, 2560
            res_label = "1440p QHD (1440x2560)"
        else:
            target_w, target_h = 1080, 1920
            res_label = "1080p FHD (1080x1920)"

        logger.info(
            f"Source video: {src_w}x{src_h} @ {src_fps:.2f}fps ({src_duration:.1f}s). "
            f"Rendering at maximum quality {res_label} preserving {src_fps:.2f}fps with CRF 18."
        )

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

        # Sharpening filter strings
        sharpen_filter = "cas=0.55" if self.has_cas else "unsharp=5:5:0.6:5:5:0.2"
        fg_sharpen = "cas=0.35" if self.has_cas else "unsharp=5:5:0.4:5:5:0.2"

        # Determine scenes and layout classifications
        layout_key = (layout or "auto").lower().strip()
        scenes_data: List[Dict[str, Any]] = []

        if layout_key in ["auto", "smart", "smart_ai"]:
            # Stage 1: Detect camera cut intervals
            scene_intervals = self.detect_scene_intervals(
                source_video_path,
                threshold=0.35,
                min_scene_sec=1.5
            )

            # Stage 2: Per-scene face classification
            for (st, et) in scene_intervals:
                mode, meta = self.face_tracker.classify_scene(
                    source_video_path,
                    start_sec=st,
                    end_sec=et,
                    num_samples=4
                )
                scenes_data.append({
                    "start": st,
                    "end": et,
                    "duration": max(0.1, et - st),
                    "mode": mode,
                    "meta": meta
                })

            applied_modes = []
            for sc in scenes_data:
                label = sc["mode"].capitalize()
                if label not in applied_modes:
                    applied_modes.append(label)
            applied_badge = f"Auto • {' / '.join(applied_modes)}"
            logger.info(f"Smart AI Framing applied: {applied_badge} across {len(scenes_data)} scenes.")

        else:
            # Manual override layout
            if layout_key in ["fill", "face_crop"]:
                mode = "fill"
                crop_x, _, crop_w, _ = self.face_tracker.track_speaker_center(source_video_path)
                meta = {"center_x": crop_x + (crop_w / 2.0)}
                applied_badge = "Manual • Fill"
            elif layout_key in ["split", "two_speakers"]:
                mode = "split"
                lx, rx = self.face_tracker.track_two_speakers(source_video_path)
                meta = {"left_x": lx, "right_x": rx}
                applied_badge = "Manual • Split"
            elif layout_key in ["screenshare", "gameplay"]:
                mode = "screenshare"
                meta = {}
                applied_badge = "Manual • Screenshare"
            elif layout_key == "center_crop":
                mode = "center_crop"
                meta = {}
                applied_badge = "Manual • Center Crop"
            else: # fit / blur_fill
                mode = "fit"
                meta = {}
                applied_badge = "Manual • Fit"

            scenes_data.append({
                "start": 0.0,
                "end": src_duration,
                "duration": src_duration,
                "mode": mode,
                "meta": meta
            })
            logger.info(f"Manual Framing applied: {applied_badge}")

        # Stage 3: Segment Rendering & Seamless Concatenation
        if len(scenes_data) == 1:
            # Single scene optimization: render in 1 fast pass
            sc = scenes_data[0]
            filter_chain = self._build_scene_filter(
                mode=sc["mode"],
                meta=sc["meta"],
                src_w=src_w,
                src_h=src_h,
                target_w=target_w,
                target_h=target_h,
                fg_sharpen=fg_sharpen
            )

            if has_subtitles:
                full_filter = f"{filter_chain};[v_out]ass='{escaped_ass}'[outv]"
                v_map = "[outv]"
            else:
                full_filter = filter_chain
                v_map = "[v_out]"

            cmd = [
                "ffmpeg", "-y",
                "-threads", "0",
                "-i", source_video_path,
                "-filter_complex", full_filter,
                "-map", v_map,
                "-map", "0:a?",
                "-r", f"{src_fps:.3f}",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "18",
                "-maxrate", "60M",
                "-bufsize", "120M",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                output_clip_path
            ]
            logger.info(f"Executing single-scene render ({sc['mode']})...")
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                logger.error(f"FFmpeg render error: {res.stderr}")
                raise RuntimeError(f"Render failed: {res.stderr[-400:]}")

        else:
            # Multi-scene pipeline: render discrete video segments and concatenate with continuous audio
            temp_seg_dir = os.path.join(self.clips_dir, f"temp_{clip_id}_{uuid.uuid4().hex[:6]}")
            os.makedirs(temp_seg_dir, exist_ok=True)
            concat_manifest_path = os.path.join(temp_seg_dir, "concat_list.txt")
            segment_files: List[str] = []

            try:
                for idx, sc in enumerate(scenes_data):
                    seg_out = os.path.join(temp_seg_dir, f"seg_{idx}.mp4")
                    filter_chain = self._build_scene_filter(
                        mode=sc["mode"],
                        meta=sc["meta"],
                        src_w=src_w,
                        src_h=src_h,
                        target_w=target_w,
                        target_h=target_h,
                        fg_sharpen=fg_sharpen
                    )

                    cmd_seg = [
                        "ffmpeg", "-y",
                        "-ss", str(sc["start"]),
                        "-t", str(sc["duration"]),
                        "-i", source_video_path,
                        "-filter_complex", filter_chain,
                        "-map", "[v_out]",
                        "-r", f"{src_fps:.3f}",
                        "-c:v", "libx264",
                        "-preset", "veryfast",
                        "-crf", "18",
                        "-pix_fmt", "yuv420p",
                        "-an",
                        seg_out
                    ]
                    logger.info(f"Rendering scene segment {idx+1}/{len(scenes_data)} ({sc['mode']}, {sc['duration']:.1f}s)...")
                    seg_res = subprocess.run(cmd_seg, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    if seg_res.returncode != 0:
                        logger.error(f"Segment render error: {seg_res.stderr}")
                        raise RuntimeError(f"Segment {idx} render failed: {seg_res.stderr[-300:]}")
                    segment_files.append(seg_out)

                # Write manifest file for FFmpeg concat demuxer
                with open(concat_manifest_path, "w", encoding="utf-8") as f:
                    for seg_path in segment_files:
                        normalized = os.path.abspath(seg_path).replace("\\", "/")
                        f.write(f"file '{normalized}'\n")

                # Concat segments seamlessly and mux with continuous audio track from source video
                if has_subtitles:
                    concat_filter = f"[0:v]ass='{escaped_ass}'[outv]"
                    v_map = "[outv]"
                else:
                    concat_filter = None
                    v_map = "0:v"

                cmd_concat = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", concat_manifest_path,
                    "-i", source_video_path,
                ]
                if concat_filter:
                    cmd_concat.extend(["-filter_complex", concat_filter, "-map", v_map])
                else:
                    cmd_concat.extend(["-map", "0:v"])

                cmd_concat.extend([
                    "-map", "1:a?", # Preserve original continuous audio track! Zero clicks/drift
                    "-r", f"{src_fps:.3f}",
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "18",
                    "-maxrate", "60M",
                    "-bufsize", "120M",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-movflags", "+faststart",
                    output_clip_path
                ])

                logger.info(f"Concatenating {len(segment_files)} video segments with continuous audio...")
                concat_res = subprocess.run(cmd_concat, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if concat_res.returncode != 0:
                    logger.error(f"Concat render error: {concat_res.stderr}")
                    raise RuntimeError(f"Concat failed: {concat_res.stderr[-400:]}")

            finally:
                # Clean up temporary scene segments
                if os.path.exists(temp_seg_dir):
                    try:
                        shutil.rmtree(temp_seg_dir)
                    except Exception as e:
                        logger.warning(f"Could not remove temp scene dir: {e}")

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

        return output_clip_path, thumb_path, srt_path, applied_badge
