/* Berlin charging site planner – dashboard */
const $ = (s) => document.querySelector(s);
const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? "–" : Number(n).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = (n, d = 0) => (n == null ? "–" : fmt(n * 100, d) + " %");
const eurk = (n) => (n == null ? "–" : (n < 0 ? "−" : "") + "€" + fmt(Math.abs(n) / 1000) + "k");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const C = { navy: "#1e3a5f", teal: "#0f8b8d", violet: "#6c4bc4", red: "#c8443a", green: "#2e8b57", ac: "#3a86c8", muted: "#9aa5b8" };
const get = (f) => fetch("data/" + f).then((r) => r.json());

Promise.all(["summary.json", "areas.geojson", "points.json", "shortlist.json", "operators.json", "districts.json", "history.json"].map(get))
  .then(([S, A, P, SL, OPS, D, H]) => {
    fillText(S);
    kpis(S);
    findings(S, SL);
    const m = map(A, P, SL);
    shortlist(SL, S, m);
    competition(OPS, D, S);
    forecastChart(S);
    business(S, SL);
    validation(S);
    quality(S);
  });

function fillText(S) {
  const v = { points: fmt(S.points), register_date: new Date(S.register_date).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }), residents: fmt(S.residents) };
  document.querySelectorAll("[data-k]").forEach((e) => (e.textContent = v[e.dataset.k] ?? ""));
}

function kpis(S) {
  const b = S.bev_stock[Math.max(...Object.keys(S.bev_stock))];
  const items = [
    [fmt(S.points), "public charge points"],
    [fmt(S.points_by_class.HPC), "HPC points ≥ 150 kW"],
    [fmt(S.operators), "operators"],
    [fmt(b), "BEVs registered (1 Jan 2026)"],
    [pct(S.coverage.ac_walk), "residents ≤ 300 m from AC"],
    [S.allego ? pct(S.allego.share_hpc, 1) : "–", "Allego share of HPC points"],
  ];
  $("#kpis").innerHTML = items.map(([a, b]) => `<div class="kpi"><b>${a}</b><span>${b}</span></div>`).join("");
}

function findings(S, SL) {
  const top = SL.hpc.slice(0, 3).map((r) => esc(r.name || r.poi_label) + " (" + esc(r.district) + ")").join(", ");
  const f = S.forecast, base = f.scenarios.base;
  const bc = S.business;
  $("#findings").innerHTML = `
    <div class="card find"><h3>Where</h3><div class="big">${SL.hpc.length} HPC + ${SL.dc.length} DC sites</div>
      <p>Fast-charging gaps are largest in <b>${esc(S_topDistricts(S))}</b>. Top HPC hosts: ${top}.</p></div>
    <div class="card find"><h3>How much</h3><div class="big">${fmt(base.points_per_year_needed)} points a year</div>
      <p>Base scenario: about ${fmt(base.bev[2030])} BEVs in Berlin by 2030 need ~${fmt(base.points[2030])} public points,
      against ${fmt(f.recent_build_pace)} added per year in 2024–25 (still in operation).</p></div>
    <div class="card find"><h3>Is it worth it</h3><div class="big">${bc.sites_positive} of ${bc.sites} sites NPV &gt; 0</div>
      <p>A typical site needs ${fmt(bc.scenarios.Base.breakeven_mult * 100)} % of base-case utilisation to earn the 8 % hurdle;
      location quality decides the investment.</p></div>`;
}
let _topD = "";
function S_topDistricts() { return _topD; }

function colorScale(v, lo, hi, diverging) {
  if (v == null || Number.isNaN(v)) return "#eee";
  if (diverging) {
    const t = Math.max(-1, Math.min(1, v / (v < 0 ? -lo : hi)));
    return t >= 0 ? mix("#fbefe9", "#b3261e", t) : mix("#eef6f6", "#1b7f7c", -t);
  }
  const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  return mix("#eef3f9", "#1e3a5f", t);
}
function mix(a, b, t) {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [p(a), p(b)];
  return "rgb(" + x.map((c, i) => Math.round(c + (y[i] - c) * t)).join(",") + ")";
}
function quant(arr, q) { const s = arr.filter((x) => x != null && !Number.isNaN(x)).sort((a, b) => a - b); return s[Math.floor(q * (s.length - 1))]; }

function map(A, P, SL) {
  const m = L.map("map", { preferCanvas: true, scrollWheelZoom: false }).setView([52.515, 13.40], 11);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap contributors" }).addTo(m);
  let areaLayer;
  const draw = (key) => {
    if (areaLayer) m.removeLayer(areaLayer);
    const vals = A.features.map((f) => f.properties[key]);
    const div = key.startsWith("gap");
    const lo = div ? quant(vals, 0.05) : quant(vals, 0.02), hi = div ? quant(vals, 0.95) : quant(vals, 0.95);
    areaLayer = L.geoJSON(A, {
      style: (f) => ({ fillColor: colorScale(f.properties[key], lo, hi, div), fillOpacity: 0.62, color: "#fff", weight: 0.6 }),
      onEachFeature: (f, l) => {
        const p = f.properties;
        l.bindTooltip(`<b>${esc(p.plr_name)}</b> · ${esc(p.district)}<br>${fmt(p.residents)} residents · ${fmt(p.points)} points (${fmt(p.hpc_points)} HPC)<br>
          Fast-charging gap ${fmt(p.gap_dc_kwh)} kWh/day · AC gap ${fmt(p.gap_ac_kwh)} kWh/day`, { sticky: true });
      },
    }).addTo(m);
    areaLayer.bringToBack();
    $("#legend").innerHTML = div
      ? `<span><i style="background:${mix("#eef6f6", "#1b7f7c", 1)}"></i>better served than average</span><span><i style="background:#f7f7f7"></i>average</span>
         <span><i style="background:${mix("#fbefe9", "#b3261e", 1)}"></i>under-served</span>${ptsLegend}`
      : `<span><i style="background:#eef3f9"></i>low</span><span><i style="background:#1e3a5f"></i>high</span>${ptsLegend}`;
  };
  const ptsLegend = `<span><i class="dot" style="background:${C.ac}"></i>AC</span><span><i class="dot" style="background:${C.teal}"></i>DC 50–149 kW</span>
    <span><i class="dot" style="background:${C.violet}"></i>HPC ≥ 150 kW</span><span><i class="dot" style="background:${C.violet};outline:2px solid #fff"></i>numbered = shortlist</span>`;
  draw("gap_dc_km2");
  $("#layerSel").onchange = (e) => draw(e.target.value);

  const ptsLayer = L.layerGroup();
  const col = { AC: C.ac, DC: C.teal, HPC: C.violet };
  P.rows.forEach(([lat, lon, kw, cls, op]) => {
    L.circleMarker([lat, lon], { radius: cls === "AC" ? 1.6 : 3.2, color: col[cls], weight: 0, fillOpacity: cls === "AC" ? 0.35 : 0.85 })
      .bindTooltip(`${esc(op)} · ${fmt(kw)} kW ${cls}`).addTo(ptsLayer);
  });
  ptsLayer.addTo(m);
  const mk = (rows, cls) => {
    const g = L.layerGroup();
    rows.forEach((r) => {
      const icon = L.divIcon({ className: "", html: `<div class="num-marker ${cls}">${r.rank}</div>`, iconSize: [24, 24], iconAnchor: [12, 12] });
      L.marker([r.lat, r.lon], { icon }).bindPopup(popup(r)).addTo(g);
    });
    return g;
  };
  const hpc = mk(SL.hpc, "").addTo(m), dc = mk(SL.dc, "dc");
  const tog = (id, layer) => ($(id).onchange = (e) => (e.target.checked ? layer.addTo(m) : m.removeLayer(layer)));
  tog("#showPts", ptsLayer); tog("#showHpc", hpc); tog("#showDc", dc);
  return m;
}

function popup(r) {
  return `<b>#${r.rank} ${esc(r.name || r.poi_label)}</b><br>${esc(r.poi_label)} · ${esc([r.street, r.house_no].filter(Boolean).join(" "))} ${esc(r.district)}<br>
    Score <b>${fmt(r.score, 1)}</b> · ${fmt(r.traffic_dtv)} cars/weekday · nearest HPC ${fmt(r.nearest_hpc_m / 1000, 1)} km<br>
    ${r.npv_base != null ? `Base-case NPV <b>${eurk(r.npv_base)}</b><br>` : ""}<i>${esc(r.reason)}</i><br>
    <a target="_blank" rel="noopener" href="https://www.openstreetmap.org/${{ n: "node", w: "way", r: "relation" }[r.osm_id[0]]}/${r.osm_id.slice(1)}">OpenStreetMap</a>`;
}

function shortlist(SL, S, m) {
  const render = (t) => {
    const rows = SL[t];
    $("#shortNote").textContent = t === "hpc"
      ? "High-traffic hosts with short stops (drive-ins, shopping centres, independent fuel stations, DIY stores). Priority uses the competitor-entry model: where operators are likely to build next."
      : "Supermarkets suit 50–150 kW: a 20–40 minute shop adds 50–150 km of range. Chosen where the fast-charging gap is largest.";
    const head = `<tr><th>#</th><th>Host</th><th>Type</th><th>Address</th><th>District</th><th class="n">Score</th><th>Score parts</th>
      <th class="n">Cars/weekday</th><th class="n">Nearest HPC</th>${t === "hpc" ? '<th class="n">NPV (Base)</th>' : ""}<th>Priority</th><th>Why</th></tr>`;
    const body = rows.map((r) => {
      const w = [["s_gap", 0.35, C.red], ["s_traffic", 0.25, C.navy], ["s_whitespace", 0.25, C.violet], ["s_poi", 0.15, C.teal]];
      const bars = `<div class="bars" title="gap · traffic · whitespace · host">${w.map(([k, wt, c]) => `<span style="width:${r[k] * wt * 100}%;background:${c}"></span>`).join("")}</div>`;
      const pr = r.priority.startsWith("Secure") ? '<span class="pill hot">Secure first</span>' : '<span class="pill calm">Lower pressure</span>';
      return `<tr data-lat="${r.lat}" data-lon="${r.lon}"><td>${r.rank}</td><td><b>${esc(r.name || "–")}</b></td><td>${esc(r.poi_label)}</td>
        <td>${esc([r.street, r.house_no].filter(Boolean).join(" ") || "–")}</td><td>${esc(r.district)}</td><td class="n"><b>${fmt(r.score, 1)}</b></td><td>${bars}</td>
        <td class="n">${fmt(r.traffic_dtv)}</td><td class="n">${fmt(r.nearest_hpc_m / 1000, 1)} km</td>
        ${t === "hpc" ? `<td class="n ${r.npv_base > 0 ? "pos" : "neg"}">${eurk(r.npv_base)}</td>` : ""}<td>${pr}</td><td class="small">${esc(r.reason)}</td></tr>`;
    }).join("");
    $("#shortTbl").innerHTML = head + body;
    $("#shortTbl").querySelectorAll("tr[data-lat]").forEach((tr) => (tr.onclick = () => {
      m.setView([+tr.dataset.lat, +tr.dataset.lon], 15);
      $("#map").scrollIntoView({ behavior: "smooth", block: "center" });
    }));
  };
  document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === b));
    render(b.dataset.t);
  }));
  render("hpc");
}

function competition(OPS, D, S) {
  Chart.defaults.font.family = "system-ui, -apple-system, Segoe UI, Roboto, sans-serif";
  Chart.defaults.color = "#5b6678";
  const top = OPS.slice(0, 12);
  const hl = (o) => (o === "Allego" ? C.violet : C.navy);
  new Chart($("#opChart"), {
    type: "bar",
    data: { labels: top.map((o) => o.operator), datasets: [{ label: "Installed power (MW)", data: top.map((o) => o.kw / 1000), backgroundColor: top.map((o) => hl(o.operator)) }] },
    options: { indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${fmt(c.raw, 1)} MW · ${fmt(top[c.dataIndex].points)} points · ${fmt(top[c.dataIndex].sites)} sites` } } },
      scales: { x: { title: { display: true, text: "MW" } } } },
  });
  const h = [...OPS].sort((a, b) => b.hpc - a.hpc).slice(0, 10);
  const rest = 1 - h.reduce((s, o) => s + o.share_hpc, 0);
  new Chart($("#hpcChart"), {
    type: "bar",
    data: { labels: [...h.map((o) => o.operator), "Others"], datasets: [{ data: [...h.map((o) => o.share_hpc * 100), rest * 100], backgroundColor: [...h.map((o) => hl(o.operator)), "#c4cbd8"] }] },
    options: { indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => fmt(c.raw, 1) + " % of HPC points" } } }, scales: { x: { title: { display: true, text: "% of HPC points" } } } },
  });
  $("#hhiNote").textContent = `Concentration (HHI): ${fmt(S.hhi.hhi_hpc)} for HPC points, ${fmt(S.hhi.hhi_kw)} for installed power. Below 1,500 is unconcentrated: no operator dominates Berlin, so good host sites are the scarce resource.`;
  _topD = D.slice(0, 3).map((d) => d.district).join(", ");
  const head = `<tr><th>District</th><th class="n">Residents</th><th class="n">BEVs (est.)</th><th class="n">Points</th><th class="n">HPC</th>
    <th class="n">BEVs per point</th><th class="n">≤ 300 m AC</th><th class="n">≤ 1.5 km HPC</th><th class="n">Fast gap kWh/day</th><th class="n">AC gap kWh/day</th></tr>`;
  const body = D.map((d) => `<tr><td><b>${esc(d.district)}</b></td><td class="n">${fmt(d.residents)}</td><td class="n">${fmt(d.bev_est)}</td><td class="n">${fmt(d.points)}</td>
    <td class="n">${fmt(d.hpc)}</td><td class="n">${fmt(d.bev_per_point, 1)}</td><td class="n">${pct(d.cov_ac_walk)}</td><td class="n">${pct(d.cov_hpc_reach)}</td>
    <td class="n ${d.gap_dc > 0 ? "neg" : "pos"}">${fmt(d.gap_dc)}</td><td class="n ${d.gap_ac > 0 ? "neg" : "pos"}">${fmt(d.gap_ac)}</td></tr>`).join("");
  $("#distTbl").innerHTML = head + body;
  // findings card depends on the district order
  const f = document.querySelector("#findings .card p");
  if (f) f.innerHTML = f.innerHTML.replace(/<b><\/b>/, `<b>${esc(_topD)}</b>`);
}

function forecastChart(S) {
  const f = S.forecast, years = Object.keys(f.scenarios.base.bev).map(Number);
  const sc = { slow: "#9aa5b8", base: C.navy, fast: C.teal };
  const ds = [];
  for (const [n, s] of Object.entries(f.scenarios)) {
    ds.push({ label: `BEVs – ${n}`, data: years.map((y) => s.bev[y] / 1000), borderColor: sc[n], backgroundColor: sc[n], borderWidth: n === "base" ? 2.5 : 1.5, pointRadius: 0, yAxisID: "y" });
    ds.push({ label: `Points – ${n}`, data: years.map((y) => s.points[y] / 1000), borderColor: sc[n], borderDash: [5, 4], borderWidth: n === "base" ? 2 : 1.2, pointRadius: 0, yAxisID: "y2" });
  }
  ds.push({ label: "BEVs observed", type: "scatter", data: years.map((y) => f.observed.bev[y] != null ? f.observed.bev[y] / 1000 : null), backgroundColor: C.red, pointRadius: 5, yAxisID: "y" });
  ds.push({ label: "Points observed", type: "scatter", data: years.map((y) => f.observed.points[y] != null ? f.observed.points[y] / 1000 : null), backgroundColor: C.violet, pointStyle: "rectRot", pointRadius: 6, yAxisID: "y2" });
  new Chart($("#fcChart"), {
    type: "line", data: { labels: years, datasets: ds },
    options: { interaction: { mode: "index", intersect: false }, plugins: { legend: { labels: { boxWidth: 12, font: { size: 11 }, filter: (i) => !i.text.includes("slow") || i.text.startsWith("BEVs") } } },
      scales: { y: { title: { display: true, text: "BEVs (thousand, solid)" }, beginAtZero: true }, y2: { position: "right", title: { display: true, text: "public points (thousand, dashed)" }, beginAtZero: true, grid: { drawOnChartArea: false } } } },
  });
  $("#fcCards").innerHTML = Object.entries(f.scenarios).map(([n, s]) => `<div class="card"><h3>${n[0].toUpperCase() + n.slice(1)} scenario</h3>
    <div class="big">${fmt(s.bev[2030])} BEVs</div><p>by 2030 → ${fmt(s.points[2030])} public points needed, <b>${fmt(s.points_per_year_needed)} a year</b>
    from now (recent pace ${fmt(f.recent_build_pace)}). Market ceiling ${fmt(s.params.k)} BEVs; availability elasticity ${s.params.elasticity}; fit error ${pct(s.fit_rmse_log, 1)}.</p></div>`).join("");
}

function business(S, SL) {
  const b = S.business;
  $("#bcCards").innerHTML = ["Pessimistic", "Base", "Optimistic"].map((n) => {
    const s = b.scenarios[n];
    return `<div class="card"><h3>${n}</h3><div class="big ${s.npv > 0 ? "pos" : "neg"}">NPV ${eurk(s.npv)}</div>
      <p>IRR ${s.irr == null ? "below −99 %" : pct(s.irr, 1)} · payback ${s.payback == null ? "&gt; 10 years" : s.payback + " years"} · capex ${eurk(b.capex)}.<br>
      Break-even at ${fmt(s.breakeven_mult * 100)} % of this scenario's utilisation.</p></div>`;
  }).join("");
  const t = b.tornado.slice(0, 10);
  new Chart($("#tornado"), {
    type: "bar",
    data: { labels: t.map((r) => r[0]), datasets: [
      { label: "pessimistic value", data: t.map((r) => r[1] / 1000), backgroundColor: C.red },
      { label: "optimistic value", data: t.map((r) => r[2] / 1000), backgroundColor: C.teal }] },
    options: { indexAxis: "y", plugins: { legend: { position: "bottom" } }, scales: { x: { stacked: true, title: { display: true, text: "change in NPV (€k)" } }, y: { stacked: true, ticks: { font: { size: 11 } } } } },
  });
  const g = b.grid;
  $("#gridTbl").innerHTML = `<tr><th>Avg utilisation \\ price</th>${g.cols.map((c) => `<th>${c}</th>`).join("")}</tr>` +
    g.rows.map((r, i) => `<tr><th>${r}</th>${g.values[i].map((v) => `<td class="${v > 0 ? "pos" : "neg"}">${fmt(v / 1000)}</td>`).join("")}</tr>`).join("");
  const best = [...SL.hpc].sort((a, b) => b.npv_base - a.npv_base).slice(0, 3);
  $("#siteNpvNote").innerHTML = `Per site, utilisation scales with the location score (0.8× to 1.2× of Base). ${b.sites_positive} of ${b.sites} shortlisted sites clear the 8 % hurdle;
    best: ${best.map((r) => `#${r.rank} ${esc(r.name || r.poi_label)} (${eurk(r.npv_base)})`).join(", ")}.`;
}

function validation(S) {
  const bt = S.backtest, em = S.entry_model;
  $("#btCards").innerHTML = `
    <div class="card"><h3>Backtest: did the opportunity score anticipate 2025–26 openings?</h3>
      <p>Scored every candidate with the network as it stood on 1 Jan 2025, then checked which got a new HPC point within 300 m
      (${fmt(bt.hpc_points_after)} HPC points opened since).</p>
      <table><tr><th>Signal</th><th class="n">AUC</th></tr>
      ${Object.entries(bt.auc).map(([k, v]) => `<tr><td>${k.replace(/_/g, " ")}</td><td class="n">${fmt(v, 2)}</td></tr>`).join("")}</table>
      <p class="small"><b>Reading:</b> AUC 0.5 = no better than chance. Operators' recent HPC openings followed <b>traffic</b> and <b>host type</b>
      (fuel stations, drive-ins, retail), not residential gaps (AUC ${fmt(bt.auc.gap, 2)}). The opportunity score deliberately targets
      under-served demand, so it is <i>not</i> a forecast of competitor behaviour; that is what the model on the right is for.</p></div>
    <div class="card"><h3>Competitor-entry model (logistic regression)</h3>
      <p>Trained on 2023–24 openings with the 2023 network, tested out-of-time on 2025–26.</p>
      <table><tr><th></th><th class="n">Train</th><th class="n">Test (out of time)</th></tr>
        <tr><td>AUC</td><td class="n">${fmt(em.train_auc, 2)}</td><td class="n"><b>${fmt(em.test_auc, 2)}</b></td></tr>
        <tr><td>Sites with a new HPC nearby</td><td class="n">${pct(em.train_positive_rate, 1)}</td><td class="n">${pct(em.test_positive_rate, 1)}</td></tr>
        <tr><td>Hit rate, top 20 % of predictions</td><td></td><td class="n">${pct(em.test_hit_rate_top20, 1)}</td></tr>
        <tr><td>Hit rate, other 80 %</td><td></td><td class="n">${pct(em.test_hit_rate_rest, 1)}</td></tr></table>
      <p class="small">Modest but real signal: the top fifth of sites is ${fmt(em.test_hit_rate_top20 / em.test_hit_rate_rest, 1)}× as likely to get a competitor HPC.
      Strongest drivers: traffic volume (+), fuel and drive-in hosts (+), hotels (−). Used to flag shortlisted sites to <b>secure first</b>.</p></div>`;
}

function quality(S) {
  const head = `<tr><th>Check</th><th>What</th><th>Severity</th><th class="n">Devices</th><th>Decision</th><th>Example</th></tr>`;
  const body = S.quality.map((q) => `<tr><td>${q.check_id}</td><td>${esc(q.title)}</td><td class="sev-${q.severity}">${q.severity}</td>
    <td class="n">${fmt(q.count)}</td><td>${esc(q.action)}</td><td class="small">${q.examples[0] ? esc(Object.values(q.examples[0]).filter(Boolean).slice(0, 4).join(" · ")) : "–"}</td></tr>`).join("");
  $("#dqTbl").innerHTML = head + body + `<tr><td colspan="6" class="small">${fmt(S.devices_raw)} Berlin devices in the register → ${fmt(S.devices_used)} used → ${fmt(S.sites)} sites, ${fmt(S.points)} charge points.</td></tr>`;
}
