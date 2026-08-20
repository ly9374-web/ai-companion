import { getAccountSessionToken } from '@/constants/account-settings';
import { getCurrentBaseUrl } from '@/constants/connection-settings';

export const CHARACTER_GENERATION_STARTED_EVENT = 'character-generation-started';

export interface CharacterGenerationJob {
  id: string;
  character_name: string;
  status: 'queued' | 'preparing' | 'generating' | 'publishing' | 'succeeded' | 'failed';
  completed: number;
  total: number;
  current_emotion?: string | null;
  error?: string | null;
  character_filename?: string | null;
}

const activeJobKey = (account: string): string => (
  `characterGenerationJob:${encodeURIComponent(account)}`
);

async function readJson(response: Response): Promise<Record<string, unknown>> {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch (_error) {
    return {};
  }
}

export function getActiveCharacterGenerationJob(account: string): string {
  try {
    return localStorage.getItem(activeJobKey(account)) || '';
  } catch (_error) {
    return '';
  }
}

export function rememberActiveCharacterGenerationJob(account: string, jobId: string): void {
  localStorage.setItem(activeJobKey(account), jobId);
}

export function clearActiveCharacterGenerationJob(account: string): void {
  localStorage.removeItem(activeJobKey(account));
}

export async function createCharacterGenerationJob(options: {
  account: string;
  characterName: string;
  replicateToken: string;
  referenceImage: File;
  avatarImage: File;
}): Promise<CharacterGenerationJob> {
  const sessionToken = getAccountSessionToken();
  if (!sessionToken) throw new Error('登录已失效');
  const formData = new FormData();
  formData.set('account', options.account);
  formData.set('sessionToken', sessionToken);
  formData.set('character_name', options.characterName);
  formData.set('replicate_token', options.replicateToken);
  formData.set('reference_image', options.referenceImage);
  formData.set('avatar_image', options.avatarImage);
  const response = await fetch(`${getCurrentBaseUrl()}/api/characters/generation-jobs`, {
    method: 'POST',
    body: formData,
  });
  const payload = await readJson(response);
  if (!response.ok) {
    throw new Error(typeof payload.error === 'string' ? payload.error : '无法启动角色生成');
  }
  return payload as unknown as CharacterGenerationJob;
}

export async function fetchCharacterGenerationJob(
  account: string,
  jobId: string,
): Promise<CharacterGenerationJob> {
  const sessionToken = getAccountSessionToken();
  if (!sessionToken) throw new Error('登录已失效');
  const response = await fetch(
    `${getCurrentBaseUrl()}/api/characters/generation-jobs/${encodeURIComponent(jobId)}/status`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account, sessionToken }),
    },
  );
  const payload = await readJson(response);
  if (!response.ok) {
    const error = new Error(
      typeof payload.error === 'string' ? payload.error : '无法读取角色生成进度',
    ) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return payload as unknown as CharacterGenerationJob;
}

