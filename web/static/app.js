// Search, the view switch, the command palette, schedule status and reveal-on-scroll.
// Every page works without it: links, anchors and the PBS check buttons are plain HTML.
(function () {
  "use strict";
  var root = document.documentElement;
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ── View: patient or pharmacist ─────────────────────────────────────────────
  function setView(view) {
    if (view === "pharmacist") root.dataset.view = "pharmacist"; else delete root.dataset.view;
    try { localStorage.setItem("view", view); } catch (e) {}
    paintView();
  }
  function paintView() {
    var current = root.dataset.view === "pharmacist" ? "pharmacist" : "simple";
    document.querySelectorAll("[data-view-set]").forEach(function (b) {
      b.setAttribute("aria-pressed", b.dataset.viewSet === current ? "true" : "false");
    });
  }
  document.querySelectorAll("[data-view-set]").forEach(function (b) {
    b.addEventListener("click", function () { setView(b.dataset.viewSet); });
  });
  paintView();

  // ── Theme: system, light or dark ────────────────────────────────────────────
  var themeMenu = document.querySelector("[data-theme-menu]");
  function paintTheme() {
    var current = root.dataset.theme || "system";
    document.querySelectorAll("[data-theme-set]").forEach(function (b) {
      b.setAttribute("aria-checked", b.dataset.themeSet === current ? "true" : "false");
    });
    var dark = root.dataset.theme === "dark" ||
      (!root.dataset.theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.querySelectorAll('meta[name="theme-color"]').forEach(function (m) {
      m.setAttribute("content", dark ? "#0a0e0f" : "#fbfcfc");
      m.removeAttribute("media");
    });
  }
  document.querySelectorAll("[data-theme-set]").forEach(function (b) {
    b.addEventListener("click", function () {
      var t = b.dataset.themeSet;
      if (t === "system") delete root.dataset.theme; else root.dataset.theme = t;
      try { localStorage.setItem("theme", t); } catch (e) {}
      paintTheme();
      if (themeMenu) themeMenu.open = false;
    });
  });
  if (themeMenu) {
    document.addEventListener("click", function (ev) { if (!themeMenu.contains(ev.target)) themeMenu.open = false; });
    themeMenu.addEventListener("keydown", function (ev) { if (ev.key === "Escape") { themeMenu.open = false; themeMenu.querySelector("summary").focus(); } });
  }
  paintTheme();

  // ── Schedule status ─────────────────────────────────────────────────────────
  // PBS releases a schedule on the 1st. If the site's month is behind the current month
  // in Australia, pbs.gov.au already shows newer prices than we do.
  var now = new Intl.DateTimeFormat("en-CA", { timeZone: "Australia/Sydney", year: "numeric", month: "2-digit" })
    .format(new Date()).slice(0, 7);
  if (root.dataset.month && root.dataset.month < now) {
    root.dataset.stale = "1";
    var status = document.querySelector("[data-status]");
    if (status) status.title = "A newer PBS schedule is out. Prices on pbs.gov.au may differ from those here.";
  }

  // ── Reveal on scroll ────────────────────────────────────────────────────────
  // Content already on screen is left alone; only what starts below the fold fades in.
  if (!reduceMotion && "IntersectionObserver" in window) {
    var seen = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.remove("pending"); seen.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    document.querySelectorAll(".reveal").forEach(function (el) {
      if (el.getBoundingClientRect().top < window.innerHeight) return;
      // Stagger siblings that enter together, such as bento tiles.
      var i = Array.prototype.indexOf.call(el.parentNode.children, el);
      el.style.setProperty("--delay", Math.min(i, 6) * 60 + "ms");
      el.classList.add("pending");
      seen.observe(el);
    });
  }

  // ── Search index ────────────────────────────────────────────────────────────
  var index = null, loading = null;
  function load() {
    if (index) return Promise.resolve(index);
    if (!loading) loading = fetch("/search-index.json").then(function (r) { return r.json(); })
      .then(function (d) { index = d; return d; });
    return loading;
  }

  function norm(s) { return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }

  function lev(a, b, max) {
    if (Math.abs(a.length - b.length) > max) return max + 1;
    var prev = [], i, j;
    for (j = 0; j <= b.length; j++) prev[j] = j;
    for (i = 1; i <= a.length; i++) {
      var cur = [i], best = i;
      for (j = 1; j <= b.length; j++) {
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
        if (cur[j] < best) best = cur[j];
      }
      if (best > max) return max + 1;
      prev = cur;
    }
    return prev[b.length];
  }

  function score(entry, q) {
    var name = norm(entry.n), best = 0, via = null;
    if (/^[0-9]{1,5}[a-z]$/.test(q) && entry.c.some(function (c) { return c.toLowerCase() === q; }))
      return { s: 1000, via: "code" };
    if (/^[0-9]{4,5}$/.test(q) && entry.a && entry.a[q])
      return { s: 900, via: "streamlined", item: entry.a[q] };
    if (name === q) best = 500;
    else if (name.indexOf(q) === 0) best = 300;
    else if (name.split(/[^a-z0-9]+/).some(function (w) { return w.indexOf(q) === 0; })) best = 200;
    else if (name.indexOf(q) > 0) best = 100;
    for (var i = 0; i < entry.b.length && best < 300; i++) {
      var b = norm(entry.b[i]);
      if (b === q || b.indexOf(q) === 0) { best = Math.max(best, 250); via = entry.b[i]; }
    }
    if (!best && q.length >= 4) {
      // Tolerate a typo or two, against the start of the name or of a brand.
      var max = q.length >= 7 ? 2 : 1;
      var cands = [name].concat(entry.b.map(norm));
      for (var k = 0; k < cands.length; k++) {
        if (lev(q, cands[k].slice(0, q.length), max) <= max) {
          best = 50; via = k ? entry.b[k - 1] : null; break;
        }
      }
    }
    return best ? { s: best - name.length / 100, via: via } : null;
  }

  function search(q) {
    q = norm(q.trim());
    if (!q) return [];
    var out = [];
    for (var i = 0; i < index.length; i++) {
      var r = score(index[i], q);
      if (r) out.push({ e: index[i], s: r.s, via: r.via,
        code: r.via === "code" ? q.toUpperCase() : r.via === "streamlined" ? r.item : null, streamlined: r.via === "streamlined" ? q : null });
    }
    out.sort(function (a, b) { return b.s - a.s; });
    return out.slice(0, 8);
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }

  function bindSearch(form) {
    var input = form.querySelector("input"), list = form.querySelector(".results"), hits = [], sel = -1;
    var inDialog = !!form.closest("dialog");
    var examples = [];
    try { examples = JSON.parse(form.dataset.examples || "[]"); } catch (e) {}

    function href(h) { return "/medicine/" + h.e.s + "/" + (h.code ? "#item-" + h.code : ""); }
    function row(h, i) {
      var sub = h.streamlined ? "Streamlined code " + h.streamlined + " · item " + h.code
        : h.code ? "Item " + h.code : h.via ? "Brand: " + h.via : h.e.k;
      return '<li role="option" aria-selected="' + (i === sel) + '"><a href="' + href(h) + '"><span>' +
        esc(h.e.n) + "</span>" + (sub ? "<small>" + esc(sub) + "</small>" : "") + "</a></li>";
    }
    function drawExamples() {
      if (!index || !examples.length) { list.hidden = true; return; }
      hits = examples.map(function (slug) {
        for (var i = 0; i < index.length; i++) if (index[i].s === slug) return { e: index[i] };
      }).filter(Boolean);
      sel = -1;
      list.innerHTML = '<li class="group">Common searches</li>' + hits.map(row).join("");
      list.hidden = false;
    }
    function draw() {
      if (!input.value.trim()) { drawExamples(); return; }
      list.innerHTML = hits.length ? hits.map(row).join("")
        : '<li class="empty">No PBS medicine matches “' + esc(input.value.trim()) + '”.</li>';
      list.hidden = false;
    }

    input.addEventListener("input", function () {
      if (!index) { list.innerHTML = '<li class="loading">Loading medicines…</li>'; list.hidden = false; }
      load().then(function () { hits = search(input.value); sel = hits.length ? 0 : -1; draw(); });
    });
    // Load early, but only open the list when the visitor reaches for the box.
    input.addEventListener("focus", load);
    input.addEventListener("click", function () {
      load().then(function () { if (!input.value.trim()) drawExamples(); });
    });
    input.addEventListener("keydown", function (ev) {
      if (list.hidden || !hits.length) return;
      if (ev.key === "ArrowDown") { sel = (sel + 1) % hits.length; draw(); ev.preventDefault(); }
      else if (ev.key === "ArrowUp") { sel = (sel - 1 + hits.length) % hits.length; draw(); ev.preventDefault(); }
      else if (ev.key === "Escape" && !inDialog) { list.hidden = true; }
    });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      load().then(function () {
        hits = search(input.value);
        if (hits.length) location.href = href(hits[Math.max(sel, 0)]); else draw();
      });
    });
    if (!inDialog) {
      document.addEventListener("click", function (ev) { if (!form.contains(ev.target)) list.hidden = true; });
      // /?q=term (where the form lands without JavaScript) opens with results showing.
      var q = new URLSearchParams(location.search).get("q");
      if (q && !input.value) {
        input.value = q;
        input.dispatchEvent(new Event("input"));
      }
    }

    return {
      open: function () {
        input.value = "";
        input.focus();
        load().then(drawExamples);
      },
    };
  }

  // ── My medicines: a list kept in this browser only ──────────────────────────
  var meds = {
    get: function () {
      try { return JSON.parse(localStorage.getItem("pbs.meds") || "[]"); } catch (e) { return memory; }
    },
    set: function (list) {
      memory = list;
      try { localStorage.setItem("pbs.meds", JSON.stringify(list)); } catch (e) {}
      paintCount();
    },
    has: function (code) { return meds.get().some(function (m) { return m.code === code; }); },
    // n is scripts a year: 12 for a monthly 30-day script, 6 for a 60-day one.
    add: function (code, n) { if (!meds.has(code)) meds.set(meds.get().concat([{ code: code, brand: "", n: n || 12 }])); },
  };
  var memory = [];
  function paintCount() {
    var n = meds.get().length;
    document.querySelectorAll("[data-med-count]").forEach(function (el) {
      el.textContent = n ? String(n) : "";
      el.hidden = !n;
    });
  }
  paintCount();

  var toast = null, toastTimer = null;
  function showToast(html) {
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "toast";
      toast.setAttribute("role", "status");
      document.body.appendChild(toast);
    }
    toast.innerHTML = html;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove("show"); }, 4000);
  }

  // "Add to my medicines" buttons. The target is read on click, not bound once, because
  // the medicine page's bottom bar re-points its button at whichever listing is chosen.
  function paintAdd() {
    document.querySelectorAll("[data-add-med]").forEach(function (b) {
      var added = meds.has(b.dataset.addMed);
      b.classList.toggle("added", added);
      var label = b.querySelector("span");
      if (label) label.textContent = added ? "In my medicines" : (b.dataset.short ? "Add" : "Add to my medicines");
    });
  }
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-add-med]");
    if (!b) return;
    var code = b.dataset.addMed;
    if (meds.has(code)) { location.href = "/my-medicines/"; return; }
    meds.add(code, parseInt(b.dataset.n || "12", 10));
    paintAdd();
    showToast("Added " + esc(b.dataset.label || code) + ' <a href="/my-medicines/">View my medicines</a>');
  });
  paintAdd();

  // The planner reuses the search and the list.
  window.PBS = { load: load, search: search, esc: esc, meds: meds, paintAdd: paintAdd, toast: showToast };

  var palette = document.querySelector("[data-palette]"), paletteSearch = null;
  document.querySelectorAll("[data-search]").forEach(function (form) {
    var s = bindSearch(form);
    if (palette && palette.contains(form)) paletteSearch = s;
  });

  // ── Command palette: ⌘K, Ctrl+K or "/" from anywhere ────────────────────────
  function openPalette() {
    var hero = document.querySelector(".search-hero input");
    if (hero) { hero.focus(); hero.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" }); return; }
    if (!palette || palette.open || typeof palette.showModal !== "function") return;
    palette.showModal();
    if (paletteSearch) paletteSearch.open();
  }
  document.querySelectorAll("[data-open-search]").forEach(function (el) {
    el.addEventListener("click", function (ev) {
      if (palette && typeof palette.showModal === "function") { ev.preventDefault(); openPalette(); }
    });
  });
  document.addEventListener("keydown", function (ev) {
    var typing = /INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || "");
    if ((ev.key === "k" && (ev.metaKey || ev.ctrlKey)) || (ev.key === "/" && !typing)) {
      ev.preventDefault();
      openPalette();
    }
  });
  if (palette) {
    palette.addEventListener("click", function (ev) { if (ev.target === palette) palette.close(); });
  }
  // Show Ctrl rather than ⌘ outside Apple platforms.
  if (!/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) {
    document.querySelectorAll(".search-trigger kbd").forEach(function (k) { k.textContent = "Ctrl K"; });
  }
})();
