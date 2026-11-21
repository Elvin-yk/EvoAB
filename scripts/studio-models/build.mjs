// Build against the source revision recorded by nanobot-ai 0.4.1.
// Upstream HKUDS/nanobot commit 3404fe83e22b7b2cb3edae1348ed45dd9b8ad051.
// Usage: npm ci --prefix <nanobot>/webui && node build.mjs <nanobot>
import { createRequire } from "node:module";
import { copyFile, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { gzipSync } from "node:zlib";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(process.argv[2], "webui");
const require = createRequire(path.join(root, "package.json"));
const { build } = await import(pathToFileURL(path.join(path.dirname(require.resolve("vite/package.json")), "dist/node/index.js")).href);
const result = await build({
  root, configFile: false, publicDir: false, logLevel: "warn",
  define: { "process.env.NODE_ENV": '"production"' },
  resolve: { alias: [
    { find: "@/providers/ClientProvider", replacement: path.join(here, "client.ts") },
    { find: "@", replacement: path.join(root, "src") },
  ], dedupe: Object.keys(require("./package.json").dependencies) },
  worker: { format: "es" },
  esbuild: { jsx: "automatic" },
  plugins: [{
    name: "studio-settings-api",
    transform(code, id) {
      if (id === path.join(root, "src/lib/api.ts")) return code.replaceAll('${base}/api/settings', '${base}/model-ui/settings');
    },
  }],
  build: { write: false, target: "es2022", reportCompressedSize: false,
    lib: { entry: path.join(here, "entry.tsx"), formats: ["es"], fileName: "studio-models" },
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
});
const bundle = (Array.isArray(result) ? result[0] : result).output.find((file) => file.type === "chunk" && file.isEntry);
const license = await readFile(path.join(root, "../LICENSE"), "utf8");
const output = path.join(here, "../../evoab/ui/studio/static");
await writeFile(path.join(output, "nanobot-models.js.gz"), gzipSync(bundle.code, {level: 9}));
await writeFile(path.join(output, "nanobot-LICENSE.txt"), license);

await copyFile(path.join(root, "../THIRD_PARTY_NOTICES.md"), path.join(output, "nanobot-NOTICES.txt"));
