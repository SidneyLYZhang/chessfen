"use strict";

function initializeTablist(tablist) {
  const tabs = [...tablist.querySelectorAll("[role='tab']")];
  if (!tabs.length) {
    return;
  }
  const panels = tabs.map((tab) => {
    const id = tab.getAttribute("aria-controls");
    return id ? document.getElementById(id) : null;
  });

  function select(index, focus = false) {
    tabs.forEach((tab, position) => {
      const selected = position === index;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      const panel = panels[position];
      if (panel) {
        panel.hidden = !selected;
      }
    });
    if (focus) {
      tabs[index].focus();
    }
  }

  tablist.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }
    const tab = event.target.closest("[role='tab']");
    if (!tab) {
      return;
    }
    const index = tabs.indexOf(tab);
    if (index >= 0) {
      select(index);
    }
  });

  tablist.addEventListener("keydown", (event) => {
    const index = tabs.indexOf(document.activeElement);
    if (index < 0) {
      return;
    }
    let next = null;
    if (event.key === "ArrowRight") {
      next = (index + 1) % tabs.length;
    } else if (event.key === "ArrowLeft") {
      next = (index - 1 + tabs.length) % tabs.length;
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = tabs.length - 1;
    }
    if (next === null) {
      return;
    }
    event.preventDefault();
    select(next, true);
  });

  const initial = tabs.findIndex(
    (tab) => tab.getAttribute("aria-selected") === "true"
  );
  select(initial >= 0 ? initial : 0);
}

function initializeTabs() {
  for (const tablist of document.querySelectorAll("[data-analysis-tablist]")) {
    initializeTablist(tablist);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeTabs);
} else {
  initializeTabs();
}
