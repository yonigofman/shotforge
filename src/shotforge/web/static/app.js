// Small client-side helpers only: copy, drag-and-drop, toasts, shortcuts.
// Everything that changes the project goes through the server.

document.addEventListener("alpine:init", () => {
  Alpine.data("compare", () => ({
    // Where the wipe sits, and whether the two images are wiped or paired.
    at: 50,
    mode: "wipe",
    init() {
      this.onKey = (event) => {
        if (event.target.matches("input, textarea, select")) return;
        const step = event.shiftKey ? 1 : 5;
        if (event.key === "ArrowLeft") this.at = Math.max(0, this.at - step);
        else if (event.key === "ArrowRight") this.at = Math.min(100, this.at + step);
        else if (event.key === " ") this.at = 50;
        else if (event.key === "s") this.mode = this.mode === "wipe" ? "pair" : "wipe";
        else return;
        event.preventDefault();
      };
      document.addEventListener("keydown", this.onKey);
    },
    destroy() {
      document.removeEventListener("keydown", this.onKey);
    },
  }));

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

function setRail(shut) {
  document.documentElement.dataset.rail = shut ? "shut" : "open";
  try {
    localStorage.setItem("shotforge.rail", shut ? "shut" : "open");
  } catch {}
  const toggle = document.querySelector("[data-rail-toggle]");
  if (toggle) {
    const label = shut ? "Show the rail" : "Collapse the rail";
    toggle.setAttribute("aria-label", label);
    toggle.title = `${label}  [`;
  }
}

// "g" then a letter jumps to a rail destination; the rail itself carries the
// letters, so the map is never a second list to keep in step.
let chord = 0;

document.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey || event.target.isContentEditable ||
      event.target.matches("input, textarea, select")) return;
  if (event.key === "?") {
    document.getElementById("keys")?.showModal();
    return;
  }
  if (event.key === "[") {
    setRail(document.documentElement.dataset.rail !== "shut");
    return;
  }
  if (chord && Date.now() - chord < 1200) {
    chord = 0;
    const item = document.querySelector(`.rail-item[data-nav="${CSS.escape(event.key)}"]`);
    if (item) {
      event.preventDefault();
      window.location.href = item.href;
      return;
    }
  }
  if (event.key === "g") {
    chord = Date.now();
    return;
  }
  chord = 0;
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

const uploadSelector = "form[data-file-upload]";

document.addEventListener("dragover", (event) => {
  if (!Array.from(event.dataTransfer.types).includes("Files")) return;
  const form = event.target.closest(uploadSelector);
  if (!form && !document.querySelector(uploadSelector)) return;
  event.preventDefault();
  if (form && form.getAttribute("aria-busy") !== "true") {
    event.dataTransfer.dropEffect = "copy";
    form.classList.add("over");
  } else {
    event.dataTransfer.dropEffect = "none";
  }
});

document.addEventListener("dragleave", (event) => {
  const form = event.target.closest(uploadSelector);
  if (form && !form.contains(event.relatedTarget)) form.classList.remove("over");
});

document.addEventListener("drop", (event) => {
  const form = event.target.closest(uploadSelector);
  const files = Array.from(event.dataTransfer.files);
  if (!form && (!files.length || !document.querySelector(uploadSelector))) return;
  event.preventDefault();
  if (form) {
    form.classList.remove("over");
    if (files.length) uploadFiles(form, files);
  }
});

document.addEventListener("paste", (event) => {
  const form = event.target.closest(uploadSelector);
  if (!form || !event.clipboardData) return;
  const images = Array.from(event.clipboardData.files).filter(file => file.type.startsWith("image/"));
  if (!images.length) {
    for (const item of event.clipboardData.items) {
      if (item.kind !== "file" || !item.type.startsWith("image/")) continue;
      const file = item.getAsFile();
      if (file) images.push(file);
    }
  }
  if (!images.length) return;
  event.preventDefault();
  uploadFiles(form, images, true);
});

document.addEventListener("change", (event) => {
  const input = event.target;
  const form = input.form;
  if (input.type !== "file" || form?.dataset.fileUpload !== "takes") return;
  const files = Array.from(input.files);
  input.value = "";
  if (files.length) uploadFiles(form, files);
});

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-keys]")) {
    document.getElementById("keys")?.showModal();
    return;
  }
  if (event.target.closest("[data-rail-toggle]")) {
    setRail(document.documentElement.dataset.rail !== "shut");
    return;
  }
  const chooser = event.target.closest("[data-choose-files]");
  if (chooser) chooser.form.querySelector('input[type="file"]').click();
  const button = event.target.closest("[data-copy-image]");
  if (button) copyImage(button.dataset.copyImage);
});

// A clipped file input still has to say what was chosen.
document.addEventListener("change", (event) => {
  const input = event.target.closest("input.file-clip");
  if (!input) return;
  const name = input.form?.querySelector("[data-file-name]");
  if (name) name.textContent = input.files?.[0]?.name || "No file chosen";
});

// Image errors do not bubble, so this listens on the way down.
document.addEventListener("error", (event) => {
  const frame = event.target instanceof HTMLImageElement && event.target.closest(".frame");
  if (frame) frame.classList.add("frame-broken");
}, true);

async function uploadFiles(form, files, pasted = false) {
  if (form.getAttribute("aria-busy") === "true") return;
  const input = form.querySelector('input[type="file"]');
  if (!input.multiple && files.length !== 1) {
    toast("File one image at a time.", "fail");
    return;
  }

  const keepFocus = form.contains(document.activeElement);
  const controls = form.querySelector("[data-upload-controls]");
  const status = form.querySelector("[data-upload-status]");
  form.setAttribute("aria-busy", "true");
  if (controls) controls.hidden = true;
  if (status) status.hidden = false;
  let posted = false;
  try {
    if (pasted) {
      files = await Promise.all(files.map(async (file) => new File(
        [await imageAsPng(file)], "clipboard.png", {type: "image/png"}
      )));
    }
    if (form.dataset.fileUpload === "design") {
      const transfer = new DataTransfer();
      transfer.items.add(files[0]);
      input.files = transfer.files;
      form.requestSubmit();
      return;
    }

    const body = new FormData();
    for (const file of files) body.append(input.name, file);
    posted = true;
    const response = await fetch(form.action, {method: "POST", body});
    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(typeof error?.detail === "string" ? error.detail : "Upload failed.");
    }
    toast("Filed takes.");
  } catch (error) {
    toast(error.message || "Upload failed.", "fail");
  } finally {
    const card = form.closest("#shot-card");
    if (posted && card && window.htmx) {
      try {
        await htmx.ajax("GET", card.getAttribute("hx-get"), {target: card, swap: "outerHTML"});
        if (keepFocus && document.activeElement === document.body) {
          document.querySelector('#shot-card [data-file-upload="takes"]')?.focus({preventScroll: true});
        }
      } catch {
        toast("Refresh the shot card to see the latest takes.", "fail");
      }
    }
    form.removeAttribute("aria-busy");
    if (controls) controls.hidden = false;
    if (status) status.hidden = true;
  }
}

async function imageAsPng(image) {
  if (image.type === "image/png") return image;
  const bitmap = await createImageBitmap(image);
  try {
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    canvas.getContext("2d").drawImage(bitmap, 0, 0);
    return await new Promise((resolve, reject) => {
      canvas.toBlob(
        (png) => png ? resolve(png) : reject(new Error("Could not convert the image to PNG.")),
        "image/png"
      );
    });
  } finally {
    bitmap.close();
  }
}

async function copyImage(url) {
  try {
    if (!navigator.clipboard?.write || !window.ClipboardItem) throw new Error();
    // Start the clipboard write during the click gesture, even if fetching takes time.
    const png = fetch(url, {cache: "no-cache"}).then((response) => {
      if (!response.ok) throw new Error();
      return response.blob().then(imageAsPng);
    });
    await navigator.clipboard.write([new ClipboardItem({"image/png": png})]);
    toast("Image copied.");
  } catch {
    toast("Could not copy the image. Open the attachment to copy it.", "fail");
  }
}
