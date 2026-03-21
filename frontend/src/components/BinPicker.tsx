import { useMemo } from "react";

type BinRecord = {
  id: string;
  title: string;
  description?: string | null;
};

type BinPickerProps = {
  bins: BinRecord[];
  selectedBinIds: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
};

export function BinPicker({ bins, selectedBinIds, onChange, disabled = false }: BinPickerProps) {
  const selectedSet = useMemo(() => new Set(selectedBinIds), [selectedBinIds]);

  const toggle = (binId: string) => {
    if (disabled) {
      return;
    }
    if (selectedSet.has(binId)) {
      onChange(selectedBinIds.filter((item) => item !== binId));
      return;
    }
    onChange([...selectedBinIds, binId]);
  };

  return (
    <section className="bin-picker">
      <h3>Active Bins</h3>
      <div className="bin-picker-list">
        {bins.map((bin) => {
          const checked = selectedSet.has(bin.id);
          return (
            <label key={bin.id} className={checked ? "bin-picker-item is-active" : "bin-picker-item"}>
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggle(bin.id)}
                disabled={disabled}
                aria-label={`Use bin ${bin.title}`}
              />
              <span className="bin-picker-title">{bin.title}</span>
            </label>
          );
        })}
      </div>
      <p className="bin-picker-summary">
        {selectedBinIds.length > 0 ? `${selectedBinIds.length} bins selected` : "No bins selected (parametric mode)"}
      </p>
    </section>
  );
}
