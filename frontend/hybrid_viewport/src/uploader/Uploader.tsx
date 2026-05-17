import React from 'react';
import * as tus from 'tus-js-client';

type UploaderResult =
  | { kind: 'progress'; pct: number; uploaded: number; total: number }
  | { kind: 'complete'; videoId: string; filename: string }
  | { kind: 'error'; message: string };

type Props = {
  projectId: string;
  tusEndpoint: string;
  onResult: (r: UploaderResult) => void;
};

type State =
  | { phase: 'idle' }
  | { phase: 'uploading'; filename: string; pct: number; uploaded: number; total: number }
  | { phase: 'done'; filename: string; videoId: string }
  | { phase: 'error'; message: string };

export default function Uploader({ projectId, tusEndpoint, onResult }: Props) {
  const [state, setState] = React.useState<State>({ phase: 'idle' });
  const uploadRef = React.useRef<tus.Upload | null>(null);
  // Debounce progress reports to ~2 Hz (Streamlit reruns are expensive).
  const lastReportRef = React.useRef(0);

  function startUpload(file: File) {
    if (!tusEndpoint || !projectId) return;

    setState({ phase: 'uploading', filename: file.name, pct: 0, uploaded: 0, total: file.size });

    let capturedVideoId = '';

    const upload = new tus.Upload(file, {
      endpoint: tusEndpoint,
      chunkSize: 64 * 1024 * 1024, // 64 MB per chunk
      retryDelays: [0, 1000, 3000, 5000, 10000],
      metadata: {
        filename: encodeURIComponent(file.name),
        filetype: file.type || 'video/mp4',
        project_id: projectId,
      },

      // Capture the video ID from the POST (creation) response header.
      onAfterResponse(_req, res) {
        const vid = res.getHeader('Traffic-Counter-Video-Id');
        if (vid) capturedVideoId = vid;
      },

      onProgress(uploaded: number, total: number) {
        const pct = total > 0 ? uploaded / total : 0;
        setState({ phase: 'uploading', filename: file.name, pct, uploaded, total });

        const now = Date.now();
        if (now - lastReportRef.current > 500) {
          lastReportRef.current = now;
          onResult({ kind: 'progress', pct, uploaded, total });
        }
      },

      onSuccess() {
        const filename = file.name;
        setState({ phase: 'done', filename, videoId: capturedVideoId });
        onResult({ kind: 'complete', videoId: capturedVideoId, filename });
      },

      onError(err: tus.DetailedError | Error) {
        const message = err instanceof Error ? err.message : String(err);
        setState({ phase: 'error', message });
        onResult({ kind: 'error', message });
      },
    });

    uploadRef.current = upload;
    upload.start();
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) startUpload(file);
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) startUpload(file);
  }

  const styles: Record<string, React.CSSProperties> = {
    shell: {
      fontFamily: "Inter, 'Segoe UI', system-ui, sans-serif",
      color: '#f4f7fb',
      padding: '12px',
    },
    dropzone: {
      border: '2px dashed rgba(78,205,196,0.4)',
      borderRadius: '12px',
      padding: '20px',
      textAlign: 'center',
      background: 'rgba(78,205,196,0.05)',
      cursor: 'pointer',
    },
    bar: {
      height: '8px',
      background: 'rgba(255,255,255,0.1)',
      borderRadius: '999px',
      overflow: 'hidden',
      marginTop: '8px',
    },
    fill: (pct: number): React.CSSProperties => ({
      height: '100%',
      width: `${Math.round(pct * 100)}%`,
      background: 'linear-gradient(90deg, #4ecdc4, #f7b731)',
      borderRadius: '999px',
      transition: 'width 0.3s ease',
    }),
    label: { fontSize: '13px', color: 'rgba(244,247,251,0.7)' },
    success: { color: '#4ecdc4', fontWeight: 600, fontSize: '14px' },
    error: { color: '#ff8a89', fontSize: '13px' },
  };

  if (!tusEndpoint || !projectId) {
    return (
      <div style={styles.shell}>
        <p style={styles.error}>Uploader not configured (missing tusEndpoint or projectId).</p>
      </div>
    );
  }

  return (
    <div style={styles.shell}>
      {state.phase === 'idle' && (
        <div
          style={styles.dropzone}
          onDrop={handleDrop}
          onDragOver={(e) => e.preventDefault()}
          onClick={() => document.getElementById('tus-file-input')?.click()}
        >
          <div style={{ fontSize: '28px', marginBottom: '6px' }}>🎬</div>
          <p style={{ margin: 0, fontSize: '14px' }}>
            Drop a video file here or <strong>click to browse</strong>
          </p>
          <p style={styles.label}>MP4, MOV, MKV, AVI — any size, resumable</p>
          <input
            id="tus-file-input"
            type="file"
            accept="video/mp4,video/quicktime,video/x-matroska,video/x-msvideo,.mp4,.mov,.mkv,.avi"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
        </div>
      )}

      {state.phase === 'uploading' && (
        <div>
          <p style={{ margin: '0 0 6px', fontSize: '14px', fontWeight: 600 }}>
            Uploading {state.filename}
          </p>
          <div style={styles.bar}>
            <div style={styles.fill(state.pct)} />
          </div>
          <p style={styles.label}>
            {(state.uploaded / 1e9).toFixed(2)} GB / {(state.total / 1e9).toFixed(2)} GB
            &nbsp;·&nbsp;{Math.round(state.pct * 100)}%
          </p>
        </div>
      )}

      {state.phase === 'done' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={styles.success}>✅ {state.filename} uploaded</span>
          <button
            style={{
              padding: '4px 10px',
              border: '1px solid rgba(255,255,255,0.2)',
              borderRadius: '8px',
              background: 'transparent',
              color: '#f4f7fb',
              cursor: 'pointer',
              fontSize: '12px',
            }}
            onClick={() => setState({ phase: 'idle' })}
          >
            Upload another
          </button>
        </div>
      )}

      {state.phase === 'error' && (
        <div>
          <p style={styles.error}>❌ Upload failed: {state.message}</p>
          <button
            style={{
              padding: '4px 10px',
              border: '1px solid rgba(226,75,74,0.4)',
              borderRadius: '8px',
              background: 'transparent',
              color: '#ff8a89',
              cursor: 'pointer',
              fontSize: '12px',
            }}
            onClick={() => setState({ phase: 'idle' })}
          >
            Retry
          </button>
        </div>
      )}
    </div>
  );
}
