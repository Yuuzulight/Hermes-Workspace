// esbuild-wasm config for Creator artifacts
const CONFIG = {
  bundle: true,
  minify: false,
  sourcemap: true,
  platform: "browser",
  target: ["es2018"],
  external: ["react", "react-dom"],
};
export default CONFIG;