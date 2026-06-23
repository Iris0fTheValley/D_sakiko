"use strict";
const electron = require("electron");
const node_path = require("node:path");
electron.app.commandLine.appendSwitch("enable-webgl");
electron.app.commandLine.appendSwitch("ignore-gpu-blacklist");
let mainWindow = null;
function createTransparentWindowConfig() {
  return {
    frame: false,
    transparent: true,
    hasShadow: false,
    type: "panel"
  };
}
function createWindow() {
  mainWindow = new electron.BrowserWindow({
    title: "Saki",
    width: 450,
    height: 600,
    show: false,
    webPreferences: {
      preload: node_path.join(__dirname, "../preload/index.js"),
      sandbox: false,
      webgl: true
    },
    ...createTransparentWindowConfig()
  });
  mainWindow.setAlwaysOnTop(true, "screen-saver", 1);
  mainWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  mainWindow.setFullScreenable(false);
  mainWindow.on("ready-to-show", () => mainWindow.show());
  mainWindow.on("close", (event) => {
    event.preventDefault();
    mainWindow?.hide();
  });
  if (process.env.NODE_ENV === "development") {
    mainWindow.loadURL("http://localhost:5173");
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    mainWindow.loadFile(node_path.join(__dirname, "../renderer/index.html"));
  }
  electron.ipcMain.handle("resize-window", (_event, { deltaX, deltaY, direction }) => {
    if (!mainWindow) return;
    const bounds = mainWindow.getBounds();
    const minWidth = 200;
    const minHeight = 250;
    let { x, y, width, height } = bounds;
    if (direction.includes("e")) width = Math.max(minWidth, width + deltaX);
    if (direction.includes("w")) {
      const newWidth = Math.max(minWidth, width - deltaX);
      x = x + (width - newWidth);
      width = newWidth;
    }
    if (direction.includes("s")) height = Math.max(minHeight, height + deltaY);
    if (direction.includes("n")) {
      const newHeight = Math.max(minHeight, height - deltaY);
      y = y + (height - newHeight);
      height = newHeight;
    }
    mainWindow.setBounds({ x, y, width, height });
  });
  electron.ipcMain.handle("toggle-devtools", () => {
    if (!mainWindow) return false;
    if (mainWindow.webContents.isDevToolsOpened()) {
      mainWindow.webContents.closeDevTools();
      return false;
    } else {
      mainWindow.webContents.openDevTools({ mode: "detach" });
      return true;
    }
  });
  electron.ipcMain.handle("set-ignore-mouse-events", (_event, ignore, options) => {
    if (!mainWindow) return;
    mainWindow.setIgnoreMouseEvents(ignore, options);
  });
  electron.ipcMain.handle("toggle-always-on-top", () => {
    if (!mainWindow) return false;
    const current = mainWindow.isAlwaysOnTop();
    mainWindow.setAlwaysOnTop(!current, "screen-saver", 1);
    return !current;
  });
}
electron.app.whenReady().then(createWindow);
electron.app.on("window-all-closed", () => {
  if (process.platform !== "darwin") electron.app.quit();
});
electron.app.on("activate", () => {
  if (electron.BrowserWindow.getAllWindows().length === 0) createWindow();
});
