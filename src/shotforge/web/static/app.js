// Small client-side helpers only: copy, drag-and-drop, toasts, shortcuts.
// Everything that changes the project goes through the server.

document.addEventListener("alpine:init", () => {
  Alpine.data("copyable", () => ({
    copied: false,
    async copy(selector) {
      const node = document.querySelector(selector);
      if (!node) return;
      if (!await copyPrompt(node)) return;
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

async function copyPrompt(node) {
  try {
    await navigator.clipboard.writeText(node.textContent);
    return true;
  } catch {
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    const copied = document.execCommand("copy");
    if (copied) selection.removeAllRanges();
    else toast("Select and copy the prompt text.", "fail");
    return copied;
  }
}

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

document.querySelectorAll("[data-design-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const prompt = document.getElementById(button.dataset.designCopy);
    if (await copyPrompt(prompt)) toast("Prompt copied.");
  });
});

document.querySelectorAll("[data-design-drop]").forEach((form) => {
  form.addEventListener("dragover", (event) => {
    event.preventDefault();
    form.classList.add("over");
  });
  form.addEventListener("dragleave", () => form.classList.remove("over"));
  form.addEventListener("drop", (event) => {
    event.preventDefault();
    form.classList.remove("over");
    const files = event.dataTransfer.files;
    if (files.length !== 1) {
      toast("Drop one image at a time.", "fail");
      return;
    }
    form.querySelector('input[type="file"]').files = files;
    form.requestSubmit();
  });
});
