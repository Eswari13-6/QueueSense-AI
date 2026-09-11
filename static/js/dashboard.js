/* =========================================================================
   dashboard.js — user dashboard & token-tracking live behaviour
   ========================================================================= */

(function () {
  "use strict";

  /**
   * Poll a token's live status endpoint and update the DOM in place.
   * Expects elements with data-field="people_ahead|estimated_wait_minutes|
   * crowd_level|status" inside a container with [data-token-id].
   */
  function initLiveTracking() {
    const containers = document.querySelectorAll("[data-token-id]");
    if (!containers.length) return;

    containers.forEach((container) => {
      const tokenId = container.getAttribute("data-token-id");
      const poll = () => refreshToken(tokenId, container);
      poll();
      setInterval(poll, 8000); // refresh every 8 seconds
    });
  }

  function refreshToken(tokenId, container) {
    fetch(`/user/track/${tokenId}/status`)
      .then((res) => {
        if (!res.ok) throw new Error("Network response was not ok");
        return res.json();
      })
      .then((data) => {
        if (data.error) return;

        setField(container, "people_ahead", data.people_ahead);
        setField(container, "estimated_wait_minutes", `${data.estimated_wait_minutes} min`);
        setField(container, "crowd_level", data.crowd_level);
        setField(container, "status", data.status);

        const banner = container.querySelector("[data-near-turn-banner]");
        if (banner) {
          banner.style.display = data.notify_near_turn ? "block" : "none";
        }

        const statusBadge = container.querySelector("[data-status-badge]");
        if (statusBadge) {
          statusBadge.className = `badge rounded-pill badge-qs-${data.status}`;
          statusBadge.textContent = data.status.toUpperCase();
        }
      })
      .catch(() => {
        /* Silently ignore transient network errors; next poll will retry */
      });
  }

  function setField(container, field, value) {
    const el = container.querySelector(`[data-field="${field}"]`);
    if (el) el.textContent = value;
  }

  /* ---------------- Service tile selection on Book Token page ------------ */
  function initServiceTiles() {
    const tiles = document.querySelectorAll(".qs-service-tile");
    if (!tiles.length) return;

    tiles.forEach((tile) => {
      tile.addEventListener("click", () => {
        tiles.forEach((t) => t.classList.remove("selected"));
        tile.classList.add("selected");
        const input = document.getElementById("service_id");
        if (input) input.value = tile.getAttribute("data-service-id");
      });
    });
  }

  /* ---------------- Theme toggle (dark / light) --------------------------- */
  function initThemeToggle() {
    const toggle = document.getElementById("qsThemeToggle");
    if (!toggle) return;

    const applyTheme = (theme) => {
      document.documentElement.setAttribute("data-theme", theme);
      toggle.textContent = theme === "dark" ? "☀️" : "🌙";
      localStorage.setItem("qs-theme", theme);
    };

    const saved = localStorage.getItem("qs-theme") || "light";
    applyTheme(saved);

    toggle.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "light";
      applyTheme(current === "dark" ? "light" : "dark");
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initLiveTracking();
    initServiceTiles();
    initThemeToggle();
  });
})();
