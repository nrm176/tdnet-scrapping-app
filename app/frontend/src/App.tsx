import { type FormEvent, type KeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Building2,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  Clock,
  Database,
  ExternalLink,
  FileText,
  History,
  RefreshCw,
  Save,
  Search,
  X,
} from "lucide-react";
import {
  fetchCompanyTimeline,
  fetchPipelineRun,
  fetchPipelineRuns,
  fetchParseJob,
  fetchParsers,
  fetchParserQuality,
  fetchReportCalendar,
  fetchReviewQueue,
  fetchTags,
  pageImageUrl,
  searchParseTexts,
  updateParseJobReview,
} from "./api";
import type {
  CompanyTimelineDisclosure,
  CompanyTimelineResponse,
  FinancialFact,
  FinancialFactsAnalysis,
  FinancialFactValue,
  FinancialMetricDelta,
  PipelineRunDetail,
  PipelineRunStep,
  PipelineRunSummary,
  ParseJobDetail,
  ParseReviewDecision,
  ParseSearchResult,
  ParserOption,
  ParserQuality,
  ReportCalendarDay,
  ReportTag,
  ReviewState,
  ReviewStateFilter,
} from "./types";

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
  reviewState: ReviewStateFilterValue;
  parserValue: string;
  bestOnly: boolean;
};

type SearchCriteriaOverrides = Partial<Omit<SearchCriteria, "tags">> & {
  tags?: string[];
};

type ReviewStateFilterValue = "" | ReviewStateFilter;
type AppView = "documents" | "pipeline";

const EMPTY_CRITERIA: SearchCriteria = {
  titleQuery: "",
  textQuery: "",
  code: "",
  dateFrom: "",
  dateTo: "",
  tags: [],
  tagMode: "any",
  reviewState: "",
  parserValue: "",
  bestOnly: true,
};

const REVIEW_STATE_OPTIONS: { value: ReviewState; label: string }[] = [
  { value: "needs_review", label: "Needs review" },
  { value: "accepted", label: "Accepted" },
  { value: "bad_parse", label: "Bad parse" },
  { value: "prefer_ocr", label: "Prefer OCR" },
  { value: "prefer_ixbrl", label: "Prefer iXBRL" },
];

const REVIEW_FILTER_OPTIONS: { value: ReviewStateFilterValue; label: string }[] = [
  { value: "", label: "All" },
  { value: "unreviewed", label: "Unreviewed" },
  ...REVIEW_STATE_OPTIONS,
];

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

function formatElapsed(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) {
    return "Not finished";
  }
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const secs = rounded % 60;
  if (hours > 0) {
    return `${hours}h ${minutes.toString().padStart(2, "0")}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${secs.toString().padStart(2, "0")}s`;
  }
  return `${secs}s`;
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "Not finished";
  }
  return new Date(value).toLocaleString();
}

function pipelineStatusLabel(value: string): string {
  return value.replaceAll("_", " ");
}

function pipelineStatusClass(value: string): string {
  return value.replaceAll("_", "-");
}

function renderPipelineStatusBadge(status: string): ReactNode {
  return <span className={`pipeline-status-chip ${pipelineStatusClass(status)}`}>{pipelineStatusLabel(status)}</span>;
}

function summarizePipelineDateRange(run: PipelineRunSummary): string {
  if (!run.effective_start_date && !run.end_date) {
    return "No date range";
  }
  if (run.effective_start_date === run.end_date) {
    return run.effective_start_date ?? run.end_date ?? "No date range";
  }
  return `${run.effective_start_date ?? "any"} to ${run.end_date ?? "any"}`;
}

function summarizeStepMetrics(step: PipelineRunStep): string {
  const preferredKeys = [
    "candidate_files",
    "candidate_disclosures",
    "candidate_parse_jobs",
    "candidate_reports",
    "downloaded_files",
    "parsed_files",
    "persisted_rows",
    "tagged_reports",
    "ocr_completed_files",
    "failed_files",
    "failed_parse_jobs",
    "failed_reports",
    "files_per_second",
  ];
  const entries = preferredKeys
    .filter((key) => step.metrics[key] !== undefined)
    .map((key) => [key, step.metrics[key]] as const);
  const visibleEntries = entries.length ? entries : Object.entries(step.metrics).slice(0, 4);
  return visibleEntries
    .slice(0, 4)
    .map(([key, value]) => `${key.replaceAll("_", " ")} ${String(value)}`)
    .join(" · ");
}

function compactVersion(value: string): string {
  return value.length > 28 ? `${value.slice(0, 25)}...` : value;
}

function describeParserOption(option: ParserOption): string {
  return `${option.parser_name} · ${compactVersion(option.parser_version)}`;
}

function reviewStateLabel(value: ReviewStateFilterValue | ReviewState): string {
  return REVIEW_FILTER_OPTIONS.find((option) => option.value === value)?.label ?? value.replaceAll("_", " ");
}

function reviewStateClass(value: ReviewStateFilter | ReviewState): string {
  return value.replaceAll("_", "-");
}

function renderReviewDecisionBadge(decision: ParseReviewDecision | null): ReactNode {
  const state: ReviewStateFilter = decision?.review_state ?? "unreviewed";
  const title = decision
    ? `${reviewStateLabel(decision.review_state)}${decision.reviewer ? ` · ${decision.reviewer}` : ""}`
    : "Unreviewed";
  return (
    <span className={`review-state-chip ${reviewStateClass(state)}`} title={title}>
      {reviewStateLabel(state)}
    </span>
  );
}

function renderHighlightedText(text: string, query: string): ReactNode {
  const needle = query.trim();
  if (!needle || !text) {
    return text;
  }

  const loweredText = text.toLowerCase();
  const loweredNeedle = needle.toLowerCase();
  const parts: ReactNode[] = [];
  let cursor = 0;
  let hitIndex = loweredText.indexOf(loweredNeedle);
  let key = 0;

  while (hitIndex >= 0) {
    if (hitIndex > cursor) {
      parts.push(text.slice(cursor, hitIndex));
    }
    const end = hitIndex + needle.length;
    parts.push(
      <mark className="search-hit" key={`hit-${key}`}>
        {text.slice(hitIndex, end)}
      </mark>,
    );
    cursor = end;
    key += 1;
    hitIndex = loweredText.indexOf(loweredNeedle, cursor);
  }

  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }
  return parts.length ? parts : text;
}

function normalizeCompanyCode(value: string): string {
  return value.trim().toUpperCase();
}

function pageIndexForPage(detail: ParseJobDetail, page: number): number {
  const index = detail.pages.findIndex((candidate) => candidate.page === page);
  return index >= 0 ? index : 0;
}

function summarizeTimelineFiles(item: CompanyTimelineDisclosure): string {
  if (!item.files.length) {
    return "No files";
  }
  return item.files.map((file) => `${file.file_type}:${file.download_status}`).join(" · ");
}

function summarizeTimelineParsers(item: CompanyTimelineDisclosure): string {
  if (!item.parsers.length) {
    return "No parser jobs";
  }
  const completed = item.parsers.filter((parser) => parser.parse_status === "completed").length;
  const failed = item.parsers.filter((parser) => parser.parse_status === "failed").length;
  const textRows = item.parsers.filter((parser) => parser.has_text).length;
  return [
    completed ? `${formatNumber(completed)} completed` : "",
    failed ? `${formatNumber(failed)} failed` : "",
    textRows ? `${formatNumber(textRows)} text` : "",
  ]
    .filter(Boolean)
    .join(" · ") || `${formatNumber(item.parsers.length)} parser jobs`;
}

const METRIC_LABELS: Record<string, string> = {
  dividend_per_share: "Dividend/share",
  eps: "EPS",
  net_income: "Net income",
  net_sales: "Net sales",
  operating_profit: "Operating profit",
  ordinary_profit: "Ordinary profit",
};

const ROW_KIND_LABELS: Record<string, string> = {
  actual_result: "Actual",
  change_amount: "Change",
  change_percent: "Change %",
  current_forecast: "Current",
  previous_forecast: "Previous",
};

const COMPARISON_BASIS_LABELS: Record<string, string> = {
  actual_vs_forecast: "actual vs forecast",
  previous_forecast: "previous forecast",
  prior_year_comparable_period: "prior comparable",
  prior_year_full_year: "prior full year",
  prior_year_same_quarter: "prior same quarter",
  unknown_comparison: "comparison",
};

function formatMetricKey(value: string | null | undefined): string {
  if (!value) {
    return "Metric";
  }
  return METRIC_LABELS[value] ?? value.replaceAll("_", " ");
}

function formatRowKind(value: string | null | undefined): string {
  if (!value) {
    return "Row";
  }
  return ROW_KIND_LABELS[value] ?? value.replaceAll("_", " ");
}

function financialFactStatusClass(analysis: FinancialFactsAnalysis | null): string {
  if (!analysis) {
    return "empty";
  }
  if (analysis.status === "completed") {
    return analysis.summary.fact_count > 0 || analysis.summary.metric_delta_count > 0 ? "completed" : "empty";
  }
  if (analysis.status === "failed") {
    return "failed";
  }
  return "pending";
}

function financialFactBadgeText(analysis: FinancialFactsAnalysis): string {
  if (analysis.status !== "completed") {
    return `Facts ${analysis.status}`;
  }
  if (analysis.summary.metric_delta_count > 0) {
    return `Deltas ${formatNumber(analysis.summary.metric_delta_count)} · Facts ${formatNumber(analysis.summary.fact_count)}`;
  }
  if (analysis.summary.fact_count === 0) {
    return "Facts 0";
  }
  return analysis.summary.has_forecast_revision
    ? `Facts ${formatNumber(analysis.summary.fact_count)} · Forecast revision`
    : `Facts ${formatNumber(analysis.summary.fact_count)}`;
}

function formatDecimalNumber(value: number): string {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 2,
  }).format(value);
}

function formatDeltaValue(value: number | null | undefined, raw?: string | null): string {
  if (raw) {
    return raw;
  }
  if (value === null || value === undefined) {
    return "-";
  }
  return formatDecimalNumber(value);
}

function formatSignedDelta(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "-";
  }
  const formatted = formatDecimalNumber(Math.abs(value));
  if (value > 0) {
    return `+${formatted}`;
  }
  if (value < 0) {
    return `-${formatted}`;
  }
  return formatted;
}

function formatDeltaPercent(delta: FinancialMetricDelta): string {
  const value = delta.reported_change_pct ?? delta.computed_change_pct;
  if (value === null || value === undefined) {
    return "-";
  }
  return `${formatSignedDelta(value)}%`;
}

function deltaToneClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) {
    return "delta-flat";
  }
  return value > 0 ? "delta-positive" : "delta-negative";
}

function comparisonBasisLabel(delta: FinancialMetricDelta): string {
  return COMPARISON_BASIS_LABELS[delta.comparison_basis ?? ""] ?? (delta.comparison_basis || "comparison");
}

function formatFactValue(value: FinancialFactValue): string {
  const rawValue = value.raw ?? value.value;
  const valueText = rawValue === undefined || rawValue === null || rawValue === "" ? "-" : String(rawValue);
  const metric = typeof value.metric === "string" ? value.metric : null;
  const metricLabel = typeof value.metric_label_ja === "string" ? value.metric_label_ja : formatMetricKey(metric);
  return metric || value.metric_label_ja ? `${metricLabel} ${valueText}` : valueText;
}

function summarizeFactValues(fact: FinancialFact): string {
  if (!fact.values.length) {
    return "-";
  }
  return fact.values.slice(0, 5).map(formatFactValue).join(" · ");
}

function sourceText(source: { line_index: number | null; text: string } | null): string {
  if (!source) {
    return "";
  }
  return source.line_index ? `Line ${source.line_index}: ${source.text}` : source.text;
}

function sourceLabel(fact: FinancialFact): string {
  return sourceText(fact.source);
}

function renderMetricDeltaTable(deltas: FinancialMetricDelta[]): ReactNode {
  if (!deltas.length) {
    return null;
  }
  return (
    <div className="metric-delta-table" aria-label="Financial metric deltas">
      <div className="metric-delta-head" aria-hidden="true">
        <span>Metric</span>
        <span>Current</span>
        <span>Compare</span>
        <span>Change</span>
        <span>Change %</span>
        <span>Basis</span>
      </div>
      {deltas.slice(0, 8).map((delta, index) => {
        const changeClass = deltaToneClass(delta.change_value);
        const percentValue = delta.reported_change_pct ?? delta.computed_change_pct;
        return (
          <div className="metric-delta-row" key={`${delta.metric ?? "metric"}-${index}`}>
            <strong className="metric-delta-cell metric-label" data-label="Metric">
              {delta.metric_label_ja || formatMetricKey(delta.metric)}
            </strong>
            <span className="metric-delta-cell number" data-label="Current">
              {formatDeltaValue(delta.current_value, delta.current_raw)}
            </span>
            <span className="metric-delta-cell number" data-label="Compare">
              {formatDeltaValue(delta.comparison_value, delta.comparison_raw)}
            </span>
            <span className={`metric-delta-cell number delta-value ${changeClass}`} data-label="Change">
              {formatSignedDelta(delta.change_value)}
            </span>
            <span className={`metric-delta-cell number delta-value ${deltaToneClass(percentValue)}`} data-label="Change %">
              {formatDeltaPercent(delta)}
              {delta.change_pct_source ? <small>{delta.change_pct_source}</small> : null}
            </span>
            <span className="metric-delta-cell basis" data-label="Basis" title={delta.period ?? undefined}>
              {comparisonBasisLabel(delta)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function renderFinancialFactsBadge(analysis: FinancialFactsAnalysis | null): ReactNode {
  if (!analysis) {
    return null;
  }
  return (
    <span
      className={`fact-chip ${financialFactStatusClass(analysis)}`}
      title={`${analysis.analyzer_name} · ${compactVersion(analysis.analyzer_version)}`}
    >
      {financialFactBadgeText(analysis)}
    </span>
  );
}

function renderFinancialFactsPanel(analysis: FinancialFactsAnalysis | null): ReactNode {
  const statusClass = financialFactStatusClass(analysis);
  const metricDeltas = analysis?.metric_deltas ?? [];
  const forecastFacts = analysis?.facts.filter((fact) => fact.type === "forecast_revision_row") ?? [];
  const metricFacts = analysis?.facts.filter((fact) => fact.type === "metric_row") ?? [];
  const sourceFacts = (analysis?.facts ?? []).filter((fact) => fact.source).slice(0, 3);
  const deltaSourceLabels = metricDeltas
    .map((delta) => sourceText(delta.source))
    .filter((label) => label.length > 0)
    .filter((label, index, labels) => labels.indexOf(label) === index)
    .slice(0, 3);
  const sourceLabels = metricDeltas.length ? deltaSourceLabels : sourceFacts.map(sourceLabel).filter(Boolean);
  const hasCompletedContent =
    analysis?.status === "completed" && (analysis.summary.fact_count > 0 || metricDeltas.length > 0);

  return (
    <section className={`facts-panel ${statusClass}`} aria-label="Financial facts">
      <div className="facts-header">
        <div className="facts-title">
          <strong>Financial facts</strong>
          <span>
            {analysis
              ? `${analysis.status} · ${analysis.analyzer_name} · ${compactVersion(analysis.analyzer_version)}`
              : "No analysis row"}
          </span>
        </div>
        <div className="facts-chip-list">
          {analysis ? (
            <>
              <span className={`fact-chip ${statusClass}`}>{financialFactBadgeText(analysis)}</span>
              {analysis.summary.metric_delta_count > 0 ? (
                <span className="fact-chip">Deltas {formatNumber(analysis.summary.metric_delta_count)}</span>
              ) : null}
              <span className="fact-chip">Forecast rows {formatNumber(analysis.summary.forecast_revision_rows)}</span>
              {analysis.summary.metric_keys.slice(0, 4).map((metric) => (
                <span className="fact-chip" key={metric}>
                  {formatMetricKey(metric)}
                </span>
              ))}
            </>
          ) : (
            <span className="fact-chip empty">Not analyzed</span>
          )}
        </div>
      </div>

      {!analysis ? <div className="facts-message">No financial fact analysis exists for this parser output.</div> : null}
      {analysis?.status === "failed" ? (
        <div className="facts-message">{analysis.last_analysis_error || "Analysis failed without an error message."}</div>
      ) : null}
      {analysis && analysis.status !== "completed" && analysis.status !== "failed" ? (
        <div className="facts-message">Analysis is {analysis.status}.</div>
      ) : null}

      {hasCompletedContent ? (
        <div className="facts-body">
          {renderMetricDeltaTable(metricDeltas)}
          {!metricDeltas.length && forecastFacts.length ? (
            <div className="facts-table" aria-label="Forecast revision facts">
              {forecastFacts.slice(0, 5).map((fact, index) => (
                <div className="facts-row" key={`forecast-${index}`}>
                  <strong>{formatRowKind(fact.row_kind)}</strong>
                  <span>{summarizeFactValues(fact)}</span>
                </div>
              ))}
            </div>
          ) : null}
          {!metricDeltas.length && metricFacts.length ? (
            <div className="facts-table compact" aria-label="Metric facts">
              {metricFacts.slice(0, 4).map((fact, index) => (
                <div className="facts-row" key={`metric-${index}`}>
                  <strong>{fact.metric_label_ja || formatMetricKey(fact.metric)}</strong>
                  <span>{summarizeFactValues(fact)}</span>
                </div>
              ))}
            </div>
          ) : null}
          {sourceLabels.length ? (
            <div className="facts-sources" aria-label="Financial fact source lines">
              {sourceLabels.map((label, index) => (
                <span key={`source-${index}`}>{label}</span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
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
  const [activeView, setActiveView] = useState<AppView>("documents");
  const [parsers, setParsers] = useState<ParserOption[]>([]);
  const [parserQuality, setParserQuality] = useState<ParserQuality | null>(null);
  const [tags, setTags] = useState<ReportTag[]>([]);
  const [parserValue, setParserValue] = useState("");
  const [bestOnly, setBestOnly] = useState(true);
  const [titleQuery, setTitleQuery] = useState("");
  const [textQuery, setTextQuery] = useState("");
  const [code, setCode] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagMode, setTagMode] = useState<"any" | "all">("any");
  const [reviewStateFilter, setReviewStateFilter] = useState<ReviewStateFilterValue>("");
  const [calendarMonth, setCalendarMonth] = useState(currentMonthKey());
  const [calendarDays, setCalendarDays] = useState<ReportCalendarDay[]>([]);
  const [timelineCode, setTimelineCode] = useState("");
  const [timeline, setTimeline] = useState<CompanyTimelineResponse | null>(null);
  const [results, setResults] = useState<ParseSearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [appliedCriteria, setAppliedCriteria] = useState<SearchCriteria>(EMPTY_CRITERIA);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ParseJobDetail | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [targetPage, setTargetPage] = useState<number | null>(null);
  const [pendingScrollPage, setPendingScrollPage] = useState<number | null>(null);
  const [reviewFormState, setReviewFormState] = useState<ReviewState>("needs_review");
  const [reviewNotes, setReviewNotes] = useState("");
  const [reviewer, setReviewer] = useState(() => window.localStorage.getItem("tdnet-reviewer") ?? "");
  const [reviewModalOpen, setReviewModalOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [initialResultsLoaded, setInitialResultsLoaded] = useState(false);
  const [qualityLoading, setQualityLoading] = useState(false);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [pipelineRuns, setPipelineRuns] = useState<PipelineRunSummary[]>([]);
  const [pipelineTotal, setPipelineTotal] = useState(0);
  const [selectedPipelineRunId, setSelectedPipelineRunId] = useState<string | null>(null);
  const [pipelineDetail, setPipelineDetail] = useState<PipelineRunDetail | null>(null);
  const [pipelineLoading, setPipelineLoading] = useState(false);
  const [pipelineDetailLoading, setPipelineDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pdfPageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const textPageRefs = useRef<Map<number, HTMLElement>>(new Map());

  const selectedResult = useMemo(
    () => results.find((result) => result.parse_job_id === selectedId) ?? null,
    [results, selectedId],
  );
  const documentPages = detail?.pages ?? [];
  const currentPage = documentPages[pageIndex]?.page ?? documentPages[0]?.page ?? 1;
  const activeTimelineCode = useMemo(
    () => normalizeCompanyCode(timelineCode || appliedCriteria.code || detail?.code || results[0]?.code || ""),
    [timelineCode, appliedCriteria.code, detail?.code, results],
  );
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
    if (appliedCriteria.parserValue) {
      const selectedParser = parsers.find((option) => parserKey(option) === appliedCriteria.parserValue);
      return selectedParser ? describeParserOption(selectedParser) : appliedCriteria.parserValue.split("::")[0];
    }
    return appliedCriteria.bestOnly ? "Best parse per file" : "All parser outputs";
  }, [appliedCriteria.bestOnly, appliedCriteria.parserValue, parsers]);
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
    if (appliedCriteria.reviewState) {
      items.push({ label: "Review", value: reviewStateLabel(appliedCriteria.reviewState) });
    }
    return items;
  }, [appliedCriteria, appliedParserLabel, tagLabelBySlug]);
  const selectedDate = dateFrom && dateFrom === dateTo ? dateFrom : "";
  const visibleQualityParsers = useMemo(
    () =>
      [...(parserQuality?.parsers ?? [])]
        .sort(
          (left, right) =>
            right.parse_texts - left.parse_texts ||
            right.total_jobs - left.total_jobs ||
            right.failed_jobs - left.failed_jobs,
        )
        .slice(0, 4),
    [parserQuality],
  );
  const latestParserError = parserQuality?.recent_errors[0] ?? null;

  function buildCriteria(overrides: SearchCriteriaOverrides = {}): SearchCriteria {
    return {
      titleQuery: overrides.titleQuery ?? titleQuery,
      textQuery: overrides.textQuery ?? textQuery,
      code: overrides.code ?? code,
      dateFrom: overrides.dateFrom ?? dateFrom,
      dateTo: overrides.dateTo ?? dateTo,
      tags: overrides.tags ?? selectedTags,
      tagMode: overrides.tagMode ?? tagMode,
      reviewState: overrides.reviewState ?? reviewStateFilter,
      parserValue: overrides.parserValue ?? parserValue,
      bestOnly: overrides.bestOnly ?? bestOnly,
    };
  }

  async function loadParsers() {
    const options = await fetchParsers();
    setError(null);
    setParsers(options);
  }

  async function loadParserQuality() {
    setQualityLoading(true);
    try {
      const summary = await fetchParserQuality();
      setError(null);
      setParserQuality(summary);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load parser quality");
    } finally {
      setQualityLoading(false);
    }
  }

  async function loadTags() {
    const options = await fetchTags();
    setError(null);
    setTags(options.filter((option) => option.active));
  }

  async function loadPipelineRuns(nextSelectedRunId?: string | null) {
    setPipelineLoading(true);
    try {
      const response = await fetchPipelineRuns({ limit: 25 });
      setError(null);
      setPipelineRuns(response.runs);
      setPipelineTotal(response.total);
      const nextId = nextSelectedRunId ?? selectedPipelineRunId ?? response.runs[0]?.run_id ?? null;
      setSelectedPipelineRunId(nextId);
      if (!nextId) {
        setPipelineDetail(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load pipeline runs");
    } finally {
      setPipelineLoading(false);
    }
  }

  async function loadPipelineRunDetail(runId: string) {
    setPipelineDetailLoading(true);
    try {
      const response = await fetchPipelineRun(runId);
      setError(null);
      setPipelineDetail(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load pipeline run");
    } finally {
      setPipelineDetailLoading(false);
    }
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
        reviewState: criteria.reviewState || undefined,
        bestOnly: criteria.bestOnly,
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
        reviewState: appliedCriteria.reviewState || undefined,
        bestOnly: appliedCriteria.bestOnly,
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

  async function loadCompanyTimeline(nextCode = activeTimelineCode) {
    const normalizedCode = normalizeCompanyCode(nextCode);
    if (!normalizedCode) {
      setTimeline(null);
      return;
    }

    setTimelineLoading(true);
    const selection = parseParserKey(appliedCriteria.parserValue);
    try {
      const response = await fetchCompanyTimeline({
        code: normalizedCode,
        titleQuery: appliedCriteria.titleQuery,
        textQuery: appliedCriteria.textQuery,
        dateFrom: appliedCriteria.dateFrom,
        dateTo: appliedCriteria.dateTo,
        tags: appliedCriteria.tags,
        tagMode: appliedCriteria.tagMode,
        reviewState: appliedCriteria.reviewState || undefined,
        bestOnly: appliedCriteria.bestOnly,
        ...selection,
        limit: 50,
      });
      setError(null);
      setTimeline(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load company timeline");
    } finally {
      setTimelineLoading(false);
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
        reviewState: criteria.reviewState || undefined,
        bestOnly: criteria.bestOnly,
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
    selectParseJob(parseJobId);
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

  function changeBestOnly(value: boolean) {
    setBestOnly(value);
    runSearch(undefined, { bestOnly: value }).catch((err) =>
      setError(err instanceof Error ? err.message : "Search failed"),
    );
  }

  function changeReviewStateFilter(value: ReviewStateFilterValue) {
    setReviewStateFilter(value);
    runSearch(undefined, { reviewState: value }).catch((err) =>
      setError(err instanceof Error ? err.message : "Search failed"),
    );
  }

  function applyReviewDecision(decision: ParseReviewDecision) {
    setDetail((current) =>
      current?.parse_job_id === decision.parse_job_id ? { ...current, review_decision: decision } : current,
    );
    setResults((current) =>
      current.map((result) =>
        result.parse_job_id === decision.parse_job_id ? { ...result, review_decision: decision } : result,
      ),
    );
    setTimeline((current) =>
      current
        ? {
            ...current,
            results: current.results.map((item) => ({
              ...item,
              review_decision:
                item.best_parse_job_id === decision.parse_job_id ? decision : item.review_decision,
              parsers: item.parsers.map((parser) =>
                parser.parse_job_id === decision.parse_job_id
                  ? { ...parser, review_decision: decision }
                  : parser,
              ),
            })),
          }
        : current,
    );
  }

  function openReviewModal() {
    if (!detail) {
      return;
    }
    setReviewFormState(detail.review_decision?.review_state ?? "needs_review");
    setReviewNotes(detail.review_decision?.notes ?? "");
    setReviewer((current) => detail.review_decision?.reviewer ?? current);
    setReviewModalOpen(true);
  }

  function rememberPdfPage(page: number, node: HTMLDivElement | null) {
    if (node) {
      pdfPageRefs.current.set(page, node);
    } else {
      pdfPageRefs.current.delete(page);
    }
  }

  function rememberTextPage(page: number, node: HTMLElement | null) {
    if (node) {
      textPageRefs.current.set(page, node);
    } else {
      textPageRefs.current.delete(page);
    }
  }

  function jumpToDocumentPage(page: number) {
    if (!detail) {
      return;
    }
    setPageIndex(pageIndexForPage(detail, page));
    setPendingScrollPage(page);
  }

  function syncPageFromScroll(container: HTMLElement, pageRefs: Map<number, HTMLElement | HTMLDivElement>) {
    if (!detail?.pages.length) {
      return;
    }
    const containerTop = container.getBoundingClientRect().top;
    const threshold = containerTop + 28;
    let nextPage = detail.pages[0].page;
    for (const page of detail.pages) {
      const node = pageRefs.get(page.page);
      if (!node) {
        continue;
      }
      const rect = node.getBoundingClientRect();
      if (rect.bottom >= threshold) {
        nextPage = page.page;
        break;
      }
    }
    setPageIndex((current) => {
      const nextIndex = pageIndexForPage(detail, nextPage);
      return nextIndex === current ? current : nextIndex;
    });
  }

  async function saveReviewDecision() {
    if (!detail) {
      return;
    }
    setReviewSaving(true);
    try {
      const cleanReviewer = reviewer.trim();
      const decision = await updateParseJobReview(detail.parse_job_id, {
        review_state: reviewFormState,
        reviewer: cleanReviewer || null,
        notes: reviewNotes,
      });
      if (cleanReviewer) {
        window.localStorage.setItem("tdnet-reviewer", cleanReviewer);
      }
      setError(null);
      applyReviewDecision(decision);
      setReviewModalOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save review decision");
    } finally {
      setReviewSaving(false);
    }
  }

  function selectParseJob(parseJobId: number, page?: number) {
    if (detail?.parse_job_id === parseJobId && page !== undefined) {
      jumpToDocumentPage(page);
      return;
    }
    setTargetPage(page ?? null);
    setSelectedId(parseJobId);
  }

  function openCompanyTimeline(nextCode: string) {
    setTimelineCode(normalizeCompanyCode(nextCode));
  }

  useEffect(() => {
    loadParsers().catch((err) => setError(err instanceof Error ? err.message : "Failed to load parsers"));
    loadParserQuality().catch((err) => setError(err instanceof Error ? err.message : "Failed to load parser quality"));
    loadTags().catch((err) => setError(err instanceof Error ? err.message : "Failed to load tags"));
  }, []);

  useEffect(() => {
    if (parsers.length && !initialResultsLoaded) {
      setInitialResultsLoaded(true);
      loadRecent().catch((err) => setError(err instanceof Error ? err.message : "Failed to load recent parses"));
    }
  }, [parsers.length, initialResultsLoaded]);

  useEffect(() => {
    if (activeView === "pipeline" && !pipelineRuns.length) {
      loadPipelineRuns().catch((err) => setError(err instanceof Error ? err.message : "Failed to load pipeline runs"));
    }
  }, [activeView]);

  useEffect(() => {
    if (activeView !== "pipeline") {
      return;
    }
    if (!selectedPipelineRunId) {
      setPipelineDetail(null);
      return;
    }
    loadPipelineRunDetail(selectedPipelineRunId).catch((err) =>
      setError(err instanceof Error ? err.message : "Failed to load pipeline run"),
    );
  }, [activeView, selectedPipelineRunId]);

  useEffect(() => {
    if (parsers.length) {
      loadCalendar().catch((err) => setError(err instanceof Error ? err.message : "Failed to load calendar"));
    }
  }, [calendarMonth, parsers.length, appliedCriteria]);

  useEffect(() => {
    if (!activeTimelineCode) {
      setTimeline(null);
      return;
    }
    loadCompanyTimeline(activeTimelineCode).catch((err) =>
      setError(err instanceof Error ? err.message : "Failed to load company timeline"),
    );
  }, [activeTimelineCode, appliedCriteria]);

  useEffect(() => {
    const activeDate = dateFrom || dateTo;
    if (activeDate.length >= 7) {
      setCalendarMonth(activeDate.slice(0, 7));
    }
  }, [dateFrom, dateTo]);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      setPendingScrollPage(null);
      setReviewModalOpen(false);
      return;
    }
    setReviewModalOpen(false);
    setDetailLoading(true);
    setPageIndex(0);
    setPendingScrollPage(null);
    const requestedPage = targetPage;
    fetchParseJob(selectedId)
      .then((value) => {
        setError(null);
        pdfPageRefs.current.clear();
        textPageRefs.current.clear();
        setDetail(value);
        setPageIndex(requestedPage !== null ? pageIndexForPage(value, requestedPage) : 0);
        setPendingScrollPage(requestedPage ?? value.pages[0]?.page ?? 1);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load parse job"))
      .finally(() => {
        setTargetPage(null);
        setDetailLoading(false);
      });
  }, [selectedId]);

  useEffect(() => {
    setReviewFormState(detail?.review_decision?.review_state ?? "needs_review");
    setReviewNotes(detail?.review_decision?.notes ?? "");
    setReviewer((current) => detail?.review_decision?.reviewer ?? current);
  }, [
    detail?.parse_job_id,
    detail?.review_decision?.review_state,
    detail?.review_decision?.notes,
    detail?.review_decision?.reviewer,
  ]);

  useEffect(() => {
    if (!detail || pendingScrollPage === null) {
      return undefined;
    }
    const frame = window.requestAnimationFrame(() => {
      pdfPageRefs.current.get(pendingScrollPage)?.scrollIntoView({ block: "start", behavior: "smooth" });
      textPageRefs.current.get(pendingScrollPage)?.scrollIntoView({ block: "start", behavior: "smooth" });
      setPendingScrollPage(null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [detail?.parse_job_id, pendingScrollPage]);

  useEffect(() => {
    if (!reviewModalOpen) {
      return undefined;
    }
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape" && !reviewSaving) {
        setReviewModalOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [reviewModalOpen, reviewSaving]);

  function renderPipelineView(): ReactNode {
    return (
      <main className="pipeline-workspace">
        <aside className="pipeline-runs-pane">
          <div className="pane-header pipeline-pane-header">
            <div className="match-summary">
              <strong>{pipelineLoading ? "Loading..." : `${formatNumber(pipelineTotal)} runs`}</strong>
              <span>{pipelineRuns.length} shown</span>
            </div>
            <button
              className="square-button"
              type="button"
              title="Refresh pipeline runs"
              disabled={pipelineLoading}
              onClick={() => {
                loadPipelineRuns(selectedPipelineRunId).catch((err) =>
                  setError(err instanceof Error ? err.message : "Failed to load pipeline runs"),
                );
              }}
            >
              <RefreshCw size={16} />
            </button>
          </div>
          <div className="pipeline-run-list">
            {pipelineRuns.map((run) => (
              <button
                key={run.run_id}
                className={`pipeline-run-row ${run.run_id === selectedPipelineRunId ? "selected" : ""}`}
                type="button"
                onClick={() => setSelectedPipelineRunId(run.run_id)}
              >
                <div className="pipeline-run-row-main">
                  <span className="pipeline-run-id">{run.run_id}</span>
                  {renderPipelineStatusBadge(run.status)}
                </div>
                <div className="pipeline-run-row-meta">
                  <span>{summarizePipelineDateRange(run)}</span>
                  <span>{formatElapsed(run.elapsed_seconds)}</span>
                </div>
                <div className="pipeline-run-row-meta">
                  <span>
                    {formatNumber(run.completed_steps)}/{formatNumber(run.step_count)} steps
                  </span>
                  {run.failed_steps ? <span>{formatNumber(run.failed_steps)} failed</span> : null}
                  {run.skipped_steps ? <span>{formatNumber(run.skipped_steps)} skipped</span> : null}
                </div>
              </button>
            ))}
            {!pipelineLoading && !pipelineRuns.length ? (
              <div className="empty-state">
                <History size={22} />
                <span>No pipeline runs have been persisted yet.</span>
              </div>
            ) : null}
          </div>
        </aside>

        <section className="pipeline-detail-pane">
          {pipelineDetailLoading ? (
            <div className="loading-state">Loading pipeline run...</div>
          ) : pipelineDetail ? (
            <>
              <div className="pipeline-detail-header">
                <div>
                  <div className="document-title">{pipelineDetail.run_id}</div>
                  <div className="document-meta">
                    <span>{formatDateTime(pipelineDetail.started_at)}</span>
                    <span>{summarizePipelineDateRange(pipelineDetail)}</span>
                    <span>{formatNumber(pipelineDetail.date_count)} days</span>
                  </div>
                </div>
                <div className="pipeline-detail-actions">
                  {renderPipelineStatusBadge(pipelineDetail.status)}
                  <span className="status-pill compact">
                    <Clock size={15} />
                    <span>{formatElapsed(pipelineDetail.elapsed_seconds)}</span>
                  </span>
                </div>
              </div>

              <div className="pipeline-summary-grid" aria-label="Pipeline run summary">
                <div>
                  <span>Checkpoint</span>
                  <strong>
                    {pipelineDetail.checkpoint_applied
                      ? `Applied ${pipelineDetail.checkpoint_start_date ?? ""}`
                      : pipelineDetail.checkpoint_disabled_reason
                        ? `Skipped ${pipelineDetail.checkpoint_disabled_reason}`
                        : "Not applied"}
                  </strong>
                </div>
                <div>
                  <span>Steps</span>
                  <strong>
                    {formatNumber(pipelineDetail.completed_steps)}/{formatNumber(pipelineDetail.step_count)} completed
                  </strong>
                </div>
                <div>
                  <span>Failures</span>
                  <strong>{pipelineDetail.failed_step ?? (pipelineDetail.failed_steps ? "Step failure" : "None")}</strong>
                </div>
                <div>
                  <span>Log</span>
                  <strong title={pipelineDetail.log_path}>{pipelineDetail.log_path}</strong>
                </div>
              </div>

              <div className="pipeline-step-table" aria-label="Pipeline step summaries">
                <div className="pipeline-step-table-header">
                  <span>Step</span>
                  <span>Status</span>
                  <span>Elapsed</span>
                  <span>Metrics</span>
                </div>
                {pipelineDetail.steps.map((step) => (
                  <div className="pipeline-step-row" key={step.id}>
                    <div>
                      <strong>{step.step_name}</strong>
                      <span title={step.command ?? step.reason ?? ""}>{step.command ?? step.reason ?? ""}</span>
                    </div>
                    <div>{renderPipelineStatusBadge(step.status)}</div>
                    <span>{formatElapsed(step.elapsed_seconds)}</span>
                    <span title={step.error_context ?? summarizeStepMetrics(step)}>
                      {summarizeStepMetrics(step) || step.error_context || "No metrics"}
                    </span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="empty-review">
              <History size={28} />
              <span>Select a pipeline run to inspect step history.</span>
            </div>
          )}
        </section>
      </main>
    );
  }

  return (
    <div className={`app-shell ${activeView === "pipeline" ? "pipeline-shell" : ""}`}>
      <header className="topbar">
        <div>
          <h1>TDnet Review</h1>
          <p>
            {activeView === "documents"
              ? "Search persisted parse text and inspect parser output against the source PDF."
              : "Inspect persisted all-in-one pipeline runs and step metrics."}
          </p>
        </div>
        <div className="topbar-actions">
          <div className="view-switch" aria-label="App view">
            <button
              className={activeView === "documents" ? "selected" : ""}
              type="button"
              onClick={() => {
                setReviewModalOpen(false);
                setActiveView("documents");
              }}
            >
              <FileText size={15} />
              <span>Documents</span>
            </button>
            <button
              className={activeView === "pipeline" ? "selected" : ""}
              type="button"
              onClick={() => {
                setReviewModalOpen(false);
                setActiveView("pipeline");
              }}
            >
              <History size={15} />
              <span>Runs</span>
            </button>
          </div>
          <div className="status-pill" title={activeView === "documents" ? "Postgres-backed parsed text cache" : "Persisted pipeline runs"}>
            {activeView === "documents" ? <Database size={16} /> : <CheckCircle2 size={16} />}
            <span>
              {activeView === "documents"
                ? `${formatNumber(parsers.reduce((sum, option) => sum + option.parse_texts, 0))} text rows`
                : `${formatNumber(pipelineTotal)} runs`}
            </span>
          </div>
        </div>
      </header>

      {activeView === "documents" ? (
      <section className="quality-strip" aria-label="Parser quality dashboard">
        <div className="quality-heading">
          <div className="quality-title">
            <Activity size={16} />
            <strong>Parser quality</strong>
            <span>{qualityLoading ? "Loading" : `${formatNumber(parserQuality?.total_jobs ?? 0)} jobs`}</span>
          </div>
          <button
            className="square-button"
            type="button"
            title="Refresh parser quality"
            disabled={qualityLoading}
            onClick={() => {
              loadParserQuality().catch((err) =>
                setError(err instanceof Error ? err.message : "Failed to load parser quality"),
              );
            }}
          >
            <RefreshCw size={16} />
          </button>
        </div>
        <div className="quality-metrics">
          <div className="quality-metric">
            <span>Completed</span>
            <strong>{formatNumber(parserQuality?.completed_jobs ?? 0)}</strong>
          </div>
          <div className={`quality-metric ${(parserQuality?.failed_jobs ?? 0) > 0 ? "warning" : ""}`}>
            <span>Failed</span>
            <strong>{formatNumber(parserQuality?.failed_jobs ?? 0)}</strong>
          </div>
          <div className="quality-metric">
            <span>Text rows</span>
            <strong>{formatNumber(parserQuality?.parse_texts ?? 0)}</strong>
          </div>
          <div className={`quality-metric ${(parserQuality?.low_text_jobs ?? 0) > 0 ? "notice" : ""}`}>
            <span>Low text</span>
            <strong>{formatNumber(parserQuality?.low_text_jobs ?? 0)}</strong>
          </div>
          {(parserQuality?.fallback_candidates ?? []).map((candidate) => (
            <div className="quality-metric notice" key={candidate.parser_name} title={candidate.description}>
              <span>{candidate.name}</span>
              <strong>{formatNumber(candidate.candidate_count)}</strong>
            </div>
          ))}
        </div>
        <div className="quality-parser-list">
          {visibleQualityParsers.map((parser) => (
            <div className="quality-parser-row" key={`${parser.parser_name}:${parser.parser_version}`}>
              <div>
                <strong>{parser.parser_name}</strong>
                <span title={parser.parser_version}>{compactVersion(parser.parser_version)}</span>
              </div>
              <span>
                {formatNumber(parser.completed_jobs)}/{formatNumber(parser.total_jobs)} jobs ·{" "}
                {formatNumber(parser.parse_texts)} text · {formatNumber(parser.low_text_jobs)} low
              </span>
            </div>
          ))}
        </div>
        {latestParserError ? (
          <div className="quality-error" title={latestParserError.error}>
            <AlertTriangle size={15} />
            <span>
              {latestParserError.parser_name} #{latestParserError.parse_job_id}: {latestParserError.error}
            </span>
          </div>
        ) : null}
      </section>
      ) : null}

      {activeView === "documents" ? (
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
              <option value="">{bestOnly ? "Best parse per file" : "All parser outputs"}</option>
              {parsers.map((option) => (
                <option key={parserKey(option)} value={parserKey(option)}>
                  {describeParserOption(option)} · {option.parse_texts}/{option.parse_jobs}
                </option>
              ))}
            </select>
          </label>
          <label className="field field-review-filter">
            <span>Review</span>
            <select
              value={reviewStateFilter}
              onChange={(event) => changeReviewStateFilter(event.target.value as ReviewStateFilterValue)}
            >
              {REVIEW_FILTER_OPTIONS.map((option) => (
                <option key={option.value || "all"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="checkbox-field field-best-only" title="Use the highest-priority parser output per file">
            <input type="checkbox" checked={bestOnly} onChange={(event) => changeBestOnly(event.target.checked)} />
            <span>Best parse only</span>
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
      ) : null}

      <div className={`error-band ${error ? "" : "empty"}`}>{error}</div>

      {activeView === "pipeline" ? renderPipelineView() : (
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
          <section className="timeline-panel" aria-label="Company timeline">
            <div className="timeline-header">
              <div className="timeline-title">
                <Building2 size={16} />
                <strong>{activeTimelineCode || "Company"}</strong>
                <span>
                  {timelineLoading
                    ? "Loading"
                    : timeline
                      ? `${timeline.company_name ?? "Unknown"} · ${formatNumber(timeline.total)} disclosures`
                      : "No company selected"}
                </span>
              </div>
              <button
                className="square-button"
                type="button"
                title="Refresh company timeline"
                disabled={!activeTimelineCode}
                onClick={() => loadCompanyTimeline()}
              >
                <RefreshCw size={16} />
              </button>
            </div>
            <div className="timeline-list">
              {timeline?.results.map((item) => {
                const canOpenParse = item.best_parse_job_id !== null;
                return (
                  <button
                    key={item.disclosure_id}
                    className={`timeline-row ${item.best_parse_job_id === selectedId ? "selected" : ""}`}
                    type="button"
                    title={canOpenParse ? "Open best parse for this disclosure" : "No parsed text available"}
                    disabled={!canOpenParse}
                    onClick={() => {
                      if (item.best_parse_job_id !== null) {
                        selectParseJob(item.best_parse_job_id);
                      }
                    }}
                  >
                    <div className="timeline-row-main">
                      <span className="date">{item.disclosure_date}</span>
                      <span className="time">{item.time}</span>
                      <span className="timeline-row-title">{item.title}</span>
                    </div>
                    {item.tags.length ? (
                      <div className="tag-chip-list timeline-tags">
                        {item.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag.slug}
                            className={`tag-chip ${tag.is_primary ? "primary" : ""}`}
                            title={`${tag.label_en} · ${tag.source}`}
                          >
                            {tag.label_ja}
                          </span>
                        ))}
                        {item.tags.length > 3 ? (
                          <span className="tag-chip">+{formatNumber(item.tags.length - 3)}</span>
                        ) : null}
                      </div>
                    ) : null}
                    <div className="timeline-row-meta">
                      <span>{summarizeTimelineFiles(item)}</span>
                      <span>{summarizeTimelineParsers(item)}</span>
                      {renderFinancialFactsBadge(item.financial_facts)}
                      {renderReviewDecisionBadge(item.review_decision)}
                    </div>
                    {item.snippet ? (
                      <div className="timeline-snippet">
                        {renderHighlightedText(item.snippet, appliedCriteria.textQuery)}
                      </div>
                    ) : null}
                  </button>
                );
              })}
              {!timelineLoading && timeline && !timeline.results.length ? (
                <div className="timeline-empty">No disclosures matched.</div>
              ) : null}
              {!timelineLoading && !timeline ? <div className="timeline-empty">Select a company code.</div> : null}
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
                onClick={() => selectParseJob(result.parse_job_id)}
                onKeyDown={(event) => selectResultFromKeyboard(event, result.parse_job_id)}
              >
                <div className="result-line">
                  <span className="date">{result.disclosure_date}</span>
                  <span className="time">{result.time}</span>
                  <span className="code">{result.code}</span>
                  <span className="company">{result.company_name}</span>
                </div>
                <div className="result-title">{renderHighlightedText(result.title, appliedCriteria.titleQuery)}</div>
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
                {result.financial_facts ? (
                  <div className="facts-chip-list result-facts">{renderFinancialFactsBadge(result.financial_facts)}</div>
                ) : null}
                <div className="review-chip-list">{renderReviewDecisionBadge(result.review_decision)}</div>
                <div className="snippet">
                  {result.snippet ? renderHighlightedText(result.snippet, appliedCriteria.textQuery) : "No snippet available."}
                </div>
                {result.matched_pages.length ? (
                  <div className="page-match-list" aria-label="Matched pages">
                    {result.matched_pages.map((match) => (
                      <button
                        className="page-match-button"
                        type="button"
                        key={`${result.parse_job_id}:${match.page}`}
                        title={match.snippet}
                        onClick={(event) => {
                          event.stopPropagation();
                          selectParseJob(result.parse_job_id, match.page);
                        }}
                      >
                        Page {match.page}
                      </button>
                    ))}
                  </div>
                ) : null}
                <div className="result-meta">
                  <span>{result.parser_name}</span>
                  <span>{result.page_count}p</span>
                  <span>{formatNumber(result.char_count)} chars</span>
                  <button
                    className="timeline-link-button"
                    type="button"
                    title={`Open timeline for ${result.code}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      openCompanyTimeline(result.code);
                    }}
                  >
                    <Building2 size={13} />
                    <span>Timeline</span>
                  </button>
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
                  <div className="document-title">{renderHighlightedText(detail.title, appliedCriteria.titleQuery)}</div>
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
                  <div className="document-review-summary">
                    {renderReviewDecisionBadge(detail.review_decision)}
                    <span>
                      {detail.review_decision?.reviewed_at
                        ? `Saved ${new Date(detail.review_decision.reviewed_at).toLocaleString()}`
                        : "No saved decision"}
                    </span>
                  </div>
                </div>
                <div className="document-actions">
                  <button className="icon-button primary" type="button" onClick={openReviewModal}>
                    <ClipboardCheck size={17} />
                    <span>Review</span>
                  </button>
                  <button
                    className="icon-button"
                    type="button"
                    title={`Open timeline for ${detail.code}`}
                    onClick={() => openCompanyTimeline(detail.code)}
                  >
                    <Building2 size={17} />
                    <span>Timeline</span>
                  </button>
                  <a className="icon-button link-button" href={detail.source_url} target="_blank" rel="noreferrer">
                    <ExternalLink size={17} />
                    <span>TDnet PDF</span>
                  </a>
                </div>
              </div>

              {renderFinancialFactsPanel(detail.financial_facts)}

              <div className="page-toolbar">
                <span className="page-scroll-status">
                  Page {currentPage} of {Math.max(1, detail.pages.length)}
                </span>
                {selectedResult?.matched_pages.length ? (
                  <div className="page-toolbar-matches" aria-label="Matched page jumps">
                    {selectedResult.matched_pages.map((match) => (
                      <button
                        className={`page-match-button ${currentPage === match.page ? "selected" : ""}`}
                        type="button"
                        key={`${detail.parse_job_id}:${match.page}`}
                        title={match.snippet}
                        onClick={() => jumpToDocumentPage(match.page)}
                      >
                        Page {match.page}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>

              <div className="review-grid">
                <div
                  className="pdf-panel"
                  onScroll={(event) => syncPageFromScroll(event.currentTarget, pdfPageRefs.current)}
                >
                  {documentPages.map((page) => (
                    <div className="pdf-page" key={page.page} ref={(node) => rememberPdfPage(page.page, node)}>
                      <div className="page-label">Page {page.page}</div>
                      <img
                        loading="lazy"
                        src={pageImageUrl(detail.parse_job_id, page.page)}
                        alt={`PDF page ${page.page}`}
                      />
                    </div>
                  ))}
                </div>
                <div
                  className="text-panel"
                  onScroll={(event) => syncPageFromScroll(event.currentTarget, textPageRefs.current)}
                >
                  {documentPages.length ? (
                    documentPages.map((page) => (
                      <section
                        className="parsed-page"
                        key={page.page}
                        ref={(node) => rememberTextPage(page.page, node)}
                      >
                        <div className="page-label">Page {page.page}</div>
                        <pre>{renderHighlightedText(page.markdown, appliedCriteria.textQuery)}</pre>
                      </section>
                    ))
                  ) : (
                    <section className="parsed-page">
                      <div className="page-label">Parsed text</div>
                      <pre>{renderHighlightedText(detail.content_text, appliedCriteria.textQuery)}</pre>
                    </section>
                  )}
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
      )}

      {reviewModalOpen && detail ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !reviewSaving) {
              setReviewModalOpen(false);
            }
          }}
        >
          <section
            className="review-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="review-modal-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="review-modal-header">
              <div>
                <strong id="review-modal-title">Manual review</strong>
                <span>
                  {detail.code} · {detail.company_name} · {detail.parser_name}
                </span>
              </div>
              <button
                className="square-button"
                type="button"
                title="Close review"
                disabled={reviewSaving}
                onClick={() => setReviewModalOpen(false)}
              >
                <X size={18} />
              </button>
            </div>
            <div className="review-modal-context">
              <span>{detail.title}</span>
              {renderReviewDecisionBadge(detail.review_decision)}
            </div>
            <form
              className="review-modal-form"
              onSubmit={(event) => {
                event.preventDefault();
                saveReviewDecision().catch((err) =>
                  setError(err instanceof Error ? err.message : "Failed to save review decision"),
                );
              }}
            >
              <label className="field field-review-state">
                <span>State</span>
                <select
                  value={reviewFormState}
                  onChange={(event) => setReviewFormState(event.target.value as ReviewState)}
                >
                  {REVIEW_STATE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field reviewer-field">
                <span>Reviewer</span>
                <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} />
              </label>
              <label className="field review-notes">
                <span>Notes</span>
                <textarea value={reviewNotes} onChange={(event) => setReviewNotes(event.target.value)} />
              </label>
              <div className="review-modal-actions">
                <button
                  className="icon-button"
                  type="button"
                  disabled={reviewSaving}
                  onClick={() => setReviewModalOpen(false)}
                >
                  <span>Cancel</span>
                </button>
                <button className="icon-button primary review-save-button" type="submit" disabled={reviewSaving}>
                  <Save size={17} />
                  <span>{reviewSaving ? "Saving" : "Save"}</span>
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}
    </div>
  );
}

export default App;
