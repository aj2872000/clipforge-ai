import os
import re
import json
import logging
from typing import List, Dict, Any, Optional
import requests
from app.models import TranscriptSegment, TranscriptWord, ClipCandidate

logger = logging.getLogger("shorts_creator.virality")

# Power words & hook triggers for short-form virality analysis
HOOK_KEYWORDS = [
    "why", "how", "secret", "mistake", "never", "always", "stop", "proven",
    "insane", "crazy", "huge", "shocking", "truth", "lie", "money", "dollar",
    "rich", "wealth", "rule", "hack", "trick", "fail", "win", "changed",
    "future", "ai", "destroy", "everyone", "nobody", "most people", "danger",
    "simple", "step", "formula", "strategy", "lesson", "warning", "advice"
]

SENTENCE_STARTERS_PENALTY = [
    "and", "but", "so", "because", "also", "or", "well", "actually", "like"
]

def score_hook(text: str) -> int:
    """Evaluate hook strength based on curiosity gap, power words, and structure."""
    text_lower = text.lower()
    score = 60 # baseline
    
    # Check for question/curiosity
    if "?" in text or any(text_lower.startswith(q) for q in ["how", "why", "what", "did you", "have you", "can you"]):
        score += 15
        
    # Check power words count in first 10 words
    first_words = text_lower.split()[:10]
    first_text = " ".join(first_words)
    hook_matches = sum(1 for kw in HOOK_KEYWORDS if kw in first_text)
    score += min(20, hook_matches * 6)
    
    # Penalize starting mid-sentence
    if first_words and first_words[0].lower() in SENTENCE_STARTERS_PENALTY:
        score -= 15
        
    return max(40, min(99, score))

def score_pacing(word_count: int, duration: float) -> int:
    """Calculate pacing score based on words per minute (optimal: 130 - 180 WPM)."""
    if duration <= 0:
        return 50
    wpm = (word_count / duration) * 60
    if 130 <= wpm <= 185:
        return 95
    elif 110 <= wpm < 130 or 185 < wpm <= 210:
        return 82
    elif 90 <= wpm < 110 or 210 < wpm <= 235:
        return 70
    else:
        return 55

def generate_viral_title(first_sentence: str, full_text: str) -> str:
    """Generate a high-converting short title from the text content."""
    clean = re.sub(r'[^a-zA-Z0-9\s]', '', first_sentence).strip()
    words = clean.split()
    
    # Look for punchy phrases
    text_lower = full_text.lower()
    for kw in ["secret", "mistake", "truth", "rule", "lesson", "strategy"]:
        if kw in text_lower:
            return f"The {kw.capitalize()} That Changes Everything"
            
    if any(q in text_lower for q in ["why", "how to"]):
        if len(words) >= 4:
            return " ".join(words[:6]).title()
            
    if len(words) >= 3:
        return " ".join(words[:5]).title() + "!"
    return "Must Watch Insight!"

def analyze_with_heuristics(
    segments: List[TranscriptSegment],
    target_duration_str: str = "30-60",
    max_clips: int = 3
) -> List[ClipCandidate]:
    """
    Advanced heuristic chunking & virality algorithm.
    Evaluates sliding windows across sentence boundaries to find the highest-impact moments.
    """
    # Parse target duration
    min_dur, max_dur = 25.0, 60.0
    if target_duration_str == "15-30":
        min_dur, max_dur = 15.0, 32.0
    elif target_duration_str == "30-60":
        min_dur, max_dur = 28.0, 62.0
    elif target_duration_str == "60-90":
        min_dur, max_dur = 55.0, 92.0
        
    if not segments:
        return []
        
    # Aggregate words
    all_words: List[TranscriptWord] = []
    for s in segments:
        all_words.extend(s.words)
        
    if not all_words:
        # Fallback if no words
        return []
        
    total_duration = all_words[-1].end - all_words[0].start
    logger.info(f"Analyzing {len(all_words)} words spanning {total_duration:.1f}s")
    
    candidates: List[ClipCandidate] = []
    step_jump = max(1, len(all_words) // 40) # window step
    
    i = 0
    while i < len(all_words):
        start_word = all_words[i]
        start_time = start_word.start
        
        # Look ahead for window in [min_dur, max_dur]
        for j in range(i + 15, len(all_words)):
            end_word = all_words[j]
            curr_dur = end_word.end - start_time
            
            if curr_dur > max_dur:
                break
                
            if curr_dur >= min_dur:
                # Check sentence boundary (punctuation in word)
                if any(end_word.word.endswith(p) for p in [".", "!", "?", "..."]):
                    window_words = all_words[i:j+1]
                    window_text = " ".join([w.word for w in window_words])
                    
                    # Calculate scores
                    hook_score = score_hook(window_text)
                    flow_score = score_pacing(len(window_words), curr_dur)
                    
                    # Bonus for emotional/curiosity density
                    kw_count = sum(1 for kw in HOOK_KEYWORDS if kw in window_text.lower())
                    kw_bonus = min(15, kw_count * 2)
                    
                    composite_score = int(0.45 * hook_score + 0.35 * flow_score + kw_bonus)
                    composite_score = max(50, min(99, composite_score))
                    
                    # First sentence for title
                    first_sent = window_text.split(".")[0] or window_text
                    title = generate_viral_title(first_sent, window_text)
                    
                    explanation = f"Strong hook rating ({hook_score}/100) with rapid narrative pacing. Contains {kw_count} viral engagement triggers."
                    
                    candidates.append(ClipCandidate(
                        id=f"clip_{len(candidates)+1}_{int(start_time)}",
                        title=title,
                        hook=first_sent[:90] + "...",
                        explanation=explanation,
                        virality_score=composite_score,
                        hook_score=hook_score,
                        flow_score=flow_score,
                        start_time=round(start_time, 2),
                        end_time=round(end_word.end, 2),
                        duration=round(curr_dur, 2),
                        transcript=window_text,
                        words=window_words,
                        hashtags=["#shorts", "#viral", "#foryou", "#trending", "#insights"]
                    ))
        i += step_jump
        
    # Sort by virality score descending
    candidates.sort(key=lambda c: c.virality_score, reverse=True)
    
    # Deduplicate overlapping clips (ensure at least 20s separation)
    selected: List[ClipCandidate] = []
    for c in candidates:
        overlap = False
        for s in selected:
            if not (c.end_time < s.start_time + 15 or c.start_time > s.end_time - 15):
                overlap = True
                break
        if not overlap:
            selected.append(c)
        if len(selected) >= max_clips:
            break
            
    # If no window matched punctuation, fall back to first segment
    if not selected and len(all_words) > 10:
        slice_dur = min(total_duration, 45.0)
        end_idx = min(len(all_words) - 1, int(len(all_words) * (slice_dur / total_duration)))
        window_words = all_words[:end_idx+1]
        text = " ".join([w.word for w in window_words])
        selected.append(ClipCandidate(
            id="clip_fallback_1",
            title="Essential Highlight",
            hook=text[:80] + "...",
            explanation="High-value opening segment with key takeaways.",
            virality_score=85,
            hook_score=85,
            flow_score=85,
            start_time=round(all_words[0].start, 2),
            end_time=round(window_words[-1].end, 2),
            duration=round(window_words[-1].end - all_words[0].start, 2),
            transcript=text,
            words=window_words,
            hashtags=["#shorts", "#viral", "#video"]
        ))
        
    return selected

def get_gemini_api_key(explicit_key: Optional[str] = None) -> Optional[str]:
    """Retrieve Gemini API key from explicit param, environment, or .env file."""
    if explicit_key and explicit_key.strip():
        return explicit_key.strip()
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key and key.strip():
        return key.strip()
    return None

def analyze_with_gemini(
    segments: List[TranscriptSegment],
    api_key: str,
    target_duration_str: str = "30-60",
    max_clips: int = 3
) -> List[ClipCandidate]:
    """
    Opus Clip-style AI curation powered by Google Gemini 2.0 / 1.5 Flash.
    Analyzes the full semantic narrative arc, extracts the strongest viral hooks,
    and returns exact timestamped boundaries with click-worthy titles and virality scores.
    """
    all_words: List[TranscriptWord] = []
    for s in segments:
        all_words.extend(s.words)

    if not all_words:
        return []

    total_duration = all_words[-1].end - all_words[0].start
    logger.info(f"Analyzing {len(all_words)} words across {total_duration:.1f}s with Google Gemini AI...")

    # Determine duration constraints
    min_dur, max_dur = 28.0, 60.0
    if target_duration_str == "15-30":
        min_dur, max_dur = 15.0, 32.0
    elif target_duration_str == "30-60":
        min_dur, max_dur = 28.0, 62.0
    elif target_duration_str == "60-90":
        min_dur, max_dur = 55.0, 92.0

    # Build a condensed timestamped transcript
    formatted_blocks = []
    curr_block_words = []
    block_start = all_words[0].start

    for w in all_words:
        curr_block_words.append(w.word)
        if w.end - block_start >= 18.0 and any(w.word.endswith(p) for p in [".", "!", "?", ","]):
            m = int(block_start // 60)
            sec = int(block_start % 60)
            text = " ".join(curr_block_words)
            formatted_blocks.append(f"[{m:02d}:{sec:02d}] ({block_start:.1f}s - {w.end:.1f}s): {text}")
            curr_block_words = []
            block_start = w.end

    if curr_block_words:
        m = int(block_start // 60)
        sec = int(block_start % 60)
        text = " ".join(curr_block_words)
        formatted_blocks.append(f"[{m:02d}:{sec:02d}] ({block_start:.1f}s - {all_words[-1].end:.1f}s): {text}")

    # For extremely long transcripts (> 600 blocks / ~3 hours), sample evenly to avoid hitting payload limits
    if len(formatted_blocks) > 600:
        step = len(formatted_blocks) // 500
        formatted_blocks = formatted_blocks[::max(1, step)]

    transcript_text = "\n".join(formatted_blocks)

    prompt = f"""You are an elite short-form video editor and algorithm strategist (modeled after Opus Clip).
Your task is to analyze this timestamped video transcript and select the TOP {max_clips} most viral, high-retention clips for YouTube Shorts and TikTok.

TARGET DURATION: {target_duration_str} seconds (strictly between {min_dur:.0f}s and {max_dur:.0f}s each).

CORE OPUS CLIP CURATION RULES:
1. THE 3-SECOND HOOK: Every clip MUST start on a high-retention trigger (a provocative question, shocking fact, contrarian view, or curiosity gap).
2. COHESIVE STORY/VALUE: The clip must be a complete, self-contained thought or story that leaves the viewer satisfied or amazed.
3. CLEAN SPEECH EDGES: Start cleanly at the beginning of a sentence. End cleanly at a full stop or punchline. NEVER cut off mid-sentence.
4. DIVERSE SELECTION: Choose clips from different distinct segments of the video.
5. CLICK-WORTHY TITLES: Generate captivating titles under 50 characters in Title Case. Do NOT include hashtags or quotes in the title.
6. OPUS METRICS: Provide realistic Virality Score (80-99), Hook Score (80-99), and Flow Score (80-99) with an explanation of why it will captivate audiences.

TIMESTAMPED TRANSCRIPT:
{transcript_text}

OUTPUT FORMAT:
Return a JSON array containing exactly {max_clips} objects:
[
  {{
    "start_time": 12.5,
    "end_time": 45.2,
    "title": "The Brutal Truth About Success",
    "hook": "Most people fail because they focus on motivation instead of discipline.",
    "virality_score": 96,
    "hook_score": 98,
    "flow_score": 94,
    "explanation": "Immediate contrarian hook that challenges common belief, followed by rapid, actionable value with high emotional resonance.",
    "hashtags": ["#shorts", "#success", "#mindset", "#viral", "#motivation"]
  }}
]"""

    models_to_try = [
        "gemini-3.8-flash",
        "gemini-3.8-pro",
        "gemini-3.5-flash",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-flash-latest"
    ]
    raw_json_str = None

    for model_name in models_to_try:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "responseMimeType": "application/json"
                }
            }
            logger.info(f"Sending transcript to Google Gemini ({model_name})...")
            resp = requests.post(url, json=payload, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                candidates_list = data.get("candidates", [])
                if candidates_list and "content" in candidates_list[0]:
                    parts = candidates_list[0]["content"].get("parts", [])
                    if parts and "text" in parts[0]:
                        raw_json_str = parts[0]["text"]
                        logger.info(f"Successfully received Opus Clip curation from Gemini ({model_name})!")
                        break
            else:
                logger.warning(f"Gemini model {model_name} returned status {resp.status_code}: {resp.text[:200]}")
                # Dynamically capture any model recommended in Google's error response
                recommended_models = re.findall(r'models/(gemini-[\w\.\-]+)', resp.text)
                for rm in recommended_models:
                    if rm not in models_to_try and rm != model_name:
                        logger.info(f"Google API recommended model '{rm}'. Adding to candidate models.")
                        models_to_try.append(rm)
        except Exception as ex:
            logger.warning(f"Gemini {model_name} request error: {ex}")

    if not raw_json_str:
        raise RuntimeError("Could not retrieve AI response from Gemini API models.")

    # Robust JSON parsing
    bracket_match = re.search(r'\[\s*\{.*\}\s*\]', raw_json_str, re.DOTALL)
    if bracket_match:
        clip_items = json.loads(bracket_match.group(0))
    else:
        clean_json = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_json_str.strip())
        clip_items = json.loads(clean_json)

    candidates: List[ClipCandidate] = []
    for idx, item in enumerate(clip_items):
        req_start = float(item["start_time"])
        req_end = float(item["end_time"])

        # Snap to exact word timestamps
        matching_words = [w for w in all_words if w.start >= req_start - 0.5 and w.end <= req_end + 0.5]
        if not matching_words:
            matching_words = [w for w in all_words if (w.start + w.end) / 2 >= req_start and (w.start + w.end) / 2 <= req_end]

        if matching_words:
            act_start = matching_words[0].start
            act_end = matching_words[-1].end
            duration = act_end - act_start
            transcript = " ".join([w.word for w in matching_words])
        else:
            act_start = req_start
            act_end = req_end
            duration = req_end - req_start
            transcript = item.get("hook", "")
            matching_words = []

        candidates.append(ClipCandidate(
            id=f"clip_gemini_{idx+1}_{int(act_start)}",
            title=item.get("title", f"Viral Moment #{idx+1}"),
            hook=item.get("hook", transcript[:90] + "..."),
            explanation=item.get("explanation", "Curated by Google Gemini AI with high retention structure."),
            virality_score=int(item.get("virality_score", 92)),
            hook_score=int(item.get("hook_score", 90)),
            flow_score=int(item.get("flow_score", 90)),
            start_time=round(act_start, 2),
            end_time=round(act_end, 2),
            duration=round(duration, 2),
            transcript=transcript,
            words=matching_words,
            hashtags=item.get("hashtags") or ["#shorts", "#viral", "#foryou"]
        ))

    candidates.sort(key=lambda c: c.virality_score, reverse=True)
    return candidates[:max_clips]

def detect_clips(
    segments: List[TranscriptSegment],
    target_duration: str = "30-60",
    max_clips: int = 3,
    provider: str = "gemini",
    api_key: Optional[str] = None
) -> List[ClipCandidate]:
    """
    Main clip detection orchestrator.
    Defaults to Google Gemini AI (Opus Clip Engine) when an API key is available.
    """
    resolved_key = get_gemini_api_key(api_key)

    if provider.lower() == "gemini":
        if resolved_key:
            try:
                candidates = analyze_with_gemini(
                    segments=segments,
                    api_key=resolved_key,
                    target_duration_str=target_duration,
                    max_clips=max_clips
                )
                if candidates:
                    return candidates
            except Exception as e:
                logger.error(f"Gemini Opus Clip curation error: {e}. Falling back to enhanced heuristic analysis.", exc_info=True)
        else:
            logger.info("Gemini AI selected, but no API key configured. Set GEMINI_API_KEY in .env or the UI. Falling back to heuristic analysis.")

    return analyze_with_heuristics(segments, target_duration, max_clips)
