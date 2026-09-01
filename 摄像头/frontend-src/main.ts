import { desktopCapturer, IpcMain } from 'electron';

export function setupOptionalFeatureIpc(ipcMain: IpcMain): void {
  ipcMain.handle('get-screen-capture', async () => {
    const sources = await desktopCapturer.getSources({ types: ['screen'] });
    if (!sources.length) throw new Error('No screen capture source is available');
    return sources[0].id;
  });
}
