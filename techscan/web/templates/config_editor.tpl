% rebase("layout.tpl", cfg=cfg)

<h1>Config Editor</h1>
<p class="muted">Editing: {{config_path}}</p>

<form hx-post="/api/config" hx-target="#save-status" hx-swap="innerHTML">
  <textarea name="config_text" id="config-textarea" rows="30" spellcheck="false">{{config_text}}</textarea>
  <div class="form-actions">
    <button type="submit" class="btn btn-primary">Save &amp; Validate</button>
    <span id="save-status"></span>
  </div>
</form>

<script>
document.addEventListener("htmx:afterSwap", function(e) {
  if (e.detail.target.id === "save-status") {
    try {
      var data = JSON.parse(e.detail.xhr.responseText);
      e.detail.target.innerHTML = '<span class="' + (data.ok ? 'text-ok' : 'text-err') + '">' + data.message + '</span>';
    } catch(err) {}
  }
});
</script>
