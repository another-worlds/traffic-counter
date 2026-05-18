import React from 'react';
import ReactDOM from 'react-dom/client';
import { Streamlit, type RenderData } from 'streamlit-component-lib';
import Uploader from './Uploader';

type UploaderArgs = {
  projectId?: string;
  tusEndpoint?: string;
};

const rootElement = document.getElementById('root')!;

function UploaderRoot() {
  const [args, setArgs] = React.useState<UploaderArgs>({});

  React.useEffect(() => {
    function onRender(event: Event) {
      const detail = (event as CustomEvent<RenderData>).detail;
      setArgs((detail?.args as UploaderArgs | undefined) ?? {});
      Streamlit.setFrameHeight(200);
    }
    Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, onRender);
    Streamlit.setComponentReady();
    return () => Streamlit.events.removeEventListener(Streamlit.RENDER_EVENT, onRender);
  }, []);

  return (
    <Uploader
      projectId={args.projectId ?? ''}
      tusEndpoint={args.tusEndpoint ?? ''}
      onResult={(result) => Streamlit.setComponentValue(result)}
    />
  );
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <UploaderRoot />
  </React.StrictMode>,
);
