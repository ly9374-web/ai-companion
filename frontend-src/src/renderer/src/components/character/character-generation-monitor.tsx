import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAccount } from '@/context/account-context';
import { toaster } from '@/components/ui/toaster';
import { wsService } from '@/services/websocket-service';
import {
  CHARACTER_GENERATION_STARTED_EVENT,
  clearActiveCharacterGenerationJob,
  fetchCharacterGenerationJob,
  getActiveCharacterGenerationJob,
} from '@/services/character-generation';

function progressDescription(
  t: ReturnType<typeof useTranslation>['t'],
  status: string,
  completed: number,
  total: number,
  emotion?: string | null,
): string {
  if (status === 'preparing') return t('characterCreator.progressPreparing');
  if (status === 'publishing') return t('characterCreator.progressPublishing');
  if (emotion) {
    return t('characterCreator.progressGenerating', {
      completed: Math.min(completed + 1, total),
      total,
      emotion,
    });
  }
  return t('characterCreator.progressQueued');
}

export function CharacterGenerationMonitor(): JSX.Element | null {
  const { t } = useTranslation();
  const { account } = useAccount();
  const [jobId, setJobId] = useState(() => (
    account ? getActiveCharacterGenerationJob(account) : ''
  ));

  useEffect(() => {
    setJobId(account ? getActiveCharacterGenerationJob(account) : '');
  }, [account]);

  useEffect(() => {
    const handleStarted = (event: Event): void => {
      const detail = (event as CustomEvent<{ jobId?: string }>).detail;
      if (detail?.jobId) setJobId(detail.jobId);
    };
    window.addEventListener(CHARACTER_GENERATION_STARTED_EVENT, handleStarted);
    return () => window.removeEventListener(CHARACTER_GENERATION_STARTED_EVENT, handleStarted);
  }, []);

  useEffect(() => {
    if (!account || !jobId) return undefined;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const toastId = `character-generation-${jobId}`;

    const schedule = (): void => {
      timer = setTimeout(() => void poll(), 2500);
    };

    const poll = async (): Promise<void> => {
      try {
        const job = await fetchCharacterGenerationJob(account, jobId);
        if (disposed) return;
        if (job.status === 'succeeded') {
          clearActiveCharacterGenerationJob(account);
          const completeToast = {
            title: t('characterCreator.completeTitle'),
            description: t('characterCreator.completeDescription', {
              name: job.character_name,
            }),
            type: 'success' as const,
            duration: 5000,
          };
          if (toaster.isVisible(toastId)) toaster.update(toastId, completeToast);
          else toaster.create({ id: toastId, ...completeToast });
          wsService.sendMessage({ type: 'fetch-configs' });
          setJobId('');
          return;
        }
        if (job.status === 'failed') {
          clearActiveCharacterGenerationJob(account);
          const failedToast = {
            title: t('characterCreator.failedTitle'),
            description: job.error || t('characterCreator.failedDescription'),
            type: 'error' as const,
            duration: 8000,
          };
          if (toaster.isVisible(toastId)) toaster.update(toastId, failedToast);
          else toaster.create({ id: toastId, ...failedToast });
          setJobId('');
          return;
        }
        const toastOptions = {
          title: t('characterCreator.runningTitle', { name: job.character_name }),
          description: progressDescription(
            t,
            job.status,
            job.completed,
            job.total,
            job.current_emotion,
          ),
          type: 'loading' as const,
        };
        if (toaster.isVisible(toastId)) toaster.update(toastId, toastOptions);
        else toaster.create({ id: toastId, ...toastOptions });
        schedule();
      } catch (error) {
        if (disposed) return;
        const status = (error as Error & { status?: number }).status;
        if (status === 401 || status === 404) {
          clearActiveCharacterGenerationJob(account);
          setJobId('');
          return;
        }
        schedule();
      }
    };

    void poll();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [account, jobId, t]);

  return null;
}
