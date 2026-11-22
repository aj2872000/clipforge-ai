document.addEventListener("DOMContentLoaded", () => {
  const urlInput = document.getElementById("urlInput");
  const createBtn = document.getElementById("createBtn");
  const demoBtn = document.getElementById("demoBtn");

  // Options
  const targetDuration = document.getElementById("targetDuration");
  const maxClips = document.getElementById("maxClips");
  const layoutSelect = document.getElementById("layoutSelect");
  const captionStyle = document.getElementById("captionStyle");
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
  let hasScrolledToResults = false;
  const renderedClipIds = new Set();

  const keyStatusBadge = document.getElementById("keyStatusBadge");
  const saveKeyBtn = document.getElementById("saveKeyBtn");

  // Load saved key from localStorage if available
  const storedGeminiKey = localStorage.getItem("gemini_api_key");
  if (storedGeminiKey && apiKeyInput) {
    apiKeyInput.value = storedGeminiKey;
  }

  // Check if Gemini API key is configured on server via .env
  fetch("/api/config")
    .then((r) => r.json())
    .then((data) => {
      if (data.has_gemini_key) {
        if (keyStatusBadge) {
          keyStatusBadge.style.display = "inline-block";
          keyStatusBadge.innerText = "✓ Configured (.env)";
        }
        if (apiKeyInput && !apiKeyInput.value) {
          apiKeyInput.placeholder = "●●●●●●●● (Loaded from .env)";
        }
      }
    })
    .catch(() => {});

  // Handle Save API Key button click
  if (saveKeyBtn && apiKeyInput) {
    saveKeyBtn.addEventListener("click", async () => {
      const keyVal = apiKeyInput.value.trim();
      if (!keyVal) {
        alert("Please paste your Google Gemini API key first.");
        return;
      }
      try {
        saveKeyBtn.disabled = true;
        saveKeyBtn.innerText = "Saving...";
        const res = await fetch("/api/config/key", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ gemini_api_key: keyVal })
        });
        const result = await res.json();
        localStorage.setItem("gemini_api_key", keyVal);
        if (keyStatusBadge) {
          keyStatusBadge.style.display = "inline-block";
          keyStatusBadge.innerText = "✓ Saved";
        }
        alert("Gemini API Key successfully saved to .env!");
      } catch (e) {
        localStorage.setItem("gemini_api_key", keyVal);
        alert("Saved key to browser storage.");
      } finally {
        saveKeyBtn.disabled = false;
        saveKeyBtn.innerText = "💾 Save";
      }
    });
  }

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
      ai_provider: aiProvider.value,
      api_key: (apiKeyInput && apiKeyInput.value.trim()) || localStorage.getItem("gemini_api_key") || null,
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
    hasScrolledToResults = false;
    renderedClipIds.clear();
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

    const checkStatus = async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}?_t=${Date.now()}`, {
          cache: "no-store",
          headers: { "Pragma": "no-cache", "Cache-Control": "no-cache" }
        });
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

        // Real-time incremental clip availability:
        // As soon as each clip is completed, immediately display it so the user can watch & download right away!
        if (job.clips && job.clips.length > 0) {
          renderIncrementalResults(job.clips, job.status !== "completed");
        }

        if (job.status === "completed") {
          clearInterval(activePollInterval);
          resetJobUI();
          updateStepper(5);
          if (job.clips && job.clips.length > 0) {
            renderIncrementalResults(job.clips, false);
          }
        } else if (job.status === "failed") {
          clearInterval(activePollInterval);
          resetJobUI();
          alert("Generation failed: " + (job.error || "Unknown error"));
        }
      } catch (err) {
        console.error("Poll error:", err);
      }
    };

    checkStatus();
    activePollInterval = setInterval(checkStatus, 1200);
  }

  function renderIncrementalResults(clips, isStillProcessing) {
    if (!clips || clips.length === 0) return;

    resultsSection.style.display = "block";

    const completedCount = clips.filter((c) => c.status === "completed").length;
    clipsCountSpan.innerText = isStillProcessing
      ? `(${completedCount}/${clips.length} ready — processing batch...)`
      : `(${completedCount})`;

    // First time an individual clip finishes: gently scroll to show download button
    if (completedCount >= 1 && !hasScrolledToResults) {
      resultsSection.scrollIntoView({ behavior: "smooth" });
      hasScrolledToResults = true;
    }

    clips.forEach((clip) => {
      const existingCard = document.getElementById(`clip-card-${clip.id}`);
      if (!existingCard) {
        // Card doesn't exist yet: create and append it to queue grid
        clipsGrid.insertAdjacentHTML("beforeend", createClipCard(clip));
      } else if (existingCard.dataset.status !== clip.status) {
        // Status changed (e.g. queued -> rendering -> completed): update card and unlock download button
        existingCard.outerHTML = createClipCard(clip);
      }
    });
  }

  function createClipCard(clip) {
    const isCompleted = clip.status === "completed" && clip.download_url;
    const isRendering = clip.status === "rendering";
    const scoreColor = clip.virality_score >= 90 ? "#fbbf24" : "#34d399";
    const hashtagsHtml = (clip.hashtags || [])
      .map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`)
      .join("");

    let videoContent = "";
    let statusBadge = "";
    let actionButtons = "";

    if (isCompleted) {
      statusBadge = `<span class="status-pill ready">✓ Ready to Download</span>`;
      videoContent = `
        <video 
          class="video-player" 
          controls 
          preload="metadata"
          poster="${clip.thumbnail_url || ''}"
        >
          <source src="${clip.video_url}" type="video/mp4">
          Your browser does not support the video tag.
        </video>
      `;
      actionButtons = `
        <a href="${clip.download_url}" download class="btn btn-primary" style="display: inline-flex; align-items: center; justify-content: center; gap: 6px;">
          <span>⬇ Download (Max Quality)</span>
        </a>
        ${clip.subtitles_url ? `
          <a href="${clip.subtitles_url}" download class="btn btn-secondary" title="Download Subtitles">
            📄 SRT
          </a>
        ` : ""}
      `;
    } else if (isRendering) {
      statusBadge = `<span class="status-pill rendering"><span class="spinner" style="width:10px;height:10px;border-width:1.5px;"></span> Rendering</span>`;
      videoContent = `
        <div class="video-placeholder">
          <div class="spinner" style="width: 42px; height: 42px; border-width: 3.5px; border-top-color: #6366f1; margin-bottom: 14px;"></div>
          <div style="font-weight: 700; color: #e0e7ff; font-size: 15px; margin-bottom: 6px;">Rendering in Background...</div>
          <div style="font-size: 12px; color: #94a3b8; max-width: 220px; line-height: 1.4;">
            Generating 9:16 framing & subtitles. Download button will unlock immediately!
          </div>
        </div>
      `;
      actionButtons = `
        <button class="btn btn-disabled" disabled style="display: inline-flex; align-items: center; justify-content: center; gap: 8px;">
          <span class="spinner" style="width: 14px; height: 14px; border-width: 2px;"></span>
          <span>Rendering Clip...</span>
        </button>
      `;
    } else {
      // Queued in batch
      statusBadge = `<span class="status-pill queued">⏳ Queued</span>`;
      videoContent = `
        <div class="video-placeholder">
          <div style="font-size: 34px; opacity: 0.6; margin-bottom: 8px;">⏳</div>
          <div style="font-weight: 600; color: #cbd5e1; font-size: 14px; margin-bottom: 4px;">Queued in Batch</div>
          <div style="font-size: 12px; color: #64748b; max-width: 200px; line-height: 1.4;">
            Will begin processing as soon as earlier clips finish.
          </div>
        </div>
      `;
      actionButtons = `
        <button class="btn btn-disabled" disabled style="display: inline-flex; align-items: center; justify-content: center; gap: 6px;">
          <span>⏳ Queued</span>
        </button>
      `;
    }

    return `
      <div class="clip-card" id="clip-card-${clip.id}" data-status="${clip.status || 'queued'}">
        <div class="video-container">
          <div class="virality-badge" style="border-color: ${scoreColor}; color: ${scoreColor};">
            🔥 ${clip.virality_score}/100 Virality
          </div>
          <div style="position: absolute; top: 12px; left: 12px; z-index: 10;">
            ${statusBadge}
          </div>
          <div class="duration-badge">${Math.round(clip.duration)}s</div>
          ${videoContent}
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

          <div class="clip-actions" id="clip-actions-${clip.id}">
            ${actionButtons}
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
