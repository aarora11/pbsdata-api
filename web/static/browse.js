// Browse pages: highlight the group in view in the left rail.
(function () {
  "use strict";
  var links = document.querySelectorAll("[data-spy-link]");
  if (!links.length || !("IntersectionObserver" in window)) return;
  var spy = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      links.forEach(function (a) { a.classList.toggle("active", a.getAttribute("href") === "#" + e.target.id); });
    });
  }, { rootMargin: "-20% 0px -70% 0px" });
  document.querySelectorAll("[data-spy]").forEach(function (s) { spy.observe(s); });
})();
