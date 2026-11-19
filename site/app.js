/* Berlin Charging Site Planner: renders the JSON written by `python -m chargeplan run`. */
(async function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? "–" : Number(n).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d }));
  const pct = (x, d = 0) => (x == null ? "–" : (x * 100).toFixed(d) + "%");
  const eurk = (n) => (n == null ? "–" : (n < 0 ? "−" : "+") + "€" + fmt(Math.abs(n) / 1000) + "k");
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const get = (f) => fetch("data/" + f).then((r) => r.json());

  // light / dark switch (remembered per browser)
  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (e) { /* storage blocked */ }
  $("#theme").onclick = () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (e) { /* ignore */ }
    location.reload();
  };

  const [S, A, P, SL, OPS, D] = await Promise.all(
    ["summary.json", "areas.geojson", "points.json", "shortlist.json", "operators.json", "districts.json"].map(get));
  const F = S.forecast, B = S.business, base = F.scenarios.base;
  const regDate = new Date(S.register_date).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

  $("#snap").innerHTML = [`Charging register ${regDate}`, `${fmt(S.planning_areas)} planning areas`, `${fmt(S.residents)} residents`,
    `KBA vehicle stock 1 Jan 2026`, `OpenStreetMap snapshot 19 Nov 2026`].map((t) => `<span>${esc(t)}</span>`).join('<span aria-hidden="true">·</span>');

  // KPIs
  const bev = S.bev_stock[Math.max(...Object.keys(S.bev_stock))];
  const kpis = [
    ["Public charge points", fmt(S.points), `${fmt(S.points_by_class.AC)} AC · ${fmt(S.points_by_class.DC)} DC · ${fmt(S.points_by_class.HPC)} HPC`],
    ["Operators", fmt(S.operators), `HHI ${fmt(S.hhi.hhi_hpc)} on HPC: unconcentrated`],
    ["BEVs per public point", fmt(S.bev_per_point, 1), `${fmt(bev)} BEVs registered (KBA)`],
    ["Residents ≤ 300 m of AC", pct(S.coverage.ac_walk), `${pct(S.coverage.hpc_reach)} within 1.5 km of HPC`],
    ["Shortlist", `${SL.hpc.length} + ${SL.dc.length}`, `HPC + DC sites · ${B.sites_positive} of ${B.sites} HPC NPV > 0`, "v2"],
    ["Points needed a year", fmt(base.points_per_year_needed), `to 2030 (Base) vs ${fmt(F.recent_build_pace)} recently`, "v2"],
  ];
  $("#kpis").innerHTML = kpis.map(([l, v, n, c]) => `<div class="kpi ${c || ""}"><div class="l">${l}</div><div class="v">${v}</div><div class="n">${n}</div></div>`).join("");

  // findings
  const topD = D.slice(0, 3).map((d) => d.district).join(", ");
  const best = [...SL.hpc].sort((a, b) => b.npv_base - a.npv_base).slice(0, 3);
  $("#findings").innerHTML = `
    <div><h3>Where</h3><b>${SL.hpc.length} HPC + ${SL.dc.length} DC sites</b><p>Fast-charging gaps are largest in ${esc(topD)}. Best investment cases:
      ${best.map((r) => `${esc(r.name || r.poi_label)} (${esc(r.district)})`).join(", ")}.</p></div>
    <div><h3>How much</h3><b>${fmt(base.points_per_year_needed)} points a year</b><p>About ${fmt(base.bev[2030])} BEVs by 2030 need ~${fmt(base.points[2030])} public points,
      against ${fmt(F.recent_build_pace)} added per year in 2024–25. Every scenario needs a faster build-out.</p></div>
    <div><h3>Is it worth it</h3><b>${B.sites_positive} of ${B.sites} sites clear 8 %</b><p>A typical site needs ${fmt(B.scenarios.Base.breakeven_mult * 100)} % of base-case
      utilisation to earn the hurdle rate, so location quality decides the investment.</p></div>`;

  // map
  const map = L.map("leaflet", { preferCanvas: true, scrollWheelZoom: false, zoomSnap: 0.5 }).setView([52.515, 13.40], 10.5);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 18, className: "tiles",
  }).addTo(map);
  const quant = (arr, q) => { const s = arr.filter((x) => x != null && !Number.isNaN(x)).sort((a, b) => a - b); return s[Math.floor(q * (s.length - 1))]; };
  const mix = (a, b, t) => `color-mix(in srgb, ${b} ${Math.round(t * 100)}%, ${a})`;
  let areaLayer;
  const drawAreas = (key) => {
    if (areaLayer) map.removeLayer(areaLayer);
    const vals = A.features.map((f) => f.properties[key]);
    const div = key.startsWith("gap");
    const lo = quant(vals, 0.05), hi = quant(vals, 0.95);
    const surface = css("--surface");
    const colour = (v) => {
      if (v == null) return surface;
      if (div) return v >= 0 ? mix(surface, css("--gap-hi"), Math.min(1, v / hi)) : mix(surface, css("--gap-lo"), Math.min(1, v / lo));
      return mix(surface, css("--brand"), Math.max(0, Math.min(1, (v - lo) / (hi - lo))));
    };
    areaLayer = L.geoJSON(A, {
      style: (f) => ({ fillColor: colour(f.properties[key]), fillOpacity: 0.72, color: css("--surface"), weight: 0.6 }),
      onEachFeature: (f, l) => {
        const p = f.properties;
        l.bindTooltip(`<b>${esc(p.plr_name)}</b> · ${esc(p.district)}<br>${fmt(p.residents)} residents · ${fmt(p.points)} points (${fmt(p.hpc_points)} HPC)<br>
          Fast-charging gap ${fmt(p.gap_dc_kwh)} kWh/day · AC gap ${fmt(p.gap_ac_kwh)} kWh/day`, { sticky: true });
      },
    }).addTo(map);
    areaLayer.bringToBack();
    const pts = `<span><i class="pt" style="background:${css("--ac")}"></i>AC</span><span><i class="pt" style="background:${css("--dc")}"></i>DC 50–149 kW</span>
      <span><i class="pt" style="background:${css("--hpc")}"></i>HPC ≥ 150 kW</span><span><i class="num" style="display:inline-flex;width:16px;height:16px;font-size:9px">1</i> shortlist</span>`;
    $("#legend").innerHTML = (div
      ? `<span><i class="sw" style="background:${mix(surface, css("--gap-lo"), 1)}"></i>better served than average</span>
         <span><i class="sw" style="background:${mix(surface, css("--gap-hi"), 1)}"></i>under-served</span>`
      : `<span><i class="sw" style="background:${mix(surface, css("--brand"), 0.1)}"></i>low</span><span><i class="sw" style="background:${css("--brand")}"></i>high</span>`) + pts;
  };
  drawAreas("gap_dc_km2");
  $("#layerSel").onchange = (e) => drawAreas(e.target.value);

  const ptsLayer = L.layerGroup();
  const col = { AC: css("--ac"), DC: css("--dc"), HPC: css("--hpc") };
  P.rows.forEach(([lat, lon, kw, cls, op]) => {
    L.circleMarker([lat, lon], { radius: cls === "AC" ? 1.6 : 3.2, color: col[cls], weight: 0, fillOpacity: cls === "AC" ? 0.4 : 0.9 })
      .bindTooltip(`${esc(op)} · ${fmt(kw)} kW ${cls}`).addTo(ptsLayer);
  });
  ptsLayer.addTo(map);
  const osm = (id) => `https://www.openstreetmap.org/${{ n: "node", w: "way", r: "relation" }[id[0]]}/${id.slice(1)}`;
  const addr = (r) => [r.street, r.house_no].filter(Boolean).join(" ");
  const popup = (r) => `<b>#${r.rank} ${esc(r.name || r.poi_label)}</b><br>${esc(r.poi_label)} · ${esc(addr(r))} ${esc(r.district)}<br>
    Score <b>${fmt(r.score, 1)}</b> · ${fmt(r.traffic_dtv)} cars/weekday · nearest HPC ${fmt(r.nearest_hpc_m / 1000, 1)} km<br>
    ${r.npv_base != null ? `Base-case NPV <b>${eurk(r.npv_base)}</b><br>` : ""}<i>${esc(r.reason)}</i><br><a target="_blank" rel="noopener" href="${osm(r.osm_id)}">OpenStreetMap ↗</a>`;
  const markers = { hpc: {}, dc: {} };
  const mk = (rows, t) => {
    const g = L.layerGroup();
    rows.forEach((r) => {
      const icon = L.divIcon({ className: "", html: `<div class="num ${t === "dc" ? "dc" : ""}">${r.rank}</div>`, iconSize: [24, 24], iconAnchor: [12, 12] });
      markers[t][r.rank] = L.marker([r.lat, r.lon], { icon }).bindPopup(popup(r)).addTo(g);
    });
    return g;
  };
  const layers = { hpc: mk(SL.hpc, "hpc").addTo(map), dc: mk(SL.dc, "dc") };
  const tog = (id, layer) => ($(id).onchange = (e) => (e.target.checked ? layer.addTo(map) : map.removeLayer(layer)));
  tog("#showPts", ptsLayer); tog("#showHpc", layers.hpc); tog("#showDc", layers.dc);

  // side list + detail table
  let tab = "hpc";
  const renderSites = () => {
    const pos = $("#f-pos").checked;
    const rows = SL[tab].filter((r) => !pos || tab !== "hpc" || r.npv_base > 0);
    $("#sites").innerHTML = rows.map((r) => `
      <li class="opp" tabindex="0" data-r="${r.rank}">
        <span class="r">#${r.rank}</span>
        <span class="p">${esc(r.name || r.poi_label)}<small>${esc(r.poi_label)} · ${esc(r.district)}${addr(r) ? " · " + esc(addr(r)) : ""}</small>
          <span class="tag ${r.priority.startsWith("Secure") ? "hot" : "calm"}">${r.priority.startsWith("Secure") ? "Secure first" : "Lower competitive pressure"}</span>
          ${r.npv_base != null ? `<span class="tag ${r.npv_base > 0 ? "npv-pos" : "npv-neg"}">NPV ${eurk(r.npv_base)}</span>` : ""}</span>
        <span class="s">${fmt(r.score, 1)}<small>score</small></span>
      </li>`).join("") || '<li class="note">No site matches the filter.</li>';
    document.querySelectorAll("#sites .opp").forEach((li) => {
      const go = () => {
        const r = SL[tab].find((x) => x.rank === +li.dataset.r);
        if (!map.hasLayer(layers[tab])) { layers[tab].addTo(map); $(tab === "hpc" ? "#showHpc" : "#showDc").checked = true; }
        document.querySelectorAll("#sites .opp").forEach((x) => x.classList.toggle("on", x === li));
        map.setView([r.lat, r.lon], 14.5);
        markers[tab][r.rank].openPopup();
      };
      li.onclick = go;
      li.onkeydown = (e) => { if (e.key === "Enter") go(); };
    });
    $("#shortNote").textContent = tab === "hpc"
      ? "High-traffic hosts with short stops: drive-ins, shopping centres, independent fuel stations, DIY stores. NPV uses the Base scenario with utilisation scaled by the score."
      : "Supermarkets suit 50–150 kW: a 20–40 minute shop adds 50–150 km of range. Chosen where the fast-charging gap is largest.";
    const w = [["s_gap", 0.35, css("--gap-hi")], ["s_traffic", 0.25, css("--ink-2")], ["s_whitespace", 0.25, css("--accent")], ["s_poi", 0.15, css("--brand-2")]];
    $("#shortTbl").innerHTML = `<thead><tr><th>#</th><th>Host</th><th>Type</th><th>Address</th><th>District</th><th class="n">Score</th>
      <th title="gap · traffic · whitespace · host">Score parts</th><th class="n">Cars/weekday</th><th class="n">Nearest HPC</th>
      ${tab === "hpc" ? '<th class="n">NPV (Base)</th><th class="n">IRR</th>' : ""}<th>Why</th></tr></thead><tbody>` +
      SL[tab].map((r) => `<tr><td>${r.rank}</td><td><b>${esc(r.name || "–")}</b></td><td>${esc(r.poi_label)}</td><td>${esc(addr(r) || "–")}</td>
        <td>${esc(r.district)}</td><td class="n"><b>${fmt(r.score, 1)}</b></td>
        <td><span class="parts">${w.map(([k, wt, c]) => `<i style="width:${r[k] * wt * 100}%;background:${c}"></i>`).join("")}</span></td>
        <td class="n">${fmt(r.traffic_dtv)}</td><td class="n">${fmt(r.nearest_hpc_m / 1000, 1)} km</td>
        ${tab === "hpc" ? `<td class="n ${r.npv_base > 0 ? "pos" : "neg"}">${eurk(r.npv_base)}</td><td class="n">${r.irr_base == null ? "–" : pct(r.irr_base, 1)}</td>` : ""}
        <td class="wrap">${esc(r.reason)}</td></tr>`).join("") + "</tbody>";
  };
  document.querySelectorAll(".seg button").forEach((b) => (b.onclick = () => {
    tab = b.dataset.t;
    document.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x === b));
    renderSites();
  }));
  $("#f-pos").onchange = renderSites;
  renderSites();

  // charts
  Chart.defaults.font.family = css("--f");
  Chart.defaults.color = css("--ink-2");
  Chart.defaults.borderColor = css("--line");
  const hl = (o) => (o === "Allego" ? css("--accent") : css("--brand-2"));
  const top = OPS.slice(0, 12);
  new Chart($("#opChart"), {
    type: "bar",
    data: { labels: top.map((o) => o.operator), datasets: [{ data: top.map((o) => o.kw / 1000), backgroundColor: top.map((o) => hl(o.operator)), borderRadius: 4 }] },
    options: { maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { display: false },
      tooltip: { callbacks: { label: (c) => `${fmt(c.raw, 1)} MW · ${fmt(top[c.dataIndex].points)} points · ${fmt(top[c.dataIndex].sites)} sites` } } },
      scales: { x: { title: { display: true, text: "MW installed" } } } },
  });
  const h = [...OPS].sort((a, b) => b.hpc - a.hpc).slice(0, 10);
  const rest = 1 - h.reduce((s, o) => s + o.share_hpc, 0);
  new Chart($("#hpcChart"), {
    type: "bar",
    data: { labels: [...h.map((o) => o.operator), "Others"], datasets: [{ data: [...h.map((o) => o.share_hpc * 100), rest * 100],
      backgroundColor: [...h.map((o) => hl(o.operator)), css("--muted")], borderRadius: 4 }] },
    options: { maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => fmt(c.raw, 1) + " % of HPC points" } } },
      scales: { x: { title: { display: true, text: "% of HPC points" } } } },
  });
  $("#hhiNote").textContent = `HHI ${fmt(S.hhi.hhi_hpc)} on HPC points and ${fmt(S.hhi.hhi_kw)} on installed power: below 1,500 is unconcentrated, so good host sites are the scarce resource.`;

  $("#distTbl").innerHTML = `<thead><tr><th>District</th><th class="n">Residents</th><th class="n">BEVs (est.)</th><th class="n">Points</th><th class="n">HPC</th>
    <th class="n">BEVs per point</th><th class="n">≤ 300 m AC</th><th class="n">≤ 1.5 km HPC</th><th class="n">Fast gap kWh/day</th><th class="n">AC gap kWh/day</th></tr></thead><tbody>` +
    D.map((d) => `<tr><td><b>${esc(d.district)}</b></td><td class="n">${fmt(d.residents)}</td><td class="n">${fmt(d.bev_est)}</td><td class="n">${fmt(d.points)}</td>
      <td class="n">${fmt(d.hpc)}</td><td class="n">${fmt(d.bev_per_point, 1)}</td><td class="n">${pct(d.cov_ac_walk)}</td><td class="n">${pct(d.cov_hpc_reach)}</td>
      <td class="n ${d.gap_dc > 0 ? "neg" : "pos"}">${fmt(d.gap_dc)}</td><td class="n ${d.gap_ac > 0 ? "neg" : "pos"}">${fmt(d.gap_ac)}</td></tr>`).join("") + "</tbody>";

  // forecast
  const years = Object.keys(base.bev).map(Number);
  const sc = { slow: css("--muted"), base: css("--brand"), fast: css("--accent") };
  const ds = [];
  for (const [n, s] of Object.entries(F.scenarios)) {
    ds.push({ label: `BEVs, ${n}`, data: years.map((y) => s.bev[y] / 1000), borderColor: sc[n], backgroundColor: sc[n], borderWidth: n === "base" ? 2.5 : 1.5, pointRadius: 0, yAxisID: "y" });
    ds.push({ label: `Points, ${n}`, data: years.map((y) => s.points[y] / 1000), borderColor: sc[n], backgroundColor: sc[n], borderDash: [5, 4], borderWidth: n === "base" ? 2 : 1.2, pointRadius: 0, yAxisID: "y2" });
  }
  ds.push({ label: "BEVs observed", type: "scatter", data: years.map((y) => (F.observed.bev[y] != null ? F.observed.bev[y] / 1000 : null)), backgroundColor: css("--gap-hi"), pointRadius: 5, yAxisID: "y" });
  ds.push({ label: "Points observed", type: "scatter", data: years.map((y) => (F.observed.points[y] != null ? F.observed.points[y] / 1000 : null)), backgroundColor: css("--hpc"), pointStyle: "rectRot", pointRadius: 6, yAxisID: "y2" });
  new Chart($("#fcChart"), {
    type: "line", data: { labels: years, datasets: ds },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { boxWidth: 10, font: { size: 10.5 } } } },
      scales: { y: { title: { display: true, text: "BEVs (thousand, solid)" }, beginAtZero: true },
        y2: { position: "right", title: { display: true, text: "public points (thousand, dashed)" }, beginAtZero: true, grid: { drawOnChartArea: false } } } },
  });
  $("#fcCards").innerHTML = Object.entries(F.scenarios).map(([n, s]) => `<div><b>${fmt(s.points_per_year_needed)}</b>
    <span>points a year, ${n} scenario · ${fmt(s.bev[2030])} BEVs by 2030 · fit error ${pct(s.fit_rmse_log, 1)}</span></div>`).join("");

  // business case
  $("#bcCards").innerHTML = ["Pessimistic", "Base", "Optimistic"].map((n) => {
    const s = B.scenarios[n];
    return `<div><b class="${s.npv > 0 ? "pos" : "neg"}">${eurk(s.npv)}</b><span>${n} NPV · IRR ${s.irr == null ? "n/a" : pct(s.irr, 1)} ·
      payback ${s.payback == null ? "> 10 years" : s.payback + " years"} · break-even at ${fmt(s.breakeven_mult * 100)} % of utilisation</span></div>`;
  }).join("") + `<div><b>${eurk(-B.capex).replace("−", "")}</b><span>capex per site: 4 × €60k hardware, €140k grid, €90k civil, €20k planning</span></div>`;
  const t = B.tornado.slice(0, 10);
  new Chart($("#tornado"), {
    type: "bar",
    data: { labels: t.map((r) => r[0].replace(/ \(.*\)/, "")), datasets: [
      { label: "pessimistic value", data: t.map((r) => r[1] / 1000), backgroundColor: css("--gap-hi"), borderRadius: 3 },
      { label: "optimistic value", data: t.map((r) => r[2] / 1000), backgroundColor: css("--brand-2"), borderRadius: 3 }] },
    options: { maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { position: "bottom" } },
      scales: { x: { stacked: true, title: { display: true, text: "change in NPV (€k)" } }, y: { stacked: true, ticks: { font: { size: 11 } } } } },
  });
  const g = B.grid;
  $("#gridTbl").innerHTML = `<thead><tr><th>Avg utilisation</th>${g.cols.map((c) => `<th class="n">${c}</th>`).join("")}</tr></thead><tbody>` +
    g.rows.map((r, i) => `<tr><td><b>${r}</b></td>${g.values[i].map((v) => `<td class="n ${v > 0 ? "pos" : "neg"}">${fmt(v / 1000)}</td>`).join("")}</tr>`).join("") + "</tbody>";
  $("#siteNpvNote").innerHTML = `Per site, utilisation scales with the location score (0.8× to 1.2× of Base): ${B.sites_positive} of ${B.sites} shortlisted sites clear the 8 % hurdle.`;

  // validation
  const bt = S.backtest, em = S.entry_model;
  $("#bt").innerHTML = `<div class="card-head"><div><h2>Backtest: did the score anticipate 2025–26 openings?</h2>
    <p>Every candidate scored with the network of 1 Jan 2025, then checked for a new HPC point within 300 m (${fmt(bt.hpc_points_after)} HPC points opened since).</p></div></div>
    <div class="table-wrap"><table class="mini-t"><thead><tr><th>Signal</th><th class="n">AUC</th></tr></thead><tbody>
    ${Object.entries(bt.auc).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, " "))}</td><td class="n">${fmt(v, 2)} <i class="bar" style="width:${Math.max(0, (v - 0.4) * 300)}px"></i></td></tr>`).join("")}</tbody></table></div>
    <p class="note"><b>Reading:</b> 0.5 = chance. Operators' recent openings followed <b>traffic</b> and <b>host type</b>, not residential gaps (${fmt(bt.auc.gap, 2)}).
    The opportunity score targets under-served demand, so it is not a forecast of competitor behaviour; the model on the right is.</p>`;
  $("#em").innerHTML = `<div class="card-head"><div><h2>Competitor-entry model</h2><p>Logistic regression trained on 2023–24 openings, tested out of time on 2025–26.</p></div>
    <span class="pill ok">Test AUC ${fmt(em.test_auc, 2)}</span></div>
    <div class="chg"><div><b>${fmt(em.train_auc, 2)}</b><span>AUC on training period</span></div><div><b>${fmt(em.test_auc, 2)}</b><span>AUC out of time</span></div>
    <div><b>${fmt(em.test_hit_rate_top20 / em.test_hit_rate_rest, 1)}×</b><span>hit rate, top fifth vs rest (${pct(em.test_hit_rate_top20, 1)} vs ${pct(em.test_hit_rate_rest, 1)})</span></div></div>
    <p class="note">Strongest drivers: traffic volume (+), fuel and drive-in hosts (+), hotels (−). Used to flag shortlisted sites to <b>secure first</b>,
    before a competitor takes them.</p>`;

  // data quality
  const icon = { error: ["✕", "bad"], warning: ["!", "warn"], info: ["i", "ok"] };
  const flagged = S.quality.filter((q) => q.count > 0 && q.severity !== "info").length;
  $("#q-sub").textContent = `${fmt(S.devices_raw)} Berlin devices → ${fmt(S.devices_used)} used → ${fmt(S.sites)} sites, ${fmt(S.points)} charge points. 12 checks, applied in code on every run.`;
  $("#q-pill").textContent = `${flagged} checks with findings`;
  $("#checks").innerHTML = S.quality.map((q) => {
    const [ic, cls] = q.count === 0 ? ["✓", "ok"] : icon[q.severity];
    const ex = q.examples[0] ? Object.values(q.examples[0]).filter(Boolean).slice(0, 4).join(" · ") : "";
    return `<li><span class="ic ${cls}">${ic}</span><span><b>${q.check_id}</b> ${esc(q.title)}</span><span class="ct">${fmt(q.count)}</span>
      <span class="ex">${esc(q.action)}${ex ? ` — e.g. ${esc(ex)}` : ""}</span></li>`;
  }).join("");

  // downloads
  const dl = [
    ["downloads/hpc-business-case-macros.xlsm", "XLSM", "m", "Business case with macros", "Ctrl+Shift+R runs all scenarios; Ctrl+Shift+B finds the break-even price by Goal Seek."],
    ["downloads/hpc-business-case.xlsx", "XLSX", "x", "Business case (no macros)", "Scenario dropdown, 10-year cash flow, NPV/IRR per shortlisted site, sensitivity."],
    ["downloads/shortlist_hpc.csv", "CSV", "", "HPC shortlist", "25 sites with score parts, traffic, competitors, NPV and reasons."],
    ["downloads/planning_areas.csv", "CSV", "", "Planning areas", "Demand, capacity and relative gaps for all 542 areas."],
    ["downloads/operators.csv", "CSV", "", "Operators", "Points, power, HPC and market shares for 185 operators."],
    ["https://github.com/Kiran-koppera/berlin-charging-site-planner/blob/main/src/chargeplan/vba/ScenarioRunner.bas", "VBA", "m", "Macro source code", "The ScenarioRunner module, readable on GitHub."],
  ];
  $("#dl").innerHTML = dl.map(([href, ext, c, title, desc]) => `<a class="dl" href="${href}"><span class="ext ${c}">${ext}</span><span><b>${title}</b><span>${desc}</span></span></a>`).join("");
})();
