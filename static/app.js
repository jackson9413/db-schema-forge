/* db-schema-forge - vanilla JS UI, no framework, no build step. */

var STATE = {
  result: null,
  profile: "saas_multi_tenant",
  sevFilter: "all",
  axisFilter: "all",
  history: []
};

var AXIS_LABELS = {
  normalization: "Normalisation",
  keying: "Keying",
  typing: "Typing",
  integrity: "Integrity",
  operability: "Operability"
};

function $(id) { return document.getElementById(id); }

function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) { n.className = cls; }
  if (text !== undefined && text !== null) { n.textContent = String(text); }
  return n;
}

function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

function api(path, opts) {
  return fetch(path, opts).then(function (r) {
    if (!r.ok) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        throw new Error(j.error || ("request failed: " + r.status));
      });
    }
    return r.json();
  });
}

function download(filename, text, mime) {
  var blob = new Blob([text], { type: mime || "text/plain" });
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
}

function scoreColor(v) {
  if (v >= 85) { return "#7ee787"; }
  if (v >= 70) { return "#79c0ff"; }
  if (v >= 55) { return "#f0b429"; }
  if (v >= 40) { return "#ff9f5a"; }
  return "#f85149";
}

/* ------------------------------------------------------------------ boot */

function boot() {
  api("/api/profiles").then(function (profiles) {
    var sel = $("profile");
    clear(sel);
    profiles.forEach(function (p) {
      var o = el("option", null, p.label);
      o.value = p.id;
      if (p.id === STATE.profile) { o.selected = true; }
      sel.appendChild(o);
    });
    sel.addEventListener("change", function () {
      STATE.profile = sel.value;
    });
  });

  $("analyzeBtn").addEventListener("click", function () { runAnalysis(false); });
  $("saveBtn").addEventListener("click", function () { runAnalysis(true); });
  $("clearBtn").addEventListener("click", function () {
    $("schemaInput").value = "";
    $("status").textContent = "";
  });

  $("sampleBtn").addEventListener("click", function () { openSamples(); });
  $("sampleClose").addEventListener("click", function () {
    $("sampleModal").classList.add("hidden");
  });

  Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
    t.addEventListener("click", function () {
      Array.prototype.forEach.call(document.querySelectorAll(".tab"),
        function (x) { x.classList.remove("active"); });
      Array.prototype.forEach.call(document.querySelectorAll(".tab-pane"),
        function (x) { x.classList.remove("active"); });
      t.classList.add("active");
      $("pane-" + t.getAttribute("data-tab")).classList.add("active");
    });
  });

  Array.prototype.forEach.call(document.querySelectorAll("#sevFilters .chip"),
    function (c) {
      c.addEventListener("click", function () {
        Array.prototype.forEach.call(
          document.querySelectorAll("#sevFilters .chip"),
          function (x) { x.classList.remove("active"); });
        c.classList.add("active");
        STATE.sevFilter = c.getAttribute("data-sev");
        renderFindings();
      });
    });

  $("axisFilter").addEventListener("change", function () {
    STATE.axisFilter = $("axisFilter").value;
    renderFindings();
  });

  $("copyDdl").addEventListener("click", function () {
    var t = $("ddlOut").textContent;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(t);
      $("copyDdl").textContent = "Copied";
      setTimeout(function () { $("copyDdl").textContent = "Copy"; }, 1400);
    }
  });
  $("downloadDdl").addEventListener("click", function () {
    download("improved-schema.sql", $("ddlOut").textContent, "text/plain");
  });
  $("downloadMd").addEventListener("click", function () {
    download("schema-review.md", buildMarkdown(), "text/markdown");
  });
  $("downloadJson").addEventListener("click", function () {
    download("schema-review.json", JSON.stringify(STATE.result, null, 2),
      "application/json");
  });

  loadHistory();
}

/* -------------------------------------------------------------- analysis */

function runAnalysis(save) {
  var text = $("schemaInput").value.trim();
  if (!text) {
    $("status").textContent = "Paste a schema first, or load a sample.";
    return;
  }
  $("status").textContent = "Reviewing...";
  $("analyzeBtn").disabled = true;
  api("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ schema: text, profile: STATE.profile, save: !!save })
  }).then(function (result) {
    STATE.result = result;
    $("status").textContent = "";
    render(result);
    if (save) {
      $("status").textContent = "Saved to the run history (session #" +
        result.session_id + ").";
      loadHistory();
    }
  }).catch(function (err) {
    $("status").textContent = "Could not review: " + err.message;
  }).then(function () {
    $("analyzeBtn").disabled = false;
  });
}

function render(result) {
  $("emptyState").classList.add("hidden");
  $("results").classList.remove("hidden");

  $("scoreValue").textContent = result.score;
  $("scoreValue").style.color = scoreColor(result.score);
  var vp = $("verdictPill");
  vp.textContent = result.verdict.label;
  vp.className = "pill big band-" + result.verdict.band;
  $("verdictBlurb").textContent = result.verdict.blurb;

  var st = result.stats;
  $("statsLine").textContent = st.tables + " tables / " + st.columns +
    " columns / " + st.foreign_keys + " foreign keys / " + st.indexes +
    " indexes - " + st.high + " high, " + st.medium + " medium, " + st.low +
    " low findings";

  renderAxes(result);
  renderCaps(result);
  renderStrengths(result);
  renderFindings();
  renderPlan(result);
  renderNorm(result);
  renderMap(result);
  renderDdl(result);
}

function renderAxes(result) {
  var box = $("axisPills");
  clear(box);
  Object.keys(AXIS_LABELS).forEach(function (axis) {
    var v = result.axes[axis];
    var d = el("div", "axis");
    d.appendChild(el("div", "axis-name", AXIS_LABELS[axis]));
    var val = el("div", "axis-val", v);
    val.style.color = scoreColor(v);
    d.appendChild(val);
    var bar = el("div", "bar");
    var fill = document.createElement("i");
    fill.style.width = Math.max(2, v) + "%";
    fill.style.background = scoreColor(v);
    bar.appendChild(fill);
    d.appendChild(bar);
    d.title = result.axis_reasons[axis].join("\n") ||
      "No findings on this axis.";
    box.appendChild(d);
  });
}

function renderCaps(result) {
  var box = $("capBox");
  clear(box);
  (result.caps || []).forEach(function (c) {
    var n = el("div", "note");
    n.appendChild(el("div", "note-title", "Structural cap - " + c.scope +
      " capped at " + c.cap));
    n.appendChild(el("div", null, c.reason));
    box.appendChild(n);
  });
}

function renderStrengths(result) {
  var box = $("strengthBox");
  clear(box);
  if (!result.strengths || !result.strengths.length) { return; }
  var n = el("div", "note good");
  n.appendChild(el("div", "note-title", "What is already right"));
  var ul = document.createElement("ul");
  result.strengths.forEach(function (s) {
    ul.appendChild(el("li", null, s));
  });
  n.appendChild(ul);
  box.appendChild(n);
}

function renderFindings() {
  var box = $("findingsList");
  clear(box);
  if (!STATE.result) { return; }
  var list = STATE.result.findings.filter(function (f) {
    if (STATE.sevFilter !== "all" && f.severity !== STATE.sevFilter) {
      return false;
    }
    if (STATE.axisFilter !== "all" && f.axis !== STATE.axisFilter) {
      return false;
    }
    return true;
  });
  if (!list.length) {
    box.appendChild(el("p", "muted", "No findings match this filter."));
    return;
  }
  list.forEach(function (f) {
    var card = el("div", "finding sev-" + f.severity);
    var head = el("div", "f-head");
    head.appendChild(el("span", "f-title", f.title));
    head.appendChild(el("span", "f-tag", f.severity));
    head.appendChild(el("span", "f-tag", AXIS_LABELS[f.axis] || f.axis));
    card.appendChild(head);
    card.appendChild(el("div", "f-evidence", f.evidence));
    card.appendChild(el("div", "f-why", f.why));
    var fix = el("div", "f-fix");
    fix.appendChild(el("b", null, "Fix: "));
    fix.appendChild(document.createTextNode(f.fix));
    card.appendChild(fix);
    box.appendChild(card);
  });
}

function renderPlan(result) {
  var box = $("planList");
  clear(box);
  if (!result.fix_plan.length) {
    box.appendChild(el("p", "muted", "Nothing to fix - this schema is clean."));
    return;
  }
  result.fix_plan.forEach(function (p) {
    var row = el("div", "plan-item");
    row.appendChild(el("div", "plan-rank", p.rank + "."));
    var body = el("div", "plan-body");
    var title = el("div", "plan-title", p.title);
    if (p.auto) {
      title.appendChild(el("span", "auto-badge", "auto-fixable"));
    }
    body.appendChild(title);
    body.appendChild(el("div", "plan-fix", p.evidence + " - " + p.fix));
    row.appendChild(body);
    row.appendChild(el("div", "gain", "+" + p.gain));
    box.appendChild(row);
  });
}

function renderNorm(result) {
  var box = $("normList");
  clear(box);
  if (!result.normalisation_plan.length) {
    box.appendChild(el("p", "muted",
      "No normalisation problems found - the tables are already in a " +
      "sensible shape."));
    return;
  }
  result.normalisation_plan.forEach(function (s) {
    var card = el("div", "finding sev-" + s.severity);
    card.appendChild(el("div", "f-title", s.title));
    card.appendChild(el("div", "f-evidence", s.evidence));
    card.appendChild(el("div", "f-why", s.detail));
    box.appendChild(card);
  });
}

function renderMap(result) {
  var box = $("mapList");
  clear(box);
  var map = result.schema_map;
  map.tables.forEach(function (t) {
    var card = el("div", "map-table");
    card.appendChild(el("h4", null, t.name));
    var cols = el("div", "map-cols");
    t.columns.forEach(function (c) {
      var cls = "col-chip";
      if (c.pk) { cls += " pk"; }
      if (c.fk) { cls += " fk"; }
      if (c.unique) { cls += " uq"; }
      if (c.nullable && !c.pk) { cls += " nu"; }
      var chip = el("span", cls, c.name + " " + (c.type || ""));
      chip.title = (c.nullable ? "nullable" : "not null") +
        (c.fk ? " -> " + c.fk : "") + (c.unique ? " unique" : "") +
        (c.default ? " default " + c.default : "");
      cols.appendChild(chip);
    });
    card.appendChild(cols);
    if (t.fks.length) {
      var lines = t.fks.map(function (f) {
        return f.column + " -> " + f.ref_table +
          (f.on_delete ? " (on delete " + f.on_delete + ")" : "");
      });
      card.appendChild(el("div", "map-fks", lines.join("   |   ")));
    }
    box.appendChild(card);
  });
  var broken = map.edges.filter(function (e) { return !e.resolves; });
  if (broken.length) {
    var n = el("div", "note");
    n.appendChild(el("div", "note-title", "Unresolved relationships"));
    n.appendChild(el("div", null, broken.map(function (e) {
      return e.from + "." + e.column + " -> " + e.to +
        " (that table is not in this schema)";
    }).join("; ")));
    box.appendChild(n);
  }
}

function renderDdl(result) {
  $("ddlOut").textContent = result.scaffold.ddl || "";
  var box = $("appliedList");
  clear(box);
  var applied = result.scaffold.applied || [];
  if (!applied.length) { return; }
  box.appendChild(el("div", "note-title",
    "Safe fixes already applied to the scaffold (" + applied.length + ")"));
  var ul = document.createElement("ul");
  applied.forEach(function (a) { ul.appendChild(el("li", null, a)); });
  box.appendChild(ul);
}

function buildMarkdown() {
  var r = STATE.result;
  if (!r) { return ""; }
  var fence = String.fromCharCode(96).repeat(3);
  var lines = [];
  lines.push("# db-schema-forge report");
  lines.push("");
  lines.push("- **Profile:** " + r.profile.label);
  lines.push("- **Score:** " + r.score + " / 100 - **" + r.verdict.label + "**");
  lines.push("- **Tables:** " + r.stats.tables + " | **Findings:** " +
    r.stats.findings);
  lines.push("");
  lines.push("## Scorecard");
  lines.push("");
  Object.keys(AXIS_LABELS).forEach(function (a) {
    lines.push("- **" + AXIS_LABELS[a] + ": " + r.axes[a] + "/100**");
  });
  lines.push("");
  lines.push("## Fix plan");
  lines.push("");
  r.fix_plan.forEach(function (p) {
    lines.push(p.rank + ". **" + p.title + "** (" + p.evidence + ") - " +
      p.fix);
  });
  lines.push("");
  lines.push("## Improved DDL");
  lines.push("");
  lines.push(fence + "sql");
  lines.push(r.scaffold.ddl);
  lines.push(fence);
  return lines.join("\n");
}

/* --------------------------------------------------------------- samples */

function openSamples() {
  api("/api/samples").then(function (list) {
    var box = $("sampleList");
    clear(box);
    list.forEach(function (s) {
      var card = el("div", "sample");
      card.appendChild(el("h4", null, s.name));
      card.appendChild(el("p", null, s.note));
      card.addEventListener("click", function () {
        api("/api/sample?id=" + encodeURIComponent(s.id)).then(function (full) {
          $("schemaInput").value = full.ddl;
          $("profile").value = full.profile;
          STATE.profile = full.profile;
          $("sampleModal").classList.add("hidden");
          $("status").textContent = "Sample loaded: " + full.name;
          runAnalysis(false);
        });
      });
      box.appendChild(card);
    });
    $("sampleModal").classList.remove("hidden");
  });
}

/* --------------------------------------------------------------- history */

function loadHistory() {
  api("/api/sessions?limit=40").then(function (rows) {
    STATE.history = rows;
    renderHistory();
  });
}

function renderHistory() {
  var box = $("historyList");
  clear(box);
  $("historyCount").textContent = STATE.history.length + " saved";
  if (!STATE.history.length) {
    box.appendChild(el("p", "muted small",
      "Nothing saved yet. Use Review and save to keep a review."));
    return;
  }
  STATE.history.forEach(function (s) {
    var card = el("div", "hist");
    var top = el("div", "hist-top");
    var sc = el("span", "hist-score", s.score);
    sc.style.color = scoreColor(s.score);
    top.appendChild(sc);
    top.appendChild(el("span", "muted small", s.verdict.replace(/_/g, " ")));
    card.appendChild(top);
    card.appendChild(el("div", "hist-meta",
      (s.label || s.profile) + " - " + String(s.created_at).replace("T", " ")));

    var stars = el("div", "stars");
    for (var i = 1; i <= 5; i++) {
      var sp = el("span", i <= (s.self_rating || 0) ? "" : "off", "*");
      sp.setAttribute("data-v", i);
      sp.addEventListener("click", function (e) {
        e.stopPropagation();
        var v = parseInt(sp.getAttribute("data-v"), 10);
        api("/api/sessions/" + s.id, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ self_rating: v })
        }).then(loadHistory);
      });
      stars.appendChild(sp);
    }
    card.appendChild(stars);

    var actions = el("div", "hist-actions");
    var note = document.createElement("input");
    note.type = "text";
    note.placeholder = "notes";
    note.value = s.notes || "";
    note.addEventListener("change", function () {
      api("/api/sessions/" + s.id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes: note.value })
      });
    });
    actions.appendChild(note);

    var open = el("button", "icon-btn", "open");
    open.addEventListener("click", function () {
      api("/api/sessions/" + s.id).then(function (full) {
        $("schemaInput").value = full.schema_text;
        $("profile").value = full.profile;
        STATE.profile = full.profile;
        if (full.result) {
          STATE.result = full.result;
          render(full.result);
        }
        $("status").textContent = "Reopened session #" + full.id;
      });
    });
    actions.appendChild(open);

    var del = el("button", "icon-btn", "delete");
    del.addEventListener("click", function () {
      api("/api/sessions/" + s.id, { method: "DELETE" }).then(loadHistory);
    });
    actions.appendChild(del);
    card.appendChild(actions);
    box.appendChild(card);
  });
}

document.addEventListener("DOMContentLoaded", boot);
