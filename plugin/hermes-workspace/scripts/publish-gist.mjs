#!/usr/bin/env node
/**
 * Publish Creator artifacts as GitHub Gists.
 */

import fs from "fs/promises";
import https from "https";

const API_BASE = "https://api.github.com/gists";

async function publishToGist(artifactDir, token) {
  const files = [];
  
  // Read artifact metadata and content
  const metaPath = artifactDir + "/meta.json";
  if (!fs.existsSync(metaPath)) {
    console.error("No meta.json found");
    return null;
  }

  const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8"));
  
  // Read all .art files as content
  for (const file of fs.readdirSync(artifactDir)) {
    if (/\.art$/.test(file)) {
      const filePath = artifactDir + "/" + file;
      const content = fs.readFileSync(filePath, "utf-8");
      
      files.push({
        filename: file,
        content: content,
      });
    }
  }

  if (!files.length) {
    console.error("No .art files found to publish");
    return null;
  }

  const gistData = {
    description: meta.title || "Hermes Workspace Creator artifact",
    public: true,
    files: files,
  };

  // POST to GitHub API
  return new Promise((resolve, reject) => {
    const options = {
      hostname: "api.github.com",
      path: `/gists?access_token=${token}`,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "User-Agent": "Hermes-Workspace-Creator/1.0",
      },
    };

    const req = https.request(options, (res) => {
      let data = "";
      
      res.on("data", chunk => { data += chunk; });
      res.on("end", () => {
        try {
          const gist = JSON.parse(data);
          console.log(`✓ Published to Gist: ${gist.html_url}`);
          resolve(gist);
        } catch (e) {
          reject(new Error("Failed to parse API response"));
        }
      });
    });

    req.on("error", reject);
    req.write(JSON.stringify(gistData));
    req.end();
  });
}

// Example usage:
// publishToGist("/path/to/artifact", process.env.GITHUB_TOKEN).then(console.log).catch(console.error);
