/* =========================================================================
   admin.js — admin dashboard interactions (confirmations, chart helpers)
   ========================================================================= */

(function () {
  "use strict";

  function initConfirmActions() {
    document.querySelectorAll("[data-confirm]").forEach((form) => {
      form.addEventListener("submit", (e) => {
        const message = form.getAttribute("data-confirm");
        if (!window.confirm(message)) {
          e.preventDefault();
        }
      });
    });
  }

  function initSidebarActive() {
    const links = document.querySelectorAll(".qs-sidebar .nav-link");
    const path = window.location.pathname;
    links.forEach((link) => {
      const href = link.getAttribute("href");
      if (href && path.startsWith(href) && href !== "/admin/dashboard") {
        link.classList.add("active");
      } else if (href === path) {
        link.classList.add("active");
      }
    });
  }

  /**
   * Build a Chart.js chart from a canvas with data-labels / data-values
   * JSON attributes and a data-chart-type attribute (bar/line/doughnut).
   */
  function initAutoCharts() {
    document.querySelectorAll("[data-chart-type]").forEach((canvas) => {
      const type = canvas.getAttribute("data-chart-type");
      let labels = [];
      let values = [];
      try {
        labels = JSON.parse(canvas.getAttribute("data-labels") || "[]");
        values = JSON.parse(canvas.getAttribute("data-values") || "[]");
      } catch (err) {
        console.error("Invalid chart data", err);
        return;
      }

      const palette = ["#4338CA", "#06B6D4", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899"];

      new Chart(canvas.getContext("2d"), {
        type: type,
        data: {
          labels: labels,
          datasets: [
            {
              label: canvas.getAttribute("data-label") || "",
              data: values,
              backgroundColor: type === "line" ? "rgba(67,56,202,0.15)" : palette,
              borderColor: type === "line" ? "#4338CA" : palette,
              borderWidth: 2,
              tension: 0.35,
              fill: type === "line",
            },
          ],
        },
        options: {
          responsive: true,
          plugins: {
            legend: { display: type === "doughnut" },
          },
          scales: type === "doughnut" ? {} : {
            y: { beginAtZero: true, grid: { color: "rgba(148,163,184,0.15)" } },
            x: { grid: { display: false } },
          },
        },
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initConfirmActions();
    initSidebarActive();
    initAutoCharts();
  });
})();
