"""
Plugin system for Reel v2
"""
from typing import Dict, Optional, List, Any
from .base import BasePlugin
from .registry import PluginRegistry, get_registry, register_plugin, get_plugin, list_plugins

__all__ = [
    'BasePlugin',
    'PluginRegistry',
    'get_registry',
    'register_plugin',
    'get_plugin',
    'list_plugins'
]

