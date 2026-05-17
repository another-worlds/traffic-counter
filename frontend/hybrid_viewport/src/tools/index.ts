export type { Tool, ToolContext } from './types';
export { LineTool, getCursor } from './LineTool';

import { LineTool } from './LineTool';
import type { Tool } from './types';

export const BUILTIN_TOOLS: Tool[] = [LineTool];

export function getTool(id: string): Tool | undefined {
  return BUILTIN_TOOLS.find((t) => t.id === id);
}
