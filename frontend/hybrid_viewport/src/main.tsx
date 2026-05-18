import React from 'react';
import ReactDOM from 'react-dom/client';
import { Streamlit, type RenderData } from 'streamlit-component-lib';
import App from './App';
import type { BridgePayload, HostViewportBootstrap } from './viewportState';

declare global {
  interface Window {
    __TRAFFIC_COUNTER_HYBRID_VIEWPORT__?: HostViewportBootstrap;
  }
}

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Missing root element for hybrid viewport');
}

type StreamlitRenderArgs = {
  bootstrap?: HostViewportBootstrap;
};

function OverlayRoot() {
  const [bootstrap, setBootstrap] = React.useState<HostViewportBootstrap>(
    () => window.__TRAFFIC_COUNTER_HYBRID_VIEWPORT__ ?? {},
  );

  // Stable reference so App's onSnapshot effect only fires when payload changes.
  const handleSnapshot = React.useCallback(
    (payload: BridgePayload) => Streamlit.setComponentValue(payload),
    [],
  );

  React.useEffect(() => {
    function updateHeight() {
      Streamlit.setFrameHeight(rootElement.getBoundingClientRect().height + 16);
    }

    function handleStreamlitRender(event: Event) {
      const customEvent = event as CustomEvent<RenderData>;
      const args = (customEvent.detail?.args as StreamlitRenderArgs | undefined) ?? {};
      if (!args.bootstrap) {
        return;
      }
      setBootstrap(args.bootstrap);
      window.__TRAFFIC_COUNTER_HYBRID_VIEWPORT__ = args.bootstrap;
    }

    Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, handleStreamlitRender);

    const observer = new ResizeObserver(updateHeight);
    observer.observe(rootElement);
    updateHeight();

    Streamlit.setComponentReady();
    return () => {
      Streamlit.events.removeEventListener(Streamlit.RENDER_EVENT, handleStreamlitRender);
      observer.disconnect();
    };
  }, []);

  return <App bootstrap={bootstrap} onSnapshot={handleSnapshot} />;
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <OverlayRoot />
  </React.StrictMode>,
);
