const EMOTION_LABELS_ZH = new Map([
  ['neutral', '中性'],
  ['happy', '开心'],
  ['sad', '悲伤'],
  ['angry', '愤怒'],
  ['surprise', '惊讶'],
  ['disgust', '厌恶'],
]);

function emotionLabel(emotions) {
  if (!Array.isArray(emotions) || !emotions.length) return '';
  return emotions
    .map((emotion) => EMOTION_LABELS_ZH.get(emotion) || emotion)
    .join('或');
}

function createValueWindow(title) {
  const element = document.createElement('div');
  Object.assign(element.style, {
    boxSizing: 'border-box',
    minWidth: '112px',
    minHeight: '54px',
    padding: '7px 10px',
    overflow: 'hidden',
    border: '1px solid rgba(255, 255, 255, 0.45)',
    borderRadius: '8px',
    background: 'rgba(17, 24, 39, 0.82)',
    color: '#ffffff',
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    textAlign: 'center',
    boxShadow: '0 2px 8px rgba(0, 0, 0, 0.28)',
  });

  const label = document.createElement('div');
  label.textContent = title;
  Object.assign(label.style, {
    color: 'rgba(255, 255, 255, 0.66)',
    fontSize: '11px',
    lineHeight: '14px',
    whiteSpace: 'nowrap',
  });

  const value = document.createElement('div');
  Object.assign(value.style, {
    minHeight: '23px',
    fontSize: '15px',
    fontWeight: '600',
    lineHeight: '23px',
    whiteSpace: 'nowrap',
  });
  element.append(label, value);
  return { element, value };
}

export class EmotionDisplay {
  constructor() {
    this.active = false;
    this.debugMode = false;
    this.latestAuDeltas = null;
    this.lastAuRenderAt = 0;
    this.container = document.createElement('div');
    this.container.setAttribute('aria-live', 'polite');
    Object.assign(this.container.style, {
      position: 'fixed',
      right: '12px',
      top: '50%',
      transform: 'translateY(-50%)',
      zIndex: '2147483646',
      display: 'none',
      flexDirection: 'column',
      gap: '6px',
      pointerEvents: 'none',
      userSelect: 'none',
    });

    const liveWindow = createValueWindow('当前表情');
    const finalWindow = createValueWindow('本轮表情');
    this.liveValue = liveWindow.value;
    this.finalValue = finalWindow.value;
    this.container.append(liveWindow.element, finalWindow.element);

    this.auPanel = document.createElement('div');
    Object.assign(this.auPanel.style, {
      position: 'fixed',
      top: '38px',
      left: '50%',
      transform: 'translateX(-50%)',
      zIndex: '2147483646',
      boxSizing: 'border-box',
      display: 'none',
      width: 'min(760px, calc(100vw - 48px))',
      height: '118px',
      padding: '8px 10px 7px',
      overflowX: 'auto',
      overflowY: 'hidden',
      border: '1px solid rgba(255, 255, 255, 0.38)',
      borderRadius: '9px',
      background: 'rgba(17, 24, 39, 0.84)',
      boxShadow: '0 2px 9px rgba(0, 0, 0, 0.28)',
      pointerEvents: 'none',
      userSelect: 'none',
    });
    this.auChart = document.createElement('div');
    Object.assign(this.auChart.style, {
      display: 'flex',
      alignItems: 'stretch',
      gap: '4px',
      minWidth: '100%',
      height: '100%',
    });
    this.auPanel.appendChild(this.auChart);

    this.mask = document.createElement('div');
    this.mask.setAttribute('aria-live', 'assertive');
    Object.assign(this.mask.style, {
      position: 'fixed',
      inset: '0',
      zIndex: '2147483647',
      display: 'none',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '24px',
      background: 'rgba(3, 7, 18, 0.76)',
      color: '#ffffff',
      fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      fontSize: 'clamp(52px, 12vw, 104px)',
      fontWeight: '700',
      textAlign: 'center',
      pointerEvents: 'none',
      userSelect: 'none',
    });

    (document.body || document.documentElement).append(
      this.auPanel,
      this.container,
      this.mask,
    );
  }

  show() {
    this.active = true;
    this.updateDebugVisibility();
  }

  hide() {
    this.active = false;
    this.container.style.display = 'none';
    this.auPanel.style.display = 'none';
    this.mask.style.display = 'none';
  }

  setDebugMode(enabled) {
    this.debugMode = enabled === true;
    this.updateDebugVisibility();
    if (this.debugMode && this.active) this.renderAuDeltas(true);
  }

  updateDebugVisibility() {
    const visible = this.active && this.debugMode;
    this.container.style.display = visible ? 'flex' : 'none';
    this.auPanel.style.display = visible ? 'block' : 'none';
  }

  setLive(emotions) {
    this.liveValue.textContent = emotionLabel(emotions);
  }

  setFinal(emotions) {
    this.finalValue.textContent = emotionLabel(emotions);
  }

  setAuDeltas(items) {
    this.latestAuDeltas = Array.isArray(items) ? items : null;
    if (!this.active || !this.debugMode) return;
    this.renderAuDeltas(false);
  }

  renderAuDeltas(force) {
    const now = performance.now();
    if (!force && now - this.lastAuRenderAt < 100) return;
    this.lastAuRenderAt = now;
    this.auChart.replaceChildren();
    const items = this.latestAuDeltas;
    if (!Array.isArray(items) || !items.length) return;
    for (const item of items) {
      const delta = Number(item.delta);
      if (!Number.isFinite(delta)) continue;

      const column = document.createElement('div');
      Object.assign(column.style, {
        position: 'relative',
        flex: '1 0 30px',
        minWidth: '30px',
        height: '100%',
      });

      const plot = document.createElement('div');
      Object.assign(plot.style, {
        position: 'absolute',
        inset: '0 0 18px',
        borderBottom: '1px solid rgba(255, 255, 255, 0.12)',
      });

      const zero = document.createElement('div');
      Object.assign(zero.style, {
        position: 'absolute',
        left: '1px',
        right: '1px',
        top: '50%',
        height: '1px',
        background: 'rgba(255, 255, 255, 0.32)',
      });

      const magnitude = Math.min(1, Math.abs(delta));
      const bar = document.createElement('div');
      Object.assign(bar.style, {
        position: 'absolute',
        left: '7px',
        right: '7px',
        height: `${Math.max(1, magnitude * 50)}%`,
        borderRadius: delta >= 0 ? '3px 3px 0 0' : '0 0 3px 3px',
        background: delta >= 0 ? '#34d399' : '#fb7185',
        opacity: magnitude < 0.002 ? '0.28' : '0.92',
        ...(delta >= 0 ? { bottom: '50%' } : { top: '50%' }),
      });

      const value = document.createElement('div');
      value.textContent = `${delta >= 0 ? '+' : ''}${delta.toFixed(2)}`;
      Object.assign(value.style, {
        position: 'absolute',
        left: '50%',
        top: delta >= 0 ? '2px' : 'calc(50% + 2px)',
        transform: 'translateX(-50%)',
        color: delta >= 0 ? '#6ee7b7' : '#fda4af',
        font: '9px/11px ui-monospace, SFMono-Regular, Menlo, monospace',
        whiteSpace: 'nowrap',
      });

      const label = document.createElement('div');
      label.textContent = item.au;
      Object.assign(label.style, {
        position: 'absolute',
        left: '0',
        right: '0',
        bottom: '0',
        color: 'rgba(255, 255, 255, 0.78)',
        font: '10px/15px system-ui, sans-serif',
        textAlign: 'center',
      });

      plot.append(zero, bar, value);
      column.append(plot, label);
      this.auChart.appendChild(column);
    }
  }

  setCalibrationState(detail) {
    if (detail?.state === 'countdown') {
      this.mask.style.display = 'flex';
      this.mask.style.fontSize = 'clamp(52px, 12vw, 104px)';
      this.mask.textContent = String(detail.remaining || 1);
      return;
    }
    if (detail?.state === 'waiting_for_face') {
      this.mask.style.display = 'flex';
      this.mask.style.fontSize = 'clamp(20px, 4vw, 34px)';
      this.mask.textContent = '请正对摄像头，正在采集表情基线…';
      return;
    }
    if (detail?.state === 'connection_error') {
      this.mask.style.display = 'flex';
      this.mask.style.fontSize = 'clamp(18px, 3vw, 28px)';
      this.mask.textContent = detail.message || '表情识别服务连接失败';
      return;
    }
    this.mask.style.display = 'none';
    this.mask.textContent = '';
  }

  clear() {
    this.setLive(null);
    this.setFinal(null);
    this.latestAuDeltas = null;
    this.setAuDeltas(null);
    this.setCalibrationState({ state: 'idle' });
  }

  destroy() {
    this.clear();
    this.auPanel.remove();
    this.container.remove();
    this.mask.remove();
  }
}
