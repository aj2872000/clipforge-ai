document.addEventListener("DOMContentLoaded", () => {
  const urlInput = document.getElementById("urlInput");
  const createBtn = document.getElementById("createBtn");
  const demoBtn = document.getElementById("demoBtn");

  // Options
  const targetDuration = document.getElementById("targetDuration");
  const maxClips = document.getElementById("maxClips");
  const layoutSelect = document.getElementById("layoutSelect");
  const captionStyle = document.getElementById("captionStyle");
  const qualitySelect = document.getElementById("qualitySelect");
  const aiProvider = document.getElementById("aiProvider");
  const apiKeyWrapper = document.getElementById("apiKeyWrapper");
  const apiKeyInput = document.getElementById("apiKeyInput");
  const customTranscript = document.getElementById("customTranscript");

  // Progress elements
  const progressCard = document.getElementById("progressCard");
  const progressTitle = document.getElementById("progressTitle");
  const progressPct = document.getElementById("progressPct");
  const progressFill = document.getElementById("progressFill");
  const terminalLogs = document.getElementById("terminalLogs");
  const stepItems = document.querySelectorAll(".step-item");

  // Results elements
  const resultsSection = document.getElementById("resultsSection");
  const clipsGrid = document.getElementById("clipsGrid");
  const clipsCountSpan = document.getElementById("clipsCount");

  let activePollInterval = null;

  // Toggle API key input visibility based on provider
  if (aiProvider) {
    aiProvider.addEventListener("change", () => {
      if (aiProvider.value === "gemini" || aiProvider.value === "openai") {
        apiKeyWrapper.style.display = "block";
      } else {
        apiKeyWrapper.style.display = "none";
      }
    });
  }

  // Quick Sample Demo
  demoBtn.addEventListener("click", async () => {
    try {
      startJobUI("Synthesizing Demo Short...");
      const res = await fetch("/api/sample-demo", { method: "POST" });
      const data = await res.json();
      if (data.job_id) {
        pollJob(data.job_id);
      }
    } catch (err) {
      alert("Error initiating demo: " + err.message);
      resetJobUI();
    }
  });

  // Main Create Shorts handler
  createBtn.addEventListener("click", async () => {
    const url = urlInput.value.trim();
    if (!url) {
      alert("Please paste a valid YouTube video link!");
      urlInput.focus();
      return;
    }

    const payload = {
      url: url,
      target_duration: targetDuration.value,
      max_clips: parseInt(maxClips.value) || 3,
      layout: layoutSelect.value,
      caption_style: captionStyle.value,
      video_quality: qualitySelect ? qualitySelect.value : "max",
      upscale_enabled: true,
      ai_provider: aiProvider.value,
      api_key: apiKeyInput ? apiKeyInput.value.trim() : null,
      custom_transcript: customTranscript ? customTranscript.value.trim() : null
    };

    try {
      startJobUI("Starting Opus Clip Engine...");
      createBtn.disabled = true;
      createBtn.innerHTML = `<span class="spinner"></span> Processing...`;

      const res = await fetch("/api/generate-all", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.detail || "Failed to start generation.");
      }

      const data = await res.json();
      if (data.job_id) {
        pollJob(data.job_id);
      }
    } catch (err) {
      alert("Error: " + err.message);
      resetJobUI();
    }
  });

  function startJobUI(initialTitle) {
    progressCard.style.display = "block";
    progressCard.scrollIntoView({ behavior: "smooth" });
    progressTitle.innerText = initialTitle;
    progressPct.innerText = "0%";
    progressFill.style.width = "0%";
    terminalLogs.innerHTML = `<p>[${new Date().toLocaleTimeString()}] Engine initialized.</p>`;
    resultsSection.style.display = "none";
    clipsGrid.innerHTML = "";
    updateStepper(1);
  }

  function resetJobUI() {
    createBtn.disabled = false;
    createBtn.innerHTML = `<span>✨ Create Viral Shorts</span>`;
  }

  function updateStepper(activeStepIndex) {
    stepItems.forEach((step, idx) => {
      const stepNum = idx + 1;
      step.classList.remove("active", "completed");
      if (stepNum < activeStepIndex) {
        step.classList.add("completed");
      } else if (stepNum === activeStepIndex) {
        step.classList.add("active");
      }
    });
  }

  function pollJob(jobId) {
    if (activePollInterval) clearInterval(activePollInterval);

    activePollInterval = setInterval(async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}`);
        if (!res.ok) return;

        const job = await res.json();
        progressPct.innerText = `${job.progress}%`;
        progressFill.style.width = `${job.progress}%`;
        progressTitle.innerText = job.message || "Processing video...";

        if (job.progress < 20) updateStepper(1);
        else if (job.progress < 40) updateStepper(2);
        else if (job.progress < 70) updateStepper(3);
        else if (job.progress < 95) updateStepper(4);
        else updateStepper(5);

        if (job.logs && job.logs.length > 0) {
          terminalLogs.innerHTML = job.logs
            .map((log) => `<p>[${new Date().toLocaleTimeString()}] ${escapeHtml(log)}</p>`)
            .join("");
          terminalLogs.scrollTop = terminalLogs.scrollHeight;
        }

        if (job.status === "completed") {
          clearInterval(activePollInterval);
          resetJobUI();
          updateStepper(5);
          renderResults(job.clips || []);
        } else if (job.status === "failed") {
          clearInterval(activePollInterval);
          resetJobUI();
          alert("Generation failed: " + (job.error || "Unknown error"));
        }
      } catch (err) {
        console.error("Poll error:", err);
      }
    }, 1200);
  }

  function renderResults(clips) {
    resultsSection.style.display = "block";
    resultsSection.scrollIntoView({ behavior: "smooth" });
    clipsCountSpan.innerText = `(${clips.length})`;

    if (clips.length === 0) {
      clipsGrid.innerHTML = `<p style="grid-column: 1/-1; text-align: center; color: var(--text-muted);">No clips generated.</p>`;
      return;
    }

    clipsGrid.innerHTML = clips.map((clip) => createClipCard(clip)).join("");
  }

  function createClipCard(clip) {
    const scoreColor = clip.virality_score >= 90 ? "#fbbf24" : "#34d399";
    const hashtagsHtml = (clip.hashtags || [])
      .map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`)
      .join("");

    return `
      <div class="clip-card">
        <div class="video-container">
          <div class="virality-badge" style="border-color: ${scoreColor}; color: ${scoreColor};">
            🔥 ${clip.virality_score}/100 Virality
          </div>
          <div class="duration-badge">${Math.round(clip.duration)}s</div>
          <video 
            class="video-player" 
            controls 
            preload="metadata"
            poster="${clip.thumbnail_url || ''}"
          >
            <source src="${clip.video_url}" type="video/mp4">
            Your browser does not support the video tag.
          </video>
        </div>
        <div class="clip-body">
          <div class="clip-title-row">
            <h3 class="clip-title">${escapeHtml(clip.title)}</h3>
            <button class="btn-icon" title="Copy Title" onclick="copyText('${escapeQuotes(clip.title)}')">
              📋
            </button>
          </div>
          
          <div class="hook-box">
            <div class="hook-label">Hook & Retention Trigger</div>
            <div class="hook-text">"${escapeHtml(clip.hook)}"</div>
          </div>

          <p class="explanation-text">${escapeHtml(clip.explanation)}</p>

          <div class="hashtags-row">
            ${hashtagsHtml}
          </div>

          <div class="clip-actions">
            <a href="${clip.download_url}" download class="btn btn-primary">
              ⬇ Download MP4
            </a>
            ${clip.subtitles_url ? `
              <a href="${clip.subtitles_url}" download class="btn btn-secondary" title="Download Subtitles">
                📄 SRT
              </a>
            ` : ""}
          </div>
        </div>
      </div>
    `;
  }

  window.copyText = function(text) {
    navigator.clipboard.writeText(text).then(() => {
      alert("Title copied to clipboard!");
    });
  };

  function escapeHtml(text) {
    if (!text) return "";
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function escapeQuotes(text) {
    if (!text) return "";
    return text.replace(/'/g, "\\'").replace(/"/g, '&quot;');
  }
});
