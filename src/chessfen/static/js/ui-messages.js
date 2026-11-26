"use strict";

function datasetKey(attribute) {
  return attribute.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}

export function readMessages(attribute) {
  const key = datasetKey(attribute);
  return Object.fromEntries(
    [...document.querySelectorAll(`[data-${attribute}]`)].map((node) => [
      node.dataset[key],
      node.textContent,
    ])
  );
}
