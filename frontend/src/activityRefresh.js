export const ACTIVITY_REFRESH_INTERVAL_MS = 30_000;

export function applyActivityRefresh(pushRows, selectedPushId) {
  return {
    pushes: pushRows,
    selectedPushId: pushRows.some((item) => item.push_id === selectedPushId)
      ? selectedPushId
      : (pushRows[0]?.push_id ?? null),
  };
}

export function startActivityRefresh({
  repository,
  refresh,
  windowTarget,
  documentTarget,
  setIntervalFn = setInterval,
  clearIntervalFn = clearInterval,
}) {
  if (!repository) return () => {};

  let stopped = false;
  let requestInFlight = false;

  const refreshIfVisible = () => {
    if (
      stopped ||
      requestInFlight ||
      documentTarget.visibilityState === "hidden"
    ) {
      return;
    }

    requestInFlight = true;
    Promise.resolve()
      .then(() => refresh(repository))
      .catch(() => {})
      .finally(() => {
        requestInFlight = false;
      });
  };
  const onVisibilityChange = () => {
    if (documentTarget.visibilityState === "visible") refreshIfVisible();
  };

  const intervalId = setIntervalFn(
    refreshIfVisible,
    ACTIVITY_REFRESH_INTERVAL_MS,
  );
  windowTarget.addEventListener("focus", refreshIfVisible);
  documentTarget.addEventListener("visibilitychange", onVisibilityChange);

  return () => {
    stopped = true;
    clearIntervalFn(intervalId);
    windowTarget.removeEventListener("focus", refreshIfVisible);
    documentTarget.removeEventListener("visibilitychange", onVisibilityChange);
  };
}
