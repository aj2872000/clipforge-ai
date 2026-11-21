---
title: ClipForge AI - Opus Shorts Creator
emoji: 🎬
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# 🎬 ClipForge AI - YouTube to Viral Shorts Creator (Opus Clip Alternative)

ClipForge AI is an end-to-end, open-source AI shorts creator inspired by **Opus Clip**. It takes any long-form YouTube video URL as input and automatically produces viral, vertical 9:16 Shorts, TikToks, and Instagram Reels with **dynamic animated karaoke-style subtitles** and **intelligent framing**.

---

## 🚀 Key Features

1. **Intelligent Highlight & Virality Detection**:
   - Analyzes video transcript, audio cadence, and narrative structure.
   - Evaluates **Hook Strength (0-100)**: Detects curiosity gaps, contrarian statements, emotional triggers, and questions.
   - Evaluates **Flow & Speech Pacing**: Tunes for the 130–180 words-per-minute short-form retention sweet spot.
   - Computes an overall **Virality Score** and generates a click-worthy short title + "Why this clip works" breakdown.
   - **Zero-Key Heuristic Mode**: Works 100% offline and instantly without needing external API keys.
   - **LLM Enhancement**: Optional integration with Google Gemini 2.0 or OpenAI GPT-4o for deep semantic curation.

2. **Smart 9:16 Vertical Reframing**:
   - **Blurred Background (Opus Clip Standard)**: Scales original 16:9 video to fit a vertical 1080x1920 canvas with a stylized, Gaussian-blurred dynamic background and subtle drop shadow.
   - **Smart Face-Tracking Crop**: Powered by OpenCV YuNet deep-learning face detection. Automatically tracks the active speaker across frames and smoothly pans the 9:16 window.
   - **Center Crop**: Traditional 9:16 vertical crop centered on action.

3. **Viral Karaoke Captions (Subtitles)**:
   - Word-by-word active highlight pop animation (`\k` and styled ASS tags burned via FFmpeg `libass`).
   - Multiple built-in creator styles:
     - **Alex Hormozi**: Bold uppercase text with electric yellow and green active word popping and 7px black outline.
     - **MrBeast**: High-energy impact typography with vibrant yellow/cyan contrast and pop shadow.
     - **Clean Minimalist**: Modern sans-serif text with soft translucent pill-box background.
     - **Cyberpunk Neon**: Glowing cyan and magenta neon color palette.
   - Downloadable `.srt` clean subtitles included with every clip.

4. **Modern Dark-Mode Web Dashboard**:
   - One-click **Quick Demo**: Generate and view a 9:16 vertical short in ~5 seconds without waiting for large video downloads.
   - Real-time step-by-step progress stepper and live terminal execution log.
   - Interactive 9:16 video player with custom download and title copy buttons.

---

## 📁 Project Structure

```
opus-shorts-creator/
├── app/
│   ├── main.py                # FastAPI web server & endpoints
│   ├── models.py              # Data models (Pydantic)
│   ├── transcriber.py         # YouTube transcript & audio extraction
│   ├── virality_engine.py     # AI clip selection & virality scoring
│   ├── face_tracker.py        # OpenCV YuNet active speaker tracker
│   ├── subtitle_generator.py  # ASS & SRT dynamic karaoke captions
│   └── video_processor.py     # FFmpeg 9:16 vertical reframing & subtitle burner
├── static/
│   ├── index.html             # Web dashboard
│   ├── css/styles.css         # Modern glassmorphism UI styles
│   └── js/app.js              # Real-time client & polling
├── storage/
│   ├── downloads/             # Downloaded source clips
│   ├── clips/                 # Rendered 1080x1920 MP4 shorts
│   └── subtitles/             # Generated subtitle files (.ass, .srt)
├── models/
│   └── face_detection_yunet.onnx # Fast face detector
├── Dockerfile                 # Hugging Face Spaces Dockerfile
├── requirements.txt           # Python dependencies
└── README.md
```
