import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const spec = JSON.parse(await readFile(new URL("../openapi/paperpilot.json", import.meta.url), "utf8"));

const jsonSchema = (operation, status = "200") =>
  operation?.responses?.[status]?.content?.["application/json"]?.schema;
const requestSchema = operation => operation?.requestBody?.content?.["application/json"]?.schema;

test("research questions, source sets, and runs remain represented in OpenAPI", () => {
  const questions = spec.paths["/api/research/questions"];
  assert.equal(jsonSchema(questions.get).items.$ref, "#/components/schemas/ResearchQuestion");
  assert.equal(requestSchema(questions.post).$ref, "#/components/schemas/ResearchQuestionCreate");
  assert.equal(
    requestSchema(spec.paths["/api/research/questions/{question_id}"].patch).$ref,
    "#/components/schemas/ResearchQuestionUpdate",
  );

  const sourceSets = spec.paths["/api/source-sets"];
  assert.equal(jsonSchema(sourceSets.get).items.$ref, "#/components/schemas/SourceSet");
  assert.equal(requestSchema(sourceSets.post).$ref, "#/components/schemas/SourceSetCreate");
  assert.equal(
    requestSchema(spec.paths["/api/source-sets/{source_set_id}"].patch).$ref,
    "#/components/schemas/SourceSetUpdate",
  );

  const runs = spec.paths["/api/research/runs"];
  assert.equal(jsonSchema(runs.get).items.$ref, "#/components/schemas/ResearchRun");
  assert.equal(requestSchema(runs.post).$ref, "#/components/schemas/ResearchRunCreate");
  assert.equal(
    requestSchema(spec.paths["/api/research/runs/{run_id}/artifacts"].post).$ref,
    "#/components/schemas/RunArtifactCreate",
  );
  assert.equal(
    jsonSchema(spec.paths["/api/research/runs/{run_id}/cancel"].post).$ref,
    "#/components/schemas/ResearchRun",
  );
});

test("library paging and bulk curation operations remain typed", () => {
  const library = spec.paths["/api/library/papers"].get;
  assert.equal(jsonSchema(library).$ref, "#/components/schemas/PaperLibraryPage");
  assert.deepEqual(
    library.parameters.map(parameter => parameter.name),
    ["page", "page_size", "query", "status", "source", "tag_id", "source_set_id", "decision"],
  );
  assert.equal(
    requestSchema(spec.paths["/api/papers/{paper_id}/decision"].put).$ref,
    "#/components/schemas/PaperDecisionUpdate",
  );
  assert.equal(
    requestSchema(spec.paths["/api/papers/bulk/tags"].post).$ref,
    "#/components/schemas/PaperTagsBulkUpdate",
  );
});

test("idea, hypothesis, belief, and experiment workflow remains typed", () => {
  const ideas = spec.paths["/api/ideas"];
  assert.equal(jsonSchema(ideas.get).items.$ref, "#/components/schemas/Idea");
  assert.equal(requestSchema(ideas.post).$ref, "#/components/schemas/IdeaCreate");
  assert.equal(
    requestSchema(spec.paths["/api/ideas/{idea_id}"].patch).$ref,
    "#/components/schemas/IdeaUpdate",
  );
  assert.equal(
    jsonSchema(spec.paths["/api/ideas/{idea_id}/promote"].post).$ref,
    "#/components/schemas/Idea",
  );

  const hypotheses = spec.paths["/api/hypotheses"];
  assert.equal(jsonSchema(hypotheses.get).items.$ref, "#/components/schemas/HypothesisCard");
  assert.equal(requestSchema(hypotheses.post).$ref, "#/components/schemas/HypothesisCardCreate");
  assert.equal(
    requestSchema(spec.paths["/api/hypotheses/{card_id}/status"].patch).$ref,
    "#/components/schemas/HypothesisCardStatusUpdate",
  );

  const beliefs = spec.paths["/api/beliefs"];
  assert.equal(jsonSchema(beliefs.get).items.$ref, "#/components/schemas/BeliefEvent");
  assert.equal(requestSchema(beliefs.post).$ref, "#/components/schemas/BeliefEventCreate");

  const experiments = spec.paths["/api/experiments"];
  assert.equal(jsonSchema(experiments.get).items.$ref, "#/components/schemas/ExperimentPlan");
  assert.equal(
    requestSchema(experiments.post).$ref,
    "#/components/schemas/ExperimentPlanCreate",
  );
  assert.equal(
    requestSchema(spec.paths["/api/experiments/{plan_id}/results"].post).$ref,
    "#/components/schemas/ExperimentResultCreate",
  );
  assert.equal(
    jsonSchema(spec.paths["/api/experiments/{plan_id}/snapshot"].get).$ref,
    "#/components/schemas/ExperimentPlanSnapshot",
  );
  assert.deepEqual(spec.components.schemas.ExperimentPlanSnapshot.properties.schema_version.const, "paperpilot.experiment-plan.v1");
});

test("discovery review and operational readiness remain typed", () => {
  const discovery = spec.paths["/api/discovery/items"].post;
  assert.equal(requestSchema(discovery).$ref, "#/components/schemas/DiscoveryItemCreate");
  assert.equal(jsonSchema(discovery, "201").$ref, "#/components/schemas/DiscoveryItem");
  assert.equal(
    requestSchema(spec.paths["/api/discovery/items/{item_id}/review"].patch).$ref,
    "#/components/schemas/DiscoveryReviewUpdate",
  );
  assert.equal(
    jsonSchema(spec.paths["/api/discovery/review-queue"].get).items.$ref,
    "#/components/schemas/DiscoveryItem",
  );
  assert.equal(
    jsonSchema(spec.paths["/api/operations/status"].get).$ref,
    "#/components/schemas/OperationsStatus",
  );
});

test("citation provenance distinguishes graph contradictions without weakening paper locators", () => {
  const citation = spec.components.schemas.Citation;
  assert.deepEqual(citation.properties.source_kind.enum, ["paper_chunk", "graph_node", "graph_edge"]);
  assert.deepEqual(citation.properties.evidence_role.anyOf[0].enum, ["supports", "contradicts", "context", "mentions"]);
  assert.deepEqual(citation.properties.retrieval_stance.anyOf[0].enum, ["positive", "negative", "neutral"]);
  assert.equal(citation.properties.source_version_id.anyOf[0].type, "string");
  assert.equal(citation.properties.source_span_id.anyOf[0].type, "string");
  assert.equal(citation.properties.knowledge_node_id.anyOf[0].type, "string");
  assert.equal(citation.properties.knowledge_edge_id.anyOf[0].type, "string");
  assert.equal(citation.properties.graph_path.items.type, "object");
  assert.equal(citation.properties.retrieval_channels.items.type, "string");
  assert.equal(citation.properties.source_quote.anyOf[0].type, "string");
  for (const field of ["paper_id", "paper_title", "chunk_id", "page", "section", "excerpt"]) {
    assert.ok(citation.required.includes(field), `${field} must remain required for EvidenceViewer safety`);
  }
});

test("search requests keep bounded paper selection and query limits", () => {
  const search = spec.components.schemas.SearchRequest;
  assert.equal(search.properties.paper_ids.type, "array");
  assert.equal(search.properties.paper_ids.items.type, "string");
  assert.equal(search.properties.paper_ids.maxItems, 500);
  assert.equal(search.properties.query.maxLength, 4000);
  assert.equal(search.properties.limit.maximum, 20);
});

test("Idea anchors and Experiment hypothesis snapshots retain their integrity constraints", () => {
  const ideaCreate = spec.components.schemas.IdeaCreate;
  const ideaUpdate = spec.components.schemas.IdeaUpdate;
  const experiment = spec.components.schemas.ExperimentPlan;
  assert.equal(ideaCreate.properties.claim_id.anyOf[0].maxLength, 128);
  assert.equal(ideaUpdate.properties.claim_id.anyOf[0].maxLength, 128);
  assert.equal(ideaUpdate.properties.content.type, "string");
  assert.equal(ideaUpdate.properties.content.anyOf, undefined);
  assert.deepEqual(ideaUpdate.properties.kind.enum, ["observation", "interpretation", "hypothesis", "falsifier", "todo"]);
  assert.equal(ideaUpdate.properties.kind.anyOf, undefined);
  assert.equal(experiment.properties.hypothesis_snapshot.anyOf[0].type, "object");
  assert.equal(experiment.properties.hypothesis_snapshot.anyOf[0].additionalProperties, true);
  assert.equal(experiment.properties.hypothesis_snapshot.anyOf[1].type, "null");
});

test("collaborative review endpoints keep anchors, discussion, assignment, and decisions typed", () => {
  const reviews = spec.paths["/api/reviews"];
  assert.equal(jsonSchema(reviews.get).items.$ref, "#/components/schemas/ReviewThread");
  assert.equal(requestSchema(reviews.post).$ref, "#/components/schemas/ReviewThreadCreate");
  assert.equal(
    jsonSchema(spec.paths["/api/reviews/{thread_id}"].get).$ref,
    "#/components/schemas/ReviewThread",
  );
  assert.equal(
    requestSchema(spec.paths["/api/reviews/{thread_id}/assignment"].patch).$ref,
    "#/components/schemas/ReviewAssignmentUpdate",
  );
  assert.equal(
    requestSchema(spec.paths["/api/reviews/{thread_id}/comments"].post).$ref,
    "#/components/schemas/ReviewCommentCreate",
  );
  assert.equal(
    requestSchema(spec.paths["/api/reviews/{thread_id}/decisions"].post).$ref,
    "#/components/schemas/ReviewDecisionCreate",
  );
  assert.ok(spec.paths["/api/reviews/report.md"].get);

  const create = spec.components.schemas.ReviewThreadCreate;
  const thread = spec.components.schemas.ReviewThread;
  assert.equal(create.properties.claim_id.anyOf[0].maxLength, 128);
  for (const field of ["research_run_id", "claim_id", "evidence_link_id", "assigned_to"]) {
    assert.ok(create.properties[field], `${field} must remain available to the review creation UI`);
  }
  assert.deepEqual(
    spec.components.schemas.ReviewDecisionCreate.properties.verdict.enum,
    ["accepted", "rejected", "changes_requested", "needs_more_evidence"],
  );
  assert.equal(thread.properties.claim_snapshot.anyOf[0].type, "object");
  assert.equal(thread.properties.claim_snapshot.anyOf[0].additionalProperties, true);
  assert.equal(thread.properties.claim_artifact_id.anyOf[0].type, "string");
});
