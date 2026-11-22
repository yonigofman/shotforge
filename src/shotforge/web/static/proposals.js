// Keep the existing forms as a fallback when browser streaming is unavailable.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!form.matches("form[data-proposal-target]") || !window.ReadableStream ||
      !window.TextDecoder || !window.AbortController) return;
  const target = document.getElementById(form.dataset.proposalTarget);
  const template = target?.parentElement.querySelector("template[data-proposal-loading]");
  if (!template) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  if (!target.dataset.generating) generateProposal(form, target, template);
}, true);

async function* proposalEvents(stream) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) {
        pending += decoder.decode();
        if (pending.trim()) yield JSON.parse(pending);
        break;
      }
      pending += decoder.decode(value, {stream: true});
      const lines = pending.split("\n");
      pending = lines.pop();
      for (const line of lines) {
        if (line.trim()) yield JSON.parse(line);
      }
    }
  } finally {
    reader.releaseLock();
  }
}

async function generateProposal(form, target, template) {
  const mediaType = "application/x-ndjson";
  const controller = new AbortController();
  const abort = () => controller.abort();
  const button = form.querySelector('button[type="submit"]');
  const fields = new FormData(form);
  target.querySelector("[data-proposal-error]")?.remove();
  const previous = document.createDocumentFragment();
  previous.append(...target.childNodes);
  target.append(template.content.cloneNode(true));
  target.dataset.generating = "true";
  button.disabled = true;

  const status = target.querySelector("[data-proposal-status]");
  const cancel = target.querySelector("[data-proposal-cancel]");
  const scenes = target.querySelector("[data-proposal-scenes]");
  const sceneTemplate = target.querySelector("template[data-proposal-scene]");
  const sceneRows = new Map();
  let restoreFocus = false;
  cancel.addEventListener("click", abort);
  window.addEventListener("pagehide", abort, {once: true});
  cancel.focus({preventScroll: true});

  try {
    const response = await fetch(form.action, {
      method: "POST",
      headers: {Accept: mediaType},
      body: new URLSearchParams(fields),
      signal: controller.signal,
    });
    if (!response.ok || !response.body || !response.headers.get("Content-Type")?.includes(mediaType)) {
      throw new Error("Could not start the proposal. Try again.");
    }
    for await (const event of proposalEvents(response.body)) {
      if (!target.isConnected) {
        abort();
        return;
      }
      if (event.type === "status") {
        status.textContent = event.message;
      } else if (event.type === "scenes" && event.ids.length) {
        scenes.replaceChildren();
        for (const id of event.ids) {
          const row = sceneTemplate.content.firstElementChild.cloneNode(true);
          row.querySelector("[data-proposal-scene-id]").textContent = id;
          scenes.append(row);
          sceneRows.set(id, row);
        }
      } else if (event.type === "scene") {
        const row = sceneRows.get(event.id);
        if (row) {
          const label = event.shots === 1 ? "shot" : "shots";
          row.querySelector("[data-proposal-scene-status]").textContent = `${event.shots} ${label} drafted`;
          row.querySelector(".proposal-skeleton").remove();
        }
      } else if (event.type === "error") {
        throw new Error(event.message);
      } else if (event.type === "complete") {
        const focused = document.activeElement === cancel;
        target.innerHTML = event.html;
        window.htmx?.process(target);
        if (focused) target.focus({preventScroll: true});
        return;
      }
    }
    throw new Error("The proposal stream ended early. Try again.");
  } catch (error) {
    restoreFocus = document.activeElement === cancel;
    target.replaceChildren(previous);
    const note = document.createElement("p");
    note.dataset.proposalError = "";
    note.setAttribute("role", controller.signal.aborted ? "status" : "alert");
    note.className = controller.signal.aborted ? "muted" : "form-error";
    note.textContent = controller.signal.aborted ? "Proposal cancelled." : error.message;
    target.prepend(note);
  } finally {
    delete target.dataset.generating;
    button.disabled = false;
    if (restoreFocus) button.focus({preventScroll: true});
    window.removeEventListener("pagehide", abort);
  }
}
