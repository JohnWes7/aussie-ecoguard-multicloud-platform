// Toast messages. Any page script can call:
//   showToast("Uploaded fox.jpg", "success");
//   showToast("Could not reach the server", "error");
//   showToast("Tagging started", "info");
// A toast appears in the bottom-right corner, closes itself after a few seconds
// (errors stay longer) and has a close button. It needs Bootstrap's JS bundle
// and the <div id="toasts"> container in index.html.

const TOAST_TYPES = {
  success: { colour: "text-bg-success", delay: 4000, closeWhite: true, role: "status" },
  info: { colour: "text-bg-info", delay: 4000, closeWhite: false, role: "status" },
  error: { colour: "text-bg-danger", delay: 8000, closeWhite: true, role: "alert" },
};

function showToast(message, type = "info") {
  const container = document.getElementById("toasts");
  const style = TOAST_TYPES[type] ?? TOAST_TYPES.info; // an unknown type falls back to "info"
  if (!container || !window.bootstrap) {
    console.log(`[${type}] ${message}`); // the page cannot show it, but the message is not lost
    return;
  }

  const toast = document.createElement("div");
  toast.className = `toast align-items-center border-0 ${style.colour}`;
  toast.setAttribute("role", style.role); // "alert" makes screen readers read an error at once
  toast.setAttribute("aria-atomic", "true");

  const layout = document.createElement("div");
  layout.className = "d-flex";

  // textContent, not innerHTML: the message may contain text from the server.
  const body = document.createElement("div");
  body.className = "toast-body";
  body.textContent = message;

  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn-close me-2 m-auto" + (style.closeWhite ? " btn-close-white" : "");
  close.setAttribute("data-bs-dismiss", "toast");
  close.setAttribute("aria-label", "Close");

  layout.append(body, close);
  toast.append(layout);
  container.append(toast);

  // Remove the element once it has faded out so old toasts do not pile up in the page.
  toast.addEventListener("hidden.bs.toast", () => toast.remove());
  new bootstrap.Toast(toast, { delay: style.delay }).show();
}
