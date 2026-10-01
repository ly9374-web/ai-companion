/* eslint-disable function-paren-newline */
/* eslint-disable react/jsx-one-expression-per-line */
/* eslint-disable no-trailing-spaces */
/* eslint-disable no-nested-ternary */
/* eslint-disable import/order */
/* eslint-disable import/no-extraneous-dependencies */
/* eslint-disable react/require-default-props */
import React from 'react';
import {
  Box, Spinner, Flex, Text, Icon,
} from '@chakra-ui/react';
import { sidebarStyles, chatPanelStyles } from './sidebar-styles';
import { MainContainer, ChatContainer, MessageList as ChatMessageList, Message as ChatMessage, Avatar as ChatAvatar } from '@chatscope/chat-ui-kit-react';
import '@chatscope/chat-ui-kit-styles/dist/default/styles.min.css';
import { useChatHistory } from '@/context/chat-history-context';
import { Global } from '@emotion/react';
import { useConfig } from '@/context/character-config-context';
import { useWebSocket } from '@/context/websocket-context';
import { FaTools, FaCheck, FaTimes } from 'react-icons/fa';
import { useTranslation } from 'react-i18next';
import { OptionalChatHistoryExtras } from '@optional-feature';

// Main component

// Classify a tool-result URL by extension for media rendering
const getMediaType = (url: string): 'image' | 'audio' | 'video' | 'link' => {
  const path = url.split('?')[0].toLowerCase();
  if (/\.(png|jpe?g|webp|gif|bmp)$/.test(path)) return 'image';
  if (/\.(mp3|wav|m4a|flac|aac|ogg)$/.test(path)) return 'audio';
  if (/\.(mp4|mov|webm|avi)$/.test(path)) return 'video';
  return 'link';
};

// Render a clickable media card for a tool-generated file URL
const renderToolMedia = (url: string, key: string) => {
  switch (getMediaType(url)) {
    case 'image':
      return (
        <img
          key={key}
          src={url}
          alt="tool generated image"
          style={{
            maxHeight: '200px',
            maxWidth: '240px',
            borderRadius: '8px',
            cursor: 'pointer',
            display: 'block',
          }}
          onClick={() => window.open(url, '_blank')}
        />
      );
    case 'audio':
      return (
        <Box key={key} width="100%" maxWidth="320px">
          <audio controls src={url} style={{ width: '100%' }} />
        </Box>
      );
    case 'video':
      return (
        <video
          key={key}
          controls
          preload="metadata"
          src={url}
          style={{ maxWidth: '280px', borderRadius: '8px', display: 'block' }}
        />
      );
    default:
      return (
        <Text key={key} fontSize="xs" isTruncated>
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            style={{ color: '#63B3ED' }}
          >
            {url}
          </a>
        </Text>
      );
  }
};

function ChatHistoryPanel(): JSX.Element {
  const { t } = useTranslation();
  const { messages } = useChatHistory(); // Get messages directly from context
  const { confName } = useConfig();
  const { baseUrl } = useWebSocket();
  const userName = "Me";

  const validMessages = messages.filter((msg) => msg.content || // Keep messages with content
     (msg.type === 'tool_call_status' && msg.status === 'running') || // Keep running tools
     (msg.type === 'tool_call_status' && msg.status === 'completed') || // Keep completed tools
     (msg.type === 'tool_call_status' && msg.status === 'error'), // Keep error tools
  );

  return (
    <Box
      h="full"
      overflow="hidden"
      bg="gray.900"
    >
      <Global styles={chatPanelStyles} />
      <MainContainer>
        <ChatContainer>
          <ChatMessageList>
            {validMessages.length === 0 ? (
              <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                height="100%"
                color="whiteAlpha.500"
                fontSize="sm"
              >
                {t('sidebar.noMessages')}
              </Box>
            ) : (
              <>
                {validMessages.map((msg) => {
                // Check if it's a tool call message
                if (msg.type === 'tool_call_status') {
                  const mediaUrls = msg.status === 'completed' && msg.media_urls?.length
                    ? msg.media_urls
                    : [];
                  return (
                    // Render Tool Call Indicator using msg properties
                    <Flex
                      key={msg.id} // Use tool_id as key
                      {...sidebarStyles.toolCallIndicator.container}
                      direction="column"
                      alignItems="flex-start"
                      justifyContent="flex-start"
                    >
                      <Flex alignItems="center" gap={2}>
                        <Icon
                          as={FaTools}
                          {...sidebarStyles.toolCallIndicator.icon}
                        />
                        <Text {...sidebarStyles.toolCallIndicator.text}>
                          {/* {msg.tool_name}: {msg.status === 'running' ? 'Running...' : msg.content} */}
                          {msg.status === "running" ? `${msg.name} is using tool ${msg.tool_name}` : `${msg.name} used tool ${msg.tool_name}`}
                        </Text>
                        {/* Show spinner if running, checkmark if completed, maybe error icon? */}
                        {msg.status === "running" && (
                          <Spinner
                            size="xs"
                            color={sidebarStyles.toolCallIndicator.spinner.color}
                            ml={sidebarStyles.toolCallIndicator.spinner.ml}
                          />
                        )}
                        {msg.status === "completed" && (
                          <Icon
                            as={FaCheck}
                            {...sidebarStyles.toolCallIndicator.completedIcon}
                          />
                        )}
                        {/* Optional: Add an error icon */}
                        {msg.status === "error" && (
                          <Icon
                            as={FaTimes}
                            {...sidebarStyles.toolCallIndicator.errorIcon}
                          />
                        )}
                      </Flex>
                      {mediaUrls.length > 0 && (
                        <Flex
                          direction="column"
                          gap={2}
                          mt={1}
                          width="100%"
                          pl="22px"
                        >
                          {mediaUrls.map((url, idx) => renderToolMedia(url, `${msg.id}-media-${idx}`))}
                        </Flex>
                      )}
                    </Flex>
                  );
                }
                // Render Standard Chat Message (human or ai text)
                return (
                  <ChatMessage
                    key={msg.id}
                    model={{
                      message: msg.content,
                      sentTime: msg.timestamp,
                      sender: msg.role === 'ai'
                        ? (msg.name || confName || 'AI')
                        : userName,
                      direction: msg.role === 'ai' ? 'incoming' : 'outgoing',
                      position: 'single',
                    }}
                    avatarPosition={msg.role === 'ai' ? 'tl' : 'tr'}
                    avatarSpacer={false}
                  >
                    <ChatAvatar>
                      {msg.role === 'ai' ? (
                        msg.avatar ? (
                          <img
                            src={`${baseUrl}/avatars/${msg.avatar}`}
                            alt="avatar"
                            style={{ width: '100%', height: '100%', borderRadius: '50%' }}
                            onError={(e) => {
                              const target = e.target as HTMLImageElement;
                              const fallbackName = msg.name || confName || 'A';
                              target.outerHTML = `<div style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; border-radius: 50%; background-color: var(--chakra-colors-blue-500); color: white; font-size: 14px;">${fallbackName[0].toUpperCase()}</div>`;
                            }}
                          />
                        ) : (
                          (msg.name && msg.name[0].toUpperCase()) ||
                            (confName && confName[0].toUpperCase()) ||
                            'A'
                        )
                      ) : (
                        userName[0].toUpperCase()
                      )}
                    </ChatAvatar>
                  </ChatMessage>
                );
                })}
                <OptionalChatHistoryExtras />
              </>
            )}
          </ChatMessageList>
        </ChatContainer>
      </MainContainer>
    </Box>
  );
}

export default ChatHistoryPanel;
