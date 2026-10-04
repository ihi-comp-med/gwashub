/** The study-metadata field catalogue (sections → fields, each section cohort- or dataset-level), fetched once. */
export function useMetadataSchema() {
  const schema = useState<any[] | null>('metadata-schema', () => null)
  const units = useState<any[] | null>('metadata-units', () => null)
  const defaultConfig = useState<{ shown: string[], required: string[] } | null>('metadata-default', () => null)
  if (!schema.value) {
    api<{ sections: any[], units: any[], default_config: any }>('/metadata-schema').then((r) => {
      schema.value = r.sections
      units.value = r.units
      defaultConfig.value = r.default_config
    }).catch(() => {})
  }
  /** Labels of required fields missing from a metadata record (grid sections count if any cell is filled). */
  function missingRequired(meta: Record<string, any>, config?: { required: string[] } | null) {
    const has = (k: string) => meta[k] !== undefined && meta[k] !== null && meta[k] !== ''
    return (config?.required ?? []).filter((k) => {
      const sec = (schema.value ?? []).find((s: any) => s.key === k && s.grid)
      return sec ? !sec.grid.rows.some((r: any) => sec.grid.cols.some((c: any) => has(`${r.key}_${c.key}`))) : !has(k)
    }).map(k => (units.value ?? []).find((u: any) => u.key === k)?.label ?? k)
  }
  return { schema, units, defaultConfig, missingRequired }
}
