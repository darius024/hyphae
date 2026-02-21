import re
import copy

from tools import LOCAL_ONLY_TOOLS


SENSITIVE_PATTERNS = [
    re.compile(r'[\\/][\w\-.]+\.\w{1,5}\b'),           # file paths
    re.compile(r'\b\d+\.\d+\s*(mg|ml|mM|uM|°C|K|Pa|V|A|Hz|nm|um|mm)\b', re.IGNORECASE),  # measurements
    re.compile(r'\b(sample|batch|lot|specimen)\s*[#-]?\s*\w+', re.IGNORECASE),  # sample IDs
    re.compile(r'\b[A-Z]{2,}-\d{3,}\b'),                # lab codes like AB-1234
]


def sanitise_for_cloud(messages):
    """Strip sensitive experimental data from messages before sending to cloud.

    Keeps abstract intent intact while removing file paths, raw measurements,
    sample identifiers, and lab codes.
    """
    sanitised = []
    for msg in messages:
        clean = copy.deepcopy(msg)
        if "content" in clean and isinstance(clean["content"], str):
            text = clean["content"]
            for pattern in SENSITIVE_PATTERNS:
                text = pattern.sub("[REDACTED]", text)
            clean["content"] = text
        sanitised.append(clean)
    return sanitised


def is_cloud_safe(tool_name):
    """Return True if a tool's data can safely be sent to or processed by cloud."""
    return tool_name not in LOCAL_ONLY_TOOLS


def filter_tools_for_cloud(tools):
    """Return only tools that are safe for cloud execution."""
    return [t for t in tools if is_cloud_safe(t["name"])]


def filter_tools_for_local(tools):
    """Return only tools that should run on-device."""
    return [t for t in tools if t["name"] in LOCAL_ONLY_TOOLS]
