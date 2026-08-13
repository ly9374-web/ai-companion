import { Box, Image } from '@chakra-ui/react';
import { memo } from 'react';
import { canvasStyles } from './canvas-styles';
import { useBgUrl } from '@/context/bgurl-context';
import { OptionalBackground } from '@optional-feature';

const Background = memo(({ children }: { children?: React.ReactNode }) => {
  const { backgroundUrl } = useBgUrl();

  return (
    <Box {...canvasStyles.background.container}>
      <OptionalBackground fallback={(
        <Image
          {...canvasStyles.background.image}
          src={backgroundUrl}
          alt="background"
        />
      )} />
      {children}
    </Box>
  );
});

Background.displayName = 'Background';

export default Background;
