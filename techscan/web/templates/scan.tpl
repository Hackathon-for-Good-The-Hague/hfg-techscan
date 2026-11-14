% rebase("layout.tpl", cfg=cfg, current=current)

<h1>Run Scan</h1>

<!-- Pre-scan summary -->
<div class="prescan-info">
  <div class="prescan-grid">
    <div class="prescan-item">
      <span class="prescan-label">Event</span>
      <span>{{cfg.get("event", {}).get("name", "—")}}</span>
    </div>
    <div class="prescan-item">
      <span class="prescan-label">Deadline</span>
      <span>{{cfg.get("event", {}).get("deadline", "—")[:19].replace("T", " ")}}</span>
    </div>
    <div class="prescan-item">
      <span class="prescan-label">Challenges</span>
      <span>{{len(cfg.get("challenges", {}))}}</span>
    </div>
    <div class="prescan-item">
      <span class="prescan-label">Review backend</span>
      <span>{{cfg.get("review", {}).get("backend", "—")}}</span>
    </div>
  </div>
</div>

<!-- Scan form -->
<div id="scan-form">
  <form hx-post="/api/scan" hx-target="#scan-status" hx-swap="innerHTML">
    <div class="form-group">
      <label for="submissions">Submissions CSV path</label>
      <input type="text" name="submissions" id="submissions" placeholder="submissions.csv (or leave empty for platform pull)">
    </div>
    <div class="form-group">
      <label class="checkbox-label">
        <input type="checkbox" name="skip_review">
        <span>Skip AI review</span>
        <span class="muted"> — run the 13 deterministic checks only (faster, free)</span>
      </label>
    </div>
    <div class="form-actions">
      <button type="submit" class="btn btn-primary btn-lg" id="start-btn" {{"disabled" if running else ""}}>
        {{"Scan running..." if running else "Start Scan"}}
      </button>
      <span id="scan-status"></span>
    </div>
  </form>
</div>

<!-- Progress section (hidden until scan starts) -->
<div id="scan-progress" class="scan-progress" style="display:none">
  <div class="progress-header">
    <span id="progress-phase" class="progress-phase">Starting...</span>
    <span id="progress-count" class="progress-count"></span>
  </div>
  <div class="progress-bar-wrap">
    <div class="progress-bar" id="progress-bar" style="width: 0%"></div>
  </div>
</div>

<!-- Team ticker -->
<div id="team-ticker" class="team-ticker" style="display:none">
</div>

<!-- Log output (collapsed by default) -->
<details id="log-details" class="log-details">
  <summary>Show raw log</summary>
  <div id="scan-output" class="log-output"></div>
</details>

<!-- Post-scan summary (hidden until complete) -->
<div id="post-scan" class="post-scan" style="display:none">
  <div class="banner banner-ok">
    <span class="banner-icon">&#10003;</span>
    <span id="post-scan-summary"><strong>Scan complete.</strong></span>
  </div>
  <div class="actions" style="margin-top: 1rem">
    <a href="/" class="btn btn-primary btn-lg">Go to Dashboard &rarr;</a>
  </div>
</div>

<script>
var sseSource = null;
var teamCount = 0, teamsDone = 0;

document.addEventListener("htmx:afterSwap", function(e) {
  if (e.detail.target && e.detail.target.id === "scan-status" && e.detail.xhr) {
    try {
      var data = JSON.parse(e.detail.xhr.responseText);
      e.detail.target.innerHTML = '<span class="' + (data.ok ? 'text-ok' : 'text-err') + '">' + data.message + '</span>';
      if (data.ok) {
        startStream();
      }
    } catch(err) {}
  }
});

function startStream() {
  document.getElementById("start-btn").disabled = true;
  document.getElementById("start-btn").textContent = "Scanning...";
  document.getElementById("scan-progress").style.display = "block";
  document.getElementById("team-ticker").style.display = "block";
  document.getElementById("log-details").style.display = "block";
  document.getElementById("post-scan").style.display = "none";

  var el = document.getElementById("scan-output");
  el.innerHTML = "";
  teamCount = 0; teamsDone = 0;

  if (sseSource) sseSource.close();
  sseSource = new EventSource("/api/scan/stream");
  sseSource.onmessage = function(evt) {
    // Append to raw log
    el.innerHTML += evt.data;
    el.scrollTop = el.scrollHeight;

    // Parse the text from the HTML
    var tmp = document.createElement("div");
    tmp.innerHTML = evt.data;
    var text = tmp.textContent || "";

    // Detect phases
    if (text.match(/submission\(s\) from/i)) {
      var m = text.match(/(\d+) submission/);
      if (m) { teamCount = parseInt(m[1]); updateProgress(); }
      setPhase("Cloning repos...");
    }
    if (text.match(/Reviewing \d+ repo/)) setPhase("AI review...");
    if (text.match(/Scan finished/)) scanDone(text);

    // Detect team completion lines like "  Crisis Lens: ready @ abc123"
    var teamMatch = text.match(/^\s+(.+?):\s+(ready|error|unreachable|empty|no_repo)/);
    if (teamMatch) {
      teamsDone++;
      updateProgress();
      addTicker(teamMatch[1], teamMatch[2]);
    }

    // Detect review completion
    var reviewMatch = text.match(/^\s+(.+?):\s+review\s+(ok|error|cached)/);
    if (reviewMatch) {
      addTicker(reviewMatch[1], "reviewed");
    }
  };
  sseSource.onerror = function() {
    sseSource.close();
    sseSource = null;
  };
}

function setPhase(text) {
  document.getElementById("progress-phase").textContent = text;
}

function updateProgress() {
  var pct = teamCount > 0 ? Math.round((teamsDone / teamCount) * 100) : 0;
  document.getElementById("progress-bar").style.width = pct + "%";
  document.getElementById("progress-count").textContent = teamsDone + " of " + teamCount + " teams";
}

function addTicker(name, status) {
  var ticker = document.getElementById("team-ticker");
  var icon = status === "ready" ? "&#10003;" : status === "reviewed" ? "&#9733;" : "&#9888;";
  var cls = (status === "ready" || status === "reviewed") ? "ticker-ok" : "ticker-err";
  ticker.innerHTML += '<span class="ticker-item ' + cls + '">' + icon + ' ' + name + '</span>';
  ticker.scrollLeft = ticker.scrollWidth;
}

function scanDone(text) {
  if (sseSource) { sseSource.close(); sseSource = null; }
  document.getElementById("start-btn").disabled = false;
  document.getElementById("start-btn").textContent = "Start Scan";
  document.getElementById("scan-progress").style.display = "none";
  document.getElementById("post-scan").style.display = "block";
}
</script>
