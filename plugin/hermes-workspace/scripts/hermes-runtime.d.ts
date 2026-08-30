// hermes-runtime.d.ts — Type definitions for Hermes runtime API
declare global {
  interface Window {
    // Bridge between artifact iframes and Hermes agent
    HermesCreatorBridge?: HermesCreatorBridge;
    
    // Renderer for mounting artifacts to DOM
    HermesCreatorRenderer?: HermesCreatorRenderer;
    
    // Artifact context exposed to each iframe
    HermesArtifactContext?: HermesArtifactContext;
  }
}

interface HermesCreatorBridge {
  init(): void;
  send(type: string, payload: any): void;
  on(type: string, handler: (event: MessageEvent, data: any) => void): () => void;
  registerArtifact(id: string): void;
  completeArtifact(id: string, sha256: string): void;
}

interface HermesCreatorRenderer {
  getRoot(): HTMLElement | null;
  sendToHermes(type: string, payload: any): void;
}

interface HermesArtifactContext {
  identifier: string;
  type: string;
  version: string;
  sendToHermes(type: string, payload: any): void;
  onHermesMessage(handler: (data: any) => void): () => void;
}

// Export for module systems
export {};
