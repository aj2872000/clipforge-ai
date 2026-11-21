import os
import cv2
import numpy as np
import logging
from typing import Tuple, List, Optional

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

        # Calculate target 9:16 dimensions inside input video
        target_aspect = 9.0 / 16.0
        crop_h = height
        crop_w = int(crop_h * target_aspect)
        # Ensure even numbers
        crop_w = crop_w - (crop_w % 2)

        default_x = max(0, (width - crop_w) // 2)

        if not self.detector or crop_w >= width:
            cap.release()
            return (default_x, 0, crop_w, crop_h)

        frame_step = max(1, int(fps * sample_interval_sec))
        detected_centers: List[float] = []

        # Resize dimensions for fast face detection
        det_w, det_h = min(640, width), min(360, height)
        det_w = det_w - (det_w % 2)
        det_h = det_h - (det_h % 2)
        self.detector.setInputSize((det_w, det_h))

        frame_idx = 0
        while frame_idx < total_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            small_frame = cv2.resize(frame, (det_w, det_h))
            _, faces = self.detector.detect(small_frame)

            if faces is not None and len(faces) > 0:
                # Pick face with largest area
                best_face = None
                max_area = 0
                for face in faces:
                    fx, fy, fw, fh = face[:4]
                    area = fw * fh
                    if area > max_area:
                        max_area = area
                        best_face = face

                if best_face is not None:
                    scale_x = width / float(det_w)
                    face_center_x = (best_face[0] + best_face[2] / 2.0) * scale_x
                    detected_centers.append(face_center_x)

            frame_idx += frame_step

        cap.release()

        if detected_centers:
            # Use trimmed mean / median to eliminate outliers
            median_center = float(np.median(detected_centers))
            # Optimal crop x: center around speaker
            target_x = int(median_center - (crop_w / 2.0))
            clamped_x = max(0, min(width - crop_w, target_x))
            logger.info(f"Speaker tracked: median X={median_center:.1f}, crop X={clamped_x}")
            return (clamped_x, 0, crop_w, crop_h)

        logger.info(f"No face detected consistently, using center crop X={default_x}")
        return (default_x, 0, crop_w, crop_h)
