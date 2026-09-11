/* =========================================================================
   queue.js — auto-refresh the admin live-queue view periodically
   ========================================================================= */

(function () {
  "use strict";

  function initAutoRefresh() {
    const wrapper = document.querySelector("[data-live-queue]");
    if (!wrapper) return;

    const intervalSeconds = parseInt(wrapper.getAttribute("data-refresh-seconds") || "20", 10);

    setTimeout(() => {
      window.location.reload();
    }, intervalSeconds * 1000);
  }

  function initCountdownBadge() {
    const badge = document.querySelector("[data-refresh-countdown]");
    if (!badge) return;

    let seconds = parseInt(badge.getAttribute("data-refresh-countdown"), 10) || 20;
    const tick = () => {
      badge.textContent = `Refreshing in ${seconds}s`;
      seconds -= 1;
      if (seconds >= 0) setTimeout(tick, 1000);
    };
    tick();
  }

  document.addEventListener("DOMContentLoaded", () => {
    initAutoRefresh();
    initCountdownBadge();
  });
})();
