function loadAndDecodeImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = 'async';
    image.onload = async () => {
      try {
        if (typeof image.decode === 'function') {
          await image.decode();
        }
      } catch (_error) {
        // onload already proves that the image is usable.
      }
      resolve(image);
    };
    image.onerror = () => reject(new Error(`Unable to load expression image: ${url}`));
    image.src = url;
  });
}

function waitForDisplayElements() {
  return new Promise((resolve, reject) => {
    const deadline = performance.now() + 10000;
    const inspect = () => {
      const wrapper = document.getElementById('live2d-internal-wrapper');
      const canvas = document.getElementById('canvas');
      if (wrapper && canvas) {
        resolve({ wrapper, canvas });
        return;
      }
      if (performance.now() >= deadline) {
        reject(new Error('Live2D display container is unavailable'));
        return;
      }
      requestAnimationFrame(inspect);
    };
    inspect();
  });
}

const LAYOUT_STORAGE_KEY = 'expression:image-layouts:v1';
const MIN_SCALE = 0.1;
const MAX_SCALE = 5;
const WHEEL_SCALE_STEP = 0.03;

function getLayoutKey(modelUrl) {
  if (!modelUrl) return 'default';
  try {
    return new URL(modelUrl, window.location.href).pathname;
  } catch (_error) {
    return modelUrl;
  }
}

function readLayouts() {
  try {
    const stored = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!stored) return {};
    const parsed = JSON.parse(stored);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (error) {
    console.warn('[Expression] Failed to read saved image layout:', error);
    return {};
  }
}

function readLayout(modelUrl) {
  return readLayouts()[getLayoutKey(modelUrl)] || { x: 0, y: 0, scale: 1 };
}

function saveLayout(modelUrl, layout) {
  try {
    const layouts = readLayouts();
    layouts[getLayoutKey(modelUrl)] = layout;
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layouts));
  } catch (error) {
    console.warn('[Expression] Failed to save image layout:', error);
  }
}

export async function createExpressionFeature(config = {}) {
  const emotionUrls = config.emotions || {};
  const entries = Object.entries(emotionUrls);
  if (!entries.length) {
    throw new Error('Expression image mapping is empty');
  }

  const loadedEntries = await Promise.all(
    entries.map(async ([emotion, url]) => [emotion, await loadAndDecodeImage(url)]),
  );
  const images = new Map(loadedEntries);

  const { wrapper, canvas } = await waitForDisplayElements();

  const transitionConfig = config.transition || {};
  const transitionEnabled = transitionConfig.enabled !== false;
  const durationMs = Number.isFinite(transitionConfig.duration_ms)
    ? Math.max(0, transitionConfig.duration_ms)
    : 160;
  const easing = typeof transitionConfig.easing === 'string'
    ? transitionConfig.easing
    : 'ease-out';
  const transitionValue = transitionEnabled && durationMs > 0
    ? `opacity ${durationMs}ms ${easing}`
    : 'none';

  const createDisplayLayer = (index) => {
    const layer = document.createElement('img');
    layer.id = `optional-expression-image-${index}`;
    layer.alt = '';
    Object.assign(layer.style, {
      position: 'absolute',
      inset: '0',
      width: '100%',
      height: '100%',
      objectFit: 'contain',
      objectPosition: 'center',
      display: 'block',
      pointerEvents: 'none',
      zIndex: '1',
      opacity: '0',
      transition: 'none',
      transformOrigin: 'center center',
      willChange: 'opacity, transform',
    });
    return layer;
  };

  const layers = [createDisplayLayer(0), createDisplayLayer(1)];
  const interactionLayer = document.createElement('div');
  interactionLayer.id = 'optional-expression-interaction-layer';
  interactionLayer.setAttribute('aria-hidden', 'true');
  Object.assign(interactionLayer.style, {
    position: 'absolute',
    inset: '0',
    zIndex: '2',
    pointerEvents: 'none',
    touchAction: 'none',
    cursor: 'default',
  });

  const originalCanvasVisibility = canvas.style.visibility;
  layers.forEach((layer) => wrapper.appendChild(layer));
  wrapper.appendChild(interactionLayer);
  canvas.style.visibility = 'hidden';

  let currentEmotion = null;
  let activeLayerIndex = 0;
  let transitionFrame = null;
  let cleanupTimer = null;
  let interaction = {
    modelUrl: '',
    pointerInteractive: false,
    scrollToResize: false,
  };
  let layout = readLayout(interaction.modelUrl);
  let dragState = null;

  const isPointOverImage = (clientX, clientY) => {
    const image = images.get(currentEmotion);
    const wrapperRect = wrapper.getBoundingClientRect();
    if (!image || wrapperRect.width === 0 || wrapperRect.height === 0) return false;

    const containScale = Math.min(
      wrapperRect.width / image.naturalWidth,
      wrapperRect.height / image.naturalHeight,
    );
    const displayedWidth = image.naturalWidth * containScale * layout.scale;
    const displayedHeight = image.naturalHeight * containScale * layout.scale;
    const centerX = wrapperRect.left + wrapperRect.width / 2 + layout.x;
    const centerY = wrapperRect.top + wrapperRect.height / 2 + layout.y;

    return clientX >= centerX - displayedWidth / 2
      && clientX <= centerX + displayedWidth / 2
      && clientY >= centerY - displayedHeight / 2
      && clientY <= centerY + displayedHeight / 2;
  };

  const applyLayout = () => {
    const transform = `translate3d(${layout.x}px, ${layout.y}px, 0) scale(${layout.scale})`;
    layers.forEach((layer) => {
      layer.style.transform = transform;
    });
  };

  const applyInteractionState = () => {
    const enabled = interaction.pointerInteractive || interaction.scrollToResize;
    interactionLayer.style.pointerEvents = enabled ? 'auto' : 'none';
    interactionLayer.style.cursor = 'default';
  };

  const handlePointerDown = (event) => {
    if (
      !interaction.pointerInteractive
      || event.button !== 0
      || !isPointOverImage(event.clientX, event.clientY)
    ) return;
    event.preventDefault();
    event.stopPropagation();
    dragState = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      layoutX: layout.x,
      layoutY: layout.y,
    };
    interactionLayer.setPointerCapture(event.pointerId);
    interactionLayer.style.cursor = 'grabbing';
  };

  const handlePointerMove = (event) => {
    if (!dragState || dragState.pointerId !== event.pointerId) {
      interactionLayer.style.cursor = interaction.pointerInteractive
        && isPointOverImage(event.clientX, event.clientY)
        ? 'grab'
        : 'default';
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    layout = {
      ...layout,
      x: dragState.layoutX + event.clientX - dragState.startX,
      y: dragState.layoutY + event.clientY - dragState.startY,
    };
    applyLayout();
  };

  const finishDrag = (event) => {
    if (!dragState || dragState.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    dragState = null;
    if (interactionLayer.hasPointerCapture(event.pointerId)) {
      interactionLayer.releasePointerCapture(event.pointerId);
    }
    interactionLayer.style.cursor = interaction.pointerInteractive ? 'grab' : 'default';
    saveLayout(interaction.modelUrl, layout);
  };

  const handleWheel = (event) => {
    if (
      !interaction.scrollToResize
      || !isPointOverImage(event.clientX, event.clientY)
    ) return;
    event.preventDefault();
    event.stopPropagation();
    const direction = event.deltaY > 0 ? -1 : 1;
    layout = {
      ...layout,
      scale: Math.max(
        MIN_SCALE,
        Math.min(MAX_SCALE, layout.scale + WHEEL_SCALE_STEP * direction),
      ),
    };
    applyLayout();
    saveLayout(interaction.modelUrl, layout);
  };

  interactionLayer.addEventListener('pointerdown', handlePointerDown);
  interactionLayer.addEventListener('pointermove', handlePointerMove);
  interactionLayer.addEventListener('pointerup', finishDrag);
  interactionLayer.addEventListener('pointercancel', finishDrag);
  interactionLayer.addEventListener('wheel', handleWheel, { passive: false });
  applyLayout();
  applyInteractionState();

  const cancelPendingTransition = () => {
    if (transitionFrame !== null) {
      cancelAnimationFrame(transitionFrame);
      transitionFrame = null;
    }
    if (cleanupTimer !== null) {
      clearTimeout(cleanupTimer);
      cleanupTimer = null;
    }
  };

  const setEmotion = (emotion) => {
    const image = images.get(emotion);
    if (!image || emotion === currentEmotion) return false;

    cancelPendingTransition();

    const visibleOpacities = layers.map((layer) => Number.parseFloat(
      getComputedStyle(layer).opacity || '0',
    ));
    const outgoingLayerIndex = visibleOpacities[0] >= visibleOpacities[1] ? 0 : 1;
    const outgoingLayer = layers[outgoingLayerIndex];
    const incomingLayerIndex = 1 - outgoingLayerIndex;
    const incomingLayer = layers[incomingLayerIndex];

    // Keep the more visible layer as the stable starting point when a previous
    // transition is interrupted. The newest emotion always wins and is never
    // queued, while the display never drops to a transparent frame.
    outgoingLayer.style.transition = 'none';
    outgoingLayer.style.opacity = outgoingLayer.src ? '1' : '0';
    incomingLayer.style.transition = 'none';
    incomingLayer.style.opacity = '0';
    incomingLayer.src = image.src;

    currentEmotion = emotion;
    activeLayerIndex = incomingLayerIndex;

    if (!transitionEnabled || durationMs === 0 || !outgoingLayer.src) {
      outgoingLayer.style.opacity = '0';
      incomingLayer.style.opacity = '1';
      return true;
    }

    // Start on the next frame so the browser paints the decoded incoming image
    // at opacity zero before both layers crossfade. At least one layer remains
    // visible throughout the transition.
    transitionFrame = requestAnimationFrame(() => {
      transitionFrame = null;
      outgoingLayer.style.transition = transitionValue;
      incomingLayer.style.transition = transitionValue;
      outgoingLayer.style.opacity = '0';
      incomingLayer.style.opacity = '1';

      cleanupTimer = window.setTimeout(() => {
        cleanupTimer = null;
        outgoingLayer.style.transition = 'none';
        outgoingLayer.style.opacity = '0';
        incomingLayer.style.transition = 'none';
        incomingLayer.style.opacity = '1';
      }, durationMs + 50);
    });
    return true;
  };

  const defaultEmotion = typeof config.default_emotion === 'string'
    ? config.default_emotion
    : '中性';
  if (!setEmotion(defaultEmotion)) {
    const firstEmotion = loadedEntries[0]?.[0];
    if (firstEmotion) setEmotion(firstEmotion);
  }

  return {
    setEmotion,
    configureInteraction(nextInteraction = {}) {
      const previousModelUrl = interaction.modelUrl;
      interaction = {
        ...interaction,
        ...nextInteraction,
      };
      if (interaction.modelUrl !== previousModelUrl) {
        layout = readLayout(interaction.modelUrl);
        applyLayout();
      }
      if (!interaction.pointerInteractive) {
        dragState = null;
      }
      applyInteractionState();
    },
    destroy() {
      cancelPendingTransition();
      interactionLayer.removeEventListener('pointerdown', handlePointerDown);
      interactionLayer.removeEventListener('pointermove', handlePointerMove);
      interactionLayer.removeEventListener('pointerup', finishDrag);
      interactionLayer.removeEventListener('pointercancel', finishDrag);
      interactionLayer.removeEventListener('wheel', handleWheel);
      interactionLayer.remove();
      layers.forEach((layer) => layer.remove());
      canvas.style.visibility = originalCanvasVisibility;
      images.clear();
    },
  };
}
