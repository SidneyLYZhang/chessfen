(function () {
  "use strict";

  // Mark that scripting is available before the body renders. The stylesheet
  // uses this class to collapse the mobile menu; without it, the menu stays
  // visible so navigation keeps working with JavaScript disabled.
  document.documentElement.classList.add("js");

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    var area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
    } catch (error) {
      /* ignore */
    }
    document.body.removeChild(area);
    return Promise.resolve();
  }

  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.querySelector("[data-menu-toggle]");
    var panel = document.querySelector("[data-menu-panel]");
    var desktop = window.matchMedia("(min-width: 1101px)");

    function setMenu(open) {
      if (!toggle || !panel) {
        return;
      }
      panel.setAttribute("data-open", open ? "true" : "false");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    if (toggle && panel) {
      setMenu(false);

      toggle.addEventListener("click", function () {
        setMenu(panel.getAttribute("data-open") !== "true");
      });

      document.addEventListener("keydown", function (event) {
        if (
          event.key === "Escape" &&
          panel.getAttribute("data-open") === "true"
        ) {
          setMenu(false);
          toggle.focus();
        }
      });

      panel.addEventListener("click", function (event) {
        if (event.target.closest("a")) {
          setMenu(false);
        }
      });

      document.addEventListener("click", function (event) {
        if (panel.getAttribute("data-open") !== "true") {
          return;
        }
        if (event.target.closest("[data-menu-panel], [data-menu-toggle]")) {
          return;
        }
        setMenu(false);
      });

      desktop.addEventListener("change", function (event) {
        if (event.matches) {
          setMenu(false);
        }
      });
    }

    document.addEventListener("click", function (event) {
      if (!(event.target instanceof Element)) {
        return;
      }
      var button = event.target.closest("[data-copy-target]");
      if (!button) {
        return;
      }
      var targetId = button.getAttribute("data-copy-target");
      var target = targetId ? document.getElementById(targetId) : null;
      if (!target) {
        return;
      }
      copyText(target.textContent.trim()).then(function () {
        var original = button.getAttribute("data-copy-label") || button.textContent;
        button.setAttribute("data-copy-label", original);
        button.textContent = button.getAttribute("data-copied-label") || original;
        setTimeout(function () {
          button.textContent = original;
        }, 1500);
      });
    });
  });
})();
