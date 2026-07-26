# Plan: chat compare + UI navigate

## Files

| File | Change |
|------|--------|
| `backend/services/chat_tools.py` | `compare_scenarios`; enrich `ui_open_panel` |
| `backend/services/chat_tools_schema.py` | schemas + DISPATCHERS map |
| `backend/services/chat_service.py` | emit `ui_event` SSE when `_ui_event` |
| `frontend/src/store/uiStore.ts` | `resultsTabRequest`, `compareNavRequest` |
| `frontend/src/components/ChatPanel.tsx` | handle `ui_event` |
| `frontend/src/pages/Results.tsx` | apply results tab request |
| `frontend/src/pages/CompareView.tsx` | apply compare nav request |
| `frontend/src/layout/Sidebar.tsx` | import-export / panel mapping helpers if needed |
| `backend/tests/test_chat_compare_navigate.py` | unit tests |

## Tasks

1. Backend compare tool + tests  
2. Backend ui_event SSE + enriched ui_open_panel  
3. Frontend store + ChatPanel + Results/Compare/Bottom listeners  
4. Commit / push / PR
