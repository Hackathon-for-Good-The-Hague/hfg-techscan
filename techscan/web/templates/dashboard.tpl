% rebase("layout.tpl", cfg=cfg, current=current)

<!-- Status banner -->
% if run_info:
<div class="banner banner-ok">
  <span class="banner-icon">&#10003;</span>
  <span><strong>Scan complete</strong> &mdash; {{stats["total"]}} teams scanned, {{stats["reviewed"]}} reviewed</span>
  <span class="banner-time">{{run_info.get("finished", "")[:19].replace("T", " ")}}</span>
</div>
% else:
<div class="banner banner-neutral">
  <span class="banner-icon">&#9432;</span>
  <span>No scan results yet. <a href="/config">Configure your event</a> or <a href="/scan">run a scan</a>.</span>
</div>
% end

% if teams:
<!-- Summary cards -->
<div class="cards">
  <div class="card">
    <div class="card-value">{{stats["total"]}}</div>
    <div class="card-label">Teams</div>
    % if stats["unreachable"]:
    <div class="card-sub card-sub-warn">{{stats["unreachable"]}} unreachable</div>
    % else:
    <div class="card-sub">all ready</div>
    % end
  </div>
  <div class="card">
    <div class="card-value">{{stats["reviewed"]}}</div>
    <div class="card-label">AI Reviewed</div>
    % not_reviewed = stats["total"] - stats["reviewed"]
    % if not_reviewed:
    <div class="card-sub">{{not_reviewed}} checks only</div>
    % end
  </div>
  <div class="card">
    <div class="card-value {{'' if not stats['flags_high'] else 'card-value-warn'}}">{{stats["flags_total"]}}</div>
    <div class="card-label">Flags</div>
    <div class="card-sub">
      % parts = []
      % if stats["flags_high"]:
      %   parts.append(f'<span class="text-high">{stats["flags_high"]} high</span>')
      % end
      % if stats["flags_medium"]:
      %   parts.append(f'{stats["flags_medium"]} medium')
      % end
      % if stats["flags_low"]:
      %   parts.append(f'{stats["flags_low"]} low')
      % end
      {{!" &middot; ".join(parts) if parts else "none"}}
    </div>
  </div>
  <div class="card">
    % if stats["score_min"] is not None:
    <div class="card-value">{{stats["score_min"]}} &ndash; {{stats["score_max"]}}</div>
    <div class="card-label">Score Range</div>
    <div class="card-sub">median {{stats["score_median"]}}</div>
    % else:
    <div class="card-value">&mdash;</div>
    <div class="card-label">Scores</div>
    <div class="card-sub">no AI reviews</div>
    % end
  </div>
</div>

<!-- Quick actions -->
<div class="actions">
  % if jury_packs:
  <div class="action-group">
    <div class="action-label">Jury Packs</div>
    % for jp in jury_packs:
    <a href="/results/{{jp['path']}}" target="_blank" class="btn btn-primary">{{jp["label"]}}</a>
    % end
  </div>
  % end
  % if scorecards:
  <div class="action-group">
    <div class="action-label">Scorecard</div>
    % for sc in scorecards:
    <a href="/results/{{sc['path']}}" class="btn btn-secondary" download>Download XLSX</a>
    % end
  </div>
  % end
  % if challenge_reports:
  <div class="action-group">
    <div class="action-label">Challenge Reports</div>
    % for cr in challenge_reports:
    <a href="/results/{{cr['path']}}" target="_blank" class="btn btn-outline">{{cr["label"]}}</a>
    % end
  </div>
  % end
</div>

<!-- Teams table -->
<div class="section">
  <div class="section-header">
    <h2>Teams</h2>
    <input type="text" id="team-search" placeholder="Search teams..." class="search-input" oninput="filterTeams()">
  </div>
  <table class="team-table" id="team-table">
    <thead>
      <tr>
        <th onclick="sortTable(0)">Project</th>
        <th onclick="sortTable(1)">Challenge</th>
        <th onclick="sortTable(2)">Score</th>
        <th onclick="sortTable(3)">Flags</th>
        <th>Review</th>
        <th>Stack</th>
      </tr>
    </thead>
    <tbody>
      % for t in teams:
      <tr class="team-row" hx-get="/api/team/{{t['slug']}}" hx-target="#team-panel-content" hx-swap="innerHTML" onclick="openPanel()">
        <td>
          <strong>{{t["project"]}}</strong>
          % if t["status"] != "ready":
          <span class="chip chip-err">{{t["status"]}}</span>
          % end
        </td>
        <td>{{t["challenge_name"]}}</td>
        <td>
          % if isinstance(t["score"], (int, float)) and t["score"]:
          <div class="score-bar-wrap">
            <div class="score-bar" style="width: {{int(t['score'] * 10)}}%"
                 data-color="{{'ok' if t['score'] >= 7 else 'warn' if t['score'] >= 5 else 'err'}}"></div>
            <span class="score-num">{{t["score"]}}</span>
          </div>
          % else:
          <span class="muted">&mdash;</span>
          % end
        </td>
        <td>
          % if t["flags_high"]:
          <span class="flag-dot flag-high" title="{{'; '.join(t['flag_titles'])}}">{{t["flags_high"]}}</span>
          % end
          % if t["flags_medium"]:
          <span class="flag-dot flag-medium">{{t["flags_medium"]}}</span>
          % end
          % if t["flags_low"]:
          <span class="flag-dot flag-low">{{t["flags_low"]}}</span>
          % end
          % if not t["flags_total"]:
          <span class="muted">none</span>
          % end
        </td>
        <td><span class="muted">{{t["review_label"]}}</span></td>
        <td><span class="muted">{{", ".join(t["languages"]) if t["languages"] else "—"}}</span></td>
      </tr>
      % end
    </tbody>
  </table>
</div>

% else:
<!-- Empty state -->
<div class="empty-state">
  <div class="empty-icon">&#9881;</div>
  <h2>Get started</h2>
  <p>No scan results yet. Here's how to set up your event:</p>
  <div class="empty-steps">
    <div class="empty-step">
      <div class="step-num">1</div>
      <div>
        <strong><a href="/config">Configure your event</a></strong>
        <p class="muted">Edit the YAML with your dates, challenges, and rubric</p>
      </div>
    </div>
    <div class="empty-step">
      <div class="step-num">2</div>
      <div>
        <strong><a href="/scan">Run a scan</a></strong>
        <p class="muted">Point it at a submissions CSV and hit Start</p>
      </div>
    </div>
    <div class="empty-step">
      <div class="step-num">3</div>
      <div>
        <strong>Try the demo first</strong>
        <p class="muted"><code>python -m techscan demo && python -m techscan selftest</code></p>
      </div>
    </div>
  </div>
</div>
% end

<!-- Team detail slide-over panel -->
<div id="team-panel" class="panel" onclick="if(event.target===this)closePanel()">
  <div class="panel-content" id="team-panel-content">
  </div>
  <button class="panel-close" onclick="closePanel()">&times;</button>
</div>

<script>
function openPanel() {
  document.getElementById("team-panel").classList.add("open");
}
function closePanel() {
  document.getElementById("team-panel").classList.remove("open");
}
document.addEventListener("keydown", function(e) {
  if (e.key === "Escape") closePanel();
});
function filterTeams() {
  var q = document.getElementById("team-search").value.toLowerCase();
  var rows = document.querySelectorAll("#team-table tbody tr");
  rows.forEach(function(r) { r.style.display = r.textContent.toLowerCase().includes(q) ? "" : "none"; });
}
var sortDir = {};
function sortTable(col) {
  sortDir[col] = !(sortDir[col] || false);
  var tbody = document.querySelector("#team-table tbody");
  var rows = Array.from(tbody.rows);
  rows.sort(function(a, b) {
    var av = a.cells[col].textContent.trim(), bv = b.cells[col].textContent.trim();
    var an = parseFloat(av), bn = parseFloat(bv);
    if (!isNaN(an) && !isNaN(bn)) return sortDir[col] ? an - bn : bn - an;
    return sortDir[col] ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  rows.forEach(function(r) { tbody.appendChild(r); });
}
</script>
