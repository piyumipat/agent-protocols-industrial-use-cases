"""A2A Executor Agent server assembly for UC-003."""

from __future__ import annotations

from typing import Any

from agent_protocols.a2a import (
    AgentCard,
    AgentExecutor,
    EventQueue,
    MessageIdempotencyOptions,
    RequestContext,
    Task,
    TaskState,
    TaskUpdater,
    create_agent_card,
    create_server,
    create_skill,
    new_data_message,
    sign_agent_card,
)
from starlette.applications import Starlette

from agent_protocols_industrial_use_cases.domain.executor import ExecutorService

from .payloads import (
    decode_award,
    decode_bid_request,
    encode_bid_response,
    encode_delivery,
    message_payload,
)

SKILL_ID = "transport-bidding"


def create_executor_agent_card(executor_id: str, rpc_url: str) -> AgentCard:
    return create_agent_card(
        name=executor_id,
        description="UC-003 transport Executor Agent.",
        url=rpc_url,
        skills=[
            create_skill(
                SKILL_ID,
                "Transport bidding and delivery",
                "Returns transport bids and executes awarded deliveries.",
                tags=["transport", "bidding", "delivery"],
                input_modes=["application/json"],
                output_modes=["application/json"],
            )
        ],
        streaming=True,
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
    )


class UC003A2AExecutor(AgentExecutor):
    def __init__(self, service: ExecutorService) -> None:
        self._service = service

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None:
            raise ValueError("A2A request contains no Message")
        payload = message_payload(context.message)
        operation = payload.get("operation")

        if operation == "request_bid":
            response = await self._service.assess_and_bid(decode_bid_request(payload))
            await event_queue.enqueue_event(new_data_message(encode_bid_response(response)))
            return

        if operation == "award":
            if context.task_id is None or context.context_id is None:
                raise ValueError("A2A award contains no protocol task context")
            award = decode_award(payload)
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id,
                    context_id=context.context_id,
                    status={"state": TaskState.TASK_STATE_SUBMITTED},
                    history=[context.message],
                )
            )
            updater = TaskUpdater(event_queue, context.task_id, context.context_id)
            await updater.start_work()
            result = await self._service.accept_award(award)
            await updater.complete(
                new_data_message(
                    encode_delivery(result),
                    context_id=context.context_id,
                    task_id=context.task_id,
                )
            )
            return

        raise ValueError(f"unsupported UC-003 operation: {operation!r}")

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.task_id is None or context.context_id is None:
            return
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def create_executor_app(
    service: ExecutorService,
    *,
    rpc_url: str,
    rpc_path: str = "/a2a",
    card_signing_key: Any | None = None,
    card_signing_kid: str | None = None,
    message_idempotency: MessageIdempotencyOptions | None = None,
) -> Starlette:
    if (card_signing_key is None) != (card_signing_kid is None):
        raise ValueError("card_signing_key and card_signing_kid must be provided together")
    card = create_executor_agent_card(service.executor_id, rpc_url)
    if card_signing_key is not None and card_signing_kid is not None:
        card = sign_agent_card(card, card_signing_key, kid=card_signing_kid)
    return create_server(
        card,
        UC003A2AExecutor(service),
        rpc_path=rpc_path,
        message_idempotency=message_idempotency,
    )
