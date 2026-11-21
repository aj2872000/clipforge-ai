import os
import re
import math
from typing import List, Dict, Any, Optional
from app.models import TranscriptWord

STYLE_PRESETS = {
    "hormozi": {
        "name": "Hormozi",
        "font": "Arial",
        "fontsize": 68,
        "primary_color": "&H00FFFFFF",    # Base text: White
        "active_color": "&H0000FFFF",     # Active word: Yellow (BGR: 00FFFF)
        "outline_color": "&H00000000",    # Black border
        "back_color": "&H80000000",
        "bold": -1,
        "outline": 7,
        "shadow": 3,
        "alignment": 2,                   # Bottom-Center
        "margin_v": 460,                  # Above Shorts UI buttons
        "border_style": 1
    },
    "beast": {
        "name": "Beast",
        "font": "Impact",
        "fontsize": 72,
        "primary_color": "&H00FFFFFF",
        "active_color": "&H0000D7FF",     # Electric Amber/Yellow
        "outline_color": "&H00000000",
        "back_color": "&H90000000",
        "bold": -1,
        "outline": 8,
        "shadow": 4,
        "alignment": 2,
        "margin_v": 480,
        "border_style": 1
    },
    "minimal": {
        "name": "Minimal",
        "font": "Segoe UI",
        "fontsize": 56,
        "primary_color": "&H00FFFFFF",
        "active_color": "&H00FFFF00",     # Cyan highlight
        "outline_color": "&H00000000",
        "back_color": "&HAA000000",       # Pill box background
        "bold": -1,
        "outline": 2,
        "shadow": 0,
        "alignment": 2,
        "margin_v": 420,
        "border_style": 3                 # Opaque background box
    },
    "neon": {
        "name": "Neon",
        "font": "Arial",
        "fontsize": 66,
        "primary_color": "&H00FFFF00",    # Neon Cyan
        "active_color": "&H00FF00FF",     # Neon Magenta
        "outline_color": "&H00111111",
        "back_color": "&H80000000",
        "bold": -1,
        "outline": 6,
        "shadow": 4,
        "alignment": 2,
        "margin_v": 460,
        "border_style": 1
    }
}

def format_ass_time(seconds: float) -> str:
    """Format seconds into ASS timestamp H:MM:SS.cs"""
    seconds = max(0.0, seconds)
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{cs:02d}"

def format_srt_time(seconds: float) -> str:
    """Format seconds into SRT timestamp HH:MM:SS,mmm"""
    seconds = max(0.0, seconds)
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"

def build_ass_subtitles(
    words: List[TranscriptWord],
    output_path: str,
    style_key: str = "hormozi",
    clip_offset: float = 0.0,
    words_per_group: int = 3,
    target_w: int = 1440,
    target_h: int = 2560
) -> str:
    """
    Generate an ASS subtitle file with animated active-word popping karaoke highlights.
    clip_offset: start_time of the clip in the full video, so subtitle timestamps start at 0.0s.
    target_w, target_h: dynamically scales font sizes and margins to match 1440p (2K) or 2160p (4K).
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cfg = STYLE_PRESETS.get(style_key.lower(), STYLE_PRESETS["hormozi"])

    scale_factor = target_h / 1920.0
    scaled_fontsize = int(cfg['fontsize'] * scale_factor)
    scaled_margin_v = int(cfg['margin_v'] * scale_factor)
    scaled_outline = max(2, int(cfg['outline'] * scale_factor))
    scaled_shadow = max(0, int(cfg['shadow'] * scale_factor))

    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {target_w}
PlayResY: {target_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: {cfg['name']},{cfg['font']},{scaled_fontsize},{cfg['primary_color']},{cfg['active_color']},{cfg['outline_color']},{cfg['back_color']},{cfg['bold']},0,0,0,100,100,2,0,{cfg['border_style']},{scaled_outline},{scaled_shadow},{cfg['alignment']},40,40,{scaled_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    dialogues = []

    # Adjust words relative to clip start
    rel_words: List[TranscriptWord] = []
    for w in words:
        start_rel = max(0.0, w.start - clip_offset)
        end_rel = max(start_rel + 0.1, w.end - clip_offset)
        rel_words.append(TranscriptWord(word=w.word.upper(), start=start_rel, end=end_rel))

    # Group words into chunks of 3-4 words
    i = 0
    while i < len(rel_words):
        chunk = rel_words[i : i + words_per_group]
        chunk_start = chunk[0].start
        chunk_end = chunk[-1].end

        # For each word inside this chunk, emit a dialogue event where that word is highlighted
        for idx, active_word in enumerate(chunk):
            w_start = active_word.start
            # End when next word starts or at chunk_end
            if idx + 1 < len(chunk):
                w_end = chunk[idx + 1].start
            else:
                w_end = chunk_end

            if w_end <= w_start:
                w_end = w_start + 0.2

            # Build line with active word highlighted
            line_parts = []
            for j, w in enumerate(chunk):
                if j == idx:
                    # Highlighted active word with 112% scale pop
                    highlight_tag = f"{{\\c{cfg['active_color']}\\fscx112\\fscy112}}{w.word}{{\\r\\fn{cfg['font']}\\fs{cfg['fontsize']}\\b{1 if cfg['bold'] else 0}\\bord{cfg['outline']}\\shad{cfg['shadow']}\\c{cfg['primary_color']}}}"
                    line_parts.append(highlight_tag)
                else:
                    line_parts.append(w.word)

            dialogue_text = " ".join(line_parts)
            dialogues.append(
                f"Dialogue: 0,{format_ass_time(w_start)},{format_ass_time(w_end)},{cfg['name']},,0,0,0,,{dialogue_text}"
            )

        i += len(chunk)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        f.write("\n".join(dialogues))
        f.write("\n")

    return output_path

def build_srt_subtitles(
    words: List[TranscriptWord],
    output_path: str,
    clip_offset: float = 0.0,
    words_per_group: int = 4
) -> str:
    """Generate a clean SRT subtitle file for export."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    srt_entries = []

    rel_words: List[TranscriptWord] = []
    for w in words:
        start_rel = max(0.0, w.start - clip_offset)
        end_rel = max(start_rel + 0.1, w.end - clip_offset)
        rel_words.append(TranscriptWord(word=w.word, start=start_rel, end=end_rel))

    i = 0
    seq = 1
    while i < len(rel_words):
        chunk = rel_words[i : i + words_per_group]
        chunk_start = chunk[0].start
        chunk_end = chunk[-1].end
        text = " ".join([w.word for w in chunk])

        srt_entries.append(
            f"{seq}\n{format_srt_time(chunk_start)} --> {format_srt_time(chunk_end)}\n{text}\n"
        )
        seq += 1
        i += len(chunk)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_entries))

    return output_path
