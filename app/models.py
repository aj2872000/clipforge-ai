from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class TranscriptWord(BaseModel):
    word: str
    start: float
    end: float

class TranscriptSegment(BaseModel):
    text: str
    start: float
    duration: float
    words: Optional[List[TranscriptWord]] = []

class VideoMetadata(BaseModel):
    id: str
    url: str
    title: str
    author: str
    duration: float
    thumbnail: str
    description: Optional[str] = ""

class ClipCandidate(BaseModel):
    id: str
    title: str
    hook: str
    explanation: str
    virality_score: int
    hook_score: int
    flow_score: int
    start_time: float
    end_time: float
    duration: float
    transcript: str
    words: Optional[List[TranscriptWord]] = []
    hashtags: Optional[List[str]] = []

class AnalyzeRequest(BaseModel):
    url: str
    target_duration: Optional[str] = "30-60" # "15-30", "30-60", "60-90", "auto"
    max_clips: Optional[int] = 3
    ai_provider: Optional[str] = "heuristic" # "heuristic", "gemini", "openai"
    api_key: Optional[str] = None
    custom_transcript: Optional[str] = None

class RenderClipRequest(BaseModel):
    url: str
    video_id: str
    start_time: float
    end_time: float
    title: str
    words: Optional[List[TranscriptWord]] = []
    layout: Optional[str] = "blur_fill" # "blur_fill", "face_crop", "center_crop"
    caption_style: Optional[str] = "hormozi" # "hormozi", "beast", "minimal", "neon"
    video_quality: Optional[str] = "max" # "max", "1080p", "720p"
    upscale_enabled: Optional[bool] = True

class GenerateAllRequest(BaseModel):
    url: str
    target_duration: Optional[str] = "30-60"
    max_clips: Optional[int] = 3
    layout: Optional[str] = "blur_fill"
    caption_style: Optional[str] = "hormozi"
    ai_provider: Optional[str] = "heuristic"
    api_key: Optional[str] = None
    custom_transcript: Optional[str] = None
    video_quality: Optional[str] = "max"
    upscale_enabled: Optional[bool] = True

class ClipResponse(BaseModel):
    id: str
    title: str
    virality_score: int
    hook: str
    explanation: str
    duration: float
    start_time: float
    end_time: float
    video_url: Optional[str] = ""
    download_url: Optional[str] = ""
    thumbnail_url: Optional[str] = None
    subtitles_url: Optional[str] = None
    hashtags: List[str] = []
    status: Optional[str] = "completed" # "queued", "rendering", "completed", "failed"

class JobStatus(BaseModel):
    job_id: str
    status: str # "queued", "downloading", "analyzing", "rendering", "completed", "failed"
    progress: int # 0 - 100
    current_step: str
    message: str
    logs: List[str] = []
    clips: List[ClipResponse] = []
    error: Optional[str] = None

class YouTubeUploadRequest(BaseModel):
    clip_id: str
    filename: str
    title: str
    description: Optional[str] = ""
    tags: Optional[List[str]] = []
    category_id: Optional[str] = "24"
    privacy_status: Optional[str] = "private"
    made_for_kids: Optional[bool] = False

class YouTubeUploadStatus(BaseModel):
    task_id: str
    clip_id: str
    status: str # "queued", "uploading", "completed", "failed"
    progress: int = 0 # 0 - 100
    bytes_uploaded: int = 0
    total_bytes: int = 0
    video_id: Optional[str] = None
    video_url: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None
