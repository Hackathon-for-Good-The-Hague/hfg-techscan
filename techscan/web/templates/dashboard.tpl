% rebase("layout.tpl", cfg=cfg)

<h1>Dashboard</h1>

% if run_info:
<div class="info-bar">
  <span>Last scan: {{run_info.get("finished", "never")[:19].replace("T", " ")}}</span>
  <span>Backend: {{run_info.get("backend", "—")}}</span>
  <span>Model: {{run_info.get("model", "—")}}</span>
  % if run_info.get("total_cost_usd"):
  <span>Cost: ${{run_info["total_cost_usd"]}}</span>
  % end
</div>
% end

<div class="section">
  <h2>Results</h2>
  <div id="results-list" hx-get="/api/results" hx-trigger="load" hx-swap="innerHTML">
    Loading...
  </div>
</div>

<div class="section">
  <h2>Teams</h2>
  % if teams:
  <table class="team-table">
    <thead>
      <tr>
        <th>Project</th>
        <th>Challenge</th>
        <th>Status</th>
        <th>Score</th>
        <th>Flags</th>
        <th>Review</th>
      </tr>
    </thead>
    <tbody>
      % for t in teams:
      <tr>
        <td><strong>{{t["project"]}}</strong></td>
        <td>{{t["challenge"]}}</td>
        <td><span class="chip chip-{{t['status']}}">{{t["status"]}}</span></td>
        <td>{{t["score"] if t["score"] != "" else "—"}}</td>
        <td>
          % if t["flags_high"] > 0:
          <span class="chip chip-high">{{t["flags_high"]}} high</span>
          % end
          {{t["flags_total"]}} total
        </td>
        <td>{{t["review_status"] or "—"}}</td>
      </tr>
      % end
    </tbody>
  </table>
  % else:
  <p>No scan results yet. <a href="/scan">Run a scan</a> to get started.</p>
  % end
</div>

<template id="results-template">
  <script>
    document.addEventListener("htmx:afterSwap", function(e) {
      if (e.detail.target.id === "results-list") {
        try {
          var data = JSON.parse(e.detail.xhr.responseText);
          var html = "";
          if (data.files && data.files.length > 0) {
            data.files.forEach(function(f) {
              html += '<a href="/results/' + f.name + '" class="result-link" target="_blank">' +
                      f.name + ' <span class="size">(' + f.size_kb + ' KB)</span></a> ';
            });
          } else {
            html = "<p>No output files yet.</p>";
          }
          document.getElementById("results-list").innerHTML = html;
        } catch(err) {}
      }
    });
  </script>
</template>
