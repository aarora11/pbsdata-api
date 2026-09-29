// Medicine page: pick your exact prescription, and the page describes that one listing.
//
// Every listing is already rendered server-side (so the page works, and is indexed,
// without this script). This only chooses which listing is visible, keeps the choice in
// the URL (#item-CODE) so it can be shared, and drives the rails and the mobile bar.
(function () {
  "use strict";
  var root = document.querySelector("[data-med]");
  if (!root) return;
  var data = JSON.parse(root.querySelector("[data-picker-data]").textContent);
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var picker = root.querySelector("[data-picker]");
  var esc = (window.PBS && window.PBS.esc) || function (s) { return String(s); };

  var byCode = {};
  data.forms.forEach(function (f, fi) {
    f.items.forEach(function (it) { it.form = fi; byCode[it.code] = it; });
  });
  var total = Object.keys(byCode).length;
  var current = null;

  root.classList.add("picking");

  function cents(price) { return price ? Math.round(parseFloat(price.replace(/[$,]/g, "")) * 100) : null; }

  function fromTarget() {
    var h = decodeURIComponent(location.hash.slice(1));
    var m = h.match(/^item-(.+)$/);
    if (m && byCode[m[1]]) return m[1];
    var f = h.match(/^form-(\d+)$/);
    if (f && data.forms[+f[1] - 1]) return data.forms[+f[1] - 1].items[0].code;
    return null;
  }

  // With no link to a particular listing, start on the cheapest one a community
  // pharmacy prices: the most likely answer for a patient, and never a hospital-only one.
  function initial() {
    var best = null;
    Object.keys(byCode).forEach(function (code) {
      var it = byCode[code], c = cents(it.price);
      if (it.retail && c !== null && (!best || c < best.c)) best = { code: code, c: c };
    });
    return best ? best.code : data.forms[0].items[0].code;
  }

  // ── The picker ──────────────────────────────────────────────────────────────
  function fromPrice(f) {
    var prices = f.items.map(function (it) { return cents(it.price); }).filter(function (c) { return c !== null; });
    if (!prices.length) return "No pharmacy price";
    var lo = Math.min.apply(null, prices);
    var label = "$" + (lo / 100).toFixed(2);
    return Math.max.apply(null, prices) > lo ? "from " + label : label;
  }

  function group(label, name, opts, selected) {
    if (opts.length > 6) {
      return '<label class="pk-group"><span class="pk-label">' + label + '</span><select data-pk="' + name + '">' +
        opts.map(function (o) {
          return '<option value="' + esc(o.value) + '"' + (o.value === selected ? " selected" : "") + ">" +
            esc(o.label) + (o.sub ? " (" + esc(o.sub) + ")" : "") + "</option>";
        }).join("") + "</select></label>";
    }
    return '<div class="pk-group" role="radiogroup" aria-label="' + label + '"><span class="pk-label">' + label + '</span><div class="pk-opts">' +
      opts.map(function (o) {
        var on = o.value === selected;
        return '<button type="button" role="radio" aria-checked="' + on + '" tabindex="' + (on ? 0 : -1) + '" data-pk="' + name +
          '" value="' + esc(o.value) + '"><span>' + esc(o.label) + "</span>" + (o.sub ? "<small>" + esc(o.sub) + "</small>" : "") + "</button>";
      }).join("") + "</div></div>";
  }

  function renderPicker() {
    var it = byCode[current], f = data.forms[it.form], html = "";
    if (data.forms.length > 1) {
      html += group("Form and strength", "form", data.forms.map(function (ff, i) {
        return { value: "f" + i, label: ff.label + (ff.program ? " (" + ff.program + ")" : ""), sub: fromPrice(ff) };
      }), "f" + it.form);
    }
    if (f.items.length > 1) {
      html += group("Which listing is yours?", "item", f.items.map(function (x) {
        return { value: x.code, label: x.label || "Item " + x.code, sub: x.price || "" };
      }), current);
    }
    picker.innerHTML = html;
    picker.hidden = !html;
  }

  function choose(name, value) {
    if (name === "form") select(data.forms[+value.slice(1)].items[0].code, true);
    else select(value, true);
  }
  picker.addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-pk]");
    if (b) { choose(b.dataset.pk, b.value); focusChecked(b.dataset.pk); }
  });
  picker.addEventListener("change", function (ev) {
    if (ev.target.dataset.pk) choose(ev.target.dataset.pk, ev.target.value);
  });
  // Arrow keys move within a radio group, as native radios do.
  picker.addEventListener("keydown", function (ev) {
    var b = ev.target.closest("button[data-pk]");
    if (!b || ["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].indexOf(ev.key) < 0) return;
    var all = Array.prototype.slice.call(b.parentNode.querySelectorAll("button"));
    var i = all.indexOf(b) + (ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : -1);
    var next = all[(i + all.length) % all.length];
    ev.preventDefault();
    choose(next.dataset.pk, next.value);
    focusChecked(next.dataset.pk);
  });
  function focusChecked(name) {
    var b = picker.querySelector('button[data-pk="' + name + '"][aria-checked="true"]');
    if (b) b.focus();
  }

  // ── Selecting a listing ─────────────────────────────────────────────────────
  function select(code, fromUser) {
    current = code;
    root.querySelectorAll("[data-item]").forEach(function (el) { el.classList.toggle("on", el.dataset.item === code); });
    root.querySelectorAll("[data-select]").forEach(function (tr) {
      tr.setAttribute("aria-selected", tr.dataset.select === code ? "true" : "false");
    });
    renderPicker();
    updateBar();
    // replaceState: choosing strengths shouldn't fill the back button with history.
    if (fromUser && history.replaceState) history.replaceState(null, "", "#item-" + code);
  }

  var bar = root.querySelector("[data-bottom-bar]");
  function updateBar() {
    var it = byCode[current], f = data.forms[it.form];
    bar.querySelector("[data-bb-price]").textContent = it.price ? it.price : "No pharmacy price";
    bar.querySelector("[data-bb-label]").textContent = "Most you'll pay · " + f.label + (it.label ? " · " + it.label : "");
    var add = bar.querySelector("[data-add-med]");
    add.hidden = !it.retail;
    add.dataset.addMed = it.code;
    add.dataset.n = it.sixty ? "6" : "12";
    add.dataset.label = document.querySelector("h1").textContent + " " + f.label;
    bar.hidden = false;
    if (window.PBS) window.PBS.paintAdd();
  }

  // Pharmacist table: a row selects its listing.
  root.querySelectorAll("[data-select]").forEach(function (tr) {
    function go() {
      select(tr.dataset.select, true);
      document.getElementById("prescription").scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
    }
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } });
  });

  // ── Share and print ─────────────────────────────────────────────────────────
  root.addEventListener("click", function (ev) {
    if (ev.target.closest("[data-print]")) { window.print(); return; }
    if (!ev.target.closest("[data-share]")) return;
    var url = location.href, title = document.title;
    if (navigator.share) { navigator.share({ title: title, url: url }).catch(function () {}); return; }
    if (navigator.clipboard) {
      navigator.clipboard.writeText(url).then(function () {
        if (window.PBS) window.PBS.toast("Link copied. It opens this exact listing.");
      });
    }
  });

  // ── On this page: highlight the section in view ─────────────────────────────
  var links = root.querySelectorAll("[data-spy-link]");
  if (links.length && "IntersectionObserver" in window) {
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        links.forEach(function (a) { a.classList.toggle("active", a.getAttribute("href") === "#" + e.target.id); });
      });
    }, { rootMargin: "-25% 0px -65% 0px" });
    root.querySelectorAll("[data-spy]").forEach(function (s) { spy.observe(s); });
  }

  window.addEventListener("hashchange", function () { var c = fromTarget(); if (c) select(c, false); });
  select(fromTarget() || initial(), false);
  root.dataset.listings = String(total);
})();
