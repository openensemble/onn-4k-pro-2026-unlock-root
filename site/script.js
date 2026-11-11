"use strict";

const status = document.getElementById("copy-status");
const resetTimers = new WeakMap();

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      // A denied clipboard permission can still allow copying through a user gesture.
    }
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.setAttribute("aria-hidden", "true");
  field.style.cssText = "position:fixed;left:-9999px;top:0;";
  document.body.appendChild(field);
  field.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (_) {
    copied = false;
  }
  field.remove();
  return copied;
}

for (const button of document.querySelectorAll("[data-copy]")) {
  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copy);
    if (!target) return;
    const copied = await copyText(target.textContent.trim());
    button.focus({ preventScroll: true });
    window.clearTimeout(resetTimers.get(button));
    button.classList.toggle("copied", copied);
    button.textContent = copied ? "Copied" : "Select text";
    if (copied) {
      status.textContent = button.dataset.label + " copied.";
    } else {
      const range = document.createRange();
      range.selectNodeContents(target);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = "Copy was unavailable. The text is selected; use your browser’s copy command.";
    }
    resetTimers.set(button, window.setTimeout(() => {
      button.textContent = "Copy";
      button.classList.remove("copied");
    }, 2200));
  });
  button.hidden = false;
}
