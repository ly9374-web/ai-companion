const fs = require('fs');
const path = require('path');

const projectRoot = path.resolve(__dirname, '..');
const optionalFeatureDescriptors = fs.readdirSync(projectRoot, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => path.join(projectRoot, entry.name, 'optional-feature.json'))
  .filter((descriptorPath) => fs.existsSync(descriptorPath));
if (optionalFeatureDescriptors.length > 1) {
  throw new Error(`Expected at most one optional feature, found: ${optionalFeatureDescriptors.join(', ')}`);
}
const optionalUsageDescriptions = optionalFeatureDescriptors.flatMap((descriptorPath) => {
  let descriptor;
  try {
    descriptor = JSON.parse(fs.readFileSync(descriptorPath, 'utf8'));
  } catch (error) {
    throw new Error(`Cannot read optional feature descriptor: ${descriptorPath}`, { cause: error });
  }
  if (!descriptor || typeof descriptor.id !== 'string' || !descriptor.id.trim()) {
    throw new Error(`Optional feature id is missing: ${descriptorPath}`);
  }
  const descriptions = descriptor.electron_mac_usage_descriptions || {};
  if (typeof descriptions !== 'object' || Array.isArray(descriptions)) {
    throw new Error(`Optional feature electron_mac_usage_descriptions is invalid: ${descriptorPath}`);
  }
  return Object.entries(descriptions).map(([key, value]) => {
    if (typeof value !== 'string') {
      throw new Error(`Optional feature mac usage description ${key} must be a string`);
    }
    return { [key]: value };
  });
});

module.exports = {
  extends: './electron-builder.yml',
  mac: {
    extendInfo: [
      ...optionalUsageDescriptions,
      { NSMicrophoneUsageDescription: "Application requests access to the device's microphone." },
      { NSDocumentsFolderUsageDescription: "Application requests access to the user's Documents folder." },
      { NSDownloadsFolderUsageDescription: "Application requests access to the user's Downloads folder." },
    ],
  },
};
