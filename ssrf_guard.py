"""
SSRF Protection and URL Destination Validator
Complies with Canopii Trust Index & OWASP SSRF Guardrails.
"""

import ipaddress
import socket
from urllib.parse import urlparse

# Private, Loopback, Link-Local, and Cloud Metadata CIDRs to reject
BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),          # Loopback IPv4
    ipaddress.ip_network("10.0.0.0/8"),           # Private Class A
    ipaddress.ip_network("172.16.0.0/12"),        # Private Class B
    ipaddress.ip_network("192.168.0.0/16"),       # Private Class C
    ipaddress.ip_network("169.254.0.0/16"),       # Link-local / AWS / Cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),            # Current network
    ipaddress.ip_network("100.64.0.0/10"),        # Shared Address Space (Carrier-grade NAT)
    ipaddress.ip_network("192.0.0.0/24"),         # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),         # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),      # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),       # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),          # Multicast
    ipaddress.ip_network("240.0.0.0/4"),          # Reserved
    ipaddress.ip_network("::1/128"),              # Loopback IPv6
    ipaddress.ip_network("fc00::/7"),             # Unique Local IPv6
    ipaddress.ip_network("fe80::/10"),            # Link-local IPv6
]

BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "instance-data",
    "metadata",
    "169.254.169.254",
}

def is_safe_url(url: str) -> tuple[bool, str]:
    """
    Validate that a destination URL is safe to request.
    Rejects loopback, private RFC1918, link-local metadata, and non-HTTP protocols.
    Returns (is_safe: bool, reason: str).
    """
    if not url or not isinstance(url, str):
        return False, "URL must be a non-empty string"
    
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Malformed URL: {e}"

    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"Unsupported URL scheme '{parsed.scheme}'. Only http/https are allowed."

    hostname = parsed.hostname
    if not hostname:
        return False, "Missing hostname in URL"

    hostname_lower = hostname.lower()
    if hostname_lower in BLOCKED_HOSTNAMES:
        return False, f"Access to restricted host '{hostname}' is blocked"

    # Resolve IP address to check if it points to internal/private space
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # Cannot resolve hostname: let upstream HTTP client handle standard DNS failure
        return True, "OK"
    except Exception as e:
        return False, f"DNS resolution failed: {e}"

    for res in addr_info:
        ip_str = res[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
            for blocked_net in BLOCKED_NETWORKS:
                if ip in blocked_net:
                    return False, f"Destination resolves to forbidden private/internal IP {ip_str}"
        except ValueError:
            continue

    return True, "OK"
