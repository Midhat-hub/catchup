export const APP_PATHS = ["/", "/home", "/workspace"];

export function normalizeAppPath(pathname) {
  return pathname.replace(/\/+$/, "") || "/";
}

export function isAppPath(pathname) {
  return APP_PATHS.includes(normalizeAppPath(pathname));
}

export function appPageForPath(pathname) {
  return normalizeAppPath(pathname) === "/workspace" ? "workspace" : "home";
}
