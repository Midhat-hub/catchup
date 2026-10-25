import React, { useEffect, useState } from "react";
import "./landing.css";
import { getRepositoriesOnce } from "./api.js";

const EMPTY = [];
export default function LandingPage({
  onOpenWorkspace,
  repositories = EMPTY,
  setRepositories,
  repositoriesLoaded = false,
  setRepositoriesLoaded,
} = {}) {
  const [reposLoading, setReposLoading] = useState(!repositoriesLoaded);
  const [reposUnavailable, setReposUnavailable] = useState(false);

  useEffect(() => {
    if (repositoriesLoaded) {
      setReposLoading(false);
      setReposUnavailable(false);
      return undefined;
    }
    let alive = true;
    getRepositoriesOnce()
      .then((rows) => {
        if (!alive) return;
        setRepositories(rows);
        setRepositoriesLoaded(true);
        setReposUnavailable(false);
      })
      .catch(() => {
        if (alive) setReposUnavailable(true);
      })
      .finally(() => {
        if (alive) setReposLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [repositoriesLoaded, setRepositories, setRepositoriesLoaded]);

  function openRepository(repository) {
    if (onOpenWorkspace) {
      onOpenWorkspace(repository);
    } else {
      window.location.assign(
        `/workspace?repo=${encodeURIComponent(repository)}`,
      );
    }
  }

  return (
    <div className="landing-layout">
      <aside className="landing-sidebar">
        <a className="landing-sidebar-brand" href="/" aria-label="Catchup home">
          <span className="landing-brand-mark">C</span>
          <span>
            <strong>catchup</strong>
            <small>CODE, IN CONTEXT</small>
          </span>
        </a>
        <nav className="landing-primary-nav" aria-label="Main navigation">
          <a
            className="landing-primary-item is-active"
            href="/"
            aria-current="page"
          >
            ⌂ <span>Home</span>
          </a>
          <a className="landing-primary-item" href="/workspace">
            ▦ <span>Workspace</span>
          </a>
        </nav>
        <div className="landing-sidebar-heading">
          <span>Repositories</span>
          <span className="landing-repo-count">{repositories.length}</span>
        </div>
        <nav
          className="landing-repository-list"
          aria-label="Existing repositories"
        >
          {repositories.map((item) => (
            <button
              key={item.repository}
              type="button"
              className="landing-repo-item"
              onClick={() => openRepository(item.repository)}
              title={`Open ${item.repository}`}
            >
              <span
                className={`repo-dot dot-${item.status || "unknown"}`}
                aria-hidden="true"
              />
              <span className="landing-repo-copy">
                <strong>
                  {item.repository.split("/")[1] || item.repository}
                </strong>
                <small>{item.repository.split("/")[0] || "Repository"}</small>
              </span>
              <span className="landing-repo-count-value">
                {item.chunks_stored ?? 0}
              </span>
            </button>
          ))}
          {reposLoading && (
            <p className="landing-repo-message">Loading repositories…</p>
          )}
          {!reposLoading && !repositories.length && !reposUnavailable && (
            <p className="landing-repo-message">No repositories mapped yet.</p>
          )}
          {reposUnavailable && (
            <p className="landing-repo-message">
              Repositories are unavailable right now.
            </p>
          )}
        </nav>
        <a className="landing-sidebar-workspace" href="/workspace">
          Open workspace <span aria-hidden="true">↗</span>
        </a>
        <div className="landing-sidebar-footer">
          <span className="local-indicator" /> Your code, in context
        </div>
      </aside>

      <div className="landing-main-column">
        <div className="landing-page">
          <header className="landing-nav">
            <a className="landing-brand" href="/" aria-label="Catchup home">
              <span className="landing-brand-mark">C</span>
              <span>
                <strong>catchup</strong>
                <small>CODE, IN CONTEXT</small>
              </span>
            </a>
            <nav className="landing-nav-links" aria-label="Main navigation">
              <a href="#how-it-works">How it works</a>
              <a href="#features">Features</a>
            </nav>
            <a className="landing-nav-cta" href="/workspace">
              Open workspace <span aria-hidden="true">↗</span>
            </a>
          </header>

          <main>
            <section className="landing-hero">
              <div className="landing-hero-copy">
                <div className="landing-kicker">
                  <span className="landing-live-dot" /> CODEBASE CONTEXT, ON
                  DEMAND
                </div>
                <h1>
                  Understand every push.
                  <br />
                  <em>Not just the diff.</em>
                </h1>
                <p className="landing-hero-description">
                  Catchup monitors mapped GitHub repositories and uses AI to
                  explain each new push—showing what changed, why it matters,
                  and which parts of your codebase may be affected without
                  manually tracing every diff.
                </p>
                <div className="landing-hero-actions">
                  <a
                    className="landing-button landing-button-primary"
                    href="/workspace"
                  >
                    Explore the workspace <span aria-hidden="true">→</span>
                  </a>
                  <a
                    className="landing-text-link"
                    href="https://github.com/Midhat-hub/catchup"
                    target="_blank"
                    rel="noreferrer"
                  >
                    View on GitHub <span aria-hidden="true">↗</span>
                  </a>
                </div>
                <div className="landing-proof">
                  <span className="landing-proof-icon">✓</span>
                  <span>Plain-language summaries</span>
                  <span className="landing-proof-separator">·</span>
                  <span>Code-backed evidence</span>
                </div>
              </div>

              <div
                className="landing-visual"
                aria-label="Illustration of a code push explanation"
              >
                <div className="landing-orbit landing-orbit-one" />
                <div className="landing-orbit landing-orbit-two" />
                <div className="landing-code-card">
                  <div className="landing-card-topbar">
                    <div className="landing-window-dots">
                      <i />
                      <i />
                      <i />
                    </div>
                    <span>push-explanation.md</span>
                    <span className="landing-card-tag">SAVED</span>
                  </div>
                  <div className="landing-code-body">
                    <div className="landing-code-line">
                      <span>01</span>
                      <code className="code-muted">
                        commit <b>8f3a2c1</b>
                      </code>
                    </div>
                    <div className="landing-code-line">
                      <span>02</span>
                      <code>
                        update <b className="code-green">checkout flow</b>
                      </code>
                    </div>
                    <div className="landing-code-divider" />
                    <p className="landing-mini-label">PLAIN-LANGUAGE SUMMARY</p>
                    <h3>Checkout now validates the cart before payment.</h3>
                    <p className="landing-mini-copy">
                      The new guard prevents empty carts from reaching the
                      payment step.
                    </p>
                    <div className="landing-impact-heading">
                      <span>↳</span> WHAT THIS MAY AFFECT
                    </div>
                    <div className="landing-impact-row">
                      <span className="landing-file-icon">ƒ</span>
                      <span>
                        <b>createPayment()</b>
                        <small>src/payments/checkout.js</small>
                      </span>
                      <span className="landing-arrow">↗</span>
                    </div>
                    <div className="landing-impact-row">
                      <span className="landing-file-icon landing-file-test">
                        ✓
                      </span>
                      <span>
                        <b>checkout tests</b>
                        <small>tests/checkout.test.js</small>
                      </span>
                      <span className="landing-arrow">↗</span>
                    </div>
                  </div>
                </div>
                <div className="landing-floating-note">
                  <span>✳</span>
                  <div>
                    <b>Context included</b>
                    <small>Callers · tests · related code</small>
                  </div>
                </div>
                <div className="landing-visual-caption">
                  FROM COMMIT TO CONTEXT <span>01 / 03</span>
                </div>
              </div>
              <div className="landing-hero-bottom">
                <span>BUILT FOR DEVELOPERS</span>
                <span>LESS GUESSWORK. MORE CONTEXT.</span>
              </div>
            </section>

            <section className="landing-intro" id="how-it-works">
              <div className="landing-section-label">
                <span>01</span> THE IDEA
              </div>
              <div className="landing-intro-copy">
                <h2>
                  A diff tells you <em>what moved.</em>
                  <br />
                  Catchup helps explain what it means.
                </h2>
                <p>
                  When you join a project or return to a codebase, reading every
                  commit takes time. Catchup connects the change to surrounding
                  code so you can get oriented faster.
                </p>
              </div>
            </section>

            <section className="landing-steps-section">
              <div className="landing-section-label">
                <span>02</span> HOW IT WORKS
              </div>
              <div className="landing-steps-grid">
                <article className="landing-step-card">
                  <span className="landing-step-number">01 / MAP</span>
                  <div className="landing-step-symbol">⌘</div>
                  <h3>Map your repository</h3>
                  <p>
                    Open the workspace, enter a public owner/name repository,
                    and let Catchup map its current default branch.
                  </p>
                </article>
                <article className="landing-step-card landing-step-highlight">
                  <span className="landing-step-number">02 / MONITOR</span>
                  <div className="landing-step-symbol">↗</div>
                  <h3>Monitor new commits</h3>
                  <p>
                    After mapping, Catchup automatically polls the default
                    branch and queues commits it has not processed yet.
                  </p>
                </article>
                <article className="landing-step-card">
                  <span className="landing-step-number">03 / EXPLAIN</span>
                  <div className="landing-step-symbol">✳</div>
                  <h3>Compare and explain</h3>
                  <p>
                    Catchup compares each detected commit with its predecessor
                    and saves an AI-generated explanation.
                  </p>
                </article>
                <article className="landing-step-card">
                  <span className="landing-step-number">04 / REVIEW</span>
                  <div className="landing-step-symbol">◎</div>
                  <h3>Review the activity</h3>
                  <p>
                    Browse the activity history and inspect related code,
                    callers, tests, similar snippets, and risk signals.
                  </p>
                </article>
              </div>
            </section>

            <section className="landing-features" id="features">
              <div className="landing-section-label">
                <span>03</span> WHY CATCHUP
              </div>
              <div className="landing-feature-layout">
                <div className="landing-feature-heading">
                  <h2>
                    Context that travels
                    <br />
                    <em>with the change.</em>
                  </h2>
                  <p>
                    Less time tracing dependencies from scratch. More time
                    understanding the work in front of you.
                  </p>
                  <a className="landing-feature-link" href="/workspace">
                    Go to your workspace <span>→</span>
                  </a>
                </div>
                <div className="landing-feature-list">
                  <article>
                    <span className="landing-feature-index">A</span>
                    <div>
                      <h3>AI-generated explanations</h3>
                      <p>
                        Get a concise explanation of what changed before diving
                        into implementation details.
                      </p>
                    </div>
                  </article>
                  <article>
                    <span className="landing-feature-index">B</span>
                    <div>
                      <h3>Automatic push detection</h3>
                      <p>
                        Catchup polls a mapped repository's default branch and
                        queues new commits for explanation automatically.
                      </p>
                    </div>
                  </article>
                  <article>
                    <span className="landing-feature-index">C</span>
                    <div>
                      <h3>Repository activity history</h3>
                      <p>
                        Review saved commit comparisons and explanations in
                        the workspace.
                      </p>
                    </div>
                  </article>
                  <article>
                    <span className="landing-feature-index">D</span>
                    <div>
                      <h3>Evidence you can inspect</h3>
                      <p>
                        Open referenced files and review snippets instead of
                        taking the summary on faith.
                      </p>
                    </div>
                  </article>
                </div>
              </div>
            </section>

            <section className="landing-final-cta">
              <div className="landing-final-stamp">C</div>
              <p className="landing-kicker">
                YOUR NEXT CODEBASE, LESS MYSTERIOUS
              </p>
              <h2>
                Get the context.
                <br />
                <em>Then get to work.</em>
              </h2>
              <a
                className="landing-button landing-button-light"
                href="/workspace"
              >
                Open Catchup workspace <span aria-hidden="true">→</span>
              </a>
            </section>
          </main>

          <footer className="landing-footer">
            <a className="landing-brand" href="/">
              <span className="landing-brand-mark">C</span>
              <span>
                <strong>catchup</strong>
                <small>CODE, IN CONTEXT</small>
              </span>
            </a>
            <span>Understand the change. Follow the context.</span>
            <a
              href="https://github.com/Midhat-hub/catchup"
              target="_blank"
              rel="noreferrer"
            >
              GitHub ↗
            </a>
            <a href="/workspace">Workspace ↗</a>
          </footer>
        </div>
      </div>
    </div>
  );
}
