import type {
  ParseJobDetail,
  ParseSearchResponse,
  ParserOption,
  ParserQuality,
  ReportCalendarResponse,
  ReportTag,
} from "./types";

type SearchParams = {
  q?: string;
  titleQuery?: string;
  textQuery?: string;
  parserName?: string;
  parserVersion?: string;
  code?: string;
  dateFrom?: string;
  dateTo?: string;
  tags?: string[];
  tagMode?: "any" | "all";
  limit?: number;
  offset?: number;
};

type CalendarParams = {
  month: string;
  q?: string;
  titleQuery?: string;
  textQuery?: string;
  parserName?: string;
  parserVersion?: string;
  code?: string;
  tags?: string[];
  tagMode?: "any" | "all";
};

async function requestJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    const text = await response.text();
    let message = text || response.statusText;
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      if (typeof body.detail === "string") {
        message = body.detail;
      }
    } catch {
      // Keep the raw response body when the server did not return JSON.
    }
    throw new Error(`${url} failed with ${response.status}: ${message}`);
  }
  return response.json() as Promise<T>;
}

function appendOptional(params: URLSearchParams, key: string, value: string | number | undefined): void {
  if (value === undefined || value === "") {
    return;
  }
  params.set(key, String(value));
}

function appendOptionalList(params: URLSearchParams, key: string, values: string[] | undefined): void {
  values?.forEach((value) => {
    if (value) {
      params.append(key, value);
    }
  });
}

export async function fetchParsers(): Promise<ParserOption[]> {
  const response = await requestJson<{ parsers: ParserOption[] }>("/api/parsers");
  return response.parsers;
}

export async function fetchParserQuality(): Promise<ParserQuality> {
  return requestJson<ParserQuality>("/api/parser-quality");
}

export async function fetchTags(): Promise<ReportTag[]> {
  const response = await requestJson<{ tags: ReportTag[] }>("/api/tags");
  return response.tags;
}

export async function searchParseTexts(params: SearchParams): Promise<ParseSearchResponse> {
  const query = new URLSearchParams();
  appendOptional(query, "q", params.q?.trim());
  appendOptional(query, "title_q", params.titleQuery?.trim());
  appendOptional(query, "text_q", params.textQuery?.trim());
  appendOptional(query, "parser_name", params.parserName);
  appendOptional(query, "parser_version", params.parserVersion);
  appendOptional(query, "code", params.code?.trim());
  appendOptional(query, "date_from", params.dateFrom);
  appendOptional(query, "date_to", params.dateTo);
  appendOptionalList(query, "tags", params.tags);
  appendOptional(query, "tag_mode", params.tagMode);
  appendOptional(query, "limit", params.limit ?? 25);
  appendOptional(query, "offset", params.offset ?? 0);
  return requestJson<ParseSearchResponse>(`/api/search?${query.toString()}`);
}

export async function fetchReviewQueue(params: SearchParams): Promise<ParseSearchResponse> {
  const query = new URLSearchParams();
  appendOptional(query, "parser_name", params.parserName);
  appendOptional(query, "parser_version", params.parserVersion);
  appendOptionalList(query, "tags", params.tags);
  appendOptional(query, "tag_mode", params.tagMode);
  appendOptional(query, "limit", params.limit ?? 25);
  appendOptional(query, "offset", params.offset ?? 0);
  return requestJson<ParseSearchResponse>(`/api/review-queue?${query.toString()}`);
}

export async function fetchReportCalendar(params: CalendarParams): Promise<ReportCalendarResponse> {
  const query = new URLSearchParams();
  appendOptional(query, "month", params.month);
  appendOptional(query, "q", params.q?.trim());
  appendOptional(query, "title_q", params.titleQuery?.trim());
  appendOptional(query, "text_q", params.textQuery?.trim());
  appendOptional(query, "parser_name", params.parserName);
  appendOptional(query, "parser_version", params.parserVersion);
  appendOptional(query, "code", params.code?.trim());
  appendOptionalList(query, "tags", params.tags);
  appendOptional(query, "tag_mode", params.tagMode);
  return requestJson<ReportCalendarResponse>(`/api/calendar?${query.toString()}`);
}

export async function fetchParseJob(parseJobId: number): Promise<ParseJobDetail> {
  return requestJson<ParseJobDetail>(`/api/parse-jobs/${parseJobId}`);
}

export function pageImageUrl(parseJobId: number, page: number): string {
  return `/api/parse-jobs/${parseJobId}/page-image?page=${page}`;
}
