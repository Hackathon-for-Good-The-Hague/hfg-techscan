<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TechScan — {{cfg.get("event", {}).get("name", "Dashboard")}}</title>
<script src="https://unpkg.com/htmx.org@2.0.4" integrity="sha384-HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+" crossorigin="anonymous"></script>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<nav>
  <div class="nav-brand">TechScan</div>
  <div class="nav-links">
    <a href="/" class="nav-link">Dashboard</a>
    <a href="/config" class="nav-link">Config</a>
    <a href="/scan" class="nav-link">Scan</a>
  </div>
  <div class="nav-event">{{cfg.get("event", {}).get("name", "")}}</div>
</nav>
<main>
{{!base}}
</main>
</body>
</html>
