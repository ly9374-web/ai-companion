export class JpegFramePump {
  constructor({ fps = 12, jpegQuality = 0.7, maxBufferedBytes = 65536 } = {}) {
    this.frameIntervalMs = 1000 / Math.max(1, fps);
    this.jpegQuality = jpegQuality;
    this.maxBufferedBytes = maxBufferedBytes;
    this.video = document.createElement('video');
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.autoplay = true;
    this.canvas = document.createElement('canvas');
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
    this.schedule(0);
  }

  schedule(delay = this.frameIntervalMs) {
    if (!this.running) return;
    window.clearTimeout(this.timer);
    this.timer = window.setTimeout(() => this.tick(), delay);
  }

  tick() {
    if (!this.running) return;
    if (this.encoding || this.video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
      this.schedule();
      return;
    }

    const width = this.video.videoWidth;
    const height = this.video.videoHeight;
    if (!width || !height) {
      this.schedule();
      return;
    }

    this.canvas.width = width;
    this.canvas.height = height;
    this.context.drawImage(this.video, 0, 0, width, height);
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
      this.schedule();
    }, 'image/jpeg', this.jpegQuality);
  }

  stop() {
    this.running = false;
    this.encoding = false;
    window.clearTimeout(this.timer);
    this.timer = null;
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
