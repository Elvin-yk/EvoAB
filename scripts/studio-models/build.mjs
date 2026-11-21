// Build against the source revision recorded by nanobot-ai 0.4.1.
// Upstream HKUDS/nanobot commit 3404fe83e22b7b2cb3edae1348ed45dd9b8ad051.
// Usage: npm ci --prefix <nanobot>/webui && node build.mjs <nanobot>
import { createRequire } from "node:module";
import { copyFile, mkdir, readFile, readdir, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { gzipSync } from "node:zlib";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(process.argv[2], "webui");
const require = createRequire(path.join(root, "package.json"));
const { build } = await import(pathToFileURL(path.join(path.dirname(require.resolve("vite/package.json")), "dist/node/index.js")).href);
function svgToDataUri(svg) {
  const normalized = svg.trim().replaceAll('"', "'");
  return (
    "data:image/svg+xml," +
    normalized
      .replaceAll("%", "%25")
      .replaceAll("<", "%3c")
      .replaceAll(">", "%3e")
      .replaceAll("#", "%23")
      .replace(/\s+/g, "%20")
  );
}
const antigravityLogoDataUri = svgToDataUri(await readFile(path.join(here, "antigravity-color.svg"), "utf8"));
// All other settings panels share the upstream SettingsView. Keep its optional
// modules in a separate download without changing the model controls.
const entry = path.join(here, "entry.tsx");
const initialModules = new Set();
function markInitial(id, getModuleInfo) {
  if (initialModules.has(id)) return;
  initialModules.add(id);
  for (const dependency of getModuleInfo(id)?.importedIds ?? []) markInitial(dependency, getModuleInfo);
}
const result = await build({
  root, base: "./", configFile: false, publicDir: false, logLevel: "warn",
  define: { "process.env.NODE_ENV": '"production"' },
  resolve: { alias: [
    { find: "@/providers/ClientProvider", replacement: path.join(here, "client.ts") },
    { find: "@", replacement: path.join(root, "src") },
  ], dedupe: Object.keys(require("./package.json").dependencies) },
  worker: { format: "es", rollupOptions: { output: { entryFileNames: "chunk-[hash].js", inlineDynamicImports: true } } },
  esbuild: { jsx: "automatic" },
  plugins: [{
    name: "studio-settings-api",
    transform(code, id) {
      let next = code;
      if (id === path.join(root, "src/lib/api.ts")) {
        next = next.replaceAll('${base}/api/settings', '${base}/model-ui/settings');
      }
      if (!id.startsWith(path.join(root, "src") + path.sep)) return next === code ? undefined : next;
      if (next.includes('"gemini.google.com"') && !/\bantigravity\s*:/.test(next)) {
        next = next.replace(
          /(\bgemini\s*:\s*)([A-Za-z_$][\w$]*)\(\s*"gemini\.google\.com"/,
          (_, prefix, fn) => `antigravity: ${fn}("antigravity.google", "#4285F4", "AG", [${JSON.stringify(antigravityLogoDataUri)}]), ${prefix}${fn}("gemini.google.com"`,
        );
      }
      if (/\bbrave_search\s*:\s*["']Brave Search["']/.test(next) && !/\bantigravity\s*:\s*["']Google Antigravity["']/.test(next)) {
        next = next.replace(
          /(\bbrave_search\s*:\s*["']Brave Search["'])/,
          'antigravity: "Google Antigravity", $1',
        );
      }
      if (next.includes("claude 克劳德 克勞德") && !/\bantigravity\s*:\s*["']google antigravity/i.test(next)) {
        next = next.replace(
          /(\banthropic\s*:\s*["']claude 克劳德 克勞德["'])/,
          'antigravity: "google antigravity agy 谷歌 反重力", $1',
        );
      }
      return next === code ? undefined : next;
    },
  }],
  build: { write: false, target: "es2022", reportCompressedSize: false, modulePreload: false,
    assetsInlineLimit: () => true,
    rollupOptions: {
      input: entry,
      output: {
        assetFileNames: "asset-[hash][extname]",
        entryFileNames: "models-[hash].js",
        chunkFileNames: "chunk-[hash].js",
        onlyExplicitManualChunks: true,
        manualChunks(id, { getModuleInfo }) {
          if (!initialModules.size) markInitial(entry, getModuleInfo);
          if (!initialModules.has(id)) return "on-demand";
        },
      },
    },
  },
});
const files = (Array.isArray(result) ? result[0] : result).output;
const license = await readFile(path.join(root, "../LICENSE"), "utf8");
const output = path.join(here, "../../evoab/ui/studio/static");
const assets = path.join(output, "model-assets");
await mkdir(assets, { recursive: true });
const filenames = new Set();
for (const file of files) {
  const filename = file.fileName + ".gz";
  filenames.add(filename);
  await writeFile(path.join(assets, filename), gzipSync(file.type === "chunk" ? file.code : file.source, { level: 9 }));
}
const main = files.find(file => file.type === "chunk" && file.facadeModuleId === entry);
await writeFile(path.join(assets, "entry.txt"), main.fileName + "\n");
for (const name of await readdir(assets)) {
  if (/^(models|chunk|asset)-[\w-]+\.[a-z0-9]+\.gz$/.test(name) && !filenames.has(name)) await unlink(path.join(assets, name));
}
await writeFile(path.join(output, "nanobot-LICENSE.txt"), license);

await copyFile(path.join(root, "../THIRD_PARTY_NOTICES.md"), path.join(output, "nanobot-NOTICES.txt"));
