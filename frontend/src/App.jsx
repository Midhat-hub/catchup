import React, { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { appPageForPath, isAppPath, normalizeAppPath } from "./navigation.js";

const HomePage = lazy(() => import("./HomePage.jsx"));
const WorkspacePage = lazy(() => import("./Workspace.jsx"));

function currentLocation() {
  return {
    pathname: normalizeAppPath(window.location.pathname),
    search: window.location.search,
  };
}

export default function App() {
  const [location, setLocation] = useState(currentLocation);
  const [repositories, setRepositories] = useState([]);
  const [repositoriesLoaded, setRepositoriesLoaded] = useState(false);
  const isWorkspace = appPageForPath(location.pathname) === "workspace";
  const [workspaceVisited, setWorkspaceVisited] = useState(isWorkspace);
  const [homeVisited, setHomeVisited] = useState(!isWorkspace);

  const navigate = useCallback((href) => {
    const next = new URL(href, window.location.href);
    if (next.origin !== window.location.origin) return;
    const nextPath = normalizeAppPath(next.pathname);
    if (!isAppPath(nextPath)) return;
    window.history.pushState({}, "", `${nextPath}${next.search}${next.hash}`);
    setLocation({ pathname: nextPath, search: next.search });
    if (!next.hash) window.scrollTo(0, 0);
    if (nextPath === "/workspace") setWorkspaceVisited(true);
    else setHomeVisited(true);
  }, []);

  useEffect(() => {
    const onPopState = () => setLocation(currentLocation());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const onAppClick = useCallback((event) => {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    const anchor = event.target.closest?.("a[href]");
    if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
    const target = new URL(anchor.href, window.location.href);
    if (target.origin !== window.location.origin) return;
    const targetPath = normalizeAppPath(target.pathname);
    if (!isAppPath(targetPath)) return;
    if (target.hash && targetPath === (location.pathname || "/")) return;
    event.preventDefault();
    navigate(`${targetPath}${target.search}${target.hash}`);
  }, [location.pathname, navigate]);

  return (
    <div onClick={onAppClick}>
      {homeVisited && (
        <div style={{ display: isWorkspace ? "none" : undefined }}>
          <Suspense
            fallback={<div className="initial-loading">Loading Catchup Home…</div>}
          >
            <HomePage
              repositories={repositories}
              setRepositories={setRepositories}
              repositoriesLoaded={repositoriesLoaded}
              setRepositoriesLoaded={setRepositoriesLoaded}
              onOpenWorkspace={(repository) =>
                navigate(`/workspace?repo=${encodeURIComponent(repository)}`)
              }
            />
          </Suspense>
        </div>
      )}
      {workspaceVisited && (
        <div style={{ display: isWorkspace ? undefined : "none" }}>
          <Suspense
            fallback={<div className="initial-loading">Loading workspace…</div>}
          >
            <WorkspacePage
              active={isWorkspace}
              locationSearch={location.search}
              repositories={repositories}
              setRepositories={setRepositories}
              repositoriesLoaded={repositoriesLoaded}
              setRepositoriesLoaded={setRepositoriesLoaded}
            />
          </Suspense>
        </div>
      )}
    </div>
  );
}
