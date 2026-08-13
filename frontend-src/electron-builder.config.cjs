const fs = require('fs');
const path = require('path');

const projectRoot = path.resolve(__dirname, '..');
const optionalUsageDescriptions = fs.readdirSync(projectRoot, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => path.join(projectRoot, entry.name, 'optional-feature.json'))
  .filter((descriptorPath) => fs.existsSync(descriptorPath))
  .flatMap((descriptorPath) => {
    try {
      const descriptor = JSON.parse(fs.readFileSync(descriptorPath, 'utf8'));
      return Object.entries(descriptor.electron_mac_usage_descriptions || {})
        .map(([key, value]) => ({ [key]: value }));
    } catch (_error) {
      return [];
    }
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
