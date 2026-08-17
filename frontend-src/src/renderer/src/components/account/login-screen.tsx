import {
  Box, Button, Image, Input, Spinner, Stack, Text,
} from '@chakra-ui/react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  LuEye, LuEyeOff, LuLockKeyhole, LuLogIn, LuUserRound, LuUserRoundPlus,
} from 'react-icons/lu';
import { useAccount } from '@/context/account-context';
import {
  DEFAULT_BASE_URL,
  DEFAULT_WS_URL,
  getCurrentBaseUrl,
  setCurrentBaseUrl,
  setCurrentWsUrl,
} from '@/constants/connection-settings';
import {
  getLastAccountBackground,
  getLastAccountName,
} from '@/constants/account-settings';
import TitleBar from '@/components/electron/title-bar';
import { InputGroup } from '@/components/ui/input-group';

function LoginScreen(): JSX.Element {
  const { t } = useTranslation();
  const { loading, login, register } = useAccount();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [name, setName] = useState(getLastAccountName());
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const defaultBackground = `${getCurrentBaseUrl() || DEFAULT_BASE_URL}/bg/ceiling-window-room-night.jpeg`;
  const backgroundUrl = getLastAccountBackground(defaultBackground);
  const isElectron = window.api !== undefined;

  const submit = async (): Promise<void> => {
    if (submitting) return;
    if (!name.trim()) {
      setError(t('account.accountRequired'));
      return;
    }
    if (password.length < 3) {
      setError(t('account.passwordTooShort'));
      return;
    }
    if (mode === 'register' && password !== confirmPassword) {
      setError(t('account.passwordMismatch'));
      return;
    }
    setSubmitting(true);
    setError('');
    setConnectionError(false);
    const result = mode === 'login'
      ? await login(name, password)
      : await register(name, password);
    if (!result.ok) {
      setError(result.error || t('account.operationFailed'));
      setConnectionError(Boolean(result.connectionError));
    }
    setSubmitting(false);
  };

  const enterRegister = (): void => {
    setMode('register');
    setName('');
    setPassword('');
    setConfirmPassword('');
    setError('');
    setConnectionError(false);
  };

  const cancelRegister = (): void => {
    setMode('login');
    setName(getLastAccountName());
    setPassword('');
    setConfirmPassword('');
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
      <Box
        position="absolute"
        inset={0}
        bg="linear-gradient(135deg, rgba(5, 15, 35, 0.72), rgba(17, 24, 39, 0.45))"
      />
      <Stack
        position="relative"
        zIndex={1}
        minHeight="100vh"
        align="center"
        justify="center"
        px={{ base: 4, md: 6 }}
      >
        <Box
          as="form"
          width="min(430px, 100%)"
          p={{ base: 6, md: 8 }}
          borderRadius="2xl"
          bg="rgba(13, 22, 38, 0.82)"
          border="1px solid"
          borderColor="whiteAlpha.400"
          backdropFilter="blur(20px)"
          boxShadow="0 24px 70px rgba(0, 0, 0, 0.42)"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <Stack gap={6}>
            <Stack gap={2} textAlign="center" align="center">
              <Box
                display="grid"
                placeItems="center"
                width="52px"
                height="52px"
                borderRadius="full"
                bg="blue.500"
                color="white"
                boxShadow="0 10px 30px rgba(59, 130, 246, 0.38)"
              >
                {mode === 'login' ? <LuLogIn size={24} /> : <LuUserRoundPlus size={24} />}
              </Box>
              <Text color="white" fontSize="2xl" fontWeight="700" letterSpacing="-0.02em">
                {mode === 'login' ? t('account.welcomeBack') : t('account.createAccount')}
              </Text>
              <Text color="whiteAlpha.700" fontSize="sm">
                {mode === 'login' ? t('account.loginSubtitle') : t('account.registerSubtitle')}
              </Text>
            </Stack>

            <Stack gap={4}>
              <Stack gap={1.5}>
                <Text color="whiteAlpha.900" fontSize="sm" fontWeight="600">
                  {t('account.accountLabel')}
                </Text>
                <InputGroup startElement={<LuUserRound />}>
                  <Input
                    name="account"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder={t('account.accountPlaceholder')}
                    autoComplete="username"
                    size="lg"
                    bg="whiteAlpha.950"
                    borderColor="transparent"
                    color="gray.900"
                    _placeholder={{ color: 'gray.500' }}
                    _focus={{ borderColor: 'blue.400', boxShadow: '0 0 0 1px #60a5fa' }}
                  />
                </InputGroup>
              </Stack>

              <Stack gap={1.5}>
                <Text color="whiteAlpha.900" fontSize="sm" fontWeight="600">
                  {t('account.passwordLabel')}
                </Text>
                <InputGroup
                  startElement={<LuLockKeyhole />}
                  endElement={(
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      minW="32px"
                      color="gray.500"
                      aria-label={showPassword
                        ? t('account.hidePassword')
                        : t('account.showPassword')}
                      onClick={() => setShowPassword((visible) => !visible)}
                    >
                      {showPassword ? <LuEyeOff /> : <LuEye />}
                    </Button>
                  )}
                >
                  <Input
                    name="password"
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder={t('account.passwordPlaceholder')}
                    autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                    size="lg"
                    bg="whiteAlpha.950"
                    borderColor="transparent"
                    color="gray.900"
                    _placeholder={{ color: 'gray.500' }}
                    _focus={{ borderColor: 'blue.400', boxShadow: '0 0 0 1px #60a5fa' }}
                  />
                </InputGroup>
              </Stack>

              {mode === 'register' && (
                <Stack gap={1.5}>
                  <Text color="whiteAlpha.900" fontSize="sm" fontWeight="600">
                    {t('account.confirmPasswordLabel')}
                  </Text>
                  <InputGroup startElement={<LuLockKeyhole />}>
                    <Input
                      name="confirmPassword"
                      type={showPassword ? 'text' : 'password'}
                      value={confirmPassword}
                      onChange={(event) => setConfirmPassword(event.target.value)}
                      placeholder={t('account.confirmPasswordPlaceholder')}
                      autoComplete="new-password"
                      size="lg"
                      bg="whiteAlpha.950"
                      borderColor="transparent"
                      color="gray.900"
                      _placeholder={{ color: 'gray.500' }}
                      _focus={{ borderColor: 'blue.400', boxShadow: '0 0 0 1px #60a5fa' }}
                    />
                  </InputGroup>
                  <Text color="whiteAlpha.600" fontSize="xs">
                    {t('account.passwordHint')}
                  </Text>
                </Stack>
              )}
            </Stack>

            {error && (
              <Box px={3} py={2.5} borderRadius="lg" bg="red.900" border="1px solid" borderColor="red.700">
                <Text color="red.100" fontSize="sm">{error}</Text>
              </Box>
            )}
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

            <Button
              type="submit"
              width="100%"
              size="lg"
              colorPalette="blue"
              disabled={loading || submitting}
              fontWeight="700"
            >
              {(loading || submitting) && <Spinner size="sm" />}
              {mode === 'login' ? t('account.login') : t('account.createAccountButton')}
            </Button>

            <Text color="whiteAlpha.700" fontSize="sm" textAlign="center">
              {mode === 'login' ? t('account.noAccount') : t('account.hasAccount')}
              {' '}
              <Button
                type="button"
                variant="plain"
                size="sm"
                p={0}
                height="auto"
                color="blue.300"
                disabled={loading || submitting}
                onClick={mode === 'login' ? enterRegister : cancelRegister}
              >
                {mode === 'login' ? t('account.registerNow') : t('account.backToLogin')}
              </Button>
            </Text>
          </Stack>
        </Box>
      </Stack>
    </Box>
  );
}

export default LoginScreen;
