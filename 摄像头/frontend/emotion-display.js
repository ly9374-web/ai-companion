const EMOTION_LABELS_ZH = new Map([
  ['neutral', '中性'],
  ['happy', '开心'],
  ['sad', '悲伤'],
  ['angry', '愤怒'],
  ['surprise', '惊讶'],
]);

function emotionLabel(emotions) {
  if (!Array.isArray(emotions) || !emotions.length) return '';
  return emotions
    .map((emotion) => EMOTION_LABELS_ZH.get(emotion) || emotion)
    .join('或');
}

function emotionAggregateLabel(emotions) {
  if (!Array.isArray(emotions) || !emotions.length) return '';
  const labels = emotions
    .map((emotion) => EMOTION_LABELS_ZH.get(emotion) || emotion);
  return labels.length === 1 ? labels[0] : `先${labels[0]}转为${labels[1]}`;
}

function emotionSequenceLabel(sequence) {
  if (!Array.isArray(sequence) || !sequence.length) return '';
  const labels = sequence.map((emotions) => emotionLabel(emotions)).filter(Boolean);
  if (!labels.length) return '';
  return labels.length === 1 ? labels[0] : `先${labels.join('转为')}`;
}

// ===== 心率折线图（移植自 emotion_camera算法支持/static/index.html） =====
const HEART_RATE_WINDOW_MS = 15000;       // 15 秒滚动窗口
const HEART_RATE_SAMPLE_INTERVAL = 333;   // 约 3 点/秒
const HEART_RATE_MIN_RANGE = 50;
const HEART_RATE_MAX_RANGE = 120;
// AU 面板宽 760px 居中；心率面板需要在其右侧再放下一块 200px 面板所需的最小视口宽度。
const HEART_RATE_PANEL_MIN_VIEWPORT = 1208;

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
    this.heartRateSamples = [];          // [{ t, bpm }]，bpm 为 null 表示断点
    this.heartRateSampler = null;        // setInterval 句柄
    this.latestHeartRate = null;         // { bpm, at } 最近一次有效心率
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

    // 心率面板：与 AU 面板同高，位于其右侧。
    this.hrPanel = document.createElement('div');
    Object.assign(this.hrPanel.style, {
      position: 'fixed',
      top: '38px',
      left: 'calc(50% + 392px)',
      zIndex: '2147483646',
      boxSizing: 'border-box',
      display: 'none',
      flexDirection: 'column',
      width: '200px',
      height: '118px',
      padding: '7px 8px 6px 6px',
      overflow: 'hidden',
      border: '1px solid rgba(255, 255, 255, 0.38)',
      borderRadius: '9px',
      background: 'rgba(17, 24, 39, 0.84)',
      boxShadow: '0 2px 9px rgba(0, 0, 0, 0.28)',
      pointerEvents: 'none',
      userSelect: 'none',
    });
    const hrTitle = document.createElement('div');
    hrTitle.textContent = '心率';
    Object.assign(hrTitle.style, {
      color: 'rgba(255, 255, 255, 0.66)',
      fontSize: '11px',
      lineHeight: '14px',
      marginBottom: '2px',
      whiteSpace: 'nowrap',
    });
    this.hrCanvas = document.createElement('canvas');
    Object.assign(this.hrCanvas.style, {
      display: 'block',
      width: '100%',
      flex: '1',
      minHeight: '0',
    });
    this.hrPanel.append(hrTitle, this.hrCanvas);

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
      this.hrPanel,
      this.container,
      this.mask,
    );

    this.handleResize = () => this.updateHeartRateLayout();
    window.addEventListener('resize', this.handleResize);
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
    this.updateHeartRateLayout();
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
    this.updateHeartRateLayout();
  }

  // ===== 心率图 =====

  isHeartRatePanelVisible() {
    return this.active && this.debugMode
      && window.innerWidth >= HEART_RATE_PANEL_MIN_VIEWPORT;
  }

  updateHeartRateLayout() {
    if (this.isHeartRatePanelVisible()) {
      this.hrPanel.style.display = 'flex';
      if (this.heartRateSampler === null) this.startHeartRateSampling();
      this.renderHeartRateChart();
    } else {
      this.hrPanel.style.display = 'none';
      if (this.heartRateSampler !== null) this.stopHeartRateSampling();
    }
  }

  setHeartRate(bpm) {
    const value = Number(bpm);
    if (!Number.isFinite(value)) return;
    this.latestHeartRate = { bpm: value, at: performance.now() };
    if (this.heartRateSampler !== null) this.renderHeartRateChart();
  }

  startHeartRateSampling() {
    this.stopHeartRateSampling();
    this.heartRateSampler = window.setInterval(() => {
      const now = performance.now();
      // 一旦出现过有效心率，就持续沿用最后一次已知值，直到下一次
      // setHeartRate 带来新值；不再因短期无更新而主动插入断点。
      if (this.latestHeartRate) {
        this.heartRateSamples.push({ t: now, bpm: this.latestHeartRate.bpm });
      } else {
        this.heartRateSamples.push({ t: now, bpm: null });
      }
      if (this.heartRateSamples.length > 200) this.heartRateSamples.shift();
      if (this.isHeartRatePanelVisible()) this.renderHeartRateChart();
    }, HEART_RATE_SAMPLE_INTERVAL);
  }

  stopHeartRateSampling() {
    if (this.heartRateSampler !== null) {
      window.clearInterval(this.heartRateSampler);
      this.heartRateSampler = null;
    }
    this.heartRateSamples = [];
    this.latestHeartRate = null;
  }

  renderHeartRateChart() {
    const canvas = this.hrCanvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 184;
    const cssH = canvas.clientHeight || 80;
    const wantW = Math.round(cssW * dpr);
    const wantH = Math.round(cssH * dpr);
    if (canvas.width !== wantW || canvas.height !== wantH) {
      canvas.width = wantW;
      canvas.height = wantH;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const padding = { left: 26, right: 10, top: 8, bottom: 16 };
    const plotW = Math.max(10, cssW - padding.left - padding.right);
    const plotH = Math.max(10, cssH - padding.top - padding.bottom);

    const now = performance.now();
    const tMin = now - HEART_RATE_WINDOW_MS;
    this.heartRateSamples = this.heartRateSamples.filter((s) => s.t >= tMin);

    // y 轴刻度 + 水平网格
    ctx.font = '10px ui-sans-serif, system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'right';
    const yTicks = [HEART_RATE_MIN_RANGE, 80, 100, HEART_RATE_MAX_RANGE]; // [50, 80, 100, 120]
    for (const yVal of yTicks) {
      const py = padding.top + plotH - (yVal - HEART_RATE_MIN_RANGE)
        / (HEART_RATE_MAX_RANGE - HEART_RATE_MIN_RANGE) * plotH;
      ctx.strokeStyle = 'rgba(255,255,255,0.07)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padding.left, py);
      ctx.lineTo(padding.left + plotW, py);
      ctx.stroke();
      ctx.fillStyle = '#8995a8';
      ctx.fillText(String(yVal), padding.left - 4, py);
    }

    // x 轴时间刻度
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const xTicks = [
      { label: '-15s', t: tMin },
      { label: '-10s', t: now - 10000 },
      { label: '-5s', t: now - 5000 },
      { label: 'now', t: now },
    ];
    for (const tick of xTicks) {
      const px = padding.left + (tick.t - tMin) / HEART_RATE_WINDOW_MS * plotW;
      ctx.fillStyle = '#8995a8';
      ctx.fillText(tick.label, px, padding.top + plotH + 4);
    }

    // 采样点映射到坐标（超范围/断点记为 null）
    const points = [];
    for (const s of this.heartRateSamples) {
      if (s.bpm === null || s.bpm < HEART_RATE_MIN_RANGE
        || s.bpm > HEART_RATE_MAX_RANGE) {
        points.push(null);
        continue;
      }
      const px = padding.left + (s.t - tMin) / HEART_RATE_WINDOW_MS * plotW;
      const py = padding.top + plotH - (s.bpm - HEART_RATE_MIN_RANGE)
        / (HEART_RATE_MAX_RANGE - HEART_RATE_MIN_RANGE) * plotH;
      points.push({ x: px, y: py, bpm: s.bpm });
    }

    // 折线（遇 null 断开）
    ctx.strokeStyle = '#66e2bc';
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    let pen = false;
    for (const p of points) {
      if (!p) {
        pen = false;
        continue;
      }
      if (!pen) {
        ctx.moveTo(p.x, p.y);
        pen = true;
      } else {
        ctx.lineTo(p.x, p.y);
      }
    }
    ctx.stroke();

    // 最新点圆点 + 数值标注
    let last = null;
    for (let i = points.length - 1; i >= 0; i -= 1) {
      if (points[i]) {
        last = points[i];
        break;
      }
    }
    if (last) {
      ctx.fillStyle = '#66e2bc';
      ctx.beginPath();
      ctx.arc(last.x, last.y, 3, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#f7f9fc';
      ctx.font = '600 13px ui-sans-serif, system-ui, sans-serif';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'top';
      ctx.fillText(`${Math.round(last.bpm)} BPM`, padding.left + plotW - 2, padding.top + 2);
    } else {
      ctx.fillStyle = 'rgba(255,255,255,0.55)';
      ctx.font = '11px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('等待心率数据…', padding.left + plotW / 2, padding.top + plotH / 2);
    }
  }

  setLive(emotions) {
    this.liveValue.textContent = emotionLabel(emotions);
  }

  // 根据当前段持续时长与阈值比较，更新实时表情文字颜色：
  // 达到阈值（最终会被带入对话）变绿，未达到变白。
  setSegmentState(state) {
    if (!state) {
      this.liveValue.style.color = '#ffffff';
      return;
    }
    this.liveValue.style.color = state.durationMs >= state.thresholdMs
      ? '#66e2bc'
      : '#ffffff';
  }

  setFinal(emotions) {
    this.finalValue.textContent = emotionAggregateLabel(emotions);
  }

  setFinalSequence(sequence) {
    this.finalValue.textContent = emotionSequenceLabel(sequence);
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
    this.setSegmentState(null);
    this.setFinal(null);
    this.latestAuDeltas = null;
    this.setAuDeltas(null);
    this.heartRateSamples = [];
    this.latestHeartRate = null;
    this.setCalibrationState({ state: 'idle' });
    if (this.heartRateSampler !== null) this.renderHeartRateChart();
  }

  destroy() {
    this.clear();
    window.removeEventListener('resize', this.handleResize);
    this.stopHeartRateSampling();
    this.auPanel.remove();
    this.hrPanel.remove();
    this.container.remove();
    this.mask.remove();
  }
}
