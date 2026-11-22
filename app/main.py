import os
import uuid
import time
import asyncio
import logging
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.models import (
    AnalyzeRequest,
    RenderClipRequest,
    ReframeClipRequest,
    Upscale4KRequest,
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

def load_env_file():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and v and k not in os.environ:
                            os.environ[k] = v
        except Exception as e:
            logger.warning(f"Could not load .env file: {e}")

load_env_file()
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
def analyze_video(req: AnalyzeRequest):
    """Fetch video metadata and detect viral shorts clips."""
    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or Video ID.")

    try:
        logger.info(f"Analyzing YouTube video: {req.url}")
        meta = fetch_video_metadata(req.url)
        segments, err_msg = fetch_transcript(
            video_id,
            download_dir=DOWNLOADS_DIR,
            custom_transcript=req.custom_transcript,
            api_key=req.api_key
        )

        if not segments:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Could not retrieve transcripts from YouTube: {err_msg}. "
                    "Tip: You can paste the transcript directly in the 'Custom Transcript' field."
                )
            )

        candidates = detect_clips(
            segments=segments,
            target_duration=req.target_duration or "30-60",
            max_clips=req.max_clips or 3,
            provider=req.ai_provider or "gemini",
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

def run_render_pipeline(
    job_id: str,
    url: str,
    video_id: str,
    candidates: List[ClipCandidate],
    layout: str,
    caption_style: str
):
    try:
        total_clips = len(candidates)

        # Initialize the progressive processing queue with all candidate clips
        queue_clips: List[ClipResponse] = []
        for idx, candidate in enumerate(candidates):
            queue_clips.append(ClipResponse(
                id=candidate.id,
                title=candidate.title,
                virality_score=candidate.virality_score,
                hook=candidate.hook,
                explanation=candidate.explanation,
                duration=candidate.duration,
                start_time=candidate.start_time,
                end_time=candidate.end_time,
                video_url="",
                download_url="",
                thumbnail_url=None,
                subtitles_url=None,
                hashtags=candidate.hashtags or ["#shorts", "#viral"],
                status="queued"
            ))

        jobs[job_id].clips = list(queue_clips)
        update_job(
            job_id,
            "rendering",
            20,
            "Queue Initialized",
            f"Queued {total_clips} viral shorts. Processing clips progressively...",
            f"Progressive queue started with {total_clips} clips."
        )

        for idx, candidate in enumerate(candidates):
            # Mark current clip as actively rendering
            queue_clips[idx].status = "rendering"
            jobs[job_id].clips = list(queue_clips)

            step_pct = 20 + int((idx / total_clips) * 75)
            update_job(
                job_id,
                "rendering",
                step_pct,
                f"Rendering Clip {idx+1}/{total_clips}",
                f"Generating '{candidate.title}' (Max Quality, min 1080p)...",
                f"Processing clip {idx+1}/{total_clips}: {candidate.start_time:.1f}s - {candidate.end_time:.1f}s"
            )

            # 1. Download trimmed video segment at max quality available
            segment_raw = os.path.join(DOWNLOADS_DIR, f"{video_id}_{candidate.id}_raw.mp4")
            actual_raw_path = download_video_segment(
                url=url,
                output_path=segment_raw,
                start_time=candidate.start_time,
                end_time=candidate.end_time
            )

            # 2. Render 9:16 vertical short in Ultra-HD (1440p/4K) with Smart AI Framing
            final_clip_filename = f"{video_id}_{candidate.id}.mp4"
            final_clip_path = os.path.join(CLIPS_DIR, final_clip_filename)

            _, _, _, applied_badge = processor.render_short_clip(
                source_video_path=actual_raw_path,
                output_clip_path=final_clip_path,
                words=candidate.words or [],
                layout=layout or "auto",
                caption_style=caption_style,
                clip_id=candidate.id,
                start_time_offset=candidate.start_time
            )

            # 3. Immediately mark THIS clip as completed and expose download & stream URLs!
            queue_clips[idx].status = "completed"
            queue_clips[idx].video_url = f"/api/stream/{final_clip_filename}?_t={int(time.time())}"
            queue_clips[idx].download_url = f"/api/download/{final_clip_filename}"
            queue_clips[idx].thumbnail_url = f"/api/stream/{os.path.splitext(final_clip_filename)[0]}.jpg?_t={int(time.time())}"
            queue_clips[idx].subtitles_url = f"/api/subtitle/{candidate.id}.srt"
            queue_clips[idx].framing_mode = layout or "auto"
            queue_clips[idx].applied_framing = applied_badge
            queue_clips[idx].url = url
            queue_clips[idx].video_id = video_id

            # Expose updated progressive list immediately in job state
            jobs[job_id].clips = list(queue_clips)
            ready_count = idx + 1
            update_job(
                job_id,
                "rendering",
                20 + int((ready_count / total_clips) * 75),
                f"Short #{ready_count} Ready for Download",
                f"Short #{ready_count} is ready to download! Remaining clips processing in background...",
                f"✓ Short #{ready_count} ('{candidate.title}') rendered & download enabled."
            )

        jobs[job_id].clips = list(queue_clips)
        update_job(job_id, "completed", 100, "Finished", f"Successfully created all {total_clips} viral shorts!", "All clips rendered and ready for download.")
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

    def orchestrate():
        try:
            update_job(job_id, "analyzing", 10, "AI Analysis", "Curating viral moments & calculating hooks...", "Fetching transcripts and analyzing hooks...")
            segments, err_msg = fetch_transcript(
                video_id,
                download_dir=DOWNLOADS_DIR,
                custom_transcript=req.custom_transcript,
                api_key=req.api_key
            )
            if not segments:
                raise RuntimeError(
                    f"Could not retrieve captions from YouTube ({err_msg}). "
                    "YouTube often blocks datacenter/cloud IPs. "
                    "Fix: Paste the video transcript into the 'Custom Transcript' field in the UI."
                )

            candidates = detect_clips(
                segments=segments,
                target_duration=req.target_duration or "30-60",
                max_clips=req.max_clips or 3,
                provider=req.ai_provider or "gemini",
                api_key=req.api_key
            )

            if not candidates:
                raise RuntimeError("No suitable narrative clips could be identified from the transcript.")

            update_job(job_id, "analyzing", 20, "Clip Selection", f"Selected {len(candidates)} high-virality clips.", f"Selected clips: {[c.title for c in candidates]}")

            run_render_pipeline(
                job_id=job_id,
                url=req.url,
                video_id=video_id,
                candidates=candidates,
                layout=req.layout or "auto",
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

    def render_task():
        run_render_pipeline(
            job_id=job_id,
            url=req.url,
            video_id=req.video_id,
            candidates=[candidate],
            layout=req.layout or "auto",
            caption_style=req.caption_style or "hormozi"
        )

    background_tasks.add_task(render_task)
    return {"job_id": job_id}

reframe_tasks: Dict[str, Dict[str, Any]] = {}

@app.post("/api/reframe-clip")
async def reframe_clip_endpoint(req: ReframeClipRequest, background_tasks: BackgroundTasks):
    """Re-render an individual clip with a forced manual override (Fill, Fit, Split, Screenshare) or Auto."""
    task_id = str(uuid.uuid4())[:8]
    reframe_tasks[task_id] = {
        "status": "rendering",
        "clip_id": req.clip_id,
        "message": f"Re-framing clip with {req.layout.upper()}...",
        "clip": None,
        "error": None
    }

    def do_reframe():
        try:
            logger.info(f"Reframe task {task_id} started for clip {req.clip_id} with layout={req.layout}")
            raw_path = os.path.join(DOWNLOADS_DIR, f"{req.video_id}_{req.clip_id}_raw.mp4")

            # If raw segment was cleaned up or missing, fetch it directly
            if not os.path.exists(raw_path):
                if not req.url:
                    raise RuntimeError("Source URL missing to re-download segment.")
                logger.info(f"Re-fetching raw video segment for reframe: {req.start_time}s - {req.end_time}s")
                raw_path = download_video_segment(
                    url=req.url,
                    output_path=raw_path,
                    start_time=req.start_time,
                    end_time=req.end_time
                )

            final_clip_filename = f"{req.video_id}_{req.clip_id}.mp4"
            final_clip_path = os.path.join(CLIPS_DIR, final_clip_filename)

            _, _, _, applied_badge = processor.render_short_clip(
                source_video_path=raw_path,
                output_clip_path=final_clip_path,
                words=req.words or [],
                layout=req.layout,
                caption_style=req.caption_style or "hormozi",
                clip_id=req.clip_id,
                start_time_offset=req.start_time
            )

            updated_clip = ClipResponse(
                id=req.clip_id,
                title=req.title or "Short Clip",
                virality_score=95,
                hook=req.title or "",
                explanation=f"Re-framed with {req.layout.capitalize()}.",
                duration=req.end_time - req.start_time,
                start_time=req.start_time,
                end_time=req.end_time,
                video_url=f"/api/stream/{final_clip_filename}?_t={int(time.time())}",
                download_url=f"/api/download/{final_clip_filename}",
                thumbnail_url=f"/api/stream/{os.path.splitext(final_clip_filename)[0]}.jpg?_t={int(time.time())}",
                subtitles_url=f"/api/subtitle/{req.clip_id}.srt",
                status="completed",
                framing_mode=req.layout,
                applied_framing=applied_badge,
                url=req.url,
                video_id=req.video_id
            )

            reframe_tasks[task_id]["status"] = "completed"
            reframe_tasks[task_id]["clip"] = updated_clip.dict()
            reframe_tasks[task_id]["message"] = f"Clip successfully re-framed to {applied_badge}!"
            logger.info(f"Reframe task {task_id} finished successfully: {applied_badge}")
        except Exception as e:
            logger.error(f"Reframe error for task {task_id}: {e}", exc_info=True)
            reframe_tasks[task_id]["status"] = "failed"
            reframe_tasks[task_id]["error"] = str(e)

    background_tasks.add_task(do_reframe)
    return {"task_id": task_id, "status": "rendering"}

@app.get("/api/reframe-status/{task_id}")
def get_reframe_status_endpoint(task_id: str):
    if task_id not in reframe_tasks:
        raise HTTPException(status_code=404, detail="Reframe task not found")
    return reframe_tasks[task_id]

upscale_tasks: Dict[str, Dict[str, Any]] = {}

@app.post("/api/upscale-4k")
async def upscale_4k_endpoint(req: Upscale4KRequest, background_tasks: BackgroundTasks):
    """Further upscale an individual clip to 4K UHD (2160x3840) with Lanczos & adaptive sharpening."""
    task_id = str(uuid.uuid4())[:8]
    upscale_tasks[task_id] = {
        "status": "processing",
        "clip_id": req.clip_id,
        "message": "Upscaling clip to 4K Ultra-HD (2160x3840)...",
        "clip": None,
        "error": None
    }

    def do_upscale():
        try:
            logger.info(f"4K upscale task {task_id} initiated for clip {req.clip_id}")
            raw_path = os.path.join(DOWNLOADS_DIR, f"{req.video_id}_{req.clip_id}_raw.mp4")
            existing_clip_path = os.path.join(CLIPS_DIR, f"{req.video_id}_{req.clip_id}.mp4")
            out_4k_filename = f"{req.video_id}_{req.clip_id}_4k.mp4"
            out_4k_path = os.path.join(CLIPS_DIR, out_4k_filename)

            applied_badge = "💎 4K UHD (2160x3840)"

            if os.path.exists(raw_path):
                logger.info(f"Native 4K rendering from raw source for clip {req.clip_id}...")
                _, thumb_path, srt_path, framing_badge = processor.render_short_clip(
                    source_video_path=raw_path,
                    output_clip_path=out_4k_path,
                    words=[],
                    layout=req.layout or "auto",
                    caption_style=req.caption_style or "hormozi",
                    clip_id=req.clip_id,
                    start_time_offset=req.start_time,
                    force_4k=True
                )
                applied_badge = f"{framing_badge} • 4K UHD"
            elif os.path.exists(existing_clip_path):
                logger.info(f"Direct 4K Lanczos upscaling from rendered clip for {req.clip_id}...")
                processor.upscale_to_4k(existing_clip_path, out_4k_path)
            elif req.url:
                logger.info(f"Downloading raw segment to render 4K for {req.clip_id}...")
                raw_path = download_video_segment(
                    url=req.url,
                    output_path=raw_path,
                    start_time=req.start_time,
                    end_time=req.end_time
                )
                _, thumb_path, srt_path, framing_badge = processor.render_short_clip(
                    source_video_path=raw_path,
                    output_clip_path=out_4k_path,
                    words=[],
                    layout=req.layout or "auto",
                    caption_style=req.caption_style or "hormozi",
                    clip_id=req.clip_id,
                    start_time_offset=req.start_time,
                    force_4k=True
                )
                applied_badge = f"{framing_badge} • 4K UHD"
            else:
                raise RuntimeError("No source video or existing clip found to upscale to 4K.")

            duration = max(1.0, req.end_time - req.start_time)
            updated_clip = ClipResponse(
                id=req.clip_id,
                title=req.title or "4K Short",
                virality_score=99,
                hook=req.title or "",
                explanation="Enhanced to 4K Ultra-HD (2160x3840) with Lanczos sharpening.",
                duration=duration,
                start_time=req.start_time,
                end_time=req.end_time,
                video_url=f"/api/stream/{out_4k_filename}?_t={int(time.time())}",
                download_url=f"/api/download/{out_4k_filename}",
                thumbnail_url=f"/api/stream/{os.path.splitext(out_4k_filename)[0]}.jpg?_t={int(time.time())}",
                subtitles_url=f"/api/subtitle/{req.clip_id}.srt",
                status="completed",
                framing_mode=req.layout or "auto",
                applied_framing=applied_badge,
                url=req.url,
                video_id=req.video_id,
                is_4k=True,
                resolution="4K UHD (2160x3840)"
            )

            upscale_tasks[task_id]["status"] = "completed"
            upscale_tasks[task_id]["clip"] = updated_clip.dict()
            upscale_tasks[task_id]["message"] = "Clip upscaled to 4K UHD successfully!"
            logger.info(f"4K upscale task {task_id} completed successfully for {req.clip_id}!")
        except Exception as e:
            logger.error(f"4K upscale error for task {task_id}: {e}", exc_info=True)
            upscale_tasks[task_id]["status"] = "failed"
            upscale_tasks[task_id]["error"] = str(e)

    background_tasks.add_task(do_upscale)
    return {"task_id": task_id, "status": "processing"}

@app.get("/api/upscale-status/{task_id}")
def get_upscale_status_endpoint(task_id: str):
    if task_id not in upscale_tasks:
        raise HTTPException(status_code=404, detail="Upscale task not found")
    return upscale_tasks[task_id]

@app.get("/api/config")
def get_config():
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    has_key = bool(gemini_key and len(gemini_key.strip()) > 5)
    return {
        "has_gemini_key": has_key,
        "default_provider": "gemini"
    }

@app.post("/api/config/key")
def save_api_key(payload: Dict[str, str]):
    key = payload.get("gemini_api_key", "").strip()
    if key:
        os.environ["GEMINI_API_KEY"] = key
        # Persist to .env file
        env_path = os.path.join(BASE_DIR, ".env")
        try:
            lines = []
            key_set = False
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip().startswith("GEMINI_API_KEY="):
                            lines.append(f"GEMINI_API_KEY={key}\n")
                            key_set = True
                        else:
                            lines.append(line)
            if not key_set:
                lines.append(f"GEMINI_API_KEY={key}\n")
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            return {"status": "saved", "message": "Gemini API key saved to .env"}
        except Exception as e:
            return {"status": "memory_only", "message": f"Saved in session: {e}"}
    return {"status": "error", "message": "Empty key"}

@app.get("/api/jobs/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str, response: Response):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found.")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
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

    def create_demo():
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

        _, _, _, demo_badge = processor.render_short_clip(
            source_video_path=demo_raw,
            output_clip_path=demo_out,
            words=words_objs,
            layout="auto",
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
            hashtags=["#shorts", "#opusclip", "#viral", "#videoediting", "#ai"],
            framing_mode="auto",
            applied_framing=demo_badge or "Auto • Fill"
        )]

        update_job(job_id, "completed", 100, "Done", "Demo short generated successfully!", "Demo complete.")

    background_tasks.add_task(create_demo)
    return {"job_id": job_id}

# Mount static web UI files
static_dir = os.path.join(BASE_DIR, "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
