import React from "react";
function ArtifactPreview() {
  return (
    <div className="p-4 space-y-4">
      <h1 className="text-2xl font-bold text-[#18181b]">Creator Preview</h1>
      <button 
        className="px-4 py-2 bg-[#2563eb] text-white rounded-lg"
        onClick={() => window.hermes?.send("artifact:interact")}
      >Interact</button>
    </div>
  );
}
export { ArtifactPreview };