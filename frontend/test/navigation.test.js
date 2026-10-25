import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

import {
  appPageForPath,
  isAppPath,
  normalizeAppPath,
} from "../src/navigation.js";

test("Home and Workspace URLs resolve to their application views", () => {
  assert.equal(appPageForPath("/"), "home");
  assert.equal(appPageForPath("/home/"), "home");
  assert.equal(appPageForPath("/workspace/"), "workspace");
  assert.equal(normalizeAppPath("/workspace///"), "/workspace");
  assert.equal(isAppPath("/home"), true);
  assert.equal(isAppPath("/workspace"), true);
  assert.equal(isAppPath("/settings"), false);
});

test("Home and Workspace navigation retain the mounted workspace state", async () => {
  const [app, home, workspace, styles] = await Promise.all([
    readFile(new URL("../src/App.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/HomePage.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/Workspace.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/landing.css", import.meta.url), "utf8"),
  ]);

  assert.match(home, /aria-label="Main navigation"/);
  assert.match(app, /^import React,/);
  assert.match(home, /^import React,/);
  assert.match(workspace, /^import React,/);
  assert.match(home, /aria-current="page"[\s\S]*?⌂\s*<span>Home<\/span>/);
  assert.match(home, /href="\/workspace"/);
  assert.match(home, /Automatic push detection/);
  assert.match(home, /Repository activity history/);
  assert.match(styles, /@media \(max-width: 900px\)[\s\S]*?landing-steps-grid \{ grid-template-columns: repeat\(2/);
  assert.match(styles, /@media \(max-width: 620px\)[\s\S]*?landing-steps-grid \{ grid-template-columns: 1fr/);
  assert.match(home, /getRepositoriesOnce\(\)/);
  assert.match(app, /const \[workspaceVisited, setWorkspaceVisited\]/);
  assert.match(app, /repositoriesLoaded/);
  assert.match(
    app,
    /workspaceVisited && \([\s\S]*?<WorkspacePage\s+active=\{isWorkspace\}/,
  );
  assert.match(app, /const WorkspacePage = lazy\(\(\) => import\("\.\/Workspace\.jsx"\)\)/);
  assert.match(workspace, /const shaRepositoryRef = useRef\(""\)/);
  assert.match(workspace, /startActivityRefresh\(/);
  assert.match(workspace, /getRepositoriesOnce\(\)/);
});
