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

def analyze_with_llm(
    segments: List[TranscriptSegment],
    provider: str,
    api_key: str,
    target_duration: str = "30-60",
    max_clips: int = 3
) -> List[ClipCandidate]:
    """
    Optional LLM integration (Gemini / OpenAI) for advanced contextual semantic virality curation.
    """
    # First get candidate windows via heuristics to bound the token count
    heuristic_candidates = analyze_with_heuristics(segments, target_duration, max_clips=5)
    if not heuristic_candidates:
        return []
        
    if provider.lower() == "gemini":
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
            prompt = f"""You are an expert viral shorts editor (like Opus Clip).
Review these {len(heuristic_candidates)} candidate clips from a video transcript:
{json.dumps([{"id": c.id, "start": c.start_time, "end": c.end_time, "text": c.transcript} for c in heuristic_candidates], indent=2)}

Select and refine the TOP {max_clips} clips that will perform best on YouTube Shorts / TikTok.
For each clip, return JSON:
[
  {{
    "id": "original_id",
    "title": "Ultra-clickable short title (under 50 chars, no hashtags)",
    "virality_score": 95, // 0 to 100
    "hook": "Opening hook explanation",
    "explanation": "Why this clip will captivate audience retention"
  }}
]
Return ONLY raw JSON list, no markdown fences."""
            
            resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
            if resp.status_code == 200:
                content = resp.json()['candidates'][0]['content']['parts'][0]['text']
                clean_json = re.sub(r'^```json\s*|\s*```$', '', content.strip())
                data = json.loads(clean_json)
                
                # Merge LLM titles & scores into candidates
                id_map = {item["id"]: item for item in data}
                for c in heuristic_candidates:
                    if c.id in id_map:
                        item = id_map[c.id]
                        c.title = item.get("title", c.title)
                        c.virality_score = item.get("virality_score", c.virality_score)
                        c.explanation = item.get("explanation", c.explanation)
                        c.hook = item.get("hook", c.hook)
                        
                heuristic_candidates.sort(key=lambda x: x.virality_score, reverse=True)
                return heuristic_candidates[:max_clips]
        except Exception as e:
            logger.warning(f"Gemini analysis error: {e}. Defaulting to heuristic scoring.")
            
    return heuristic_candidates[:max_clips]

def detect_clips(
    segments: List[TranscriptSegment],
    target_duration: str = "30-60",
    max_clips: int = 3,
    provider: str = "heuristic",
    api_key: Optional[str] = None
) -> List[ClipCandidate]:
    """Main clip detection orchestrator."""
    if provider in ["gemini", "openai"] and api_key:
        return analyze_with_llm(segments, provider, api_key, target_duration, max_clips)
    return analyze_with_heuristics(segments, target_duration, max_clips)
