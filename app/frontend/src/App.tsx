import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Database,
  ExternalLink,
  FileText,
  RefreshCw,
  Search,
} from "lucide-react";
import {
  fetchParseJob,
  fetchParsers,
  fetchReviewQueue,
  pageImageUrl,
  searchParseTexts,
} from "./api";
import type { ParseJobDetail, ParseSearchResult, ParserOption } from "./types";

type ParserSelection = {
  parserName?: string;
  parserVersion?: string;
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

function App() {
  const [parsers, setParsers] = useState<ParserOption[]>([]);
  const [parserValue, setParserValue] = useState("");
  const [query, setQuery] = useState("");
  const [code, setCode] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [results, setResults] = useState<ParseSearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ParseJobDetail | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parserSelection = useMemo(() => parseParserKey(parserValue), [parserValue]);
  const selectedPage = detail?.pages[pageIndex] ?? detail?.pages[0] ?? null;
  const visibleText = selectedPage?.markdown || detail?.content_text || "";

  async function loadParsers() {
    const options = await fetchParsers();
    setParsers(options);
    if (!parserValue && options.length) {
      setParserValue(parserKey(options[0]));
    }
  }

  async function loadRecent() {
    setLoading(true);
    setError(null);
    try {
      const response = await fetchReviewQueue({
        ...parserSelection,
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

  async function runSearch(event?: FormEvent) {
    event?.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await searchParseTexts({
        q: query,
        code,
        dateFrom,
        dateTo,
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

  useEffect(() => {
    loadParsers().catch((err) => setError(err instanceof Error ? err.message : "Failed to load parsers"));
  }, []);

  useEffect(() => {
    if (parsers.length && !results.length) {
      loadRecent().catch((err) => setError(err instanceof Error ? err.message : "Failed to load recent parses"));
    }
  }, [parsers.length, parserValue]);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    setPageIndex(0);
    fetchParseJob(selectedId)
      .then((value) => setDetail(value))
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
