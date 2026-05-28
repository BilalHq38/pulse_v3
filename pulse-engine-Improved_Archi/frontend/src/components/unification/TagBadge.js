import { AlertTriangle, Bot, Link2, ShieldCheck, Sparkles, UserCog } from 'lucide-react';

const TAG_META = {
  review_required: {
    label: 'Review Required',
    className: 'bg-amber-100 text-amber-700 border-amber-200',
    Icon: AlertTriangle,
  },
  manual_merge: {
    label: 'Manual Merge',
    className: 'bg-sky-100 text-sky-700 border-sky-200',
    Icon: UserCog,
  },
  deterministic: {
    label: 'Deterministic',
    className: 'bg-emerald-100 text-emerald-700 border-emerald-200',
    Icon: ShieldCheck,
  },
  probabilistic: {
    label: 'Probabilistic',
    className: 'bg-orange-100 text-orange-700 border-orange-200',
    Icon: Link2,
  },
  ai_match: {
    label: 'AI Match',
    className: 'bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200',
    Icon: Bot,
  },
  unknown: {
    label: 'Identity Signal',
    className: 'bg-slate-100 text-slate-600 border-slate-200',
    Icon: Sparkles,
  },
};

export default function TagBadge({ kind = 'unknown', label = '', compact = false }) {
  const meta = TAG_META[kind] || TAG_META.unknown;
  const Icon = meta.Icon;
  const text = String(label || meta.label || 'Tag');

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[10px] font-semibold tracking-wide ${meta.className}`}
      title={text}
    >
      <Icon size={compact ? 10 : 11} />
      <span>{text}</span>
    </span>
  );
}
