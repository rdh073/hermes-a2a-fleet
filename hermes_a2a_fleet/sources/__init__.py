"""Discovery sources — each a DiscoverySource that PROPOSES candidate peers."""

from .base import DiscoverySource
from .mdns_source import MdnsSource
from .static_source import StaticSource
from .tailnet_source import TailnetSource

__all__ = ["DiscoverySource", "StaticSource", "MdnsSource", "TailnetSource"]
