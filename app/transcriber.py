import os
import re
import json
import logging
import shutil
from typing import List, Dict, Any, Optional
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from app.models import VideoMetadata, TranscriptSegment, TranscriptWord

logger = logging.getLogger("shorts_creator.transcriber")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
DOWNLOADS_DIR = os.path.join(STORAGE_DIR, "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Find node runtime for yt-dlp JS challenges
NODE_PATH = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"

def get_base_ydl_opts() -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
    }
    if os.path.exists(NODE_PATH) or shutil.which("node"):
        opts['js_runtimes'] = {'node': {'path': NODE_PATH}}
    return opts

def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})',
        r'(?:embed\/)([0-9A-Za-z_-]{11})',
        r'(?:shorts\/)([0-9A-Za-z_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if len(url) == 11 and re.match(r'^[0-9A-Za-z_-]{11}$', url):
        return url
    return None

def fetch_video_metadata(url: str) -> VideoMetadata:
    """Fetch video details using yt-dlp without downloading media."""
    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        'extract_flat': False,
        'skip_download': True,
    })
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        video_id = info.get('id', extract_video_id(url) or "unknown")
        title = info.get('title', 'YouTube Video')
        author = info.get('uploader', info.get('channel', 'Unknown Creator'))
        duration = float(info.get('duration', 0.0) or 0.0)
        thumbnail = info.get('thumbnail', '')
        description = info.get('description', '')

        return VideoMetadata(
            id=video_id,
            url=url,
            title=title,
            author=author,
            duration=duration,
            thumbnail=thumbnail,
            description=description[:500] if description else ""
        )

def generate_word_timestamps(segments: List[Dict[str, Any]]) -> List[TranscriptSegment]:
    """
    Given transcript segments with start and duration, compute proportional
    word-level timestamps for smooth karaoke-style subtitle rendering.
    """
    result: List[TranscriptSegment] = []
    
    for seg in segments:
        text = seg.get('text', '').strip()
        start = float(seg.get('start', 0.0))
        duration = float(seg.get('duration', 0.0))
        if not text or duration <= 0:
            continue
            
        words = text.split()
        if not words:
            continue
            
        cleaned_words = [re.sub(r'[\r\n\t]+', ' ', w).strip() for w in words]
        char_counts = [max(1, len(w)) for w in cleaned_words]
        total_chars = sum(char_counts)
        
        word_objs: List[TranscriptWord] = []
        current_time = start
        
        for w, count in zip(cleaned_words, char_counts):
            w_duration = (count / total_chars) * duration
            w_duration = max(0.12, w_duration)
            w_end = min(start + duration, current_time + w_duration)
            word_objs.append(TranscriptWord(
                word=w,
                start=round(current_time, 2),
                end=round(w_end, 2)
            ))
            current_time = w_end
            
        result.append(TranscriptSegment(
            text=text,
            start=round(start, 2),
            duration=round(duration, 2),
            words=word_objs
        ))
        
    return result

def fetch_transcript(video_id: str, download_dir: Optional[str] = None) -> List[TranscriptSegment]:
    """
    Fetch transcripts using youtube-transcript-api with fallback to yt-dlp subtitles.
    """
    target_dir = download_dir or DOWNLOADS_DIR
    logger.info(f"Attempting transcript retrieval for {video_id}")
    
    # 1. Try youtube_transcript_api
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        
        target_transcript = None
        try:
            target_transcript = transcript_list.find_manually_created_transcript(['en', 'en-US', 'en-GB'])
        except Exception:
            try:
                target_transcript = transcript_list.find_generated_transcript(['en', 'en-US', 'en-GB'])
            except Exception:
                for t in transcript_list:
                    target_transcript = t
                    break
                    
        if target_transcript:
            raw_data = target_transcript.fetch().to_raw_data()
            if raw_data:
                logger.info(f"Successfully fetched {len(raw_data)} segments via youtube_transcript_api")
                return generate_word_timestamps(raw_data)
    except Exception as e:
        logger.warning(f"youtube_transcript_api fallback: {e}")
        
    # 2. Fallback to yt-dlp subtitle download
    try:
        sub_path_template = os.path.join(target_dir, f"{video_id}_sub")
        ydl_opts = get_base_ydl_opts()
        ydl_opts.update({
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en', 'en-.*'],
            'subtitlesformat': 'json3/vtt',
            'outtmpl': sub_path_template,
        })
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            
        raw_segments = []
        json3_file = f"{sub_path_template}.en.json3"
        vtt_file = f"{sub_path_template}.en.vtt"
        
        if os.path.exists(json3_file):
            with open(json3_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for event in data.get('events', []):
                    t_start = event.get('tStartMs', 0) / 1000.0
                    d_ms = event.get('dDurationMs', 0) / 1000.0
                    segs = event.get('segs', [])
                    text = "".join([s.get('utf8', '') for s in segs]).strip()
                    if text and text != '\n':
                        raw_segments.append({'text': text, 'start': t_start, 'duration': d_ms})
        elif os.path.exists(vtt_file):
            with open(vtt_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                curr_start = 0.0
                curr_end = 0.0
                for line in lines:
                    line = line.strip()
                    time_match = re.search(r'(\d{2}):(\d{2}):(\d{2})\.(\d{3}) --> (\d{2}):(\d{2}):(\d{2})\.(\d{3})', line)
                    if time_match:
                        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, time_match.groups())
                        curr_start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
                        curr_end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
                    elif line and not line.startswith('WEBVTT') and not line.isdigit() and curr_end > curr_start:
                        raw_segments.append({
                            'text': line,
                            'start': curr_start,
                            'duration': curr_end - curr_start
                        })
                        
        if raw_segments:
            logger.info(f"Successfully extracted {len(raw_segments)} segments from yt-dlp subtitles")
            return generate_word_timestamps(raw_segments)
    except Exception as e:
        logger.error(f"yt-dlp subtitle extraction error: {e}")
        
    return []

def download_video_segment(
    url: str,
    output_path: str,
    start_time: float,
    end_time: float
) -> str:
    """
    Downloads ONLY the requested segment with BOTH video and audio.
    Filters out DVR m3u8 playlists that cannot seek efficiently.
    Ensures the resulting file has both video and audio streams.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    base_no_ext, _ = os.path.splitext(output_path)
    temp_tmpl = f"{base_no_ext}.%(ext)s"

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        'format': 'bv*[protocol^=http][height<=1080]+ba[protocol^=http]/b[protocol^=http] / bv*[height<=1080]+ba/b',
        'outtmpl': temp_tmpl,
        'merge_output_format': 'mp4',
        'download_ranges': yt_dlp.utils.download_range_func(None, [(start_time, end_time)]),
        'force_keyframes_at_cuts': True,
        'overwrites': True,
    })

    logger.info(f"Downloading clip segment {start_time:.1f}s - {end_time:.1f}s for {url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    expected_mp4 = f"{base_no_ext}.mp4"
    if os.path.exists(expected_mp4) and os.path.getsize(expected_mp4) > 1000:
        return expected_mp4
        
    for ext in [".webm", ".mkv", ".m4v"]:
        cand = f"{base_no_ext}{ext}"
        if os.path.exists(cand) and os.path.getsize(cand) > 1000:
            return cand

    if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
        return output_path

    raise RuntimeError("Downloaded video segment file not found or is empty.")
