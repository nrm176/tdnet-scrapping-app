export type ParserOption = {
  parser_name: string;
  parser_version: string;
  parse_jobs: number;
  parse_texts: number;
};

export type ParserQualityParser = {
  parser_name: string;
  parser_version: string;
  total_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  pending_jobs: number;
  parse_texts: number;
  low_text_jobs: number;
  average_char_count: number | null;
};

export type ParserFallbackCandidate = {
  name: string;
  parser_name: string;
  parser_version: string;
  candidate_count: number;
  description: string;
};

export type ParserRecentError = {
  parse_job_id: number;
  file_id: number;
  parser_name: string;
  parser_version: string;
  error: string;
  updated_at: string;
};

export type ParserQuality = {
  total_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  parse_texts: number;
  low_text_jobs: number;
  fallback_candidates: ParserFallbackCandidate[];
  parsers: ParserQualityParser[];
  recent_errors: ParserRecentError[];
};

export type ReportCalendarDay = {
  disclosure_date: string;
  record_count: number;
  report_count: number;
};

export type ReportCalendarResponse = {
  month: string;
  days: ReportCalendarDay[];
};

export type ReportTag = {
  slug: string;
  label_ja: string;
  label_en: string;
  description: string;
  priority: number;
  active: boolean;
  assignment_count: number;
  primary_count: number;
};

export type ReportTagAssignment = {
  slug: string;
  label_ja: string;
  label_en: string;
  is_primary: boolean;
  confidence: number;
  source: string;
};

export type FinancialFactSummary = {
  fact_count: number;
  metric_delta_count: number;
  metric_keys: string[];
  forecast_revision_rows: number;
  has_forecast_revision: boolean;
};

export type FinancialFactSource = {
  line_index: number | null;
  text: string;
};

export type FinancialFactValue = {
  raw?: string;
  value?: number | string | null;
  unit?: string | null;
  metric?: string | null;
  metric_label_ja?: string | null;
  metric_unit?: string | null;
  [key: string]: unknown;
};

export type FinancialFact = {
  type: string;
  row_kind: string | null;
  metric: string | null;
  metric_label_ja: string | null;
  unit: string | null;
  values: FinancialFactValue[];
  source: FinancialFactSource | null;
  confidence: number | null;
};

export type FinancialMetricDelta = {
  type: string;
  metric: string | null;
  metric_label_ja: string | null;
  unit: string | null;
  period: string | null;
  comparison_period: string | null;
  comparison_basis: string | null;
  current_value: number | null;
  current_raw: string | null;
  comparison_value: number | null;
  comparison_raw: string | null;
  change_value: number | null;
  reported_change_pct: number | null;
  reported_change_pct_raw: string | null;
  computed_change_pct: number | null;
  change_pct_source: string | null;
  source: FinancialFactSource | null;
  confidence: number | null;
};

export type FinancialFactsAnalysis = {
  analysis_id: number;
  file_id: number;
  parse_job_id: number | null;
  status: string;
  analyzer_name: string;
  analyzer_version: string;
  analyzed_at: string | null;
  last_analysis_error: string | null;
  result_text: string | null;
  summary: FinancialFactSummary;
  facts: FinancialFact[];
  metric_deltas: FinancialMetricDelta[];
};

export type CompanyTimelineFile = {
  file_id: number;
  file_type: string;
  download_status: string;
  file_size_bytes: number | null;
  downloaded_at: string | null;
  source_url: string;
  storage_path: string;
};

export type CompanyTimelineParser = {
  parse_job_id: number;
  file_id: number;
  parser_name: string;
  parser_version: string;
  parse_status: string;
  parse_attempts: number;
  parsed_at: string | null;
  page_count: number | null;
  char_count: number | null;
  has_text: boolean;
  last_parse_error: string | null;
  financial_facts: FinancialFactsAnalysis | null;
};

export type CompanyTimelineDisclosure = {
  disclosure_id: string;
  disclosure_date: string;
  time: string;
  code: string;
  company_name: string;
  title: string;
  xbrl_available: boolean;
  tags: ReportTagAssignment[];
  files: CompanyTimelineFile[];
  parsers: CompanyTimelineParser[];
  best_parse_job_id: number | null;
  financial_facts: FinancialFactsAnalysis | null;
  snippet: string;
};

export type CompanyTimelineResponse = {
  code: string;
  company_name: string | null;
  total: number;
  limit: number;
  offset: number;
  results: CompanyTimelineDisclosure[];
};

export type ParseSearchResult = {
  parse_job_id: number;
  file_id: number;
  disclosure_id: string;
  disclosure_date: string;
  time: string;
  code: string;
  company_name: string;
  title: string;
  parser_name: string;
  parser_version: string;
  page_count: number;
  char_count: number;
  parsed_at: string | null;
  snippet: string;
  tags: ReportTagAssignment[];
  matched_pages: ParsedPageMatch[];
  financial_facts: FinancialFactsAnalysis | null;
};

export type ParseSearchResponse = {
  query: string | null;
  total: number;
  limit: number;
  offset: number;
  results: ParseSearchResult[];
};

export type ParsedPage = {
  page: number;
  markdown: string;
  char_count: number;
};

export type ParsedPageMatch = {
  page: number;
  snippet: string;
};

export type ParseJobDetail = ParseSearchResult & {
  source_url: string;
  pdf_path: string;
  text_path: string | null;
  content_text: string;
  pages: ParsedPage[];
};
