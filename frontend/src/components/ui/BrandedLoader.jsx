export default function BrandedLoader() {
  return (
    <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center gap-5">
      <div style={{
        width: 52,
        height: 52,
        borderRadius: '50%',
        background: 'linear-gradient(135deg, #2563eb 0%, #7c3aed 100%)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        boxShadow: '0 8px 32px rgba(37,99,235,0.28)',
        animation: 'pe-loader-pulse 1.8s ease-in-out infinite',
      }}>
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="4" fill="white" />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3" stroke="white" strokeWidth="2" strokeLinecap="round" opacity="0.6" />
        </svg>
      </div>
      <div style={{ width: 160, height: 3, borderRadius: 99, background: '#e2e8f0', overflow: 'hidden' }}>
        <div style={{
          height: '100%',
          borderRadius: 99,
          background: 'linear-gradient(90deg, #2563eb, #7c3aed)',
          animation: 'pe-loader-bar 1.6s ease-in-out infinite',
        }} />
      </div>
      <p style={{ fontSize: 12, color: '#94a3b8', letterSpacing: '0.04em', fontWeight: 500 }}>
        Loading Pulse Engine
      </p>
      <style>{`
        @keyframes pe-loader-pulse {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.07); }
        }
        @keyframes pe-loader-bar {
          0% { width: 0%; margin-left: 0; }
          50% { width: 70%; margin-left: 15%; }
          100% { width: 0%; margin-left: 100%; }
        }
      `}</style>
    </div>
  );
}
