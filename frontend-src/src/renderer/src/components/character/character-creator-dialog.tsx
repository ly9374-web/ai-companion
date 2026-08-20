import {
  Box, Input, Stack, Text,
} from '@chakra-ui/react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
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
import { Field } from '@/components/ui/field';
import { useAccount } from '@/context/account-context';
import { getStoredApiKeys } from '@/constants/api-keys';
import { toaster } from '@/components/ui/toaster';
import {
  CHARACTER_GENERATION_STARTED_EVENT,
  createCharacterGenerationJob,
  rememberActiveCharacterGenerationJob,
} from '@/services/character-generation';

interface CharacterCreatorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CharacterCreatorDialog({
  open,
  onOpenChange,
}: CharacterCreatorDialogProps): JSX.Element {
  const { t } = useTranslation();
  const { account } = useAccount();
  const [characterName, setCharacterName] = useState('');
  const [referenceImage, setReferenceImage] = useState<File | null>(null);
  const [avatarImage, setAvatarImage] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const reset = (): void => {
    setCharacterName('');
    setReferenceImage(null);
    setAvatarImage(null);
    setError('');
  };

  const close = (): void => {
    if (submitting) return;
    onOpenChange(false);
    reset();
  };

  const handleSubmit = async (): Promise<void> => {
    if (!account || submitting) return;
    const replicateToken = getStoredApiKeys().replicateApiKey.trim();
    if (!replicateToken) {
      setError(t('characterCreator.replicateKeyRequired'));
      return;
    }
    if (!characterName.trim() || !referenceImage || !avatarImage) {
      setError(t('characterCreator.requiredFields'));
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const job = await createCharacterGenerationJob({
        account,
        characterName: characterName.trim(),
        replicateToken,
        referenceImage,
        avatarImage,
      });
      rememberActiveCharacterGenerationJob(account, job.id);
      window.dispatchEvent(new CustomEvent(CHARACTER_GENERATION_STARTED_EVENT, {
        detail: { jobId: job.id },
      }));
      toaster.create({
        title: t('characterCreator.acceptedTitle'),
        description: t('characterCreator.acceptedDescription'),
        type: 'success',
        duration: 2500,
      });
      onOpenChange(false);
      reset();
    } catch (submitError) {
      setError(submitError instanceof Error
        ? submitError.message
        : t('characterCreator.failedDescription'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <DialogRoot
      open={open}
      onOpenChange={(details) => {
        if (!details.open) close();
      }}
    >
      <DialogContent
        bg="gray.900"
        color="white"
        borderRadius="2xl"
        border="1px solid"
        borderColor="whiteAlpha.200"
        maxW="lg"
      >
        <DialogHeader>
          <DialogTitle>{t('characterCreator.title')}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <Stack gap={4}>
            <DialogDescription color="whiteAlpha.700">
              {t('characterCreator.description')}
            </DialogDescription>
            <Field label={t('characterCreator.nameLabel')} required>
              <Input
                value={characterName}
                onChange={(event) => setCharacterName(event.target.value)}
                placeholder={t('characterCreator.namePlaceholder')}
                disabled={submitting}
                maxLength={48}
              />
            </Field>
            <Field label={t('characterCreator.referenceLabel')} required>
              <Input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={(event) => setReferenceImage(event.target.files?.[0] || null)}
                disabled={submitting}
                p={1}
              />
            </Field>
            <Field label={t('characterCreator.avatarLabel')} required>
              <Input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={(event) => setAvatarImage(event.target.files?.[0] || null)}
                disabled={submitting}
                p={1}
              />
            </Field>
            <Text fontSize="sm" color="orange.300">
              {t('characterCreator.costNotice')}
            </Text>
            {error && (
              <Box bg="red.950" color="red.200" borderRadius="md" p={3} fontSize="sm">
                {error}
              </Box>
            )}
          </Stack>
        </DialogBody>
        <DialogFooter gap={3}>
          <Button variant="ghost" onClick={close} disabled={submitting}>
            {t('common.cancel')}
          </Button>
          <Button
            colorPalette="blue"
            onClick={() => void handleSubmit()}
            loading={submitting}
            loadingText={t('characterCreator.submitting')}
          >
            {t('characterCreator.create')}
          </Button>
        </DialogFooter>
        <DialogCloseTrigger />
      </DialogContent>
    </DialogRoot>
  );
}

