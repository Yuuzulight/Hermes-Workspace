// artifact-lifecycle.ts — Manages artifact completion and Hermes communication
import { createArtifact, update_artifact } from "./cr_tools";

export class ArtifactLifecycleManager {
  constructor(private identifier: string) {}
  
  async initialize() {
    // Check if artifact already exists in store
    const meta = await read_artifact(this.identifier);
    
    if (meta) {
      console.log("[Hermes Creator] Restoring existing artifact:", this.identifier);
      return;
    }
    
    // Create new artifact directory
    const [dirPath, versionN] = createArtifact(
      this.identifier,
      "react",
      "typescript",
      `Creator Artifact ${this.identifier}`,
      "agent"
    );
    
    console.log("[Hermes Creator] New artifact created:", dirPath);
  }
  
  async complete(sha256: string) {
    // Mark artifact as completed in store
    update_artifact(this.identifier, "react", "typescript");
    
    // Notify Hermes runtime
    if (window.HermesAgent && window.HermesAgent.oneshot) {
      window.HermesAgent.oneshot({
        type: "artifact-complete",
        identifier: this.identifier,
        sha256,
        timestamp: Date.now(),
      });
    }
    
    console.log("[Hermes Creator] Artifact completed:", this.identifier);
  }
  
  async updateMetadata(title?: string, language?: string) {
    await update_artifact(
      this.identifier,
      "react",
      language || "typescript",
      title || `Creator Artifact ${this.identifier}`
    );
    
    console.log("[Hermes Creator] Metadata updated:", this.identifier);
  }
}

// Export read_artifact for lifecycle manager (normally imported from cr_tools)
export { read_artifact, update_artifact };
