import { resolve, sep } from 'path';
import fs from 'fs';
import { defineConfig, externalizeDepsPlugin } from 'electron-vite';
import react from '@vitejs/plugin-react';
import { viteStaticCopy } from 'vite-plugin-static-copy'
import { normalizePath } from 'vite';

const projectRoot = resolve(__dirname, '..');
const optionalFeatureSources = fs.readdirSync(projectRoot, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => resolve(projectRoot, entry.name))
  .filter((directory) => fs.existsSync(resolve(directory, 'optional-feature.json')));
if (optionalFeatureSources.length > 1) {
  throw new Error(`Expected at most one optional feature, found: ${optionalFeatureSources.join(', ')}`);
}
const optionalFeatureSource = optionalFeatureSources[0];
let optionalFeatureFrontendEntry: string | undefined;
let optionalFeatureMainEntry: string | undefined;
if (optionalFeatureSource) {
  const descriptorPath = resolve(optionalFeatureSource, 'optional-feature.json');
  let descriptor: Record<string, unknown>;
  try {
    descriptor = JSON.parse(fs.readFileSync(descriptorPath, 'utf8'));
  } catch (error) {
    throw new Error(`Cannot read optional feature descriptor: ${descriptorPath}`, { cause: error });
  }
  if (typeof descriptor.id !== 'string' || !descriptor.id.trim()) {
    throw new Error(`Optional feature id is missing: ${descriptorPath}`);
  }
  const resolveEntry = (field: 'frontend_source_entry' | 'electron_main_entry'): string => {
    const value = descriptor[field];
    if (typeof value !== 'string' || !value.trim()) {
      throw new Error(`Optional feature ${field} is missing: ${descriptorPath}`);
    }
    const candidate = resolve(optionalFeatureSource, value);
    if (!candidate.startsWith(`${resolve(optionalFeatureSource)}${sep}`) || !fs.statSync(candidate, { throwIfNoEntry: false })?.isFile()) {
      throw new Error(`Optional feature ${field} file is missing: ${candidate}`);
    }
    return candidate;
  };
  optionalFeatureFrontendEntry = resolveEntry('frontend_source_entry');
  optionalFeatureMainEntry = resolveEntry('electron_main_entry');
}

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    resolve: {
      alias: {
        '@optional-feature-main': optionalFeatureMainEntry
          ? optionalFeatureMainEntry
          : resolve(__dirname, 'src/main/optional-feature-stub.ts'),
      },
    },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
  },
  renderer: {
    resolve: {
      dedupe: [
        'react',
        'react-dom',
        '@chakra-ui/react',
        'react-icons',
        'react-i18next',
      ],
      alias: {
        '@': resolve('src/renderer/src'),
        "@framework": resolve("src/renderer/WebSDK/Framework/src"),
        "@cubismsdksamples": resolve("src/renderer/WebSDK/src"),
        "@motionsyncframework": resolve(
          "src/renderer/MotionSync/Framework/src",
        ),
        "@motionsync": resolve("src/renderer/MotionSync/src"),
        "/src": resolve("src/renderer/src"),
        "@optional-feature": optionalFeatureFrontendEntry
          ? optionalFeatureFrontendEntry
          : resolve(__dirname, 'src/renderer/src/optional-feature-stub.tsx'),
      },
    },
    plugins: [
      viteStaticCopy({
        targets: [
          {
            src: normalizePath(resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js')),
            dest: './libs/',
          },
          {
            src: normalizePath(resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/silero_vad_v5.onnx')),
            dest: './libs/',
          },
          {
            src: normalizePath(resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/silero_vad_legacy.onnx')),
            dest: './libs/',
          },
          {
            src: normalizePath(resolve(__dirname, 'node_modules/onnxruntime-web/dist/*.wasm')),
            dest: './libs/',
          },
          {
            src: normalizePath(resolve(__dirname, 'src/renderer/WebSDK/Core/live2dcubismcore.js')),
            dest: './libs/'
          }
        ],
      }),
      react(),
    ],
    build: {
      rollupOptions: {
        onwarn(warning, warn) {
          if (warning.message.includes('onnxruntime')) {
            return;
          }
          warn(warning);
        },
      },
    },
  },
});
