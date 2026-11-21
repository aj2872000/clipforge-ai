import os
import uuid
import asyncio
import logging
from typing import Dict, Any, List
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.models import (
    AnalyzeRequest,
    RenderClipRequest,
    GenerateAllRequest,
    ClipCandidate,
    ClipResponse,
    JobStatus,
    VideoMetadata
)
from app.transcriber import (
    extract_video_id,
    fetch_video_metadata,
    fetch_transcript,
    download_video_segment
)
from app.virality_engine import detect_clips
from app.video_processor import VideoProcessor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("shorts_creator")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
CLIPS_DIR = os.path.join(STORAGE_DIR, "clips")
DOWNLOADS_DIR = os.path.join(STORAGE_DIR, "downloads")
SUBTITLES_DIR = os.path.join(STORAGE_DIR, "subtitles")

os.makedirs(CLIPS_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(SUBTITLES_DIR, exist_ok=True)

app = FastAPI(title="ClipForge - AI Shorts Creator", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

processor = VideoProcessor(base_dir=STORAGE_DIR)

# In-memory job state store
jobs: Dict[str, JobStatus] = {}

def update_job(job_id: str, status: str, progress: int, step: str, message: str, log: str = None):
    if job_id in jobs:
        j = jobs[job_id]
        j.status = status
        j.progress = progress
        j.current_step = step
        j.message = message
        if log:
            j.logs.append(log)
            logger.info(f"[{job_id}] {log}")

@app.post("/api/analyze")
async def analyze_video(req: AnalyzeRequest):
    """Fetch video metadata and detect viral shorts clips."""
    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or Video ID.")

    try:
        logger.info(f"Analyzing YouTube video: {req.url}")
        meta = fetch_video_metadata(req.url)
        segments = fetch_transcript(video_id, download_dir=DOWNLOADS_DIR)

        if not segments:
            raise HTTPException(
                status_code=400,
                detail="Could not retrieve transcript/subtitles for this video. Please ensure the video has English audio or captions."
            )

        candidates = detect_clips(
            segments=segments,
            target_duration=req.target_duration or "30-60",
            max_clips=req.max_clips or 3,
            provider=req.ai_provider or "heuristic",
            api_key=req.api_key
        )

        return {
            "metadata": meta,
            "clips": candidates,
            "total_segments": len(segments)
        }
    except Exception as e:
        logger.error(f"Error during video analysis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

async def run_render_pipeline(
    job_id: str,
    url: str,
    video_id: str,
    candidates: List[ClipCandidate],
    layout: str,
    caption_style: str
):
    try:
        update_job(job_id, "downloading", 15, "Download", "Downloading required video segments...", "Starting segment downloads via yt-dlp...")

        rendered_clips: List[ClipResponse] = []
        total_clips = len(candidates)

        for idx, candidate in enumerate(candidates):
            step_pct = 20 + int((idx / total_clips) * 70)
            update_job(
                job_id,
                "rendering",
                step_pct,
                f"Rendering Clip {idx+1}/{total_clips}",
                f"Generating '{candidate.title}' (9:16 {layout})...",
                f"Processing clip {idx+1}/{total_clips}: {candidate.start_time:.1f}s - {candidate.end_time:.1f}s"
            )

            # 1. Download trimmed video segment
            segment_raw = os.path.join(DOWNLOADS_DIR, f"{video_id}_{candidate.id}_raw.mp4")
            actual_raw_path = download_video_segment(url, segment_raw, candidate.start_time, candidate.end_time)

            # 2. Render 9:16 vertical short with chosen layout and burning karaoke captions
            final_clip_filename = f"{video_id}_{candidate.id}.mp4"
            final_clip_path = os.path.join(CLIPS_DIR, final_clip_filename)

            processor.render_short_clip(
                source_video_path=actual_raw_path,
                output_clip_path=final_clip_path,
                words=candidate.words or [],
                layout=layout,
                caption_style=caption_style,
                clip_id=candidate.id,
                start_time_offset=candidate.start_time
            )

            # Clean up intermediate raw segment
            if os.path.exists(actual_raw_path):
                try:
                    os.remove(actual_raw_path)
                except Exception:
                    pass

            rendered_clips.append(ClipResponse(
                id=candidate.id,
                title=candidate.title,
                virality_score=candidate.virality_score,
                hook=candidate.hook,
                explanation=candidate.explanation,
                duration=candidate.duration,
                start_time=candidate.start_time,
                end_time=candidate.end_time,
                video_url=f"/api/stream/{final_clip_filename}",
                download_url=f"/api/download/{final_clip_filename}",
                thumbnail_url=f"/api/stream/{os.path.splitext(final_clip_filename)[0]}.jpg",
                subtitles_url=f"/api/subtitle/{candidate.id}.srt",
                hashtags=candidate.hashtags or ["#shorts", "#viral"]
            ))

        jobs[job_id].clips = rendered_clips
        update_job(job_id, "completed", 100, "Finished", f"Successfully created {len(rendered_clips)} viral shorts!", "All clips rendered and ready for download.")
    except Exception as e:
        logger.error(f"Render pipeline failed for job {job_id}: {e}", exc_info=True)
        jobs[job_id].error = str(e)
        update_job(job_id, "failed", 100, "Error", f"Failed: {str(e)}", f"Exception: {str(e)}")

@app.post("/api/generate-all")
async def generate_all(req: GenerateAllRequest, background_tasks: BackgroundTasks):
    """End-to-End Opus Clip workflow: Analyze -> Select Top Moments -> Render 9:16 Shorts with Captions."""
    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL.")

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = JobStatus(
        job_id=job_id,
        status="analyzing",
        progress=5,
        current_step="Metadata & Transcripts",
        message="Extracting video audio and transcript...",
        logs=[f"Initialized job {job_id} for {req.url}"]
    )

    async def orchestrate():
        try:
            update_job(job_id, "analyzing", 10, "AI Analysis", "Curating viral moments & calculating hooks...", "Fetching transcripts and analyzing hooks...")
            segments = fetch_transcript(video_id, download_dir=DOWNLOADS_DIR)
            if not segments:
                raise RuntimeError("No transcript or auto-captions could be extracted for this video.")

            candidates = detect_clips(
                segments=segments,
                target_duration=req.target_duration or "30-60",
                max_clips=req.max_clips or 3,
                provider=req.ai_provider or "heuristic",
                api_key=req.api_key
            )

            if not candidates:
                raise RuntimeError("No suitable narrative clips could be identified from the transcript.")

            update_job(job_id, "analyzing", 20, "Clip Selection", f"Selected {len(candidates)} high-virality clips.", f"Selected clips: {[c.title for c in candidates]}")

            await run_render_pipeline(
                job_id=job_id,
                url=req.url,
                video_id=video_id,
                candidates=candidates,
                layout=req.layout or "blur_fill",
                caption_style=req.caption_style or "hormozi"
            )
        except Exception as e:
            logger.error(f"Orchestration error: {e}", exc_info=True)
            jobs[job_id].error = str(e)
            update_job(job_id, "failed", 100, "Failed", str(e), f"Fatal error: {str(e)}")

    background_tasks.add_task(orchestrate)
    return {"job_id": job_id, "status": "queued"}

@app.post("/api/render-clip")
async def render_single_clip(req: RenderClipRequest, background_tasks: BackgroundTasks):
    """Render a single specific clip with customized settings."""
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = JobStatus(
        job_id=job_id,
        status="queued",
        progress=0,
        current_step="Queued",
        message="Queued for rendering...",
        logs=[f"Single clip render job {job_id} created."]
    )

    candidate = ClipCandidate(
        id=f"custom_{job_id}",
        title=req.title,
        hook=req.title,
        explanation="Custom rendered short clip.",
        virality_score=90,
        hook_score=90,
        flow_score=90,
        start_time=req.start_time,
        end_time=req.end_time,
        duration=req.end_time - req.start_time,
        transcript=" ".join([w.word for w in req.words]) if req.words else "",
        words=req.words or []
    )

    async def render_task():
        await run_render_pipeline(
            job_id=job_id,
            url=req.url,
            video_id=req.video_id,
            candidates=[candidate],
            layout=req.layout or "blur_fill",
            caption_style=req.caption_style or "hormozi"
        )

    background_tasks.add_task(render_task)
    return {"job_id": job_id}

@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found.")
    return jobs[job_id]

@app.get("/api/stream/{filename}")
async def stream_media(filename: str):
    clip_path = os.path.join(CLIPS_DIR, filename)
    if not os.path.exists(clip_path):
        raise HTTPException(status_code=404, detail="File not found.")
    media_type = "video/mp4" if filename.endswith(".mp4") else "image/jpeg"
    return FileResponse(clip_path, media_type=media_type)

@app.get("/api/download/{filename}")
async def download_clip(filename: str):
    clip_path = os.path.join(CLIPS_DIR, filename)
    if not os.path.exists(clip_path):
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(clip_path, media_type="video/mp4", filename=filename)

@app.get("/api/subtitle/{filename}")
async def download_sub(filename: str):
    sub_path = os.path.join(SUBTITLES_DIR, filename)
    if not os.path.exists(sub_path):
        raise HTTPException(status_code=404, detail="Subtitle not found.")
    return FileResponse(sub_path, media_type="text/plain", filename=filename)

@app.post("/api/sample-demo")
async def generate_sample_demo(background_tasks: BackgroundTasks):
    """
    Generate an instant sample demo short so the user can test all features
    and view the vertical player without downloading an external video!
    """
    job_id = "demo_" + str(uuid.uuid4())[:6]
    jobs[job_id] = JobStatus(
        job_id=job_id,
        status="rendering",
        progress=30,
        current_step="Generating Demo",
        message="Synthesizing vertical demo short with karaoke captions...",
        logs=["Creating synthetic 16:9 test stream..."]
    )

    async def create_demo():
        demo_raw = os.path.join(DOWNLOADS_DIR, f"{job_id}_raw.mp4")
        demo_out = os.path.join(CLIPS_DIR, f"{job_id}.mp4")

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=6:size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-c:v", "libx264", "-c:a", "aac",
            demo_raw
        ]
        import subprocess
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        demo_words = [
            {"word": "THIS", "start": 0.5, "end": 1.1},
            {"word": "IS", "start": 1.1, "end": 1.6},
            {"word": "THE", "start": 1.6, "end": 2.1},
            {"word": "ULTIMATE", "start": 2.1, "end": 2.8},
            {"word": "SHORTS", "start": 2.8, "end": 3.6},
            {"word": "CREATOR", "start": 3.6, "end": 4.5},
            {"word": "LIKE", "start": 4.5, "end": 5.0},
            {"word": "OPUS", "start": 5.0, "end": 5.5},
            {"word": "CLIP!", "start": 5.5, "end": 6.0},
        ]
        from app.models import TranscriptWord
        words_objs = [TranscriptWord(**w) for w in demo_words]

        update_job(job_id, "rendering", 70, "Rendering 9:16", "Burning Hormozi captions & 9:16 blur framing...", "Rendering demo video...")

        processor.render_short_clip(
            source_video_path=demo_raw,
            output_clip_path=demo_out,
            words=words_objs,
            layout="blur_fill",
            caption_style="hormozi",
            clip_id=job_id,
            start_time_offset=0.0
        )

        if os.path.exists(demo_raw):
            try:
                os.remove(demo_raw)
            except Exception:
                pass

        jobs[job_id].clips = [ClipResponse(
            id=job_id,
            title="The Ultimate Shorts Secret Revealed!",
            virality_score=98,
            hook="This is the ultimate shorts creator like Opus Clip!",
            explanation="Maximum retention hook + high-energy Hormozi style pop karaoke captions.",
            duration=6.0,
            start_time=0.0,
            end_time=6.0,
            video_url=f"/api/stream/{job_id}.mp4",
            download_url=f"/api/download/{job_id}.mp4",
            thumbnail_url=f"/api/stream/{job_id}.jpg",
            subtitles_url=f"/api/subtitle/{job_id}.srt",
            hashtags=["#shorts", "#opusclip", "#viral", "#videoediting", "#ai"]
        )]

        update_job(job_id, "completed", 100, "Done", "Demo short generated successfully!", "Demo complete.")

    background_tasks.add_task(create_demo)
    return {"job_id": job_id}

# Mount static web UI files
static_dir = os.path.join(BASE_DIR, "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
