import { useCallback, useEffect, useRef, useState } from "react";
import { applyActivityRefresh, startActivityRefresh } from "./activityRefresh.js";
import {
  explainPush,
  getMappingStatus,
  getPushes,
  getRepositories,
  getRepositoriesOnce,
  mapRepository,
} from "./api.js";

const EMPTY = [];
function shortSha(value) {
  return value ? value.slice(0, 10) : "Not mapped";
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }).format(date);
}

function repositoryParts(repository) {
  const parts = (repository || "").split("/");
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  return parts;
}

function githubCodeUrl(repository, commit, reference) {
  if (!reference) return null;
  if (/^https?:\/\//i.test(reference)) return reference;
  const [path, fragment] = reference.split("#", 2);
  if (!path || path.split("/").some((part) => part === "..")) return null;
  const encodedPath = path.split("/").map(encodeURIComponent).join("/");
  const lineAnchor =
    fragment && /^L\d+(?:-L\d+)?$/.test(fragment) ? `#${fragment}` : "";
  return `https://github.com/${repository}/blob/${commit}/${encodedPath}${lineAnchor}`;
}

function ErrorBanner({ message, onDismiss }) {
  if (!message) return null;
  return (
    <div className="notice notice-error" role="alert">
      <span>{message}</span>
      <button
        className="notice-close"
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss error"
      >
        ×
      </button>
    </div>
  );
}

function StatusPill({ status }) {
  return (
    <span className={`status-pill status-${status || "unknown"}`}>
      {status || "unknown"}
    </span>
  );
}

function EvidenceSection({ evidence, repository, commit }) {
  if (!evidence) {
    return (
      <p className="muted empty-copy">
        No saved prompt evidence is available for this push.
      </p>
    );
  }
  const changes = evidence.changes || EMPTY;
  const callers = evidence.callers || EMPTY;
  const tests = evidence.tests || EMPTY;
  const similar = evidence.similar_code || EMPTY;
  const flags = evidence.flags || EMPTY;

  return (
    <section className="evidence-block" aria-labelledby="evidence-heading">
      <div className="section-heading evidence-heading">
        <div>
          <p className="eyebrow">Prompt record</p>
          <h3 id="evidence-heading">Evidence used</h3>
        </div>
        <span className="token-total">~{evidence.token_count || 0} tokens</span>
      </div>

      {changes.length > 0 && (
        <div className="evidence-group">
          <h4>Changed code</h4>
          {changes.map((change, index) => {
            const url = githubCodeUrl(
              repository,
              commit,
              `${change.file}#L${change.start_line}`,
            );
            return (
              <article
                className="evidence-change"
                key={`${change.file}-${change.name}-${index}`}
              >
                <div className="evidence-title">
                  <span className="change-kind">{change.kind}</span>
                  <strong>{change.name}</strong>
                  <span className="line-range">
                    L{change.start_line}–L{change.end_line}
                  </span>
                </div>
                {url ? (
                  <a
                    className="file-link"
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {change.file}
                  </a>
                ) : (
                  <span className="file-link">{change.file}</span>
                )}
                {change.diff_snippet && (
                  <pre className="diff-snippet">{change.diff_snippet}</pre>
                )}
              </article>
            );
          })}
        </div>
      )}

      {callers.length > 0 && (
        <div className="evidence-group">
          <h4>Callers</h4>
          <ul className="evidence-list">
            {callers.map((caller, index) => {
              const url = githubCodeUrl(repository, commit, caller.caller_file);
              return (
                <li
                  key={`${caller.caller_file}-${caller.caller_name}-${index}`}
                >
                  <span>
                    <strong>{caller.caller_name}</strong> calls{" "}
                    {caller.target_name}
                  </span>
                  {url && (
                    <a href={url} target="_blank" rel="noreferrer">
                      {caller.caller_file}
                    </a>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {tests.length > 0 && (
        <div className="evidence-group">
          <h4>Related tests</h4>
          <ul className="evidence-list">
            {tests.map((test, index) => {
              const url = githubCodeUrl(repository, commit, test.test_file);
              return (
                <li key={`${test.test_file}-${test.target_name}-${index}`}>
                  <span>
                    <strong>{test.target_name}</strong>
                  </span>
                  {url && (
                    <a href={url} target="_blank" rel="noreferrer">
                      {test.test_file}
                    </a>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {similar.length > 0 && (
        <div className="evidence-group">
          <h4>Similar code</h4>
          <ul className="similar-list">
            {similar.map((item, index) => {
              const url = githubCodeUrl(repository, commit, item.file);
              return (
                <li
                  className="similar-item"
                  key={`${item.file}-${item.name}-${index}`}
                >
                  <div className="similar-topline">
                    <strong>{item.name}</strong>
                    <span className="similar-score">
                      {Math.round(item.score * 100)}%
                    </span>
                  </div>
                  {url ? (
                    <a
                      className="file-link"
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {item.file}
                    </a>
                  ) : (
                    <span className="file-link">{item.file}</span>
                  )}
                  {item.snippet && (
                    <pre className="similar-snippet">{item.snippet}</pre>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {flags.length > 0 && (
        <div className="evidence-group">
          <h4>Rule flags</h4>
          <div className="flag-row">
            {flags.map((flag) => (
              <span className="flag-chip" key={flag}>
                {flag}
              </span>
            ))}
          </div>
        </div>
      )}
      {!changes.length &&
        !callers.length &&
        !tests.length &&
        !similar.length &&
        !flags.length && (
          <p className="muted empty-copy">
            This explanation used no supplementary code evidence.
          </p>
        )}
    </section>
  );
}

function ExplanationDetail({ item, repository }) {
  if (!item) {
    return (
      <div className="detail-empty">
        <div className="empty-mark" aria-hidden="true">
          ↗
        </div>
        <h2>Select a push</h2>
        <p>
          Choose an explanation from the activity list to inspect its impact and
          evidence.
        </p>
      </div>
    );
  }

  if (!item.explanation) {
    return (
      <div className="detail-empty">
        <StatusPill status={item.status} />
        <h2>Explanation not available</h2>
        <p>
          This push is {item.status}. A saved explanation and evidence will
          appear here after it completes.
        </p>
      </div>
    );
  }

  const explanation = item.explanation;
  return (
    <div className="detail-content">
      <div className="detail-topline">
        <StatusPill status={item.status} />
        <time>{formatDate(item.created_at)}</time>
      </div>
      <p className="eyebrow">Plain-language summary</p>
      <h2 className="explanation-title">{explanation.one_line}</h2>
      <p className="what-changed">{explanation.what_changed}</p>

      {explanation.flags?.length > 0 && (
        <section className="impact-section">
          <h3>Signals</h3>
          <div className="flag-row">
            {explanation.flags.map((flag) => (
              <span className="flag-chip" key={flag}>
                {flag}
              </span>
            ))}
          </div>
        </section>
      )}

      {explanation.might_affect?.length > 0 && (
        <section className="impact-section">
          <h3>What this may affect</h3>
          <div className="impact-list">
            {explanation.might_affect.map((itemAffect, index) => {
              const url = githubCodeUrl(
                repository,
                item.after_commit,
                itemAffect.link,
              );
              return (
                <article
                  className="impact-item"
                  key={`${itemAffect.target}-${index}`}
                >
                  <div className="impact-target">
                    {url ? (
                      <a href={url} target="_blank" rel="noreferrer">
                        {itemAffect.target}
                        <span aria-hidden="true"> ↗</span>
                      </a>
                    ) : (
                      itemAffect.target
                    )}
                  </div>
                  <p>{itemAffect.reason}</p>
                  {url && (
                    <a
                      className="file-link"
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {itemAffect.link}
                    </a>
                  )}
                </article>
              );
            })}
          </div>
        </section>
      )}

      {explanation.things_to_know?.length > 0 && (
        <section className="impact-section terms-section">
          <h3>Useful terms</h3>
          {explanation.things_to_know.map((itemTerm) => (
            <p className="term-row" key={itemTerm.term}>
              <strong>{itemTerm.term}</strong>
              <span>{itemTerm.meaning}</span>
            </p>
          ))}
        </section>
      )}

      <section className="metrics-row" aria-label="Generation metrics">
        <div>
          <span>Model</span>
          <strong>{explanation.meta.model}</strong>
        </div>
        <div>
          <span>Input</span>
          <strong>{explanation.meta.tokens_in.toLocaleString()} tokens</strong>
        </div>
        <div>
          <span>Output</span>
          <strong>{explanation.meta.tokens_out.toLocaleString()} tokens</strong>
        </div>
        <div>
          <span>Generation</span>
          <strong>{explanation.meta.seconds.toFixed(2)}s</strong>
        </div>
      </section>

      <EvidenceSection
        evidence={item.evidence}
        repository={repository}
        commit={item.after_commit}
      />
    </div>
  );
}

export default function Workspace({
  active = true,
  locationSearch = window.location.search,
  repositories,
  setRepositories,
  repositoriesLoaded,
  setRepositoriesLoaded,
}) {
  const [selectedRepository, setSelectedRepository] = useState("");
  const [mapping, setMapping] = useState(null);
  const [pushes, setPushes] = useState(EMPTY);
  const [selectedPushId, setSelectedPushId] = useState(null);
  const [repoInput, setRepoInput] = useState("");
  const [beforeCommit, setBeforeCommit] = useState("");
  const [afterCommit, setAfterCommit] = useState("");
  const [useExtraEvidence, setUseExtraEvidence] = useState(true);
  const [showExplainForm, setShowExplainForm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [mappingBusy, setMappingBusy] = useState(false);
  const [explainBusy, setExplainBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mappingResult, setMappingResult] = useState(null);
  const activityRequestsRef = useRef(new Map());
  const shaRepositoryRef = useRef("");

  const loadRepositoryActivity = useCallback((repository) => {
    const inFlight = activityRequestsRef.current.get(repository);
    if (inFlight) return inFlight;

    const request = Promise.all([
      getMappingStatus(repository),
      getPushes(repository),
    ]);
    activityRequestsRef.current.set(repository, request);
    request.then(
      () => {
        if (activityRequestsRef.current.get(repository) === request) {
          activityRequestsRef.current.delete(repository);
        }
      },
      () => {
        if (activityRequestsRef.current.get(repository) === request) {
          activityRequestsRef.current.delete(repository);
        }
      },
    );
    return request;
  }, []);

  const refreshRepositories = useCallback(
    async (preferred = "") => {
      const rows = await getRepositories();
      setRepositories(rows);
      setRepositoriesLoaded(true);
      const next = preferred || selectedRepository;
      if (next && rows.some((row) => row.repository === next)) {
        setSelectedRepository(next);
      } else if (rows.length && !selectedRepository) {
        setSelectedRepository(rows[0].repository);
      } else if (!rows.length) {
        setSelectedRepository("");
      }
    },
    [selectedRepository, setRepositories, setRepositoriesLoaded],
  );

  useEffect(() => {
    let alive = true;
    if (repositoriesLoaded) {
      const requestedRepository = new URLSearchParams(locationSearch).get("repo");
      if (
        requestedRepository &&
        repositories.some((row) => row.repository === requestedRepository)
      ) {
        setSelectedRepository(requestedRepository);
      } else if (repositories.length) {
        setSelectedRepository(repositories[0].repository);
      }
      setLoading(false);
      return () => {
        alive = false;
      };
    }
    getRepositoriesOnce()
      .then((rows) => {
        if (!alive) return;
        setRepositories(rows);
        setRepositoriesLoaded(true);
        const requestedRepository = new URLSearchParams(
          locationSearch,
        ).get("repo");
        if (
          requestedRepository &&
          rows.some((row) => row.repository === requestedRepository)
        ) {
          setSelectedRepository(requestedRepository);
        } else if (rows.length) {
          setSelectedRepository(rows[0].repository);
        }
      })
      .catch((requestError) => alive && setError(requestError.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!active) return;
    const requestedRepository = new URLSearchParams(locationSearch).get("repo");
    if (
      requestedRepository &&
      repositories.some((item) => item.repository === requestedRepository) &&
      requestedRepository !== selectedRepository
    ) {
      setSelectedRepository(requestedRepository);
    }
  }, [active, locationSearch, repositories, selectedRepository]);

  const refreshSelectedRepository = useCallback(async (repository) => {
    if (!repository) {
      setMapping(null);
      setPushes([]);
      setSelectedPushId(null);
      return;
    }
    const [statusResult, pushRows] = await loadRepositoryActivity(repository);
    setMapping(statusResult);
    setPushes(pushRows);
    setSelectedPushId((current) =>
      applyActivityRefresh(pushRows, current).selectedPushId,
    );
    setBeforeCommit(statusResult.mapped_commit_sha || "");
  }, [loadRepositoryActivity]);

  useEffect(() => {
    if (!selectedRepository || !active) return undefined;
    let alive = true;
    const statusResult = repositories.find(
      (item) => item.repository === selectedRepository,
    );
    if (!statusResult) return undefined;
    setMapping(statusResult);
    getPushes(selectedRepository)
      .then((pushRows) => {
        if (!alive) return;
        setPushes(pushRows);
        setSelectedPushId((current) =>
          applyActivityRefresh(pushRows, current).selectedPushId,
        );
      })
      .catch((requestError) => {
        if (alive) setError(requestError.message);
      });
    return () => {
      alive = false;
    };
  }, [active, selectedRepository]);

  useEffect(() => {
    if (!selectedRepository) return;
    if (shaRepositoryRef.current === selectedRepository) return;
    const status = repositories.find(
      (item) => item.repository === selectedRepository,
    );
    if (!status) return;
    shaRepositoryRef.current = selectedRepository;
    setBeforeCommit(status.mapped_commit_sha || "");
  }, [repositories, selectedRepository]);

  useEffect(() => {
    if (!selectedRepository || !active) return undefined;
    let active = true;
    const stopRefreshing = startActivityRefresh({
      repository: selectedRepository,
      windowTarget: window,
      documentTarget: document,
      refresh: async (repository) => {
        try {
          const [statusResult, pushRows] = await loadRepositoryActivity(repository);
          if (!active) return;
          setMapping(statusResult);
          setPushes(pushRows);
          setSelectedPushId((current) =>
            applyActivityRefresh(pushRows, current).selectedPushId,
          );
        } catch (requestError) {
          if (active) {
            console.warn(
              `Could not refresh activity for ${repository}:`,
              requestError,
            );
          }
        }
      },
    });
    return () => {
      active = false;
      stopRefreshing();
    };
  }, [active, loadRepositoryActivity, selectedRepository]);

  const selectedPush =
    pushes.find((item) => item.push_id === selectedPushId) || null;
  const selectedSummary =
    repositories.find((item) => item.repository === selectedRepository) || null;

  async function handleAddRepository(event) {
    event.preventDefault();
    const repository = repoInput.trim();
    if (!repositoryParts(repository)) {
      setError("Enter a public repository as owner/name.");
      return;
    }
    setError("");
    setNotice("");
    setMappingBusy(true);
    setMappingResult(null);
    try {
      const result = await mapRepository(repository);
      setMappingResult(result);
      setRepoInput("");
      setSelectedRepository(result.repository);
      await refreshRepositories(result.repository);
      await refreshSelectedRepository(result.repository);
      setNotice(
        `Mapped ${result.repository} at ${shortSha(result.commit_sha)}.`,
      );
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setMappingBusy(false);
    }
  }

  async function handleExplainPush(event) {
    event.preventDefault();
    if (!selectedRepository || !beforeCommit.trim() || !afterCommit.trim()) {
      setError("Enter both commit SHAs to explain a push.");
      return;
    }
    setError("");
    setNotice("");
    setExplainBusy(true);
    try {
      const result = await explainPush({
        repo: selectedRepository,
        before_commit: beforeCommit.trim(),
        after_commit: afterCommit.trim(),
        use_extra_evidence: useExtraEvidence,
      });
      if (result === null) {
        setNotice(
          "No meaningful code changes found. No explanation was saved.",
        );
      } else {
        setNotice("Push explanation saved.");
      }
      await refreshSelectedRepository(selectedRepository);
      setShowExplainForm(false);
      setAfterCommit("");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setExplainBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <aside className="side-rail">
        <a className="brand" href="#top" aria-label="Catchup home">
          <span className="brand-mark">C</span>
          <span>
            <strong>catchup</strong>
            <small>CODE, IN CONTEXT</small>
          </span>
        </a>

        <nav className="primary-nav" aria-label="Main navigation">
          <a className="primary-nav-item" href="/" aria-label="Home">
            <span aria-hidden="true">⌂</span> Home
          </a>
          <a
            className="primary-nav-item is-active"
            href="/workspace"
            aria-current="page"
          >
            <span aria-hidden="true">▦</span> Workspace
          </a>
        </nav>

        <div className="rail-section-head">
          <span>Repositories</span>
          <span className="count-badge">{repositories.length}</span>
        </div>
        <nav className="repository-nav" aria-label="Repositories">
          {repositories.map((item) => (
            <button
              className={`repo-nav-item ${item.repository === selectedRepository ? "is-active" : ""}`}
              key={item.repository}
              type="button"
              onClick={() => {
                setError("");
                setNotice("");
                setSelectedRepository(item.repository);
                setMappingResult(null);
              }}
            >
              <span
                className={`repo-dot dot-${item.status}`}
                aria-hidden="true"
              />
              <span className="repo-nav-copy">
                <strong>{item.repository.split("/")[1]}</strong>
                <small>{item.repository.split("/")[0]}</small>
              </span>
              <span className="repo-nav-count">{item.chunks_stored}</span>
            </button>
          ))}
          {!repositories.length && !loading && (
            <p className="rail-empty">No repositories yet.</p>
          )}
        </nav>

        <form className="add-repo-form" onSubmit={handleAddRepository}>
          <label htmlFor="repo-name">Add a public repository</label>
          <div className="repo-input-row">
            <input
              id="repo-name"
              autoComplete="off"
              placeholder="owner/name"
              value={repoInput}
              onChange={(event) => setRepoInput(event.target.value)}
              disabled={mappingBusy}
            />
            <button
              type="submit"
              disabled={mappingBusy || !repoInput.trim()}
              aria-label="Map repository"
            >
              {mappingBusy ? <span className="button-spinner" /> : "+"}
            </button>
          </div>
          <small>
            {mappingBusy
              ? "Mapping the default branch…"
              : "Private repositories are not supported."}
          </small>
        </form>

        <div className="rail-footer">
          <span className="local-indicator" />
          <span>Local workspace</span>
        </div>
      </aside>

      <main className="main-area" id="top">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Workspace</span>
            <span className="crumb-separator">/</span>
            <strong>{selectedRepository || "Repositories"}</strong>
          </div>
          <a className="api-link" href="/docs" target="_blank" rel="noreferrer">
            API docs <span aria-hidden="true">↗</span>
          </a>
        </header>

        <div className="content-wrap">
          <ErrorBanner message={error} onDismiss={() => setError("")} />
          {notice && (
            <div className="notice notice-success" role="status">
              <span>{notice}</span>
              <button
                className="notice-close"
                type="button"
                onClick={() => setNotice("")}
                aria-label="Dismiss message"
              >
                ×
              </button>
            </div>
          )}

          {loading ? (
            <div className="initial-loading">
              <span className="large-spinner" />
              <span>Loading your repositories…</span>
            </div>
          ) : !selectedRepository ? (
            <section className="welcome-state">
              <p className="eyebrow">Local code intelligence</p>
              <h1>Catch up with the changes.</h1>
              <p>
                Add a public JavaScript repository to map its code, then explain
                commits with saved code evidence.
              </p>
              <div className="welcome-hint">
                Start with <code>owner/name</code> in the repository panel.
              </div>
            </section>
          ) : (
            <>
              <section className="repo-overview">
                <div className="overview-copy">
                  <p className="eyebrow">Repository overview</p>
                  <h1>{selectedRepository}</h1>
                  <div className="overview-meta">
                    <StatusPill
                      status={mapping?.status || selectedSummary?.status}
                    />
                    <span>
                      {mapping?.mapped_commit_sha
                        ? `Mapped at ${shortSha(mapping.mapped_commit_sha)}`
                        : "Mapping details unavailable"}
                    </span>
                    {mapping?.mapping_error && (
                      <span className="mapping-error">
                        {mapping.mapping_error}
                      </span>
                    )}
                  </div>
                </div>
                <div className="stat-strip" aria-label="Mapping statistics">
                  <div>
                    <strong>
                      {mapping?.files_parsed ??
                        selectedSummary?.files_parsed ??
                        0}
                    </strong>
                    <span>files</span>
                  </div>
                  <div>
                    <strong>
                      {mapping?.chunks_stored ??
                        selectedSummary?.chunks_stored ??
                        0}
                    </strong>
                    <span>chunks</span>
                  </div>
                  <div>
                    <strong>
                      {mapping?.references_stored ??
                        selectedSummary?.references_stored ??
                        0}
                    </strong>
                    <span>links</span>
                  </div>
                  <div>
                    <strong>
                      {mapping?.test_links_stored ??
                        selectedSummary?.test_links_stored ??
                        0}
                    </strong>
                    <span>tests</span>
                  </div>
                </div>
              </section>

              {mappingResult && (
                <div className="mapping-result">
                  <div>
                    <strong>Mapping complete</strong>
                    <span>
                      {mappingResult.files_parsed} files ·{" "}
                      {mappingResult.chunks_stored} chunks ·{" "}
                      {mappingResult.embeddings_generated} embeddings
                    </span>
                  </div>
                  <span className="mono">
                    {shortSha(mappingResult.commit_sha)}
                  </span>
                  <span>{mappingResult.duration_seconds.toFixed(1)}s</span>
                </div>
              )}

              <section className="push-workspace">
                <div className="push-list-panel">
                  <div className="section-heading push-list-heading">
                    <div>
                      <p className="eyebrow">Repository activity</p>
                      <h2>Push explanations</h2>
                    </div>
                    <button
                      className="primary-button compact-button"
                      type="button"
                      onClick={() => {
                        setShowExplainForm((shown) => !shown);
                        setError("");
                      }}
                      disabled={mapping?.status !== "ready"}
                    >
                      {showExplainForm ? "Close" : "Explain a push"}
                    </button>
                  </div>

                  {showExplainForm && (
                    <form className="explain-form" onSubmit={handleExplainPush}>
                      <div className="form-heading">
                        <strong>Compare commits</strong>
                        <span>
                          Mapped base: {shortSha(mapping?.mapped_commit_sha)}
                        </span>
                      </div>
                      <label htmlFor="before-sha">Before commit</label>
                      <input
                        id="before-sha"
                        value={beforeCommit}
                        onChange={(event) =>
                          setBeforeCommit(event.target.value)
                        }
                        required
                        minLength={7}
                        maxLength={64}
                      />
                      <label htmlFor="after-sha">After commit</label>
                      <input
                        id="after-sha"
                        value={afterCommit}
                        onChange={(event) => setAfterCommit(event.target.value)}
                        required
                        minLength={7}
                        maxLength={64}
                        placeholder="Commit SHA"
                      />
                      <label className="toggle-row" htmlFor="extra-evidence">
                        <input
                          id="extra-evidence"
                          type="checkbox"
                          checked={useExtraEvidence}
                          onChange={(event) =>
                            setUseExtraEvidence(event.target.checked)
                          }
                        />
                        <span>
                          <strong>Include impact evidence</strong>
                          <small>
                            Callers, tests, similar code, and risk flags
                          </small>
                        </span>
                      </label>
                      <button
                        className="primary-button submit-explain"
                        type="submit"
                        disabled={explainBusy || mapping?.status !== "ready"}
                      >
                        {explainBusy ? (
                          <>
                            <span className="button-spinner" /> Generating
                            explanation…
                          </>
                        ) : (
                          "Explain changes"
                        )}
                      </button>
                    </form>
                  )}

                  <div className="push-list-meta">
                    <span>
                      {pushes.length} saved{" "}
                      {pushes.length === 1 ? "push" : "pushes"}
                    </span>
                    <span>Newest first</span>
                  </div>
                  {pushes.length ? (
                    <div className="push-list">
                      {pushes.map((item) => (
                        <button
                          className={`push-row ${selectedPushId === item.push_id ? "selected" : ""}`}
                          key={item.push_id}
                          type="button"
                          onClick={() => setSelectedPushId(item.push_id)}
                        >
                          <span className="push-row-status">
                            <StatusPill status={item.status} />
                          </span>
                          <span className="push-row-content">
                            <strong>
                              {item.explanation?.one_line ||
                                `${shortSha(item.before_commit)} → ${shortSha(item.after_commit)}`}
                            </strong>
                            <span className="push-row-shas">
                              <code>{shortSha(item.before_commit)}</code>
                              <span>to</span>
                              <code>{shortSha(item.after_commit)}</code>
                            </span>
                          </span>
                          <time>{formatDate(item.created_at)}</time>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="empty-list">
                      <span className="empty-mark" aria-hidden="true">
                        —
                      </span>
                      <strong>No explained pushes yet</strong>
                      <p>
                        Compare a before and after commit to create the first
                        explanation.
                      </p>
                    </div>
                  )}
                </div>

                <section
                  className="detail-panel"
                  aria-label="Selected push explanation"
                >
                  <ExplanationDetail
                    item={selectedPush}
                    repository={selectedRepository}
                  />
                </section>
              </section>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
