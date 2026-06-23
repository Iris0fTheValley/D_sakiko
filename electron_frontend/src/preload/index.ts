import { contextBridge, ipcRenderer } from 'electron'

const api = {
  resizeWindow: (deltaX: number, deltaY: number, direction: string) =>
    ipcRenderer.invoke('resize-window', { deltaX, deltaY, direction }),

  toggleDevTools: () =>
    ipcRenderer.invoke('toggle-devtools'),

  setIgnoreMouseEvents: (ignore: boolean, options?: { forward: boolean }) =>
    ipcRenderer.invoke('set-ignore-mouse-events', ignore, options),

  getMousePosition: () =>
    ipcRenderer.invoke('get-mouse-position'),

  toggleAlwaysOnTop: () =>
    ipcRenderer.invoke('toggle-always-on-top'),
}

contextBridge.exposeInMainWorld('electronAPI', api)
