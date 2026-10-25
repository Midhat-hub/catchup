
const PRODUCTION_API_BASE_URL = "https://catchup-7tzq.onrender.com";
const configuredApiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();
const API_BASE_URL = (configuredApiBaseUrl || (
  import.meta.env.DEV ? "" : PRODUCTION_API_BASE_URL
)).replace(/\/+$/, "");
let repositoriesInFlight = null;

function validateApiBaseUrl() {
  if (!API_BASE_URL) return;
  try {
    const url = new URL(API_BASE_URL);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
      throw new Error("unsupported URL");
    }
  } catch {
    throw new Error(
      "Catchup API URL is invalid. Set VITE_API_BASE_URL to the backend base URL (for production: https://catchup-7tzq.onrender.com).",
    );
  }
}

async function request(path, options = {}) {
  validateApiBaseUrl();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });

  if (response.status === 204) return null;

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("text/html")) {
    throw new Error(
      `Catchup expected a JSON API response from ${API_BASE_URL || window.location.origin} but received HTML. Check VITE_API_BASE_URL and the backend deployment.`,
    );
  }
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail =
      typeof body === "object" && body !== null ? body.detail : body;
    throw new Error(detail || `Request failed (${response.status})`);
  }

  return body;
}

function repoPath(repository) {
  return repository.split("/").map(encodeURIComponent).join("/");
}

export async function getRepositories() {
  const data = await request("/api/repos");

  if (Array.isArray(data)) {
    return data;
  }

  if (Array.isArray(data?.repositories)) {
    return data.repositories;
  }

  console.error("Unexpected /api/repos response:", data);
  throw new Error("The repositories API returned an unexpected response.");
}

export function getRepositoriesOnce() {
  if (!repositoriesInFlight) {
    repositoriesInFlight = getRepositories().finally(() => {
      repositoriesInFlight = null;
    });
  }
  return repositoriesInFlight;
}

export function getMappingStatus(repository) {
  return request(`/api/repos/${repoPath(repository)}/mapping`);
}

export function getPushes(repository) {
  return request(`/api/repos/${repoPath(repository)}/pushes?limit=50`);
}

export function mapRepository(repository) {
  return request("/api/repos/map", {
    method: "POST",
    body: JSON.stringify({ repository }),
  });
}

export function explainPush({
  repo,
  before_commit,
  after_commit,
  use_extra_evidence,
}) {
  return request("/api/pushes/explain", {
    method: "POST",
    body: JSON.stringify({
      repo,
      before_commit,
      after_commit,
      use_extra_evidence,
    }),
  });
}
