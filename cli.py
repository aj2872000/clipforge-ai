import os
import sys
import argparse
import logging
from app.transcriber import extract_video_id, fetch_video_metadata, fetch_transcript, download_video_segment
from app.virality_engine import detect_clips
from app.video_processor import VideoProcessor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cli")

def main():
    parser = argparse.ArgumentParser(description="ClipForge CLI - Convert YouTube Videos into Viral 9:16 Shorts")
    parser.add_argument("url", help="YouTube video link (e.g. https://www.youtube.com/watch?v=...)")
    parser.add_argument("--duration", default="30-60", choices=["15-30", "30-60", "60-90"], help="Target clip duration")
    parser.add_argument("--clips", type=int, default=3, help="Number of clips to generate")
    parser.add_argument("--layout", default="blur_fill", choices=["blur_fill", "face_crop", "center_crop"], help="9:16 vertical layout framing")
    parser.add_argument("--style", default="hormozi", choices=["hormozi", "beast", "minimal", "neon"], help="Caption animation style")
    parser.add_argument("--outdir", default="storage/clips", help="Output directory for generated shorts")

    args = parser.parse_args()

    video_id = extract_video_id(args.url)
    if not video_id:
        print("[ERROR] Invalid YouTube URL.")
        sys.exit(1)

    print(f"\n🎬 [1/4] Fetching video metadata & transcript for: {args.url}...")
    meta = fetch_video_metadata(args.url)
    print(f"       Title:    {meta.title}")
    print(f"       Duration: {int(meta.duration // 60)}m {int(meta.duration % 60)}s")
    print(f"       Creator:  {meta.author}")

    segments = fetch_transcript(video_id)
    if not segments:
        print("[ERROR] No transcript or auto-captions found for this video.")
        sys.exit(1)

    print(f"\n🧠 [2/4] Analyzing virality hooks & narrative arcs...")
    candidates = detect_clips(segments, target_duration=args.duration, max_clips=args.clips)
    print(f"       Found {len(candidates)} high-potential viral moments:")
    for i, c in enumerate(candidates):
        print(f"       [{i+1}] Score: {c.virality_score}/100 | {c.duration}s | \"{c.title}\"")

    processor = VideoProcessor(base_dir="storage")
    os.makedirs(args.outdir, exist_ok=True)

    print(f"\n⚡ [3/4] Downloading segments & [4/4] Rendering 9:16 shorts ({args.layout}, {args.style})...\n")

    for idx, c in enumerate(candidates):
        print(f"--- Processing Clip {idx+1}/{len(candidates)}: {c.title} ---")
        raw_seg = os.path.join("storage", "downloads", f"{video_id}_{c.id}_raw.mp4")
        out_clip = os.path.join(args.outdir, f"{video_id}_{c.id}.mp4")

        # Download segment at highest available YouTube quality
        download_video_segment(args.url, raw_seg, c.start_time, c.end_time)

        # Render 9:16 vertical short
        processor.render_short_clip(
            source_video_path=raw_seg,
            output_clip_path=out_clip,
            words=c.words or [],
            layout=args.layout,
            caption_style=args.style,
            clip_id=c.id,
            start_time_offset=c.start_time
        )

        if os.path.exists(raw_seg):
            try:
                os.remove(raw_seg)
            except Exception:
                pass

        print(f"   ✓ Finished! Saved to: {out_clip}")
        print(f"   ✓ Virality Score: {c.virality_score}/100 | Hook: \"{c.hook}\"\n")

    print(f"🎉 Done! All {len(candidates)} viral shorts generated in '{args.outdir}'.\n")

if __name__ == "__main__":
    main()
