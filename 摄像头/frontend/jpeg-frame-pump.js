// 与参考实现对齐：中心正方形裁剪后缩放到固定尺寸，保证人脸像素密度，
// rPPG 心率检测依赖面部区域的保真度。
const FRAME_SIZE = 480;

export class JpegFramePump {
  constructor({ fps = 12, jpegQuality = 0.78, maxBufferedBytes = 65536 } = {}) {
    this.frameIntervalMs = 1000 / Math.max(1, fps);
    this.jpegQuality = jpegQuality;
    this.maxBufferedBytes = maxBufferedBytes;
    this.video = document.createElement('video');
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.autoplay = true;
    this.canvas = document.createElement('canvas');
    this.canvas.width = FRAME_SIZE;
    this.canvas.height = FRAME_SIZE;
    this.context = this.canvas.getContext('2d', { alpha: false });
    this.running = false;
    this.encoding = false;
    this.timer = null;
    this.sendFrame = null;
  }

  async start(stream, sendFrame) {
    this.stop();
    if (!stream || !this.context) return;
    this.video.srcObject = stream;
    this.sendFrame = sendFrame;
    this.running = true;
    try {
      await this.video.play();
    } catch (error) {
      console.warn('[CameraEmotion] 摄像头流无法在隐藏视频元素中播放:', error);
    }
    // 与参考实现对齐：setInterval 独立计时，编码慢时跳帧而不是拉长帧周期，
    // 保证 rPPG 心率检测所需的稳定帧率。
    this.timer = window.setInterval(() => this.tick(), this.frameIntervalMs);
  }

  tick() {
    if (!this.running || this.encoding) return;
    if (this.video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;

    const width = this.video.videoWidth;
    const height = this.video.videoHeight;
    if (!width || !height) return;

    // 中心正方形裁剪再缩放到 FRAME_SIZE：提升人脸区域像素密度与压缩保真度。
    const cropSize = Math.min(width, height);
    const sourceX = (width - cropSize) / 2;
    const sourceY = (height - cropSize) / 2;
    this.context.drawImage(
      this.video,
      sourceX, sourceY, cropSize, cropSize,
      0, 0, FRAME_SIZE, FRAME_SIZE,
    );
    this.encoding = true;
    this.canvas.toBlob((blob) => {
      this.encoding = false;
      if (blob && this.running && this.sendFrame) {
        try {
          this.sendFrame(blob, this.maxBufferedBytes);
        } catch (error) {
          console.warn('[CameraEmotion] JPEG 帧发送失败:', error);
        }
      }
    }, 'image/jpeg', this.jpegQuality);
  }

  stop() {
    this.running = false;
    this.encoding = false;
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
    this.sendFrame = null;
    this.video.pause();
    this.video.srcObject = null;
  }

  destroy() {
    this.stop();
    this.canvas.width = 0;
    this.canvas.height = 0;
  }
}
