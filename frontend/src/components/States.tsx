export function EmptyState({ title, description }: { title: string; description?: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded border border-dashed border-border py-16 text-center">
      <p className="text-sm font-medium text-ink-primary">{title}</p>
      {description && <p className="mt-1 max-w-sm text-sm text-ink-secondary">{description}</p>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded border border-danger/30 bg-danger-dim/40 py-16 text-center">
      <p className="text-sm font-medium text-danger">Something went wrong</p>
      <p className="mt-1 max-w-sm text-sm text-ink-secondary">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-4 rounded border border-border-strong px-3 py-1.5 text-sm text-ink-primary hover:bg-raised"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-10 animate-pulse rounded bg-raised" />
      ))}
    </div>
  );
}

export function CardSkeleton() {
  return <div className="h-24 animate-pulse rounded border border-border bg-surface" />;
}
