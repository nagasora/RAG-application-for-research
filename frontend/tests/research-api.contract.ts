import {
  getResearchMemoryPage, getResearchMessagesPage,
  type ResearchMemoryEvent, type ResearchMemoryPage, type ResearchMessagePage,
} from "@/lib/api/client";

// Compile-time contract: generated OpenAPI types must keep the wrappers and
// pagination/filter inputs aligned. This function is intentionally not run.
async function researchPaginationContract(signal: AbortSignal) {
  const messages: ResearchMessagePage = await getResearchMessagesPage(
    "conversation-id", { limit:50, beforeOrdinal:120 }, signal,
  );
  const memory: ResearchMemoryPage = await getResearchMemoryPage(
    "conversation-id", { kind:"hypothesis", limit:25, beforeOrdinal:80 }, signal,
  );
  const event: ResearchMemoryEvent | undefined = memory.items?.[0];
  const nextMessageOrdinal: number | null | undefined = messages.next_before_ordinal;
  const nextMemoryOrdinal: number | null | undefined = memory.next_before_ordinal;
  return { event, nextMessageOrdinal, nextMemoryOrdinal };
}

void researchPaginationContract;
