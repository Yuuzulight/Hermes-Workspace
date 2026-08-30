// renderer-integration.ts — Attaches Creator artifacts to the DOM with Hermes bridge
import React from "react";
import ReactDOM from "react-dom/client";

const ARTIFACT_MOUNT_ID = "__hermes-creator-root__";

function createArtifactMount() {
  const container = document.getElementById(ARTIFACT_MOUNT_ID);
  
  if (!container) {
    console.error("Hermes Creator mount point not found");
    return null;
  }
  
  // Clear any existing content (artifacts render into this container)
  container.innerHTML = "";
  
  const root = ReactDOM.createRoot(container);
  return root;
}

// Initialize when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initRenderer);
} else {
  initRenderer();
}

function initRenderer() {
  const root = createArtifactMount();
  
  if (!root) return;
  
  // Load Hermes Creator Bridge first
  import("./scripts/window-hermes-bridge.mjs").then(() => {
    console.log("[Hermes Creator] Bridge loaded");
    
    // Then load the initial artifact
    import("./src/initial.tsx").then(({ ArtifactPreview }) => {
      root.render(React.createElement(React.StrictMode, null, React.createElement(ArtifactPreview)));
      
      // Notify Hermes that renderer is ready
      if (window.HermesCreatorBridge) {
        window.HermesCreatorBridge.send("renderer-ready", {});
      }
    }).catch(error => {
      console.error("[Hermes Creator] Failed to load initial artifact:", error);
      
      // Notify Hermes of failure
      if (window.HermesCreatorBridge) {
        window.HermesCreatorBridge.send("artifact-error", { error: error.message });
      }
    });
  }).catch(error => {
    console.error("[Hermes Creator] Failed to load bridge:", error);
    
    // Notify Hermes of failure
    if (window.HermesCreatorBridge) {
      window.HermesCreatorBridge.send("bridge-error", { error: error.message });
    }
  });
}

// Expose for Hermes agent communication
window.HermesCreatorRenderer = {
  getRoot: () => document.getElementById(ARTIFACT_MOUNT_ID),
  sendToHermes(type, payload) {
    if (window.HermesCreatorBridge) {
      window.HermesCreatorBridge.send(type, payload);
    }
  },
};
