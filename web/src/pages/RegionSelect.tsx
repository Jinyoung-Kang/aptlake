import type { Region } from "../api";

export default function RegionSelect({ regions, value, onChange }: { regions: Region[]; value: string; onChange: (v: string) => void }) {
  const bySido = new Map<string, Region[]>();
  for (const r of regions) bySido.set(r.sidoName, [...(bySido.get(r.sidoName) ?? []), r]);
  return (
    <label>
      시군구
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {[...bySido.entries()].map(([sido, list]) => (
          <optgroup key={sido} label={sido}>
            {list.map((r) => (
              <option key={r.sggCd} value={r.sggCd}>
                {r.name} ({r.sggCd})
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  );
}
