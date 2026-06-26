"""The DiscoverySource port — what every discovery mechanism implements."""

from __future__ import annotations

import abc

from ..types import AgentRef


class DiscoverySource(abc.ABC):
    """A pluggable way to find candidate A2A peers.

    A source PROPOSES candidates; the Registry VERIFIES them (card fetch). So a
    source may return unreachable/over-broad candidates cheaply — it does not
    need to confirm reachability itself.
    """

    #: stable identifier shown in `a2a_fleet_list` / logs
    name: str = "source"

    @abc.abstractmethod
    def discover(self) -> list[AgentRef]:
        """Return candidate peers. Must not raise for the common 'nothing
        found' / 'dependency absent' cases — return ``[]`` and degrade."""
        raise NotImplementedError
