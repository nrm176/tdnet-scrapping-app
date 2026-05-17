import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Database,
  ExternalLink,
  FileText,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import {
  fetchParseJob,
  fetchParsers,
  fetchReportCalendar,
  fetchReviewQueue,
  fetchTags,
  pageImageUrl,
  searchParseTexts,
} from "./api";
import type { ParseJobDetail, ParseSearchResult, ParserOption, ReportCalendarDay, ReportTag } from "./types";

type ParserSelection = {
  parserName?: string;
  parserVersion?: string;
};

type CalendarCell = {
  date: string;
  day: number;
  isCurrentMonth: boolean;
  isWeekend: boolean;
};

function parserKey(option: ParserOption): string {
  return `${option.parser_name}::${option.parser_version}`;
}

function parseParserKey(value: string): ParserSelection {
  if (!value) {
    return {};
  }
  const [parserName, parserVersion] = value.split("::");
  return { parserName, parserVersion };
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

function formatDateKey(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function currentMonthKey(): string {
  return formatDateKey(new Date()).slice(0, 7);
}

function addMonths(monthKey: string, delta: number): string {
  const [year, month] = monthKey.split("-").map(Number);
  return formatDateKey(new Date(year, month - 1 + delta, 1)).slice(0, 7);
}

function formatMonth(monthKey: string): string {
  const [year, month] = monthKey.split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" }).format(
    new Date(year, month - 1, 1),
  );
}

function buildCalendarCells(monthKey: string): CalendarCell[] {
  const [year, month] = monthKey.split("-").map(Number);
  const firstOfMonth = new Date(year, month - 1, 1);
  const mondayOffset = (firstOfMonth.getDay() + 6) % 7;
  const startDate = new Date(year, month - 1, 1 - mondayOffset);

  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(startDate);
    date.setDate(startDate.getDate() + index);
    const dayOfWeek = date.getDay();
    return {
      date: formatDateKey(date),
      day: date.getDate(),
      isCurrentMonth: date.getMonth() === month - 1,
      isWeekend: dayOfWeek === 0 || dayOfWeek === 6,
    };
  });
}

function App() {
  const [parsers, setParsers] = useState<ParserOption[]>([]);
  const [tags, setTags] = useState<ReportTag[]>([]);
  const [parserValue, setParserValue] = useState("");
  const [query, setQuery] = useState("");
  const [code, setCode] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagMode, setTagMode] = useState<"any" | "all">("any");
  const [calendarMonth, setCalendarMonth] = useState(currentMonthKey());
  const [calendarDays, setCalendarDays] = useState<ReportCalendarDay[]>([]);
  const [results, setResults] = useState<ParseSearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ParseJobDetail | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parserSelection = useMemo(() => parseParserKey(parserValue), [parserValue]);
  const selectedPage = detail?.pages[pageIndex] ?? detail?.pages[0] ?? null;
  const visibleText = selectedPage?.markdown || detail?.content_text || "";
  const calendarCells = useMemo(() => buildCalendarCells(calendarMonth), [calendarMonth]);
  const calendarRecordCounts = useMemo(
    () => new Map(calendarDays.map((day) => [day.disclosure_date, day.record_count])),
    [calendarDays],
  );
  const calendarReportCounts = useMemo(
    () => new Map(calendarDays.map((day) => [day.disclosure_date, day.report_count])),
    [calendarDays],
  );
  const calendarRecordTotal = useMemo(
    () => calendarDays.reduce((sum, day) => sum + day.record_count, 0),
    [calendarDays],
  );
  const calendarReportTotal = useMemo(
    () => calendarDays.reduce((sum, day) => sum + day.report_count, 0),
    [calendarDays],
  );
  const selectedDate = dateFrom && dateFrom === dateTo ? dateFrom : "";

  async function loadParsers() {
    const options = await fetchParsers();
    setError(null);
    setParsers(options);
    if (!parserValue && options.length) {
      setParserValue(parserKey(options[0]));
    }
  }

  async function loadTags() {
    const options = await fetchTags();
    setError(null);
    setTags(options.filter((option) => option.active));
  }

  async function loadRecent() {
    setLoading(true);
    setError(null);
    try {
      const response = await fetchReviewQueue({
        ...parserSelection,
        tags: selectedTags,
        tagMode,
        limit: 25,
      });
      setResults(response.results);
      setTotal(response.total);
      setSelectedId(response.results[0]?.parse_job_id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load review queue");
    } finally {
      setLoading(false);
    }
  }

  async function loadCalendar() {
    setCalendarLoading(true);
    try {
      const response = await fetchReportCalendar({
        month: calendarMonth,
        code,
        tags: selectedTags,
        tagMode,
        ...parserSelection,
      });
      setError(null);
      setCalendarDays(response.days);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load calendar");
    } finally {
      setCalendarLoading(false);
    }
  }

  async function runSearch(event?: FormEvent, overrides: { dateFrom?: string; dateTo?: string } = {}) {
    event?.preventDefault();
    setLoading(true);
    setError(null);
    const nextDateFrom = overrides.dateFrom ?? dateFrom;
    const nextDateTo = overrides.dateTo ?? dateTo;
    try {
      const response = await searchParseTexts({
        q: query,
        code,
        dateFrom: nextDateFrom,
        dateTo: nextDateTo,
        tags: selectedTags,
        tagMode,
        ...parserSelection,
        limit: 25,
      });
      setResults(response.results);
      setTotal(response.total);
      setSelectedId(response.results[0]?.parse_job_id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }

  function selectCalendarDate(date: string) {
    setDateFrom(date);
    setDateTo(date);
    setCalendarMonth(date.slice(0, 7));
    runSearch(undefined, { dateFrom: date, dateTo: date }).catch((err) =>
      setError(err instanceof Error ? err.message : "Search failed"),
    );
  }

  function clearDateFilters() {
    setDateFrom("");
    setDateTo("");
    runSearch(undefined, { dateFrom: "", dateTo: "" }).catch((err) =>
      setError(err instanceof Error ? err.message : "Search failed"),
    );
  }

  useEffect(() => {
    loadParsers().catch((err) => setError(err instanceof Error ? err.message : "Failed to load parsers"));
    loadTags().catch((err) => setError(err instanceof Error ? err.message : "Failed to load tags"));
  }, []);

  useEffect(() => {
    if (parsers.length && !results.length) {
      loadRecent().catch((err) => setError(err instanceof Error ? err.message : "Failed to load recent parses"));
    }
  }, [parsers.length, parserValue]);

  useEffect(() => {
    if (parsers.length) {
      loadCalendar().catch((err) => setError(err instanceof Error ? err.message : "Failed to load calendar"));
    }
  }, [calendarMonth, code, parsers.length, parserValue, selectedTags, tagMode]);

  useEffect(() => {
    const activeDate = dateFrom || dateTo;
    if (activeDate.length >= 7) {
      setCalendarMonth(activeDate.slice(0, 7));
    }
  }, [dateFrom, dateTo]);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    setPageIndex(0);
    fetchParseJob(selectedId)
      .then((value) => {
        setError(null);
        setDetail(value);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load parse job"))
      .finally(() => setDetailLoading(false));
  }, [selectedId]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <h1>TDnet Review</h1>
          <p>Search persisted parse text and inspect parser output against the source PDF.</p>
        </div>
        <div className="status-pill" title="Postgres-backed parsed text cache">
          <Database size={16} />
          <span>{formatNumber(parsers.reduce((sum, option) => sum + option.parse_texts, 0))} text rows</span>
        </div>
      </header>

      <form className="search-band" onSubmit={runSearch}>
        <label className="field field-query">
          <span>Text query</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="業績予想, 18,000, company name..."
          />
        </label>
        <label className="field">
          <span>Parser</span>
          <select value={parserValue} onChange={(event) => setParserValue(event.target.value)}>
            <option value="">All parsers</option>
            {parsers.map((option) => (
              <option key={parserKey(option)} value={parserKey(option)}>
                {option.parser_name} · {option.parse_texts}/{option.parse_jobs}
              </option>
            ))}
          </select>
        </label>
        <label className="field field-code">
          <span>Code</span>
          <input value={code} onChange={(event) => setCode(event.target.value)} placeholder="85600" />
        </label>
        <label className="field field-tags">
          <span>Tags</span>
          <select
            multiple
            value={selectedTags}
            onChange={(event) =>
              setSelectedTags(Array.from(event.currentTarget.selectedOptions, (option) => option.value))
            }
          >
            {tags.map((tag) => (
              <option key={tag.slug} value={tag.slug}>
                {tag.label_ja} · {formatNumber(tag.assignment_count)}
              </option>
            ))}
          </select>
        </label>
        <label className="field field-mode">
          <span>Mode</span>
          <select value={tagMode} onChange={(event) => setTagMode(event.target.value as "any" | "all")}>
            <option value="any">Any</option>
            <option value="all">All</option>
          </select>
        </label>
        <label className="field field-date">
          <span>From</span>
          <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
        </label>
        <label className="field field-date">
          <span>To</span>
          <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
        </label>
        <button className="icon-button primary" type="submit" title="Search parsed text">
          <Search size={18} />
          <span>Search</span>
        </button>
        <button className="icon-button" type="button" onClick={loadRecent} title="Load recent parse jobs">
          <RefreshCw size={18} />
        </button>
      </form>

      {error ? <div className="error-band">{error}</div> : null}

      <main className="workspace">
        <aside className="results-pane">
          <section className="calendar-panel" aria-label="Report calendar">
            <div className="calendar-header">
              <button
                className="square-button"
                type="button"
                title="Previous month"
                onClick={() => setCalendarMonth((value) => addMonths(value, -1))}
              >
                <ChevronLeft size={18} />
              </button>
              <div className="calendar-title">
                <CalendarDays size={16} />
                <strong>{formatMonth(calendarMonth)}</strong>
                <span>
                  {calendarLoading
                    ? "Loading"
                    : `${formatNumber(calendarRecordTotal)} records${
                        calendarReportTotal !== calendarRecordTotal
                          ? ` · ${formatNumber(calendarReportTotal)} parsed matches`
                          : ""
                      }`}
                </span>
              </div>
              <button
                className="square-button"
                type="button"
                title="Next month"
                onClick={() => setCalendarMonth((value) => addMonths(value, 1))}
              >
                <ChevronRight size={18} />
              </button>
              <button
                className="square-button"
                type="button"
                title="Clear date filter"
                disabled={!dateFrom && !dateTo}
                onClick={clearDateFilters}
              >
                <X size={17} />
              </button>
            </div>
            <div className="calendar-weekdays">
              {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => (
                <span key={day}>{day}</span>
              ))}
            </div>
            <div className="calendar-grid">
              {calendarCells.map((cell) => {
                const recordCount = calendarRecordCounts.get(cell.date) ?? 0;
                const reportCount = calendarReportCounts.get(cell.date) ?? 0;
                const isSelected = selectedDate === cell.date;
                const isInRange = !selectedDate && dateFrom && dateTo && cell.date >= dateFrom && cell.date <= dateTo;
                return (
                  <button
                    key={cell.date}
                    className={[
                      "calendar-day",
                      cell.isCurrentMonth ? "" : "outside",
                      cell.isWeekend ? "weekend" : "",
                      recordCount > 0 ? "has-reports" : "",
                      reportCount > 0 ? "has-matches" : "",
                      isSelected ? "selected" : "",
                      isInRange ? "in-range" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    type="button"
                    title={`${cell.date}: ${formatNumber(recordCount)} records${
                      reportCount !== recordCount ? `, ${formatNumber(reportCount)} parsed matches` : ""
                    }`}
                    onClick={() => selectCalendarDate(cell.date)}
                  >
                    <span className="calendar-day-number">{cell.day}</span>
                    {recordCount > 0 ? <span className="calendar-count">{formatNumber(recordCount)}</span> : null}
                  </button>
                );
              })}
            </div>
          </section>
          <div className="pane-header">
            <div>
              <strong>{loading ? "Loading..." : `${formatNumber(total)} matches`}</strong>
              <span>{results.length} shown</span>
            </div>
          </div>
          <div className="result-list">
            {results.map((result) => (
              <button
                key={result.parse_job_id}
                className={`result-row ${selectedId === result.parse_job_id ? "selected" : ""}`}
                type="button"
                onClick={() => setSelectedId(result.parse_job_id)}
              >
                <div className="result-line">
                  <span className="date">{result.disclosure_date}</span>
                  <span className="code">{result.code}</span>
                </div>
                <div className="result-title">{result.title}</div>
                <div className="company">{result.company_name}</div>
                {result.tags.length ? (
                  <div className="tag-chip-list">
                    {result.tags.map((tag) => (
                      <span
                        key={tag.slug}
                        className={`tag-chip ${tag.is_primary ? "primary" : ""}`}
                        title={`${tag.label_en} · ${tag.source}`}
                      >
                        {tag.label_ja}
                      </span>
                    ))}
                  </div>
                ) : null}
                <div className="snippet">{result.snippet || "No snippet available."}</div>
                <div className="result-meta">
                  <span>{result.parser_name}</span>
                  <span>{result.page_count}p</span>
                  <span>{formatNumber(result.char_count)} chars</span>
                </div>
              </button>
            ))}
            {!loading && !results.length ? (
              <div className="empty-state">
                <FileText size={22} />
                <span>No parse text rows matched the current filters.</span>
              </div>
            ) : null}
          </div>
        </aside>

        <section className="review-pane">
          {detailLoading ? (
            <div className="loading-state">Loading parse job...</div>
          ) : detail ? (
            <>
              <div className="document-header">
                <div>
                  <div className="document-title">{detail.title}</div>
                  <div className="document-meta">
                    <span>{detail.disclosure_date}</span>
                    <span>{detail.time}</span>
                    <span>{detail.code}</span>
                    <span>{detail.company_name}</span>
                    <span>{detail.parser_name}</span>
                  </div>
                  {detail.tags.length ? (
                    <div className="tag-chip-list document-tags">
                      {detail.tags.map((tag) => (
                        <span
                          key={tag.slug}
                          className={`tag-chip ${tag.is_primary ? "primary" : ""}`}
                          title={`${tag.label_en} · ${tag.source}`}
                        >
                          {tag.label_ja}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
                <a className="icon-button link-button" href={detail.source_url} target="_blank" rel="noreferrer">
                  <ExternalLink size={17} />
                  <span>TDnet PDF</span>
                </a>
              </div>

              <div className="page-toolbar">
                <button
                  className="square-button"
                  type="button"
                  title="Previous page"
                  disabled={pageIndex <= 0}
                  onClick={() => setPageIndex((value) => Math.max(0, value - 1))}
                >
                  <ChevronLeft size={18} />
                </button>
                <span>
                  Page {selectedPage?.page ?? 1} of {Math.max(1, detail.pages.length)}
                </span>
                <button
                  className="square-button"
                  type="button"
                  title="Next page"
                  disabled={pageIndex >= detail.pages.length - 1}
                  onClick={() => setPageIndex((value) => Math.min(detail.pages.length - 1, value + 1))}
                >
                  <ChevronRight size={18} />
                </button>
              </div>

              <div className="review-grid">
                <div className="pdf-panel">
                  <img
                    src={pageImageUrl(detail.parse_job_id, selectedPage?.page ?? 1)}
                    alt={`PDF page ${selectedPage?.page ?? 1}`}
                  />
                </div>
                <div className="text-panel">
                  <pre>{visibleText}</pre>
                </div>
              </div>
            </>
          ) : (
            <div className="empty-review">
              <FileText size={28} />
              <span>Select a parse result to review the PDF and parsed text.</span>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

export default App;
