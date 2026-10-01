// Wittenberg recruiting board. Data comes from data/prospects.json (rebuilt
// every day). Statuses, notes and templates are saved in this browser.

const STATUSES = [
  ["", "Not reviewed"],
  ["verified", "Good fit"],
  ["contacted", "DM'd"],
  ["talking", "Replied"],
  ["visit", "Visit / applied"],
  ["pass", "Pass"],
];
const STATUS_LABEL = Object.fromEntries(STATUSES);
const PAGE = 100;
const FOLLOW_UP_DAYS = 5;

const DEFAULT_TEMPLATES = {
  subject: "Wittenberg Hockey - {first}",
  email:
`Hi {first},

My name is {coach} and I'm with the men's hockey program at Wittenberg University in Springfield, Ohio. I've been following your season with {team} in the {league} and I like what I'm seeing.

Wittenberg is a growing program, currently ACHA D2 with plans to move to NCAA D3, and we're looking for {position}s who want to play college hockey and get a great education.

I'd love to set up a call to tell you more about the program and hear about your plans for next year. What's the best way to reach you?

Thanks,
{coach}
Wittenberg University Men's Hockey
{coach_email} {coach_phone}`,
  dm:
`Hey {first}, {coach} here with Wittenberg University hockey (Ohio). Been watching your year with {team} and would love to talk about playing college hockey for us. What's the best email or number to reach you?`,
  followup:
`Hey {first}, just following up on my message about Wittenberg hockey. We're building something special as we move toward NCAA D3 and think you'd be a great fit. Would you be open to a quick call this week?`,
};

const POS_WORD = { F: "forward", D: "defenseman", G: "goalie" };

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

let DATA = { players: [], sources: [], program: {} };
let tracking = store.get("wu-tracking", {});   // id -> {status, note, updated}
let templates = { ...DEFAULT_TEMPLATES, ...store.get("wu-templates", {}) };
let sortKey = "score", sortDir = -1, shown = PAGE;

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove("show"), 1800);
}

function track(id) { return tracking[id] || {}; }
const igHandle = (p) => (track(p.id).ig || p.socials?.instagram || "").trim().replace(/^@/, "").replace(/^https?:\/\/(www\.)?instagram\.com\//, "").replace(/\/.*$/, "");
const daysSince = (d) => d ? Math.floor((Date.now() - new Date(d + "T12:00:00").getTime()) / 86400000) : null;
function followUpDue(p) {
  const t = track(p.id);
  return t.status === "contacted" && t.dm_date && daysSince(t.dm_date) >= FOLLOW_UP_DAYS;
}
function setTrack(id, patch) {
  tracking[id] = { ...track(id), ...patch, updated: new Date().toISOString().slice(0, 10) };
  store.set("wu-tracking", tracking);
}

function statLine(p) {
  if (p.position === "G") {
    return [p.gp != null && `${p.gp} GP`, p.gaa != null && `${p.gaa.toFixed(2)} GAA`,
      p.sv_pct != null && `${p.sv_pct.toFixed(3)} SV%`].filter(Boolean).join(" · ");
  }
  return [p.gp != null && `${p.gp} GP`, p.pts != null && `${p.g ?? "-"}-${p.a ?? "-"}-${p.pts}`,
    p.ppg != null && `${p.ppg} PPG`].filter(Boolean).join(" · ");
}

// ---------------------------------------------------------------- filters
function fillSelect(sel, values) {
  const el = $(sel);
  values.forEach((v) => { const o = document.createElement("option"); o.value = v; o.textContent = v; el.appendChild(o); });
}

function setupFilters() {
  const uniq = (f) => [...new Set(DATA.players.map(f).filter(Boolean))].sort();
  STATUSES.forEach(([v, l]) => {
    const o = document.createElement("option"); o.value = v || "none"; o.textContent = l; $("#f-status").appendChild(o);
  });
  fillSelect("#f-level", uniq((p) => p.level));
  fillSelect("#f-league", uniq((p) => p.league));
  fillSelect("#f-year", uniq((p) => p.birth_year).reverse());
  ["#q", "#f-status", "#f-pos", "#f-level", "#f-league", "#f-year", "#f-new", "#f-hide-pass", "#f-follow", "#f-ig"].forEach((s) =>
    $(s).addEventListener("input", () => { shown = PAGE; renderBoard(); }));
}

function filtered() {
  const q = $("#q").value.trim().toLowerCase();
  const st = $("#f-status").value, pos = $("#f-pos").value, lvl = $("#f-level").value;
  const lg = $("#f-league").value, yr = $("#f-year").value;
  const onlyNew = $("#f-new").checked, hidePass = $("#f-hide-pass").checked;
  const onlyFollow = $("#f-follow").checked, onlyIg = $("#f-ig").checked;
  let rows = DATA.players.filter((p) => {
    const s = track(p.id).status || "";
    if (q && ![p.name, p.team, p.league, p.hometown].join(" ").toLowerCase().includes(q)) return false;
    if (st && (st === "none" ? s !== "" : s !== st)) return false;
    if (hidePass && s === "pass" && st !== "pass") return false;
    if (pos && p.position !== pos) return false;
    if (lvl && p.level !== lvl) return false;
    if (lg && p.league !== lg) return false;
    if (yr && String(p.birth_year) !== yr) return false;
    if (onlyNew && !p.new) return false;
    if (onlyFollow && !followUpDue(p)) return false;
    if (onlyIg && !igHandle(p)) return false;
    return true;
  });
  rows.sort((a, b) => {
    let x = a[sortKey], y = b[sortKey];
    if (x == null) return 1; if (y == null) return -1;
    if (typeof x === "string") return sortDir * x.localeCompare(y);
    return sortDir * (x - y);
  });
  return rows;
}

// ---------------------------------------------------------------- board
function renderBoard() {
  const rows = filtered();
  $("#count").textContent = `${rows.length} prospects`;
  $("#grid tbody").innerHTML = rows.slice(0, shown).map((p) => {
    const s = track(p.id).status || "";
    return `<tr data-id="${p.id}">
      <td class="score">${p.score}</td>
      <td><span class="name">${esc(p.name)}</span>${p.new ? '<span class="badge new">NEW</span>' : ""}
        <div class="sub">${esc(p.hometown || "")}</div>
        <div class="sub show-sm">${esc(p.position || "?")} · ${p.birth_year || "?"} · ${statLine(p)}</div></td>
      <td class="hide-sm">${esc(p.position || "?")}</td>
      <td class="hide-sm">${p.birth_year || "?"}</td>
      <td>${esc(p.team)}<div class="sub">${esc(p.league)} · ${esc(p.level)}</div></td>
      <td class="hide-sm">${statLine(p)}</td>
      <td><span class="status" data-s="${s}">${STATUS_LABEL[s]}</span>
        ${followUpDue(p) ? '<span class="badge follow">Follow up</span>' : ""}
        ${igHandle(p) ? `<div class="sub">@${esc(igHandle(p))}</div>` : ""}</td>
    </tr>`;
  }).join("");
  $("#more").hidden = rows.length <= shown;
  document.querySelectorAll("th[data-sort]").forEach((th) => th.classList.toggle("sorted", th.dataset.sort === sortKey));
}

// ---------------------------------------------------------------- outreach
const nameCase = (w) => (w && w === w.toUpperCase() ? w.toLowerCase().replace(/(^|[-' ])\w/g, (c) => c.toUpperCase()) : w);

function fill(tpl, p) {
  let [first, ...rest] = (p.first_name ? [p.first_name] : p.name.split(" "));
  first = nameCase(first);
  const vals = {
    first, last: nameCase(p.last_name || rest.join(" ")), name: nameCase(p.name), team: p.team || "your team",
    league: p.league || "", position: POS_WORD[p.position] || "player",
    coach: templates.coach || DATA.program.coach_name || "",
    coach_email: templates.coach_email || DATA.program.coach_email || "",
    coach_phone: templates.coach_phone || DATA.program.coach_phone || "",
  };
  return tpl.replace(/\{(\w+)\}/g, (m, k) => (k in vals ? vals[k] : m)).trim();
}

function igFinders(p) {
  const n = encodeURIComponent(`"${p.name}"`);
  return [
    ["Google his IG", `https://www.google.com/search?q=${n}+${encodeURIComponent(p.team || "hockey")}+site%3Ainstagram.com`],
    ["Search on Instagram", `https://www.instagram.com/explore/search/keyword/?q=${encodeURIComponent(p.name)}`],
    ["Team's IG (tagged posts)", `https://www.google.com/search?q=${encodeURIComponent(`"${p.team}" hockey instagram`)}`],
  ];
}

function links(p) {
  const n = encodeURIComponent(`"${p.name}"`);
  const nh = encodeURIComponent(`"${p.name}" hockey`);
  const out = [];
  if (p.profile_url) out.push(["Profile", p.profile_url]);
  out.push(["Elite Prospects", `https://www.eliteprospects.com/search/player?q=${encodeURIComponent(p.name)}`]);
  out.push(["X / Twitter", `https://www.google.com/search?q=${n}+hockey+site%3Ax.com+OR+site%3Atwitter.com`]);
  out.push(["Google", `https://www.google.com/search?q=${nh}+${encodeURIComponent(p.team || "")}`]);
  out.push(["Team staff", `https://www.google.com/search?q=${encodeURIComponent(`"${p.team}" ${p.league} hockey coaching staff contact`)}`]);
  if (p.socials?.x) out.unshift(["X: " + p.socials.x, `https://x.com/${p.socials.x.replace(/^@/, "")}`]);
  return out;
}

function coachSet() {
  if (templates.coach || DATA.program.coach_name) return true;
  toast("Add your name in the Templates tab first");
  return false;
}

async function copy(text, what) {
  try { await navigator.clipboard.writeText(text); toast(`${what} copied`); }
  catch { prompt(`Copy the ${what.toLowerCase()}:`, text); }
}

function openDetail(id) {
  const p = DATA.players.find((x) => x.id === id);
  if (!p) return;
  const t = track(id);
  const stats = p.position === "G"
    ? [["GP", p.gp], ["GAA", p.gaa?.toFixed(2)], ["SV%", p.sv_pct?.toFixed(3)]]
    : [["GP", p.gp], ["G", p.g], ["A", p.a], ["PTS", p.pts], ["PPG", p.ppg], ["+/-", p.plus_minus], ["PIM", p.pim]];
  const bio = [p.shoots && `Shoots ${p.shoots}`, p.height, p.weight && `${p.weight} lbs`, p.hometown, p.nationality, p.dob && `DOB ${p.dob}`].filter(Boolean);
  const emailTo = t.email || p.email || "";
  $("#detail-body").innerHTML = `<div class="detail">
    <h2>${esc(p.name)} ${p.new ? '<span class="badge new">NEW</span>' : ""}</h2>
    <div class="muted">${esc(POS_WORD[p.position] || "?")} · ${p.birth_year || "birth year ?"} · ${esc(p.team)} (${esc(p.league)}, ${esc(p.level)})</div>
    <div class="muted">${bio.map(esc).join(" · ")}</div>
    <div class="stats-grid">
      <div><b>${p.score}</b><span>Score</span></div>
      ${p.production_pct != null ? `<div><b>${Math.round(p.production_pct * 100)}%</b><span>vs league</span></div>` : ""}
      ${stats.filter(([, v]) => v != null && v !== undefined).map(([k, v]) => `<div><b>${v}</b><span>${k}</span></div>`).join("")}
    </div>
    ${p.notes?.length ? `<ul class="notes-list">${p.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    <div class="links">${links(p).map(([l, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(l)}</a>`).join("")}</div>
    <div class="ig-box">
      <h3>Instagram</h3>
      <label class="block">Handle <input id="d-ig" value="${esc(igHandle(p))}" placeholder="@handle (paste once you find him)" autocapitalize="off" autocorrect="off"></label>
      <div id="d-ig-find" class="links"${igHandle(p) ? " hidden" : ""}>${igFinders(p).map(([l, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(l)}</a>`).join("")}</div>
      <div class="row">
        <button id="d-dm-go">Copy DM &amp; open chat</button>
        <button id="d-followup" class="ghost">Copy follow-up</button>
        <button id="d-copy-dm" class="ghost">Copy DM only</button>
      </div>
      <p class="sub" id="d-dm-info">${t.dm_date ? `DM'd ${esc(t.dm_date)} (${daysSince(t.dm_date)} days ago)${t.followups ? ` · ${t.followups} follow-up${t.followups > 1 ? "s" : ""}, last ${esc(t.followup_date || "")}` : ""}` : "Not DM'd yet"}</p>
    </div>
    <label class="block">Status
      <select id="d-status">${STATUSES.map(([v, l]) => `<option value="${v}" ${v === (t.status || "") ? "selected" : ""}>${l}</option>`).join("")}</select>
    </label>
    <label class="block">Email / phone found <input id="d-email" value="${esc(emailTo)}" placeholder="player@email.com"></label>
    <label class="block">Notes <textarea id="d-note" rows="3" placeholder="Video, coach feedback, grades…">${esc(t.note || "")}</textarea></label>
    <div class="row">
      <button id="d-copy-email" class="ghost">Copy email</button>
      <button id="d-mail" class="ghost">Open in mail app</button>
    </div>
    <p class="sub">Found via ${esc(p.source)} · first seen ${esc(p.first_seen)}${t.updated ? ` · updated ${esc(t.updated)}` : ""}</p>
  </div>`;
  const save = () => { setTrack(id, { status: $("#d-status").value, note: $("#d-note").value, email: $("#d-email").value.trim(), ig: $("#d-ig").value.trim().replace(/^@/, "") }); renderBoard(); renderPipeline(); };
  const today = () => new Date().toISOString().slice(0, 10);
  $("#d-ig").addEventListener("change", () => { save(); $("#d-ig-find").hidden = !!igHandle(p); });
  $("#d-dm-go").onclick = () => {
    if (!coachSet()) return;
    const h = $("#d-ig").value.trim().replace(/^@/, "");
    // copy + open in the same tap: phones block new tabs opened after a wait
    copy(fill(templates.dm, p), "DM");
    if (!h) { toast("DM copied - find his IG first, then paste it"); $("#d-ig-find").hidden = false; return; }
    if (!t.status || t.status === "verified") $("#d-status").value = "contacted";
    save();
    if (!track(id).dm_date) setTrack(id, { dm_date: today() });
    // ig.me/m/<handle> opens the DM thread straight away in the app
    window.open(`https://ig.me/m/${encodeURIComponent(h)}`, "_blank", "noopener");
    openDetail(id);
  };
  $("#d-followup").onclick = () => {
    if (!coachSet()) return;
    copy(fill(templates.followup, p), "Follow-up");
    setTrack(id, { followups: (track(id).followups || 0) + 1, followup_date: today(), dm_date: track(id).dm_date || today() });
    const h = igHandle(p);
    if (h) window.open(`https://ig.me/m/${encodeURIComponent(h)}`, "_blank", "noopener");
    renderBoard(); openDetail(id);
  };
  $("#d-status").addEventListener("change", save);
  $("#d-note").addEventListener("change", save);
  $("#d-email").addEventListener("change", save);
  $("#d-copy-email").onclick = () => copy(`${fill(templates.subject, p)}\n\n${fill(templates.email, p)}`, "Email");
  $("#d-copy-dm").onclick = () => coachSet() && copy(fill(templates.dm, p), "DM");
  $("#d-mail").onclick = () => {
    const to = $("#d-email").value.trim();
    location.href = `mailto:${to.includes("@") && !to.startsWith("@") ? encodeURIComponent(to) : ""}?subject=${encodeURIComponent(fill(templates.subject, p))}&body=${encodeURIComponent(fill(templates.email, p))}`;
    if (!track(id).status || track(id).status === "verified") { $("#d-status").value = "contacted"; save(); }
  };
  $("#detail").showModal();
}

// ---------------------------------------------------------------- pipeline
function renderPipeline() {
  const byId = Object.fromEntries(DATA.players.map((p) => [p.id, p]));
  $("#pipeline-cols").innerHTML = STATUSES.filter(([v]) => v).map(([v, l]) => {
    const ids = Object.entries(tracking).filter(([id, t]) => t.status === v && byId[id]).map(([id]) => id);
    return `<div class="col"><h3>${l} (${ids.length})</h3>${ids.map((id) => {
      const p = byId[id];
      return `<div class="card" data-id="${id}"><b>${esc(p.name)}</b> <span class="sub">${esc(p.position || "")} ${p.birth_year || ""}</span><div class="sub">${esc(p.team)} · ${esc(p.league)}</div></div>`;
    }).join("") || '<p class="sub">Nobody yet</p>'}</div>`;
  }).join("");
}

// ---------------------------------------------------------------- sources / settings
function renderSources() {
  $("#sources-body").innerHTML = (DATA.sources || []).map((s) =>
    `<tr><td>${esc(s.name)}</td><td>${esc(s.type)}</td><td>${s.count}</td>
     <td>${s.ok ? '<span class="ok">OK</span>' : s.setup ? '<span class="muted">Needs URL</span>'
       : `<span class="bad">Failed</span> <span class="sub">${esc(s.error)}</span>`}</td></tr>`).join("")
    || '<tr><td colspan="4" class="muted">No run yet.</td></tr>';
}

function loadTemplateForm() {
  $("#t-coach").value = templates.coach || DATA.program.coach_name || "";
  $("#t-coach-email").value = templates.coach_email || DATA.program.coach_email || "";
  $("#t-coach-phone").value = templates.coach_phone || DATA.program.coach_phone || "";
  $("#t-subject").value = templates.subject;
  $("#t-email").value = templates.email;
  $("#t-dm").value = templates.dm;
  $("#t-followup").value = templates.followup;
}

function setupSettings() {
  loadTemplateForm();
  $("#t-save").onclick = () => {
    templates = { coach: $("#t-coach").value, coach_email: $("#t-coach-email").value, coach_phone: $("#t-coach-phone").value,
      subject: $("#t-subject").value, email: $("#t-email").value, dm: $("#t-dm").value,
      followup: $("#t-followup").value };
    store.set("wu-templates", templates); toast("Templates saved");
  };
  $("#t-reset").onclick = () => {
    templates = { ...DEFAULT_TEMPLATES, coach: $("#t-coach").value, coach_email: $("#t-coach-email").value, coach_phone: $("#t-coach-phone").value };
    store.set("wu-templates", templates); loadTemplateForm(); toast("Templates reset");
  };
  $("#backup").onclick = () => download("wittenberg-recruiting-backup.json",
    JSON.stringify({ tracking, templates }, null, 1), "application/json");
  $("#restore").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const b = JSON.parse(await f.text());
      tracking = { ...tracking, ...(b.tracking || {}) }; store.set("wu-tracking", tracking);
      if (b.templates) { templates = { ...templates, ...b.templates }; store.set("wu-templates", templates); loadTemplateForm(); }
      renderBoard(); renderPipeline(); toast("Backup restored");
    } catch { toast("That file isn't a backup"); }
  };
}

function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function exportCsv() {
  const cols = ["score", "name", "position", "birth_year", "team", "league", "level", "gp", "g", "a", "pts", "ppg", "gaa", "sv_pct", "hometown", "profile_url"];
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [[...cols, "status", "instagram", "dm_date", "email", "my_notes"].join(",")];
  filtered().forEach((p) => {
    const t = track(p.id);
    lines.push([...cols.map((c) => q(p[c])), q(STATUS_LABEL[t.status || ""]), q(igHandle(p) && "@" + igHandle(p)),
      q(t.dm_date), q(t.email || p.email), q(t.note)].join(","));
  });
  download(`prospects-${DATA.generated || "export"}.csv`, lines.join("\n"), "text/csv");
}

// ---------------------------------------------------------------- init
function setupEvents() {
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${b.dataset.tab}`));
    if (b.dataset.tab === "pipeline") renderPipeline();
  }));
  document.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    if (k === sortKey) sortDir *= -1; else { sortKey = k; sortDir = ["name", "team", "position"].includes(k) ? 1 : -1; }
    renderBoard();
  }));
  $("#grid tbody").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) openDetail(tr.dataset.id); });
  $("#pipeline-cols").addEventListener("click", (e) => { const c = e.target.closest(".card[data-id]"); if (c) openDetail(c.dataset.id); });
  $("#more").onclick = () => { shown += PAGE; renderBoard(); };
  $("#export").onclick = exportCsv;
}

async function init() {
  setupEvents();
  try {
    const r = await fetch(`data/prospects.json?ts=${Date.now()}`);
    DATA = await r.json();
  } catch {}
  if (DATA.generated) {
    const failed = (DATA.sources || []).filter((s) => !s.ok && !s.setup).length;
    $("#meta").textContent = `Updated ${DATA.generated} · ${DATA.season} season · ${DATA.count} prospects · ${DATA.new_count} new today` +
      (failed ? ` · ${failed} source${failed > 1 ? "s" : ""} failed` : "");
  } else {
    $("#meta").textContent = "No data yet - run the daily pull from the Actions tab.";
  }
  setupFilters();
  setupSettings();
  renderBoard();
  renderSources();
}

init();
