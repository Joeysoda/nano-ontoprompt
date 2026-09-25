import type { ReactNode } from "react";
import { X } from "lucide-react";

export type ObjectPanelRecord = {
  object_id: string;
  object_type: string;
  properties: Record<string, unknown>;
};

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export default function ObjectPanel({
  object,
  onClose,
  children,
  className = "",
  showEmptyState = true,
  testId,
}: {
  object: ObjectPanelRecord;
  onClose: () => void;
  children?: ReactNode;
  className?: string;
  showEmptyState?: boolean;
  testId?: string;
}) {
  return (
    <aside className={`oe-object-panel ${className}`.trim()} aria-label={`${object.object_id} 对象预览`} data-testid={testId}>
      <div className="oe-object-panel__header">
        <div className="min-w-0">
          <p className="oe-kicker">{object.object_type}</p>
          <h3 className="truncate">{object.object_id}</h3>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭对象预览"><X size={16} /></button>
      </div>
      <div className="oe-object-panel__actions">
        <button type="button" disabled title="Action compiler 尚未接入 Explorer">Actions</button>
        <button type="button" disabled title="Graph adapter 将在后续阶段接入">Open in Graph</button>
      </div>
      <dl className="oe-property-list">
        {Object.entries(object.properties).map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{displayValue(value)}</dd>
          </div>
        ))}
      </dl>
      {showEmptyState && !Object.keys(object.properties).length && <p className="oe-empty-copy">该对象没有可显示的授权属性。</p>}
      {children}
    </aside>
  );
}
