import { defineConfig, normalizePath } from 'vite';
import path from 'path';
import react from '@vitejs/plugin-react-swc';
import fs from 'fs';

const projectRoot = path.resolve(__dirname, '..');
const optionalFeatureSources = fs.readdirSync(projectRoot, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => path.join(projectRoot, entry.name))
  .filter((directory) => fs.existsSync(path.join(directory, 'optional-feature.json')));
if (optionalFeatureSources.length > 1) {
  throw new Error(`Expected at most one optional feature, found: ${optionalFeatureSources.join(', ')}`);
}
const optionalFeatureSource = optionalFeatureSources[0];
let optionalFeatureFrontendEntry: string | undefined;
if (optionalFeatureSource) {
  const descriptorPath = path.join(optionalFeatureSource, 'optional-feature.json');
  let descriptor: Record<string, unknown>;
  try {
    descriptor = JSON.parse(fs.readFileSync(descriptorPath, 'utf8'));
  } catch (error) {
    throw new Error(`Cannot read optional feature descriptor: ${descriptorPath}`, { cause: error });
  }
  if (typeof descriptor.id !== 'string' || !descriptor.id.trim()) {
    throw new Error(`Optional feature id is missing: ${descriptorPath}`);
  }
  if (typeof descriptor.frontend_source_entry !== 'string' || !descriptor.frontend_source_entry.trim()) {
    throw new Error(`Optional feature frontend_source_entry is missing: ${descriptorPath}`);
  }
  const candidate = path.resolve(optionalFeatureSource, descriptor.frontend_source_entry);
  if (!candidate.startsWith(`${path.resolve(optionalFeatureSource)}${path.sep}`) || !fs.statSync(candidate, { throwIfNoEntry: false })?.isFile()) {
    throw new Error(`Optional feature frontend source entry is missing: ${candidate}`);
  }
  optionalFeatureFrontendEntry = candidate;
}

const createConfig = async (outDir: string) => ({
  plugins: [
    (await import('vite-plugin-static-copy')).viteStaticCopy({
      targets: [
        {
          src: normalizePath(path.resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js')),
          dest: './libs/',
        },
        {
          src: normalizePath(path.resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/silero_vad_v5.onnx')),
          dest: './libs/',
        },
        {
          src: normalizePath(path.resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/silero_vad_legacy.onnx')),
          dest: './libs/',
        },
        {
          src: normalizePath(path.resolve(__dirname, 'node_modules/onnxruntime-web/dist/*.wasm')),
          dest: './libs/',
        },
        {
          src: normalizePath(path.resolve(__dirname, 'src/renderer/WebSDK/Core/live2dcubismcore.js')),
          dest: './libs/',
        },
      ],
    }),
    react(),
  ],
  resolve: {
    dedupe: [
      'react',
      'react-dom',
      '@chakra-ui/react',
      'react-icons',
      'react-i18next',
    ],
    alias: {
      "@": path.resolve(__dirname, "./src/renderer/src"),
      "@framework": path.resolve(__dirname, "./src/renderer/WebSDK/Framework/src"),
      "@cubismsdksamples": path.resolve(__dirname, "./src/renderer/WebSDK/src"),
      "@motionsyncframework": path.resolve(
        __dirname,
        "./src/renderer/MotionSync/Framework/src",
      ),
      "@motionsync": path.resolve(__dirname, "./src/renderer/MotionSync/src"),
      "/src": path.resolve(__dirname, "./src/renderer/src"),
      "@optional-feature": optionalFeatureFrontendEntry
        ? optionalFeatureFrontendEntry
        : path.resolve(__dirname, './src/renderer/src/optional-feature-stub.tsx'),
    },
  },
  root: path.join(__dirname, "src/renderer"),
  publicDir: path.join(__dirname, "src/renderer/public"),
  base: "./",
  server: {
    port: 3000,
  },
  build: {
    outDir: path.join(__dirname, outDir),
    emptyOutDir: true,
    assetsDir: "assets",
    rollupOptions: {
      input: {
        main: path.join(__dirname, "src/renderer/index.html"),
      },
    },
  },
  ssr: {
    noExternal: ['vite-plugin-static-copy'],
  },
});

export default defineConfig(async ({ mode }) => {
  if (mode === 'web') {
    return createConfig('dist/web');
  }
  return createConfig('dist/renderer');
});
