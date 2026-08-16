export const DEFAULT_IMAGE_COMPRESSION_QUALITY = 0.8;
export const DEFAULT_IMAGE_MAX_WIDTH = 0;
export const IMAGE_COMPRESSION_QUALITY_KEY = 'appImageCompressionQuality';
export const IMAGE_MAX_WIDTH_KEY = 'appImageMaxWidth';

export const getStoredImageCompressionQuality = (): number => {
  const value = Number.parseFloat(
    localStorage.getItem(IMAGE_COMPRESSION_QUALITY_KEY) ?? '',
  );
  return !Number.isNaN(value) && value >= 0.1 && value <= 1
    ? value
    : DEFAULT_IMAGE_COMPRESSION_QUALITY;
};

export const getStoredImageMaxWidth = (): number => {
  const value = Number.parseInt(
    localStorage.getItem(IMAGE_MAX_WIDTH_KEY) ?? '',
    10,
  );
  return !Number.isNaN(value) && value >= 0
    ? value
    : DEFAULT_IMAGE_MAX_WIDTH;
};

export const setCurrentImageSettings = (
  compressionQuality: number,
  maxWidth: number,
): void => {
  localStorage.setItem(
    IMAGE_COMPRESSION_QUALITY_KEY,
    compressionQuality.toString(),
  );
  localStorage.setItem(IMAGE_MAX_WIDTH_KEY, maxWidth.toString());
};
