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
<div id="scan-output" class="log-output">
  <div class="log-line muted">Click "Start Scan" to begin.</div>
</div>

<script>
var sseSource = null;
document.addEventListener("htmx:afterSwap", function(e) {
  if (e.detail.target && e.detail.target.id === "scan-status" && e.detail.xhr) {
    try {
      var data = JSON.parse(e.detail.xhr.responseText);
      e.detail.target.innerHTML = '<span class="' + (data.ok ? 'text-ok' : 'text-err') + '">' + data.message + '</span>';
      if (data.ok) {
        // Connect to SSE stream when scan starts
        var el = document.getElementById("scan-output");
        el.innerHTML = "";
        if (sseSource) sseSource.close();
        sseSource = new EventSource("/api/scan/stream");
        sseSource.onmessage = function(evt) {
          el.innerHTML += evt.data;
          el.scrollTop = el.scrollHeight;
        };
        sseSource.onerror = function() {
          sseSource.close();
          sseSource = null;
        };
      }
    } catch(err) {}
  }
});
</script>
