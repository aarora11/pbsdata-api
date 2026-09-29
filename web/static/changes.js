// Changes page: filter by kind of change, or to the medicines in "My medicines".
(function () {
  "use strict";
  var root = document.querySelector("[data-changes]");
  if (!root) return;
  var kinds = root.querySelectorAll("[data-kind]"), mine = root.querySelector("[data-mine]");
  function read() { try { return localStorage.getItem("pbs.changes.mine") === "1"; } catch (e) { return false; } }
  function write(v) { try { localStorage.setItem("pbs.changes.mine", v ? "1" : "0"); } catch (e) {} }
  mine.checked = read();

  function myCodes() {
    var list = window.PBS ? window.PBS.meds.get() : [];
    var set = {};
    list.forEach(function (m) { set[m.code] = true; });
    return { set: set, n: list.length };
  }

  function apply() {
    var on = {}, mc = myCodes(), onlyMine = mine.checked, anyVisible = false;
    kinds.forEach(function (k) { on[k.dataset.kind] = k.checked; });
    root.querySelector("[data-mine-hint]").hidden = !(onlyMine && !mc.n);
    root.querySelectorAll("[data-group]").forEach(function (g) {
      var kind = g.dataset.group, shown = 0;
      g.querySelectorAll("li[data-codes]").forEach(function (li) {
        var match = !onlyMine || li.dataset.codes.split(" ").some(function (c) { return mc.set[c]; });
        li.hidden = !match;
        if (match) shown++;
      });
      g.hidden = !on[kind] || shown === 0;
      g.querySelector("[data-group-count]").textContent = String(shown);
      // A filtered list is short: open it rather than hide it behind "Show all".
      var more = g.querySelector("[data-more]");
      if (more) {
        if (onlyMine) more.open = true;
        more.querySelector("summary").hidden = onlyMine;
      }
      var count = root.querySelector('[data-kind-count="' + kind + '"]');
      if (count) count.textContent = String(shown);
      if (!g.hidden && getComputedStyle(g).display !== "none") anyVisible = true;
    });
    root.querySelector("[data-none]").hidden = !(onlyMine && mc.n && !anyVisible);
    root.querySelector("[data-mine-count]").textContent = mc.n ? String(mc.n) : "";
  }
  kinds.forEach(function (k) { k.addEventListener("change", apply); });
  mine.addEventListener("change", function () { write(mine.checked); apply(); });
  apply();
})();
