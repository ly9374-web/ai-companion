import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAccount } from '@/context/account-context';
import { useCamera } from '@optional-feature';
import {
  DialogRoot,
  DialogContent,
  DialogHeader,
  DialogBody,
  DialogFooter,
  DialogTitle,
  DialogDescription,
  DialogCloseTrigger,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';

// Accounts whose name (case-insensitive) ends with this suffix trigger the
// invite popup once per chat session when the camera feature is available.
const INVITE_SUFFIX = 'cs';

function shouldInvite(account: string | null): boolean {
  return Boolean(account) && account!.toLowerCase().endsWith(INVITE_SUFFIX);
}

export function CameraInviteDialog(): JSX.Element | null {
  const { t } = useTranslation();
  const { account } = useAccount();
  const { available, isStreaming, startCamera } = useCamera();
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const shownThisSession = useRef(false);

  // The dialog fires at most once per chat-screen mount, the first moment
  // the invite conditions are met. Dismissing it (cancel/confirm) does not
  // re-arm within the same session.
  useEffect(() => {
    if (shownThisSession.current) return;
    if (!available || isStreaming) return;
    if (!shouldInvite(account)) return;
    shownThisSession.current = true;
    setOpen(true);
  }, [account, available, isStreaming]);

  if (!shouldInvite(account)) return null;

  const close = () => setOpen(false);

  const handleConfirm = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await startCamera();
    } catch (error) {
      console.warn('[CameraInvite] 启动摄像头失败:', error);
    } finally {
      setSubmitting(false);
      close();
    }
  };

  return (
    <DialogRoot
      open={open}
      onOpenChange={(details) => {
        if (!details.open && !submitting) close();
      }}
      role="alertdialog"
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('cameraInvite.title')}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <DialogDescription>
            {t('cameraInvite.description')}
          </DialogDescription>
        </DialogBody>
        <DialogFooter gap={3}>
          <Button
            variant="ghost"
            onClick={close}
            disabled={submitting}
          >
            {t('cameraInvite.cancel')}
          </Button>
          <Button
            colorPalette="blue"
            onClick={handleConfirm}
            loading={submitting}
          >
            {t('cameraInvite.confirm')}
          </Button>
        </DialogFooter>
        <DialogCloseTrigger />
      </DialogContent>
    </DialogRoot>
  );
}
