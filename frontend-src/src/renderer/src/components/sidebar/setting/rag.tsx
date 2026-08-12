/* eslint-disable import/no-extraneous-dependencies */
import { Box, HStack, Stack, Text } from '@chakra-ui/react';
import { useTranslation } from 'react-i18next';
import { Slider } from '@/components/ui/slider';
import { settingStyles } from './setting-styles';
import { useRagSettings } from '@/hooks/sidebar/setting/use-rag-settings';

interface RagProps {
  onSave?: (callback: () => void) => () => void;
  onCancel?: (callback: () => void) => () => void;
}

interface RagSliderProps {
  label: string;
  value: number;
  displayValue: string;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  leftLabel?: string;
  rightLabel?: string;
}

function RagSlider({
  label, value, displayValue, min, max, step, onChange, leftLabel, rightLabel,
}: RagSliderProps): JSX.Element {
  return (
    <Box>
      <HStack justify="space-between" mb={3}>
        <Text color="whiteAlpha.800">{label}</Text>
        <Text color="blue.200" fontVariantNumeric="tabular-nums">{displayValue}</Text>
      </HStack>
      <Slider
        min={min}
        max={max}
        step={step}
        value={[value]}
        colorPalette="blue"
        onValueChange={(details) => onChange(details.value[0])}
      />
      {(leftLabel || rightLabel) && (
        <HStack justify="space-between" mt={2}>
          <Text fontSize="xs" color="whiteAlpha.600">{leftLabel}</Text>
          <Text fontSize="xs" color="whiteAlpha.600">{rightLabel}</Text>
        </HStack>
      )}
    </Box>
  );
}

function Rag({ onSave, onCancel }: RagProps): JSX.Element {
  const { t } = useTranslation();
  const { settings, update } = useRagSettings({ onSave, onCancel });

  return (
    <Stack {...settingStyles.common.container} gap={8}>
      <RagSlider
        label={t('settings.rag.topK')}
        value={settings.topK}
        displayValue={String(settings.topK)}
        min={1}
        max={20}
        step={1}
        onChange={(value) => update('topK', value)}
      />
      <RagSlider
        label={t('settings.rag.threshold')}
        value={settings.threshold}
        displayValue={settings.threshold.toFixed(2)}
        min={0}
        max={1}
        step={0.05}
        onChange={(value) => update('threshold', value)}
      />
      <RagSlider
        label={t('settings.rag.hybridWeight')}
        value={settings.hybridWeight}
        displayValue={settings.hybridWeight.toFixed(2)}
        min={0}
        max={1}
        step={0.1}
        onChange={(value) => update('hybridWeight', value)}
        leftLabel={t('settings.rag.keywordOnly')}
        rightLabel={t('settings.rag.vectorOnly')}
      />
    </Stack>
  );
}

export default Rag;
