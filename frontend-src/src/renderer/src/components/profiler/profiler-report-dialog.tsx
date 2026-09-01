import { useEffect, useState } from 'react';
import { Box, Button, Text } from '@chakra-ui/react';
import {
  DialogBody,
  DialogCloseTrigger,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogRoot,
  DialogTitle,
} from '@/components/ui/dialog';

interface ProfilerReport {
  content: string;
  path: string;
}

export function ProfilerReportDialog(): JSX.Element {
  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<ProfilerReport>({ content: '', path: '' });

  useEffect(() => {
    const handleComplete = (event: Event) => {
      const detail = (event as CustomEvent<Partial<ProfilerReport>>).detail || {};
      setReport({
        content: typeof detail.content === 'string' ? detail.content : '',
        path: typeof detail.path === 'string' ? detail.path : '',
      });
      setOpen(true);
    };
    window.addEventListener('profiler-analysis-complete', handleComplete);
    return () => window.removeEventListener('profiler-analysis-complete', handleComplete);
  }, []);

  return (
    <DialogRoot open={open} onOpenChange={(details) => setOpen(details.open)} size="xl">
      <DialogContent maxW="min(920px, calc(100vw - 32px))" maxH="calc(100vh - 48px)">
        <DialogHeader>
          <DialogTitle>薄片心理侧写报告</DialogTitle>
        </DialogHeader>
        <DialogBody overflowY="auto">
          <Box
            as="pre"
            m={0}
            p={4}
            borderRadius="md"
            bg="blackAlpha.300"
            color="whiteAlpha.900"
            fontFamily="inherit"
            fontSize="sm"
            lineHeight="1.75"
            whiteSpace="pre-wrap"
            overflowWrap="anywhere"
          >
            {report.content}
          </Box>
          {report.path && (
            <Text mt={3} color="whiteAlpha.600" fontSize="xs" overflowWrap="anywhere">
              已保存至：{report.path}
            </Text>
          )}
        </DialogBody>
        <DialogFooter>
          <Button onClick={() => setOpen(false)}>关闭</Button>
        </DialogFooter>
        <DialogCloseTrigger />
      </DialogContent>
    </DialogRoot>
  );
}
