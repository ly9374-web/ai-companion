import {
  Box, Button, HStack, Image, Spinner, Stack, Text, Textarea,
} from '@chakra-ui/react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAccount } from '@/context/account-context';
import {
  DEFAULT_BASE_URL,
  DEFAULT_WS_URL,
  getCurrentBaseUrl,
  setCurrentBaseUrl,
  setCurrentWsUrl,
} from '@/constants/connection-settings';
import { getLastAccountBackground } from '@/constants/account-settings';
import TitleBar from '@/components/electron/title-bar';

function LoginScreen(): JSX.Element {
  const { t } = useTranslation();
  const { loading, login, register } = useAccount();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const defaultBackground = `${getCurrentBaseUrl() || DEFAULT_BASE_URL}/bg/ceiling-window-room-night.jpeg`;
  const backgroundUrl = getLastAccountBackground(defaultBackground);
  const isElectron = window.api !== undefined;

  const submit = async (): Promise<void> => {
    if (submitting) return;
    setSubmitting(true);
    setError('');
    setConnectionError(false);
    const result = mode === 'login' ? await login(name) : await register(name);
    if (!result.ok) {
      setError(result.error || t('account.operationFailed'));
      setConnectionError(Boolean(result.connectionError));
    }
    setSubmitting(false);
  };

  const enterRegister = (): void => {
    setMode('register');
    setName('');
    setError('');
    setConnectionError(false);
  };

  const cancelRegister = (): void => {
    setMode('login');
    setName('');
    setError('');
    setConnectionError(false);
  };

  const restoreDefaultConnection = (): void => {
    setCurrentBaseUrl(DEFAULT_BASE_URL);
    setCurrentWsUrl(DEFAULT_WS_URL);
    window.location.reload();
  };

  return (
    <Box position="fixed" inset={0} overflow="hidden" bg="gray.900">
      {isElectron && <TitleBar />}
      <Image
        src={backgroundUrl}
        alt="background"
        position="absolute"
        inset={0}
        width="100%"
        height="100%"
        objectFit="cover"
      />
      <Box position="absolute" inset={0} bg="blackAlpha.500" />
      <Stack
        position="relative"
        zIndex={1}
        minHeight="100vh"
        align="center"
        justify="center"
        px={4}
      >
        <Stack
          width="min(420px, 100%)"
          gap={4}
          p={6}
          borderRadius="xl"
          bg="blackAlpha.700"
          border="1px solid"
          borderColor="whiteAlpha.300"
          backdropFilter="blur(14px)"
          boxShadow="2xl"
        >
          <Textarea
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
            placeholder={mode === 'login'
              ? t('account.loginPlaceholder')
              : t('account.registerPlaceholder')}
            resize="none"
            minHeight="88px"
            bg="whiteAlpha.900"
            color="gray.900"
            _placeholder={{ color: 'gray.500' }}
          />
          {error && <Text color="red.200" fontSize="sm">{error}</Text>}
          {connectionError && (
            <Button
              size="sm"
              variant="ghost"
              colorPalette="orange"
              onClick={restoreDefaultConnection}
            >
              {t('account.restoreDefaultConnection')}
            </Button>
          )}
          <HStack gap={3} width="100%">
            <Button
              flex={1}
              colorPalette="blue"
              disabled={loading || submitting}
              onClick={() => void submit()}
            >
              {(loading || submitting) && <Spinner size="sm" />}
              {mode === 'login' ? t('account.login') : t('account.confirm')}
            </Button>
            <Button
              flex={1}
              disabled={loading || submitting}
              onClick={mode === 'login' ? enterRegister : cancelRegister}
            >
              {mode === 'login' ? t('account.register') : t('account.cancel')}
            </Button>
          </HStack>
        </Stack>
      </Stack>
    </Box>
  );
}

export default LoginScreen;
