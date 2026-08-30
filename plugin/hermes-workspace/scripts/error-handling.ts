// error-handling.ts — Graceful degradation for Hermes runtime failures
export class HermesErrorHandler {
  private _retryCount: number = 0;
  private _maxRetries: number = 3;
  
  async sendToHermes(type: string, payload: any): Promise<boolean> {
    try {
      // Try direct bridge first
      if (window.HermesCreatorBridge) {
        window.HermesCreatorBridge.send(type, payload);
        return true;
      }
      
      // Fallback to renderer
      if (window.HermesCreatorRenderer) {
        window.HermesCreatorRenderer.sendToHermes(type, payload);
        return true;
      }
      
      return false;
    } catch (error) {
      console.error("[Hermes Error] Failed to send message:", error);
      this._handleFailure(error);
      return false;
    }
  }
  
  private _handleFailure(error: any) {
    if (this._retryCount < this._maxRetries) {
      this._retryCount++;
      
      // Exponential backoff
      const delay = Math.min(1000 * Math.pow(2, this._retryCount), 30000);
      
      setTimeout(() => {
        this._handleFailure(error);
      }, delay);
    } else {
      console.error("[Hermes Error] Max retries exceeded. Giving up.");
      this._reset();
    }
  }
  
  private _reset() {
    this._retryCount = 0;
  }
}

// Usage example:
// const errorHandler = new HermesErrorHandler();
// await errorHandler.sendToHermes("artifact-update", { data: "..." });
