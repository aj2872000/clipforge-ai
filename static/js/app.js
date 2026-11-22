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
  const clipsById = {};
  let currentYtStatus = { connected: false, has_credentials: false };

  // YouTube UI elements
  const connectYtBtn = document.getElementById("connectYtBtn");
  const connectYtBtnText = document.getElementById("connectYtBtnText");
  const ytConnectedBadge = document.getElementById("ytConnectedBadge");
  const ytChannelAvatar = document.getElementById("ytChannelAvatar");
  const ytChannelName = document.getElementById("ytChannelName");
  const disconnectYtBtn = document.getElementById("disconnectYtBtn");

  // YouTube Upload Modal elements
  const ytUploadModal = document.getElementById("ytUploadModal");
  const ytUploadModalClose = document.getElementById("ytUploadModalClose");
  const ytCancelBtn = document.getElementById("ytCancelBtn");
  const ytModalChannelAvatar = document.getElementById("ytModalChannelAvatar");
  const ytModalChannelName = document.getElementById("ytModalChannelName");
  const ytUploadClipId = document.getElementById("ytUploadClipId");
  const ytUploadFilename = document.getElementById("ytUploadFilename");
  const ytUploadTitle = document.getElementById("ytUploadTitle");
  const ytTitleCount = document.getElementById("ytTitleCount");
  const ytUploadDesc = document.getElementById("ytUploadDesc");
  const ytDescCount = document.getElementById("ytDescCount");
  const ytUploadTags = document.getElementById("ytUploadTags");
  const ytTagsCount = document.getElementById("ytTagsCount");
  const ytUploadCategory = document.getElementById("ytUploadCategory");
  const ytUploadPrivacy = document.getElementById("ytUploadPrivacy");
  const ytUploadMadeForKids = document.getElementById("ytUploadMadeForKids");
  const ytUploadProgressBox = document.getElementById("ytUploadProgressBox");
  const ytProgressFill = document.getElementById("ytProgressFill");
  const ytProgressStatusText = document.getElementById("ytProgressStatusText");
  const ytProgressPctText = document.getElementById("ytProgressPctText");
  const ytProgressDetailText = document.getElementById("ytProgressDetailText");
  const ytUploadSuccessBox = document.getElementById("ytUploadSuccessBox");
  const ytSuccessVideoLink = document.getElementById("ytSuccessVideoLink");
  const ytSubmitUploadBtn = document.getElementById("ytSubmitUploadBtn");

  // YouTube Credentials Modal elements
  const ytCredentialsModal = document.getElementById("ytCredentialsModal");
  const ytCredModalClose = document.getElementById("ytCredModalClose");
  const ytCredCancelBtn = document.getElementById("ytCredCancelBtn");
  const ytClientIdInput = document.getElementById("ytClientIdInput");
  const ytClientSecretInput = document.getElementById("ytClientSecretInput");
  const ytCredSaveBtn = document.getElementById("ytCredSaveBtn");

  let activeUploadPollInterval = null;

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

    clips.forEach((c) => {
      clipsById[c.id] = c;
    });

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
          <span>⬇ Download</span>
        </a>
        <button type="button" class="btn btn-yt-upload" onclick="window.openYouTubeUploadModal('${clip.id}')" title="Publish directly to YouTube Shorts">
          <span>▶ Upload</span>
        </button>
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

  // =========================================================================
  // YouTube Data API v3 & Upload Management
  // =========================================================================

  // Check YouTube connection status on load
  function checkYouTubeStatus() {
    fetch("/api/youtube/status")
      .then((r) => r.json())
      .then((status) => {
        currentYtStatus = status;
        if (status.connected) {
          connectYtBtn.style.display = "none";
          ytConnectedBadge.style.display = "inline-flex";
          ytChannelName.innerText = status.channel_title || "Connected Channel";
          if (status.channel_thumbnail) {
            ytChannelAvatar.src = status.channel_thumbnail;
            ytChannelAvatar.style.display = "block";
          } else {
            ytChannelAvatar.style.display = "none";
          }
        } else {
          ytConnectedBadge.style.display = "none";
          connectYtBtn.style.display = "inline-flex";
          connectYtBtnText.innerText = status.has_credentials ? "Connect YouTube" : "Setup YouTube";
        }
      })
      .catch((err) => console.error("YouTube status check error:", err));
  }
  checkYouTubeStatus();

  // Listen for message from OAuth popup callback
  window.addEventListener("message", (event) => {
    if (event.data && event.data.type === "YOUTUBE_CONNECTED") {
      checkYouTubeStatus();
    }
  });

  // Connect YouTube Account button click
  if (connectYtBtn) {
    connectYtBtn.addEventListener("click", async () => {
      if (!currentYtStatus.has_credentials) {
        // Open credentials setup modal
        ytCredentialsModal.classList.add("active");
        return;
      }
      try {
        connectYtBtn.disabled = true;
        connectYtBtnText.innerText = "Connecting...";
        const res = await fetch("/api/youtube/auth-url");
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Failed to get auth URL");
        }
        const data = await res.json();
        // Open OAuth consent window in popup
        const width = 600, height = 720;
        const left = (window.innerWidth - width) / 2;
        const top = (window.innerHeight - height) / 2;
        window.open(data.auth_url, "yt_oauth", `width=${width},height=${height},top=${top},left=${left}`);
      } catch (err) {
        alert("YouTube Auth Error: " + err.message);
        ytCredentialsModal.classList.add("active");
      } finally {
        connectYtBtn.disabled = false;
        connectYtBtnText.innerText = "Connect YouTube";
      }
    });
  }

  // Disconnect YouTube Account button click
  if (disconnectYtBtn) {
    disconnectYtBtn.addEventListener("click", async () => {
      if (!confirm("Are you sure you want to disconnect this YouTube channel?")) return;
      try {
        await fetch("/api/youtube/disconnect", { method: "POST" });
        checkYouTubeStatus();
      } catch (err) {
        alert("Disconnect failed: " + err.message);
      }
    });
  }

  // Save OAuth credentials modal button click
  if (ytCredSaveBtn) {
    ytCredSaveBtn.addEventListener("click", async () => {
      const clientId = ytClientIdInput.value.trim();
      const clientSecret = ytClientSecretInput.value.trim();
      if (!clientId || !clientSecret) {
        alert("Please enter both OAuth Client ID and Client Secret.");
        return;
      }

      try {
        ytCredSaveBtn.disabled = true;
        ytCredSaveBtn.innerText = "Saving...";
        const res = await fetch("/api/youtube/credentials", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client_id: clientId, client_secret: clientSecret })
        });
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Failed to save credentials");
        }
        ytCredentialsModal.classList.remove("active");
        currentYtStatus.has_credentials = true;
        // Trigger auth flow immediately
        connectYtBtn.click();
      } catch (err) {
        alert("Error saving credentials: " + err.message);
      } finally {
        ytCredSaveBtn.disabled = false;
        ytCredSaveBtn.innerText = "💾 Save & Authorize";
      }
    });
  }

  if (ytCredModalClose) ytCredModalClose.addEventListener("click", () => ytCredentialsModal.classList.remove("active"));
  if (ytCredCancelBtn) ytCredCancelBtn.addEventListener("click", () => ytCredentialsModal.classList.remove("active"));
  if (ytUploadModalClose) ytUploadModalClose.addEventListener("click", () => closeUploadModal());
  if (ytCancelBtn) ytCancelBtn.addEventListener("click", () => closeUploadModal());

  function closeUploadModal() {
    ytUploadModal.classList.remove("active");
    if (activeUploadPollInterval) {
      clearInterval(activeUploadPollInterval);
      activeUploadPollInterval = null;
    }
  }

  // Character counters for upload form
  if (ytUploadTitle) {
    ytUploadTitle.addEventListener("input", () => {
      ytTitleCount.innerText = `${ytUploadTitle.value.length}/100`;
    });
  }
  if (ytUploadDesc) {
    ytUploadDesc.addEventListener("input", () => {
      ytDescCount.innerText = `${ytUploadDesc.value.length}/5000`;
    });
  }
  if (ytUploadTags) {
    ytUploadTags.addEventListener("input", () => {
      ytTagsCount.innerText = `${ytUploadTags.value.length}/500`;
    });
  }

  // Open YouTube Upload Modal for a specific clip
  window.openYouTubeUploadModal = function(clipId) {
    const clip = clipsById[clipId];
    if (!clip) {
      alert("Clip metadata not found.");
      return;
    }

    if (!currentYtStatus.connected) {
      if (confirm("You need to connect your YouTube Channel before uploading. Connect now?")) {
        connectYtBtn.click();
      }
      return;
    }

    // Populate channel destination
    if (currentYtStatus.channel_thumbnail) {
      ytModalChannelAvatar.src = currentYtStatus.channel_thumbnail;
      ytModalChannelAvatar.style.display = "block";
    } else {
      ytModalChannelAvatar.style.display = "none";
    }
    ytModalChannelName.innerText = `${currentYtStatus.channel_title} (${currentYtStatus.channel_handle || 'Channel'})`;

    // Extract filename from video_url
    const rawFilename = (clip.video_url || "").replace("/api/stream/", "").trim();
    ytUploadClipId.value = clip.id;
    ytUploadFilename.value = rawFilename;

    // Pre-populate Title: ensure #Shorts is present and under 100 chars
    let title = (clip.title || "").trim();
    if (!title.toLowerCase().includes("#shorts") && title.length + 8 <= 100) {
      title += " #Shorts";
    }
    ytUploadTitle.value = title.slice(0, 100);
    ytTitleCount.innerText = `${ytUploadTitle.value.length}/100`;

    // Pre-populate Description
    let descParts = [];
    if (clip.hook) descParts.push(clip.hook);
    if (clip.explanation) descParts.push(clip.explanation);
    descParts.push("Created automatically with ClipForge AI (Opus Clip Engine).");
    if (clip.hashtags && clip.hashtags.length > 0) {
      const tagsStr = clip.hashtags.map(t => t.startsWith("#") ? t : "#" + t).join(" ");
      descParts.push(tagsStr);
    }
    ytUploadDesc.value = descParts.join("\n\n").slice(0, 5000);
    ytDescCount.innerText = `${ytUploadDesc.value.length}/5000`;

    // Pre-populate Tags (without #)
    const cleanTags = (clip.hashtags || []).map(t => t.replace(/^#/, "").trim()).filter(Boolean);
    ytUploadTags.value = cleanTags.join(", ").slice(0, 500);
    ytTagsCount.innerText = `${ytUploadTags.value.length}/500`;

    // Reset Category, Privacy, Kids toggle
    ytUploadCategory.value = "24"; // Entertainment
    ytUploadPrivacy.value = "private";
    ytUploadMadeForKids.checked = false;

    // Reset upload status boxes
    ytUploadProgressBox.style.display = "none";
    ytUploadSuccessBox.style.display = "none";
    ytSubmitUploadBtn.disabled = false;
    ytSubmitUploadBtn.innerHTML = `<span>🚀 Publish to YouTube</span>`;

    // Show modal
    ytUploadModal.classList.add("active");
  };

  // Submit YouTube Upload Handler
  if (ytSubmitUploadBtn) {
    ytSubmitUploadBtn.addEventListener("click", async () => {
      const title = ytUploadTitle.value.trim();
      if (!title) {
        alert("Please enter a title for the video.");
        ytUploadTitle.focus();
        return;
      }

      const filename = ytUploadFilename.value.trim();
      if (!filename) {
        alert("Invalid clip video file reference.");
        return;
      }

      const tagsList = ytUploadTags.value
        .split(",")
        .map(t => t.trim())
        .filter(Boolean);

      const payload = {
        clip_id: ytUploadClipId.value,
        filename: filename,
        title: title,
        description: ytUploadDesc.value.trim(),
        tags: tagsList,
        category_id: ytUploadCategory.value,
        privacy_status: ytUploadPrivacy.value,
        made_for_kids: ytUploadMadeForKids.checked
      };

      try {
        ytSubmitUploadBtn.disabled = true;
        ytSubmitUploadBtn.innerHTML = `<span class="spinner"></span> Uploading...`;
        ytUploadProgressBox.style.display = "flex";
        ytUploadSuccessBox.style.display = "none";
        ytProgressFill.style.width = "5%";
        ytProgressPctText.innerText = "5%";
        ytProgressStatusText.innerText = "Initializing upload...";
        ytProgressDetailText.innerText = "Creating resumable upload session on YouTube...";

        const res = await fetch("/api/youtube/upload", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });

        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Failed to start upload");
        }

        const data = await res.json();
        pollUploadStatus(data.task_id);
      } catch (err) {
        alert("Upload Failed: " + err.message);
        ytSubmitUploadBtn.disabled = false;
        ytSubmitUploadBtn.innerHTML = `<span>🚀 Publish to YouTube</span>`;
        ytUploadProgressBox.style.display = "none";
      }
    });
  }

  // Poll Resumable Upload Status
  function pollUploadStatus(taskId) {
    if (activeUploadPollInterval) clearInterval(activeUploadPollInterval);

    activeUploadPollInterval = setInterval(async () => {
      try {
        const res = await fetch(`/api/youtube/upload-status/${taskId}?_t=${Date.now()}`, {
          cache: "no-store"
        });
        if (!res.ok) return;

        const data = await res.json();
        ytProgressFill.style.width = `${data.progress}%`;
        ytProgressPctText.innerText = `${data.progress}%`;
        ytProgressDetailText.innerText = data.message || "Streaming video chunks...";

        if (data.status === "completed") {
          clearInterval(activeUploadPollInterval);
          activeUploadPollInterval = null;
          ytUploadProgressBox.style.display = "none";
          ytUploadSuccessBox.style.display = "flex";
          ytSuccessVideoLink.href = data.video_url;
          ytSuccessVideoLink.innerHTML = `<span>${data.video_url}</span> <span>↗</span>`;
          ytSubmitUploadBtn.disabled = false;
          ytSubmitUploadBtn.innerHTML = `<span>✓ Published</span>`;
        } else if (data.status === "failed") {
          clearInterval(activeUploadPollInterval);
          activeUploadPollInterval = null;
          ytUploadProgressBox.style.display = "none";
          ytSubmitUploadBtn.disabled = false;
          ytSubmitUploadBtn.innerHTML = `<span>🚀 Retry Upload</span>`;
          alert("YouTube Upload Failed: " + (data.error || "Unknown error occurred."));
        }
      } catch (err) {
        console.error("Upload status poll error:", err);
      }
    }, 800);
  }
});
