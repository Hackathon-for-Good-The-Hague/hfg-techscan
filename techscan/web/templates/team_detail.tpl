<%
sub = data.get("submission", {})
review = data.get("review") or {}
facts = data.get("facts") or {}
flags = data.get("flags", [])
chal_key = sub.get("challenge", "")
%>

<div class="panel-header">
  <h2>{{sub.get("project", "Team")}}</h2>
  <div class="panel-meta">
    <span class="chip chip-challenge">{{challenge_name(chal_key)}}</span>
    % if sub.get("repo"):
    <a href="{{sub['repo']}}" target="_blank" class="panel-link">Repository &nearr;</a>
    % end
    % if sub.get("demo"):
    <a href="{{sub['demo']}}" target="_blank" class="panel-link">Demo &nearr;</a>
    % end
  </div>
</div>

<!-- At a glance -->
<div class="panel-grid">
  % if review.get("technical_score"):
  <div class="panel-score">
    <div class="score-donut" data-score="{{review['technical_score']}}" data-color="{{'ok' if review['technical_score'] >= 7 else 'warn' if review['technical_score'] >= 5 else 'err'}}">
      <span>{{review["technical_score"]}}</span>
    </div>
    <div class="muted">weighted score</div>
  </div>
  % end
  <div class="panel-facts">
    <div class="fact-row"><span class="fact-label">Code</span> <span>{{"{:,}".format(facts.get("team_code_lines", 0))}} lines</span></div>
    <div class="fact-row"><span class="fact-label">Languages</span> <span>{{", ".join(list(facts.get("languages", {}).keys())[:4]) or "—"}}</span></div>
    <div class="fact-row"><span class="fact-label">Contributors</span> <span>{{facts.get("timeline", {}).get("contributors", "—")}}</span></div>
    <div class="fact-row"><span class="fact-label">Files</span> <span>{{facts.get("team_code_files", "—")}} code files</span></div>
    % tpl_share = facts.get("files_by_status", {}).get("template", 0)
    % if tpl_share:
    <div class="fact-row"><span class="fact-label">Template</span> <span>{{tpl_share}} files unchanged</span></div>
    % end
  </div>
</div>

<!-- Flags -->
% if flags:
<div class="panel-section">
  <h3>Flags</h3>
  % for fl in flags:
  <div class="flag-card flag-card-{{fl.get('severity', 'info')}}">
    <span class="flag-sev">{{fl.get("severity", "info")}}</span>
    <div>
      <div class="flag-title">{{fl.get("title", "")}}</div>
      % if fl.get("detail"):
      <div class="flag-detail">{{fl["detail"]}}</div>
      % end
    </div>
  </div>
  % end
</div>
% end

<!-- Claims -->
% claims = review.get("claims") or []
% if claims:
<div class="panel-section">
  <h3>Claims Verification</h3>
  <table class="claims-table">
    <thead><tr><th>Claim</th><th>Status</th></tr></thead>
    <tbody>
    % for c in claims:
    <tr>
      <td>{{c.get("claim", c.get("text", ""))}}</td>
      <td>
        <%
        st = c.get("status", "")
        st_class = {"verified": "ok", "partial": "warn", "mocked": "err", "missing": "err"}.get(st, "")
        st_icon = {"verified": "&#10003;", "partial": "&#9679;", "mocked": "&#10007;", "missing": "&#10007;"}.get(st, "?")
        %>
        <span class="claim-status claim-{{st_class}}">{{!st_icon}} {{st}}</span>
      </td>
    </tr>
    % end
    </tbody>
  </table>
</div>
% end

<!-- Jury questions -->
% questions = review.get("jury_questions") or []
% if questions:
<div class="panel-section">
  <h3>Jury Questions</h3>
  <div class="jury-questions">
    % for q in questions:
    <div class="jury-q">&ldquo;{{q}}&rdquo;</div>
    % end
  </div>
</div>
% end

<!-- Architecture -->
% if review.get("architecture"):
<div class="panel-section">
  <h3>Architecture</h3>
  <p>{{review["architecture"]}}</p>
</div>
% end

% if review.get("summary"):
<div class="panel-section">
  <h3>Summary</h3>
  <p>{{review["summary"]}}</p>
</div>
% end
