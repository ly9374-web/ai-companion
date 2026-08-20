/* eslint-disable react/require-default-props */
import { Box, Button, Menu, Textarea } from '@chakra-ui/react';
import {
  FiSettings, FiClock, FiPlus, FiChevronLeft, FiLayers, FiUser
} from 'react-icons/fi';
import { memo, useEffect, useState, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { sidebarStyles } from './sidebar-styles';
import SettingUI from './setting/setting-ui';
import ChatHistoryPanel from './chat-history-panel';
import BottomTab from './bottom-tab';
import HistoryDrawer from './history-drawer';
import { useSidebar } from '@/hooks/sidebar/use-sidebar';
import { ModeType } from '@/context/mode-context';
import { wsService } from '@/services/websocket-service';
import {
  DialogRoot,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogBody,
  DialogFooter,
  DialogBackdrop,
} from '@/components/ui/dialog';
import { toaster } from '@/components/ui/toaster';

// Type definitions
interface SidebarProps {
  isCollapsed?: boolean
  onToggle: () => void
}

interface HeaderButtonsProps {
  onSettingsOpen: () => void
  onNewHistory: () => void
  setMode: (mode: ModeType) => void
  currentMode: 'window' | 'pet'
  isElectron: boolean
}

// Reusable components
const ToggleButton = memo(({ isCollapsed, onToggle }: {
  isCollapsed: boolean
  onToggle: () => void
}) => (
  <Box
    {...sidebarStyles.sidebar.toggleButton}
    style={{
      transform: isCollapsed ? 'rotate(180deg)' : 'rotate(0deg)',
    }}
    onClick={onToggle}
  >
    <FiChevronLeft />
  </Box>
));

ToggleButton.displayName = 'ToggleButton';

const ModeMenu = memo(({ setMode, currentMode, isElectron }: {
  setMode: (mode: ModeType) => void
  currentMode: ModeType
  isElectron: boolean
}) => (
  <Menu.Root>
    <Menu.Trigger as={Button} aria-label="Mode Menu" title="Change Mode">
      <FiLayers />
    </Menu.Trigger>
    <Menu.Positioner>
      <Menu.Content>
        <Menu.RadioItemGroup value={currentMode}>
          <Menu.RadioItem value="window" onClick={() => setMode('window')}>
            <Menu.ItemIndicator />
            Live Mode
          </Menu.RadioItem>
          <Menu.RadioItem
            value="pet"
            onClick={() => {
              if (isElectron) {
                setMode('pet');
              }
            }}
            disabled={!isElectron}
            title={!isElectron ? "Pet mode is only available in desktop app" : undefined}
          >
            <Menu.ItemIndicator />
            Pet Mode
          </Menu.RadioItem>
        </Menu.RadioItemGroup>
      </Menu.Content>
    </Menu.Positioner>
  </Menu.Root>
));

ModeMenu.displayName = 'ModeMenu';

// System prompt editor modal: shows the editable section of the active
// character's system prompt, with reset/cancel/confirm actions. The backend
// keeps the fixed section (emomap + emotion rules) untouched; only the
// editable persona section is stored per account/role.
const SystemPromptButton = memo(() => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [content, setContent] = useState('');
  // Track whether the currently displayed content is the default (non-overridden)
  // version, so we can toggle the reset button label/state meaningfully.
  const initialContentRef = useRef('');
  const isDirty = content !== initialContentRef.current;

  // Keep latest handlers in refs so window listeners can be attached once on
  // mount and always invoke the up-to-date closure. This avoids a race where
  // the backend responds before the open-gated useEffect attaches the listener.
  const handleFetchResultRef = useRef<(e: Event) => void>(() => {});
  const handleUpdateResultRef = useRef<(e: Event) => void>(() => {});
  const handleResetResultRef = useRef<(e: Event) => void>(() => {});

  handleFetchResultRef.current = (event: Event) => {
    const detail = (event as CustomEvent).detail as {
      content?: string;
      error?: string;
    };
    setLoading(false);
    if (detail.error) {
      toaster.create({
        title: t('systemPrompt.fetchFailed'),
        description: detail.error,
        type: 'error',
        duration: 3000,
      });
      setOpen(false);
      return;
    }
    setContent(detail.content ?? '');
    initialContentRef.current = detail.content ?? '';
  };

  handleUpdateResultRef.current = (event: Event) => {
    const detail = (event as CustomEvent).detail as {
      success?: boolean;
      error?: string;
    };
    setSubmitting(false);
    if (detail.success) {
      toaster.create({
        title: t('systemPrompt.updateSuccess'),
        type: 'success',
        duration: 2500,
      });
      setOpen(false);
    } else {
      toaster.create({
        title: t('systemPrompt.updateFailed'),
        description: detail.error,
        type: 'error',
        duration: 3000,
      });
    }
  };

  handleResetResultRef.current = (event: Event) => {
    const detail = (event as CustomEvent).detail as {
      success?: boolean;
      content?: string;
      error?: string;
    };
    setSubmitting(false);
    if (detail.success) {
      setContent(detail.content ?? '');
      initialContentRef.current = detail.content ?? '';
      toaster.create({
        title: t('systemPrompt.resetSuccess'),
        type: 'success',
        duration: 2500,
      });
    } else {
      toaster.create({
        title: t('systemPrompt.resetFailed'),
        description: detail.error,
        type: 'error',
        duration: 3000,
      });
    }
  };

  useEffect(() => {
    const onFetch = (e: Event) => handleFetchResultRef.current(e);
    const onUpdate = (e: Event) => handleUpdateResultRef.current(e);
    const onReset = (e: Event) => handleResetResultRef.current(e);
    window.addEventListener('system-prompt', onFetch as EventListener);
    window.addEventListener('system-prompt-updated', onUpdate as EventListener);
    window.addEventListener('system-prompt-reset', onReset as EventListener);
    return () => {
      window.removeEventListener('system-prompt', onFetch as EventListener);
      window.removeEventListener('system-prompt-updated', onUpdate as EventListener);
      window.removeEventListener('system-prompt-reset', onReset as EventListener);
    };
  }, []);

  // Timeout watchdog: if the backend doesn't respond within 8s, surface a
  // clear error instead of leaving the modal stuck in the loading state.
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startWatchdog = useCallback(() => {
    if (watchdogRef.current) clearTimeout(watchdogRef.current);
    watchdogRef.current = setTimeout(() => {
      setLoading(false);
      setSubmitting(false);
      toaster.create({
        title: t('systemPrompt.fetchFailed'),
        description: 'backend did not respond (is the server running the new code?)',
        type: 'error',
        duration: 5000,
      });
      setOpen(false);
    }, 8000);
  }, [t]);
  const clearWatchdog = useCallback(() => {
    if (watchdogRef.current) {
      clearTimeout(watchdogRef.current);
      watchdogRef.current = null;
    }
  }, []);

  // Clear watchdog whenever loading/submitting flips off (response arrived).
  useEffect(() => {
    if (!loading && !submitting) clearWatchdog();
  }, [loading, submitting, clearWatchdog]);

  const handleOpen = useCallback(() => {
    setContent('');
    initialContentRef.current = '';
    setLoading(true);
    setSubmitting(false);
    setOpen(true);
    wsService.fetchSystemPrompt();
    startWatchdog();
  }, [startWatchdog]);

  const handleConfirm = useCallback(() => {
    if (!content.trim()) return;
    setSubmitting(true);
    wsService.updateSystemPrompt(content);
    startWatchdog();
  }, [content, startWatchdog]);

  const handleReset = useCallback(() => {
    // eslint-disable-next-line no-alert
    if (!window.confirm(t('systemPrompt.resetConfirm'))) return;
    setSubmitting(true);
    wsService.resetSystemPrompt();
    startWatchdog();
  }, [t, startWatchdog]);

  const handleOpenChange = useCallback((details: { open: boolean }) => {
    // Only allow closing when not submitting, to avoid losing an in-flight update.
    if (submitting) return;
    setOpen(details.open);
  }, [submitting]);

  return (
    <>
      <Button
        aria-label="Edit system prompt"
        title={t('systemPrompt.title')}
        onClick={handleOpen}
        minH="41px"
      >
        <FiUser />
      </Button>
      <DialogRoot open={open} onOpenChange={handleOpenChange}>
        <DialogBackdrop />
        <DialogContent
          bg="gray.900"
          color="white"
          borderRadius="2xl"
          boxShadow="0 12px 48px rgba(0,0,0,0.6)"
          border="1px solid"
          borderColor="whiteAlpha.200"
        >
        <DialogHeader borderBottom="1px solid" borderColor="whiteAlpha.100">
          <DialogTitle color="white">{t('systemPrompt.title')}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <Box
            p="2px"
            borderRadius="xl"
            style={{
              backgroundImage:
                'linear-gradient(135deg, #1e3a8a 0%, #4c1d95 100%)',
            }}
          >
            <Textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={loading ? t('systemPrompt.loading') : ''}
              isDisabled={loading || submitting}
              minH="320px"
              fontFamily="mono"
              fontSize="sm"
              whiteSpace="pre"
              overflow="auto"
              bg="black"
              color="white"
              borderRadius="lg"
              border="none"
              _placeholder={{ color: 'whiteAlpha.500' }}
              _disabled={{ opacity: 0.6, cursor: 'not-allowed' }}
              _focus={{
                border: 'none',
                boxShadow: 'none',
              }}
            />
          </Box>
        </DialogBody>
        <DialogFooter borderTop="1px solid" borderColor="whiteAlpha.100">
          <Button
            variant="ghost"
            colorScheme="red"
            onClick={handleReset}
            isDisabled={loading || submitting}
          >
            {t('systemPrompt.reset')}
          </Button>
          <Box flex="1" />
          <Button
            variant="ghost"
            color="white"
            _hover={{ bg: 'whiteAlpha.200' }}
            onClick={() => setOpen(false)}
            isDisabled={loading || submitting}
          >
            {t('common.cancel')}
          </Button>
          <Button
            colorScheme="blue"
            onClick={handleConfirm}
            isDisabled={loading || submitting || !content.trim() || !isDirty}
          >
            {t('common.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
      </DialogRoot>
    </>
  );
});

SystemPromptButton.displayName = 'SystemPromptButton';

const HeaderButtons = memo(({ onSettingsOpen, onNewHistory, setMode, currentMode, isElectron }: HeaderButtonsProps) => (
  <Box display="flex" gap={1}>
    <Button onClick={onSettingsOpen}>
      <FiSettings />
    </Button>

    <HistoryDrawer>
      <Button>
        <FiClock />
      </Button>
    </HistoryDrawer>

    <Button onClick={onNewHistory}>
      <FiPlus />
    </Button>

    <ModeMenu setMode={setMode} currentMode={currentMode} isElectron={isElectron} />

    <SystemPromptButton />
  </Box>
));

HeaderButtons.displayName = 'HeaderButtons';

const SidebarContent = memo(({
  onSettingsOpen,
  onNewHistory,
  setMode,
  currentMode,
  isElectron,
}: HeaderButtonsProps) => (
  <Box {...sidebarStyles.sidebar.content}>
    <Box {...sidebarStyles.sidebar.header}>
      <HeaderButtons
        onSettingsOpen={onSettingsOpen}
        onNewHistory={onNewHistory}
        setMode={setMode}
        currentMode={currentMode}
        isElectron={isElectron}
      />
    </Box>
    <ChatHistoryPanel />
    <BottomTab />
  </Box>
));

SidebarContent.displayName = 'SidebarContent';

// Main component
function Sidebar({ isCollapsed = false, onToggle }: SidebarProps): JSX.Element {
  const {
    settingsOpen,
    onSettingsOpen,
    onSettingsClose,
    createNewHistory,
    setMode,
    currentMode,
    isElectron,
  } = useSidebar();

  return (
    <Box {...sidebarStyles.sidebar.container(isCollapsed)}>
      <ToggleButton isCollapsed={isCollapsed} onToggle={onToggle} />

      {!isCollapsed && !settingsOpen && (
        <SidebarContent
          onSettingsOpen={onSettingsOpen}
          onNewHistory={createNewHistory}
          setMode={setMode}
          currentMode={currentMode}
          isElectron={isElectron}
        />
      )}

      {!isCollapsed && settingsOpen && (
        <SettingUI
          open={settingsOpen}
          onClose={onSettingsClose}
          onToggle={onToggle}
        />
      )}
    </Box>
  );
}

export default Sidebar;
