import { apiClientV2, getApiErrorStatus } from '@/api/client';
import type { ObjectSetExpression } from './contract';
import type { ObjectPanelRecord } from './ObjectPanel';
import type { ExplorerContext } from './ExplorerOperations';
type Load = { objects: ObjectPanelRecord[]; completeness: string; page: { has_more: boolean; next_page_token?: string } };

export function explorerError(error: unknown): string {
  const status = getApiErrorStatus(error);
  const labels: Record<number, string> = { 403: '没有操作权限', 404: '资源不存在或不可访问', 409: '资源已变化，请刷新后重试', 410: '视图或游标已过期，请重新固定视图', 503: '数据服务暂不可用', 504: '查询超时，请缩小范围' };
  if (status && labels[status]) return labels[status];
  if (typeof error === 'object' && error && 'detail' in error) {
    const detail = error.detail;
    if (typeof detail === 'string') return detail;
    if (typeof detail === 'object' && detail && 'message' in detail) return String(detail.message);
  }
  return '操作失败，请重试';
}

export async function loadComplete(ontologyId: string, expression: ObjectSetExpression, context: ExplorerContext, signal?: AbortSignal): Promise<ObjectPanelRecord[]> {
  if (context.consistency !== 'snapshot' || !context.data_view_id) throw new Error('A pinned view is required');
  const objects: ObjectPanelRecord[] = [];
  let pageToken: string | undefined;
  const seen = new Set<string>();
  for (let page = 0; page < 50; page++) {
    const result = await apiClientV2.post<Load>(`/ontologies/${ontologyId}/object-query/load`, {
      expression, context, read: { page_size: 200, page_token: pageToken },
    }, { signal });
    if (result.completeness === 'incomplete' || (!result.page.has_more && result.completeness !== 'complete')) throw new Error('Incomplete result cannot be saved or exported');
    objects.push(...result.objects);
    if (!result.page.has_more) return objects;
    const token = result.page.next_page_token;
    if (!token || seen.has(token)) throw new Error('Invalid pagination response');
    seen.add(token); pageToken = token;
  }
  throw new Error('Result exceeds 10,000 objects; narrow the filter');
}
