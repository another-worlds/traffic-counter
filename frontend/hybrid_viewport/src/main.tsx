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

  React.useEffect(() => {
    function handleStreamlitRender(event: Event) {
      const customEvent = event as CustomEvent<RenderData>;
      const args = (customEvent.detail?.args as StreamlitRenderArgs | undefined) ?? {};
      if (!args.bootstrap) {
        return;
      }
      setBootstrap(args.bootstrap);
      window.__TRAFFIC_COUNTER_HYBRID_VIEWPORT__ = args.bootstrap;
      Streamlit.setFrameHeight(940);
    }

    Streamlit.setComponentReady();
    window.addEventListener(Streamlit.RENDER_EVENT, handleStreamlitRender);
    return () => window.removeEventListener(Streamlit.RENDER_EVENT, handleStreamlitRender);
  }, []);

  const spec = buildViewportSpecFromBootstrap(bootstrap);
  const initialLines = buildInitialLinesFromBootstrap(bootstrap);

  return <App spec={spec} initialLines={initialLines} onSnapshot={(payload) => Streamlit.setComponentValue(payload)} />;
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <OverlayRoot />
  </React.StrictMode>,
);
