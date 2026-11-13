% rebase("layout.tpl", cfg=cfg)

<h1>Run Scan</h1>

<form hx-post="/api/scan" hx-target="#scan-status" hx-swap="innerHTML">
  <div class="form-group">
    <label for="submissions">Submissions CSV path:</label>
    <input type="text" name="submissions" id="submissions" placeholder="submissions.csv (or leave empty for platform pull)">
  </div>
  <div class="form-group">
    <label>
      <input type="checkbox" name="skip_review"> Skip AI review (deterministic checks only)
    </label>
  </div>
  <div class="form-actions">
    <button type="submit" class="btn btn-primary" {{"disabled" if running else ""}}>
      {{"Scan running..." if running else "Start Scan"}}
    </button>
    <span id="scan-status"></span>
  </div>
</form>

<h2>Scan Output</h2>
<div id="scan-output" class="log-output"
     hx-ext="sse" sse-connect="/api/scan/stream" sse-swap="message" hx-swap="beforeend">
  <div class="log-line muted">Waiting for scan output...</div>
</div>

<script>
document.addEventListener("htmx:afterSwap", function(e) {
  if (e.detail.target.id === "scan-status") {
    try {
      var data = JSON.parse(e.detail.xhr.responseText);
      e.detail.target.innerHTML = '<span class="' + (data.ok ? 'text-ok' : 'text-err') + '">' + data.message + '</span>';
    } catch(err) {}
  }
  // Auto-scroll log output
  if (e.detail.target.id === "scan-output") {
    var el = document.getElementById("scan-output");
    el.scrollTop = el.scrollHeight;
  }
});
// Format SSE messages as log lines
document.body.addEventListener("sse:message", function(e) {
  var el = document.getElementById("scan-output");
  if (e.detail.data === "[DONE]") {
    el.innerHTML += '<div class="log-line log-done">Scan complete. <a href="/">View dashboard</a></div>';
    return;
  }
  el.innerHTML += '<div class="log-line">' + e.detail.data.replace(/</g, '&lt;') + '</div>';
  el.scrollTop = el.scrollHeight;
});
</script>
