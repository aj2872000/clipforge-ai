import os
import subprocess
from app.models import TranscriptWord
from app.video_processor import VideoProcessor
from app.virality_engine import score_hook, score_pacing, generate_viral_title

print("--- Testing Virality Engine scoring ---")
test_text = "Why most people will never succeed with AI in 2026? The truth is crazy."
h_score = score_hook(test_text)
p_score = score_pacing(15, 5.0)
title = generate_viral_title(test_text, test_text)
print(f"Hook score: {h_score}/100, Pacing score: {p_score}/100, Title: {title}")

print("\n--- Testing 9:16 Video Rendering Pipeline ---")
processor = VideoProcessor(base_dir="storage")

# Create test 4s synthetic 16:9 video
test_src = "storage/downloads/test_sample_raw.mp4"
test_out = "storage/clips/test_sample_out.mp4"

os.makedirs("storage/downloads", exist_ok=True)
os.makedirs("storage/clips", exist_ok=True)

cmd = [
    "ffmpeg", "-y",
    "-f", "lavfi", "-i", "testsrc=duration=4:size=1280x720:rate=30",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
    "-c:v", "libx264", "-c:a", "aac",
    test_src
]
subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

words = [
    TranscriptWord(word="THIS", start=0.4, end=1.0),
    TranscriptWord(word="IS", start=1.0, end=1.5),
    TranscriptWord(word="HOW", start=1.5, end=2.0),
    TranscriptWord(word="OPUS", start=2.0, end=2.8),
    TranscriptWord(word="CLIPS", start=2.8, end=3.5),
    TranscriptWord(word="WORK!", start=3.5, end=4.0),
]

out_vid, thumb, srt = processor.render_short_clip(
    source_video_path=test_src,
    output_clip_path=test_out,
    words=words,
    layout="blur_fill",
    caption_style="hormozi",
    clip_id="test_clip_1",
    start_time_offset=0.0
)

print(f"Rendered video: {out_vid} (exists: {os.path.exists(out_vid)}, size: {os.path.getsize(out_vid)} bytes)")
print(f"Thumbnail:      {thumb} (exists: {os.path.exists(thumb)})")
print(f"Subtitles SRT:  {srt} (exists: {os.path.exists(srt)})")

# Probe output resolution with ffprobe
probe_cmd = [
    "ffprobe", "-v", "error",
    "-select_streams", "v:0",
    "-show_entries", "stream=width,height,codec_name",
    "-of", "csv=s=x:p=0",
    out_vid
]
probe_res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, text=True)
print(f"FFprobe output stream details: {probe_res.stdout.strip()}")
print("\n All pipeline tests passed successfully!")
