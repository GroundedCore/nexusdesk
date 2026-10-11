import json
from collections import OrderedDict

from agent_platform.integrations.business_tools.platform_tools import BUILTIN_SCHEMAS, PlatformTools
from agent_platform.modules.agent_config.service import AgentService
from agent_platform.modules.agent_runtime.engine import RuntimeEngine
from agent_platform.modules.agent_runtime.schemas import RuntimeFault
from agent_platform.modules.channel.service import ChannelService
from agent_platform.modules.conversation.service import ConversationService
from agent_platform.modules.customer_service.service import CustomerService
from agent_platform.modules.evaluation.service import MockTools
from agent_platform.modules.human_handoff.service import HandoffService
from agent_platform.modules.knowledge.ingestion import IngestionService
from agent_platform.modules.knowledge.retrieval import Retrieval
from agent_platform.modules.knowledge.service import KnowledgeService
from agent_platform.modules.knowledge.vector_store import VectorIndex
from agent_platform.modules.knowledge.workspace import Workspace
from agent_platform.modules.model_gateway.gateway import GatewayChatModel, ModelGateway
from agent_platform.modules.model_gateway.service import build_model
from agent_platform.modules.observability.service import ObservabilityService
from agent_platform.modules.open_platform.service import OpenPlatform
from agent_platform.modules.policy.service import ExecutionPolicy
from agent_platform.modules.tool_gateway.registry import ToolRegistry
from agent_platform.modules.tool_gateway.service import HttpTool, ToolGateway
from agent_platform.modules.tool_gateway.workspace import ToolWorkspace
from agent_platform.platform.identity.staff import StaffDirectory
from agent_platform.platform.persistence.store import DomainError, execute, one


class PlatformServices:
    def __init__(self, engine, repository, settings, snapshot, file_specs, model_client):
        self.engine, self.repository, self.settings, self.snapshot = (
            engine,
            repository,
            settings,
            snapshot,
        )
        self.tools = ToolRegistry(engine, settings, file_specs)
        self.tool_workspace = ToolWorkspace(self.tools, model_client)
        self.tools.workspace = self.tool_workspace
        self.staff = StaffDirectory(engine)
        self.policy = ExecutionPolicy(self.tools)
        self.gateway = ModelGateway(engine, settings, model_client)
        self.agents = AgentService(engine, self.tools, self.gateway.catalog)
        self.knowledge = KnowledgeService(engine)
        self.ingestion = IngestionService(engine, self.knowledge, settings, model_client)
        self.vector_index = VectorIndex(engine, self.gateway, settings, model_client)
        self.knowledge_workspace = Workspace(
            engine, settings, model_client, self.vector_index, self.gateway
        )
        self.retrieval = Retrieval(engine, self.knowledge, self.vector_index, self.gateway)
        self.conversations = ConversationService(engine, settings.history_turns)
        self.handoffs = HandoffService(engine, repository)
        self.customer = CustomerService(
            engine, repository, self.agents, self.handoffs, settings, snapshot
        )
        self.channels = ChannelService(engine, self.conversations, self.customer, self.agents)
        self.observability = ObservabilityService(engine)
        self.open_platform = OpenPlatform(self)
        self.evaluation = None

    async def legacy_submit(self, tenant, body):
        async with self.engine.begin() as c:
            await execute(c, "SELECT pg_advisory_xact_lock(71432019)")
            conv = await one(
                c,
                "SELECT * FROM runtime_conversations WHERE tenant_id=:t AND external_id=:key",
                t=tenant,
                key=body.conversation_id,
            )
            snapshot = dict(self.snapshot)
            if conv and conv["agent_id"]:
                snapshot["agent"] = await self.agents.snapshot(tenant, conv["agent_id"], c)
            elif self.settings.model_backend == "unconfigured":
                raise DomainError("model_not_configured", 409)
            return await self.repository.submit(
                tenant,
                body,
                snapshot,
                self.settings.queue_capacity,
                self.settings.tenant_capacity,
                self.settings.queue_timeout_seconds,
                connection=c,
            )


class RuntimeFactory:
    def __init__(self, legacy, platform, client, settings, shared_limit):
        self.legacy, self.platform, self.client, self.settings = legacy, platform, client, settings
        self.limit = shared_limit
        self.models = OrderedDict()

    def model(self, schemas):
        key = json.dumps(schemas, sort_keys=True)
        if key not in self.models:
            self.models[key] = build_model(self.settings, schemas)
            if len(self.models) > 32:
                self.models.popitem(last=False)
        return self.models[key]

    def effective(self, snapshot):
        languages = {
            "auto": "Match the language of the user's latest message.",
            "zh-CN": "Reply in Simplified Chinese.",
            "zh-TW": "Reply in Traditional Chinese.",
            "en": "Reply in English.",
            "hi": "Reply in Hindi using the Devanagari script.",
        }
        language_rule = languages[snapshot["config"].get("reply_language", "auto")]
        return self.settings.model_copy(
            update={
                "system_prompt": self.settings.system_prompt
                + "\n"
                + snapshot["config"]["system_prompt"]
                + "\nResponse language policy (takes precedence over other language preferences in this prompt): "
                + language_rule
                + " Preserve proper names, identifiers and quoted source text when appropriate.",
                "max_model_rounds": self.platform.policy.check_rounds(
                    snapshot["config"]["max_model_rounds"], self.settings
                ),
                # Both switches must agree before the engine injects a summary.
                "summary_enabled": self.settings.summary_enabled
                and snapshot["config"].get("summary_enabled", True),
            }
        )

    async def for_run(self, row, emit):
        snapshot = row["config"].get("agent")
        if not snapshot:
            return self.legacy
        http = ToolGateway(
            self.client,
            [HttpTool.model_validate(s["spec"]) for s in snapshot["tools"]],
            credential_resolver=lambda spec: self.platform.tools.credential_headers(
                row["tenant_id"], spec
            ),
        )
        http.limit = self.limit
        tools = PlatformTools(
            http,
            self.platform,
            row,
            snapshot["config"]["tool_names"],
            snapshot["config"]["knowledge_base_ids"],
            emit,
        )
        return RuntimeEngine(
            self.bound_model(snapshot, tools.schemas, row["id"]), tools, self.effective(snapshot)
        )

    def bound_model(self, snapshot, schemas, run_id=None):
        config = snapshot["config"]
        if config.get("model_profile_id"):
            return GatewayChatModel(
                self.platform.gateway,
                self.settings.tenant_id,
                config["model_profile_id"],
                config["model_profile_version"],
                schemas,
                run_id,
            )
        raise RuntimeFault("agent_model_required")

    def evaluation(self, snapshot, responses):
        schemas = [HttpTool.model_validate(s["spec"]).model_schema() for s in snapshot["tools"]]
        schemas += [
            schema
            for name, schema in BUILTIN_SCHEMAS.items()
            if name in snapshot["config"]["tool_names"]
        ]
        mocks = MockTools(schemas, responses)
        return RuntimeEngine(
            self.bound_model(snapshot, schemas), mocks, self.effective(snapshot)
        ), mocks
