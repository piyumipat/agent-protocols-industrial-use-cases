"""UC-003 Protocol Document used by the Agora adapter."""

from agent_protocols.agora import ProtocolDocument

TRANSPORT_PROTOCOL_SOURCE = """name: uc003-transport
description: UC-003 transport bidding and award exchange
multiround: true
---
# UC-003 transport exchange

The body is a JSON object. A `request_bid` operation contains the shared
TransportRequest fields and returns a `bid` or `no_bid` object. An `award`
operation contains the task ID and Executor ID and returns a `delivery_result`.
The same conversation may carry the bid and award for one task.
"""

TRANSPORT_PROTOCOL = ProtocolDocument.parse(TRANSPORT_PROTOCOL_SOURCE)
