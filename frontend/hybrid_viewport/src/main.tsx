import React from 'react';
import ReactDOM from 'react-dom/client';
import { Streamlit, type RenderData } from 'streamlit-component-lib';
import App from './App';
import { buildInitialLinesFromBootstrap, buildViewportSpecFromBootstrap, type HostViewportBootstrap } from './viewportState';

declare global {
  interface Window {
    __TRAFFIC_COUNTER_HYBRID_VIEWPORT__?: HostViewportBootstrap;
  }
}

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Missing root element for hybrid viewport');
}

type HostBridgeMessage = {
  source?: string;
  payload?: HostViewportBootstrap;
};

type StreamlitRenderArgs = {
  bootstrap?: HostViewportBootstrap;
};

function OverlayRoot() {
  const [bootstrap, setBootstrap] = React.useState<HostViewportBootstrap>(() => window.__TRAFFIC_COUNTER_HYBRID_VIEWPORT__ ?? {});

  // Stable reference so the App's onSnapshot effect only fires when bridgePayload changes,
  // not on every render cycle.
  const handleSnapshot = React.useCallback(
    (payload: ReturnType<typeof import('./viewportState').buildBridgePayload>) => Streamlit.setComponentValue(payload),
    [],
  );

  React.useEffect(() => {
    function handleStreamlitRender(event: Event) {
      const customEvent = event as CustomEvent<RenderData>;
      const args = (customEvent.detail?.args as StreamlitRenderArgs | undefined) ?? {};
      // Always notify Streamlit of the frame height so the iframe is never collapsed.
      Streamlit.setFrameHeight(940);
      if (!args.bootstrap) {
        return;
      }
      setBootstrap(args.bootstrap);
      window.__TRAFFIC_COUNTER_HYBRID_VIEWPORT__ = args.bootstrap;
    }

    // Register the render listener BEFORE signalling readiness so the first render
    // event (which Streamlit fires synchronously after receiving setComponentReady)
    // is never missed.
    window.addEventListener(Streamlit.RENDER_EVENT, handleStreamlitRender);
    Streamlit.setComponentReady();
    return () => window.removeEventListener(Streamlit.RENDER_EVENT, handleStreamlitRender);
  }, []);

  const spec = buildViewportSpecFromBootstrap(bootstrap);
  const initialLines = buildInitialLinesFromBootstrap(bootstrap);

  return <App spec={spec} initialLines={initialLines} onSnapshot={handleSnapshot} />;
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <OverlayRoot />
  </React.StrictMode>,
);
