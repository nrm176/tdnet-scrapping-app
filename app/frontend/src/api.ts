import type { ParseJobDetail, ParseSearchResponse, ParserOption, ReportCalendarResponse } from "./types";

type SearchParams = {
  q?: string;
  parserName?: string;
  parserVersion?: string;
  code?: string;
  dateFrom?: string;
  dateTo?: string;
  limit?: number;
  offset?: number;
};

type CalendarParams = {
  month: string;
  q?: string;
  parserName?: string;
  parserVersion?: string;
  code?: string;
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

export async function fetchParsers(): Promise<ParserOption[]> {
  const response = await requestJson<{ parsers: ParserOption[] }>("/api/parsers");
  return response.parsers;
}

export async function searchParseTexts(params: SearchParams): Promise<ParseSearchResponse> {
  const query = new URLSearchParams();
  appendOptional(query, "q", params.q?.trim());
  appendOptional(query, "parser_name", params.parserName);
  appendOptional(query, "parser_version", params.parserVersion);
  appendOptional(query, "code", params.code?.trim());
  appendOptional(query, "date_from", params.dateFrom);
  appendOptional(query, "date_to", params.dateTo);
  appendOptional(query, "limit", params.limit ?? 25);
  appendOptional(query, "offset", params.offset ?? 0);
  return requestJson<ParseSearchResponse>(`/api/search?${query.toString()}`);
}

export async function fetchReviewQueue(params: SearchParams): Promise<ParseSearchResponse> {
  const query = new URLSearchParams();
  appendOptional(query, "parser_name", params.parserName);
  appendOptional(query, "parser_version", params.parserVersion);
  appendOptional(query, "limit", params.limit ?? 25);
  appendOptional(query, "offset", params.offset ?? 0);
  return requestJson<ParseSearchResponse>(`/api/review-queue?${query.toString()}`);
}

export async function fetchReportCalendar(params: CalendarParams): Promise<ReportCalendarResponse> {
  const query = new URLSearchParams();
  appendOptional(query, "month", params.month);
  appendOptional(query, "q", params.q?.trim());
  appendOptional(query, "parser_name", params.parserName);
  appendOptional(query, "parser_version", params.parserVersion);
  appendOptional(query, "code", params.code?.trim());
  return requestJson<ReportCalendarResponse>(`/api/calendar?${query.toString()}`);
}

export async function fetchParseJob(parseJobId: number): Promise<ParseJobDetail> {
  return requestJson<ParseJobDetail>(`/api/parse-jobs/${parseJobId}`);
}

export function pageImageUrl(parseJobId: number, page: number): string {
  return `/api/parse-jobs/${parseJobId}/page-image?page=${page}`;
}
