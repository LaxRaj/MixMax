import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const PORT = 3123;

// The suite runs against its own store and its own passcode, never the real
// songs in .data/ — see e2e/global-setup.ts.
const DATA_DIR = path.join(__dirname, "e2e", ".data");
const AUTH_FILE = path.join(__dirname, "e2e", ".auth.json");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  timeout: 60_000,
  globalSetup: "./e2e/global-setup.ts",
  use: {
    storageState: AUTH_FILE,
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    // The listener is on a phone, and the memory ceiling for decoding every
    // version at once is the risk this whole milestone exists to disprove.
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `npx next dev -p ${PORT}`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    env: { MIXMAX_DATA_DIR: DATA_DIR, MIXMAX_PASSCODE: "e2e-passcode" },
    timeout: 120_000,
  },
});
