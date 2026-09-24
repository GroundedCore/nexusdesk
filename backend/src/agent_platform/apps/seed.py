"""Create an optional sample workspace after migrations; safe to run again."""

import asyncio

from agent_platform.modules.agent_config.service import AgentConfig, AgentDraft
from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.modules.knowledge.service import DocumentInput
from agent_platform.modules.model_gateway.contracts import Connection, Model, Profile
from agent_platform.platform.persistence.store import one
from agent_platform.settings import Settings


async def main():
    settings = Settings()
    tenant, actor = settings.tenant_id, "seed"
    async with runtime_services(settings) as runtime:
        p = runtime.platform
        # A separate session lock serializes repeated seed commands, including their commits.
        async with p.engine.connect() as lock:
            from sqlalchemy import text

            await lock.execute(text("SELECT pg_advisory_lock(71432020)"))
            try:
                bases = await p.knowledge.bases(tenant)
                base = next((b for b in bases if b["name"] == "示例服务知识库"), None)
                if not base:
                    base = await p.knowledge.create_base(tenant, actor, "示例服务知识库")
                if not await p.knowledge.documents(tenant, base["id"]):
                    await p.knowledge.save_document(
                        tenant,
                        actor,
                        base["id"],
                        DocumentInput(
                            title="配送与售后说明（演示）",
                            content="演示商店通常在付款后 48 小时内发货。配送问题请提供订单编号。"
                            "需要进一步处理时可以拟定工单，经过客服确认后创建。也可以请求转人工服务。",
                        ),
                    )
                agents = await p.agents.list(tenant)
                agent = next((a for a in agents if a["name"] == "示例客服 Agent"), None)
                if not agent:
                    catalog = p.gateway.catalog

                    async def ensure(kind, body):
                        rows = await catalog.list(tenant, kind)
                        existing = next((r for r in rows if r["name"] == body.name), None)
                        return existing or await catalog.save(tenant, actor, kind, body)

                    connection = await ensure(
                        "connections", Connection(name="示例模拟连接", protocol="demo")
                    )
                    model = await ensure(
                        "models",
                        Model(
                            name="示例模拟 Chat",
                            connection_id=connection["id"],
                            model_name="demo",
                            operations=["chat"],
                            tool_calling=True,
                        ),
                    )
                    profile = await ensure(
                        "profiles",
                        Profile(name="示例客服模型方案", operation="chat", model_id=model["id"]),
                    )
                    version = profile["published_version"]
                    if not version:
                        published = await catalog.publish(
                            tenant, actor, profile["id"], profile["revision"]
                        )
                        version = published["version"]
                    agent = await p.agents.create(
                        tenant,
                        actor,
                        AgentDraft(
                            name="示例客服 Agent",
                            description="知识问答、工单确认及人工协作演示",
                            is_example=True,
                            config=AgentConfig(
                                model_profile_id=profile["id"],
                                model_profile_version=version,
                                system_prompt="使用知识检索回答问题并说明来源。工单须等待确认。",
                                tool_names=["knowledge_search", "propose_ticket"],
                                knowledge_base_ids=[base["id"]],
                            ),
                        ),
                    )
                    await p.agents.publish(tenant, actor, agent["id"], agent["draft_revision"])
                async with p.engine.connect() as c:
                    exists = await one(
                        c,
                        "SELECT id FROM runtime_conversations WHERE tenant_id=:t AND external_id='sample-support'",
                        t=tenant,
                    )
                if not exists:
                    await p.conversations.create(tenant, actor, "sample-support", agent["id"])
                print(f"示例数据就绪，租户：{tenant}；请打开工作台的 sample-support 会话。")
            finally:
                await lock.execute(text("SELECT pg_advisory_unlock(71432020)"))


if __name__ == "__main__":
    asyncio.run(main())
