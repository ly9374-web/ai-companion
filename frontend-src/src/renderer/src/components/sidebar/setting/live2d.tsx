/* eslint-disable import/no-extraneous-dependencies */
/* eslint-disable react-hooks/rules-of-hooks */
import { Stack } from '@chakra-ui/react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { settingStyles } from './setting-styles';
import { useLive2dSettings } from '@/hooks/sidebar/setting/use-live2d-settings';
import { SwitchField } from './common';
import TTS from './tts';
import { Button } from '@/components/ui/button';
import { CharacterCreatorDialog } from '@/components/character/character-creator-dialog';

interface live2DProps {
  onSave?: (callback: () => void) => () => void
  onCancel?: (callback: () => void) => () => void
}

function live2D({ onSave, onCancel }: live2DProps): JSX.Element {
  const { t } = useTranslation();
  const [creatorOpen, setCreatorOpen] = useState(false);
  const {
    modelInfo,
    handleInputChange,
    handleSave,
    handleCancel,
  } = useLive2dSettings();

  useEffect(() => {
    if (!onSave || !onCancel) return;

    const cleanupSave = onSave(handleSave);
    const cleanupCancel = onCancel(handleCancel);

    return (): void => {
      cleanupSave?.();
      cleanupCancel?.();
    };
  }, [onSave, onCancel]);

  return (
    <Stack {...settingStyles.common.container}>
      <SwitchField
        label={t('settings.live2d.pointerInteractive')}
        checked={modelInfo.pointerInteractive ?? false}
        onChange={(checked) => handleInputChange('pointerInteractive', checked)}
      />

      <SwitchField
        label={t('settings.live2d.scrollToResize')}
        checked={modelInfo.scrollToResize ?? false}
        onChange={(checked) => handleInputChange('scrollToResize', checked)}
      />

      <Button colorPalette="blue" onClick={() => setCreatorOpen(true)}>
        {t('settings.live2d.addCharacter')}
      </Button>
      <CharacterCreatorDialog open={creatorOpen} onOpenChange={setCreatorOpen} />

      <TTS onSave={onSave} onCancel={onCancel} />
    </Stack>
  );
}

export default live2D;
