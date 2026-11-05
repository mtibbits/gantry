"use strict";
// The worksite page's own script (#9): the network's detail, trace and zoom,
// and the lane schedule drawn from gantrySchedule.plan. Every record value is
// inserted as text, or through esc; a class comes from CLASS_SET only; a
// lookup by key is a Map. Listeners are delegated on document.
(function () {
  const ESC = new Map([["&", "&amp;"], ["<", "&lt;"], [">", "&gt;"], ['"', "&quot;"], ["'", "&#39;"]]);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC.get(c));
  // gantry/render.py's CLASS_TOKENS; a test holds the two equal.
  const CLASS_SET = new Set([
    "m-haiku", "m-sonnet", "m-opus", "m-fable", "m-other",
    "st-pending", "st-wip", "st-stuck", "st-blocked", "st-parked", "st-other",
    "crit", "near", "free", "unest", "pr-hi", "pr-p0",
  ]);
  const tok = (t) => (typeof t === "string" && CLASS_SET.has(t) ? t : "");
  const classTokens = () => Array.from(CLASS_SET).sort();
  globalThis.gantryPage = Object.freeze({ esc, classTokens });

  const block = document.getElementById("gantry-data");
  if (block === null) return;
  const DATA = JSON.parse(block.textContent);
  const leaves = new Map(DATA.leaves.map((leaf) => [leaf.key, leaf]));
  const lanes = DATA.lanes;
  const items = DATA.schedule.items;
  const HPD = DATA.hours_per_day;
  const ELLIPSIS = "\u2026";
  const cut = (s, n) => (s.length > n ? s.slice(0, n - 1).trimEnd() + ELLIPSIS : s);
  const num = (x) => (Math.round(x * 10) / 10).toString();
  const laneText = (i) => [lanes[i].tag, lanes[i].name].filter((s) => s !== "").join(" ");
  const lanesText = (k) => (k === 1 ? "1 lane" : k + " lanes");
  // The page's count texts (#10): numbers only are filled in.
  const count = (n, many, one) => (n === 1 ? one : many.split("{n}").join(String(n)));
  const includes = (n) => count(n, DATA.texts.includes, DATA.texts.includes_one);
  const fillIn = (t, pairs) => pairs.reduce((s, [name, v]) => s.split("{" + name + "}").join(String(v)), t);
  const onPath = new Set(DATA.network.path);
  const SOURCES = new Map([["est", " (from its est: value)"], ["default", " (unestimated: the default unit)"]]);
  const sourceText = (leaf) => (leaf.source === "estimates_file"
    ? " (from the estimates file" + (leaf.effort === null ? "" : ", effort " + leaf.effort) + ")"
    : SOURCES.get(leaf.source) || "");

  // --- the detail panels, built as text --------------------------------------

  function line(parent, parts) {
    const div = document.createElement("div");
    parts.forEach(([label, value], i) => {
      if (i) div.append(" \u00b7 ");
      if (label !== null) {
        const k = document.createElement("span");
        k.className = "k";
        k.textContent = label;
        div.append(k, " ");
      }
      div.append(String(value));
    });
    parent.append(div);
  }

  function leafLines(box, leaf) {
    const title = document.createElement("div");
    const key = document.createElement("span");
    key.className = "mono";
    key.textContent = leaf.key;
    title.append(key, " ", leaf.title);
    box.append(title);
    line(box, [["Epic:", laneText(leaf.lane)], ["priority", leaf.priority === null ? "none" : leaf.priority],
      ["scheduled as", "P" + leaf.eff]]);
    line(box, [["Hours:", num(leaf.hours) + sourceText(leaf)]]
      .concat(leaf.non_time === null ? [] : [["Not in the hours:", leaf.non_time]])
      .concat(leaf.mark === null ? [] : [["state", leaf.mark]]));
    if (leaf.models !== null) {
      line(box, [["Thinking:", leaf.models[0] === null ? "none" : leaf.models[0]],
        ["checking", leaf.models[1] === null ? "none" : leaf.models[1]]]);
    }
    if (leaf.flags.length) line(box, [["Flags:", leaf.flags.join(", ")]]);
    if (leaf.meta.length) line(box, [["Metadata:", leaf.meta.map(([k, v]) => k + ": " + v).join("; ")]]);
    if (leaf.foreign.length) line(box, [["Waits on:", leaf.foreign.join("; ")]]);
  }

  function resting(box) {
    const saved = Array.from(box.childNodes, (n) => n.cloneNode(true));
    return () => box.replaceChildren(...saved.map((n) => n.cloneNode(true)));
  }

  // --- the network ----------------------------------------------------------

  const svg = document.querySelector("svg.net");
  const detail = document.getElementById("detail");
  const clearNet = detail === null ? () => {} : resting(detail);

  function walk(start, side) {
    const seen = new Set();
    const stack = leaves.get(start)[side].slice();
    while (stack.length) {
      const k = stack.pop();
      if (seen.has(k)) continue;
      seen.add(k);
      stack.push(...leaves.get(k)[side]);
    }
    return seen;
  }

  let traced = null;

  function showNet(key) {
    const leaf = leaves.get(key);
    // mouseover fires again for each child of the node the pointer crosses.
    if (leaf === undefined || svg === null || detail === null || key === traced) return;
    traced = key;
    const up = walk(key, "preds");
    const down = walk(key, "succs");
    const inUp = (k) => k === key || up.has(k);
    const inDown = (k) => k === key || down.has(k);
    svg.classList.add("tracing");
    for (const g of svg.querySelectorAll(".node")) {
      const k = g.getAttribute("data-item");
      g.classList.toggle("lit", inUp(k) || inDown(k));
    }
    for (const e of svg.querySelectorAll(".edge")) {
      const u = e.getAttribute("data-u");
      const v = e.getAttribute("data-v");
      e.classList.toggle("lit", (inUp(u) && inUp(v)) || (inDown(u) && inDown(v)));
    }
    detail.replaceChildren();
    leafLines(detail, leaf);
    line(detail, [["Earliest slot:", leaf.es + 1], ["slack", leaf.slack]]);
    line(detail, [["Waits on:", leaf.preds.join(", ") || "none"], ["unblocks", leaf.succs.join(", ") || "none"],
      [null, up.size + " upstream, " + down.size + " downstream"]]);
  }

  function leaveNet() {
    traced = null;
    if (svg !== null) svg.classList.remove("tracing");
    clearNet();
  }

  function setZoom(mode) {
    if (svg !== null) svg.classList.toggle("mode-wave", mode === "wave");
    for (const b of controls("zoom")) b.setAttribute("aria-pressed", String(b.getAttribute("data-value") === mode));
  }

  // --- the schedule ---------------------------------------------------------

  const host = document.getElementById("gantt");
  const gdetail = document.getElementById("gdetail");
  const limitLine = document.getElementById("g-limit");
  const datesLine = document.getElementById("g-dates");
  const summaryLine = document.getElementById("g-sum");
  const select = Array.from(document.querySelectorAll("select[data-control]"))
    .find((s) => s.getAttribute("data-control") === "lanes");
  const clearSched = gdetail === null ? () => {} : resting(gdetail);
  const LABW = 320;
  const TW = 860;
  const PADR = 70;
  const TOP = 34;
  const LH = 19;
  const TRK = 16;
  const WLH = 40;
  const open = new Set();
  let k = select === undefined ? DATA.lanes_default : Number(select.value);
  let group = "epic";
  let rows = [];
  let byKey = new Map();
  let end = 0;
  let critEnd = 0;
  let span = 20;
  let hot = null;
  const W = LABW + TW + PADR;
  const px = (d) => LABW + (d / span) * TW;

  function controls(name) {
    return Array.from(document.querySelectorAll("button[data-control]"))
      .filter((b) => b.getAttribute("data-control") === name);
  }

  function compute() {
    const p = gantrySchedule.plan(items, k);
    rows = items.map((item) => {
      const [a, b, wl] = p.get(item.key);
      return { leaf: leaves.get(item.key), a: a / HPD, b: b / HPD, wl };
    });
    byKey = new Map(rows.map((r) => [r.leaf.key, r]));
    end = rows.reduce((m, r) => Math.max(m, r.b), 0);
    span = Math.max(5, Math.ceil(end / 5) * 5);
    // gantry/views/worksite.py's summary_text, step for step (U6): sums in
    // items order, rounding last and half up.
    let busy = 0;
    let chain = 0;
    critEnd = 0;
    for (const r of rows) {
      busy += r.b - r.a;
      if (onPath.has(r.leaf.key)) {
        chain += r.b - r.a;
        critEnd = Math.max(critEnd, r.b);
      }
    }
    if (summaryLine !== null) {
      const u = DATA.unestimated;
      summaryLine.setAttribute("data-unestimated", String(u.site));
      summaryLine.setAttribute("data-unestimated-chain", String(u.path));
      let text = "";
      if (rows.length) {
        text = fillIn(DATA.texts.summary, [["lanes", lanesText(k)], ["end", Math.round(end)],
          ["crit", Math.round(critEnd)], ["chain", Math.round(chain)], ["even", Math.round(busy / k)],
          ["busy", Math.round(busy / (k * end) * 100)]]);
        if (u.site) {
          const form = u.site === 1 ? DATA.texts.summary_unest_one : DATA.texts.summary_unest;
          text += " The plan " + fillIn(form, [["n", u.site], ["m", u.path]]) + ".";
        }
      }
      summaryLine.textContent = text;
    }
    if (limitLine !== null) {
      const name = items.length ? gantrySchedule.limit(items, k) : null;
      const text = name === "chain" ? DATA.texts.chain : name === "capacity" ? DATA.texts.capacity : "";
      limitLine.textContent = text.split("{lanes}").join(lanesText(k));
    }
  }

  function stack(list) {
    const ends = [];
    const at = list.map((r) => {
      let t = ends.findIndex((e) => e <= r.a + 1e-9);
      if (t < 0) {
        t = ends.length;
        ends.push(0);
      }
      ends[t] = r.b;
      return t;
    });
    return { at, n: Math.max(1, ends.length) };
  }

  function segClass(leaf) {
    return ["seg", leaf.sclass === "crit" ? "crit" : "", leaf.mc === null ? "" : tok(leaf.mc[0]), tok(leaf.sc),
      leaf.unestimated ? tok("unest") : ""].filter((c) => c !== "").join(" ");
  }

  function extent(r) {
    const x = px(r.a);
    return [x, Math.max(1.2, px(r.b) - x)];
  }

  function bar(r, y, h) {
    const [x, w] = extent(r);
    let out = '<rect class="' + segClass(r.leaf) + '" data-item="' + esc(r.leaf.key) + '" data-lane="' + r.leaf.lane +
      '" x="' + num(x) + '" y="' + num(y) + '" width="' + num(w) + '" height="' + h + '" tabindex="0"/>';
    // The bar comes first: a data-item lookup finds it before its mark.
    const mark = tok(r.leaf.prio_mark);
    if (mark !== "") {
      out += '<circle class="pmark ' + mark + '" data-item="' + esc(r.leaf.key) + '" cx="' + num(x + Math.min(w / 2, 5)) +
        '" cy="' + num(y - 3) + '" r="2.2"/>';
    }
    return out;
  }

  function laneRect(i, y, h) {
    return '<rect class="lane' + (i % 2 ? " alt" : "") + '" x="0" y="' + y + '" width="' + W + '" height="' + h + '"/>';
  }

  function text(cls, x, y, value, anchor) {
    return '<text class="' + cls + '" x="' + num(x) + '" y="' + num(y) + '"' +
      (anchor ? ' text-anchor="' + anchor + '"' : "") + ">" + esc(value) + "</text>";
  }

  function render() {
    for (const b of controls("expand")) b.disabled = group !== "epic";
    hot = null;
    if (datesLine !== null) {
      // Every drawn date depends on the whole schedule: the site's N.
      const n = DATA.unestimated.site;
      datesLine.setAttribute("data-unestimated", String(n));
      datesLine.textContent = n ? count(n, DATA.texts.dates, DATA.texts.dates_one) : "";
    }
    if (host === null) return;
    if (!rows.length) {
      host.replaceChildren();
      return;
    }
    const out = [];
    let y = TOP;
    if (group === "epic") {
      lanes.forEach((ln, li) => {
        const list = rows.filter((r) => r.leaf.lane === li).sort((p, q) => p.a - q.a);
        if (!list.length) return;
        const isOpen = open.has(li);
        const st = stack(list);
        const GH = Math.max(44, st.n * TRK + 12);
        const h = GH + (isOpen ? list.length * LH + 6 : 0);
        const hours = list.reduce((s, r) => s + r.leaf.hours, 0);
        const first = list[0].a;
        const last = list.reduce((m, r) => Math.max(m, r.b), 0);
        out.push(laneRect(li, y, h));
        const unest = ln.unestimated;
        out.push('<g class="g-toggle" data-lane="' + li + '" data-unestimated="' + unest +
          '" tabindex="0" role="button" aria-expanded="' + isOpen + '">' +
          '<rect x="0" y="' + y + '" width="' + (LABW - 8) + '" height="' + GH + '"/>' +
          text("g-name", 14, y + 18, (isOpen ? "\u25be " : "\u25b8 ") + cut(laneText(li), 40)) +
          text("g-sub", 28, y + 33, list.length + (list.length === 1 ? " leaf" : " leaves") + " \u00b7 " + num(hours) +
            " h \u00b7 days " + num(first) + "\u2013" + num(last) + (unest ? " \u00b7 " + includes(unest) : "")) +
          "</g>");
        const top = y + (GH - st.n * TRK) / 2;
        list.forEach((r, j) => out.push(bar(r, top + st.at[j] * TRK + 5, TRK - 5)));
        if (isOpen) {
          list.forEach((r, j) => {
            const ry = y + GH + j * LH + 13;
            const [x, w] = extent(r);
            out.push(text("g-leaf", 28, ry, cut(r.leaf.key + " " + r.leaf.title, 44)));
            if (r.leaf.priority !== null) out.push(text("g-prio", LABW - 12, ry, cut(r.leaf.priority, 8), "end"));
            out.push(bar(r, ry - 9, 11));
            out.push(text("g-hrs", x + w + 5, ry, num(r.leaf.hours) + " h" + (r.leaf.unestimated ? ", unestimated" : "")));
            if (r.leaf.mark !== null) out.push(text("g-mark", x + w + 90, ry, r.leaf.mark));
          });
        }
        y += h;
      });
    } else {
      for (let wl = 0; wl < k; wl++) {
        const list = rows.filter((r) => r.wl === wl).sort((p, q) => p.a - q.a);
        const busy = list.reduce((s, r) => s + (r.b - r.a), 0);
        out.push(laneRect(wl, y, WLH));
        out.push(text("g-name", 14, y + 17, "Lane " + (wl + 1)));
        out.push(text("g-sub", 14, y + 32, list.length + (list.length === 1 ? " leaf" : " leaves") + " \u00b7 busy " +
          Math.round((busy / end) * 100) + "% of the plan"));
        list.forEach((r) => out.push(bar(r, y + 12, 20)));
        y += WLH;
      }
    }
    const H = y + 8;
    const axis = [];
    const step = span > 120 ? 20 : span > 40 ? 10 : 5;
    for (let d = 0; d <= span; d += step) {
      axis.push('<line class="g-grid" x1="' + num(px(d)) + '" y1="' + (TOP - 6) + '" x2="' + num(px(d)) + '" y2="' + H + '"/>');
      axis.push(text("g-axis", px(d), TOP - 12, d === 0 ? "day 0" : String(d), "middle"));
    }
    axis.push(text("g-axis", 14, TOP - 12, "working days, " + HPD + " agent-hours per lane"));
    // Both finish marks are built on every leaf's hours: the site's N.
    const u = DATA.unestimated.site;
    const note = u ? "; " + includes(u) : "";
    const markLine = (cls, d, label) => '<line class="' + cls + '" data-unestimated="' + u + '" x1="' + num(px(d)) +
      '" y1="' + (TOP - 4) + '" x2="' + num(px(d)) + '" y2="' + H + '"><title>' + esc(label + num(d) + note) +
      "</title></line>";
    const chainShown = DATA.network.path.length > 0;
    const marks = (chainShown ? markLine("mk-crit", critEnd, "the critical chain ends around day ") : "") +
      markLine("mk-end", end, "all open work ends around day ");
    const label = "Schedule of the open leaves on " + lanesText(k) + "." + (chainShown
      ? " The critical chain finishes around working day " + Math.round(critEnd) + " and all work around day " +
        Math.round(end) + "."
      : " All work finishes around working day " + Math.round(end) + ".");
    host.innerHTML = '<svg class="gantt" viewBox="0 0 ' + W + " " + H + '" width="' + W + '" height="' + H +
      '" role="img" aria-label="' + esc(label) + '">' + axis.join("") + out.join("") + marks + "</svg>";
  }

  function showSched(key) {
    const r = byKey.get(key);
    if (r === undefined || gdetail === null || host === null || key === hot) return;
    hot = key;
    for (const s of host.querySelectorAll(".seg")) s.classList.toggle("hot", s.getAttribute("data-item") === key);
    gdetail.replaceChildren();
    leafLines(gdetail, r.leaf);
    line(gdetail, [["Scheduled:", "lane " + (r.wl + 1) + ", days " + num(r.a) + "\u2013" + num(r.b)],
      [null, r.leaf.sclass === "crit" ? "on the critical path" : r.leaf.linked ? "linked in the network" : "no links: could move anywhere"]]);
  }

  function leaveSched() {
    hot = null;
    if (host !== null) for (const s of host.querySelectorAll(".seg.hot")) s.classList.remove("hot");
    clearSched();
  }

  function setGroup(g) {
    group = g;
    for (const b of controls("group")) b.setAttribute("aria-pressed", String(b.getAttribute("data-value") === g));
    render();
  }

  function toggle(li) {
    if (open.has(li)) open.delete(li);
    else open.add(li);
    render();
  }

  // --- delegated listeners ----------------------------------------------------

  function itemOf(target, cls) {
    const el = target instanceof Element ? target.closest("." + cls) : null;
    return el !== null && el.hasAttribute("data-item") ? el.getAttribute("data-item") : null;
  }

  function enter(e) {
    const node = itemOf(e.target, "node");
    if (node !== null) showNet(node);
    const seg = itemOf(e.target, "seg");
    if (seg !== null) showSched(seg);
  }

  function leave(e) {
    const to = e.relatedTarget instanceof Element ? e.relatedTarget : null;
    for (const [cls, clear] of [["node", leaveNet], ["seg", leaveSched]]) {
      const from = itemOf(e.target, cls);
      if (from !== null && (to === null || itemOf(to, cls) !== from)) clear();
    }
  }

  document.addEventListener("focusin", enter);
  document.addEventListener("mouseover", enter);
  document.addEventListener("focusout", leave);
  document.addEventListener("mouseout", leave);
  document.addEventListener("change", (e) => {
    if (e.target === select) {
      k = Number(select.value);
      compute();
      render();
      leaveSched();
    }
  });
  document.addEventListener("click", (e) => {
    const target = e.target instanceof Element ? e.target : null;
    if (target === null) return;
    const button = target.closest("button[data-control]");
    if (button !== null) {
      const control = button.getAttribute("data-control");
      const value = button.getAttribute("data-value");
      if (control === "zoom") setZoom(value);
      if (control === "group") setGroup(value);
      if (control === "expand") {
        if (value === "all") lanes.forEach((_, i) => open.add(i));
        else open.clear();
        render();
      }
      return;
    }
    const row = target.closest(".g-toggle");
    if (row !== null) toggle(Number(row.getAttribute("data-lane")));
  });
  document.addEventListener("keydown", (e) => {
    const target = e.target instanceof Element ? e.target : null;
    const row = target === null ? null : target.closest(".g-toggle");
    if (row !== null && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      const li = Number(row.getAttribute("data-lane"));
      toggle(li);
      const again = Array.from(host.querySelectorAll(".g-toggle")).find((g) => g.getAttribute("data-lane") === String(li));
      if (again !== undefined) again.focus();
    }
  });

  compute();
  render();
})();
