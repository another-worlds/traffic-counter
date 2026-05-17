import React from 'react';
import ReactDOM from 'react-dom/client';
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

function OverlayRoot() {
  const [bootstrap, setBootstrap] = React.useState<HostViewportBootstrap>(() => window.__TRAFFIC_COUNTER_HYBRID_VIEWPORT__ ?? {});

  React.useEffect(() => {
    function handleMessage(event: MessageEvent<HostBridgeMessage>) {
      if (!event.data || event.data.source !== 'traffic-counter-host-shell' || !event.data.payload) {
        return;
      }
      setBootstrap(event.data.payload);
    }

    window.addEventListener('message', handleMessage as EventListener);
    return () => window.removeEventListener('message', handleMessage as EventListener);
  }, []);

  const spec = buildViewportSpecFromBootstrap(bootstrap);
  const initialLines = buildInitialLinesFromBootstrap(bootstrap);

  return <App spec={spec} initialLines={initialLines} />;
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <OverlayRoot />
  </React.StrictMode>,
);
