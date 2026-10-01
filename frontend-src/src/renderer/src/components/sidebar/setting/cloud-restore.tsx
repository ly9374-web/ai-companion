import { useState } from 'react';
import { Button, Text } from '@chakra-ui/react';
import { useTranslation } from 'react-i18next';
import { getAccountSessionToken } from '@/constants/account-settings';
import { useAccount } from '@/context/account-context';
import { useWebSocket } from '@/context/websocket-context';
import { wsService } from '@/services/websocket-service';

function isLocalAddress(value: string): boolean {
  try {
    return ['localhost', '127.0.0.1', '[::1]'].includes(new URL(value).hostname);
  } catch (_error) {
    return false;
  }
}

export default function CloudRestore(): JSX.Element | null {
  const { t } = useTranslation();
  const { account } = useAccount();
  const { baseUrl, wsUrl } = useWebSocket();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState('');
  const localPage = window.location.protocol === 'file:'
    || isLocalAddress(window.location.href);

  if (!localPage || !isLocalAddress(baseUrl) || !account) return null;

  const restore = async (): Promise<void> => {
    if (busy) return;
    const sessionToken = getAccountSessionToken();
    if (!sessionToken) {
      setResult(t('settings.general.cloudRestoreLoginRequired'));
      return;
    }
    if (!window.confirm(t('settings.general.cloudRestoreConfirm', { account }))) return;

    setBusy(true);
    setResult(t('settings.general.cloudRestoreWorking'));
    wsService.disconnect();
    try {
      const response = await fetch(`${baseUrl}/api/local/cloud-restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account, sessionToken }),
      });
      const payload = await response.json() as {
        error?: string;
        restored_files?: number;
        backup_path?: string;
      };
      if (!response.ok) throw new Error(payload.error || `${response.status}`);
      const successMessage = t('settings.general.cloudRestoreDone', {
        count: payload.restored_files,
        backup: payload.backup_path,
      });
      setResult(successMessage);
      window.alert(successMessage);
      window.location.reload();
    } catch (error) {
      setResult(t('settings.general.cloudRestoreFailed', {
        reason: error instanceof Error ? error.message : String(error),
      }));
      wsService.connect(wsUrl);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button colorPalette="orange" variant="outline" onClick={() => { void restore(); }} disabled={busy}>
        {busy ? t('settings.general.cloudRestoreWorking') : t('settings.general.cloudRestoreButton')}
      </Button>
      <Text fontSize="sm" color="gray.400">{t('settings.general.cloudRestoreHelp')}</Text>
      {result && <Text fontSize="sm" whiteSpace="pre-wrap">{result}</Text>}
    </>
  );
}
