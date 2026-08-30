// rate-limit.ts — Prevents Hermes agent from being overwhelmed by artifact messages
export function createRateLimiter(maxMessagesPerSecond: number = 10) {
  const interval = 1000 / maxMessagesPerSecond;
  
  return {
    lastSendTime: Date.now(),
    
    send(type: string, payload: any) {
      const now = Date.now();
      
      if (now - this.lastSendTime >= interval) {
        // Send immediately
        window.HermesCreatorBridge?.send(type, payload);
        this.lastSendTime = now;
      } else {
        // Queue for later
        setTimeout(() => {
          window.HermesCreatorBridge?.send(type, payload);
          this.lastSendTime = Date.now();
        }, interval - (now - this.lastSendTime));
      }
    },
  };
}

// Usage example:
// const limiter = createRateLimiter(10);
// limiter.send("artifact-update", { data: "..." });
