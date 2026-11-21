import os
import re
import json
import glob
import logging
import shutil
import base64
from typing import List, Dict, Any, Optional, Tuple
import requests
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from app.models import VideoMetadata, TranscriptSegment, TranscriptWord

logger = logging.getLogger("shorts_creator.transcriber")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
DOWNLOADS_DIR = os.path.join(STORAGE_DIR, "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

NODE_PATH = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"

def get_base_ydl_opts() -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        # Concurrent downloads for high-speed fragment downloading
        'concurrent_fragment_downloads': 4,
        # Emulate mobile clients to bypass cloud IP bot checks and throttles
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'mweb', 'web']
            }
        }
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

def parse_custom_transcript(raw_text: str) -> List[TranscriptSegment]:
    """
    Parse user-pasted YouTube transcripts. Handles formats like:
    0:00
    First line of speech
    0:05
    Second line of speech
    """
    if not raw_text or not raw_text.strip():
        return []

    lines = [l.strip() for l in raw_text.strip().split('\n') if l.strip()]
    parsed = []
    curr_time = 0.0
    curr_text = []

    time_pattern = r'^(\d{1,2}):(\d{2})(?::(\d{2}))?$'

    for line in lines:
        m = re.match(time_pattern, line)
        if m:
            if curr_text:
                parsed.append({'start': curr_time, 'text': ' '.join(curr_text)})
                curr_text = []
            parts = [int(p) for p in m.groups() if p is not None]
            if len(parts) == 2:
                curr_time = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                curr_time = parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            curr_text.append(line)

    if curr_text:
        parsed.append({'start': curr_time, 'text': ' '.join(curr_text)})

    if not parsed:
        full_text = " ".join(lines)
        words = full_text.split()
        chunk_size = 10
        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i:i+chunk_size])
            start_t = (i / 150.0) * 60.0
            dur_t = (len(words[i:i+chunk_size]) / 150.0) * 60.0
            parsed.append({'start': start_t, 'duration': dur_t, 'text': chunk})
    else:
        for i in range(len(parsed)):
            if i + 1 < len(parsed):
                parsed[i]['duration'] = max(1.0, parsed[i+1]['start'] - parsed[i]['start'])
            else:
                parsed[i]['duration'] = 4.0

    return generate_word_timestamps(parsed)

def transcribe_audio_with_gemini(audio_path: str, api_key: str) -> List[TranscriptSegment]:
    """Fallback: Transcribe audio track with Gemini 2.0 Flash when YouTube blocks captions."""
    try:
        logger.info(f"Using Gemini Flash to transcribe audio track: {audio_path}")
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

        payload = {
            "contents": [
                {
                    "parts": [
                        {"inline_data": {"mime_type": "audio/mp3", "data": b64_audio}},
                        {
                            "text": (
                                "Transcribe this audio file accurately. Break the transcription into short segments with start timestamps (in seconds) and durations (in seconds).\n"
                                "Return ONLY valid JSON in this format:\n"
                                "[\n  {\"start\": 0.0, \"duration\": 3.2, \"text\": \"First sentence\"},\n  {\"start\": 3.5, \"duration\": 4.1, \"text\": \"Next sentence\"}\n]"
                            )
                        }
                    ]
                }
            ],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        resp = requests.post(url, json=payload, timeout=45)
        if resp.status_code == 200:
            result = resp.json()
            raw_text = result['candidates'][0]['content']['parts'][0]['text']
            parsed_json = json.loads(raw_text)
            return generate_word_timestamps(parsed_json)
    except Exception as e:
        logger.error(f"Gemini audio transcription error: {e}")
    return []

def fetch_transcript(
    video_id: str,
    download_dir: Optional[str] = None,
    custom_transcript: Optional[str] = None,
    api_key: Optional[str] = None
) -> Tuple[List[TranscriptSegment], Optional[str]]:
    """Multi-channel transcript extractor."""
    if custom_transcript and custom_transcript.strip():
        logger.info("Using user-provided custom transcript")
        segments = parse_custom_transcript(custom_transcript)
        if segments:
            return segments, None

    target_dir = download_dir or DOWNLOADS_DIR
    logger.info(f"Attempting transcript retrieval for {video_id}")
    errors = []
    
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
                return generate_word_timestamps(raw_data), None
    except Exception as e:
        err_msg = str(e)
        logger.warning(f"youtube_transcript_api error: {err_msg}")
        errors.append(f"youtube-transcript-api: {err_msg[:120]}")
        
    # 2. Fallback to yt-dlp subtitle extraction
    try:
        sub_prefix = os.path.join(target_dir, f"{video_id}_sub")
        ydl_opts = get_base_ydl_opts()
        ydl_opts.update({
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en', 'en-.*'],
            'subtitlesformat': 'json3/vtt',
            'outtmpl': sub_prefix,
        })
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            
        raw_segments = []
        json3_files = glob.glob(f"{sub_prefix}*.json3")
        vtt_files = glob.glob(f"{sub_prefix}*.vtt")
        
        if json3_files:
            target_file = json3_files[0]
            with open(target_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for event in data.get('events', []):
                    t_start = event.get('tStartMs', 0) / 1000.0
                    d_ms = event.get('dDurationMs', 0) / 1000.0
                    segs = event.get('segs', [])
                    text = "".join([s.get('utf8', '') for s in segs]).strip()
                    if text and text != '\n':
                        raw_segments.append({'text': text, 'start': t_start, 'duration': d_ms})
        elif vtt_files:
            target_file = vtt_files[0]
            with open(target_file, 'r', encoding='utf-8') as f:
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
            return generate_word_timestamps(raw_segments), None
    except Exception as e:
        err_msg = str(e)
        logger.error(f"yt-dlp subtitle extraction error: {err_msg}")
        errors.append(f"yt-dlp: {err_msg[:120]}")

    # 3. Fallback to audio download + Gemini Speech-to-Text if API key available
    if api_key:
        try:
            logger.info("Attempting audio download + Gemini speech transcription fallback...")
            audio_out = os.path.join(target_dir, f"{video_id}_temp_audio.mp3")
            ydl_audio_opts = get_base_ydl_opts()
            ydl_audio_opts.update({
                'format': 'ba[protocol^=http]/ba/b',
                'outtmpl': audio_out,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '64',
                }],
            })
            with yt_dlp.YoutubeDL(ydl_audio_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

            if os.path.exists(audio_out):
                stt_segments = transcribe_audio_with_gemini(audio_out, api_key)
                try:
                    os.remove(audio_out)
                except Exception:
                    pass
                if stt_segments:
                    return stt_segments, None
        except Exception as e:
            logger.error(f"Audio transcription fallback error: {e}")
            errors.append(f"stt: {str(e)[:120]}")
        
    error_summary = " | ".join(errors) if errors else "YouTube captions unavailable or blocked by server IP."
    return [], error_summary

def download_video_segment(
    url: str,
    output_path: str,
    start_time: float,
    end_time: float
) -> str:
    """
    High-Speed Video Segment Downloader:
    - Prioritizes fast seekable HTTP DASH streams (720p/1080p)
    - 4 concurrent connection threads
    - Automatically muxes to MP4/WebM
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    base_no_ext, _ = os.path.splitext(output_path)
    temp_tmpl = f"{base_no_ext}.%(ext)s"

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        # Fast format: balances high resolution with fast download bandwidth
        'format': 'bv*[protocol^=http][height<=1080]+ba[protocol^=http] / bv*[height<=720]+ba / b',
        'outtmpl': temp_tmpl,
        'merge_output_format': 'mp4',
        'download_ranges': yt_dlp.utils.download_range_func(None, [(start_time, end_time)]),
        'force_keyframes_at_cuts': True,
        'overwrites': True,
        'buffersize': 1024 * 1024, # 1MB buffer
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
