"use strict";
const electron = require("electron");
const api = {
  resizeWindow: (deltaX, deltaY, direction) => electron.ipcRenderer.invoke("resize-window", { deltaX, deltaY, direction }),
  toggleDevTools: () => electron.ipcRenderer.invoke("toggle-devtools"),
  setIgnoreMouseEvents: (ignore, options) => electron.ipcRenderer.invoke("set-ignore-mouse-events", ignore, options),
  toggleAlwaysOnTop: () => electron.ipcRenderer.invoke("toggle-always-on-top")
};
electron.contextBridge.exposeInMainWorld("electronAPI", api);
