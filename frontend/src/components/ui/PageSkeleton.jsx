function ShimmerRow({ w = '100%', h = 16, rounded = 8 }) {
  return (
    <div style={{
      width: w, height: h, borderRadius: rounded,
      background: 'linear-gradient(90deg, #f1f5f9 25%, #e2e8f0 50%, #f1f5f9 75%)',
      backgroundSize: '400% 100%',
      animation: 'pe-shimmer 1.4s ease-in-out infinite',
    }} />
  );
}

function InboxSkeleton() {
  return (
    <div className="flex h-full overflow-hidden">
      {/* conversation list */}
      <div className="w-72 border-r border-slate-100 p-3 space-y-2.5 flex-shrink-0">
        {Array.from({ length: 7 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3 p-2">
            <div style={{ width: 38, height: 38, borderRadius: '50%', flexShrink: 0, background: '#e2e8f0' }} />
            <div className="flex-1 space-y-1.5">
              <ShimmerRow w="70%" h={12} />
              <ShimmerRow w="50%" h={10} />
            </div>
          </div>
        ))}
      </div>
      {/* message area */}
      <div className="flex-1 flex flex-col gap-4 p-5">
        {[60, 45, 75, 50].map((w, i) => (
          <div key={i} className={`flex ${i % 2 === 0 ? '' : 'justify-end'} gap-2.5`}>
            {i % 2 === 0 && <div style={{ width: 30, height: 30, borderRadius: '50%', flexShrink: 0, background: '#e2e8f0' }} />}
            <ShimmerRow w={`${w}%`} h={38} rounded={12} />
          </div>
        ))}
      </div>
    </div>
  );
}

function SettingsSkeleton() {
  return (
    <div className="p-6 space-y-5 max-w-xl">
      <ShimmerRow w="35%" h={22} />
      <div className="space-y-3">
        {[80, 65, 90, 55].map((w, i) => <ShimmerRow key={i} w={`${w}%`} h={36} rounded={8} />)}
      </div>
      <ShimmerRow w="28%" h={36} rounded={8} />
    </div>
  );
}

function GenericSkeleton() {
  return (
    <div className="p-6 space-y-4">
      <ShimmerRow w="40%" h={24} />
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="rounded-2xl border border-slate-100 bg-white p-4 space-y-3 shadow-sm">
            <div className="flex items-center gap-3">
              <div style={{ width: 38, height: 38, borderRadius: '50%', background: '#e2e8f0' }} />
              <div className="flex-1 space-y-1.5">
                <ShimmerRow w="75%" h={12} />
                <ShimmerRow w="55%" h={10} />
              </div>
            </div>
            <ShimmerRow h={10} />
            <ShimmerRow w="65%" h={10} />
          </div>
        ))}
      </div>
    </div>
  );
}

export default function PageSkeleton({ variant = 'generic' }) {
  return (
    <>
      <style>{`
        @keyframes pe-shimmer {
          0% { background-position: -400% 0; }
          100% { background-position: 400% 0; }
        }
      `}</style>
      {variant === 'inbox' && <InboxSkeleton />}
      {variant === 'settings' && <SettingsSkeleton />}
      {variant === 'generic' && <GenericSkeleton />}
    </>
  );
}
