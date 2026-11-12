export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  danger = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onCancel}>
      <div
        role="dialog"
        aria-modal="true"
        className="w-full max-w-sm rounded border border-border-strong bg-surface p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-medium text-ink-primary">{title}</h3>
        <p className="mt-2 text-sm text-ink-secondary">{description}</p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded border border-border-strong px-3 py-1.5 text-sm text-ink-primary hover:bg-raised"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`rounded px-3 py-1.5 text-sm font-medium ${
              danger ? "bg-danger text-canvas hover:bg-danger/90" : "bg-signal text-canvas hover:bg-signal/90"
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
