"""
URL Obfuscator plugin - obfuscates IP addresses and URLs using various encoding techniques
"""
from typing import Dict, Any, Optional, List, Tuple
from ..base import BasePlugin
from shared.workflow_variables import interpolate_string
import logging
import urllib.parse
import ipaddress
import socket

logger = logging.getLogger(__name__)


class UrlObfuscatorPlugin(BasePlugin):
    """
    Plugin for obfuscating IP addresses and URLs using various encoding techniques.
    
    This plugin applies IP obfuscation methods to bypass URL filters, security scanners,
    and IP-based restrictions. Supports 18 different obfuscation techniques including
    DWORD formats, dotted notation variants, IPv6-mapped addresses, fake authentication
    tricks, and overflow techniques.
    
    Use cases:
    - Bypass IP-based URL filters in security tools
    - Obfuscate campaign URLs to avoid detection
    - Test URL parser implementations
    - Penetration testing and security research
    
    Example: Obfuscate http://192.168.1.100/login to http://3232235876/login
    (decimal DWORD format) or http://secure.bank.com@3232235876/login (fake auth).
    """
    
    @property
    def plugin_type(self) -> str:
        return "url_obfuscator"
    
    @property
    def display_name(self) -> str:
        return "URL Obfuscator"
    
    @property
    def description(self) -> str:
        return "Obfuscate IP addresses and URLs using 18 encoding techniques to bypass filters and security scanners. Supports DWORD formats, dotted notation, IPv6-mapped addresses, fake auth tricks, and overflow techniques. Use for penetration testing, security research, and bypassing IP-based restrictions."
    
    @property
    def plugin_category(self) -> str:
        return "data_transform"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url_source": {
                    "type": "string",
                    "title": "URL Source",
                    "enum": [
                        {
                            "value": "variable",
                            "label": "From Context Variable",
                            "description": "Read URL from a context variable (e.g., from previous plugin or workflow variable)"
                        },
                        {
                            "value": "direct",
                            "label": "Direct Input",
                            "description": "Enter URL directly, supports variable interpolation with {{variable}} syntax"
                        }
                    ],
                    "default": "variable",
                    "description": "Where to get the URL to obfuscate",
                    "help": "Choose whether to read the URL from a context variable (like 'url' or 'target.landing_page') or enter it directly. Direct input supports variable interpolation: http://{{target.ip}}/login"
                },
                "url_variable": {
                    "type": "string",
                    "title": "URL Variable Name",
                    "description": "Context variable name containing the URL to obfuscate",
                    "help": "Name of the context variable that contains the URL. Can be a simple variable (e.g., 'url') or nested path (e.g., 'target.landing_page', 'redirect_url'). The variable should contain a full URL with IP address or domain.",
                    "placeholder": "url, target.landing_page, redirect_url",
                    "default": "url"
                },
                "url_direct": {
                    "type": "string",
                    "title": "Direct URL Input",
                    "description": "URL to obfuscate (supports variable interpolation)",
                    "help": "Enter the URL directly. Supports variable interpolation using {{variable}} syntax. Example: http://{{target.ip}}/login or https://192.168.1.100/dashboard?token={{auth_token}}",
                    "placeholder": "http://192.168.1.100/login or http://{{target.ip}}/login"
                },
                "method": {
                    "type": "string",
                    "title": "Obfuscation Method",
                    "description": "Select the IP obfuscation technique to apply",
                    "help": "Choose how to obfuscate the IP address. Different methods work better for bypassing different filters. DWORD formats convert IP to integer (bypasses dotted notation checks), dotted formats preserve structure, fake auth uses @ trick (appears as domain@ip), IPv6 formats use IPv6 notation, overflow adds 2^32 to wrap around.",
                    "enum": [
                        {
                            "value": "decimal_dword",
                            "label": "Decimal DWORD",
                            "description": "Convert IP to decimal integer (e.g., 192.168.1.100 → 3232235876). Bypasses filters that only check for dotted notation. Most common obfuscation method."
                        },
                        {
                            "value": "hex_dword",
                            "label": "Hex DWORD",
                            "description": "Convert IP to hexadecimal integer (e.g., 192.168.1.100 → 0xC0A80164). Useful for bypassing basic IP filters that don't recognize hex notation."
                        },
                        {
                            "value": "octal_dword",
                            "label": "Octal DWORD",
                            "description": "Convert IP to octal integer (e.g., 192.168.1.100 → 030052000544). Less common, may bypass filters that don't expect octal notation."
                        },
                        {
                            "value": "dotted_hex",
                            "label": "Dotted Hex",
                            "description": "Format each octet in hexadecimal (e.g., 192.168.1.100 → 0xC0.0xA8.0x1.0x64). Preserves dotted structure while using hex notation."
                        },
                        {
                            "value": "dotted_octal",
                            "label": "Dotted Octal",
                            "description": "Format each octet in octal (e.g., 192.168.1.100 → 0300.0250.01.0144). Preserves dotted structure while using octal notation."
                        },
                        {
                            "value": "mixed_bases",
                            "label": "Mixed Bases",
                            "description": "Mix decimal, hex, and octal (e.g., 192.168.1.100 → 192.0xa8.01.100). Combines different number bases in one IP address."
                        },
                        {
                            "value": "class_b",
                            "label": "Class B Notation",
                            "description": "First octet + 24-bit value (e.g., 192.168.1.100 → 192.11010404). Uses classful addressing notation."
                        },
                        {
                            "value": "class_c",
                            "label": "Class C Notation",
                            "description": "Two octets + 16-bit value (e.g., 192.168.1.100 → 192.168.356). Uses classful addressing notation."
                        },
                        {
                            "value": "ipv6_mapped_hex",
                            "label": "IPv6 Mapped (Hex)",
                            "description": "IPv6-mapped IPv4 in hex (e.g., 192.168.1.100 → ::ffff:c0a8:164). Uses IPv6 notation to represent IPv4 address."
                        },
                        {
                            "value": "ipv6_mapped_decimal",
                            "label": "IPv6 Mapped (Decimal)",
                            "description": "IPv6-mapped IPv4 in decimal (e.g., 192.168.1.100 → ::ffff:192.168.1.100). Uses IPv6 notation with decimal IPv4."
                        },
                        {
                            "value": "ipv6_mapped_full",
                            "label": "IPv6 Mapped (Full)",
                            "description": "IPv6-mapped IPv4 in full notation (e.g., 192.168.1.100 → 0000:0000:0000:0000:0000:ffff:c0a8:0164). Full 128-bit IPv6 representation."
                        },
                        {
                            "value": "fake_auth_decimal",
                            "label": "Fake Auth + Decimal DWORD",
                            "description": "Fake domain with decimal DWORD (e.g., secure.bank.com@3232235876). Uses @ trick to make IP appear as domain authentication. Requires fake_domain setting."
                        },
                        {
                            "value": "fake_auth_hex",
                            "label": "Fake Auth + Hex DWORD",
                            "description": "Fake domain with hex DWORD (e.g., secure.bank.com@0xC0A80164). Combines @ trick with hex notation. Requires fake_domain setting."
                        },
                        {
                            "value": "fake_auth_octal",
                            "label": "Fake Auth + Octal DWORD",
                            "description": "Fake domain with octal DWORD (e.g., secure.bank.com@030052000544). Combines @ trick with octal notation. Requires fake_domain setting."
                        },
                        {
                            "value": "fake_auth_dotted_hex",
                            "label": "Fake Auth + Dotted Hex",
                            "description": "Fake domain with dotted hex (e.g., secure.bank.com@0xC0.0xA8.0x1.0x64). Combines @ trick with dotted hex format. Requires fake_domain setting."
                        },
                        {
                            "value": "fake_auth_dotted_octal",
                            "label": "Fake Auth + Dotted Octal",
                            "description": "Fake domain with dotted octal (e.g., secure.bank.com@0300.0250.01.0144). Combines @ trick with dotted octal format. Requires fake_domain setting."
                        },
                        {
                            "value": "fake_auth_ipv6",
                            "label": "Fake Auth + IPv6 Mapped",
                            "description": "Fake domain with IPv6-mapped (e.g., secure.bank.com@::ffff:c0a8:164). Combines @ trick with IPv6 notation. Requires fake_domain setting."
                        },
                        {
                            "value": "overflow",
                            "label": "Overflow",
                            "description": "Add 2^32 to wrap around (e.g., 192.168.1.100 → 7527203172). Uses integer overflow technique to create large number representation."
                        }
                    ]
                },
                "fake_domain": {
                    "type": "string",
                    "title": "Fake Domain",
                    "description": "Domain name to use for @ trick in fake_auth methods",
                    "help": "Domain name to appear before the @ symbol in fake auth obfuscation methods. This makes the URL appear as domain@ip format. Use a legitimate-looking domain (e.g., secure.bank.com, login.microsoft.com) to increase believability. Required for all fake_auth methods, optional for others.",
                    "placeholder": "secure.bank.com, login.microsoft.com",
                    "default": "google.com"
                },
                "output_variable": {
                    "type": "string",
                    "title": "Output Variable Name",
                    "description": "Context variable name to store the obfuscated URL",
                    "help": "Name of the context variable where the obfuscated URL will be stored. This variable can be used by subsequent plugins in the workflow (e.g., in Redirect plugin, Render Template plugin). The obfuscated URL preserves all original URL components (scheme, path, port, query, fragment) but replaces the IP address with the obfuscated version.",
                    "placeholder": "obfuscated_url, target_url, redirect_url",
                    "default": "obfuscated_url"
                }
            },
            "required": ["method"]
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute URL obfuscation"""
        try:
            # Get URL from source
            url_source = config.get('url_source', 'variable')
            if url_source == 'variable':
                url_variable = config.get('url_variable', 'url')
                url = self._get_nested(context, url_variable)
                if not url:
                    raise ValueError(f"URL not found in context variable '{url_variable}'")
            else:  # direct
                url_direct = config.get('url_direct', '')
                if not url_direct:
                    raise ValueError("url_direct is required when url_source is 'direct'")
                # Interpolate variables in direct URL
                url = interpolate_string(url_direct, context)
            
            if not url:
                raise ValueError("URL is empty")
            
            # Extract IP and URL components
            ip_address, url_components = self._extract_ip_from_url(url)
            if not ip_address:
                raise ValueError(f"Could not extract IP address from URL: {url}")
            
            # Get obfuscation method
            method = config.get('method')
            if not method:
                raise ValueError("Obfuscation method is required")
            
            # Apply obfuscation
            obfuscated_host = self._apply_obfuscation(ip_address, method, config)
            
            # Rebuild URL with obfuscated IP
            obfuscated_url = self._rebuild_url(obfuscated_host, url_components)
            
            # Store in context
            output_var = config.get('output_variable', 'obfuscated_url')
            context[output_var] = obfuscated_url
            
            logger.info(f"URL obfuscated using method '{method}': {obfuscated_url}")
            return context
            
        except Exception as e:
            logger.error(f"URL obfuscation failed: {e}")
            return self.on_error(e, context, config)
    
    @staticmethod
    def _get_nested(data: Dict[str, Any], path: str) -> Any:
        """Get value from dict by dotted path (e.g. 'target.landing_page')."""
        if not path:
            return None
        parts = path.split('.')
        value = data
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value if isinstance(value, str) else (str(value) if value is not None else None)
    
    def _extract_ip_from_url(self, url: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Extract IP address from URL and return URL components
        
        Returns:
            Tuple of (ip_address, url_components_dict)
        """
        try:
            parsed = urllib.parse.urlparse(url)
            
            # Extract components
            url_components = {
                'scheme': parsed.scheme or 'http',
                'netloc': parsed.netloc,
                'path': parsed.path or '/',
                'query': parsed.query,
                'fragment': parsed.fragment,
                'port': None,
                'hostname': None
            }
            
            # Extract hostname and port from netloc
            if ':' in parsed.netloc:
                hostname, port_str = parsed.netloc.rsplit(':', 1)
                try:
                    url_components['port'] = int(port_str)
                except ValueError:
                    hostname = parsed.netloc
            else:
                hostname = parsed.netloc
            
            url_components['hostname'] = hostname
            
            # Check if hostname is already an IP address
            try:
                ip_obj = ipaddress.ip_address(hostname)
                if isinstance(ip_obj, ipaddress.IPv4Address):
                    return str(ip_obj), url_components
            except ValueError:
                pass
            
            # Try to resolve hostname to IP (if it's a domain)
            try:
                ip_str = socket.gethostbyname(hostname)
                return ip_str, url_components
            except (socket.gaierror, OSError):
                # If resolution fails, check if it's already an IP format we missed
                # or return None
                return None, url_components
                
        except Exception as e:
            logger.error(f"Error extracting IP from URL: {e}")
            return None, {}
    
    def _ip_to_int(self, ip: str) -> int:
        """Convert IPv4 address to integer"""
        try:
            return int(ipaddress.IPv4Address(ip))
        except ValueError:
            raise ValueError(f"Invalid IP address: {ip}")
    
    def _int_to_octets(self, ip_int: int) -> Tuple[int, int, int, int]:
        """Convert IP integer to octets"""
        return (
            (ip_int >> 24) & 0xFF,
            (ip_int >> 16) & 0xFF,
            (ip_int >> 8) & 0xFF,
            ip_int & 0xFF
        )
    
    def _format_dword_decimal(self, ip: str) -> str:
        """Format IP as decimal DWORD"""
        return str(self._ip_to_int(ip))
    
    def _format_dword_hex(self, ip: str) -> str:
        """Format IP as hex DWORD"""
        return f"0x{self._ip_to_int(ip):X}"
    
    def _format_dword_octal(self, ip: str) -> str:
        """Format IP as octal DWORD"""
        return f"0{self._ip_to_int(ip):o}"
    
    def _format_dotted_hex(self, ip: str) -> str:
        """Format IP as dotted hex octets"""
        octets = self._int_to_octets(self._ip_to_int(ip))
        return '.'.join(f"0x{o:02x}" for o in octets)
    
    def _format_dotted_octal(self, ip: str) -> str:
        """Format IP as dotted octal octets"""
        octets = self._int_to_octets(self._ip_to_int(ip))
        return '.'.join(f"0{o:o}" for o in octets)
    
    def _format_mixed_bases(self, ip: str) -> str:
        """Format IP with mixed decimal/hex/octal"""
        octets = self._int_to_octets(self._ip_to_int(ip))
        return f"{octets[0]}.0x{octets[1]:02x}.0{octets[2]:o}.{octets[3]}"
    
    def _format_class_b(self, ip: str) -> str:
        """Format IP as Class B notation (first octet + 24-bit value)"""
        ip_int = self._ip_to_int(ip)
        first_octet = (ip_int >> 24) & 0xFF
        remaining = ip_int & 0xFFFFFF
        return f"{first_octet}.{remaining}"
    
    def _format_class_c(self, ip: str) -> str:
        """Format IP as Class C notation (two octets + 16-bit value)"""
        ip_int = self._ip_to_int(ip)
        first_two = (ip_int >> 16) & 0xFFFF
        remaining = ip_int & 0xFFFF
        return f"{first_two >> 8}.{first_two & 0xFF}.{remaining}"
    
    def _format_ipv6_mapped_hex(self, ip: str) -> str:
        """Format IP as IPv6-mapped IPv4 (hex)"""
        octets = self._int_to_octets(self._ip_to_int(ip))
        # Format as ::ffff:XXXX:YYYY where XXXX is first 16 bits, YYYY is last 16 bits
        first_16 = (octets[0] << 8) | octets[1]
        last_16 = (octets[2] << 8) | octets[3]
        return f"::ffff:{first_16:x}:{last_16:x}"
    
    def _format_ipv6_mapped_decimal(self, ip: str) -> str:
        """Format IP as IPv6-mapped IPv4 (decimal)"""
        return f"::ffff:{ip}"
    
    def _format_ipv6_mapped_full(self, ip: str) -> str:
        """Format IP as IPv6-mapped IPv4 (full notation)"""
        octets = self._int_to_octets(self._ip_to_int(ip))
        first_16 = (octets[0] << 8) | octets[1]
        last_16 = (octets[2] << 8) | octets[3]
        return f"0000:0000:0000:0000:0000:ffff:{first_16:04x}:{last_16:04x}"
    
    def _format_fake_auth(self, ip: str, obfuscated_ip: str, fake_domain: str) -> str:
        """Format IP with fake auth @ trick"""
        return f"{fake_domain}@{obfuscated_ip}"
    
    def _format_overflow(self, ip: str) -> str:
        """Format IP with overflow technique (value + 2^32)"""
        ip_int = self._ip_to_int(ip)
        overflow = ip_int + (2 ** 32)
        return str(overflow)
    
    def _apply_obfuscation(self, ip: str, method: str, config: Dict[str, Any]) -> str:
        """Apply the selected obfuscation method to IP address"""
        fake_domain = config.get('fake_domain', 'google.com')
        
        if method == "decimal_dword":
            return self._format_dword_decimal(ip)
        elif method == "hex_dword":
            return self._format_dword_hex(ip)
        elif method == "octal_dword":
            return self._format_dword_octal(ip)
        elif method == "dotted_hex":
            return self._format_dotted_hex(ip)
        elif method == "dotted_octal":
            return self._format_dotted_octal(ip)
        elif method == "mixed_bases":
            return self._format_mixed_bases(ip)
        elif method == "class_b":
            return self._format_class_b(ip)
        elif method == "class_c":
            return self._format_class_c(ip)
        elif method == "ipv6_mapped_hex":
            return self._format_ipv6_mapped_hex(ip)
        elif method == "ipv6_mapped_decimal":
            return self._format_ipv6_mapped_decimal(ip)
        elif method == "ipv6_mapped_full":
            return self._format_ipv6_mapped_full(ip)
        elif method == "fake_auth_decimal":
            obfuscated = self._format_dword_decimal(ip)
            return self._format_fake_auth(ip, obfuscated, fake_domain)
        elif method == "fake_auth_hex":
            obfuscated = self._format_dword_hex(ip)
            return self._format_fake_auth(ip, obfuscated, fake_domain)
        elif method == "fake_auth_octal":
            obfuscated = self._format_dword_octal(ip)
            return self._format_fake_auth(ip, obfuscated, fake_domain)
        elif method == "fake_auth_dotted_hex":
            obfuscated = self._format_dotted_hex(ip)
            return self._format_fake_auth(ip, obfuscated, fake_domain)
        elif method == "fake_auth_dotted_octal":
            obfuscated = self._format_dotted_octal(ip)
            return self._format_fake_auth(ip, obfuscated, fake_domain)
        elif method == "fake_auth_ipv6":
            obfuscated = self._format_ipv6_mapped_hex(ip)
            return self._format_fake_auth(ip, obfuscated, fake_domain)
        elif method == "overflow":
            return self._format_overflow(ip)
        else:
            raise ValueError(f"Unknown obfuscation method: {method}")
    
    def _rebuild_url(self, obfuscated_host: str, url_components: Dict[str, Any]) -> str:
        """Rebuild URL with obfuscated host while preserving components"""
        # Build netloc with port if present
        if url_components.get('port'):
            netloc = f"{obfuscated_host}:{url_components['port']}"
        else:
            netloc = obfuscated_host
        
        # Build URL parts
        parts = {
            'scheme': url_components.get('scheme', 'http'),
            'netloc': netloc,
            'path': url_components.get('path', '/'),
            'query': url_components.get('query', ''),
            'fragment': url_components.get('fragment', '')
        }
        
        # Reconstruct URL
        url = f"{parts['scheme']}://{parts['netloc']}{parts['path']}"
        if parts['query']:
            url += f"?{parts['query']}"
        if parts['fragment']:
            url += f"#{parts['fragment']}"
        
        return url
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate plugin configuration"""
        errors = []
        
        method = config.get('method')
        if not method:
            errors.append("method is required")
        else:
            valid_methods = [
                "decimal_dword", "hex_dword", "octal_dword",
                "dotted_hex", "dotted_octal", "mixed_bases",
                "class_b", "class_c",
                "ipv6_mapped_hex", "ipv6_mapped_decimal", "ipv6_mapped_full",
                "fake_auth_decimal", "fake_auth_hex", "fake_auth_octal",
                "fake_auth_dotted_hex", "fake_auth_dotted_octal", "fake_auth_ipv6",
                "overflow"
            ]
            if method not in valid_methods:
                errors.append(f"Invalid method: {method}")
            
            # Check fake_domain for fake_auth methods
            if method.startswith('fake_auth'):
                fake_domain = config.get('fake_domain', '')
                if not fake_domain:
                    errors.append(f"fake_domain is required for method '{method}'")
        
        url_source = config.get('url_source', 'variable')
        if url_source == 'variable':
            if not config.get('url_variable'):
                errors.append("url_variable is required when url_source is 'variable'")
        elif url_source == 'direct':
            if not config.get('url_direct'):
                errors.append("url_direct is required when url_source is 'direct'")
        else:
            errors.append(f"Invalid url_source: {url_source}")
        
        return errors if errors else None
