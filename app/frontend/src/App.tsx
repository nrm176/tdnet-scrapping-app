import { type FormEvent, type KeyboardEvent, useEffect, useMemo, useState } from "react";
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

type SearchCriteria = {
  titleQuery: string;
  textQuery: string;
  code: string;
  dateFrom: string;
  dateTo: string;
  tags: string[];
  tagMode: "any" | "all";
  parserValue: string;
};

type SearchCriteriaOverrides = Partial<Omit<SearchCriteria, "tags">> & {
  tags?: string[];
};

const EMPTY_CRITERIA: SearchCriteria = {
  titleQuery: "",
  textQuery: "",
  code: "",
  dateFrom: "",
  dateTo: "",
  tags: [],
  tagMode: "any",
  parserValue: "",
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
  const sundayOffset = firstOfMonth.getDay();
  const startDate = new Date(year, month - 1, 1 - sundayOffset);

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
  const [titleQuery, setTitleQuery] = useState("");
  const [textQuery, setTextQuery] = useState("");
  const [code, setCode] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagMode, setTagMode] = useState<"any" | "all">("any");
  const [calendarMonth, setCalendarMonth] = useState(currentMonthKey());
  const [calendarDays, setCalendarDays] = useState<ReportCalendarDay[]>([]);
  const [results, setResults] = useState<ParseSearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [appliedCriteria, setAppliedCriteria] = useState<SearchCriteria>(EMPTY_CRITERIA);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ParseJobDetail | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [initialResultsLoaded, setInitialResultsLoaded] = useState(false);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
  const tagLabelBySlug = useMemo(() => new Map(tags.map((tag) => [tag.slug, tag.label_ja])), [tags]);
  const appliedParserLabel = useMemo(() => {
    if (!appliedCriteria.parserValue) {
      return "All parsers";
    }
    return appliedCriteria.parserValue.split("::")[0] || "All parsers";
  }, [appliedCriteria.parserValue]);
  const appliedCriteriaItems = useMemo(() => {
    const items: { label: string; value: string }[] = [];
    items.push({ label: "Parser", value: appliedParserLabel });
    if (appliedCriteria.titleQuery) {
      items.push({ label: "Title", value: appliedCriteria.titleQuery });
    }
    if (appliedCriteria.textQuery) {
      items.push({ label: "Full text", value: appliedCriteria.textQuery });
    }
    if (appliedCriteria.code) {
      items.push({ label: "Code", value: appliedCriteria.code });
    }
    if (appliedCriteria.dateFrom || appliedCriteria.dateTo) {
      const value =
        appliedCriteria.dateFrom && appliedCriteria.dateTo && appliedCriteria.dateFrom === appliedCriteria.dateTo
          ? appliedCriteria.dateFrom
          : `${appliedCriteria.dateFrom || "any"} to ${appliedCriteria.dateTo || "any"}`;
      items.push({ label: "Date", value });
    }
    if (appliedCriteria.tags.length) {
      const value = appliedCriteria.tags.map((slug) => tagLabelBySlug.get(slug) ?? slug).join(", ");
      items.push({ label: `Tags ${appliedCriteria.tagMode}`, value });
    }
    return items;
  }, [appliedCriteria, appliedParserLabel, tagLabelBySlug]);
  const selectedDate = dateFrom && dateFrom === dateTo ? dateFrom : "";

  function buildCriteria(overrides: SearchCriteriaOverrides = {}): SearchCriteria {
    return {
      titleQuery: overrides.titleQuery ?? titleQuery,
      textQuery: overrides.textQuery ?? textQuery,
      code: overrides.code ?? code,
      dateFrom: overrides.dateFrom ?? dateFrom,
      dateTo: overrides.dateTo ?? dateTo,
      tags: overrides.tags ?? selectedTags,
      tagMode: overrides.tagMode ?? tagMode,
      parserValue: overrides.parserValue ?? parserValue,
    };
  }

  async function loadParsers() {
    const options = await fetchParsers();
    setError(null);
    setParsers(options);
  }

  async function loadTags() {
    const options = await fetchTags();
    setError(null);
    setTags(options.filter((option) => option.active));
  }

  async function loadRecent() {
    setLoading(true);
    setError(null);
    const criteria = buildCriteria({
      titleQuery: "",
      textQuery: "",
      code: "",
      dateFrom: "",
      dateTo: "",
    });
    const selection = parseParserKey(criteria.parserValue);
    try {
      const response = await fetchReviewQueue({
        ...selection,
        tags: criteria.tags,
        tagMode: criteria.tagMode,
        limit: 25,
      });
      setResults(response.results);
      setTotal(response.total);
      setSelectedId(response.results[0]?.parse_job_id ?? null);
      setAppliedCriteria(criteria);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load review queue");
    } finally {
      setLoading(false);
    }
  }

  async function loadCalendar() {
    setCalendarLoading(true);
    const selection = parseParserKey(appliedCriteria.parserValue);
    try {
      const response = await fetchReportCalendar({
        month: calendarMonth,
        titleQuery: appliedCriteria.titleQuery,
        textQuery: appliedCriteria.textQuery,
        code: appliedCriteria.code,
        tags: appliedCriteria.tags,
        tagMode: appliedCriteria.tagMode,
        ...selection,
      });
      setError(null);
      setCalendarDays(response.days);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load calendar");
    } finally {
      setCalendarLoading(false);
    }
  }

  async function runSearch(event?: FormEvent, overrides: SearchCriteriaOverrides = {}) {
    event?.preventDefault();
    setLoading(true);
    setError(null);
    const criteria = buildCriteria(overrides);
    const selection = parseParserKey(criteria.parserValue);
    try {
      const response = await searchParseTexts({
        titleQuery: criteria.titleQuery,
        textQuery: criteria.textQuery,
        code: criteria.code,
        dateFrom: criteria.dateFrom,
        dateTo: criteria.dateTo,
        tags: criteria.tags,
        tagMode: criteria.tagMode,
        ...selection,
        limit: 25,
      });
      setResults(response.results);
      setTotal(response.total);
      setSelectedId(response.results[0]?.parse_job_id ?? null);
      setAppliedCriteria(criteria);
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

  function selectResultFromKeyboard(event: KeyboardEvent<HTMLDivElement>, parseJobId: number) {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    setSelectedId(parseJobId);
  }

  function clearDateFilters() {
    setDateFrom("");
    setDateTo("");
    runSearch(undefined, { dateFrom: "", dateTo: "" }).catch((err) =>
      setError(err instanceof Error ? err.message : "Search failed"),
    );
  }

  function toggleTag(tagSlug: string) {
    const nextTags = selectedTags.includes(tagSlug)
      ? selectedTags.filter((slug) => slug !== tagSlug)
      : [...selectedTags, tagSlug];
    setSelectedTags(nextTags);
    runSearch(undefined, { tags: nextTags }).catch((err) =>
      setError(err instanceof Error ? err.message : "Search failed"),
    );
  }

  function changeTagMode(value: "any" | "all") {
    setTagMode(value);
    runSearch(undefined, { tagMode: value }).catch((err) =>
      setError(err instanceof Error ? err.message : "Search failed"),
    );
  }

  function changeParser(value: string) {
    setParserValue(value);
    runSearch(undefined, { parserValue: value }).catch((err) =>
      setError(err instanceof Error ? err.message : "Search failed"),
    );
  }

  useEffect(() => {
    loadParsers().catch((err) => setError(err instanceof Error ? err.message : "Failed to load parsers"));
    loadTags().catch((err) => setError(err instanceof Error ? err.message : "Failed to load tags"));
  }, []);

  useEffect(() => {
    if (parsers.length && !initialResultsLoaded) {
      setInitialResultsLoaded(true);
      loadRecent().catch((err) => setError(err instanceof Error ? err.message : "Failed to load recent parses"));
    }
  }, [parsers.length, initialResultsLoaded]);

  useEffect(() => {
    if (parsers.length) {
      loadCalendar().catch((err) => setError(err instanceof Error ? err.message : "Failed to load calendar"));
    }
  }, [calendarMonth, parsers.length, appliedCriteria]);

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
        <div className="search-fields">
          <label className="field field-title-query">
            <span>Title</span>
            <input
              value={titleQuery}
              onChange={(event) => setTitleQuery(event.target.value)}
              placeholder="業績予想, 決算短信..."
            />
          </label>
          <label className="field field-text-query">
            <span>Full text</span>
            <input
              value={textQuery}
              onChange={(event) => setTextQuery(event.target.value)}
              placeholder="18,000, 上方修正..."
            />
          </label>
          <label className="field field-parser">
            <span>Parser</span>
            <select value={parserValue} onChange={(event) => changeParser(event.target.value)}>
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
          <label className="field field-date">
            <span>From</span>
            <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
          </label>
          <label className="field field-date">
            <span>To</span>
            <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
          </label>
          <div className="search-actions">
            <button className="icon-button primary" type="submit" title="Search parsed text">
              <Search size={18} />
              <span>Search</span>
            </button>
            <button className="icon-button" type="button" onClick={loadRecent} title="Load recent parse jobs">
              <RefreshCw size={18} />
            </button>
          </div>
        </div>
        <div className="tag-criteria-row">
          <div className="tag-criteria-header">
            <span>Tags</span>
            <label className="field field-mode">
              <span>Mode</span>
              <select value={tagMode} onChange={(event) => changeTagMode(event.target.value as "any" | "all")}>
                <option value="any">Any</option>
                <option value="all">All</option>
              </select>
            </label>
          </div>
          <div className="tag-filter-list" aria-label="Report tag filters">
            {tags.map((tag) => {
              const selected = selectedTags.includes(tag.slug);
              return (
                <button
                  key={tag.slug}
                  className={`tag-filter-pill ${selected ? "selected" : ""}`}
                  type="button"
                  title={`${tag.label_en} · ${formatNumber(tag.assignment_count)} reports`}
                  aria-pressed={selected}
                  onClick={() => toggleTag(tag.slug)}
                >
                  <span>{tag.label_ja}</span>
                </button>
              );
            })}
          </div>
        </div>
      </form>

      <div className={`error-band ${error ? "" : "empty"}`}>{error}</div>

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
              {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((day) => (
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
            <div className="match-summary">
              <strong>{loading ? "Loading..." : `${formatNumber(total)} matches`}</strong>
              <span>{results.length} shown</span>
            </div>
            <div className="applied-criteria" aria-label="Applied search criteria">
              {appliedCriteriaItems.map((item) => (
                <span className="criteria-pill" key={`${item.label}:${item.value}`}>
                  <strong>{item.label}</strong>
                  <span>{item.value}</span>
                </span>
              ))}
            </div>
          </div>
          <div className="result-list">
            {results.map((result) => (
              <div
                key={result.parse_job_id}
                className={`result-row ${selectedId === result.parse_job_id ? "selected" : ""}`}
                role="button"
                tabIndex={0}
                aria-current={selectedId === result.parse_job_id ? "true" : undefined}
                onClick={() => setSelectedId(result.parse_job_id)}
                onKeyDown={(event) => selectResultFromKeyboard(event, result.parse_job_id)}
              >
                <div className="result-line">
                  <span className="date">{result.disclosure_date}</span>
                  <span className="time">{result.time}</span>
                  <span className="code">{result.code}</span>
                  <span className="company">{result.company_name}</span>
                </div>
                <div className="result-title">{result.title}</div>
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
              </div>
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
