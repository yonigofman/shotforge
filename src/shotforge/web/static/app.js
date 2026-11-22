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
    uploading: false,
    async upload(event, url) {
      this.over = false;
      const files = event.dataTransfer ? event.dataTransfer.files : event.target.files;
      if (this.uploading || !files || !files.length) return;
      const body = new FormData();
      for (const file of files) body.append("files", file);
      this.uploading = true;
      try {
        const response = await fetch(url, { method: "POST", body });
        if (!response.ok) throw new Error("Upload failed");
        toast("Filed takes.");
      } catch {
        toast("Upload failed.", "fail");
      } finally {
        this.uploading = false;
        if (window.htmx) htmx.trigger(document.body, "card-changed");
      }
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
