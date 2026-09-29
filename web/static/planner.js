// "My medicines": an estimate of a year's PBS costs and when the Safety Net applies.
//
// Every figure comes from /planner.json, which the build writes from published PBS fields
// (see planner_data in web/build.py). Nothing is priced here that PBS doesn't publish:
// this file only schedules scripts across the year and adds them up.
//
// The rules, from the PBS data dictionary and Services Australia:
//   - Each script records max_record_val_for_safety_net (general) or the concessional
//     charge (concession) towards the threshold. Brand premiums never count.
//   - Once the family total reaches the threshold, general patients pay the concession
//     price and concession card holders pay nothing, for the rest of the calendar year.
//     Brand premiums are still paid.
(function () {
  "use strict";
  var root = document.querySelector("[data-planner]");
  if (!root || !window.PBS) return;
  var PBS = window.PBS, esc = PBS.esc;

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"];
  var FREQ = [[12, "Every month"], [13, "Every 4 weeks"], [24, "Twice a month"], [6, "Every 2 months (60-day script)"],
    [4, "Every 3 months"], [2, "Twice a year"], [1, "Once a year"]];

  function read(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function write(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  // Takes cents.
  function money(v) {
    v = v / 100;
    return "$" + v.toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function $(sel) { return root.querySelector(sel); }

  var data = null;
  var state = {
    patient: read("pbs.patient") === "concession" ? "concession" : "general",
    start: Math.min(11, Math.max(0, parseInt(read("pbs.start") || "0", 10) || 0)),
    recorded: Math.max(0, parseFloat(read("pbs.recorded") || "0") || 0),
  };

  // ── Pricing ─────────────────────────────────────────────────────────────────
  // All sums are in whole cents. In dollars, 36 x 7.70 adds up to 277.19999..., which falls
  // short of the 277.20 concession threshold and charges a script that should be free.
  function cents(v) { return Math.round(v * 100); }

  function premiumOf(item, brand) {
    if (!brand) return 0;
    for (var i = 0; i < item.b.length; i++) if (item.b[i][0] === brand) return item.b[i][1];
    return 0;
  }
  function price(item, brand, conc, afterSafetyNet) {
    var prem = premiumOf(item, brand), base;
    if (conc) base = afterSafetyNet ? 0 : item.c;
    else base = afterSafetyNet ? item.c : item.g;
    return {
      pay: cents(base + prem),
      // What the pharmacy records towards the Safety Net: never the premium.
      record: cents(conc ? Math.min(item.c, item.r) : item.r),
    };
  }

  // ── 60-day scripts ──────────────────────────────────────────────────────────
  // A 30-day listing with a 60-day twin can be planned as the twin at half the scripts.
  function switched(m) {
    var item = data.items[m.code];
    if (!item || !item.p || item.d || !data.items[item.p]) return null;
    var twin = data.items[item.p];
    var brand = twin.b.some(function (b) { return b[0] === m.brand; }) ? m.brand : "";
    return { code: item.p, brand: brand, n: Math.max(1, Math.round(m.n / 2)) };
  }

  // ── The year ────────────────────────────────────────────────────────────────
  function simulate(list) {
    var conc = state.patient === "concession";
    var threshold = cents(data.threshold[conc ? 1 : 0]);
    var year = parseInt(data.month.slice(0, 4), 10);
    var scripts = [];
    list.forEach(function (m) {
      var item = data.items[m.code];
      if (!item) return;
      for (var k = 0; k < m.n; k++) {
        // Spread each medicine's scripts evenly across the calendar year.
        var day = Math.floor((k + 0.5) * 365 / m.n);
        var month = new Date(year, 0, 1 + day).getMonth();
        if (month < state.start) continue;
        scripts.push({ day: day, month: month, item: item, brand: m.brand });
      }
    });
    scripts.sort(function (a, b) { return a.day - b.day; });

    var out = { monthly: [], counts: [], recorded: [], total: 0, totalWithout: 0, reached: null,
      threshold: threshold, cum: cents(state.recorded), scripts: scripts.length, conc: conc };
    for (var i = 0; i < 12; i++) { out.monthly.push(0); out.counts.push(0); out.recorded.push(0); }
    if (out.cum >= threshold) out.reached = { month: state.start, already: true };

    scripts.forEach(function (s) {
      var after = out.cum >= threshold;
      var p = price(s.item, s.brand, conc, after);
      out.monthly[s.month] += p.pay;
      out.counts[s.month] += 1;
      out.total += p.pay;
      out.totalWithout += price(s.item, s.brand, conc, false).pay;
      if (!after) {
        out.cum += p.record;
        out.recorded[s.month] += p.record;
        if (out.cum >= threshold && !out.reached) out.reached = { month: s.month, already: false };
      }
    });
    return out;
  }

  // ── Rendering ───────────────────────────────────────────────────────────────
  function render() {
    var list = PBS.meds.get();
    renderList(list);
    var known = list.filter(function (m) { return data.items[m.code]; });
    $("[data-empty]").hidden = list.length > 0;
    $("[data-summary]").hidden = known.length === 0;
    $("[data-year]").hidden = known.length === 0;
    root.classList.toggle("has-meds", known.length > 0);
    if (!known.length) return;

    var r = simulate(known);
    var conc = r.conc;
    $("[data-total]").textContent = money(r.total);
    var saved = r.totalWithout - r.total;
    $("[data-total-sub]").textContent = saved > 0
      ? "The Safety Net saves you about " + money(saved) + "."
      : "At this rate you won't reach the Safety Net this year.";

    var switchable = known.filter(function (m) { return switched(m); });
    var tip = $("[data-sixty-tip]");
    if (switchable.length) {
      var alt = simulate(known.map(function (m) { return switched(m) || m; }));
      var save = r.total - alt.total;
      tip.hidden = save <= 0;
      tip.textContent = "60-day scripts could save about " + money(save) + " a year on " + switchable.length +
        " of these medicines, after the Safety Net. Ask your doctor whether they suit you.";
    } else {
      tip.hidden = true;
    }

    var pct = Math.min(1, r.cum / r.threshold);
    $("[data-meter-fill]").style.width = (pct * 100).toFixed(1) + "%";
    $("[data-meter]").setAttribute("aria-label", Math.round(pct * 100) + "% of the Safety Net threshold");
    if (r.reached) {
      $("[data-sn-status]").textContent = r.reached.already ? "Already reached" : "Reached in " + MONTHS_LONG[r.reached.month];
      $("[data-sn-hint]").textContent = "After that, " + (conc
        ? "your scripts are free for the rest of the year, apart from any brand premium."
        : "you pay the concession price (usually " + money(cents(data.copay[1])) + ") for the rest of the year, plus any brand premium.");
    } else {
      $("[data-sn-status]").textContent = money(r.cum) + " of " + money(r.threshold);
      $("[data-sn-hint]").textContent = "You'd need another " + money(r.threshold - r.cum) +
        " recorded this year to reach it. Your whole family's scripts count together.";
    }

    var perScript = known.reduce(function (sum, m) {
      return sum + price(data.items[m.code], m.brand, conc, false).pay;
    }, 0);
    $("[data-per-script]").textContent = money(perScript) + " for one of each";
    $("[data-scripts]").textContent = r.scripts + (state.start ? " from " + MONTHS_LONG[state.start] : "");
    $("[data-year-sub]").textContent = "Assumes each script is filled evenly across the year" +
      (state.start ? ", starting in " + MONTHS_LONG[state.start] : "") + ".";

    $("[data-takeaway]").textContent = takeaway(r);
    renderChart(r);
    renderTable(r);
  }

  // The chart's point in one sentence, so nobody has to read bars to get it.
  function takeaway(r) {
    var after = r.conc ? "free, apart from any brand premium" : "the concession price";
    if (r.reached && r.reached.already) return "You've already reached the Safety Net, so your scripts are " + after + " for the rest of the year.";
    if (r.reached && r.reached.month >= 11) return "You reach the Safety Net in December, so it barely lowers this year's costs.";
    if (r.reached) {
      return "Your costs drop from " + MONTHS_LONG[r.reached.month + 1] + ", after you reach the Safety Net in " +
        MONTHS_LONG[r.reached.month] + ". From then on your scripts are " + after + ".";
    }
    var months = 12 - state.start;
    return "At most about " + money(Math.round(r.total / months)) + " a month. You won't reach the Safety Net this year.";
  }

  function renderList(list) {
    var ul = $("[data-med-list]");
    var conc = state.patient === "concession";
    ul.innerHTML = list.map(function (m, i) {
      var item = data.items[m.code];
      if (!item) {
        return '<li class="med-card med-gone"><div class="med-card-head"><div><strong>Item ' + esc(m.code) +
          '</strong><span>Not on the PBS in the ' + esc(data.label) + ' schedule.</span></div>' +
          '<button type="button" class="icon-x" data-remove="' + i + '" aria-label="Remove">×</button></div></li>';
      }
      var brands = '<option value="">' + (item.b.length ? "Any brand without a premium" : "Any brand") + "</option>" +
        item.b.map(function (b) {
          return '<option value="' + esc(b[0]) + '"' + (m.brand === b[0] ? " selected" : "") + ">" +
            esc(b[0]) + " (+" + money(cents(b[1])) + ")</option>";
        }).join("");
      var freq = FREQ.map(function (f) {
        return '<option value="' + f[0] + '"' + (m.n === f[0] ? " selected" : "") + ">" + f[1] + "</option>";
      }).join("");
      var p = price(item, m.brand, conc, false);
      var sw = switched(m), sixty = "";
      if (sw) {
        var saving = p.pay * m.n - price(data.items[sw.code], sw.brand, conc, false).pay * sw.n;
        if (saving > 0) {
          sixty = '<div class="med-sixty"><span><strong>60-day script available.</strong> About ' + money(saving) +
            ' less a year for this medicine. Ask your doctor whether it suits you.</span>' +
            '<button type="button" class="btn btn-soft" data-switch="' + i + '">Plan with 60-day</button></div>';
        }
      } else if (item.d && m.n > 6) {
        // Filled more often than every two months, a 60-day script would be more than a
        // year's supply: almost certainly the frequency, not the medicine, is wrong.
        sixty = '<div class="med-sixty"><span><strong>This is a 60-day script.</strong> It covers two months, ' +
          "so it's usually filled 6 times a year, not " + m.n + ".</span>" +
          '<button type="button" class="btn btn-soft" data-every-two="' + i + '">Set to every 2 months</button></div>';
      } else if (item.d) {
        sixty = '<div class="med-sixty med-sixty-on"><span><strong>60-day script.</strong> Two months per script.</span></div>';
      }
      return '<li class="med-card">' +
        '<div class="med-card-head"><div>' +
          '<a href="/medicine/' + esc(item.s) + '/#item-' + esc(m.code) + '">' + esc(item.n) + "</a>" +
          "<span>" + esc(item.f || "") + (item.v ? " · " + esc(item.v) : "") + "</span></div>" +
          '<div class="med-card-price"><strong>' + money(p.pay) + "</strong><span>per script</span></div>" +
          '<button type="button" class="icon-x" data-remove="' + i + '" aria-label="Remove ' + esc(item.n) + '">×</button>' +
        "</div>" +
        '<div class="med-card-controls">' +
          '<label><span>Brand</span><select data-brand="' + i + '"' + (item.b.length ? "" : " disabled") + ">" + brands + "</select></label>" +
          '<label><span>How often</span><select data-freq="' + i + '">' + freq + "</select></label>" +
        "</div>" + sixty + "</li>";
    }).join("");
  }

  function niceMax(v) {
    if (v <= 0) return 10;
    var steps = [10, 20, 25, 50, 100, 200, 250, 500, 1000];
    for (var i = 0; i < steps.length; i++) if (v <= steps[i]) return steps[i];
    return Math.ceil(v / 500) * 500;
  }

  function renderChart(r) {
    var max = niceMax(Math.max.apply(null, r.monthly) / 100) * 100;
    var peak = r.monthly.indexOf(Math.max.apply(null, r.monthly));
    var html = '<div class="chart-plot">';
    [1, 0.5, 0].forEach(function (f) {
      html += '<div class="gridline" style="bottom:' + f * 100 + '%"><span>' + money(max * f).replace(".00", "") + "</span></div>";
    });
    html += '<div class="cols">';
    for (var m = 0; m < 12; m++) {
      var h = r.monthly[m] / max * 100;
      var off = m < state.start;
      var isReach = r.reached && !r.reached.already && r.reached.month === m;
      var label = MONTHS_LONG[m] + ": " + (off ? "not counted" : money(r.monthly[m]) + ", " + r.counts[m] + " script" + (r.counts[m] === 1 ? "" : "s")) +
        (isReach ? ". Safety Net reached this month." : "");
      html += '<div class="col' + (off ? " off" : "") + (r.reached && m > r.reached.month ? " after" : "") + '" tabindex="0" role="img" aria-label="' + esc(label) + '" data-m="' + m + '">' +
        (isReach ? '<span class="reach"><span>Safety Net reached</span></span>' : "") +
        (m === peak && r.monthly[m] > 0 ? '<span class="val" style="bottom:calc(' + h + '% + 6px)">' + money(r.monthly[m]) + "</span>" : "") +
        '<span class="bar" style="height:' + h + '%"></span>' +
        "</div>";
    }
    html += "</div></div>";
    html += '<div class="xlabels">' + MONTHS.map(function (mo, i) {
      return "<span" + (i < state.start ? ' class="off"' : "") + ">" + mo + "</span>";
    }).join("") + "</div>";
    html += '<div class="tip" role="tooltip" hidden></div>';
    var chart = $("[data-chart]");
    chart.innerHTML = html;

    // One tooltip, on hover and on keyboard focus. Built with textContent.
    var tip = chart.querySelector(".tip");
    function show(col) {
      var m = +col.dataset.m;
      tip.textContent = "";
      var v = document.createElement("strong");
      v.textContent = m < state.start ? "Not counted" : money(r.monthly[m]);
      var l = document.createElement("span");
      l.textContent = MONTHS_LONG[m] + (m < state.start ? "" : " · " + r.counts[m] + " script" + (r.counts[m] === 1 ? "" : "s"));
      tip.appendChild(v); tip.appendChild(l);
      var cb = chart.getBoundingClientRect(), b = col.getBoundingClientRect();
      tip.hidden = false;
      var x = b.left - cb.left + b.width / 2 - tip.offsetWidth / 2;
      tip.style.left = Math.max(0, Math.min(x, cb.width - tip.offsetWidth)) + "px";
    }
    chart.querySelectorAll(".col").forEach(function (col) {
      col.addEventListener("pointerenter", function () { show(col); });
      col.addEventListener("focus", function () { show(col); });
      col.addEventListener("pointerleave", function () { tip.hidden = true; });
      col.addEventListener("blur", function () { tip.hidden = true; });
    });
  }

  function renderTable(r) {
    var rows = "";
    for (var m = 0; m < 12; m++) {
      if (m < state.start) continue;
      rows += "<tr><td>" + MONTHS_LONG[m] + (r.reached && !r.reached.already && r.reached.month === m ? " (Safety Net reached)" : "") +
        '</td><td class="n">' + r.counts[m] + '</td><td class="n">' + money(r.monthly[m]) +
        '</td><td class="n">' + money(r.recorded[m]) + "</td></tr>";
    }
    rows += '<tr class="total"><td>Total</td><td class="n">' + r.scripts + '</td><td class="n">' + money(r.total) +
      '</td><td class="n">' + money(r.cum - cents(state.recorded)) + "</td></tr>";
    $("[data-table]").innerHTML = rows;
  }

  // ── Adding medicines ────────────────────────────────────────────────────────
  var form = $("[data-planner-search]"), input = form.querySelector("input"), results = form.querySelector(".results");
  var pick = $("[data-pick]"), hits = [];

  function plannable(entry) { return entry.c.filter(function (c) { return data.items[c]; }); }

  input.addEventListener("input", function () {
    PBS.load().then(function () {
      var q = input.value.trim();
      pick.hidden = true;
      if (!q) { results.hidden = true; return; }
      hits = PBS.search(q).filter(function (h) { return plannable(h.e).length; });
      results.innerHTML = hits.length ? hits.map(function (h, i) {
        return '<li role="option"><a href="#" data-hit="' + i + '"><span>' + esc(h.e.n) + "</span>" +
          (h.streamlined ? "<small>Streamlined code " + esc(h.streamlined) + "</small>"
            : h.via && h.via !== "code" ? "<small>Brand: " + esc(h.via) + "</small>" : "<small>" + esc(h.e.k || "") + "</small>") + "</a></li>";
      }).join("") : '<li class="empty">No PBS medicine sold in pharmacies matches that.</li>';
      results.hidden = false;
    });
  });
  form.addEventListener("submit", function (ev) { ev.preventDefault(); if (hits.length) choose(hits[0]); });
  results.addEventListener("click", function (ev) {
    var a = ev.target.closest("[data-hit]");
    if (!a) return;
    ev.preventDefault();
    choose(hits[+a.dataset.hit]);
  });
  document.addEventListener("click", function (ev) { if (!form.contains(ev.target)) results.hidden = true; });

  function choose(hit) {
    results.hidden = true;
    var codes = plannable(hit.e);
    if (hit.code && data.items[hit.code]) codes = [hit.code];
    if (codes.length === 1) { add(codes[0]); return; }
    var conc = state.patient === "concession";
    pick.innerHTML = '<div class="pick-head"><strong>' + esc(hit.e.n) + "</strong><span>Which one is on your box?</span>" +
      '<button type="button" class="icon-x" data-pick-close aria-label="Close">×</button></div><div class="pick-list">' +
      codes.map(function (c) {
        var it = data.items[c];
        return '<button type="button" data-pick-code="' + esc(c) + '"><span><strong>' + esc(it.f || c) + "</strong>" +
          (it.v ? "<small>" + esc(it.v) + "</small>" : "") + "</span><em>" + money(price(it, "", conc, false).pay) + "</em></button>";
      }).join("") + "</div>";
    pick.hidden = false;
  }
  pick.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-pick-code]");
    if (b) add(b.dataset.pickCode);
    if (b || ev.target.closest("[data-pick-close]")) pick.hidden = true;
  });

  function add(code) {
    PBS.meds.add(code, data.items[code] && data.items[code].d ? 6 : 12);
    input.value = "";
    pick.hidden = true;
    render();
  }

  // ── Editing the list ────────────────────────────────────────────────────────
  var list = $("[data-med-list]");
  list.addEventListener("change", function (ev) {
    var meds = PBS.meds.get(), t = ev.target;
    if (t.dataset.brand !== undefined) meds[+t.dataset.brand].brand = t.value;
    if (t.dataset.freq !== undefined) meds[+t.dataset.freq].n = parseInt(t.value, 10);
    PBS.meds.set(meds);
    render();
  });
  list.addEventListener("click", function (ev) {
    var two = ev.target.closest("[data-every-two]");
    if (two) {
      var list2 = PBS.meds.get();
      list2[+two.dataset.everyTwo].n = 6;
      PBS.meds.set(list2);
      render();
      return;
    }
    var sw = ev.target.closest("[data-switch]");
    if (sw) {
      var all = PBS.meds.get(), i = +sw.dataset.switch, next = switched(all[i]);
      if (next) { all[i] = next; PBS.meds.set(all); render(); }
      return;
    }
    var b = ev.target.closest("[data-remove]");
    if (!b) return;
    var meds = PBS.meds.get();
    meds.splice(+b.dataset.remove, 1);
    PBS.meds.set(meds);
    render();
  });

  // ── Settings ────────────────────────────────────────────────────────────────
  function paintPatient() {
    root.querySelectorAll("[data-patient]").forEach(function (b) {
      b.setAttribute("aria-pressed", b.dataset.patient === state.patient ? "true" : "false");
    });
  }
  root.querySelectorAll("[data-patient]").forEach(function (b) {
    b.addEventListener("click", function () {
      state.patient = b.dataset.patient;
      write("pbs.patient", state.patient);
      paintPatient();
      render();
    });
  });
  var start = $("[data-start]"), recorded = $("[data-recorded]");
  start.value = String(state.start);
  recorded.value = state.recorded ? String(state.recorded) : "0";
  start.addEventListener("change", function () { state.start = +start.value; write("pbs.start", start.value); render(); });
  recorded.addEventListener("input", function () {
    state.recorded = Math.max(0, parseFloat(recorded.value) || 0);
    write("pbs.recorded", String(state.recorded));
    render();
  });
  paintPatient();

  fetch("/planner.json").then(function (r) { return r.json(); }).then(function (d) {
    data = d;
    render();
    var q = new URLSearchParams(location.search).get("add");
    if (q && data.items[q]) add(q);
  });
})();
