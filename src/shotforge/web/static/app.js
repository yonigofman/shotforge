// Small client-side helpers only: copy, drag-and-drop, toasts, shortcuts.
// Everything that changes the project goes through the server.

document.addEventListener("alpine:init", () => {
  Alpine.data("copyable", () => ({
    copied: false,
    async copy(selector) {
      const node = document.querySelector(selector);
      if (!node) return;
      try {
        await navigator.clipboard.writeText(node.innerText);
      } catch (err) {
        const range = document.createRange();
        range.selectNodeContents(node);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        document.execCommand("copy");
        selection.removeAllRanges();
      }
      this.copied = true;
      toast("Prompt copied. Paste it into a new chat.");
      setTimeout(() => (this.copied = false), 2000);
    },
  }));

  Alpine.data("dropzone", () => ({
    over: false,
    upload(event, url) {
      this.over = false;
      const files = event.dataTransfer ? event.dataTransfer.files : event.target.files;
      if (!files || !files.length) return;
      const body = new FormData();
      for (const file of files) body.append("files", file);
      fetch(url, { method: "POST", body })
        .then((response) => response.text())
        .then((html) => {
          document.body.dispatchEvent(new CustomEvent("takes-changed"));
          toast("Filed as a new take.");
          if (window.htmx) htmx.trigger(document.body, "takes-changed");
        })
        .catch(() => toast("Upload failed.", "fail"));
    },
  }));
});

function toast(message, level = "") {
  let holder = document.querySelector(".toasts");
  if (!holder) {
    holder = document.createElement("div");
    holder.className = "toasts";
    document.body.appendChild(holder);
  }
  const node = document.createElement("div");
  node.className = `toast ${level}`;
  node.textContent = message;
  holder.appendChild(node);
  setTimeout(() => node.remove(), 4000);
}

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, textarea, select")) return;
  if (event.key === "c") {
    const button = document.querySelector("[data-copy-prompt]");
    if (button) button.click();
  }
  if (event.key === "a") {
    const button = document.querySelector("[data-approve]");
    if (button && !button.disabled) button.focus();
  }
});

document.body && document.body.addEventListener("htmx:responseError", (event) => {
  toast(event.detail.xhr.responseText || "Something went wrong.", "fail");
});
