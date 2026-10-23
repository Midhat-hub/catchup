```javascript
const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || ""
).replace(/\/+$/, "");

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });

  if (response.status === 204) return null;

  const contentType = response.headers.get("content-type") || "";
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

export function getRepositories() {
  return request("/api/repos");
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
```
