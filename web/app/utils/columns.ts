// The standard columns a dataset's column mapping fills (api/gwashub_api/routers/datasets.py MAPPABLE), in the order
// the upload wizard and the Details tab show them. A P-value column or a -log10 P column will do for P.
export const STANDARD_FIELDS = [
  { key: 'rsid', label: 'Variant ID (rsID)', required: false },
  { key: 'chr', label: 'Chromosome', required: true },
  { key: 'pos', label: 'Position', required: true },
  { key: 'ea', label: 'Effect allele', required: true },
  { key: 'oa', label: 'Other allele', required: true },
  { key: 'eaf', label: 'Effect allele frequency', required: true },
  { key: 'beta', label: 'Effect (BETA or OR)', required: true },
  { key: 'se', label: 'Standard error', required: true },
  { key: 'p', label: 'P-value', required: true },
  { key: 'log10p', label: '-log10 P (if there is no P-value)', required: false },
  { key: 'n', label: 'Sample size', required: false },
  { key: 'ncase', label: 'Number of cases', required: false },
  { key: 'ncontrol', label: 'Number of controls', required: false },
  { key: 'info', label: 'Imputation INFO', required: false },
]

// Labels of the required fields a mapping leaves empty.
export function unmappedRequired(mapping: Record<string, string | null | undefined>) {
  return STANDARD_FIELDS.filter(f => f.required && !mapping[f.key] && !(f.key === 'p' && mapping.log10p)).map(f => f.label)
}
