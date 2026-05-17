export type ParserOption = {
  parser_name: string;
  parser_version: string;
  parse_jobs: number;
  parse_texts: number;
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

export type ParseJobDetail = ParseSearchResult & {
  source_url: string;
  pdf_path: string;
  text_path: string | null;
  content_text: string;
  pages: ParsedPage[];
};
