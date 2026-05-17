import type { ParseJobDetail, ParseSearchResponse, ParserOption } from "./types";

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

async function requestJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
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

export async function fetchParseJob(parseJobId: number): Promise<ParseJobDetail> {
  return requestJson<ParseJobDetail>(`/api/parse-jobs/${parseJobId}`);
}

export function pageImageUrl(parseJobId: number, page: number): string {
  return `/api/parse-jobs/${parseJobId}/page-image?page=${page}`;
}
