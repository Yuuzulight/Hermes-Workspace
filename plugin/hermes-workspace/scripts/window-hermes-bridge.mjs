
// window.hermes runtime bridge for Creator artifacts
(function() {
  // Guard against multiple inclusions
  if (window.HermesCreatorBridge) return;
  
  const Bridge = {
    _queue: [],
    _handlers: {},
    
    init() {
      // Listen for postMessage events from artifact iframes
      window.addEventListener("message", this._onMessage.bind(this));
      
      // Expose bridge to renderer and artifacts
      window.HermesCreatorBridge = this;
      
      console.log("[Hermes Creator] Bridge initialized");
    },
    
    _onMessage(event) {
      const data = event.data || {};
      
      if (!data.__hermes_creator__) return;
      
      // Route to appropriate handler
      if (this._handlers[data.type]) {
        this._handlers[data.type](event, data);
      } else {
        console.warn("[Hermes Creator] Unknown message type:", data.type);
      }
    },
    
    send(type, payload) {
      // Queue messages for Hermes agent to process
      this._queue.push({ type, payload });
      
      // Notify renderer that a message is ready
      if (window.HermesCreatorRenderer && window.HermesCreatorRenderer.getRoot()) {
        const root = window.HermesCreatorRenderer.getRoot();
        if (root) {
          root.dispatchEvent(new CustomEvent("hermes:bridge-message", { detail: { type, payload } }));
        }
      }
    },
    
    on(type, handler) {
      this._handlers[type] = handler;
      return () => delete this._handlers[type];
    },
    
    // Lifecycle hooks for artifact completion
    registerArtifact(id) {
      console.log("[Hermes Creator] Artifact registered:", id);
    },
    
    completeArtifact(id, sha256) {
      console.log("[Hermes Creator] Artifact completed:", id, sha256);
      
      // Trigger LLM oneshot for next artifact generation
      if (window.HermesAgent && window.HermesAgent.oneshot) {
        window.HermesAgent.oneshot({
          type: "artifact-complete",
          identifier: id,
          sha256,
        });
      }
    },
    
    throttleMessages(maxPerSecond = 10) {
      const interval = 1000 / maxPerSecond;
      
      this._lastSendTime = Date.now();
      return (type, payload) => {
        const now = Date.now();
        
        if (now - this._lastSendTime >= interval) {
          this.send(type, payload);
          this._lastSendTime = now;
        } else {
          // Queue for later
          setTimeout(() => this.send(type, payload), interval - (now - this._lastSendTime));
        }
      };
    },
  };
  
  Bridge.init();
})();
