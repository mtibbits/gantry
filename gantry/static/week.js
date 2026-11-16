"use strict";
// The week view's own script (#38): it only shows or hides. At load it shows
// the controls and hides days 6 to 10 and each beyond-the-horizon list, so a
// page with scripts off shows everything. It reads no data, builds no markup
// and sets no class from a record. One listener, delegated on document.
(function () {
  const all = (selector) => Array.from(document.querySelectorAll(selector));
  const setDays = (ten) => {
    all(".day.later").forEach((day) => day.classList.toggle("gone", !ten));
    all('button[data-control="days"]').forEach((b) => b.setAttribute("aria-pressed", String((b.getAttribute("data-value") === "10") === ten)));
  };
  all("[data-control]").forEach((el) => el.classList.remove("gone"));
  all(".beyond-list").forEach((list) => list.classList.add("gone"));
  setDays(false);
  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("button[data-control]") : null;
    if (button === null) return;
    const name = button.getAttribute("data-control");
    if (name === "days") {
      setDays(button.getAttribute("data-value") === "10");
    } else if (name.startsWith("beyond-")) {
      const list = document.getElementById(name);
      if (list === null || !list.classList.contains("beyond-list")) return;
      const hidden = list.classList.toggle("gone");
      button.setAttribute("aria-expanded", String(!hidden));
    }
  });
})();
