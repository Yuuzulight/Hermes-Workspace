// renderer.ts — Attaches Creator artifacts to the DOM
const ARTIFACT_MOUNT_ID = "__hermes-creator-root__";
function initRenderer() {
  const container = document.getElementById(ARTIFACT_MOUNT_ID);
  if (!container) return;
  container.innerHTML = "";
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initRenderer);
} else {
  initRenderer();
}