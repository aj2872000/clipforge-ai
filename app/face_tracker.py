import os
import cv2
import numpy as np
import logging
from collections import Counter
from typing import Tuple, List, Optional, Dict, Any

logger = logging.getLogger("shorts_creator.face_tracker")

DEFAULT_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models",
    "face_detection_yunet.onnx"
)

class FaceTracker:
    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.detector = None
        if os.path.exists(self.model_path):
            try:
                self.detector = cv2.FaceDetectorYN_create(self.model_path, "", (320, 320))
                logger.info("YuNet face detector initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize YuNet face detector: {e}")
        else:
            logger.warning(f"YuNet model file not found at {self.model_path}. Will fall back to center crop.")

    def detect_faces_in_frame(
        self,
        frame: np.ndarray,
        min_width_ratio: float = 0.05,
        score_thresh: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Detects faces in a frame using YuNet ONNX.
        Filters out tiny background faces (bounding box width < min_width_ratio of frame width).
        Returns list of detected faces sorted left-to-right by center_x.
        """
        if self.detector is None or frame is None:
            return []

        h, w = frame.shape[:2]
        det_w, det_h = min(640, w), min(360, h)
        det_w -= det_w % 2
        det_h -= det_h % 2

        self.detector.setInputSize((det_w, det_h))
        small = cv2.resize(frame, (det_w, det_h))
        _, faces = self.detector.detect(small)

        if faces is None or len(faces) == 0:
            return []

        scale_x = w / float(det_w)
        scale_y = h / float(det_h)
        min_face_w = w * min_width_ratio

        valid_faces = []
        for face in faces:
            fx, fy, fw, fh = face[:4]
            real_fw = fw * scale_x
            real_fh = fh * scale_y
            score = float(face[14])

            # Filter background faces smaller than 5% of frame width
            if score < score_thresh or real_fw < min_face_w:
                continue

            real_fx = fx * scale_x
            real_fy = fy * scale_y
            center_x = real_fx + (real_fw / 2.0)
            center_y = real_fy + (real_fh / 2.0)

            valid_faces.append({
                "center_x": float(center_x),
                "center_y": float(center_y),
                "box": (float(real_fx), float(real_fy), float(real_fw), float(real_fh)),
                "score": score
            })

        # Sort left to right by x coordinates
        valid_faces.sort(key=lambda item: item["center_x"])
        return valid_faces

    def classify_scene(
        self,
        video_path: str,
        start_sec: float,
        end_sec: float,
        num_samples: int = 4
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Per-Scene Face Detection & Classification (Stage 2 of Smart AI Framing):
        - Samples 3-5 evenly spaced frames inside [start_sec, end_sec].
        - Filters out background faces (< 5% frame width).
        - Determines the dominant face count and assigns the scene's framing mode:
            * 0 faces -> 'fit' (full horizontal frame centered on blurred/darkened canvas)
            * 1 face  -> 'fill' (9:16 crop centered on median speaker x)
            * 2 faces -> 'split' (two 1080x960 crops stacked vertically: top=left, bottom=right)
            * 3+ faces -> 'fit' (fallback to fit so nobody is cut off)
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error(f"Cannot open video for classification: {video_path}")
            return "fit", {"dominant_faces": 0}

        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        dur = max(0.1, end_sec - start_sec)

        num_samples = max(3, min(5, num_samples))
        sample_times = [start_sec + dur * (i + 1) / (num_samples + 1) for i in range(num_samples)]

        sampled_counts: List[int] = []
        all_single_centers: List[float] = []
        all_left_centers: List[float] = []
        all_right_centers: List[float] = []

        for st in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, st * 1000.0)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            faces = self.detect_faces_in_frame(frame, min_width_ratio=0.05)
            count = len(faces)
            sampled_counts.append(count)

            if count == 1:
                all_single_centers.append(faces[0]["center_x"])
            elif count >= 2:
                # 2 or more: left speaker and right speaker
                all_left_centers.append(faces[0]["center_x"])
                all_right_centers.append(faces[-1]["center_x"])

        cap.release()

        if not sampled_counts:
            return "fit", {"dominant_faces": 0, "frame_w": w, "frame_h": h}

        # Dominant face count (mode across sampled frames)
        counts_freq = Counter(sampled_counts)
        dominant_count, _ = counts_freq.most_common(1)[0]

        logger.info(
            f"Scene [{start_sec:.1f}s - {end_sec:.1f}s]: sampled counts={sampled_counts} -> dominant={dominant_count}"
        )

        if dominant_count == 1:
            median_x = float(np.median(all_single_centers)) if all_single_centers else (w / 2.0)
            return "fill", {
                "dominant_faces": 1,
                "center_x": median_x,
                "frame_w": w,
                "frame_h": h
            }
        elif dominant_count == 2:
            left_x = float(np.median(all_left_centers)) if all_left_centers else (w * 0.25)
            right_x = float(np.median(all_right_centers)) if all_right_centers else (w * 0.75)
            return "split", {
                "dominant_faces": 2,
                "left_x": left_x,
                "right_x": right_x,
                "frame_w": w,
                "frame_h": h
            }
        else:
            # 0 faces or 3+ faces -> fit
            return "fit", {
                "dominant_faces": dominant_count,
                "frame_w": w,
                "frame_h": h
            }

    def track_two_speakers(
        self,
        video_path: str,
        sample_interval_sec: float = 0.5
    ) -> Tuple[float, float]:
        """
        Tracks the median centers for two speakers (left and right) across the entire video.
        Returns (left_center_x, right_center_x).
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return (480.0, 1440.0)

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_step = max(1, int(fps * sample_interval_sec))

        left_centers: List[float] = []
        right_centers: List[float] = []

        frame_idx = 0
        while frame_idx < total_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            faces = self.detect_faces_in_frame(frame, min_width_ratio=0.05)
            if len(faces) >= 2:
                left_centers.append(faces[0]["center_x"])
                right_centers.append(faces[-1]["center_x"])
            elif len(faces) == 1:
                cx = faces[0]["center_x"]
                if cx < width / 2.0:
                    left_centers.append(cx)
                else:
                    right_centers.append(cx)

            frame_idx += frame_step

        cap.release()

        left_x = float(np.median(left_centers)) if left_centers else (width * 0.25)
        right_x = float(np.median(right_centers)) if right_centers else (width * 0.75)
        return (left_x, right_x)

    def track_speaker_center(
        self,
        video_path: str,
        sample_interval_sec: float = 0.5
    ) -> Tuple[int, int, int, int]:
        """
        Analyzes video frames to track the primary speaker's horizontal center.
        Returns (crop_x, crop_y, crop_w, crop_h) suitable for a 9:16 vertical crop.
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error(f"Cannot open video {video_path}")
            return (0, 0, 1080, 1920)

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # Target 9:16 aspect ratio inside input video
        target_aspect = 9.0 / 16.0
        crop_h = height
        crop_w = int(crop_h * target_aspect)
        crop_w -= (crop_w % 2)

        default_x = max(0, (width - crop_w) // 2)

        if not self.detector or crop_w >= width:
            cap.release()
            return (default_x, 0, crop_w, crop_h)

        frame_step = max(1, int(fps * sample_interval_sec))
        detected_centers: List[float] = []

        frame_idx = 0
        while frame_idx < total_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            faces = self.detect_faces_in_frame(frame, min_width_ratio=0.05)
            if faces:
                # Pick face with largest area as primary speaker
                best_face = max(faces, key=lambda f: f["box"][2] * f["box"][3])
                detected_centers.append(best_face["center_x"])

            frame_idx += frame_step

        cap.release()

        if detected_centers:
            median_center = float(np.median(detected_centers))
            target_x = int(median_center - (crop_w / 2.0))
            clamped_x = max(0, min(width - crop_w, target_x))
            clamped_x -= (clamped_x % 2)
            logger.info(f"Speaker tracked: median X={median_center:.1f}, crop X={clamped_x}")
            return (clamped_x, 0, crop_w, crop_h)

        logger.info(f"No face detected consistently, using center crop X={default_x}")
        return (default_x, 0, crop_w, crop_h)
